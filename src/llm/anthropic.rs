//! Anthropic Messages API client.
use anyhow::{Context, Result};
use async_trait::async_trait;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tracing::debug;

use super::{ChatResponse, LlmClient, Message, MessageContent, Role, ToolCall, ToolDefinition};

pub struct AnthropicClient {
    client: Client,
    api_key: String,
    model: String,
    max_tokens: u32,
}

impl AnthropicClient {
    pub fn new(api_key: impl Into<String>, model: impl Into<String>) -> Self {
        Self {
            client: Client::new(),
            api_key: api_key.into(),
            model: model.into(),
            max_tokens: 4096,
        }
    }
}

#[derive(Serialize)]
struct AnthropicRequest {
    model: String,
    max_tokens: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    system: Option<String>,
    messages: Vec<AnthropicMessage>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tools: Vec<Value>,
}

#[derive(Serialize, Deserialize)]
struct AnthropicMessage {
    role: String,
    content: Value,
}

#[derive(Deserialize)]
struct AnthropicResponse {
    content: Vec<AnthropicBlock>,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum AnthropicBlock {
    Text { text: String },
    ToolUse { id: String, name: String, input: Value },
}

fn extract_text(c: &MessageContent) -> String {
    match c {
        MessageContent::Text(s) => s.clone(),
        MessageContent::Parts(parts) => parts.iter().filter_map(|p| p.text.as_deref()).collect::<Vec<_>>().join(""),
    }
}

fn to_anthropic_messages(messages: &[Message]) -> (Option<String>, Vec<AnthropicMessage>) {
    let mut system = None;
    let mut out: Vec<AnthropicMessage> = Vec::new();

    for msg in messages {
        match msg.role {
            Role::System => {
                system = Some(extract_text(&msg.content));
            }
            Role::User => {
                out.push(AnthropicMessage {
                    role: "user".into(),
                    content: Value::String(extract_text(&msg.content)),
                });
            }
            Role::Assistant => {
                let text = extract_text(&msg.content);
                let mut parts: Vec<Value> = Vec::new();
                if !text.is_empty() {
                    parts.push(json!({ "type": "text", "text": text }));
                }
                if let Some(calls) = &msg.tool_calls {
                    for c in calls {
                        parts.push(json!({ "type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments }));
                    }
                }
                out.push(AnthropicMessage {
                    role: "assistant".into(),
                    content: if parts.len() == 1 && parts[0]["type"] == "text" {
                        parts[0]["text"].clone()
                    } else {
                        Value::Array(parts)
                    },
                });
            }
            Role::Tool => {
                let tool_use_id = msg.tool_call_id.as_deref().unwrap_or("unknown");
                let new_part = json!({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": extract_text(&msg.content),
                });

                // Merge consecutive tool results into a single user message.
                let merged = out.last_mut()
                    .filter(|m| m.role == "user")
                    .and_then(|m| {
                        if let Value::Array(arr) = &mut m.content {
                            arr.push(new_part.clone());
                            Some(())
                        } else {
                            None
                        }
                    });

                if merged.is_none() {
                    out.push(AnthropicMessage {
                        role: "user".into(),
                        content: json!([new_part]),
                    });
                }
            }
        }
    }

    (system, out)
}

#[async_trait]
impl LlmClient for AnthropicClient {
    async fn chat(&self, messages: &[Message], tools: &[ToolDefinition]) -> Result<ChatResponse> {
        let (system, anthropic_messages) = to_anthropic_messages(messages);

        let anthropic_tools: Vec<Value> = tools.iter().map(|t| json!({
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        })).collect();

        let req = AnthropicRequest {
            model: self.model.clone(),
            max_tokens: self.max_tokens,
            system,
            messages: anthropic_messages,
            tools: anthropic_tools,
        };

        debug!("POST https://api.anthropic.com/v1/messages");

        let resp: AnthropicResponse = self.client
            .post("https://api.anthropic.com/v1/messages")
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .json(&req)
            .send().await.context("Anthropic request failed")?
            .error_for_status().context("Anthropic error status")?
            .json().await.context("Anthropic parse error")?;

        let mut content = String::new();
        let mut tool_calls = Vec::new();

        for block in resp.content {
            match block {
                AnthropicBlock::Text { text } => content.push_str(&text),
                AnthropicBlock::ToolUse { id, name, input } => {
                    tool_calls.push(ToolCall { id, name, arguments: input });
                }
            }
        }

        Ok(ChatResponse { content, tool_calls })
    }

    fn provider_name(&self) -> &str {
        "anthropic"
    }
}
