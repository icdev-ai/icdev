# LLM Provider Abstraction (Vendor-Agnostic)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## LLM Provider Abstraction (Vendor-Agnostic)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| LLM Provider Base | tools/llm/provider.py | ABC base classes (LLMProvider, EmbeddingProvider), vendor-agnostic LLMRequest/LLMResponse, message/tool format translators | — | — |
| LLM Router | tools/llm/router.py | Config-driven function→model routing with fallback chains, reads args/llm_config.yaml | function name | (provider, model_id, config) |
| RL Router | tools/llm/rl_router.py | Q-Learning RL agent that ranks model chains by learned success/latency history (epsilon-greedy, TD(0)); persists Q-table to llm_rl_qtable in icdev.db | --stats, --reset, --function, --model, --json | Q-table stats |
| Bedrock Provider | tools/llm/bedrock_provider.py | AWS Bedrock LLMProvider: Anthropic models, thinking/effort, tools, structured output, retry/backoff | LLMRequest | LLMResponse |
| Anthropic Provider | tools/llm/anthropic_provider.py | Direct Anthropic API LLMProvider via anthropic SDK | LLMRequest | LLMResponse |
| OpenAI-Compat Provider | tools/llm/openai_provider.py | OpenAI-compatible LLMProvider: OpenAI, vLLM, Azure via configurable base_url | LLMRequest | LLMResponse |
| Ollama Native Provider | tools/llm/ollama_provider.py | Native Ollama REST API provider using /api/chat — faster than OpenAI-compat for local models, native vision support | LLMRequest | LLMResponse |
| Embedding Provider | tools/llm/embedding_provider.py | Embedding providers: OpenAI, Bedrock Titan, Ollama (nomic-embed-text) | text | float[] |
| LLM Config | args/llm_config.yaml | Master config: providers, models, per-function routing chains, embedding config, pricing | — | — |
| LLM Gateway | tools/llm/gateway.py | LLM Gateway/Proxy: pre/post-invoke security checks (injection detection, PII scrubbing, rate limiting), audit trail, gate check | --invoke, --check, --rate-status, --gate, --json | Gateway response + audit |
| LLM Gateway Config | args/llm_gateway_config.yaml | Gateway config: injection rules, PII patterns, rate limits, audit settings, gate thresholds | (data) | YAML config |
| Prompt Registry | tools/llm/prompt_registry.py | Prompt version control: version, activate, rollback, diff, A/B test prompt templates with audit trail | --register, --activate, --rollback, --diff, --ab-test, --list, --json | Prompt versions + diffs |
| Cost Intelligence | tools/llm/cost_intelligence.py | Cost intelligence: anomaly detection, monthly projection, optimization recommendations, edge-vs-cloud comparison | --report, --anomalies, --project, --optimize, --edge-vs-cloud, --json | Cost analysis + recommendations |
| Model Drift Monitor | tools/llm/model_monitor.py | Model drift monitor: quality scoring, latency/token tracking, statistical drift detection (Welch's t-test), gate check | --check, --report, --drift, --gate, --model, --json | Drift analysis + gate status |
| LLM Eval Runner (OPT-64) | tools/llm/eval_runner.py | Declarative YAML→side-by-side provider comparison (promptfoo pattern, MIT). Loads args/llm_evals/*.yaml, runs prompts across logical models via LLMRouter, evaluates 6 assertion types (contains/not_contains/regex/max_length/min_length/json_schema), writes markdown+json+html reports to reports/llm_evals/ | --eval NAME, --models, --output-dir, --json, --gate | EvalReport (md/json/html) |

