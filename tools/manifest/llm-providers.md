# LLM Providers (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## LLM Providers (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gemini Provider | tools/llm/gemini_provider.py | Google Vertex AI Gemini LLM provider | (library) | GeminiProvider |
| CLI Bridge Activate | tools/llm/cli_bridge/activate.py | Auto-enable Claude CLI provider — prepends `claude-cli` to routing chains when air-gapped or no cloud key; gated by `ICDEV_CLI_BRIDGE` env | config dict | rewritten config |
| CLI Bridge Job Store | tools/llm/cli_bridge/job_store.py | RLS-aware CRUD over `cli_llm_jobs` for deferred CLI LLM requests: create/claim/complete/fail/get/list + `wait_for_job` (polls to terminal or timeout). Atomic guarded claim; never raises on benign races | (library) | job dict / job_id |
| CLI Bridge Provider | tools/llm/cli_bridge/cli_provider.py | `CLILLMProvider` (ptype=`cli`): `invoke` = `create_job` → dispatch to backend → `wait_for_job(soft_wait_seconds)`. done→LLMResponse; error→LLMUnavailableError; still running→`CLIJobDeferred(job_id=...)` (subclasses LLMUnavailableError so chat callers go background while others fall back) | LLMRequest | LLMResponse / raises |
| CLI Bridge Subprocess Backend | tools/llm/cli_bridge/subprocess_backend.py | `dispatch(job_id, backend)` runs `claude -p <prompt> --output-format json` in a daemon thread (no hard kill; ceiling `ICDEV_CLI_BRIDGE_MAX_SECONDS`=900), parses result+tokens, then `complete_job`/`fail_job`. Bounded concurrency (`ICDEV_CLI_BRIDGE_MAX_CONCURRENT`=3) + in-flight dedup. Emits `cli_synthesis` SSE progress (queued→running→done) keyed by job_id. Non-blocking; never raises | job_id / job dict | (writes job row) |

