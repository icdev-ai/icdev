---
ontology_id: icdev:mission:m-cortex-01-unified-ai-layer:step:1
step_class: icdev:Lab
---

# ICDEV Cortex — The Unified AI Layer

Before Cortex, every feature that needed AI wired its own backends by hand: call
`LLMRouter` for completion, `rag_search` for retrieval, `kg_search` for the knowledge
graph, DIC for documents, a keyword index for exact matches — each with its own auth,
governance, and error handling. **Cortex** (`tools/cortex/`) collapses all of that into
**one governed facade**. You ask Cortex; Cortex picks the backend and runs every answer
through governance.

## The capability surface

Cortex is a **governed function namespace**, not a class: application code does
`from tools.cortex import ask, search, complete, classify, extract, reason, govern, agent`
(`tools/cortex/api.py`). The same capabilities are exposed as MCP tools:

| MCP tool | What it does |
|----------|--------------|
| `cortex_complete` | Text generation via the config-routed LLM (`LLMRouter`) |
| `cortex_search` | Unified retrieval across rag / graph / dic / kb with strategy routing |
| `cortex_extract` | Pull structured fields (to a JSON schema) out of text |
| `cortex_classify` | Label an input into one of the supplied labels (+ heuristic fallback) |
| `cortex_ask` | Natural-language data question (IQE primary, NL→SQL fallback) |
| `cortex_reason` | Multi-step reasoning: `cot` / `debate` / `council` |
| `cortex_govern` | Run the enforced TRUST chain standalone over produced text |
| `cortex_agent_launch` | Run a goal through the ACE team / agent loop, governed |

The registry describes Cortex as *"one governed facade over RAG, KG, documents, and
keyword search (complete/classify/extract/search/ask/govern/agent)"*
(`args/component_registry.yaml`, key `cortex`). Every facade is wrapped by the enforced
governance pipeline — there is no ungoverned path, and a blocked call raises
`GovernanceBlockedError`.

## When to use Cortex vs. RAG directly

- **Use Cortex** when your feature needs an *answer* and you would otherwise have to
  choose and wire a backend yourself — Cortex routes and governs for you, so you get
  consistent classification markings and provenance for free.
- **Call `rag_search` / `kg_search` directly** only when you are building the retrieval
  layer itself, or you need a raw hit list with no reasoning or governance envelope.

The rule of thumb: **application code asks Cortex; infrastructure code implements the
backends Cortex routes to.**

## What you'll build

A miniature `CortexFacade` (a teaching class — the real Cortex is the function
namespace above) that models the two moves that make Cortex valuable:

1. **Route** — `classify_intent()` maps a natural-language request to a capability.
2. **Govern** — `govern()` wraps every response in a classification + provenance
   envelope, and `CortexFacade.invoke()` ties routing and governance together so no
   answer ever leaves ungoverned.

The handlers here return canned data (this lab is offline and deterministic) — in the
real system they call `LLMRouter`, RAG, and the KG. Open `step1_starter.py` and
implement the three `TODO`s.
