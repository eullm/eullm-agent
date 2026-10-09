//! The Core as a service: accepts runs, executes them in the background
//! under a profile, records everything in the store, and serves single
//! model calls with accounting.

use anyhow::{bail, Context, Result};
use reqwest::{Method, Url};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{Mutex, Semaphore};

use crate::agent::Agent;
use crate::approvals::{ApprovalQueue, Approver, Notify, QueueApprover};
use crate::audit::{Recorder, Recorders, StoreRecorder};
use crate::config::Config;
use crate::llm::{ChatResponse, Message, ToolDefinition};
use crate::policy::Policy;
use crate::router::ModelRouter;
use crate::store::{FetchRecord, LlmCallRecord, NewRun, RunEnd, Store};
use crate::tools::http::{settable_headers, Fetched, Fetcher};
use crate::tools::net_guard::NetPolicy;
use crate::tools::ToolRegistry;

pub struct Core {
    pub config: Arc<Config>,
    pub router: ModelRouter,
    pub tools: ToolRegistry,
    pub policy: Arc<Policy>,
    pub store: Arc<dyn Store>,
    pub approvals: Arc<ApprovalQueue>,
    /// Extra recorder (e.g. the JSONL audit file) besides the store.
    pub extra_recorder: Option<Arc<dyn Recorder>>,
    slots: Arc<Semaphore>,
    fetch: Option<FetchService>,
}

/// `POST /v1/fetch` behind the address checks, plus a per-host pace shared
/// by every caller.
struct FetchService {
    fetcher: Fetcher,
    interval: Duration,
    next_slot: Mutex<HashMap<String, Instant>>,
}

#[derive(Debug)]
pub enum FetchError {
    /// The configuration has no `api.fetch` section.
    Disabled,
    /// Refused before any request: address, scheme, header.
    Refused(String),
    /// The request was made and failed.
    Failed(String),
}

pub struct StartRun {
    pub tenant: String,
    pub profile: String,
    pub input: String,
    pub source: String,
    /// Called when one of this run's actions needs approval.
    pub notify: Option<Notify>,
}

impl Core {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        config: Arc<Config>,
        router: ModelRouter,
        tools: ToolRegistry,
        policy: Arc<Policy>,
        store: Arc<dyn Store>,
        approval_timeout: Duration,
        max_concurrent_runs: usize,
        extra_recorder: Option<Arc<dyn Recorder>>,
    ) -> Arc<Self> {
        Arc::new(Self {
            router,
            tools,
            policy,
            approvals: ApprovalQueue::new(Arc::clone(&store), approval_timeout),
            store,
            extra_recorder,
            slots: Arc::new(Semaphore::new(max_concurrent_runs.max(1))),
            fetch: config
                .api
                .as_ref()
                .and_then(|a| a.fetch.as_ref())
                .map(|f| FetchService {
                    fetcher: Fetcher::new(
                        NetPolicy {
                            allow_http: f.allow_http,
                            allow_private_networks: f.allow_private_networks,
                            allowed_domains: Vec::new(),
                        },
                        Duration::from_secs(f.timeout_seconds.max(1)),
                        f.max_response_bytes,
                        f.max_redirects,
                        f.user_agent.clone(),
                    ),
                    interval: Duration::from_millis(f.min_host_interval_ms),
                    next_slot: Mutex::new(HashMap::new()),
                }),
            config,
        })
    }

    fn prepare(&self, req: &StartRun) -> Result<crate::config::ProfileConfig> {
        let profile = self
            .config
            .profile(&req.profile)
            .with_context(|| format!("unknown profile '{}'", req.profile))?;
        if self.router.get(&profile.model).is_none() {
            bail!(
                "profile '{}' uses unknown model '{}'",
                req.profile,
                profile.model
            );
        }
        Ok(profile)
    }

    async fn record_new(&self, req: &StartRun) -> Result<String> {
        let id = uuid::Uuid::new_v4().to_string();
        self.store
            .create_run(&NewRun {
                id: id.clone(),
                tenant: req.tenant.clone(),
                profile: req.profile.clone(),
                source: req.source.clone(),
                input: req.input.clone(),
            })
            .await?;
        Ok(id)
    }

    /// Validate and record a run, then execute it in the background.
    /// Returns the run id at once.
    pub async fn start_run(self: &Arc<Self>, req: StartRun) -> Result<String> {
        let profile = self.prepare(&req)?;
        let id = self.record_new(&req).await?;
        let core = Arc::clone(self);
        let run_id = id.clone();
        tokio::spawn(async move {
            let _permit = core.slots.clone().acquire_owned().await;
            let _ = core.execute(&run_id, &req, &profile).await;
        });
        Ok(id)
    }

    /// Record a run and execute it now, returning its answer.
    pub async fn run_now(&self, req: StartRun) -> Result<(String, Result<String>)> {
        let profile = self.prepare(&req)?;
        let id = self.record_new(&req).await?;
        let _permit = self.slots.clone().acquire_owned().await;
        let result = self.execute(&id, &req, &profile).await;
        Ok((id, result))
    }

    async fn execute(
        &self,
        run_id: &str,
        req: &StartRun,
        profile: &crate::config::ProfileConfig,
    ) -> Result<String> {
        let Some(model) = self.router.get(&profile.model).cloned() else {
            let err = format!("unknown model '{}'", profile.model);
            let _ = self
                .store
                .finish_run(
                    run_id,
                    &RunEnd {
                        error: Some(err.clone()),
                        ..Default::default()
                    },
                )
                .await;
            bail!(err);
        };
        let mut recorders: Vec<Arc<dyn Recorder>> = vec![Arc::new(StoreRecorder {
            store: Arc::clone(&self.store),
        })];
        if let Some(r) = &self.extra_recorder {
            recorders.push(Arc::clone(r));
        }
        let approver: Arc<dyn Approver> = Arc::new(QueueApprover {
            queue: Arc::clone(&self.approvals),
            notify: req.notify.clone(),
        });
        let system_prompt = profile
            .system_prompt
            .clone()
            .unwrap_or_else(|| self.config.system_prompt.clone());
        let agent = Agent::new(
            model.client.as_ref(),
            &self.tools,
            self.config.max_iterations,
        )
        .with_limits(&self.config.limits)
        .with_profile(&req.profile, profile)
        .with_pricing(model.pricing)
        .with_policy(Arc::clone(&self.policy))
        .with_approver(approver)
        .with_recorder(Some(Arc::new(Recorders(recorders))))
        .with_identity(&req.tenant, &req.source);
        agent
            .run_with_id(run_id, &system_prompt, &req.input, |_| {})
            .await
    }

    /// One model call through the router, recorded with its usage and cost.
    pub async fn chat(
        &self,
        tenant: &str,
        model_name: &str,
        messages: &[Message],
        tools: &[ToolDefinition],
    ) -> Result<(ChatResponse, Option<f64>)> {
        let model = self
            .router
            .get(model_name)
            .with_context(|| format!("unknown model '{model_name}'"))?
            .clone();
        let started = Instant::now();
        let timeout = Duration::from_secs(self.config.limits.llm_timeout_seconds.max(1));
        let result = match tokio::time::timeout(timeout, model.client.chat(messages, tools)).await {
            Ok(r) => r,
            Err(_) => Err(anyhow::anyhow!("model call timed out")),
        };
        let usage = result.as_ref().ok().and_then(|r| r.usage);
        let cost = match (usage, model.pricing) {
            (Some(u), Some(p)) => Some(
                (u.input_tokens as f64 * p.input_per_mtok
                    + u.output_tokens as f64 * p.output_per_mtok)
                    / 1_000_000.0,
            ),
            _ => None,
        };
        let record = LlmCallRecord {
            provider: model.client.provider_name().to_string(),
            model: model.client.model().to_string(),
            duration_ms: started.elapsed().as_millis() as u64,
            input_tokens: usage.map(|u| u.input_tokens),
            output_tokens: usage.map(|u| u.output_tokens),
            cost,
            error: result.as_ref().err().map(|e| e.to_string()),
        };
        if let Err(e) = self.store.record_llm_call(tenant, None, &record).await {
            tracing::warn!("store: cannot record model call: {e}");
        }
        Ok((result?, cost))
    }

    /// One GET on behalf of a tenant, recorded with its outcome.
    pub async fn fetch(
        &self,
        tenant: &str,
        url: &str,
        headers: &[(String, String)],
    ) -> std::result::Result<Fetched, FetchError> {
        let svc = self.fetch.as_ref().ok_or(FetchError::Disabled)?;
        let url = Url::parse(url).map_err(|e| FetchError::Refused(format!("invalid URL: {e}")))?;
        settable_headers(headers).map_err(|e| FetchError::Refused(e.to_string()))?;
        let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
        let wait = {
            let mut slots = svc.next_slot.lock().await;
            let now = Instant::now();
            let at = slots
                .get(&host)
                .copied()
                .filter(|t| *t > now)
                .unwrap_or(now);
            slots.insert(host, at + svc.interval);
            at - now
        };
        tokio::time::sleep(wait).await;

        let mut shown = url.clone();
        shown.set_query(None);
        shown.set_fragment(None);
        let started = Instant::now();
        let result = svc.fetcher.fetch(Method::GET, url, headers, None).await;
        let record = FetchRecord {
            url: shown.to_string(),
            status: result.as_ref().ok().map(|f| f.status),
            bytes: result.as_ref().map(|f| f.body.len() as u64).unwrap_or(0),
            duration_ms: started.elapsed().as_millis() as u64,
            error: result.as_ref().err().map(|e| e.to_string()),
        };
        if let Err(e) = self.store.record_fetch(tenant, &record).await {
            tracing::warn!("store: cannot record fetch: {e}");
        }
        result.map_err(|e| {
            let msg = e.to_string();
            if msg.starts_with("Blocked") || msg.contains("cannot be set") {
                FetchError::Refused(msg)
            } else {
                FetchError::Failed(msg)
            }
        })
    }
}
