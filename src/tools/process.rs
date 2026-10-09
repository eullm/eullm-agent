//! Running external programs without a shell, with a timeout that really
//! stops them and output that cannot grow without bound.

use anyhow::{Context, Result};
use std::path::Path;
use std::process::{ExitStatus, Stdio};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::Command;

pub struct ProgramOutput {
    pub status: ExitStatus,
    pub stdout: String,
    pub stderr: String,
    pub truncated: bool,
}

/// Run `program` with `args` (never through a shell) in `cwd`, with a clean
/// environment. On timeout the whole process group is killed.
pub async fn run_program(
    program: &str,
    args: &[String],
    cwd: &Path,
    timeout: Duration,
    max_output_bytes: usize,
) -> Result<ProgramOutput> {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .current_dir(cwd)
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    for var in ["PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP"] {
        if let Ok(v) = std::env::var(var) {
            cmd.env(var, v);
        }
    }
    cmd.env("HOME", cwd);
    #[cfg(unix)]
    cmd.process_group(0);

    let mut child = cmd
        .spawn()
        .with_context(|| format!("cannot start {program}"))?;
    let pid = child.id();
    let stdout = child.stdout.take().context("no stdout")?;
    let stderr = child.stderr.take().context("no stderr")?;
    let out_task = tokio::spawn(read_capped(stdout, max_output_bytes));
    let err_task = tokio::spawn(read_capped(stderr, max_output_bytes));

    let deadline = tokio::time::Instant::now() + timeout;
    let timed_out = |pid| {
        kill_group(pid);
        anyhow::anyhow!(
            "Command timed out after {}s and was stopped",
            timeout.as_secs()
        )
    };
    let status = match tokio::time::timeout_at(deadline, child.wait()).await {
        Ok(status) => status?,
        Err(_) => {
            let err = timed_out(pid);
            let _ = child.kill().await;
            out_task.abort();
            err_task.abort();
            return Err(err);
        }
    };
    // A background process started by the program can keep the pipes open
    // after it exits; the same deadline applies to reading them.
    let outputs = tokio::time::timeout_at(deadline, async {
        let (stdout, t1) = out_task.await??;
        let (stderr, t2) = err_task.await??;
        anyhow::Ok((stdout, t1, stderr, t2))
    })
    .await;
    let (stdout, t1, stderr, t2) = match outputs {
        Ok(r) => r?,
        Err(_) => return Err(timed_out(pid)),
    };
    Ok(ProgramOutput {
        status,
        stdout,
        stderr,
        truncated: t1 || t2,
    })
}

/// Read everything, keep at most `max` bytes (lossy UTF-8).
async fn read_capped<R: AsyncRead + Unpin>(mut r: R, max: usize) -> Result<(String, bool)> {
    let mut kept = Vec::new();
    let mut buf = [0u8; 8192];
    let mut truncated = false;
    loop {
        let n = r.read(&mut buf).await?;
        if n == 0 {
            break;
        }
        let room = max.saturating_sub(kept.len());
        if room < n {
            truncated = true;
        }
        kept.extend_from_slice(&buf[..n.min(room)]);
    }
    Ok((String::from_utf8_lossy(&kept).into_owned(), truncated))
}

#[cfg(unix)]
fn kill_group(pid: Option<u32>) {
    if let Some(pid) = pid {
        // The child leads its own process group (process_group(0)).
        unsafe {
            libc::killpg(pid as libc::pid_t, libc::SIGKILL);
        }
    }
}

#[cfg(not(unix))]
fn kill_group(_pid: Option<u32>) {}

/// Format the result the way tools return it to the model.
pub fn format_output(out: &ProgramOutput) -> String {
    let mut result = String::new();
    if !out.stdout.is_empty() {
        result.push_str(&out.stdout);
    }
    if !out.stderr.is_empty() {
        if !result.is_empty() {
            result.push('\n');
        }
        result.push_str("[stderr] ");
        result.push_str(&out.stderr);
    }
    if out.truncated {
        result.push_str("\n[output truncated]");
    }
    if !out.status.success() {
        let code = out.status.code().unwrap_or(-1);
        result.push_str(&format!("\n[exit {code}]"));
    }
    if result.is_empty() {
        result = "(no output)".into();
    }
    result
}
