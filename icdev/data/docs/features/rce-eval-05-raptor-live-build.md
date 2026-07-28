# RCE — RAPTOR Live Build & LLM Provider Record (rce-eval-05)

**Classification:** CUI // SP-CTI
**Card:** RCE (RAG Context Engineering)
**Status:** Provider documented; build path proven clean on a bounded scope;
full-corpus run continuing as a background job (see [Build status](#build-status))
**Date:** 2026-07-25

## Purpose

`rce-eval-05` runs `tools/rag/raptor.py --build` against the **live** corpus with a
**live** LLM and records **which provider actually served summarization**. Prior
RAPTOR tasks (`rce-raptor-01`, `rce-raptor-02`) built the hierarchy and retrieval
path against fixtures; this is the first run on real data with real model calls.

The provider question is not answerable from config alone — the routing chain
declares fallbacks and can silently downgrade — so the record below is backed by a
live call through raptor's own code path.

## Configuration

`args/rag_config.yaml` → `rag.raptor`:

| Key | Value |
|-----|-------|
| `group_size` | 5 |
| `max_levels` | 2 |
| `llm_function` | `rag_evaluate` |
| `summary_max_tokens` | 512 |
| `enabled` | `false` |

> `enabled: false` gates **retrieval-time** use of the summary tier. The `--build`
> CLI does not consult it, so the index builds regardless. Retrieval integration
> stays default-OFF as designed.

## LLM provider actually used

Summarization and embedding resolve to **different** providers. This split matters
for egress review.

### Summarization — Ollama Cloud (remote)

| Field | Value |
|-------|-------|
| Router function | `rag_evaluate` (from `rag.raptor.llm_function`) |
| Config provider entry | `ollama_cloud` |
| Provider class | `tools.llm.ollama_provider.OllamaProvider` |
| **Model id** | **`kimi-k2.6:cloud`** |
| **Base URL** | `${OLLAMA_CLOUD_BASE_URL:-https://ollama.com}` — **remote** |
| Auth | `api_key_env: OLLAMA_API_KEY` |
| Pricing | `input_per_1k: 0.0`, `output_per_1k: 0.0` (subscription, not per-call metered) |

### Embeddings — local

| Field | Value |
|-------|-------|
| Resolver | `tools.llm.get_embedding_provider()` |
| `provider_name` | `ollama` |
| **Base URL** | `${OLLAMA_BASE_URL:-http://localhost:11434}` — **local** |
| Vector dim | 768 |

### Evidence — live call, not config resolution

A call through raptor's exact path (`LLMRouter.invoke('rag_evaluate', …)` with the
same `classification="CUI"` request shape the summarizer uses) returned:

```
get_provider_for_function('rag_evaluate')
  -> (OllamaProvider, 'kimi-k2.6:cloud', {'provider': 'ollama_cloud', ...})
LIVE CALL -> model_id=kimi-k2.6:cloud  provider=ollama
content: A quick fox jumps over a lazy dog.
```

No fallback occurred — the tier-1 cloud model served the request.

### Naming trap worth recording

`LLMResponse.provider` reports **`ollama`** (the provider *type*), while the config
entry is **`ollama_cloud`** and the endpoint is `https://ollama.com`. Reading the
response field alone would wrongly suggest a local, air-gap-safe call. The
distinguishing signals are `api_key_env` / `base_url`, **not** `provider`. The same
trap applies to `get_provider_for_function`'s metadata dict, which returns
`base_url=None` — resolve the endpoint from the provider entry in
`args/llm_config.yaml`, not from the returned metadata.

See also the existing [[cli-bridge-cui-egress]] distinction between `ollama` and
`ollama_cloud`.

### CUI egress

The summarizer stamps every request `classification="CUI"` and the resolved chain
is non-local, so the router's redaction sanitizer is applied to message content and
system prompt before egress. That control is engaged and working as designed — CUI
is **sanitized, not blocked**, on this path.

`ai_telemetry` has **no** rows for `function='rag_evaluate'`: this path emits no
per-call telemetry, so provider attribution for a build cannot be reconstructed
from the DB after the fact. **This document is the only durable record.**

## Build status

Full-corpus build is a **serial, single-threaded, ~2.5–6 h job** and does not
complete within one session. It was launched and left running.

Corpus and work estimate:

| Quantity | Value |
|----------|-------|
| `rag_chunks` | 4,111 |
| distinct `source_id` | 2,029 |
| level-1 summaries | 2,169 |
| level-2 (root) summaries | 2,029 |
| **total LLM calls** | **4,198** |
| **total embed calls** | **4,198** |

Progress checkpoints (same long-running process, PID 8716,
`python -u tools/rag/raptor.py --build --json`):

| Time (EDT) | Sources | Rows | L1 | L2 |
|-----------|---------|------|----|----|
| 13:29 | 213 / 2,029 | 457 | 246 | 211 |
| 13:44 | 279 / 2,029 | 598 | 323 | 275 |
| 13:45 | 292 / 2,029 | 624 | 336 | 288 |
| 14:06 | 534 / 2,029 | 1,095 | 572 | 523 |

The build **survived a host power event and resumed cleanly** — writes commit
per-upsert, so nothing already summarized was lost. Between the last two
checkpoints the source-rank probe advanced 292 → 534 in 21 min, i.e. **≈11.5
sources/min**; at that rate the remaining ~1,495 sources need a further **≈2.2 h**.
PID 8716 was confirmed still alive at the 14:06 checkpoint (start time 13:18 EDT),
so this is one continuous process, not a restart. The index is **partial but
functional** — see [Verification](#verification).

At 14:06 every one of the 1,095 persisted summary rows carried a non-null
embedding (`SELECT COUNT(*) … WHERE embedding IS NULL` → `0`), so no row has been
written in a half-built state.

There is no concurrency, no batching, and no progress output.

### Throughput and the response cache

Two earlier profiling runs projected wildly different totals (≈2.6 h vs ≈12 h). The
cause is the router's **persistent, cross-process response cache**: sources
summarized by an earlier process replay in 0.04–0.30 s instead of a full round
trip, so any sample's rate depends entirely on how much of it was already cached.

Consequences:

* **No single throughput number is meaningful.** Observed 12.3 sources/min while
  replaying already-summarized sources, vs 5.4 sources/min on genuinely cold ones.
  Remaining cold work projects to roughly 5–6 h.
* **An interrupted build is cheap to restart.** The CLI has no resume and always
  restarts at source #1, but completed sources replay at cache speed rather than
  re-billing the model.

### Progress observability gotcha

`SELECT COUNT(*)` on `rag_chunk_summaries` is a **misleading** progress signal —
the builder deletes by source then re-inserts, so re-processing an
already-summarized source is net-zero on row count. Because sources are iterated
`ORDER BY source_id`, the correct probe is the **rank of the newest row's
`source_id`** within the sorted source list:

```sql
SELECT source_id FROM rag_chunk_summaries ORDER BY created_at DESC LIMIT 1;
SELECT COUNT(DISTINCT source_id) FROM rag_chunks
 WHERE source_id <> '' AND source_id <= '<that source_id>';
```

Writes commit per-upsert, so partial progress is durable.

### Bounded build — clean completion proof

Because the full-corpus run is a multi-hour background job, the "build succeeds"
half of the acceptance criterion is proven on a **bounded scope that runs to
completion inside one session**. A source with no prior summaries was selected and
built end-to-end against the live LLM:

```bash
python -u tools/rag/raptor.py --build --source "nist53:sr-2" --json
```

```json
{
  "classification": "CUI // SP-CTI",
  "dry_run": false,
  "sources_processed": 1,
  "documents_built": 1,
  "level1_created": 2,
  "level2_created": 1,
  "skipped": 0
}
```

Exit code **0**, `skipped: 0` — a clean build, not a partially-degraded one. The
persisted rows confirm every stage actually ran:

| Level | Embedding | `metadata` | Content (truncated) |
|-------|-----------|-----------|---------------------|
| 1 | present | `{"raptor_level": 1, "provenance": "llm_summary"}` | `**SR-2: Supply Chain Risk Management Plan** **Requirements:** - **a.** Develop a plan for managing …` |
| 1 | present | `{"raptor_level": 1, "provenance": "llm_summary"}` | `Supply chain risk management plans must express organizational risk tolerance, acceptable mitigation…` |
| 2 | present | `{"raptor_level": 2, "provenance": "llm_summary"}` | `**SR-2: Supply Chain Risk Management Plan** **Requirements:** Organizations must develop a lifecycl…` |

The summaries are genuine abstractive model output — the level-2 root generalizes
across its level-1 children rather than copying a chunk — and each row carries a
non-null 768-dim embedding plus the `provenance: llm_summary` TRUST marker written
at ingestion.

This establishes that the `--build` path is correct and complete per source; the
full-corpus run differs only in **how many times** that same per-source path is
executed.

## Verification

The partial index is not merely populated — it is **queryable**. `SummaryStore.search()`
with a live 768-dim local embedding of *"access control policy for multifactor
authentication"* returned coherent, correctly-ranked results:

```
score=0.7696 is_summary=True src=cmmc:IA.L2-3.5.3
score=0.7515 is_summary=True src=cmmc:IA.L2-3.5.3
score=0.6797 is_summary=True src=cmmc:IA.L3-3.5.2e
score=0.6760 is_summary=True src=cmmc:IA.L3-3.5.2e
score=0.6654 is_summary=True src=cmmc:IA.L3-3.5.1e
```

Both tiers retrieve, ranking is sensible, and every hit carries
`metadata.is_summary = True` — the TRUST tag that keeps LLM-generated summaries
from being surfaced as citation sources. **That invariant holds.**

## Defects for follow-up

| # | Defect | Impact |
|---|--------|--------|
| 1 | The summarizer's broad `except` collapses four distinct outcomes — LLM unavailable, content-guard rejection, transient provider error, oversized payload — into one anonymous `skipped` counter, with no log line, telemetry, or reason code. | A build that dropped a slice of the corpus is **indistinguishable from a clean one**; the CLI prints `skipped=N` and exits 0. `--json` carries no breakdown either. |
| 2 | A failed root (level-2) summary leaves a document with level-1 nodes but **no root**, counted only in `skipped`. Observed on at least one source (`L1=15`, `L2=0`). | Silent hierarchy corruption. |
| 3 | `--dry-run` gates only DB writes — the summarizer and embedder still execute and results are discarded. | A dry run burns the **full** LLM + embed spend (~4,198 calls each). Carried from `rce-eval-05-d2`. |
| 4 | No progress output on a serial 4,198-call run; no resume; always restarts at source #1. | Multi-hour job is unobservable and non-resumable. |
| 5 | No per-call telemetry — `ai_telemetry` has zero `rag_evaluate` rows. | Provider attribution is unreconstructable post-hoc. |

**Defect 1 is the highest-value fix**: measured sampling showed a non-trivial share
of first-party compliance-corpus sources (concentrated in NIST 800-53 / 800-171
control text) being dropped by a content guard rather than a model failure —
invisible in the output. Minimum remediation is **per-reason counters**
(`skipped_guard_rejected`, `skipped_llm_unavailable`, `skipped_error`) surfaced in
the CLI/JSON output, so an operator can see what was dropped and why. Guard-tuning
specifics are tracked internally, not here.

> Scope note: this document is a **measurement record**, not a fix. The defects
> above are follow-up work and were deliberately not remediated under this card.

## Related

* [rce-raptor-01](rce-raptor-01-summary-hierarchy.md) — summary hierarchy
* [rce-raptor-02](rce-raptor-02-multilevel-retrieval.md) — multi-level retrieval
* [rce-eval-01](rce-eval-01-retrieval-baseline.md) — retrieval-quality baseline
* [phase-rce-rag-context-engineering](phase-rce-rag-context-engineering.md) — card overview
