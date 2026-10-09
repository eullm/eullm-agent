# EuLLM Agent

Autonomous ReAct agent with multi-provider LLM support (EuLLM Engine, Ollama,
OpenAI-compatible APIs, Anthropic), a small set of sandboxed tools, optional
modules and a Telegram interface.

## Quick start

```bash
cargo build --release
cp config.example.yaml config.yaml     # or run without a config for a wizard
./target/release/eullm-agent run "List the files in the workspace"
./target/release/eullm-agent serve     # Telegram bot (needs telegram.allowed_users)
```

## Providers

| `provider.type` | Endpoint | Tool calling |
|---|---|---|
| `eullm` | `{base_url}/v1/chat/completions` (EuLLM Engine, OpenAI-compatible) | yes |
| `ollama` | `{base_url}/api/chat` (Ollama native) | yes |
| `openai` | `{base_url}/chat/completions` (default `https://api.openai.com/v1`) | yes |
| `anthropic` | `https://api.anthropic.com/v1/messages` | yes |

Secrets can be given as `api_key` / `token` or, preferably, as `api_key_env` /
`token_env` naming an environment variable. Every provider request has a
timeout (`limits.llm_timeout_seconds`) and is retried once on 429, 502, 503 or
504, honouring `Retry-After`.

**Upgrading from 0.1.x:** `type: eullm` now talks to the Engine's `/v1` API,
because the Engine's `/api/chat` ignores tools. If you were pointing
`type: eullm` at a plain Ollama server, change it to `type: ollama`.

## Security model

EuLLM Agent runs actions chosen by a language model, and a model can be steered
by any text it reads (a web page, a file, a message). So every capability that
touches the machine or the network is **off by default** and narrowly scoped
when turned on. See [config.example.yaml](config.example.yaml) for every
setting.

| Capability | Default | When enabled |
|---|---|---|
| `run_program` (`tools.exec`) | off | Only programs in `allowed_programs`, argv only (never a shell), run in the workspace with a clean environment, killed with their process group on timeout, output capped |
| `read_file`, `list_dir` | on | Confined to `workspace`: `..`, absolute paths outside it and symlinks leading out are refused |
| `write_file` | off (`allow_write`) | Same confinement; never writes through a symlink; size capped |
| `fetch_url` (`tools.http`) | off | HTTPS GET only; every resolved address must be public (no loopback, LAN, link-local or cloud metadata); each redirect is checked again; the connection is pinned to the checked address; optional domain allowlist; body capped |
| Modules | off | Tools of modules an operator installed with `eullm-agent module install`; arguments are passed as single argv elements, file arguments are confined to the workspace. The agent cannot install modules |
| Telegram | needs config | Refuses to start without `allowed_users`; messages without a sender are ignored |

Each task is also bounded by `max_iterations`, `limits.max_run_seconds` and
`limits.max_tool_output_bytes`. The audit log (`audit_log`, JSONL, mode 0600)
records each run, model call (duration, tokens, errors) and tool call (name,
argument key names, outcome, duration); it never stores argument values,
prompts or outputs.

What this does not protect against: programs you allowlist run with the
agent's own user rights, so allowlisting an interpreter (`sh`, `python`, ...)
gives the model full control of that user. Run the agent as an unprivileged
user, ideally in a container.

## Using it with EuLLM Engine

1. Start the Engine and note its address (default `http://localhost:11434`)
   and, if it runs with `EULLM_API_KEYS`, one of the keys.
2. Check that the OpenAI-compatible API answers and that the model is loaded:
   ```bash
   curl -s http://localhost:11434/v1/models -H "Authorization: Bearer $EULLM_API_KEY"
   ```
3. Configure the agent:
   ```yaml
   provider:
     type: eullm
     base_url: http://localhost:11434
     model: qwen3:8b            # a model with tool-calling support
     api_key_env: EULLM_API_KEY # omit if the Engine has no keys
   ```
4. Verify tool calling end to end; the agent must call `list_dir`:
   ```bash
   mkdir -p workspace && echo hello > workspace/note.txt
   RUST_LOG=eullm_agent=debug ./target/release/eullm-agent run \
     "Which files are in the workspace? Read note.txt and tell me its content."
   ```
   The progress lines show `tool:list_dir` and `tool:read_file`, the debug log
   shows `POST http://localhost:11434/v1/chat/completions`, and
   `eullm-agent-audit.jsonl` gets a `tool_call` entry per tool.

## Development

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test                 # unit, provider, agent and security regression tests
cargo test --test security # security regressions only
```

## License

EuLLM Agent is licensed under [AGPL-3.0-or-later](LICENSE). Use it, fork it,
modify it, run it commercially: the one condition is copyleft. If you modify
EuLLM Agent and let others use it over a network (including as a hosted
service), you must offer them the Corresponding Source of your modified
version. Versions published up to and including 0.1.7 remain available to
everyone under their original Apache 2.0 terms; the AGPL governs new work from
this change onwards.

**I3K Technologies Srl holds the copyright** and also offers this software
under a separate commercial licence, for organisations that cannot accept the
AGPL's terms. Enquiries: **info@i3k.eu**

Because the project is licensed both ways, contributions need a Contributor
Licence Agreement: a one-line statement in your pull request. You keep the
copyright in your own work; [CLA.md](CLA.md) explains exactly what it grants
and why it is necessary. See [CONTRIBUTING.md](CONTRIBUTING.md).
