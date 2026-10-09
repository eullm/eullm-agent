//! Where the agent reports what it did.
//!
//! [`Recorder`] receives every run start, model call, tool call (with the
//! policy decision) and run end. [`Audit`] writes them to an append-only
//! JSONL file; [`StoreRecorder`] writes them to the Core's store.
//! Neither keeps tool argument values or outputs.

use anyhow::{Context, Result};
use async_trait::async_trait;
use serde_json::{json, Value};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::store::{LlmCallRecord, RunEnd, RunStatus, Store, ToolCallRecord};

/// Identity of a run, passed to every recorder call.
#[derive(Debug, Clone)]
pub struct RunInfo {
    pub id: String,
    pub tenant: String,
    pub profile: String,
    pub source: String,
    pub provider: String,
    pub model: String,
}

#[async_trait]
pub trait Recorder: Send + Sync {
    async fn run_start(&self, run: &RunInfo);
    async fn llm_call(&self, run: &RunInfo, call: &LlmCallRecord);
    async fn tool_call(&self, run: &RunInfo, call: &ToolCallRecord);
    async fn run_end(&self, run: &RunInfo, end: &RunEnd);
}

/// Sends every event to several recorders.
pub struct Recorders(pub Vec<Arc<dyn Recorder>>);

#[async_trait]
impl Recorder for Recorders {
    async fn run_start(&self, run: &RunInfo) {
        for r in &self.0 {
            r.run_start(run).await;
        }
    }
    async fn llm_call(&self, run: &RunInfo, call: &LlmCallRecord) {
        for r in &self.0 {
            r.llm_call(run, call).await;
        }
    }
    async fn tool_call(&self, run: &RunInfo, call: &ToolCallRecord) {
        for r in &self.0 {
            r.tool_call(run, call).await;
        }
    }
    async fn run_end(&self, run: &RunInfo, end: &RunEnd) {
        for r in &self.0 {
            r.run_end(run, end).await;
        }
    }
}

/// Append-only JSONL audit file (mode 0600 on Unix).
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

    fn write(&self, event: &str, run: &RunInfo, mut fields: Value) {
        let ts_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        fields["ts_ms"] = json!(ts_ms);
        fields["event"] = json!(event);
        fields["run"] = json!(run.id);
        let mut line = fields.to_string();
        line.push('\n');
        let mut f = self.file.lock().unwrap();
        // Auditing must never stop the agent; a failed write is logged.
        if let Err(e) = f.write_all(line.as_bytes()) {
            tracing::warn!("audit write failed: {e}");
        }
    }
}

#[async_trait]
impl Recorder for Audit {
    async fn run_start(&self, run: &RunInfo) {
        self.write(
            "run_start",
            run,
            json!({
                "tenant": run.tenant,
                "profile": run.profile,
                "source": run.source,
                "provider": run.provider,
                "model": run.model,
            }),
        );
    }

    async fn llm_call(&self, run: &RunInfo, call: &LlmCallRecord) {
        self.write(
            "llm_call",
            run,
            serde_json::to_value(call).unwrap_or_default(),
        );
    }

    async fn tool_call(&self, run: &RunInfo, call: &ToolCallRecord) {
        let mut v = serde_json::to_value(call).unwrap_or_default();
        v["ok"] = json!(call.error.is_none());
        self.write("tool_call", run, v);
    }

    async fn run_end(&self, run: &RunInfo, end: &RunEnd) {
        self.write(
            "run_end",
            run,
            json!({
                "iterations": end.iterations,
                "input_tokens": end.input_tokens,
                "output_tokens": end.output_tokens,
                "cost": end.cost,
                "tainted": end.tainted,
                "ok": end.error.is_none(),
                "error": end.error,
            }),
        );
    }
}

/// Writes run state and audit to the Core's store. The run row must exist
/// (created when the run was accepted).
pub struct StoreRecorder {
    pub store: Arc<dyn Store>,
}

#[async_trait]
impl Recorder for StoreRecorder {
    async fn run_start(&self, run: &RunInfo) {
        if let Err(e) = self.store.set_run_status(&run.id, RunStatus::Running).await {
            tracing::warn!("store: cannot mark run {} running: {e}", run.id);
        }
    }

    async fn llm_call(&self, run: &RunInfo, call: &LlmCallRecord) {
        if let Err(e) = self
            .store
            .record_llm_call(&run.tenant, Some(&run.id), call)
            .await
        {
            tracing::warn!("store: cannot record model call of run {}: {e}", run.id);
        }
    }

    async fn tool_call(&self, run: &RunInfo, call: &ToolCallRecord) {
        if let Err(e) = self
            .store
            .record_tool_call(&run.tenant, &run.id, call)
            .await
        {
            tracing::warn!("store: cannot record tool call of run {}: {e}", run.id);
        }
    }

    async fn run_end(&self, run: &RunInfo, end: &RunEnd) {
        if let Err(e) = self.store.finish_run(&run.id, end).await {
            tracing::warn!("store: cannot finish run {}: {e}", run.id);
        }
    }
}
