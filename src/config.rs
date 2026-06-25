use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Config {
    pub provider: ProviderConfig,
    #[serde(default)]
    pub telegram: Option<TelegramConfig>,
    #[serde(default)]
    pub tools: ToolsConfig,
    #[serde(default = "default_max_iterations")]
    pub max_iterations: usize,
    #[serde(default = "default_system_prompt")]
    pub system_prompt: String,
}

fn default_max_iterations() -> usize {
    20
}

fn default_system_prompt() -> String {
    "You are EULLM Agent, an autonomous task executor running on EU infrastructure. \
     Think step by step. Use the available tools to complete tasks accurately and efficiently. \
     When the task is complete, summarise the result clearly."
        .into()
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ProviderConfig {
    Eullm {
        #[serde(default = "default_eullm_url")]
        base_url: String,
        model: String,
    },
    Anthropic {
        api_key: String,
        #[serde(default = "default_claude_model")]
        model: String,
    },
    OpenAI {
        api_key: String,
        #[serde(default = "default_openai_model")]
        model: String,
        base_url: Option<String>,
    },
}

fn default_eullm_url() -> String {
    "http://localhost:11434".into()
}
fn default_claude_model() -> String {
    "claude-sonnet-4-6".into()
}
fn default_openai_model() -> String {
    "gpt-4o".into()
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct TelegramConfig {
    pub token: String,
    #[serde(default)]
    pub allowed_users: Vec<i64>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct ToolsConfig {
    #[serde(default)]
    pub shell: ShellToolConfig,
    #[serde(default)]
    pub filesystem: FilesystemToolConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ShellToolConfig {
    #[serde(default = "bool_true")]
    pub enabled: bool,
    #[serde(default)]
    pub allow_sudo: bool,
    #[serde(default = "default_timeout")]
    pub timeout_seconds: u64,
}

impl Default for ShellToolConfig {
    fn default() -> Self {
        Self { enabled: true, allow_sudo: false, timeout_seconds: 30 }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct FilesystemToolConfig {
    #[serde(default = "bool_true")]
    pub enabled: bool,
    #[serde(default)]
    pub allowed_paths: Vec<PathBuf>,
}

impl Default for FilesystemToolConfig {
    fn default() -> Self {
        Self { enabled: true, allowed_paths: Vec::new() }
    }
}

fn bool_true() -> bool {
    true
}
fn default_timeout() -> u64 {
    30
}

impl Config {
    pub fn load(path: &std::path::Path) -> Result<Self> {
        let text = std::fs::read_to_string(path)
            .with_context(|| format!("Cannot read {}", path.display()))?;
        serde_yaml::from_str(&text)
            .with_context(|| format!("Invalid YAML in {}", path.display()))
    }
}
