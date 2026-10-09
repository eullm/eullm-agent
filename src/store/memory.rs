use anyhow::{bail, Result};
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Mutex;

use super::*;

/// In-memory store: state is lost on restart.
#[derive(Default)]
pub struct MemoryStore {
    inner: Mutex<Inner>,
}

#[derive(Default)]
struct Inner {
    runs: HashMap<String, RunView>,
    order: Vec<String>,
    approvals: HashMap<String, Approval>,
    approval_order: Vec<String>,
    /// Single LLM calls outside a run, kept for completeness.
    loose_llm_calls: Vec<(String, LlmCallRecord)>,
    fetches: Vec<(String, FetchRecord)>,
}

impl MemoryStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Calls made through `/v1/llm/chat`, for tests.
    pub fn loose_llm_calls(&self) -> Vec<(String, LlmCallRecord)> {
        self.inner.lock().unwrap().loose_llm_calls.clone()
    }

    /// Requests made through `/v1/fetch`, for tests.
    pub fn fetches(&self) -> Vec<(String, FetchRecord)> {
        self.inner.lock().unwrap().fetches.clone()
    }
}

#[async_trait]
impl Store for MemoryStore {
    async fn create_run(&self, run: &NewRun) -> Result<()> {
        let mut g = self.inner.lock().unwrap();
        if g.runs.contains_key(&run.id) {
            bail!("run {} already exists", run.id);
        }
        g.runs.insert(
            run.id.clone(),
            RunView {
                summary: RunSummary {
                    id: run.id.clone(),
                    tenant: run.tenant.clone(),
                    profile: run.profile.clone(),
                    source: run.source.clone(),
                    status: RunStatus::Queued,
                    created_at_ms: now_ms(),
                    finished_at_ms: None,
                },
                input: run.input.clone(),
                output: None,
                error: None,
                iterations: 0,
                input_tokens: 0,
                output_tokens: 0,
                cost: None,
                tainted: false,
                steps: Vec::new(),
            },
        );
        g.order.push(run.id.clone());
        Ok(())
    }

    async fn set_run_status(&self, id: &str, status: RunStatus) -> Result<()> {
        if let Some(r) = self.inner.lock().unwrap().runs.get_mut(id) {
            r.summary.status = status;
        }
        Ok(())
    }

    async fn finish_run(&self, id: &str, end: &RunEnd) -> Result<()> {
        if let Some(r) = self.inner.lock().unwrap().runs.get_mut(id) {
            r.summary.status = if end.error.is_some() {
                RunStatus::Failed
            } else {
                RunStatus::Succeeded
            };
            r.summary.finished_at_ms = Some(now_ms());
            r.iterations = end.iterations as u64;
            r.input_tokens = end.input_tokens;
            r.output_tokens = end.output_tokens;
            r.cost = end.cost;
            r.tainted = end.tainted;
            r.output = end.output.clone();
            r.error = end.error.clone();
        }
        Ok(())
    }

    async fn get_run(&self, tenant: &str, id: &str) -> Result<Option<RunView>> {
        let g = self.inner.lock().unwrap();
        Ok(g.runs
            .get(id)
            .filter(|r| r.summary.tenant == tenant)
            .cloned())
    }

    async fn list_runs(&self, tenant: &str, limit: u32) -> Result<Vec<RunSummary>> {
        let g = self.inner.lock().unwrap();
        Ok(g.order
            .iter()
            .rev()
            .filter_map(|id| g.runs.get(id))
            .filter(|r| r.summary.tenant == tenant)
            .take(limit as usize)
            .map(|r| r.summary.clone())
            .collect())
    }

    async fn record_llm_call(
        &self,
        tenant: &str,
        run_id: Option<&str>,
        call: &LlmCallRecord,
    ) -> Result<()> {
        let mut g = self.inner.lock().unwrap();
        match run_id.and_then(|id| g.runs.get_mut(id)) {
            Some(r) => r.steps.push(Step::LlmCall {
                at_ms: now_ms(),
                call: call.clone(),
            }),
            None => g.loose_llm_calls.push((tenant.to_string(), call.clone())),
        }
        Ok(())
    }

    async fn record_fetch(&self, tenant: &str, fetch: &FetchRecord) -> Result<()> {
        self.inner
            .lock()
            .unwrap()
            .fetches
            .push((tenant.to_string(), fetch.clone()));
        Ok(())
    }

    async fn record_tool_call(
        &self,
        _tenant: &str,
        run_id: &str,
        call: &ToolCallRecord,
    ) -> Result<()> {
        if let Some(r) = self.inner.lock().unwrap().runs.get_mut(run_id) {
            r.steps.push(Step::ToolCall {
                at_ms: now_ms(),
                call: call.clone(),
            });
        }
        Ok(())
    }

    async fn create_approval(&self, approval: &Approval) -> Result<()> {
        let mut g = self.inner.lock().unwrap();
        if let Some(r) = g.runs.get_mut(&approval.run_id) {
            r.summary.status = RunStatus::WaitingApproval;
        }
        g.approvals.insert(approval.id.clone(), approval.clone());
        g.approval_order.push(approval.id.clone());
        Ok(())
    }

    async fn decide_approval(
        &self,
        tenant: &str,
        id: &str,
        approve: bool,
        by: &str,
        note: Option<&str>,
    ) -> Result<Option<Approval>> {
        let mut g = self.inner.lock().unwrap();
        let Some(a) = g.approvals.get_mut(id) else {
            return Ok(None);
        };
        if a.tenant != tenant || a.status != ApprovalStatus::Pending {
            return Ok(None);
        }
        a.status = if approve {
            ApprovalStatus::Approved
        } else {
            ApprovalStatus::Denied
        };
        a.decided_by = Some(by.to_string());
        a.decision_note = note.map(str::to_string);
        a.decided_at_ms = Some(now_ms());
        let a = a.clone();
        if let Some(r) = g.runs.get_mut(&a.run_id) {
            if r.summary.status == RunStatus::WaitingApproval {
                r.summary.status = RunStatus::Running;
            }
        }
        Ok(Some(a))
    }

    async fn expire_approval(&self, id: &str) -> Result<()> {
        if let Some(a) = self.inner.lock().unwrap().approvals.get_mut(id) {
            if a.status == ApprovalStatus::Pending {
                a.status = ApprovalStatus::Expired;
                a.decided_at_ms = Some(now_ms());
            }
        }
        Ok(())
    }

    async fn list_approvals(
        &self,
        tenant: &str,
        status: Option<ApprovalStatus>,
    ) -> Result<Vec<Approval>> {
        let g = self.inner.lock().unwrap();
        Ok(g.approval_order
            .iter()
            .rev()
            .filter_map(|id| g.approvals.get(id))
            .filter(|a| a.tenant == tenant && status.is_none_or(|s| a.status == s))
            .cloned()
            .collect())
    }
}
