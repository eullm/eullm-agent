use anyhow::Result;
use async_trait::async_trait;
use serde_json::Value;
use std::sync::{Arc, Mutex};

use crate::llm::ToolDefinition;

pub mod filesystem;
pub mod http;
pub mod module_tool;
pub mod shell;

#[async_trait]
pub trait Tool: Send + Sync {
    fn definition(&self) -> ToolDefinition;
    async fn execute(&self, arguments: &Value) -> Result<String>;
}

/// Shared, cloneable tool registry with interior mutability.
/// Clones share the same underlying tool list — modules installed at runtime
/// become immediately visible to all clones (including ones held by InstallModuleTool).
#[derive(Clone)]
pub struct ToolRegistry {
    tools: Arc<Mutex<Vec<Arc<dyn Tool>>>>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self { tools: Arc::new(Mutex::new(Vec::new())) }
    }

    pub fn register(&self, tool: Arc<dyn Tool>) {
        self.tools.lock().unwrap().push(tool);
    }

    pub fn definitions(&self) -> Vec<ToolDefinition> {
        self.tools.lock().unwrap().iter().map(|t| t.definition()).collect()
    }

    pub async fn execute(&self, name: &str, arguments: &Value) -> Result<String> {
        let tool = {
            let tools = self.tools.lock().unwrap();
            tools.iter().find(|t| t.definition().name == name).map(Arc::clone)
        };
        match tool {
            Some(t) => t.execute(arguments).await,
            None => Err(anyhow::anyhow!("Unknown tool: {name}")),
        }
    }
}

impl Default for ToolRegistry {
    fn default() -> Self {
        Self::new()
    }
}
