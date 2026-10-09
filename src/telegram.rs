use anyhow::Result;
use std::sync::Arc;
use teloxide::prelude::*;
use tracing::{info, warn};

use crate::{
    agent::Agent, audit::Audit, config::resolve_secret, config::Config, llm::LlmClient,
    tools::ToolRegistry, util::truncate_utf8,
};

struct BotState {
    llm: Arc<dyn LlmClient>,
    tools: Arc<ToolRegistry>,
    config: Arc<Config>,
    audit: Option<Arc<Audit>>,
}

/// Longest answer sent in one Telegram message (the API limit is 4096).
const MAX_REPLY_BYTES: usize = 4000;

pub async fn serve(
    config: Arc<Config>,
    llm: Arc<dyn LlmClient>,
    tools: Arc<ToolRegistry>,
    audit: Option<Arc<Audit>>,
) -> Result<()> {
    let tg = config.validate_telegram()?;
    let token = resolve_secret(&tg.token, &tg.token_env)?
        .ok_or_else(|| anyhow::anyhow!("telegram.token or telegram.token_env is required"))?;

    let bot = Bot::new(token);
    info!(
        "Telegram bot started, {} allowed user(s)",
        tg.allowed_users.len()
    );

    let state = Arc::new(BotState {
        llm,
        tools,
        config,
        audit,
    });

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
    let Some(tg) = state.config.telegram.as_ref() else {
        return Ok(());
    };
    // Channel posts and anonymous admins have no sender: never authorised.
    let Some(uid) = msg.from().map(|u| u.id.0 as i64) else {
        warn!("Rejected message without a sender");
        return Ok(());
    };
    if !is_allowed(&tg.allowed_users, uid) {
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
            bot.send_message(msg.chat.id, "Usage: /run <task description>")
                .await?;
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
        let agent = Agent::new(state.llm.as_ref(), &state.tools, max_iter)
            .with_limits(&state.config.limits)
            .with_audit(state.audit.clone(), "telegram");

        match agent
            .run(&system, &task, move |s| {
                let _ = tx.try_send(s.to_string());
            })
            .await
        {
            Ok(answer) => {
                let reply = if answer.len() > MAX_REPLY_BYTES {
                    format!("{}…\n[truncated]", truncate_utf8(&answer, MAX_REPLY_BYTES))
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

/// Only listed users may send tasks; an empty list allows nobody.
pub fn is_allowed(allowed_users: &[i64], uid: i64) -> bool {
    allowed_users.contains(&uid)
}
