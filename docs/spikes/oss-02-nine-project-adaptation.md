# CUI // SP-CTI

# OSS-02 — Nine-project adaptation analysis

**Date:** 2026-07-26
**Sources evaluated:**
- anything-llm — https://github.com/Mintplex-Labs/anything-llm (MIT)
- RAGFlow **DeepDoc** — https://github.com/infiniflow/ragflow/blob/main/deepdoc/README.md (Apache-2.0)
- LocalAI — https://github.com/mudler/LocalAI (MIT)
- AutoGen — https://github.com/microsoft/autogen (MIT; CC-BY-4.0 docs)
- mem0 — https://github.com/mem0ai/mem0 (Apache-2.0)
- CrewAI — https://github.com/crewAIInc/crewAI (MIT)
- agent-chief — https://github.com/SmileLikeYe/agent-chief (MIT)
- watch-skill — https://github.com/oxbshw/watch-skill (MIT)
- rocketplaneIO — https://github.com/olemeyer/rocketplaneIO (Apache-2.0)

**Method:** every ICDEV claim below was verified against the tree at `C:\ai\icdev` on 2026-07-26
(file:line where cited), and board state was queried against the **configured PostgreSQL backend**.
Upstream claims come from the projects' own READMEs and manifests, fetched the same day.

**Prior art this document defers to (do not re-litigate):**
`docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md`,
`docs/spikes/agx-00-agentic-architectures-adaptation.md` (superseded by **ADR D391**, which is
authoritative), `docs/spikes/cdp-00-browser-automation-airgap-adaptation.md`, **ADR D67**
(local inference), and the `NOTICE` "ARCHITECTURAL INSPIRATIONS" batches.

---

## 1. Headline verdict

**Six of the nine were already decided. The three nobody has heard of are the only ones that were
never evaluated.**

A repo-wide case-insensitive search (including `agent_chief`, `rocket-plane`, `watch_skill`,
`watchskill` variants) returns **zero matches** for agent-chief, rocketplaneIO and watch-skill. Every
other project on the list already appears in `NOTICE`, an ADR, a prior spike, a competitive-scan
config, or the provider abstraction.

| # | Project | Licence | Verdict |
|---|---|---|---|
| 1 | anything-llm | MIT | **Reject** — competing product, npm/Docker runtime, duplicates DIC + RAG + `/chat` |
| 2 | DeepDoc | Apache-2.0 | **Already decided (oss-00) — successor work is live on the board** |
| 3 | LocalAI | MIT | **Already supported — configuration, not code** |
| 4 | AutoGen | MIT | **Reject — upstream is in maintenance mode; already credited as inspiration** |
| 5 | mem0 | Apache-2.0 | **Reject runtime; already partly adopted; remaining question is measurement** |
| 6 | CrewAI | MIT | **Reject — already credited as inspiration; format interop already exists** |
| 7 | **agent-chief** | MIT | **Never evaluated — adapt one idea, narrowly** |
| 8 | **watch-skill** | MIT | **Never evaluated — reject runtime; premise does not hold here** |
| 9 | **rocketplaneIO** | Apache-2.0 | **Never evaluated — reject** |

All nine are MIT or Apache-2.0, so **none is licence-blocked** — `_BLOCKING_LICENSES`
(`tools/workflow/coherence_checker.py:1784`) covers the GPL/AGPL/LGPL family plus
`tutorial-restrictive`. Any project cited *in code* still needs an `_ATTRIBUTION_REGISTRY` entry
first (14 entries today, `coherence_checker.py:1630-1778`).

---

## 2. The six already-settled — with receipts

Recorded briefly and with citations so a future session can confirm rather than re-derive.

### 2.1 anything-llm — reject
Node/React/Express + Docker + its own vector DB, telemetry on by default. npm and Docker-required
paths are standing non-goals (`oss-00` §5 non-goals; ADR **D-CRX-9** rejects React/Vue SPA + Vite +
npm component libraries; **D362** keeps canvas rendering on vanilla JS + SVG). Nothing is adoptable
as code, and it duplicates DIC, the RAG subsystem, `/chat`, and tenants wholesale.

It is already tracked as a **competitive-scan target**, not a candidate:
`args/innovation_config.yaml:552-553` (`name: anythingllm`, `repo: Mintplex-Labs/anything-llm`), and
`docs/features/dic-discovery.md:111` frames it as *validating* DIC's air-gap posture rather than
threatening it.

### 2.2 DeepDoc — already decided; the successor work is live
`oss-00` ruled **"reject the implementation, take the goal"**: DeepDoc pulls torch/Paddle and
downloads HuggingFace weights on first use, which `extractors.py` already documents as not air-gap
safe — the reason `layout_mode()` is permanently `flat-ocr`. Its genuinely valuable part is table
fidelity, recoverable via `pdfplumber.extract_tables()` without torch, Paddle, or weights (oss-00
adaptation **A2b**).

**That successor work already exists on the board.** Queried against PostgreSQL:
`oss-table-01` ("Real table extraction via pdfplumber") and `oss-table-02` (close the extraction
optional-dependency cliff) are both **`scheduled`**, and `oss-gate-00` is **`done`** — the `oss-`
card is released and dispatchable.

**Therefore: no new card, no new analysis.** Creating a second table-extraction task would duplicate
live work. The honest status is that A2b has *not shipped yet* — verified: `extract_tables()` is
still never called anywhere in the extraction path, and tables are still flattened to `" | "` joins
in DOCX (`extractors.py` `--- Tables ---` heading), XLSX, and PDF — but it is queued, not forgotten.

### 2.3 LocalAI — already supported, as configuration
LocalAI is written in **Go**, so nothing is adoptable as code. But it does not need to be: ADR **D67**
already generalised local inference to a single OpenAI-compatible provider, and
`pyproject.toml:86` states it outright — *"Works with: vLLM, llama.cpp server, TGI, **LocalAI**,
LM Studio, Ollama, etc. Config: set `type: openai_compatible` + `base_url`."*

`OpenAICompatibleProvider` serves both inference and embeddings, so a LocalAI deployment is reachable
today with a ~4-line YAML block and no code. `tools/airgap/detector.py::probe_local_llm_servers()`
already knows LocalAI's name and `LOCALAI_BASE_URL` env var.

Two small gaps, recorded as defects in §6: the probe's default port is wrong, and there is no named
`localai:` entry in `args/llm_config.yaml` (only `openai`, `vllm`, `mistral`, `mistral_vllm` use
`openai_compatible`).

### 2.4 AutoGen — reject, twice over
**First: upstream is in maintenance mode.** Microsoft's own README states no new features are
planned and directs users to Microsoft Agent Framework for production. Adopting a framework whose
maintainer has stopped developing it is a non-starter irrespective of merit.

**Second: its ideas were already taken, deliberately and on the record.**
`NOTICE:374` credits `microsoft/autogen` for *"Multi-agent conversation orchestration, nested chat
patterns, agent-as-tool composition"*, inside the 2026-05 batch whose preamble (`NOTICE:344-346`)
states: *"No code was copied from any of these sources; they informed design patterns only."*

AutoGen is **not imported anywhere** — no `import autogen` / `pyautogen` in any `.py`, and it appears
in neither `requirements.txt` nor `pyproject.toml`. Format-level interop already exists via
`tools/ace/skill_adapter.py:289 _normalize_autogen`.

See §6 D3 for the one loose end this surfaced.

### 2.5 CrewAI — reject; already credited, and version-incompatible
`NOTICE:369` credits `crewAIInc/crewAI` for *"Role-based multi-agent coordination matrix, crew
kickoff lifecycle, task delegation patterns"* under the same no-code-copied preamble. Skill-format
interop exists at `tools/ace/skill_adapter.py:274 _normalize_crewai` (role/goal/backstory YAML).

Independently, CrewAI requires **Python `>=3.10,<3.14`**. ICDEV's floor is `>=3.9`
(`pyproject.toml:10`) and the interpreter in this environment is **3.14** — CrewAI excludes both
ends of that range.

And structurally there is nothing to gain: CrewAI's differentiator is Crews (autonomy) plus Flows
(deterministic control). ICDEV already has both halves — ACE co-worker teams with YAML-declared
roles (`args/ace/roles/*.yaml`, `tools/ace/role_loader.py`) for autonomy, and the kanban/workflow
engine for deterministic control. `oss-00:82` already ruled: *"Do not import a second agent
framework."* ADR **D391** rejects the LangGraph/LangChain stack on the same
adopt-patterns-not-the-stack grounds, recorded expressly so no future session re-proposes it.

### 2.6 mem0 — see §3
Already cited as concept inspiration; the remaining question is measurement, not adoption.

---

## 3. mem0 — already partly adopted, and the rest is already built

### 3.1 What was already taken
`tools/document_intelligence/chat_memory.py:9` is explicitly *"adapted from getzep/graphiti, mem0,
'Memory is Reconstructed, Not Retrieved', and 'Training-Free Lexical-Dense Fusion for
Conversational-Memory Retrieval'"*. The registry entry lives under `getzep/graphiti`
(`coherence_checker.py:1766-1774`), which records mem0 as a co-inspiration, concept-only, no code.

Its design is deliberately conservative: a turn's remembered subject is derived **strictly from the
prior turn's grounded citations** and never invented; a follow-up is detected lexically and
*reconstructed* by prepending that subject so the existing grounded retrieval and citation machinery
resolves it normally. Memory adds context to a query; it never substitutes for retrieval and never
short-circuits citations. RLS-scoped, and toggleable via `ICDEV_DIC_CHAT_MEMORY`.

That is DIC-chat-session scoped. mem0's broader claims are cross-session and user-scoped.

### 3.2 Cross-session memory is also already built — and on by default
Verified directly:

- `icdev/tools/llm/agent_loop.py:747-756` — `memory_enabled` defaults to **True**,
  `memory_top_k=5`, `memory_tier="episodic|semantic"`, and
  `_retrieve_memory_context(user_prompt, memory_top_k, memory_tier)` injects recalled memory into
  every fresh loop (skipped only when resuming a session or when `initial_messages` is supplied).
- `tools/memory/` contains `auto_capture.py`, `auto_consolidate.py`, `memory_consolidation.py`,
  `time_decay.py`, `embed_memory.py`, `hybrid_search.py`, `semantic_search.py`,
  `session_indexer.py`, `history_compressor.py`, `maintenance_cron.py`.

Mapping mem0's differentiators onto that:

| mem0 claim | ICDEV equivalent | Working? |
|---|---|---|
| Tiered memory | `memory_tier="episodic\|semantic"`, on by default | Yes |
| Automatic extraction/capture | `tools/memory/auto_capture.py` | Yes |
| Consolidation instead of UPDATE/DELETE cycles | `memory_consolidation.py`, `auto_consolidate.py` | **No — dead code, see §3.3** |
| Hybrid semantic + keyword retrieval | `hybrid_search.py`, `semantic_search.py`, `embed_memory.py` | Yes (full-table scan, no ANN index) |
| Relevance decay over time | `time_decay.py` | Yes, computed on the fly — but the `decay_weight` column it implies is dead (D7) |
| Scheduled upkeep | `maintenance_cron.py` | Yes |
| Entity linking *across* memories | **Partial** — the KG is a separate subsystem, not fused into memory recall | — |
| Temporal ranking relative to *intent* (past / current / upcoming) | **Not present** — `time_decay` is recency decay, which is a different thing | — |

### 3.3 The decisive finding: consolidation is already built, and it is dead code

Mapping mem0 against `tools/memory/` surfaced a defect that matters more than the adoption question.
**`MemoryConsolidator` cannot work.** Both of its paths fail, and both failures are swallowed:

1. **Primary path** — `memory_consolidation.py:103` does
   `from tools.memory.hybrid_search import hybrid_search`. That symbol **does not exist**;
   `hybrid_search.py` exports `search`, `hybrid_rank`, `bm25_search`, `semantic_search`,
   `fts5_search`, `get_all_entries`. Caught by a broad `except (ImportError, Exception)`.
2. **Jaccard fallback** — `:128` and `:353` run
   `SELECT id, content, entry_type FROM memory_entries`. The column is **`type`**; `entry_type`
   does not exist. Caught at `:159`, returns `[]`.

Both proven empirically against the live PostgreSQL backend on 2026-07-26:

```
import hybrid_search : ImportError -> cannot import name 'hybrid_search' from 'tools.memory.hybrid_search'
entry_type SQL      : UndefinedColumn -> column "entry_type" does not exist
```

Net effect: `check_for_consolidation()` **always** returns `KEEP_SEPARATE` / `should_write=True`, so
near-duplicate memories accumulate forever and nothing is ever merged. Exact-hash dedup still works,
so the failure is invisible — the table just grows with semantically redundant rows.

### 3.4 Recommendation: no adoption. Fix what is already built, then measure.

mem0's headline is benchmark numbers (LoCoMo 92.5, LongMemEval 94.4). **The RAPTOR case is why
"the README has good numbers" is not an argument here**: that DROP decision was later **withdrawn**
on re-measurement against the v2 48-query set (`oss-00:67`), where RAPTOR showed +0.0208 recall@5.
Upstream benchmarks measure upstream's corpus, not ours.

But the sequencing argument is stronger than the measurement argument. ICDEV already has the
architecture mem0 describes — tiering, capture, consolidation, hybrid retrieval, decay, scheduled
upkeep. Adopting a second memory system while this one's consolidation stage silently no-ops would
buy a feature ICDEV already owns and does not run.

So, in order:
1. **Fix the consolidation defects** (§6 D5) — restore the capability that already exists.
2. **Then measure** with `tools/rag/rag_benchmark.py` against `args/rag/golden_query_set.yaml`, the
   same harness that produced the KEEP(`contextual_retrieval`) / DROP(`raptor`) decisions: is the
   memory tier earning its keep, and would intent-relative temporal ranking beat plain decay?
3. **Build only if the numbers say so.**

Anything beyond that is a second memory system competing with `tools/memory/`, the agent-loop tier,
`co_learning_store`, the KG, and `chat_memory.py`.

---

## 4. agent-chief — the one genuinely new idea

MIT, Python 3.12+, ~1k stars, 418 offline tests, published evaluation benchmarks and reproducible
cost figures. A "chief of staff" for agents: incoming events (alerts, agent reports, CI
notifications, feeds) pass through a three-stage **worthiness** engine that decides whether to
**interrupt** the user, **dispatch** work to an agent, or **file** it for later. The upstream claim
is 24 events in → 1 interruption.

Notably, the project markets itself as *"evaluated, not asserted"* — every metric backed by offline
tests a reader can run. That posture matches this repo's own measurement discipline.

### 4.1 What ICDEV already has — the gap is narrower than the README implies
`tools/notifications/` is a real subsystem, not a stub:

- `gateway.py` — dispatch entry point
- `routing_rules.py` — `load_rules`, `resolve_channels`, `_dimension_matches` (rule-based
  channel selection by dimension)
- `escalation.py` — `register_alert`, `acknowledge`, `process_escalations`, `get_escalation`,
  `ack_link`; ack tokens, timers, audited
- `preferences.py` — per-recipient preferences
- plus digest behaviour in at least one reflex (`tools/genesis/reflexes/dic_digest.py`)

So routing, escalation, acknowledgement and preferences all exist. **What is absent is a scored
worthiness decision upstream of routing** — nothing currently decides *whether an event deserves
attention at all* before choosing where to send it.

### 4.2 Why that gap plausibly matters here
ICDEV runs dozens of Genesis reflexes on schedules (awareness every 3h, foundry every 12h, OSINT
every 4h, and many more), a kanban board that generates cards autonomously, and an awareness engine
that promotes predictions into suggested tasks. That is exactly the event-volume profile
agent-chief targets.

### 4.3 Recommendation
**Adopt the pattern, narrowly, and only after evaluating it.** One task to assess a worthiness stage
in front of `resolve_channels` — scored interrupt / dispatch / file — explicitly **not** a new
notification system, and explicitly not the package (Python 3.12+ against a 3.9 floor). Needs an
`_ATTRIBUTION_REGISTRY` entry before any file cites it.

---

## 5. watch-skill and rocketplaneIO

### 5.1 watch-skill — reject the runtime, and the premise does not hold here
MIT, Python, ~230 stars. Converts video, streams and screen recordings into searchable timestamped
evidence, with "THE LOOP" (capture → critique → fix → prove) for verifying agent work.

**Reject the runtime.** OCR plus local transcription means model weights; sourcing from "1,800+
sites" means network egress; and its integration surface is LangChain / CrewAI / LlamaIndex adapters
ICDEV deliberately does not have. All three collide with air-gap constraints and standing non-goals.

**The premise also does not survive checking.** The obvious hook would be "we record video and never
look at it" — `playwright.config.ts:39` does set `video: 'on'`. But `playwright/videos/` contains
**0 files**. What actually accumulates is **91 MB of `playwright/screenshots/` and 311 MB of
`playwright-report/`**, and `tools/testing/screenshot_validator.py` already performs vision-LLM
assertion checking over screenshots (air-gap aware: local LLaVA via Ollama, or cloud vision when
connected), wired to MCP as `validate_screenshot` and into `e2e_runner.py` via
`--validate-screenshots` / `--vision-assertions`.

So the capture-but-never-analyse gap is largely already closed, in the medium actually produced.
**No action**, beyond noting ~400 MB of accumulated test artefacts as a housekeeping matter.

### 5.2 rocketplaneIO — reject
Apache-2.0, Go control plane + agent with a Next.js UI, alpha, and explicitly not production-ready
(*"don't point it at production yet"*). eBPF-based zero-instrumentation tracing for
HTTP/gRPC/SQL/Redis/Kafka, plus an MCP interface letting external agents perform Kubernetes
mutations under a transaction-based safety model with human approval gates.

**Reject.** Go is unadoptable into this Python tree; eBPF Kubernetes tracing is outside ICDEV's
scope; and the one transferable idea — transaction-scoped mutations behind human approval gates
exposed via MCP — is already ICDEV's model: HITL hooks in the agent loop
(`icdev/tools/llm/agent_hitl.py`), `tools/security/sandbox_executor.py`,
`tools/security/mcp_tool_authorizer.py` deny-first RBAC, ACE trust tiers gating `write_file` /
`run_tool`, and an append-only audit trail. Nothing to take.

---

## 6. Defects found while mapping

Independent of any adaptation; each stands on its own.

| # | Defect | Location | Severity |
|---|---|---|---|
| D1 | **LocalAI probe uses the wrong default port.** The docstring says *"LocalAI (8080)"* but the code defaults to `http://localhost:8081/v1` — 8080 having been taken by `llama_cpp` in the same list. LocalAI's actual upstream default is 8080, so a stock install is not detected by air-gap probing. There is also no named `localai:` provider in `args/llm_config.yaml`, though `type: openai_compatible` makes that a ~4-line addition. | `tools/airgap/detector.py:93` vs `:105` | Low |
| D2 | **Template chunking shipped but has no caller.** `oss-chunk-01` delivered `args/chunking_templates.yaml` and `tools/rag/chunking_templates.py` with 10 templates (`oscal_catalog`, `stig_checklist`, `rfp_sow`, `contract`, `sop_runbook`, …). But `tools/document_intelligence/ingest_orchestrator.py:1740` calls `chunk_content(text, source_type="dic_document", …)` **without `template=`**, so every DIC-ingested document still gets `general` sliding-window chunking. The capability that was built to stop splitting controls mid-control is not reached by the pipeline it was built for. | `ingest_orchestrator.py:1740` | **Medium** |
| D3 | **Four skill cards describe AutoGen agents that nothing executes.** `.claude/commands/{code_review_agent,test_orchestrator_agent,security_researcher,senior_software_engineer}.md` embed JSON agent definitions (`system_message`, `human_input_mode`, `max_consecutive_auto_reply`) seeded from SkillHub with Trust Score 0.3 and provenance `local://official-seed/autogen/…`. AutoGen is not imported anywhere, so these are inert data blobs presented as capabilities. Same class as the phantom `AirgapDriverMissingError` found in `cdp-00` — documentation asserting a capability the code does not provide — though materially lower severity, since these are seeded marketplace cards rather than a safety guarantee. | `.claude/commands/*.md` | Low |
| D4 | *(not new — already tracked as `oss-table-02`, `scheduled`)* extraction optional-dependency cliff: `pdfplumber`, `pymupdf`, `openpyxl`, `pypdfium2` are all undeclared optional imports, so on a clean install three of the four PDF passes and all XLSX extraction silently no-op. | `requirements.txt` vs `extractors.py` | Medium |
| D5 | **Memory consolidation is dead code.** Both paths fail and both failures are swallowed: the import at `:103` names a symbol that does not exist, and the fallback SQL at `:128`/`:353` selects a column (`entry_type`) that does not exist — the column is `type`. `check_for_consolidation()` therefore always returns `KEEP_SEPARATE`, so semantically redundant memories accumulate forever. Exact-hash dedup still works, which is exactly why nobody noticed. Both failures reproduced against live PostgreSQL. | `tools/memory/memory_consolidation.py:103, 128, 353` | **High** |
| D6 | **`memory_read` cannot render DB entries.** `read_db_recent()` selects **6** columns (`content, type, importance, created_at, classification, compartment`, `:70`) but `format_markdown()` unpacks **4** (`:114`) — composing them raises `ValueError: too many values to unpack (expected 4)`, reproduced directly. Separately, `read_db_recent` builds SQL with bare `?` placeholders (`:73, :76, :78`), which is SQLite dialect — on the configured PostgreSQL backend this trips the `translate_sql` bare-placeholder warning. This is the module behind `python tools/memory/memory_read.py --format markdown`, the first command in CLAUDE.md's Session Start Protocol. | `tools/memory/memory_read.py:70` vs `:114` | **Medium** |
| D7 | **`decay_weight` is a dead column.** Written as `1.0` on every insert; `memory_write.py:88` comments that it is *"managed by hybrid_search decay pass"* — no such pass exists (`grep decay_weight tools/memory/hybrid_search.py tools/memory/time_decay.py` returns nothing). The only writer is `reset_decay()`, which has no callers. Actual decay is computed on the fly from `created_at`, so retrieval never strengthens a memory and the column is misleading. | `tools/memory/memory_write.py:88, 282` | Low |

---

## 7. Cost, risk, governance

- **Dependency budget: zero new runtime dependencies.** Nothing here proposes adopting a package.
- **Attribution:** `agent-chief` needs an `_ATTRIBUTION_REGISTRY` entry before any file cites it.
  mem0 is already covered indirectly under the `getzep/graphiti` entry; if a memory change cites it
  directly, give it its own entry. Follow the wording precedent in `tools/agent_toolkit/__init__.py`
  — concept adopted, independent implementation, no runtime dependency.
- **Do not duplicate live work:** `oss-table-01` / `oss-table-02` already cover the DeepDoc-derived
  table extraction. This document deliberately creates no card for it.
- **Public repo:** this document carries file paths, version numbers and design intent only.
- **Delivery:** worktree-first branches; project card plus seeded kanban tasks; every gated task must
  carry `depends_on_task_id` — the `-gate-00` id suffix alone only protects the sentinel.

**Non-goals reaffirmed:** npm; Docker-required paths; a second agent framework; vendored
torch/Paddle weights; any cloud-hosted memory or scraping service.

---

## 8. What survives

Seven tasks, not nine epics — and note that **five of the seven are defect fixes, not adaptations**.
That ratio is the real result of this review: the nine projects yielded one narrow idea worth
evaluating, and mapping them against the tree found more broken-in-place capability than missing
capability.

| Task | What |
|---|---|
| `oss2-fix-04` | **D5 — repair memory consolidation** (dead import + wrong column name). Do this first; it restores a capability ICDEV already claims |
| `oss2-meas-01` | **Then** measure whether the memory tier earns its keep, and whether intent-relative temporal ranking beats plain decay (§3.4) |
| `oss2-triage-01` | Evaluate a worthiness stage in front of `resolve_channels`, adapted from agent-chief (§4) — the one genuinely new idea |
| `oss2-fix-01` | D1 — LocalAI probe port + named provider entry |
| `oss2-fix-02` | D2 — wire template chunking into DIC ingest |
| `oss2-fix-03` | D3 — resolve the inert AutoGen skill cards |
| `oss2-fix-05` | D6 + D7 — `memory_read` 6-vs-4 unpack and bare `?` placeholders on PostgreSQL; retire or wire the dead `decay_weight` column |

Everything else on the list is either already decided, already built, or already queued.

# CUI // SP-CTI
