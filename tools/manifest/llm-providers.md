# LLM Providers (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## LLM Providers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gemini Provider | tools/llm/gemini_provider.py | Google Vertex AI Gemini LLM provider | (library) | GeminiProvider |
| CLI Bridge — Activate | tools/llm/cli_bridge/activate.py | Auto-enable logic + routing-chain rewrite that prepends `claude-cli` to every function's chain | env, llm_config.yaml | patched config |
| CLI Bridge — Capability | tools/llm/cli_bridge/capability.py | Backend capability probes (`is_cli_headless_capable`, `mailbox_worker_alive`) | env, PATH | boolean |
| CLI Bridge — CLI Provider | tools/llm/cli_bridge/cli_provider.py | `CLILLMProvider` — job-store-backed LLM provider that defers to a local Claude CLI | LLMRequest | LLMResponse / CLIJobDeferred |
| CLI Provider (inline) | tools/llm/cli_provider.py | `CLIProvider` — direct subprocess LLM provider for the local `claude` CLI in print mode (`-p --output-format text`); pipes system+user prompt via stdin in a temp cwd, returns `LLMResponse` with optional `structured_output` when stdout parses as JSON. Used by `tools/llm/router.py` when a provider is configured with `type: cli`. | LLMRequest | LLMResponse |
| CLI Bridge — Job Store | tools/llm/cli_bridge/job_store.py | CRUD + claim + wait on `cli_llm_jobs` rows (mutable, RLS-aware) | job dict | job row |
| CLI Bridge — Subprocess Backend | tools/llm/cli_bridge/subprocess_backend.py | Daemon-thread worker that runs `claude-cli` in a subprocess and writes results back to the job row | job_id | updated row |
| Provider Scaffold | tools/llm/provider_scaffold.py | One-command LLM provider onboarding. Generates provider module (custom type), patches router.py, registers in args/llm_config.yaml, syncs to root tools/llm/, runs live test + ruff. Supports openai_compatible, ollama, custom. | `--name`, `--type`, `--api_key_env`, `--base_url`, `--model_id`, `--chain_function` | Registered provider + model + summary |

