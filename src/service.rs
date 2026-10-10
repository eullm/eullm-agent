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
use crate::config::{Config, TenantLimits};
use crate::llm::{ChatResponse, Message, ToolDefinition};
use crate::policy::Policy;
use crate::router::ModelRouter;
use crate::store::{FetchRecord, LlmCallRecord, NewRun, RunEnd, Store, Usage};
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
    /// Runs and fetches that passed the limit check and are not in the
    /// store yet, so that concurrent requests cannot all slip under a limit.
    in_flight: InFlight,
    /// Serialises "check, then reserve" within this process.
    limit_gate: Mutex<()>,
}

type InFlight = Arc<std::sync::Mutex<HashMap<(String, Consume), u64>>>;

/// A slot counted against a tenant's limit until the work is in the store.
pub struct Reservation {
    in_flight: InFlight,
    key: (String, Consume),
}

impl Drop for Reservation {
    fn drop(&mut self) {
        let mut m = self.in_flight.lock().unwrap();
        if let Some(n) = m.get_mut(&self.key) {
            *n = n.saturating_sub(1);
            if *n == 0 {
                m.remove(&self.key);
            }
        }
    }
}

/// Re-checks a tenant's monthly budget before every model call of a run, so
/// one long run cannot overshoot it.
pub struct TenantBudget {
    store: Arc<dyn Store>,
    tenant: String,
    limits: TenantLimits,
}

#[async_trait::async_trait]
impl crate::agent::BudgetGuard for TenantBudget {
    async fn check(&self) -> Result<()> {
        check_tenant_limits(
            self.store.as_ref(),
            &self.limits,
            &self.tenant,
            Consume::ModelCall,
            0,
        )
        .await
    }
}

/// Refuse when the tenant has reached a limit that applies to `what`;
/// `pending` counts work already admitted but not yet recorded.
async fn check_tenant_limits(
    store: &dyn Store,
    l: &TenantLimits,
    tenant: &str,
    what: Consume,
    pending: u64,
) -> Result<()> {
    let (day_start, month_start) = period_starts(now_ms());
    let monthly = l.max_cost_per_month.is_some() || l.max_tokens_per_month.is_some();
    // Runs call the model too, so they count against the monthly budget.
    if matches!(what, Consume::Run | Consume::ModelCall) && monthly {
        let m = store.usage(tenant, month_start).await?;
        if let Some(max) = l.max_cost_per_month {
            if m.cost >= max {
                return Err(LimitReached(format!(
                    "monthly cost limit reached ({:.2} of {max:.2})",
                    m.cost
                ))
                .into());
            }
        }
        if let Some(max) = l.max_tokens_per_month {
            let used = m.input_tokens + m.output_tokens;
            if used >= max {
                return Err(
                    LimitReached(format!("monthly token limit reached ({used} of {max})")).into(),
                );
            }
        }
    }
    let daily = match what {
        Consume::Run => l.max_runs_per_day.map(|max| ("runs", max)),
        Consume::Fetch => l.max_fetches_per_day.map(|max| ("fetches", max)),
        Consume::ModelCall => None,
    };
    if let Some((name, max)) = daily {
        let d = store.usage(tenant, day_start).await?;
        let used = pending
            + if what == Consume::Run {
                d.runs
            } else {
                d.fetches
            };
        if used >= max {
            return Err(
                LimitReached(format!("daily {name} limit reached ({used} of {max})")).into(),
            );
        }
    }
    Ok(())
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
    /// The tenant has used up its fetches for today.
    Limited(String),
}

/// What a caller is about to consume, checked against `api.tenants`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Consume {
    Run,
    ModelCall,
    Fetch,
}

/// A tenant's limit was reached; the message says which.
#[derive(Debug)]
pub struct LimitReached(pub String);

impl std::fmt::Display for LimitReached {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LimitReached {}

/// Usage of a tenant in the current UTC day and month.
#[derive(Debug, Clone, serde::Serialize)]
pub struct TenantUsage {
    pub day: Usage,
    pub month: Usage,
    pub limits: TenantLimits,
}

const DAY_MS: i64 = 86_400_000;

/// Start of the UTC day and of the UTC month containing `now_ms`.
pub fn period_starts(now_ms: i64) -> (i64, i64) {
    let days = now_ms.div_euclid(DAY_MS);
    // Day of month from the civil calendar (H. Hinnant's algorithm).
    let z = days + 719_468;
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day_of_month = doy - (153 * mp + 2) / 5 + 1;
    (days * DAY_MS, (days - (day_of_month - 1)) * DAY_MS)
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
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
            in_flight: Arc::default(),
            limit_gate: Mutex::new(()),
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

    fn limits(&self, tenant: &str) -> Option<&TenantLimits> {
        self.config.api.as_ref().and_then(|a| a.tenants.get(tenant))
    }

    /// Usage of a tenant today and this month (UTC), with its limits.
    pub async fn usage(&self, tenant: &str) -> Result<TenantUsage> {
        let (day, month) = period_starts(now_ms());
        Ok(TenantUsage {
            day: self.store.usage(tenant, day).await?,
            month: self.store.usage(tenant, month).await?,
            limits: self.limits(tenant).cloned().unwrap_or_default(),
        })
    }

    /// Refuse when the tenant has reached a limit that applies to `what`.
    /// The check is made before the work: a call already under way may
    /// take a tenant slightly past a monthly budget, never further.
    pub async fn check_limits(&self, tenant: &str, what: Consume) -> Result<()> {
        self.reserve(tenant, what).await.map(drop)
    }

    /// Check the limits and hold a slot until the work is recorded: requests
    /// arriving together are counted one after the other, not all against
    /// the same total. (Within one process; several Core processes on one
    /// database can still overshoot by the work they admit at once.)
    pub async fn reserve(&self, tenant: &str, what: Consume) -> Result<Reservation> {
        let key = (tenant.to_string(), what);
        let reservation = || {
            *self
                .in_flight
                .lock()
                .unwrap()
                .entry(key.clone())
                .or_default() += 1;
            Reservation {
                in_flight: Arc::clone(&self.in_flight),
                key: key.clone(),
            }
        };
        let Some(l) = self.limits(tenant) else {
            return Ok(reservation());
        };
        let _gate = self.limit_gate.lock().await;
        let pending = self
            .in_flight
            .lock()
            .unwrap()
            .get(&key)
            .copied()
            .unwrap_or(0);
        check_tenant_limits(self.store.as_ref(), l, tenant, what, pending).await?;
        Ok(reservation())
    }

    /// Validate and record a run, then execute it in the background.
    /// Returns the run id at once.
    pub async fn start_run(self: &Arc<Self>, req: StartRun) -> Result<String> {
        let profile = self.prepare(&req)?;
        let reserved = self.reserve(&req.tenant, Consume::Run).await?;
        let id = self.record_new(&req).await?;
        drop(reserved);
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
        let reserved = self.reserve(&req.tenant, Consume::Run).await?;
        let id = self.record_new(&req).await?;
        drop(reserved);
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
        .with_budget(self.limits(&req.tenant).map(|l| {
            Arc::new(TenantBudget {
                store: Arc::clone(&self.store),
                tenant: req.tenant.clone(),
                limits: l.clone(),
            }) as Arc<dyn crate::agent::BudgetGuard>
        }))
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
        self.check_limits(tenant, Consume::ModelCall).await?;
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
        let _reserved = match self.reserve(tenant, Consume::Fetch).await {
            Ok(r) => r,
            Err(e) => {
                return Err(match e.downcast::<LimitReached>() {
                    Ok(l) => FetchError::Limited(l.0),
                    Err(e) => FetchError::Failed(e.to_string()),
                })
            }
        };
        let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
        let wait = {
            let mut slots = svc.next_slot.lock().await;
            let now = Instant::now();
            if slots.len() > 10_000 {
                // Hosts whose next slot has passed need no entry.
                slots.retain(|_, t| *t > now);
            }
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
