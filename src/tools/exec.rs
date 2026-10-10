//! `run_program`: runs one allowlisted program with explicit arguments.
//!
//! There is no shell, so `;`, `|`, `$(...)` and quoting tricks are plain
//! characters inside an argument. A program not in `allowed_programs` is
//! refused, whatever its arguments.

use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::{json, Value};
use std::time::Duration;

use super::process::{format_output, run_program};
use super::sandbox::Workspace;
use super::Tool;
use crate::config::{ExecToolConfig, SandboxConfig};
use crate::llm::ToolDefinition;

const MAX_ARGS: usize = 64;
const MAX_ARG_BYTES: usize = 4096;

pub struct ExecTool {
    allowed: Vec<String>,
    timeout: Duration,
    max_output_bytes: usize,
    workspace: Workspace,
    sandbox: SandboxConfig,
}

impl ExecTool {
    pub fn new(cfg: &ExecToolConfig, workspace: Workspace, sandbox: &SandboxConfig) -> Self {
        Self {
            allowed: cfg.allowed_programs.clone(),
            timeout: Duration::from_secs(cfg.timeout_seconds.max(1)),
            max_output_bytes: cfg.max_output_bytes,
            workspace,
            sandbox: sandbox.clone(),
        }
    }
}

#[async_trait]
impl Tool for ExecTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "run_program".into(),
            description: format!(
                "Run one program with a list of arguments (no shell: pipes, redirects and \
                 variables are not interpreted) in the workspace directory, and return its \
                 output. Allowed programs: {}.",
                self.allowed.join(", ")
            ),
            parameters: json!({
                "type": "object",
                "properties": {
                    "program": { "type": "string", "description": "Program name, one of the allowed programs" },
                    "args": { "type": "array", "items": { "type": "string" }, "description": "Arguments, one per element" }
                },
                "required": ["program"]
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let program = arguments["program"]
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("Missing 'program'"))?;
        if !self.allowed.iter().any(|p| p == program) {
            bail!("Program '{program}' is not in tools.exec.allowed_programs");
        }
        let args: Vec<String> = match &arguments["args"] {
            Value::Null => Vec::new(),
            Value::Array(items) => items
                .iter()
                .map(|v| {
                    v.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| anyhow::anyhow!("'args' must contain only strings"))
                })
                .collect::<Result<_>>()?,
            _ => bail!("'args' must be an array of strings"),
        };
        if args.len() > MAX_ARGS {
            bail!("Too many arguments (max {MAX_ARGS})");
        }
        if args
            .iter()
            .any(|a| a.len() > MAX_ARG_BYTES || a.contains('\0'))
        {
            bail!("Invalid argument (too long or contains NUL)");
        }
        let out = run_program(
            program,
            &args,
            self.workspace.root(),
            self.timeout,
            self.max_output_bytes,
            &self.sandbox,
        )
        .await?;
        Ok(format_output(&out))
    }
}
