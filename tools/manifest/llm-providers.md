# LLM Providers (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## LLM Providers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gemini Provider | tools/llm/gemini_provider.py | Google Vertex AI Gemini LLM provider | (library) | GeminiProvider |
| CLI Bridge — Activate | tools/llm/cli_bridge/activate.py | Auto-enable logic + routing-chain rewrite that prepends `claude-cli` to every function's chain | env, llm_config.yaml | patched config |
| CLI Bridge — Capability | tools/llm/cli_bridge/capability.py | Backend capability probes (`is_cli_headless_capable`, `mailbox_worker_alive`) | env, PATH | boolean |
| CLI Bridge — CLI Provider | tools/llm/cli_bridge/cli_provider.py | `CLILLMProvider` — job-store-backed LLM provider that defers to a local Claude CLI | LLMRequest | LLMResponse / CLIJobDeferred |
| CLI Bridge — Job Store | tools/llm/cli_bridge/job_store.py | CRUD + claim + wait on `cli_llm_jobs` rows (mutable, RLS-aware) | job dict | job row |
| CLI Bridge — Subprocess Backend | tools/llm/cli_bridge/subprocess_backend.py | Daemon-thread worker that runs `claude-cli` in a subprocess and writes results back to the job row | job_id | updated row |

