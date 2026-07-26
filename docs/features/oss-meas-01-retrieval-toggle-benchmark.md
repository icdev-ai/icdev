# CUI // SP-CTI

# Retrieval Toggle Benchmark — measured results (oss-meas-01)

**Status:** measurement complete; two decisions deferred pending latency instrumentation
**Tasks:** `oss-meas-01-d1` (golden set), `-d2` (harness), `-d3` (this run)
**Artefact:** [`data/rag/oss_meas_01_toggle_sweep.json`](../../data/rag/oss_meas_01_toggle_sweep.json)
**Corpus:** 4,111 `rag_chunks`, 3,830 `rag_chunk_summaries` (2 RAPTOR levels), PostgreSQL

---

## Headline

Of the five toggles this card set out to measure, **two were measurable, three are not wired to
anything.** And the one previously-published verdict on this surface — RAPTOR: *DROP* — was
produced by an instrument that could not have detected an improvement.

## Method

`python tools/rag/rag_benchmark.py --sweep`. One all-off control plus one isolated arm per toggle,
with **every** toggle written explicitly on every arm so no ambient config state enters a result.
The control was run three times consecutively and produced byte-identical aggregates, so the deltas
below are real rather than retrieval nondeterminism.

Control (all toggles off), 48 queries:

| recall@5 | MRR | nDCG@5 | citation_hit_rate |
|---|---|---|---|
| 0.7431 | 0.7292 | 0.7639 | 0.8542 |

## Results

| Toggle | Verdict | recall@5 | MRR | nDCG@5 | citation | Decision |
|---|---|---|---|---|---|---|
| `rerank` | WIRED | +0.0208 | +0.0104 | +0.0098 | +0.0208 | **KEEP (weak)** |
| `raptor` | WIRED | +0.0208 | +0.0093 | +0.0103 | +0.0208 | **REVISIT — prior DROP is unsafe** |
| `binary_prefilter` | WIRED | 0.0000 | 0.0000 | 0.0000 | 0.0000 | **DEFERRED — needs latency** |
| `reflective_rerank` | NOT-WIRED | — | — | — | — | **wire or delete** |
| `adaptive_routing` | NOT-WIRED | — | — | — | — | **wire or delete** |
| `auto_indexer` | NOT-WIRED | — | — | — | — | **wire or delete** |

## The prior RAPTOR verdict does not survive re-measurement

`data/rag/rce_eval05_raptor_results.json` recorded `"decision": "DROP / keep OFF"` from
`delta_on_minus_off` of `recall 0.0, MRR 0.0, nDCG -0.0005`.

That run used the **same corpus** — its own `environment` block records 4,111 chunks, 3,830
summaries, 2 levels, identical to this one. The only thing that changed is the golden query set.

Its control sat at **recall@5 0.9545**. Against the v1 33-query set, **29 of 33 queries were already
at both perfect recall and perfect MRR** — the correct chunk was already rank 1. There were four
queries' worth of headroom in the entire instrument. A retrieval improvement had almost nowhere to
show up, so measuring `0.0` was close to the only available outcome.

On the v2 48-query set (control recall@5 **0.7431**, real headroom), the same toggle on the same
corpus measures **+0.0208 recall@5 / +0.0093 MRR / +0.0103 nDCG@5**. Same direction as `rerank`.

**This does not establish that RAPTOR is good.** It establishes that the evidence behind the
existing DROP is void, and that the decision needs to be retaken with a latency number attached —
RAPTOR's cost is a summarisation pass over the corpus, which the quality delta alone cannot justify.

The general lesson is the one `oss-meas-01-d1` ran into twice: **a golden set with no headroom
cannot produce a KEEP decision, only a DROP.** Any prior verdict measured against the v1 set is
suspect in the same way.

## Three toggles are not wired

`reflective_rerank` (agx-rag-02), `adaptive_routing` (agx-rag-01) and `auto_indexer` each ship a
config block, a test file, and **zero non-test import sites**. They are not in the import closure of
`tools/rag/retriever.py`.

The harness refuses to benchmark them. This is the point of the reachability probe: an unwired
toggle and a wired-but-useless toggle both measure as a zero delta, and reporting the number would
have written *"DROP — no measurable benefit"* against roughly 1,500 lines of code that was simply
never connected. `auto_indexer` is additionally ingest-side and could not move a retrieval metric
even once wired.

## What this run cannot tell you

Stated plainly, because the deltas are small enough that these matter:

1. **No latency was measured.** The task asked for "latency delta" and the harness does not collect
   it. This is why `binary_prefilter` gets no decision: it is a *speed* optimisation, so its
   `0.0000` quality delta across all four metrics is the **desired** outcome, not a failure — it
   shrinks the candidate set without changing what comes back. Judging it requires the number that
   was not taken.
2. **Every non-zero delta is one query.** `+0.0208` is exactly 1/48. `rerank` and `raptor` each
   moved the **same single query** — `q-cx-admin-rights-creep`, 0.0 → 1.0 recall. They are almost
   certainly surfacing the same missing candidate, so the two are **not additive** and neither has
   been shown to generalise. `n=1` is a signal to investigate, not a mandate to ship.
3. **The RAPTOR tree may be incomplete.** PR #763 tracks a full-corpus build at 534/2,029; this run
   saw 1,963 level-1 summaries. A finished tree could move the number in either direction.

## Recommended next steps

- Add latency capture to the sweep, then retake `binary_prefilter` and `raptor`.
- Widen the golden set further before treating a 1-query delta as a KEEP.
- Decide wire-or-delete on the three unwired modules. Nothing currently forces that call, and they
  will keep appearing in toggle inventories as if they were live capabilities.
