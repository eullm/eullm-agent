# Changelog

## 0.2.0

Security hardening release (Sprint F0). Configurations from 0.1.x still load,
but anything risky they enabled is now off until turned on explicitly.

### Breaking changes

- `provider.type: eullm` now uses the EuLLM Engine's OpenAI-compatible
  `{base_url}/v1/chat/completions`, where tool calling works. For a plain
  Ollama server use the new `provider.type: ollama`.
- The `shell` tool is replaced by `run_program` (`tools.exec`): disabled by
  default, runs only programs in `allowed_programs`, never through a shell.
  An old `tools.shell` section enables nothing.
- File tools are confined to `workspace`; `tools.filesystem.allowed_paths` is
  ignored (a warning is printed). `write_file` needs `allow_write: true`.
- `fetch_url` is disabled by default; when enabled it allows HTTPS GET to
  public addresses only.
- Modules are disabled by default and installed only by an operator with
  `eullm-agent module install <name>`. The `install_module` tool is removed.
- `eullm-agent serve` refuses to start when `telegram.allowed_users` is empty.

### Security fixes

- Shell command execution with a bypassable `sudo` filter (S1).
- Path traversal and symlink escapes in `write_file` and `read_file`, and
  unrestricted `list_dir` (S2, S3).
- SSRF in `fetch_url`, including through redirects and DNS rebinding (S4).
- Command injection through module tool arguments, and module installation
  (with `sudo`) triggered by the model (S5).
- Telegram bot open to anyone by default (S6).
- Timed-out commands left running; unbounded tool output (S10).

### Added

- Per-request provider timeouts and one retry on 429/502/503/504 with
  `Retry-After`; error messages carry the provider's status and body.
- Run limits: `limits.max_run_seconds`, `limits.max_tool_output_bytes`.
- JSONL audit log (`audit_log`) of runs, model calls and tool calls, without
  argument values or outputs.
- Secrets from environment variables: `api_key_env`, `token_env`.
- `max_tokens` for Anthropic; token usage parsed for every provider.
- Anthropic responses with unknown block types (e.g. thinking) are accepted.
- Malformed tool-call arguments are reported to the model instead of running
  the tool with defaults.
- `eullm-agent module list|install` subcommands.
- Security, provider and agent regression tests (`tests/`).
