# ICDEV LLM Routing Guide

ICDEV is **LLM-vendor agnostic**. A single YAML config file controls which model handles each task — Claude for orchestration, GPT-4o for coding, Gemini for cost-effective compliance checks, Ollama for air-gapped environments. Change the routing without touching any code.

---

## Config File

**`args/llm_config.yaml`** — single source of truth for all LLM model routing (D68, D71).

The file has 4 sections:

| Section | Purpose |
|---------|---------|
| `providers` | Connection details for each vendor (API keys, endpoints, regions) |
| `models` | Model registry with capabilities, token limits, and pricing |
| `routing` | Function-to-model mapping with fallback chains |
| `embeddings` | Embedding model configuration for semantic search |

---

## 1. Providers

Each provider defines how to connect to a vendor's API:

```yaml
providers:
  bedrock:
    type: bedrock
    region: us-gov-west-1          # Uses boto3 IAM credentials

  anthropic:
    type: anthropic
    api_key_env: ANTHROPIC_API_KEY  # Reads from environment variable

  openai:
    type: openai_compatible
    api_key_env: OPENAI_API_KEY
    base_url: https://api.openai.com/v1

  gemini:
    type: gemini
    api_key_env: GOOGLE_API_KEY

  ollama:
    type: ollama
    base_url: http://localhost:11434  # Local inference

  ibm_watsonx:
    type: ibm_watsonx
    api_key_env: IBM_CLOUD_API_KEY
    project_id: ${IBM_WATSONX_PROJECT_ID}
    url: https://us-south.ml.cloud.ibm.com

  vllm:
    type: openai_compatible
    base_url: http://localhost:8000/v1  # Self-hosted vLLM
```

**Environment variables** for API keys:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="AIza..."
export IBM_CLOUD_API_KEY="..."
export OLLAMA_BASE_URL="http://localhost:11434"   # Optional, default shown
```

---

## 2. Models

Each model entry declares its provider, capabilities, and pricing:

```yaml
models:
  claude-opus:
    provider: bedrock
    model_id: anthropic.claude-opus-4-6-20260215-v1:0
    max_output_tokens: 128000
    supports_thinking: true
    supports_tools: true
    supports_vision: true
    pricing: { input_per_1k: 0.005, output_per_1k: 0.025 }

  gpt-4o:
    provider: openai
    model_id: gpt-4o
    max_output_tokens: 16384
    supports_tools: true
    supports_vision: true
    pricing: { input_per_1k: 0.0025, output_per_1k: 0.010 }

  gemini-2.5-pro:
    provider: gemini
    model_id: gemini-2.5-pro-preview-05-06
    max_output_tokens: 65536
    supports_thinking: true
    supports_tools: true
    supports_vision: true
    pricing: { input_per_1k: 0.00125, output_per_1k: 0.010 }

  codestral-local:
    provider: ollama
    model_id: codestral:22b
    max_output_tokens: 8192
    pricing: { input_per_1k: 0.0, output_per_1k: 0.0 }  # Free (local)
```

### Available Models (Pre-Configured)

| Model Key | Provider | Best For |
|-----------|----------|----------|
| `claude-opus` | Bedrock | Architecture, complex reasoning, orchestration |
| `claude-sonnet` | Bedrock | Code generation, compliance, general tasks |
| `claude-haiku` | Bedrock | Fast lightweight tasks, monitoring |
| `o3` | OpenAI | Deep reasoning, task decomposition |
| `gpt-4o` | OpenAI | Code generation, tool use, vision |
| `gpt-4o-mini` | OpenAI | Cost-effective NLQ, simple tasks |
| `gemini-2.5-pro` | Google | Code, architecture, vision (cost-effective) |
| `gemini-2.5-flash` | Google | Fast compliance checks, lightweight tasks |
| `gemini-2.0-flash` | Google | Free tier, simple tasks |
| `granite-code` | IBM watsonx | Code generation (IBM environments) |
| `granite-chat` | IBM watsonx | Chat/tool use (IBM environments) |
| `llama-local` | Ollama | Air-gapped general purpose |
| `codestral-local` | Ollama | Air-gapped code generation |
| `deepseek-local` | Ollama | Air-gapped reasoning |
| `llava-local` | Ollama | Air-gapped vision/screenshots |

---

## 3. Routing — Assigning Models to Tasks

The `routing` section maps each ICDEV function to a **fallback chain** of models. The router tries each model in order and uses the first one that responds:

```yaml
routing:
  # Architect agent — needs the strongest reasoner
  agent_architect:
    chain: [claude-opus, o3, gemini-2.5-pro, gpt-4o, llama-local]
    effort: max

  # Builder agent — needs strong coder
  agent_builder:
    chain: [claude-opus, gemini-2.5-pro, gpt-4o, codestral-local]
    effort: max

  # Compliance agent — good enough with mid-tier
  agent_compliance:
    chain: [claude-sonnet, gpt-4o, gemini-2.5-flash, llama-local]
    effort: high

  # Monitor agent — fast and cheap
  agent_monitor:
    chain: [claude-haiku, gpt-4o-mini, gemini-2.0-flash, llama-local]
    effort: low

  # NLQ queries — fast, cheap, low-stakes
  nlq_sql:
    chain: [gpt-4o-mini, gemini-2.0-flash, claude-haiku, llama-local]
    effort: low

  # Code generation — strong coder needed
  code_generation:
    chain: [claude-sonnet, gemini-2.5-pro, gpt-4o, codestral-local]
    effort: high

  # Default fallback for unlisted functions
  default:
    chain: [claude-sonnet, gpt-4o, gemini-2.5-flash, llama-local]
    effort: medium
```

### All Routable Functions

| Function | Default Primary | Purpose |
|----------|----------------|---------|
| `agent_architect` | claude-opus | ATLAS A/T phases, system design |
| `agent_orchestrator` | claude-opus | Task routing, workflow management |
| `agent_builder` | claude-opus | TDD code generation |
| `agent_compliance` | claude-sonnet | ATO artifact generation |
| `agent_security` | claude-sonnet | Security scanning, analysis |
| `agent_infra` | claude-sonnet | Terraform, Ansible, K8s |
| `agent_knowledge` | claude-sonnet | Pattern detection, recommendations |
| `agent_monitor` | claude-haiku | Log analysis, health checks |
| `task_decomposition` | claude-opus | DAG workflow planning |
| `collaboration` | claude-sonnet | Multi-agent collaboration |
| `nlq_sql` | gpt-4o-mini | Natural language to SQL |
| `code_generation` | claude-sonnet | General code generation |
| `code_translation` | claude-opus | Cross-language translation |
| `code_translation_repair` | claude-sonnet | Fix translation errors |
| `test_translation` | claude-sonnet | Translate test suites |
| `screenshot_validation` | claude-opus | Vision-based UI validation |
| `document_vision` | claude-sonnet | PDF/image analysis |
| `ui_analysis` | claude-opus | Legacy UI screenshot analysis |
| `diagram_extraction` | claude-opus | SysML diagram parsing |
| `compliance_diagram` | claude-sonnet | Compliance diagram validation |
| `attachment_analysis` | claude-sonnet | Jira/ServiceNow attachments |
| `intake_persona_response` | claude-sonnet | Requirements intake AI |
| `bdd_preview` | claude-sonnet | BDD scenario generation |
| `narrative_generation` | claude-sonnet | Report/document generation |
| `compliance_export` | claude-sonnet | OSCAL/eMASS export |
| `saas_proxy` | claude-sonnet | Multi-tenant LLM proxy |
| `child_app` | claude-sonnet | Child app generation |
| `dependency_suggestion` | claude-haiku | Cross-language dep mapping |
| `default` | claude-sonnet | Catch-all for unmapped functions |

### Effort Levels

Each route has an `effort` that controls reasoning depth:

| Level | Behavior | Use Case |
|-------|----------|----------|
| `max` | Extended thinking, full context | Architecture, complex code gen |
| `high` | Thorough analysis | Code generation, compliance |
| `medium` | Balanced speed/quality | General tasks |
| `low` | Fast, minimal reasoning | Monitoring, NLQ, simple lookups |

---

## 4. Common Customization Examples

### Use OpenAI for all coding, Claude for everything else

```yaml
routing:
  agent_builder:
    chain: [o3, gpt-4o, claude-sonnet, codestral-local]
    effort: max

  code_generation:
    chain: [gpt-4o, o3, claude-sonnet, codestral-local]
    effort: high

  code_translation:
    chain: [o3, gpt-4o, claude-opus, codestral-local]
    effort: max
```

### Use Gemini as primary (cost optimization)

```yaml
routing:
  agent_architect:
    chain: [gemini-2.5-pro, claude-opus, gpt-4o]
    effort: max

  agent_builder:
    chain: [gemini-2.5-pro, claude-sonnet, gpt-4o]
    effort: max

  code_generation:
    chain: [gemini-2.5-pro, claude-sonnet, gpt-4o]
    effort: high

  agent_compliance:
    chain: [gemini-2.5-flash, claude-sonnet, gpt-4o-mini]
    effort: high

  nlq_sql:
    chain: [gemini-2.0-flash, gpt-4o-mini, claude-haiku]
    effort: low
```

### Air-gapped environment (local models only)

```yaml
settings:
  prefer_local: true

routing:
  agent_architect:
    chain: [deepseek-local, llama-local]
    effort: max

  agent_builder:
    chain: [codestral-local, llama-local]
    effort: max

  code_generation:
    chain: [codestral-local, llama-local]
    effort: high

  default:
    chain: [llama-local]
    effort: medium
```

### IBM watsonx for Gov/DoD

```yaml
routing:
  code_generation:
    chain: [granite-code, claude-sonnet, gpt-4o]
    effort: high

  agent_builder:
    chain: [granite-code, claude-opus, gpt-4o]
    effort: max

  default:
    chain: [granite-chat, claude-sonnet, gpt-4o]
    effort: medium
```

### Add a new model

1. Add the provider (if new vendor):
```yaml
providers:
  my_provider:
    type: openai_compatible          # Works with any OpenAI-compatible API
    api_key_env: MY_PROVIDER_API_KEY
    base_url: https://api.my-provider.com/v1
```

2. Register the model:
```yaml
models:
  my-model:
    provider: my_provider
    model_id: my-model-v1
    max_output_tokens: 32000
    supports_tools: true
    pricing: { input_per_1k: 0.001, output_per_1k: 0.005 }
```

3. Add to routing chains:
```yaml
routing:
  code_generation:
    chain: [my-model, claude-sonnet, gpt-4o]
    effort: high
```

---

## 5. How the Router Works

```
Function requested (e.g. "code_generation")
    |
    v
Look up chain in args/llm_config.yaml
    chain: [claude-sonnet, gemini-2.5-pro, gpt-4o, codestral-local]
    |
    v
Try model 1: claude-sonnet (Bedrock)
    Available? --> YES --> Use it
                   NO  --> Try next
    |
    v
Try model 2: gemini-2.5-pro (Google)
    Available? --> YES --> Use it
                   NO  --> Try next
    ...
    |
    v
Last resort: codestral-local (Ollama)
    Always available if Ollama is running
```

- **Availability probing** is cached for 30 minutes (configurable via `availability_cache_ttl_seconds`)
- **Prompt injection scanning** runs on every request before sending to the model
- **BYOK keys** (from dashboard `/profile` page) override config-level keys per user

### Python API

```python
from tools.llm.router import LLMRouter

router = LLMRouter()

# Get the best available provider for a function
provider, model_id, config = router.get_provider_for_function("code_generation")
print(f"Using {model_id} via {config.get('provider')}")

# Check routing for a specific function
chain = router._get_chain_for_function("agent_builder")
print(f"Builder chain: {chain}")

# Get effort level
effort = router.get_effort("agent_architect")
print(f"Architect effort: {effort}")  # "max"
```

### CLI Quick Check

```bash
# Show which provider/model would be used for a function
python -c "
from tools.llm.router import LLMRouter
r = LLMRouter()
p, mid, cfg = r.get_provider_for_function('code_generation')
print(f'Provider: {cfg.get(\"provider\", \"unknown\")}')
print(f'Model: {mid}')
print(f'Effort: {r.get_effort(\"code_generation\")}')
"
```

---

## 6. Embedding Providers

Embedding models for semantic search follow the same pattern:

```yaml
embeddings:
  default_chain: [openai-embed, gemini-embed, nomic-embed-local]

  models:
    openai-embed:
      provider: openai
      model_id: text-embedding-3-small
      dimensions: 1536

    gemini-embed:
      provider: gemini
      model_id: text-embedding-004
      dimensions: 768

    nomic-embed-local:
      provider: ollama
      model_id: nomic-embed-text       # Air-gapped safe
      dimensions: 768

    ibm-slate-embed:
      provider: ibm_watsonx
      model_id: ibm/slate-125m-english-rtrvr-v2
      dimensions: 768
```

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D67 | OpenAI-compatible provider covers Ollama, vLLM, Azure | Same API spec, one implementation |
| D68 | Function-level routing (not agent-level) | NLQ needs fast/cheap, code gen needs strong coder |
| D69 | Fallback chains per function | Air-gapped sets `prefer_local: true`, chains end with local models |
| D71 | `llm_config.yaml` is single source of truth | Replaces scattered hardcoded model IDs |
| D72 | Embedding providers same pattern as LLM providers | Consistent ABC pattern |
| D73 | Graceful degradation on missing SDKs | Each provider handles missing imports |

---

## Related Files

| File | Purpose |
|------|---------|
| `args/llm_config.yaml` | All provider, model, and routing configuration |
| `tools/llm/router.py` | LLMRouter class — resolves function to provider+model |
| `tools/llm/providers/` | Provider implementations (Bedrock, Anthropic, OpenAI, Gemini, Ollama, IBM) |
| `args/bedrock_models.yaml` | Bedrock-specific model registry (legacy, still used by `bedrock_client.py`) |
| `args/agent_config.yaml` | Per-agent effort defaults |
