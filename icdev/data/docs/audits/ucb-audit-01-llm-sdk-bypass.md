<!-- CUI // SP-CTI -->
# Audit: Direct LLM SDK Calls That Bypass LLMRouter

- **Task ID:** ucb-audit-01
- **Type:** research / audit
- **Date:** 2026-06-06
- **Scope:** `tools/` and `icdev/` (the `icdev/tools/*` tree is the companion-sync mirror of `tools/*`)
- **Method:** deterministic grep — see *Reproduction* below

## Reproduction (deterministic)

```bash
# From repo root. Reproduces EXACTLY the call sites in this report.
rg -n "import anthropic|from anthropic|from openai|import openai|OpenAI\(|Anthropic\(" tools/
rg -n "import anthropic|from anthropic|from openai|import openai|OpenAI\(|Anthropic\(" icdev/
```

Excluded by charter (not bypasses, do not re-classify):
- `tools/llm/*_provider.py` — the sanctioned provider abstraction.
- `tools/memory/*embed*` — embedding generation (vector search, not chat/completion routing).
- `tools/slides/graphics_generator.py` — DALL-E image generation (uses raw `requests`, not the SDK; appears only via the `OpenAI`-in-comment hit, not an import).

## Verdict legend

| Verdict | Meaning |
|---------|---------|
| `legitimate-provider` | The sanctioned `tools/llm/` provider module. SDK use is its whole job. |
| `specialized-allowed` | Embeddings / fine-tuning-job / image-gen SDK use. Not chat routing; CLI-bridge N/A. Charter-excluded family. |
| `bypass-to-fix` | A canvas/engine AI feature calling chat completion directly. **Should route through `LLMRouter`** so `ICDEV_CLI_BRIDGE` (and air-gap/Bedrock/multi-cloud routing) applies. |
| `not-an-import` | Regex matched a docstring/string literal, not a real import. AST-based OPT-44 check ignores these. |

---

## Findings — `bypass-to-fix` (4 files, action required)

All four are Genesis **reflex engine** AI features that instantiate `anthropic.Anthropic()` and call `client.messages.create(...)` directly. Each already has a graceful non-LLM fallback, but none honor `ICDEV_CLI_BRIDGE` because they never touch `LLMRouter`. These are the OPT-44 `direct_anthropic_import` coherence violations.

> Note: none of these four files are mirrored under `icdev/tools/...` (the `icdev/` grep returned no genesis reflexes), so a fix lands once in `tools/`.

| # | File:line | Call | What it does | Verdict |
|---|-----------|------|--------------|---------|
| 1 | `tools/genesis/reflexes/fathomdesk_openbb_refresh.py:81-82` | `import anthropic` / `anthropic.Anthropic()` | `_llm_recommend_threshold` — Claude Haiku recommends a Z-score anomaly threshold | `bypass-to-fix` |
| 2 | `tools/genesis/reflexes/fathomdesk_openbb_refresh.py:165-167` | `import anthropic` / `anthropic.Anthropic()` | `_llm_analyze_anomalies` — Claude Haiku one-sentence anomaly narrative | `bypass-to-fix` |
| 3 | `tools/genesis/reflexes/inspect_adapt.py:203,209` | `import anthropic` / `anthropic.Anthropic()` | `_nlp_extract_patterns` — Claude Haiku NLP systemic-pattern extraction from lessons | `bypass-to-fix` |
| 4 | `tools/genesis/reflexes/strategos/osint_harvester.py:906,908` | `import anthropic` / `anthropic.Anthropic()` | OSINT harvest anomaly verdict (normal/suspicious/surge/noise_flood) | `bypass-to-fix` |
| 5 | `tools/genesis/reflexes/sdc_control_expiry.py:145,146` | `import anthropic` / `anthropic.Anthropic()` | Claude Haiku ranks expiring security controls by severity | `bypass-to-fix` |

**Rationale:** each is an engine AI feature (anomaly grading / narrative / classification / ranking) — exactly the chat-completion workload `LLMRouter` exists to route. Direct `anthropic.Anthropic()` breaks air-gap/Bedrock routing and ignores `ICDEV_CLI_BRIDGE`. Fix: replace the inline client with `LLMRouter().get_provider_for_function(...)` (or the appropriate routed completion call), keeping each existing fallback.

---

## Findings — `legitimate-provider` (charter-excluded, the abstraction itself)

| File:line | Call | Verdict |
|-----------|------|---------|
| `tools/llm/anthropic_provider.py:25,70` | `import anthropic` / `Anthropic(**kwargs)` | `legitimate-provider` |
| `tools/llm/openai_provider.py:31,67` | `import openai` / `OpenAI(**kwargs)` | `legitimate-provider` |
| `tools/llm/azure_openai_provider.py:43,145` | `from openai import AzureOpenAI` / `AzureOpenAI(**kwargs)` | `legitimate-provider` |
| `tools/llm/embedding_provider.py:19,82,232` | `import openai` / `OpenAI(**kwargs)` / `from openai import AzureOpenAI` | `legitimate-provider` (embeddings provider) |

These are the sanctioned providers `LLMRouter` dispatches to. The OPT-44 coherence check explicitly whitelists `tools/llm/anthropic_provider.py`.

---

## Findings — `specialized-allowed` (charter-excluded family)

| File:line | Call | Why allowed | Verdict |
|-----------|------|-------------|---------|
| `tools/finetune/openai_provider.py:60,67` | `import openai` / `openai.OpenAI(...)` | Fine-tuning **jobs** API (training lifecycle), not chat routing | `specialized-allowed` |
| `tools/finetune/azure_provider.py:70,72` | `import openai` / `openai.AzureOpenAI(...)` | Azure fine-tuning jobs API | `specialized-allowed` |
| `tools/memory/embed_memory.py:45,47` | `import openai` / `openai.OpenAI(...)` | Embeddings (`*embed*` exclusion) | `specialized-allowed` |
| `tools/memory/semantic_search.py:37,39` | `import openai` / `openai.OpenAI(...)` | Embeddings (`embeddings.create`, `text-embedding-3-small`); prefers `EmbeddingProvider.embed` if passed | `specialized-allowed` |
| `tools/memory/maintenance_cron.py:104,106` | `import openai` / `openai.OpenAI(...)` | Batch embedding backfill | `specialized-allowed` |
| `tools/memory/hybrid_search.py:133,135` | `import openai` / `openai.OpenAI(...)` | Query embedding for hybrid search | `specialized-allowed` |
| `tools/slides/graphics_generator.py:168` | raw `POST https://api.openai.com/v1/images/generations` via `requests` | DALL-E 3 image gen; not the SDK (only a comment/string matched), charter-excluded | `specialized-allowed` |

> The `tools/memory/{semantic_search,maintenance_cron,hybrid_search}.py` files are not literal `*embed*` filenames but belong to the same embeddings exception family (all call `embeddings.create` only). Flagging them as bypasses would be a false positive.

---

## Findings — `not-an-import` (regex false positives)

The grep also matches docstrings/string literals inside the coherence checker that *describes* the OPT-44 rule. These are not imports (the checker uses AST and ignores them):

| File:line | Verdict |
|-----------|---------|
| `tools/workflow/coherence_checker.py:21,2337,2388,2402` | `not-an-import` |
| `icdev/tools/workflow/coherence_checker.py:21,2324,2375,2389` | `not-an-import` |

---

## `icdev/` mirror

The `icdev/tools/*` hits are the companion-sync mirror of the `tools/*` provider + finetune + memory files (same verdicts). The mirror contains **no** genesis-reflex bypasses — those exist only under `tools/genesis/reflexes/`. Apply bypass fixes in `tools/` then `companion.py --sync`.

## Summary

| Verdict | Count (files) |
|---------|---------------|
| `bypass-to-fix` | 4 files / 5 call sites |
| `legitimate-provider` | 4 files |
| `specialized-allowed` | 7 files |
| `not-an-import` | 1 file (2 mirrored) |

**Action:** route the 5 Genesis-reflex Anthropic calls through `LLMRouter` (clears the OPT-44 `direct_anthropic_import` coherence gate and makes `ICDEV_CLI_BRIDGE` apply). Everything else is correctly placed.

<!-- CUI // SP-CTI -->
