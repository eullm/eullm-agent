use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::{json, Value};
use std::time::Duration;
use tokio::{process::Command, time::timeout};

use crate::llm::ToolDefinition;
use super::Tool;

pub struct ShellTool {
    allow_sudo: bool,
    timeout_secs: u64,
}

impl ShellTool {
    pub fn new(allow_sudo: bool, timeout_secs: u64) -> Self {
        Self { allow_sudo, timeout_secs: timeout_secs.max(5) }
    }
}

#[async_trait]
impl Tool for ShellTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "shell".into(),
            description: "Execute a shell command and return stdout + stderr. \
                          Use for reading output of programs, inspecting files, \
                          running scripts, or any system operation.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute"
                    }
                },
                "required": ["command"]
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let command = arguments["command"]
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("Missing 'command'"))?;

        if !self.allow_sudo && command.contains("sudo") {
            bail!("sudo is disabled. Set tools.shell.allow_sudo: true in config to enable.");
        }

        let output = timeout(
            Duration::from_secs(self.timeout_secs),
            Command::new("sh").arg("-c").arg(command).output(),
        )
        .await
        .map_err(|_| anyhow::anyhow!("Command timed out after {}s", self.timeout_secs))?
        .map_err(|e| anyhow::anyhow!("Spawn error: {e}"))?;

        let mut result = String::new();

        let stdout = String::from_utf8_lossy(&output.stdout);
        if !stdout.is_empty() {
            result.push_str(&stdout);
        }

        let stderr = String::from_utf8_lossy(&output.stderr);
        if !stderr.is_empty() {
            if !result.is_empty() { result.push('\n'); }
            result.push_str("[stderr] ");
            result.push_str(&stderr);
        }

        if !output.status.success() {
            let code = output.status.code().unwrap_or(-1);
            result.push_str(&format!("\n[exit {code}]"));
        }

        if result.is_empty() {
            result = "(no output)".into();
        }

        Ok(result)
    }
}
