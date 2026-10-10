use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::io::{self, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tracing::warn;
use tracing_subscriber::EnvFilter;

use eullm_agent::approvals::TerminalApprover;
use eullm_agent::audit::Audit;
use eullm_agent::config::Config;
use eullm_agent::modules::ModuleRegistry;
use eullm_agent::{agent, api, setup, telegram, wizard};

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
    /// Start the Core HTTP API (runs, model calls, approvals)
    Api {
        /// Address to listen on (overrides api.listen)
        #[arg(long)]
        listen: Option<String>,
    },
    /// List or install modules (operator only; the agent cannot install them)
    Module {
        #[command(subcommand)]
        action: ModuleAction,
    },
    /// Manage API tokens
    Token {
        #[command(subcommand)]
        action: TokenAction,
    },
}

#[derive(Subcommand)]
enum TokenAction {
    /// Print a new random token and the SHA-256 to put in api.tokens
    New,
}

#[derive(Subcommand)]
enum ModuleAction {
    /// Show available and installed modules
    List,
    /// Run a module's install commands and enable its tools
    Install {
        name: String,
        /// Do not ask for confirmation
        #[arg(long)]
        yes: bool,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("eullm_agent=info".parse()?))
        .init();

    let cli = Cli::parse();

    let module_registry = Arc::new(Mutex::new(ModuleRegistry::load(module_state_path())?));

    if let Commands::Module { action } = &cli.command {
        return module_command(action, &module_registry);
    }
    if let Commands::Token {
        action: TokenAction::New,
    } = &cli.command
    {
        let token = api::new_token();
        println!("token:        {token}");
        println!("token_sha256: {}", api::sha256_hex(&token));
        println!("\nGive the token to the client; put only token_sha256 in api.tokens.");
        return Ok(());
    }

    let mut config = if cli.config.exists() {
        Config::load(&cli.config)
            .with_context(|| format!("Cannot load config from {:?}", cli.config))?
    } else {
        wizard::run(&cli.config)?
    };
    for w in config.deprecation_warnings() {
        warn!("{w}");
    }

    if config.modules.enabled {
        let reg = module_registry.lock().unwrap();
        config.system_prompt.push_str(&reg.status_summary());
    }

    match cli.command {
        Commands::Serve => {
            config.validate_telegram()?;
            let core = setup::build_core(Arc::new(config), Arc::clone(&module_registry)).await?;
            setup::recover_runs(&core, "telegram:").await?;
            telegram::serve(core).await?;
        }
        Commands::Api { listen } => {
            let api_cfg = config
                .api
                .clone()
                .context("missing api section in the config")?;
            let tokens = api_cfg
                .tokens
                .iter()
                .map(api::ApiToken::resolve)
                .collect::<Result<Vec<_>>>()?;
            let listen = listen.unwrap_or(api_cfg.listen);
            let core = setup::build_core(Arc::new(config), Arc::clone(&module_registry)).await?;
            setup::recover_runs(&core, "api:").await?;
            api::serve(core, &listen, tokens).await?;
        }
        Commands::Run { task } => {
            let router = eullm_agent::router::ModelRouter::from_config(&config)?;
            let profile = config.profile("default").unwrap_or_default();
            let model = router
                .get(&profile.model)
                .context("the default profile uses an unknown model")?
                .clone();
            let tools = setup::build_tools(&config, Arc::clone(&module_registry))?;
            let policy = setup::load_policy(&config)?;
            let audit = match &config.audit_log {
                Some(path) => Some(Arc::new(Audit::open(path)?)),
                None => None,
            };
            let system_prompt = profile
                .system_prompt
                .clone()
                .unwrap_or_else(|| config.system_prompt.clone());
            let agent = agent::Agent::new(model.client.as_ref(), &tools, config.max_iterations)
                .with_limits(&config.limits)
                .with_profile("default", &profile)
                .with_pricing(model.pricing)
                .with_policy(policy)
                .with_approver(Arc::new(TerminalApprover))
                .with_audit(audit, "cli");
            let result = agent
                .run(&system_prompt, &task, |s| println!("[\u{2022}] {s}"))
                .await?;
            println!("{result}");
        }
        Commands::Module { .. } | Commands::Token { .. } => unreachable!(),
    }

    Ok(())
}

fn module_command(action: &ModuleAction, registry: &Arc<Mutex<ModuleRegistry>>) -> Result<()> {
    let mut reg = registry.lock().unwrap();
    match action {
        ModuleAction::List => print!("{}", reg.listing()),
        ModuleAction::Install { name, yes } => {
            let manifest = reg
                .manifests
                .iter()
                .find(|m| &m.name == name)
                .with_context(|| format!("Unknown module '{name}'"))?;
            println!("Module '{name}' will run these commands on this machine:");
            for cmd in manifest.install_commands() {
                println!("  {cmd}");
            }
            if !yes {
                print!("Continue? [y/N]: ");
                io::stdout().flush()?;
                let mut buf = String::new();
                io::stdin().read_line(&mut buf)?;
                if !matches!(buf.trim().to_lowercase().as_str(), "y" | "yes") {
                    println!("Aborted.");
                    return Ok(());
                }
            }
            reg.install(name)?;
            println!(
                "Module '{name}' installed. Set `modules.enabled: true` in the config to use it."
            );
        }
    }
    Ok(())
}

fn module_state_path() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home)
        .join(".eullm-agent")
        .join("module-state.json")
}
