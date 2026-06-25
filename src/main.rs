use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tracing::info;
use tracing_subscriber::EnvFilter;

mod agent;
mod config;
mod llm;
mod modules;
mod telegram;
mod tools;
mod wizard;

use config::{Config, ProviderConfig};
use llm::{
    anthropic::AnthropicClient,
    eullm::EullmClient,
    openai::OpenAiClient,
};
use modules::ModuleRegistry;
use tools::{
    filesystem::{ListDirTool, ReadFileTool, WriteFileTool},
    http::FetchUrlTool,
    module_tool::{InstallModuleTool, ListModulesTool, ModuleTool},
    shell::ShellTool,
    ToolRegistry,
};

#[derive(Parser)]
#[command(name = "eullm-agent", version, about = "EULLM autonomous task agent")]
struct Cli {
    #[arg(short, long, default_value = "config.yaml")]
    config: PathBuf,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Start the Telegram bot and wait for tasks
    Serve,
    /// Run a one-shot task from the CLI and print the result
    Run {
        /// Task description
        task: String,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::from_default_env()
                .add_directive("eullm_agent=info".parse()?),
        )
        .init();

    let cli = Cli::parse();

    let mut config = if cli.config.exists() {
        Config::load(&cli.config)
            .with_context(|| format!("Cannot load config from {:?}", cli.config))?
    } else {
        wizard::run(&cli.config)?
    };

    let module_registry = Arc::new(Mutex::new(
        ModuleRegistry::load(module_state_path())?
    ));

    // Augment system prompt with current module status
    {
        let reg = module_registry.lock().unwrap();
        config.system_prompt.push_str(&reg.status_summary());
    }

    let llm: Arc<dyn llm::LlmClient> = match &config.provider {
        ProviderConfig::Eullm { base_url, model } => {
            info!("provider=eullm base_url={base_url} model={model}");
            Arc::new(EullmClient::new(base_url, model))
        }
        ProviderConfig::Anthropic { api_key, model } => {
            info!("provider=anthropic model={model}");
            Arc::new(AnthropicClient::new(api_key, model))
        }
        ProviderConfig::OpenAI { api_key, model, base_url } => {
            let base = base_url.as_deref().unwrap_or("https://api.openai.com/v1");
            info!("provider=openai base_url={base} model={model}");
            Arc::new(OpenAiClient::new(api_key, model, base_url.clone()))
        }
    };

    let tools = build_tool_registry(&config, Arc::clone(&module_registry));

    match cli.command {
        Commands::Serve => {
            telegram::serve(Arc::new(config), llm, Arc::new(tools)).await?;
        }
        Commands::Run { task } => {
            let agent = agent::Agent::new(llm.as_ref(), &tools, config.max_iterations);
            let result = agent
                .run(&config.system_prompt, &task, |s| println!("[\u{2022}] {s}"))
                .await?;
            println!("{result}");
        }
    }

    Ok(())
}

fn build_tool_registry(
    config: &Config,
    module_registry: Arc<Mutex<ModuleRegistry>>,
) -> ToolRegistry {
    let r = ToolRegistry::new();
    let tc = &config.tools;

    if tc.shell.enabled {
        r.register(Arc::new(ShellTool::new(tc.shell.allow_sudo, tc.shell.timeout_seconds)));
    }
    if tc.filesystem.enabled {
        let paths = tc.filesystem.allowed_paths.clone();
        r.register(Arc::new(ReadFileTool::new(paths.clone())));
        r.register(Arc::new(WriteFileTool::new(paths)));
        r.register(Arc::new(ListDirTool));
    }
    if tc.http.enabled {
        r.register(Arc::new(FetchUrlTool::new(tc.http.timeout_seconds)));
    }

    // Module management tools (always available)
    r.register(Arc::new(ListModulesTool::new(Arc::clone(&module_registry))));
    // InstallModuleTool gets a clone of r so it can register new tools at runtime
    r.register(Arc::new(InstallModuleTool::new(Arc::clone(&module_registry), r.clone())));

    // Register tools from already-installed modules
    {
        let reg = module_registry.lock().unwrap();
        for manifest in &reg.manifests {
            if reg.state.installed.contains(&manifest.name) {
                for spec in &manifest.tools {
                    r.register(Arc::new(ModuleTool::new(spec.clone())));
                }
            }
        }
    }

    r
}

fn module_state_path() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".eullm-agent").join("module-state.json")
}
