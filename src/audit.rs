//! Minimal append-only audit log (one JSON object per line).
//!
//! It records what the agent did, not what it saw: tool arguments are
//! reduced to their key names and no prompt or output text is written.

use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::llm::Usage;

pub struct Audit {
    file: Mutex<File>,
}

impl Audit {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let mut opts = OpenOptions::new();
        opts.create(true).append(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let file = opts
            .open(path)
            .with_context(|| format!("cannot open audit log {}", path.display()))?;
        Ok(Self {
            file: Mutex::new(file),
        })
    }

    fn write(&self, event: &str, mut fields: Value) {
        let ts_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        fields["ts_ms"] = json!(ts_ms);
        fields["event"] = json!(event);
        let mut line = fields.to_string();
        line.push('\n');
        let mut f = self.file.lock().unwrap();
        // Auditing must never stop the agent; a failed write is logged.
        if let Err(e) = f.write_all(line.as_bytes()) {
            tracing::warn!("audit write failed: {e}");
        }
    }

    pub fn run_start(&self, run_id: &str, source: &str, provider: &str, model: &str) {
        self.write(
            "run_start",
            json!({ "run": run_id, "source": source, "provider": provider, "model": model }),
        );
    }

    pub fn llm_call(
        &self,
        run_id: &str,
        duration: Duration,
        usage: Option<Usage>,
        error: Option<&str>,
    ) {
        self.write(
            "llm_call",
            json!({
                "run": run_id,
                "duration_ms": duration.as_millis() as u64,
                "input_tokens": usage.map(|u| u.input_tokens),
                "output_tokens": usage.map(|u| u.output_tokens),
                "error": error,
            }),
        );
    }

    pub fn tool_call(
        &self,
        run_id: &str,
        tool: &str,
        arguments: &Value,
        duration: Duration,
        outcome: Result<usize, &str>,
    ) {
        let keys: Vec<&str> = arguments
            .as_object()
            .map(|o| o.keys().map(String::as_str).collect())
            .unwrap_or_default();
        let (ok, output_bytes, error) = match outcome {
            Ok(n) => (true, Some(n), None),
            Err(e) => (false, None, Some(e)),
        };
        self.write(
            "tool_call",
            json!({
                "run": run_id,
                "tool": tool,
                "argument_keys": keys,
                "duration_ms": duration.as_millis() as u64,
                "ok": ok,
                "output_bytes": output_bytes,
                "error": error,
            }),
        );
    }

    pub fn run_end(&self, run_id: &str, iterations: usize, outcome: Result<(), &str>) {
        self.write(
            "run_end",
            json!({
                "run": run_id,
                "iterations": iterations,
                "ok": outcome.is_ok(),
                "error": outcome.err(),
            }),
        );
    }
}
