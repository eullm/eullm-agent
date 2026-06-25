use anyhow::Result;
use async_trait::async_trait;
use reqwest::Client;
use serde_json::{json, Value};
use std::time::Duration;

use crate::llm::ToolDefinition;
use super::Tool;

pub struct FetchUrlTool {
    client: Client,
}

impl FetchUrlTool {
    pub fn new(timeout_secs: u64) -> Self {
        let client = Client::builder()
            .timeout(Duration::from_secs(timeout_secs))
            .user_agent("eullm-agent/0.1")
            .build()
            .expect("Failed to build HTTP client");
        Self { client }
    }
}

#[async_trait]
impl Tool for FetchUrlTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "fetch_url".into(),
            description: "Perform an HTTP request (GET/POST/PUT/DELETE) and return the response \
                          body as text. Use for REST APIs, fetching web content, or any HTTP \
                          endpoint. JSON responses are returned as-is.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to request"
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE"],
                        "description": "HTTP method (default: GET)"
                    },
                    "headers": {
                        "type": "object",
                        "description": "Additional request headers as key/value pairs",
                        "additionalProperties": { "type": "string" }
                    },
                    "body": {
                        "type": "string",
                        "description": "Raw request body (for POST/PUT)"
                    },
                    "json_body": {
                        "description": "Request body sent as JSON; sets Content-Type: application/json automatically"
                    }
                },
                "required": ["url"]
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let url = arguments["url"]
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("Missing 'url'"))?;

        let method = arguments["method"].as_str().unwrap_or("GET");

        let mut req = match method.to_uppercase().as_str() {
            "GET" => self.client.get(url),
            "POST" => self.client.post(url),
            "PUT" => self.client.put(url),
            "DELETE" => self.client.delete(url),
            m => return Err(anyhow::anyhow!("Unsupported HTTP method: {m}")),
        };

        if let Some(hdrs) = arguments["headers"].as_object() {
            for (k, v) in hdrs {
                if let Some(val) = v.as_str() {
                    req = req.header(k.as_str(), val);
                }
            }
        }

        if !arguments["json_body"].is_null() {
            req = req.json(&arguments["json_body"]);
        } else if let Some(body) = arguments["body"].as_str() {
            req = req.body(body.to_string());
        }

        let resp = req
            .send()
            .await
            .map_err(|e| anyhow::anyhow!("Request failed: {e}"))?;

        let status = resp.status();
        let text = resp
            .text()
            .await
            .map_err(|e| anyhow::anyhow!("Failed to read response body: {e}"))?;

        const MAX_LEN: usize = 12_000;
        let body = if text.len() > MAX_LEN {
            let mut end = MAX_LEN;
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            format!("{}…\n[truncated — {} bytes total]", &text[..end], text.len())
        } else {
            text
        };

        if status.is_success() {
            Ok(body)
        } else {
            Ok(format!("[HTTP {status}]\n{body}"))
        }
    }
}
