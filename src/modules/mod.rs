use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::PathBuf;

pub mod builtin;

#[derive(Debug, Clone)]
pub struct ModuleToolSpec {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
    /// Shell command template; use {arg_name} for argument substitution.
    pub command: String,
}

#[derive(Debug, Clone)]
pub struct ModuleManifest {
    pub name: String,
    pub description: String,
    pub version: &'static str,
    pub install_linux: Vec<String>,
    pub install_macos: Vec<String>,
    pub tools: Vec<ModuleToolSpec>,
}

#[derive(Debug, Serialize, Deserialize, Default)]
pub struct ModuleState {
    pub installed: HashSet<String>,
}

pub struct ModuleRegistry {
    pub manifests: Vec<ModuleManifest>,
    pub state: ModuleState,
    pub state_path: PathBuf,
}

impl ModuleRegistry {
    pub fn load(state_path: PathBuf) -> Result<Self> {
        let state = if state_path.exists() {
            let text = std::fs::read_to_string(&state_path)?;
            serde_json::from_str(&text)?
        } else {
            ModuleState::default()
        };
        Ok(Self {
            manifests: builtin::all_modules(),
            state,
            state_path,
        })
    }

    pub fn save_state(&self) -> Result<()> {
        if let Some(parent) = self.state_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&self.state_path, serde_json::to_string_pretty(&self.state)?)?;
        Ok(())
    }

    /// Short text injected into the system prompt so the LLM knows what it has.
    pub fn status_summary(&self) -> String {
        let installed: Vec<&str> = self.manifests.iter()
            .filter(|m| self.state.installed.contains(&m.name))
            .map(|m| m.name.as_str())
            .collect();
        let not_installed: Vec<&str> = self.manifests.iter()
            .filter(|m| !self.state.installed.contains(&m.name))
            .map(|m| m.name.as_str())
            .collect();

        let mut s = String::new();
        if !installed.is_empty() {
            s.push_str(&format!("\n\nInstalled modules: {}", installed.join(", ")));
        }
        if !not_installed.is_empty() {
            s.push_str(&format!(
                "\nAvailable modules (not installed): {}",
                not_installed.join(", ")
            ));
            s.push_str("\nUse list_modules to inspect them and install_module to install one.");
        }
        s
    }
}
