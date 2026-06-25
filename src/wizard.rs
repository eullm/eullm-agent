use anyhow::Result;
use std::io::{self, Write};

use crate::config::{default_system_prompt, Config, ProviderConfig, TelegramConfig, ToolsConfig};

pub fn run(config_path: &std::path::Path) -> Result<Config> {
    println!("\nNo config file found at '{}'.", config_path.display());
    println!("Answer a few questions to get started.\n");

    let provider = pick_provider()?;
    let telegram = pick_telegram()?;

    let config = Config {
        provider,
        telegram,
        tools: ToolsConfig::default(),
        max_iterations: 20,
        system_prompt: default_system_prompt(),
    };

    let yaml = serde_yaml::to_string(&config)?;
    std::fs::write(config_path, &yaml)?;
    println!("\nConfig saved to '{}'. Starting...\n", config_path.display());

    Ok(config)
}

fn pick_provider() -> Result<ProviderConfig> {
    println!("Choose a provider:");
    println!("  1. Anthropic (Claude)  [default]");
    println!("  2. EULLM Engine (local)");
    println!("  3. OpenAI / compatible");
    println!();

    loop {
        let choice = prompt("Provider", "1")?;
        match choice.trim() {
            "1" | "" => return setup_anthropic(),
            "2" => return setup_eullm(),
            "3" => return setup_openai(),
            _ => println!("Enter 1, 2 or 3."),
        }
    }
}

fn setup_anthropic() -> Result<ProviderConfig> {
    println!();
    let api_key = prompt_required("Anthropic API key")?;
    let model = prompt("Model", "claude-sonnet-4-6")?;
    Ok(ProviderConfig::Anthropic { api_key, model })
}

fn setup_eullm() -> Result<ProviderConfig> {
    println!();
    let base_url = prompt("Server URL", "http://localhost:11434")?;
    let model = prompt_required("Model name")?;
    Ok(ProviderConfig::Eullm { base_url, model })
}

fn setup_openai() -> Result<ProviderConfig> {
    println!();
    let api_key = prompt_required("OpenAI API key")?;
    let model = prompt("Model", "gpt-4o")?;
    let base_url_raw = prompt("Base URL (blank = api.openai.com)", "")?;
    Ok(ProviderConfig::OpenAI {
        api_key,
        model,
        base_url: if base_url_raw.is_empty() { None } else { Some(base_url_raw) },
    })
}

fn pick_telegram() -> Result<Option<TelegramConfig>> {
    println!();
    let enable = prompt("Enable Telegram bot? [y/N]", "N")?;
    let enable_lower = enable.to_lowercase();
    if !matches!(enable_lower.trim(), "y" | "yes") {
        return Ok(None);
    }
    let token = prompt_required("Bot token")?;
    let ids_raw = prompt("Allowed user IDs, comma-separated (blank = allow all)", "")?;
    let allowed_users = ids_raw
        .split(',')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse::<i64>().ok())
        .collect();
    Ok(Some(TelegramConfig { token, allowed_users }))
}

fn prompt(label: &str, default: &str) -> Result<String> {
    if default.is_empty() {
        print!("{}: ", label);
    } else {
        print!("{} [{}]: ", label, default);
    }
    io::stdout().flush()?;
    let mut buf = String::new();
    io::stdin().read_line(&mut buf)?;
    let val = buf.trim().to_string();
    Ok(if val.is_empty() { default.to_string() } else { val })
}

fn prompt_required(label: &str) -> Result<String> {
    loop {
        print!("{}: ", label);
        io::stdout().flush()?;
        let mut buf = String::new();
        io::stdin().read_line(&mut buf)?;
        let val = buf.trim().to_string();
        if !val.is_empty() {
            return Ok(val);
        }
        println!("  (required — please enter a value)");
    }
}
