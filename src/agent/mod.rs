use anyhow::{bail, Result};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tracing::{debug, info, warn};

use crate::audit::Audit;
use crate::config::LimitsConfig;
use crate::llm::{strip_think_blocks, LlmClient, Message, ToolCall};
use crate::tools::ToolRegistry;
use crate::util::truncate_utf8;

pub struct Agent<'a> {
    llm: &'a dyn LlmClient,
    tools: &'a ToolRegistry,
    max_iterations: usize,
    max_run: Duration,
    max_tool_output_bytes: usize,
    audit: Option<Arc<Audit>>,
    source: &'static str,
}

impl<'a> Agent<'a> {
    pub fn new(llm: &'a dyn LlmClient, tools: &'a ToolRegistry, max_iterations: usize) -> Self {
        let limits = LimitsConfig::default();
        Self {
            llm,
            tools,
            max_iterations,
            max_run: Duration::from_secs(limits.max_run_seconds),
            max_tool_output_bytes: limits.max_tool_output_bytes,
            audit: None,
            source: "cli",
        }
    }

    pub fn with_limits(mut self, limits: &LimitsConfig) -> Self {
        self.max_run = Duration::from_secs(limits.max_run_seconds.max(1));
        self.max_tool_output_bytes = limits.max_tool_output_bytes.max(256);
        self
    }

    pub fn with_audit(mut self, audit: Option<Arc<Audit>>, source: &'static str) -> Self {
        self.audit = audit;
        self.source = source;
        self
    }

    pub async fn run(
        &self,
        system_prompt: &str,
        task: &str,
        progress: impl Fn(&str),
    ) -> Result<String> {
        let run_id = new_run_id();
        if let Some(a) = &self.audit {
            a.run_start(
                &run_id,
                self.source,
                self.llm.provider_name(),
                self.llm.model(),
            );
        }
        let deadline = Instant::now() + self.max_run;
        let mut iterations = 0;
        let result = self
            .run_loop(
                &run_id,
                system_prompt,
                task,
                deadline,
                &mut iterations,
                &progress,
            )
            .await;
        if let Some(a) = &self.audit {
            let err = result.as_ref().err().map(|e| e.to_string());
            a.run_end(&run_id, iterations, err.as_deref().map_or(Ok(()), Err));
        }
        result
    }

    async fn run_loop(
        &self,
        run_id: &str,
        system_prompt: &str,
        task: &str,
        deadline: Instant,
        iterations: &mut usize,
        progress: &impl Fn(&str),
    ) -> Result<String> {
        let tool_defs = self.tools.definitions();
        let mut messages = vec![Message::system(system_prompt), Message::user(task)];

        for iteration in 0..self.max_iterations {
            *iterations = iteration + 1;
            debug!("iteration {}", iteration + 1);

            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                bail!("run time limit reached ({}s)", self.max_run.as_secs());
            }
            let started = Instant::now();
            let response =
                match tokio::time::timeout(remaining, self.llm.chat(&messages, &tool_defs)).await {
                    Ok(r) => r,
                    Err(_) => Err(anyhow::anyhow!(
                        "run time limit reached ({}s)",
                        self.max_run.as_secs()
                    )),
                };
            if let Some(a) = &self.audit {
                let err = response.as_ref().err().map(|e| e.to_string());
                a.llm_call(
                    run_id,
                    started.elapsed(),
                    response.as_ref().ok().and_then(|r| r.usage),
                    err.as_deref(),
                );
            }
            let response = response?;

            if response.tool_calls.is_empty() {
                info!("done after {} iterations", iteration + 1);
                return Ok(strip_think_blocks(&response.content));
            }

            messages.push(Message::assistant_with_tools(
                strip_think_blocks(&response.content),
                response.tool_calls.clone(),
            ));

            for tc in &response.tool_calls {
                let label = format!("tool:{}", tc.name);
                progress(&label);
                info!("{label}");

                let remaining = deadline.saturating_duration_since(Instant::now());
                let started = Instant::now();
                let result = match tokio::time::timeout(remaining, self.execute_tool(tc)).await {
                    Ok(r) => r,
                    Err(_) => Err(anyhow::anyhow!("stopped: run time limit reached")),
                };
                let result_text = match &result {
                    Ok(out) => self.cap_output(out),
                    Err(e) => {
                        warn!("tool {} error: {e}", tc.name);
                        format!("Error: {e}")
                    }
                };
                if let Some(a) = &self.audit {
                    let err = result.as_ref().err().map(|e| e.to_string());
                    let outcome = match (&result, err.as_deref()) {
                        (Ok(out), _) => Ok(out.len()),
                        (Err(_), Some(e)) => Err(e),
                        (Err(_), None) => Err("error"),
                    };
                    a.tool_call(run_id, &tc.name, &tc.arguments, started.elapsed(), outcome);
                }

                messages.push(Message::tool_result(&tc.id, result_text));
            }
        }

        bail!("exceeded max_iterations ({})", self.max_iterations)
    }

    async fn execute_tool(&self, tc: &ToolCall) -> Result<String> {
        match &tc.arguments {
            Value::Object(_) => self.tools.execute(&tc.name, &tc.arguments).await,
            // A model that sends malformed arguments is told so instead of
            // having the tool run with defaults.
            Value::String(raw) => bail!(
                "arguments for '{}' are not valid JSON: {}",
                tc.name,
                truncate_utf8(raw, 200)
            ),
            Value::Null => {
                self.tools
                    .execute(&tc.name, &Value::Object(Default::default()))
                    .await
            }
            _ => bail!("arguments for '{}' must be a JSON object", tc.name),
        }
    }

    fn cap_output(&self, out: &str) -> String {
        if out.len() <= self.max_tool_output_bytes {
            return out.to_string();
        }
        let shown = truncate_utf8(out, self.max_tool_output_bytes);
        format!(
            "{shown}\n[tool output truncated: {} of {} bytes shown]",
            shown.len(),
            out.len()
        )
    }
}

fn new_run_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!(
        "{ms:x}-{:x}-{:x}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}
