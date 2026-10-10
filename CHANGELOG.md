# Changelog

## 0.3.5

### Added

- Editor Mode (`editor-mode/`, Python): the first vertical built on the
  Core, now part of this repository under AGPL-3.0-or-later. Domain-first
  editorial work: site analysis and an editorial profile with evidence,
  source discovery and rating, Hype and Opportunity scores, an editorial
  plan, drafts where every sentence cites a stored source, publication to
  WordPress, webhooks and Telegram after an owner's approval, and a
  seven-page dashboard. Plan limits per tenant on top of the Core's.

### Changed

- README: Editor Mode shown with its dashboard.
- Pull request template: no CLA section; the CLA check tells external
  contributors what to add.

## 0.3.0

The Core as a service: other programs can start runs, make model calls,
decide approvals and fetch web pages over HTTP, with state and audit in
PostgreSQL and limits per tenant. Applications such as Editor Mode are built
on this API instead of calling providers or the web directly.

### Added

- `eullm-agent api`: HTTP API (`/v1/runs`, `/v1/llm/chat`, `/v1/approvals`,
  `/v1/health`) with bearer tokens per tenant, documented in
  `docs/openapi.json` and checked by contract tests.
- `eullm-agent token new` to create API tokens (only the SHA-256 is stored).
- PostgreSQL store (`database`): schema `core` with runs, model calls, tool
  calls, approvals and an append-only `audit_events` table; migrations run
  at start. Without a database the state is kept in memory.
- Model Router (`models`): named models with fallback chains and pricing.
- Profiles (`profiles`): model, allowed tools, iteration and time limits,
  token and cost budgets per run.
- Policy engine (`policy_file`): allow / deny / require_approval per tool and
  profile; runs that read external content are tainted and their side
  effects need approval.
- Approval queue: runs wait for a decision from the API, from Telegram
  (`/approve`, `/deny`) or from the terminal; no answer in time is a refusal.
- Telegram tasks run in the background so approvals can be answered, and use
  `telegram.profile`.
- `POST /v1/fetch` (`api.fetch`): HTTP GET for applications built on the Core
  (crawling, feeds) with the same address checks as `fetch_url`, every
  redirect re-checked, a size limit and a per-host pace shared by all
  callers; each request is recorded in `core.fetches` and the audit.
- Limits per tenant (`api.tenants`): monthly cost and tokens, runs and
  fetches per day, in UTC calendar periods; a request over a limit gets 429
  with the reason. `GET /v1/usage` reports the tenant's usage and limits.

### Changed

- The audit log records the policy decision of every tool call and the
  token totals, cost and taint of every run.

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
