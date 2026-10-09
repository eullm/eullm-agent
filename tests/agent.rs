//! Agent loop limits and audit log.

mod common;

use anyhow::Result;
use async_trait::async_trait;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use eullm_agent::agent::Agent;
use eullm_agent::audit::Audit;
use eullm_agent::config::LimitsConfig;
use eullm_agent::llm::{ChatResponse, LlmClient, Message, Role, ToolCall, ToolDefinition};
use eullm_agent::tools::{Tool, ToolRegistry};

/// Plays back scripted responses and records what the agent sent.
struct ScriptedLlm {
    script: Mutex<Vec<ChatResponse>>,
    seen: Mutex<Vec<Vec<Message>>>,
    delay: Duration,
}

impl ScriptedLlm {
    fn new(mut script: Vec<ChatResponse>) -> Self {
        script.reverse();
        Self {
            script: Mutex::new(script),
            seen: Mutex::new(Vec::new()),
            delay: Duration::ZERO,
        }
    }
}

#[async_trait]
impl LlmClient for ScriptedLlm {
    async fn chat(&self, messages: &[Message], _: &[ToolDefinition]) -> Result<ChatResponse> {
        self.seen.lock().unwrap().push(messages.to_vec());
        tokio::time::sleep(self.delay).await;
        let next = self.script.lock().unwrap().pop();
        Ok(next.unwrap_or(ChatResponse {
            content: "done".into(),
            tool_calls: vec![],
            usage: None,
        }))
    }
    fn provider_name(&self) -> &str {
        "scripted"
    }
    fn model(&self) -> &str {
        "test"
    }
}

fn call(name: &str, arguments: Value) -> ChatResponse {
    ChatResponse {
        content: String::new(),
        tool_calls: vec![ToolCall {
            id: "c1".into(),
            name: name.into(),
            arguments,
        }],
        usage: None,
    }
}

struct EchoTool {
    output: String,
}

#[async_trait]
impl Tool for EchoTool {
    fn definition(&self) -> ToolDefinition {
        ToolDefinition {
            name: "echo".into(),
            description: String::new(),
            parameters: json!({"type": "object"}),
        }
    }
    async fn execute(&self, _: &Value) -> Result<String> {
        Ok(self.output.clone())
    }
}

fn registry(output: &str) -> ToolRegistry {
    let r = ToolRegistry::new();
    r.register(Arc::new(EchoTool {
        output: output.into(),
    }));
    r
}

fn last_tool_result(llm: &ScriptedLlm) -> String {
    let seen = llm.seen.lock().unwrap();
    let msgs = seen.last().unwrap();
    msgs.iter()
        .rev()
        .find(|m| m.role == Role::Tool)
        .unwrap()
        .content
        .clone()
}

#[tokio::test]
async fn malformed_arguments_are_reported_to_the_model() {
    let llm = ScriptedLlm::new(vec![call("echo", json!("{not json"))]);
    let tools = registry("should not run");
    let out = Agent::new(&llm, &tools, 5)
        .run("sys", "task", |_| {})
        .await
        .unwrap();
    assert_eq!(out, "done");
    let result = last_tool_result(&llm);
    assert!(result.contains("not valid JSON"), "{result}");
}

#[tokio::test]
async fn tool_output_is_truncated_before_the_model_sees_it() {
    let llm = ScriptedLlm::new(vec![call("echo", json!({}))]);
    let tools = registry(&"é".repeat(5000));
    let limits = LimitsConfig {
        max_tool_output_bytes: 1000,
        ..Default::default()
    };
    Agent::new(&llm, &tools, 5)
        .with_limits(&limits)
        .run("sys", "task", |_| {})
        .await
        .unwrap();
    let result = last_tool_result(&llm);
    assert!(
        result.contains("[tool output truncated"),
        "{}",
        result.len()
    );
    assert!(result.len() < 1100);
}

#[tokio::test]
async fn iteration_limit_stops_a_looping_model() {
    let llm = ScriptedLlm::new((0..10).map(|_| call("echo", json!({}))).collect());
    let tools = registry("x");
    let err = Agent::new(&llm, &tools, 3)
        .run("sys", "task", |_| {})
        .await
        .unwrap_err();
    assert!(err.to_string().contains("max_iterations"), "{err}");
}

#[tokio::test]
async fn run_time_limit_stops_a_slow_model() {
    let mut llm = ScriptedLlm::new(vec![]);
    llm.delay = Duration::from_secs(10);
    let tools = registry("x");
    let limits = LimitsConfig {
        max_run_seconds: 1,
        ..Default::default()
    };
    let started = Instant::now();
    let err = Agent::new(&llm, &tools, 5)
        .with_limits(&limits)
        .run("sys", "task", |_| {})
        .await
        .unwrap_err();
    assert!(err.to_string().contains("time limit"), "{err}");
    assert!(started.elapsed() < Duration::from_secs(3));
}

#[tokio::test]
async fn audit_log_records_events_without_argument_values() {
    let dir = common::tmp("audit");
    let path = dir.join("audit.jsonl");
    let audit = Arc::new(Audit::open(&path).unwrap());
    let llm = ScriptedLlm::new(vec![call("echo", json!({"secret": "hunter2"}))]);
    let tools = registry("tool output text");
    Agent::new(&llm, &tools, 5)
        .with_audit(Some(audit), "cli")
        .run("sys", "task", |_| {})
        .await
        .unwrap();

    let text = std::fs::read_to_string(&path).unwrap();
    assert!(!text.contains("hunter2"));
    assert!(!text.contains("tool output text"));
    let events: Vec<Value> = text
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    let kinds: Vec<&str> = events
        .iter()
        .map(|e| e["event"].as_str().unwrap())
        .collect();
    assert_eq!(
        kinds,
        ["run_start", "llm_call", "tool_call", "llm_call", "run_end"]
    );
    assert_eq!(events[2]["tool"], "echo");
    assert_eq!(events[2]["argument_keys"], json!(["secret"]));
    assert_eq!(events[4]["ok"], true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&path).unwrap().permissions().mode();
        assert_eq!(mode & 0o077, 0, "audit log readable by others");
    }
}
