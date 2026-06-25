use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::{path::PathBuf, sync::Arc};
use tracing::info;
use tracing_subscriber::EnvFilter;

mod agent;
mod config;
mod llm;
mod telegram;
mod tools;

use config::{Config, ProviderConfig};
use llm::{
    anthropic::AnthropicClient,
    eullm::EullmClient,
    openai::OpenAiClient,
};
use tools::{
    filesystem::{ListDirTool, ReadFileTool, WriteFileTool},
    http::FetchUrlTool,
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
    let config = Config::load(&cli.config)
        .with_context(|| format!("Cannot load config from {:?}", cli.config))?;

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

    let tools = build_tool_registry(&config);

    match cli.command {
        Commands::Serve => {
            telegram::serve(Arc::new(config), llm, Arc::new(tools)).await?;
        }
        Commands::Run { task } => {
            let agent = agent::Agent::new(llm.as_ref(), &tools, config.max_iterations);
            let result = agent
                .run(&config.system_prompt, &task, |s| println!("[•] {s}"))
                .await?;
            println!("{result}");
        }
    }

    Ok(())
}

fn build_tool_registry(config: &Config) -> ToolRegistry {
    let mut r = ToolRegistry::new();
    let tc = &config.tools;

    if tc.shell.enabled {
        r.register(Box::new(ShellTool::new(
            tc.shell.allow_sudo,
            tc.shell.timeout_seconds,
        )));
    }

    if tc.filesystem.enabled {
        let paths = tc.filesystem.allowed_paths.clone();
        r.register(Box::new(ReadFileTool::new(paths.clone())));
        r.register(Box::new(WriteFileTool::new(paths)));
        r.register(Box::new(ListDirTool));
    }

    if tc.http.enabled {
        r.register(Box::new(FetchUrlTool::new(tc.http.timeout_seconds)));
    }

    r
}
