use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tokio::fs;

use crate::llm::ToolDefinition;
use super::Tool;

fn is_allowed(path: &Path, allowed: &[PathBuf]) -> bool {
    if allowed.is_empty() {
        return true;
    }
    let canonical = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    allowed.iter().any(|base| canonical.starts_with(base))
}

// --- read_file ---

pub struct ReadFileTool {
    allowed_paths: Vec<PathBuf>,
}

impl ReadFileTool {
    pub fn new(allowed_paths: Vec<PathBuf>) -> Self {
        Self { allowed_paths }
    }
}

#[async_trait]
impl Tool for ReadFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "read_file".into(),
            description: "Read the text contents of a file.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Path to the file" }
                },
                "required": ["path"]
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let path: PathBuf = arguments["path"].as_str()
            .ok_or_else(|| anyhow::anyhow!("Missing 'path'"))?.into();

        if !is_allowed(&path, &self.allowed_paths) {
            bail!("Access denied: path outside allowed_paths");
        }

        fs::read_to_string(&path).await
            .map_err(|e| anyhow::anyhow!("read_file {}: {e}", path.display()))
    }
}

// --- write_file ---

pub struct WriteFileTool {
    allowed_paths: Vec<PathBuf>,
}

impl WriteFileTool {
    pub fn new(allowed_paths: Vec<PathBuf>) -> Self {
        Self { allowed_paths }
    }
}

#[async_trait]
impl Tool for WriteFileTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "write_file".into(),
            description: "Write (create or overwrite) a text file.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Destination path" },
                    "content": { "type": "string", "description": "File content to write" }
                },
                "required": ["path", "content"]
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let path: PathBuf = arguments["path"].as_str()
            .ok_or_else(|| anyhow::anyhow!("Missing 'path'"))?.into();
        let content = arguments["content"].as_str()
            .ok_or_else(|| anyhow::anyhow!("Missing 'content'"))?;

        if let Some(parent) = path.parent() {
            if !is_allowed(parent, &self.allowed_paths) {
                bail!("Access denied: path outside allowed_paths");
            }
            fs::create_dir_all(parent).await?;
        }

        fs::write(&path, content).await
            .map_err(|e| anyhow::anyhow!("write_file {}: {e}", path.display()))?;

        Ok(format!("Written {} bytes to {}", content.len(), path.display()))
    }
}

// --- list_dir ---

pub struct ListDirTool;

#[async_trait]
impl Tool for ListDirTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "list_dir".into(),
            description: "List the entries of a directory.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Directory path (default: .)" }
                }
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let path = arguments["path"].as_str().unwrap_or(".");
        let mut entries = fs::read_dir(path).await
            .map_err(|e| anyhow::anyhow!("list_dir {path}: {e}"))?;

        let mut names = Vec::new();
        while let Some(entry) = entries.next_entry().await? {
            let name = entry.file_name().to_string_lossy().to_string();
            let is_dir = entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false);
            names.push(if is_dir { format!("{name}/") } else { name });
        }
        names.sort();
        Ok(if names.is_empty() { "(empty)".into() } else { names.join("\n") })
    }
}
