use anyhow::{Context, Result};
use async_trait::async_trait;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tracing::debug;

use super::{ChatResponse, LlmClient, Message, Role, ToolCall, ToolDefinition};

pub struct EullmClient {
    client: Client,
    base_url: String,
    model: String,
}

impl EullmClient {
    pub fn new(base_url: impl Into<String>, model: impl Into<String>) -> Self {
        Self {
            client: Client::new(),
            base_url: base_url.into().trim_end_matches('/').to_string(),
            model: model.into(),
        }
    }
}

#[derive(Serialize)]
struct OllamaRequest {
    model: String,
    messages: Vec<OllamaMessage>,
    stream: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tools: Vec<Value>,
}

#[derive(Serialize, Deserialize, Default)]
struct OllamaMessage {
    role: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tool_calls: Option<Vec<OllamaToolCall>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tool_call_id: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct OllamaToolCall {
    #[serde(skip_serializing_if = "Option::is_none")]
    id: Option<String>,
    function: OllamaFunction,
}

#[derive(Serialize, Deserialize)]
struct OllamaFunction {
    name: String,
    arguments: Value,
}

#[derive(Deserialize)]
struct OllamaResponse {
    message: OllamaMessage,
}

fn to_ollama(msg: &Message) -> OllamaMessage {
    let role = match msg.role {
        Role::System => "system",
        Role::User => "user",
        Role::Assistant => "assistant",
        Role::Tool => "tool",
    };

    let content = if msg.content.is_empty() { None } else { Some(msg.content.clone()) };

    let tool_calls = msg.tool_calls.as_ref().map(|calls| {
        calls.iter().map(|c| OllamaToolCall {
            id: Some(c.id.clone()),
            function: OllamaFunction { name: c.name.clone(), arguments: c.arguments.clone() },
        }).collect()
    });

    OllamaMessage {
        role: role.into(),
        content,
        tool_calls,
        tool_call_id: msg.tool_call_id.clone(),
    }
}

#[async_trait]
impl LlmClient for EullmClient {
    async fn chat(&self, messages: &[Message], tools: &[ToolDefinition]) -> Result<ChatResponse> {
        let ollama_tools: Vec<Value> = tools.iter().map(|t| json!({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
        })).collect();

        let req = OllamaRequest {
            model: self.model.clone(),
            messages: messages.iter().map(to_ollama).collect(),
            stream: false,
            tools: ollama_tools,
        };

        let url = format!("{}/api/chat", self.base_url);
        debug!("POST {url}");

        let resp: OllamaResponse = self.client
            .post(&url)
            .json(&req)
            .send().await.context("EULLM request failed")?
            .error_for_status().context("EULLM error status")?
            .json().await.context("EULLM parse error")?;

        let content = resp.message.content.unwrap_or_default();
        let tool_calls = resp.message.tool_calls
            .unwrap_or_default()
            .into_iter()
            .enumerate()
            .map(|(i, tc)| ToolCall {
                id: tc.id.unwrap_or_else(|| format!("call_{i}")),
                name: tc.function.name,
                arguments: tc.function.arguments,
            })
            .collect();

        Ok(ChatResponse { content, tool_calls })
    }

    fn provider_name(&self) -> &str {
        "eullm"
    }
}
