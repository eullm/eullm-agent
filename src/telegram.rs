use anyhow::Result;
use std::sync::Arc;
use teloxide::prelude::*;
use tracing::{info, warn};

use crate::{agent::Agent, config::Config, llm::LlmClient, tools::ToolRegistry};

struct BotState {
    llm: Arc<dyn LlmClient>,
    tools: Arc<ToolRegistry>,
    config: Arc<Config>,
}

pub async fn serve(
    config: Arc<Config>,
    llm: Arc<dyn LlmClient>,
    tools: Arc<ToolRegistry>,
) -> Result<()> {
    let tg = config
        .telegram
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("Missing [telegram] section in config.yaml"))?;

    let bot = Bot::new(&tg.token);
    info!("Telegram bot started");

    let state = Arc::new(BotState { llm, tools, config });

    teloxide::repl(bot, move |bot: Bot, msg: Message| {
        let state = Arc::clone(&state);
        async move {
            if let Err(e) = dispatch(bot, msg, state).await {
                warn!("dispatch error: {e}");
            }
            respond(())
        }
    })
    .await;

    Ok(())
}

async fn dispatch(bot: Bot, msg: Message, state: Arc<BotState>) -> Result<()> {
    let tg = state.config.telegram.as_ref().unwrap();

    let uid = msg.from().map(|u| u.id.0 as i64).unwrap_or(0);
    if !tg.allowed_users.is_empty() && !tg.allowed_users.contains(&uid) {
        warn!("Rejected unauthorized user {uid}");
        return Ok(());
    }

    let text = match msg.text() {
        Some(t) => t,
        None => return Ok(()),
    };

    if text == "/help" || text.starts_with("/help ") {
        bot.send_message(
            msg.chat.id,
            "/run <task> — execute a task\n/status — show provider & tools\n/help — this message",
        )
        .await?;
        return Ok(());
    }

    if text == "/status" {
        let defs = state.tools.definitions();
        let tool_names: Vec<&str> = defs.iter().map(|t| t.name.as_str()).collect();
        bot.send_message(
            msg.chat.id,
            format!(
                "Provider: {}\nTools: {}",
                state.llm.provider_name(),
                tool_names.join(", "),
            ),
        )
        .await?;
        return Ok(());
    }

    if let Some(task) = text.strip_prefix("/run ") {
        let task = task.trim().to_string();
        if task.is_empty() {
            bot.send_message(msg.chat.id, "Usage: /run <task description>").await?;
            return Ok(());
        }

        bot.send_message(msg.chat.id, format!("▶ {task}")).await?;

        let (tx, mut rx) = tokio::sync::mpsc::channel::<String>(16);
        let bot_p = bot.clone();
        let chat_id = msg.chat.id;
        tokio::spawn(async move {
            while let Some(m) = rx.recv().await {
                let _ = bot_p.send_message(chat_id, m).await;
            }
        });

        let system = state.config.system_prompt.clone();
        let max_iter = state.config.max_iterations;
        let agent = Agent::new(state.llm.as_ref(), &state.tools, max_iter);

        match agent.run(&system, &task, move |s| { let _ = tx.try_send(s.to_string()); }).await {
            Ok(answer) => {
                let reply = if answer.len() > 4000 {
                    format!("{}…\n[truncated]", &answer[..4000])
                } else {
                    answer
                };
                bot.send_message(chat_id, reply).await?;
            }
            Err(e) => {
                bot.send_message(chat_id, format!("❌ {e}")).await?;
            }
        }
        return Ok(());
    }

    Ok(())
}
