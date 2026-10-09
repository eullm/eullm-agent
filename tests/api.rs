//! Core HTTP API: authentication, tenants, runs, approvals, model calls,
//! and conformance with docs/openapi.json.

mod common;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use common::{tool_call, CountingTool, Scripted};
use http_body_util::BodyExt;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;
use tower::ServiceExt;

use eullm_agent::api::{self, ApiToken, AppState};
use eullm_agent::config::{ApiTokenConfig, Config};
use eullm_agent::policy::Policy;
use eullm_agent::router::ModelRouter;
use eullm_agent::service::Core;
use eullm_agent::store::{MemoryStore, Store};
use eullm_agent::tools::ToolRegistry;

const TOKEN_A: &str = "token-a-0123456789abcdef0123456789abcdef";
const TOKEN_B: &str = "token-b-0123456789abcdef0123456789abcdef";
const TOKEN_LIMITED: &str = "token-l-0123456789abcdef0123456789abcdef";

const CONFIG: &str = r#"
provider:
  type: eullm
  model: test
profiles:
  writer:
    tools: [danger]
"#;

const POLICY: &str = r#"
rules:
  - tool: danger
    effect: require_approval
    reason: dangerous on purpose
  - tool: forbidden
    effect: deny
"#;

struct Harness {
    app: axum::Router,
    store: Arc<MemoryStore>,
    llm: Arc<Scripted>,
    danger_runs: Arc<std::sync::Mutex<u32>>,
}

fn token(name: &str, tenant: &str, secret: &str, profiles: &[&str]) -> ApiToken {
    ApiToken::resolve(&ApiTokenConfig {
        name: name.into(),
        tenant: tenant.into(),
        token_sha256: Some(api::sha256_hex(secret)),
        token_env: None,
        profiles: profiles.iter().map(|s| s.to_string()).collect(),
    })
    .unwrap()
}

fn harness(script: Vec<eullm_agent::llm::ChatResponse>) -> Harness {
    let config = Arc::new(Config::from_yaml(CONFIG).unwrap());
    let llm = Arc::new(Scripted::new(script));
    let router = ModelRouter::single("default", llm.clone());
    let tools = ToolRegistry::new();
    let (danger, danger_runs) = CountingTool::new("danger", "boom");
    tools.register(danger);
    tools.register(CountingTool::new("safe", "fine").0);
    tools.register(CountingTool::new("forbidden", "never").0);
    let store = Arc::new(MemoryStore::new());
    let core = Core::new(
        config,
        router,
        tools,
        Arc::new(Policy::from_yaml(POLICY).unwrap()),
        store.clone() as Arc<dyn Store>,
        Duration::from_secs(5),
        2,
        None,
    );
    let app = api::router(AppState {
        core,
        tokens: Arc::new(vec![
            token("a", "tenant-a", TOKEN_A, &[]),
            token("b", "tenant-b", TOKEN_B, &[]),
            token("limited", "tenant-a", TOKEN_LIMITED, &["writer"]),
        ]),
    });
    Harness {
        app,
        store,
        llm,
        danger_runs,
    }
}

async fn call(
    app: &axum::Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut req = Request::builder().method(method).uri(path);
    if let Some(t) = token {
        req = req.header("authorization", format!("Bearer {t}"));
    }
    let req = match body {
        Some(b) => req
            .header("content-type", "application/json")
            .body(Body::from(b.to_string()))
            .unwrap(),
        None => req.body(Body::empty()).unwrap(),
    };
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, value)
}

async fn wait_for(app: &axum::Router, id: &str, token: &str, status: &str) -> Value {
    for _ in 0..200 {
        let (_, run) = call(app, "GET", &format!("/v1/runs/{id}"), Some(token), None).await;
        if run["status"] == status {
            return run;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("run {id} never reached {status}");
}

async fn pending_approval(app: &axum::Router, token: &str) -> Value {
    for _ in 0..200 {
        let (_, list) = call(
            app,
            "GET",
            "/v1/approvals?status=pending",
            Some(token),
            None,
        )
        .await;
        if let Some(a) = list["approvals"].as_array().and_then(|a| a.first()) {
            return a.clone();
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("no pending approval");
}

// --- authentication and tenants ----------------------------------------------

#[tokio::test]
async fn health_is_public_everything_else_needs_a_token() {
    let h = harness(vec![]);
    let (s, body) = call(&h.app, "GET", "/v1/health", None, None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(body["status"], "ok");
    for (m, p) in [
        ("GET", "/v1/runs"),
        ("GET", "/v1/approvals"),
        ("GET", "/v1/runs/x"),
    ] {
        let (s, _) = call(&h.app, m, p, None, None).await;
        assert_eq!(s, StatusCode::UNAUTHORIZED, "{m} {p}");
        let (s, _) = call(&h.app, m, p, Some("wrong-token"), None).await;
        assert_eq!(s, StatusCode::UNAUTHORIZED, "{m} {p}");
    }
    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/runs",
        None,
        Some(json!({"input": "x"})),
    )
    .await;
    assert_eq!(s, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn runs_are_isolated_by_tenant() {
    let h = harness(vec![]);
    let (s, acc) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "hello"})),
    )
    .await;
    assert_eq!(s, StatusCode::ACCEPTED);
    let id = acc["id"].as_str().unwrap().to_string();
    wait_for(&h.app, &id, TOKEN_A, "succeeded").await;

    let (s, _) = call(
        &h.app,
        "GET",
        &format!("/v1/runs/{id}"),
        Some(TOKEN_B),
        None,
    )
    .await;
    assert_eq!(s, StatusCode::NOT_FOUND);
    let (_, list) = call(&h.app, "GET", "/v1/runs", Some(TOKEN_B), None).await;
    assert_eq!(list["runs"], json!([]));
    let (_, list) = call(&h.app, "GET", "/v1/runs", Some(TOKEN_A), None).await;
    assert_eq!(list["runs"][0]["id"], id);
}

#[tokio::test]
async fn tokens_limited_to_profiles() {
    let h = harness(vec![]);
    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_LIMITED),
        Some(json!({"input": "x"})),
    )
    .await;
    assert_eq!(s, StatusCode::FORBIDDEN);
    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_LIMITED),
        Some(json!({"input": "x", "profile": "writer"})),
    )
    .await;
    assert_eq!(s, StatusCode::ACCEPTED);
    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "x", "profile": "nope"})),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "  "})),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

// --- runs, policy and approvals ------------------------------------------------

#[tokio::test]
async fn run_records_model_and_tool_steps_with_usage() {
    let h = harness(vec![tool_call("safe", json!({"q": "secret-value"}))]);
    let (_, acc) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "go"})),
    )
    .await;
    let run = wait_for(&h.app, acc["id"].as_str().unwrap(), TOKEN_A, "succeeded").await;
    assert_eq!(run["output"], "done");
    assert_eq!(run["input_tokens"], 110);
    assert_eq!(run["output_tokens"], 22);
    let kinds: Vec<&str> = run["steps"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["kind"].as_str().unwrap())
        .collect();
    assert_eq!(kinds, ["llm_call", "tool_call", "llm_call"]);
    let tool = &run["steps"][1];
    assert_eq!(tool["tool"], "safe");
    assert_eq!(tool["decision"], "allow");
    assert_eq!(tool["argument_keys"], json!(["q"]));
    assert!(!run.to_string().contains("secret-value"));
}

#[tokio::test]
async fn denied_tool_never_runs() {
    let h = harness(vec![tool_call("forbidden", json!({}))]);
    let (_, acc) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "go"})),
    )
    .await;
    let run = wait_for(&h.app, acc["id"].as_str().unwrap(), TOKEN_A, "succeeded").await;
    assert_eq!(run["steps"][1]["decision"], "deny");
    assert!(h
        .llm
        .last_tool_result()
        .unwrap()
        .contains("denied by policy"));
}

#[tokio::test]
async fn sensitive_action_waits_for_approval_then_runs() {
    let h = harness(vec![tool_call("danger", json!({"target": "prod"}))]);
    let (_, acc) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "go"})),
    )
    .await;
    let id = acc["id"].as_str().unwrap().to_string();

    let approval = pending_approval(&h.app, TOKEN_A).await;
    assert_eq!(approval["tool"], "danger");
    assert_eq!(approval["arguments"]["target"], "prod");
    assert_eq!(approval["run_id"], id);
    let (_, run) = call(
        &h.app,
        "GET",
        &format!("/v1/runs/{id}"),
        Some(TOKEN_A),
        None,
    )
    .await;
    assert_eq!(run["status"], "waiting_approval");
    assert_eq!(*h.danger_runs.lock().unwrap(), 0, "ran before approval");

    // Another tenant can neither see nor decide it.
    let (_, other) = call(&h.app, "GET", "/v1/approvals", Some(TOKEN_B), None).await;
    assert_eq!(other["approvals"], json!([]));
    let path = format!("/v1/approvals/{}", approval["id"].as_str().unwrap());
    let (s, _) = call(
        &h.app,
        "POST",
        &path,
        Some(TOKEN_B),
        Some(json!({"decision": "approve"})),
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);

    let (s, decided) = call(
        &h.app,
        "POST",
        &path,
        Some(TOKEN_A),
        Some(json!({"decision": "approve", "note": "ok"})),
    )
    .await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(decided["status"], "approved");
    assert_eq!(decided["decided_by"], "api:a");

    let run = wait_for(&h.app, &id, TOKEN_A, "succeeded").await;
    assert_eq!(*h.danger_runs.lock().unwrap(), 1);
    assert_eq!(run["steps"][1]["decision"], "require_approval");
    assert_eq!(run["steps"][1]["approval_id"], approval["id"]);

    // A decision is final.
    let (s, _) = call(
        &h.app,
        "POST",
        &path,
        Some(TOKEN_A),
        Some(json!({"decision": "deny"})),
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT);
}

#[tokio::test]
async fn refused_action_is_reported_to_the_model() {
    let h = harness(vec![tool_call("danger", json!({}))]);
    let (_, acc) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "go"})),
    )
    .await;
    let approval = pending_approval(&h.app, TOKEN_A).await;
    let path = format!("/v1/approvals/{}", approval["id"].as_str().unwrap());
    call(
        &h.app,
        "POST",
        &path,
        Some(TOKEN_A),
        Some(json!({"decision": "deny", "note": "not today"})),
    )
    .await;
    wait_for(&h.app, acc["id"].as_str().unwrap(), TOKEN_A, "succeeded").await;
    assert_eq!(*h.danger_runs.lock().unwrap(), 0);
    let result = h.llm.last_tool_result().unwrap();
    assert!(
        result.contains("refused") && result.contains("not today"),
        "{result}"
    );
}

// --- single model calls ----------------------------------------------------------

#[tokio::test]
async fn llm_chat_is_recorded_with_usage() {
    let h = harness(vec![]);
    let (s, body) = call(
        &h.app,
        "POST",
        "/v1/llm/chat",
        Some(TOKEN_A),
        Some(json!({
            "messages": [{"role": "user", "content": "hi"}]
        })),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "{body}");
    assert_eq!(body["content"], "done");
    assert_eq!(body["usage"]["input_tokens"], 10);
    let calls = h.store.loose_llm_calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "tenant-a");

    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/llm/chat",
        Some(TOKEN_A),
        Some(json!({"model": "nope", "messages": [{"role": "user", "content": "hi"}]})),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, _) = call(
        &h.app,
        "POST",
        "/v1/llm/chat",
        Some(TOKEN_A),
        Some(json!({"messages": [{"role": "tool", "content": "hi"}]})),
    )
    .await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

// --- contract ---------------------------------------------------------------------

fn spec() -> Value {
    serde_json::from_str(api::OPENAPI).unwrap()
}

fn resolve<'a>(spec: &'a Value, schema: &'a Value) -> &'a Value {
    match schema["$ref"].as_str() {
        Some(r) => {
            let name = r.trim_start_matches("#/components/schemas/");
            &spec["components"]["schemas"][name]
        }
        None => schema,
    }
}

/// Check required properties and basic types, following $ref and allOf.
fn conforms(spec: &Value, schema: &Value, value: &Value, path: &str) {
    let schema = resolve(spec, schema);
    if let Some(all) = schema["allOf"].as_array() {
        for s in all {
            conforms(spec, s, value, path);
        }
        return;
    }
    if let Some(one) = schema["oneOf"].as_array() {
        let ok = one.iter().any(|s| {
            let s = resolve(spec, s);
            s["type"] == "null" && value.is_null() || s["type"] == "object" && value.is_object()
        });
        assert!(ok, "{path}: {value} matches no oneOf branch");
        return;
    }
    let types: Vec<&str> = match &schema["type"] {
        Value::String(t) => vec![t.as_str()],
        Value::Array(ts) => ts.iter().filter_map(Value::as_str).collect(),
        _ => vec![],
    };
    if !types.is_empty() {
        let actual = match value {
            Value::Null => "null",
            Value::Bool(_) => "boolean",
            Value::Number(n) if n.is_i64() || n.is_u64() => "integer",
            Value::Number(_) => "number",
            Value::String(_) => "string",
            Value::Array(_) => "array",
            Value::Object(_) => "object",
        };
        let ok = types.contains(&actual) || (actual == "integer" && types.contains(&"number"));
        assert!(ok, "{path}: expected {types:?}, got {actual} ({value})");
    }
    if let Some(e) = schema["enum"].as_array() {
        assert!(e.contains(value), "{path}: {value} not in {e:?}");
    }
    if let Some(req) = schema["required"].as_array() {
        for k in req {
            let k = k.as_str().unwrap();
            assert!(
                value.get(k).is_some(),
                "{path}: missing required '{k}' in {value}"
            );
        }
    }
    if let (Some(props), Some(obj)) = (schema["properties"].as_object(), value.as_object()) {
        for (k, v) in obj {
            if let Some(ps) = props.get(k) {
                conforms(spec, ps, v, &format!("{path}.{k}"));
            }
        }
    }
    if let (Some(items), Some(arr)) = (schema.get("items"), value.as_array()) {
        for (i, v) in arr.iter().enumerate() {
            conforms(spec, items, v, &format!("{path}[{i}]"));
        }
    }
}

fn response_schema<'a>(spec: &'a Value, method: &str, path: &str, status: StatusCode) -> &'a Value {
    &spec["paths"][path][method]["responses"][status.as_str()]["content"]["application/json"]
        ["schema"]
}

#[test]
fn every_route_is_documented_and_every_documented_route_exists() {
    let spec = spec();
    let mut documented: Vec<(String, String)> = Vec::new();
    for (path, ops) in spec["paths"].as_object().unwrap() {
        for method in ops.as_object().unwrap().keys() {
            documented.push((method.clone(), path.clone()));
        }
    }
    documented.sort();
    let mut routes: Vec<(String, String)> = api::ROUTES
        .iter()
        .map(|(m, p)| (m.to_string(), p.to_string()))
        .collect();
    routes.sort();
    assert_eq!(routes, documented);
}

#[tokio::test]
async fn responses_match_the_openapi_contract() {
    let spec = spec();
    let h = harness(vec![tool_call("danger", json!({"x": 1}))]);

    let (s, v) = call(&h.app, "GET", "/v1/health", None, None).await;
    conforms(
        &spec,
        response_schema(&spec, "get", "/v1/health", s),
        &v,
        "health",
    );

    let (s, v) = call(&h.app, "GET", "/v1/runs", None, None).await;
    conforms(
        &spec,
        response_schema(&spec, "get", "/v1/runs", s),
        &v,
        "401",
    );

    let (s, acc) = call(
        &h.app,
        "POST",
        "/v1/runs",
        Some(TOKEN_A),
        Some(json!({"input": "go"})),
    )
    .await;
    conforms(
        &spec,
        response_schema(&spec, "post", "/v1/runs", s),
        &acc,
        "accepted",
    );
    let id = acc["id"].as_str().unwrap().to_string();

    let approval = pending_approval(&h.app, TOKEN_A).await;
    let (s, v) = call(&h.app, "GET", "/v1/approvals", Some(TOKEN_A), None).await;
    conforms(
        &spec,
        response_schema(&spec, "get", "/v1/approvals", s),
        &v,
        "approvals",
    );
    let path = format!("/v1/approvals/{}", approval["id"].as_str().unwrap());
    let (s, v) = call(
        &h.app,
        "POST",
        &path,
        Some(TOKEN_A),
        Some(json!({"decision": "approve"})),
    )
    .await;
    conforms(
        &spec,
        response_schema(&spec, "post", "/v1/approvals/{id}", s),
        &v,
        "decided",
    );
    let (s, v) = call(
        &h.app,
        "POST",
        &path,
        Some(TOKEN_A),
        Some(json!({"decision": "approve"})),
    )
    .await;
    conforms(
        &spec,
        response_schema(&spec, "post", "/v1/approvals/{id}", s),
        &v,
        "conflict",
    );

    let run = wait_for(&h.app, &id, TOKEN_A, "succeeded").await;
    conforms(
        &spec,
        response_schema(&spec, "get", "/v1/runs/{id}", StatusCode::OK),
        &run,
        "run",
    );
    let (s, v) = call(&h.app, "GET", "/v1/runs", Some(TOKEN_A), None).await;
    conforms(
        &spec,
        response_schema(&spec, "get", "/v1/runs", s),
        &v,
        "runs",
    );
    let (s, v) = call(
        &h.app,
        "GET",
        "/v1/runs/00000000-0000-0000-0000-000000000000",
        Some(TOKEN_A),
        None,
    )
    .await;
    conforms(
        &spec,
        response_schema(&spec, "get", "/v1/runs/{id}", s),
        &v,
        "404",
    );

    let (s, v) = call(
        &h.app,
        "POST",
        "/v1/llm/chat",
        Some(TOKEN_A),
        Some(json!({"messages": [{"role": "user", "content": "x"}]})),
    )
    .await;
    conforms(
        &spec,
        response_schema(&spec, "post", "/v1/llm/chat", s),
        &v,
        "chat",
    );
}
