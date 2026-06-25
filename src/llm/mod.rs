use anyhow::Result;
use async_trait::async_trait;
use serde_json::Value;

pub mod anthropic;
pub mod eullm;
pub mod openai;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: Value,
}

#[derive(Debug, Clone)]
pub struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

#[derive(Debug, Clone)]
pub struct Message {
    pub role: Role,
    pub content: String,
    pub tool_call_id: Option<String>,
    pub tool_calls: Option<Vec<ToolCall>>,
}

impl Message {
    pub fn system(content: impl Into<String>) -> Self {
        Self { role: Role::System, content: content.into(), tool_call_id: None, tool_calls: None }
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self { role: Role::User, content: content.into(), tool_call_id: None, tool_calls: None }
    }

    pub fn assistant_with_tools(content: impl Into<String>, tool_calls: Vec<ToolCall>) -> Self {
        Self { role: Role::Assistant, content: content.into(), tool_call_id: None, tool_calls: Some(tool_calls) }
    }

    pub fn tool_result(tool_call_id: impl Into<String>, content: impl Into<String>) -> Self {
        Self { role: Role::Tool, content: content.into(), tool_call_id: Some(tool_call_id.into()), tool_calls: None }
    }
}

#[derive(Debug, Clone)]
pub struct ChatResponse {
    pub content: String,
    pub tool_calls: Vec<ToolCall>,
}

#[async_trait]
pub trait LlmClient: Send + Sync {
    async fn chat(&self, messages: &[Message], tools: &[ToolDefinition]) -> Result<ChatResponse>;
    fn provider_name(&self) -> &str;
}

/// Remove <think>...</think> blocks emitted by reasoning models (e.g. Qwen3, DeepSeek).
pub fn strip_think_blocks(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(start) = rest.find("<think>") {
        out.push_str(&rest[..start]);
        match rest.find("</think>") {
            Some(end) => rest = &rest[end + "</think>".len()..],
            None => break,
        }
    }
    out.push_str(rest);
    out.trim().to_string()
}
