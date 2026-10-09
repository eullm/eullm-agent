//! Security regression tests. Each one replays an attack from the 0.1.7
//! review (S1–S10) and checks that it now fails.

mod common;

use common::{http_response, tmp, MockServer};
use serde_json::json;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use eullm_agent::config::{Config, ExecToolConfig, HttpToolConfig};
use eullm_agent::modules::{builtin::all_modules, ModuleRegistry};
use eullm_agent::setup::build_tools;
use eullm_agent::telegram::is_allowed;
use eullm_agent::tools::{
    exec::ExecTool,
    filesystem::{ListDirTool, ReadFileTool, WriteFileTool},
    http::FetchUrlTool,
    module_tool::ModuleTool,
    sandbox::Workspace,
    Tool,
};

const MINIMAL: &str = "provider:\n  type: eullm\n  model: qwen3:8b\n";

fn workspace(name: &str) -> (Workspace, std::path::PathBuf) {
    let base = tmp(name);
    (Workspace::open(&base.join("ws")).unwrap(), base)
}

fn exec_tool(ws: Workspace, programs: &[&str], timeout: u64) -> ExecTool {
    let cfg = ExecToolConfig {
        enabled: true,
        allowed_programs: programs.iter().map(|s| s.to_string()).collect(),
        timeout_seconds: timeout,
        ..Default::default()
    };
    ExecTool::new(&cfg, ws)
}

fn tool_names(config: &Config, name: &str) -> Vec<String> {
    let reg = Arc::new(Mutex::new(
        ModuleRegistry::load(tmp(name).join("state.json")).unwrap(),
    ));
    build_tools(config, reg)
        .unwrap()
        .definitions()
        .into_iter()
        .map(|d| d.name)
        .collect()
}

// --- defaults -------------------------------------------------------------

#[test]
fn default_config_exposes_only_read_tools() {
    let mut config = Config::from_yaml(MINIMAL).unwrap();
    config.workspace = tmp("defaults").join("ws");
    let mut names = tool_names(&config, "defaults-mod");
    names.sort();
    assert_eq!(names, ["list_dir", "read_file"]);
}

#[test]
fn old_shell_config_does_not_enable_execution() {
    let yaml =
        format!("{MINIMAL}tools:\n  shell:\n    allow_sudo: false\n    timeout_seconds: 30\n");
    let mut config = Config::from_yaml(&yaml).unwrap();
    config.workspace = tmp("oldshell").join("ws");
    let names = tool_names(&config, "oldshell-mod");
    assert!(!names.iter().any(|n| n == "run_program" || n == "shell"));
}

#[test]
fn exec_enabled_without_allowlist_registers_nothing() {
    let yaml = format!("{MINIMAL}tools:\n  exec:\n    enabled: true\n");
    let mut config = Config::from_yaml(&yaml).unwrap();
    config.workspace = tmp("noallow").join("ws");
    assert!(!tool_names(&config, "noallow-mod").contains(&"run_program".to_string()));
}

// --- S1: program execution ------------------------------------------------

#[tokio::test]
async fn s1_program_outside_allowlist_is_refused() {
    let (ws, _) = workspace("s1");
    let t = exec_tool(ws, &["echo"], 5);
    for program in ["sh", "bash", "sudo", "/bin/sh", "echo;sh"] {
        let r = t
            .execute(&json!({"program": program, "args": ["-c", "id"]}))
            .await;
        assert!(r.is_err(), "{program} should be refused");
    }
}

#[cfg(unix)]
#[tokio::test]
async fn s1_shell_metacharacters_are_plain_text() {
    let (ws, _) = workspace("s1meta");
    let marker = ws.root().join("pwned");
    let t = exec_tool(ws, &["echo"], 5);
    let arg = format!("a; touch {} $(id) `id` | cat", marker.display());
    let out = t
        .execute(&json!({"program": "echo", "args": [arg]}))
        .await
        .unwrap();
    assert!(out.contains("$(id)"), "{out}");
    assert!(!marker.exists());
}

#[cfg(unix)]
#[tokio::test]
async fn s1_environment_is_not_inherited() {
    std::env::set_var("EULLM_TEST_SECRET", "do-not-leak");
    let (ws, _) = workspace("s1env");
    let t = exec_tool(ws, &["env"], 5);
    let out = t.execute(&json!({"program": "env"})).await.unwrap();
    assert!(!out.contains("do-not-leak"), "{out}");
}

// --- S10: timeouts really stop the process ---------------------------------

#[cfg(unix)]
#[tokio::test]
async fn s10_timeout_kills_the_process_group() {
    let (ws, _) = workspace("s10");
    let marker = ws.root().join("still-ran");
    // The operator explicitly allowed sh here; the point is the kill.
    let t = exec_tool(ws, &["sh"], 1);
    let script = format!("sleep 2; touch {}", marker.display());
    let err = t
        .execute(&json!({"program": "sh", "args": ["-c", script]}))
        .await
        .unwrap_err();
    assert!(err.to_string().contains("timed out"), "{err}");
    tokio::time::sleep(Duration::from_secs(3)).await;
    assert!(!marker.exists(), "child survived the timeout");
}

#[cfg(unix)]
#[tokio::test]
async fn s10_output_is_capped() {
    let (ws, _) = workspace("s10cap");
    let cfg = ExecToolConfig {
        enabled: true,
        allowed_programs: vec!["head".into()],
        timeout_seconds: 5,
        max_output_bytes: 1000,
    };
    let t = ExecTool::new(&cfg, ws);
    let out = t
        .execute(&json!({"program": "head", "args": ["-c", "100000", "/dev/zero"]}))
        .await
        .unwrap();
    assert!(out.len() < 1200, "{} bytes", out.len());
    assert!(out.contains("[output truncated]"));
}

// --- S2/S3: filesystem confinement ----------------------------------------

#[tokio::test]
async fn s2_write_with_dotdot_is_refused() {
    let (ws, base) = workspace("s2");
    let t = WriteFileTool::new(ws, 1024);
    let r = t
        .execute(&json!({"path": "new/../../outside/pwned.txt", "content": "x"}))
        .await;
    assert!(r.is_err());
    assert!(!base.join("outside/pwned.txt").exists());
}

#[tokio::test]
async fn s2_absolute_path_outside_is_refused() {
    let (ws, _) = workspace("s2abs");
    let read = ReadFileTool::new(ws.clone(), 1024);
    assert!(read.execute(&json!({"path": "/etc/passwd"})).await.is_err());
    let write = WriteFileTool::new(ws, 1024);
    let target = tmp("s2abs-out").join("x.txt");
    let r = write
        .execute(&json!({"path": target.to_str().unwrap(), "content": "x"}))
        .await;
    assert!(r.is_err());
    assert!(!target.exists());
}

#[cfg(unix)]
#[tokio::test]
async fn s2_symlink_escapes_are_refused() {
    let (ws, base) = workspace("s2link");
    let secret = base.join("secret.txt");
    std::fs::write(&secret, "original").unwrap();
    std::os::unix::fs::symlink(&secret, ws.root().join("link")).unwrap();
    std::os::unix::fs::symlink(&base, ws.root().join("dirlink")).unwrap();

    let write = WriteFileTool::new(ws.clone(), 1024);
    assert!(write
        .execute(&json!({"path": "link", "content": "overwritten"}))
        .await
        .is_err());
    assert!(write
        .execute(&json!({"path": "dirlink/new.txt", "content": "x"}))
        .await
        .is_err());
    assert_eq!(std::fs::read_to_string(&secret).unwrap(), "original");
    assert!(!base.join("new.txt").exists());

    let read = ReadFileTool::new(ws, 1024);
    assert!(read.execute(&json!({"path": "link"})).await.is_err());
}

#[tokio::test]
async fn s2_reads_and_writes_inside_the_workspace_work() {
    let (ws, _) = workspace("s2ok");
    let write = WriteFileTool::new(ws.clone(), 1024);
    write
        .execute(&json!({"path": "sub/note.txt", "content": "hello"}))
        .await
        .unwrap();
    let read = ReadFileTool::new(ws.clone(), 1024);
    assert_eq!(
        read.execute(&json!({"path": "sub/note.txt"}))
            .await
            .unwrap(),
        "hello"
    );
    let list = ListDirTool::new(ws);
    assert!(list.execute(&json!({})).await.unwrap().contains("sub/"));
}

#[tokio::test]
async fn s2_large_files_are_truncated() {
    let (ws, _) = workspace("s2big");
    std::fs::write(ws.root().join("big.txt"), "a".repeat(5000)).unwrap();
    let read = ReadFileTool::new(ws.clone(), 100);
    let out = read.execute(&json!({"path": "big.txt"})).await.unwrap();
    assert!(out.contains("[truncated at 100 bytes]"));
    let write = WriteFileTool::new(ws, 100);
    assert!(write
        .execute(&json!({"path": "w.txt", "content": "a".repeat(101)}))
        .await
        .is_err());
}

#[tokio::test]
async fn s3_list_dir_is_confined() {
    let (ws, _) = workspace("s3");
    let t = ListDirTool::new(ws);
    assert!(t.execute(&json!({"path": "/etc"})).await.is_err());
    assert!(t.execute(&json!({"path": ".."})).await.is_err());
}

// --- S4: SSRF -------------------------------------------------------------

async fn internal_server() -> MockServer {
    MockServer::start(|req, _| {
        if req.path.starts_with("/redirect") {
            "HTTP/1.1 302 Found\r\nLocation: http://localhost/admin\r\nContent-Length: 0\r\n\r\n"
                .to_string()
        } else if req.path.starts_with("/big") {
            http_response("200 OK", "", &"x".repeat(10_000))
        } else {
            http_response("200 OK", "", "INTERNAL-ONLY")
        }
    })
    .await
}

fn http_cfg() -> HttpToolConfig {
    HttpToolConfig {
        enabled: true,
        allow_http: true,
        timeout_seconds: 5,
        ..Default::default()
    }
}

#[tokio::test]
async fn s4_loopback_and_private_addresses_are_blocked() {
    let srv = internal_server().await;
    let t = FetchUrlTool::new(&http_cfg());
    for url in [
        format!("{}/admin", srv.url()),
        format!("http://localhost:{}/admin", srv.port),
        format!("http://[::1]:{}/admin", srv.port),
        "http://169.254.169.254/latest/meta-data/".to_string(),
        "http://10.0.0.1/".to_string(),
        "http://0.0.0.0/".to_string(),
    ] {
        let r = t.execute(&json!({"url": url})).await;
        assert!(r.is_err(), "{url} should be blocked: {r:?}");
    }
    assert!(srv.recorded().is_empty(), "a request reached the server");
}

#[tokio::test]
async fn s4_plain_http_and_other_schemes_are_refused_by_default() {
    let t = FetchUrlTool::new(&HttpToolConfig {
        enabled: true,
        ..Default::default()
    });
    for url in [
        "http://example.com/",
        "file:///etc/passwd",
        "ftp://example.com/",
        "https://user:pass@example.com/",
    ] {
        assert!(t.execute(&json!({"url": url})).await.is_err(), "{url}");
    }
}

#[tokio::test]
async fn s4_redirects_are_checked_again() {
    let srv = internal_server().await;
    // Private networks allowed but only 127.0.0.1 listed: the redirect to
    // `localhost` must be checked against the allowlist and refused.
    let t = FetchUrlTool::new(&HttpToolConfig {
        allow_private_networks: true,
        allowed_domains: vec!["127.0.0.1".into()],
        ..http_cfg()
    });
    let r = t
        .execute(&json!({"url": format!("{}/redirect", srv.url())}))
        .await;
    let err = r.unwrap_err().to_string();
    assert!(err.contains("allowed_domains"), "{err}");
    assert_eq!(srv.recorded().len(), 1);
}

#[tokio::test]
async fn s4_methods_other_than_get_are_refused_by_default() {
    let t = FetchUrlTool::new(&http_cfg());
    let r = t
        .execute(&json!({"url": "https://example.com/", "method": "POST"}))
        .await;
    assert!(r.unwrap_err().to_string().contains("not allowed"));
}

#[tokio::test]
async fn s4_response_body_is_capped() {
    let srv = internal_server().await;
    let t = FetchUrlTool::new(&HttpToolConfig {
        allow_private_networks: true,
        max_response_bytes: 100,
        ..http_cfg()
    });
    let out = t
        .execute(&json!({"url": format!("{}/big", srv.url())}))
        .await
        .unwrap();
    assert!(out.contains("download stopped at the size limit"), "{out}");
    assert!(out.len() < 300);
}

// --- S5: modules ----------------------------------------------------------

fn pdf_tool(ws: Workspace) -> ModuleTool {
    let spec = all_modules()
        .into_iter()
        .find(|m| m.name == "pdf")
        .unwrap()
        .tools[0]
        .clone();
    ModuleTool::new(spec, ws, Duration::from_secs(5)).unwrap()
}

#[test]
fn s5_module_arguments_stay_single_argv_elements() {
    let (ws, _) = workspace("s5");
    let t = pdf_tool(ws.clone());
    let argv = t
        .build_argv(&json!({"pdf_path": "x.pdf; echo INJECTED #"}))
        .unwrap();
    assert_eq!(argv.len(), 3, "{argv:?}");
    assert_eq!(argv[0], "pdftotext");
    assert!(argv[1].ends_with("x.pdf; echo INJECTED #"));
    assert!(argv[1].starts_with(ws.root().to_str().unwrap()));
}

#[test]
fn s5_module_paths_and_options_are_checked() {
    let (ws, _) = workspace("s5b");
    let t = pdf_tool(ws);
    for bad in ["/etc/passwd", "../x.pdf", "-opw", ""] {
        assert!(
            t.build_argv(&json!({ "pdf_path": bad })).is_err(),
            "{bad:?} accepted"
        );
    }
}

#[test]
fn s5_agent_cannot_install_modules() {
    let yaml = format!("{MINIMAL}modules:\n  enabled: true\n");
    let mut config = Config::from_yaml(&yaml).unwrap();
    config.workspace = tmp("s5c").join("ws");
    let names = tool_names(&config, "s5c-mod");
    assert!(names.contains(&"list_modules".to_string()));
    assert!(!names.iter().any(|n| n.contains("install")), "{names:?}");
}

#[test]
fn s5_modules_are_off_by_default() {
    let mut config = Config::from_yaml(MINIMAL).unwrap();
    config.workspace = tmp("s5d").join("ws");
    assert!(!tool_names(&config, "s5d-mod").contains(&"list_modules".to_string()));
}

// --- S6: Telegram ---------------------------------------------------------

#[test]
fn s6_telegram_requires_an_allowlist() {
    let yaml = format!("{MINIMAL}telegram:\n  token: abc\n  allowed_users: []\n");
    let config = Config::from_yaml(&yaml).unwrap();
    assert!(config.validate_telegram().is_err());

    let yaml = format!("{MINIMAL}telegram:\n  token: abc\n  allowed_users: [42]\n");
    let config = Config::from_yaml(&yaml).unwrap();
    assert!(config.validate_telegram().is_ok());
}

#[test]
fn s6_empty_allowlist_allows_nobody() {
    assert!(!is_allowed(&[], 42));
    assert!(!is_allowed(&[], 0));
    assert!(!is_allowed(&[1, 2], 42));
    assert!(is_allowed(&[1, 42], 42));
}
