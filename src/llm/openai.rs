use anyhow::{Context, Result};
use async_trait::async_trait;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tracing::debug;

use super::{ChatResponse, LlmClient, Message, Role, ToolCall, ToolDefinition};

pub struct OpenAiClient {
    client: Client,
    base_url: String,
    api_key: String,
    model: String,
}

impl OpenAiClient {
    pub fn new(
        api_key: impl Into<String>,
        model: impl Into<String>,
        base_url: Option<String>,
    ) -> Self {
        Self {
            client: Client::new(),
            api_key: api_key.into(),
            model: model.into(),
            base_url: base_url
                .unwrap_or_else(|| "https://api.openai.com/v1".into())
                .trim_end_matches('/')
                .to_string(),
        }
    }
}

#[derive(Serialize)]
struct OpenAiRequest {
    model: String,
    messages: Vec<Value>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tools: Vec<Value>,
}

#[derive(Deserialize)]
struct OpenAiResponse {
    choices: Vec<OpenAiChoice>,
}

#[derive(Deserialize)]
struct OpenAiChoice {
    message: OpenAiMessage,
}

#[derive(Deserialize)]
struct OpenAiMessage {
    #[serde(default)]
    content: Option<String>,
    #[serde(default)]
    tool_calls: Option<Vec<OpenAiToolCall>>,
}

#[derive(Deserialize)]
struct OpenAiToolCall {
    id: String,
    function: OpenAiFunction,
}

#[derive(Deserialize)]
struct OpenAiFunction {
    name: String,
    arguments: String,
}

fn to_openai_message(msg: &Message) -> Value {
    match msg.role {
        Role::System => json!({ "role": "system", "content": msg.content }),
        Role::User => json!({ "role": "user", "content": msg.content }),
        Role::Assistant => {
            let tool_calls: Option<Vec<Value>> = msg.tool_calls.as_ref().map(|calls| {
                calls.iter().map(|c| json!({
                    "id": c.id,
                    "type": "function",
                    "function": { "name": c.name, "arguments": c.arguments.to_string() },
                })).collect()
            });
            let mut m = json!({ "role": "assistant" });
            if !msg.content.is_empty() {
                m["content"] = json!(msg.content);
            }
            if let Some(tc) = tool_calls {
                m["tool_calls"] = json!(tc);
            }
            m
        }
        Role::Tool => json!({
            "role": "tool",
            "tool_call_id": msg.tool_call_id.as_deref().unwrap_or(""),
            "content": msg.content,
        }),
    }
}

#[async_trait]
impl LlmClient for OpenAiClient {
    async fn chat(&self, messages: &[Message], tools: &[ToolDefinition]) -> Result<ChatResponse> {
        let openai_tools: Vec<Value> = tools.iter().map(|t| json!({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
        })).collect();

        let req = OpenAiRequest {
            model: self.model.clone(),
            messages: messages.iter().map(to_openai_message).collect(),
            tools: openai_tools,
        };

        let url = format!("{}/chat/completions", self.base_url);
        debug!("POST {url}");

        let resp: OpenAiResponse = self.client
            .post(&url)
            .bearer_auth(&self.api_key)
            .json(&req)
            .send().await.context("OpenAI request failed")?
            .error_for_status().context("OpenAI error status")?
            .json().await.context("OpenAI parse error")?;

        let msg = resp.choices.into_iter().next().context("empty choices")?.message;
        let content = msg.content.unwrap_or_default();
        let tool_calls = msg.tool_calls
            .unwrap_or_default()
            .into_iter()
            .map(|tc| {
                let arguments = serde_json::from_str(&tc.function.arguments)
                    .unwrap_or(Value::Object(Default::default()));
                ToolCall { id: tc.id, name: tc.function.name, arguments }
            })
            .collect();

        Ok(ChatResponse { content, tool_calls })
    }

    fn provider_name(&self) -> &str {
        "openai"
    }
}
