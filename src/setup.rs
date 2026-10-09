//! Turn a [`Config`] into a model client and a tool registry.
//!
//! Every sensitive tool is registered only when the configuration turns it
//! on; anything missing from the config stays off.

use anyhow::{Context, Result};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tracing::{info, warn};

use crate::audit::{Audit, Recorder};
use crate::config::{resolve_secret, Config, ProviderConfig};
use crate::llm::LlmClient;
use crate::llm::{anthropic::AnthropicClient, eullm, ollama::OllamaClient, openai::OpenAiClient};
use crate::modules::ModuleRegistry;
use crate::policy::Policy;
use crate::router::ModelRouter;
use crate::service::Core;
use crate::store::{MemoryStore, PgStore, Store};
use crate::tools::{
    exec::ExecTool,
    filesystem::{ListDirTool, ReadFileTool, WriteFileTool},
    http::FetchUrlTool,
    module_tool::{ListModulesTool, ModuleTool},
    sandbox::Workspace,
    ToolRegistry,
};

pub fn build_llm(config: &Config) -> Result<Arc<dyn LlmClient>> {
    build_client(
        &config.provider,
        Duration::from_secs(config.limits.llm_timeout_seconds),
    )
}

/// A client for one provider block.
pub fn build_client(provider: &ProviderConfig, timeout: Duration) -> Result<Arc<dyn LlmClient>> {
    Ok(match provider {
        ProviderConfig::Eullm {
            base_url,
            model,
            api_key,
            api_key_env,
        } => {
            let key = resolve_secret(api_key, api_key_env)?;
            info!(
                "provider=eullm base_url={} model={model}",
                eullm::v1_url(base_url)
            );
            Arc::new(eullm::client(base_url, model.clone(), key, timeout))
        }
        ProviderConfig::Ollama { base_url, model } => {
            info!("provider=ollama base_url={base_url} model={model}");
            Arc::new(OllamaClient::new(base_url.clone(), model.clone(), timeout))
        }
        ProviderConfig::Anthropic {
            api_key,
            api_key_env,
            model,
            max_tokens,
        } => {
            let key = resolve_secret(api_key, api_key_env)?
                .context("provider.api_key or provider.api_key_env is required for anthropic")?;
            info!("provider=anthropic model={model}");
            Arc::new(AnthropicClient::new(
                key,
                model.clone(),
                *max_tokens,
                timeout,
            ))
        }
        ProviderConfig::OpenAI {
            api_key,
            api_key_env,
            model,
            base_url,
        } => {
            let key = resolve_secret(api_key, api_key_env)?;
            if key.is_none() && base_url.is_none() {
                anyhow::bail!("provider.api_key or provider.api_key_env is required for openai");
            }
            let base = base_url.as_deref().unwrap_or("https://api.openai.com/v1");
            info!("provider=openai base_url={base} model={model}");
            Arc::new(OpenAiClient::new(
                key,
                model.clone(),
                base_url.clone(),
                timeout,
            ))
        }
    })
}

pub fn build_tools(
    config: &Config,
    module_registry: Arc<Mutex<ModuleRegistry>>,
) -> Result<ToolRegistry> {
    let r = ToolRegistry::new();
    let tc = &config.tools;
    let needs_workspace = tc.filesystem.enabled || tc.exec.enabled || config.modules.enabled;
    if !needs_workspace {
        register_http(&r, config);
        return Ok(r);
    }
    let ws = Workspace::open(&config.workspace)?;
    info!("workspace={}", ws.root().display());

    if tc.exec.enabled {
        if tc.exec.allowed_programs.is_empty() {
            warn!(
                "tools.exec is enabled but allowed_programs is empty: run_program not registered"
            );
        } else {
            r.register(Arc::new(ExecTool::new(&tc.exec, ws.clone())));
        }
    }
    if tc.filesystem.enabled {
        r.register(Arc::new(ReadFileTool::new(
            ws.clone(),
            tc.filesystem.max_file_bytes,
        )));
        r.register(Arc::new(ListDirTool::new(ws.clone())));
        if tc.filesystem.allow_write {
            r.register(Arc::new(WriteFileTool::new(
                ws.clone(),
                tc.filesystem.max_file_bytes,
            )));
        }
    }
    register_http(&r, config);

    if config.modules.enabled {
        r.register(Arc::new(ListModulesTool::new(Arc::clone(&module_registry))));
        let timeout = Duration::from_secs(config.modules.timeout_seconds.max(1));
        let reg = module_registry.lock().unwrap();
        for manifest in &reg.manifests {
            if reg.state.installed.contains(&manifest.name) {
                for spec in &manifest.tools {
                    match ModuleTool::new(spec.clone(), ws.clone(), timeout) {
                        Ok(t) => {
                            r.register(Arc::new(t));
                        }
                        Err(e) => warn!("module tool {} not registered: {e}", spec.name),
                    }
                }
            }
        }
    }
    Ok(r)
}

fn register_http(r: &ToolRegistry, config: &Config) {
    if config.tools.http.enabled {
        r.register(Arc::new(FetchUrlTool::new(&config.tools.http)));
    }
}

pub fn load_policy(config: &Config) -> Result<Arc<Policy>> {
    Ok(Arc::new(match &config.policy_file {
        Some(path) => Policy::load(path)?,
        None => Policy::default(),
    }))
}

/// PostgreSQL when `database` is configured, otherwise memory (state is
/// lost on restart, fine for `run` and quick local use).
pub async fn build_store(config: &Config) -> Result<Arc<dyn Store>> {
    match &config.database {
        Some(db) => {
            let url = resolve_secret(&db.url, &db.url_env)?
                .context("database.url or database.url_env is required")?;
            info!("store=postgresql");
            Ok(Arc::new(PgStore::connect(&url, db.max_connections).await?))
        }
        None => {
            warn!("no database configured: run state and approvals are kept in memory only");
            Ok(Arc::new(MemoryStore::new()))
        }
    }
}

pub async fn build_core(
    config: Arc<Config>,
    module_registry: Arc<Mutex<ModuleRegistry>>,
) -> Result<Arc<Core>> {
    let router = ModelRouter::from_config(&config)?;
    let tools = build_tools(&config, module_registry)?;
    let policy = load_policy(&config)?;
    let store = build_store(&config).await?;
    let extra: Option<Arc<dyn Recorder>> = match &config.audit_log {
        Some(path) => Some(Arc::new(Audit::open(path)?)),
        None => None,
    };
    let (timeout, slots) = match &config.api {
        Some(api) => (api.approval_timeout_seconds, api.max_concurrent_runs),
        None => (3600, 4),
    };
    Ok(Core::new(
        Arc::clone(&config),
        router,
        tools,
        policy,
        store,
        Duration::from_secs(timeout.max(1)),
        slots,
        extra,
    ))
}
