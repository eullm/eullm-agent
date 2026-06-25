use anyhow::Result;
use async_trait::async_trait;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};

use crate::llm::ToolDefinition;
use crate::modules::{ModuleRegistry, ModuleToolSpec};
use super::{Tool, ToolRegistry};

/// Executes a single module-defined tool via shell command template substitution.
pub struct ModuleTool {
    spec: ModuleToolSpec,
}

impl ModuleTool {
    pub fn new(spec: ModuleToolSpec) -> Self {
        Self { spec }
    }
}

#[async_trait]
impl Tool for ModuleTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: self.spec.name.clone(),
            description: self.spec.description.clone(),
            parameters: self.spec.parameters.clone(),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let mut cmd = self.spec.command.clone();
        if let Some(obj) = arguments.as_object() {
            for (key, val) in obj {
                let placeholder = format!("{{{}}}", key);
                let value = match val {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                cmd = cmd.replace(&placeholder, &value);
            }
        }

        let output = tokio::process::Command::new("sh")
            .arg("-c")
            .arg(&cmd)
            .output()
            .await?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();

        if output.status.success() {
            Ok(stdout)
        } else {
            Err(anyhow::anyhow!("Command failed: {}", stderr.trim()))
        }
    }
}

/// Lists all modules and their installation status.
pub struct ListModulesTool {
    registry: Arc<Mutex<ModuleRegistry>>,
}

impl ListModulesTool {
    pub fn new(registry: Arc<Mutex<ModuleRegistry>>) -> Self {
        Self { registry }
    }
}

#[async_trait]
impl Tool for ListModulesTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "list_modules".into(),
            description: "List all available modules and their installation status. \
                         Use install_module to install a missing one.".into(),
            parameters: json!({ "type": "object", "properties": {} }),
        }
    }

    async fn execute(&self, _: &Value) -> Result<String> {
        let reg = self.registry.lock().unwrap();
        let mut out = String::new();

        out.push_str("=== Installed modules ===\n");
        let mut any = false;
        for m in &reg.manifests {
            if reg.state.installed.contains(&m.name) {
                any = true;
                out.push_str(&format!("  {} v{} — {}\n", m.name, m.version, m.description));
                for t in &m.tools {
                    out.push_str(&format!("    tool: {}\n", t.name));
                }
            }
        }
        if !any { out.push_str("  (none)\n"); }

        out.push_str("\n=== Available (not installed) ===\n");
        let mut any = false;
        for m in &reg.manifests {
            if !reg.state.installed.contains(&m.name) {
                any = true;
                out.push_str(&format!("  {} — {}\n", m.name, m.description));
            }
        }
        if !any { out.push_str("  (all modules installed)\n"); }

        Ok(out)
    }
}

/// Installs a module and immediately registers its tools into the shared ToolRegistry.
pub struct InstallModuleTool {
    registry: Arc<Mutex<ModuleRegistry>>,
    tool_registry: ToolRegistry,
}

impl InstallModuleTool {
    pub fn new(registry: Arc<Mutex<ModuleRegistry>>, tool_registry: ToolRegistry) -> Self {
        Self { registry, tool_registry }
    }
}

#[async_trait]
impl Tool for InstallModuleTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "install_module".into(),
            description: "Install a module to gain new tool capabilities. \
                         Runs the platform install commands, then makes the new tools \
                         immediately available in this session.".into(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Module name (e.g. 'ocr', 'pdf'). Use list_modules to see available ones."
                    }
                },
                "required": ["name"]
            }),
        }
    }

    async fn execute(&self, arguments: &Value) -> Result<String> {
        let name = arguments["name"]
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("'name' argument required"))?
            .to_string();

        let manifest = {
            let reg = self.registry.lock().unwrap();
            if reg.state.installed.contains(&name) {
                return Ok(format!("Module '{}' is already installed.", name));
            }
            reg.manifests.iter().find(|m| m.name == name).cloned()
        };

        let manifest = manifest.ok_or_else(|| {
            anyhow::anyhow!("Unknown module: '{}'. Use list_modules to see available modules.", name)
        })?;

        #[cfg(target_os = "macos")]
        let commands = &manifest.install_macos;
        #[cfg(not(target_os = "macos"))]
        let commands = &manifest.install_linux;

        for cmd in commands {
            tracing::info!("module install: {cmd}");
            let output = tokio::process::Command::new("sh")
                .arg("-c")
                .arg(cmd)
                .output()
                .await?;
            if !output.status.success() {
                let stderr = String::from_utf8_lossy(&output.stderr);
                return Err(anyhow::anyhow!(
                    "Install failed at '{cmd}': {}", stderr.trim()
                ));
            }
        }

        {
            let mut reg = self.registry.lock().unwrap();
            reg.state.installed.insert(name.clone());
            reg.save_state()?;
        }

        let tool_names: Vec<String> = manifest.tools.iter().map(|t| t.name.clone()).collect();
        for spec in manifest.tools {
            self.tool_registry.register(Arc::new(ModuleTool::new(spec)));
        }

        Ok(format!(
            "Module '{}' installed.\nNew tools available: {}",
            name,
            tool_names.join(", ")
        ))
    }
}
