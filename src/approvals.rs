//! Human approval of sensitive tool calls.
//!
//! When the policy answers `require_approval`, the agent stops at that call
//! and asks an [`Approver`]. The run waits until a person decides (through
//! the API, Telegram or the terminal) or the timeout expires, which counts
//! as a refusal.

use anyhow::Result;
use async_trait::async_trait;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::sync::oneshot;

use crate::store::{now_ms, Approval, ApprovalStatus, Store};
use crate::util::truncate_utf8;

#[derive(Debug, Clone)]
pub struct ApprovalRequest {
    pub tenant: String,
    pub run_id: String,
    pub tool: String,
    pub arguments: Value,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    Approved { by: String },
    Denied { by: String, note: Option<String> },
    Expired,
}

#[async_trait]
pub trait Approver: Send + Sync {
    /// Ask for a decision. Returns the approval id (when one was recorded)
    /// and the outcome.
    async fn request(&self, req: ApprovalRequest) -> Result<(Option<String>, Outcome)>;
}

/// Refuses everything: used where no person can be asked.
pub struct DenyAll;

#[async_trait]
impl Approver for DenyAll {
    async fn request(&self, _req: ApprovalRequest) -> Result<(Option<String>, Outcome)> {
        Ok((
            None,
            Outcome::Denied {
                by: "system".into(),
                note: Some("no approver is configured for this run".into()),
            },
        ))
    }
}

/// Arguments shown to the person deciding, with long strings shortened.
pub fn preview_arguments(v: &Value) -> Value {
    match v {
        Value::String(s) if s.len() > 2000 => {
            Value::String(format!("{}… [{} bytes]", truncate_utf8(s, 2000), s.len()))
        }
        Value::Array(a) => Value::Array(a.iter().map(preview_arguments).collect()),
        Value::Object(o) => Value::Object(
            o.iter()
                .map(|(k, v)| (k.clone(), preview_arguments(v)))
                .collect(),
        ),
        other => other.clone(),
    }
}

/// Called when an approval is created, e.g. to message someone.
pub type Notify = Arc<dyn Fn(&Approval) + Send + Sync>;

/// Approvals recorded in the store and decided by [`ApprovalQueue::decide`]
/// (the API and Telegram call it). Waiting runs are woken directly.
pub struct ApprovalQueue {
    store: Arc<dyn Store>,
    waiters: Mutex<HashMap<String, oneshot::Sender<Outcome>>>,
    timeout: Duration,
}

impl ApprovalQueue {
    pub fn new(store: Arc<dyn Store>, timeout: Duration) -> Arc<Self> {
        Arc::new(Self {
            store,
            waiters: Mutex::new(HashMap::new()),
            timeout,
        })
    }

    pub async fn request_with(
        &self,
        req: ApprovalRequest,
        notify: Option<&Notify>,
    ) -> Result<(Option<String>, Outcome)> {
        let approval = Approval {
            id: uuid::Uuid::new_v4().to_string(),
            tenant: req.tenant,
            run_id: req.run_id,
            tool: req.tool,
            arguments: preview_arguments(&req.arguments),
            reason: req.reason,
            status: ApprovalStatus::Pending,
            decided_by: None,
            decision_note: None,
            created_at_ms: now_ms(),
            decided_at_ms: None,
        };
        let (tx, rx) = oneshot::channel();
        self.waiters.lock().unwrap().insert(approval.id.clone(), tx);
        if let Err(e) = self.store.create_approval(&approval).await {
            self.waiters.lock().unwrap().remove(&approval.id);
            return Err(e);
        }
        if let Some(n) = notify {
            n(&approval);
        }
        let outcome = match tokio::time::timeout(self.timeout, rx).await {
            Ok(Ok(outcome)) => outcome,
            _ => {
                self.waiters.lock().unwrap().remove(&approval.id);
                self.store.expire_approval(&approval.id).await?;
                Outcome::Expired
            }
        };
        Ok((Some(approval.id), outcome))
    }

    /// Record a decision and wake the waiting run. `None` when the approval
    /// does not exist for this tenant or is no longer pending.
    pub async fn decide(
        &self,
        tenant: &str,
        id: &str,
        approve: bool,
        by: &str,
        note: Option<&str>,
    ) -> Result<Option<Approval>> {
        let decided = self
            .store
            .decide_approval(tenant, id, approve, by, note)
            .await?;
        if let Some(a) = &decided {
            if let Some(tx) = self.waiters.lock().unwrap().remove(&a.id) {
                let outcome = if approve {
                    Outcome::Approved { by: by.into() }
                } else {
                    Outcome::Denied {
                        by: by.into(),
                        note: note.map(str::to_string),
                    }
                };
                let _ = tx.send(outcome);
            }
        }
        Ok(decided)
    }

    pub fn store(&self) -> &Arc<dyn Store> {
        &self.store
    }
}

/// An [`Approver`] backed by the queue, with an optional notification.
pub struct QueueApprover {
    pub queue: Arc<ApprovalQueue>,
    pub notify: Option<Notify>,
}

#[async_trait]
impl Approver for QueueApprover {
    async fn request(&self, req: ApprovalRequest) -> Result<(Option<String>, Outcome)> {
        self.queue.request_with(req, self.notify.as_ref()).await
    }
}

/// Asks on the terminal (`eullm-agent run`).
pub struct TerminalApprover;

#[async_trait]
impl Approver for TerminalApprover {
    async fn request(&self, req: ApprovalRequest) -> Result<(Option<String>, Outcome)> {
        let args = serde_json::to_string_pretty(&preview_arguments(&req.arguments))?;
        let answer = tokio::task::spawn_blocking(move || -> std::io::Result<String> {
            use std::io::Write;
            println!(
                "\nApproval needed: {}\nReason: {}\nArguments: {}",
                req.tool, req.reason, args
            );
            print!("Allow this call? [y/N]: ");
            std::io::stdout().flush()?;
            let mut buf = String::new();
            std::io::stdin().read_line(&mut buf)?;
            Ok(buf)
        })
        .await??;
        let by = std::env::var("USER").unwrap_or_else(|_| "terminal".into());
        Ok((
            None,
            if matches!(answer.trim().to_lowercase().as_str(), "y" | "yes") {
                Outcome::Approved { by }
            } else {
                Outcome::Denied { by, note: None }
            },
        ))
    }
}
