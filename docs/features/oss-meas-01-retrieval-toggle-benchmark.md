# CUI // SP-CTI

# Retrieval Toggle Benchmark — measured results (oss-meas-01)

**Status:** complete — quality **and** latency measured; all three wired toggles decided
**Tasks:** `oss-meas-01-d1` (golden set), `-d2` (harness + latency), `-d3` (this run)
**Artefact:** [`data/rag/oss_meas_01_toggle_sweep.json`](../../data/rag/oss_meas_01_toggle_sweep.json)
**Corpus:** 4,111 `rag_chunks`, 3,830 `rag_chunk_summaries` (2 RAPTOR levels), PostgreSQL

---

## Headline

Of the five toggles this card set out to measure, **three were measurable and two are not wired to
anything** (the fifth, `auto_indexer`, is a CLI that was never a retrieval toggle).

Two things worth carrying off this page:

1. **Latency inverted two of three decisions.** On quality alone `rerank` and `raptor` looked like
   the winners and `binary_prefilter` looked inert. With cost attached, `binary_prefilter` is the
   only KEEP and the other two are DROPs.
2. **The previously-published RAPTOR *DROP* was produced by an instrument that could not detect an
   improvement** — 29 of 33 v1 queries were already at rank 1. It lands on DROP again here, but for
   a reason that holds.

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

Control latency: **p50 424.1 ms, p95 539.6 ms, mean 745.0 ms** (n=48).

| Toggle | Verdict | recall@5 | MRR | nDCG@5 | Δp50 | Δp95 | Decision |
|---|---|---|---|---|---|---|---|
| `binary_prefilter` | WIRED | 0.0000 | 0.0000 | 0.0000 | **−2.4 ms** | **−91.7 ms** | **KEEP** |
| `rerank` | WIRED | +0.0208 | +0.0104 | +0.0098 | +236.7 ms | **+2044.9 ms** | **DROP on cost** |
| `raptor` | WIRED | +0.0208 | +0.0093 | +0.0103 | +328.6 ms | +312.9 ms | **DROP on cost** |
| `reflective_rerank` | NOT-WIRED | — | — | — | — | — | wire or delete |
| `adaptive_routing` | NOT-WIRED | — | — | — | — | — | wire or delete |
| `auto_indexer` | NOT-WIRED (by design) | — | — | — | — | — | **keep — it is a CLI, not a retrieval toggle** |

### Latency inverted two of the three decisions

Read on quality alone, `rerank` and `raptor` were the winners and `binary_prefilter` was the
nothing-burger. With cost attached, that is backwards:

- **`binary_prefilter` → KEEP.** A zero quality delta is what a candidate-set prefilter is *supposed*
  to produce — identical results, less work. It returns the same answers with **mean latency down
  323.1 ms (−43%)** and the p95 down 91.7 ms. This is the only toggle on the list that is free.
- **`rerank` → DROP on cost.** One extra correct query out of 48, paid for with **p95 +2044.9 ms** —
  the tail nearly quintuples, 539.6 ms → 2584.5 ms. A cross-encoder pass over the candidate set is
  not worth a two-second tail for a 2% recall move.
- **`raptor` → DROP on cost.** Same +1 query, ~+300 ms across p50/p95/mean (roughly +75%), *plus* the
  corpus-wide summarisation build. It lands where `rce-eval-05` landed — but for a defensible
  reason, rather than because the instrument could not see a gain.

The `rerank` p95 is the number that matters most and is the one a quality-only sweep never surfaces.

### A note on the control's mean

Control mean (745.0 ms) sits **above** its p95 (539.6 ms). With n=48 the p95 is the 46th sorted
sample, so three queries are slower than it — and slow enough to drag the mean past it. That is
cold-start (first embed, connection setup), not typical cost. **Use p50/p95, not the mean**, when
comparing arms; the mean deltas above are reported for completeness and are the least trustworthy
column.

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

## Two toggles are not wired — and one only looked that way

`reflective_rerank` (agx-rag-02, 240 lines + 157 lines of tests) and `adaptive_routing`
(agx-rag-01, 252 lines, no tests) each ship a config block and **zero non-test import sites**.
Neither is a module you can run: no `__main__`, no CLI, nothing calls their public functions. They
are libraries with no consumers, fronted by config toggles that read as live capabilities.

The harness refuses to benchmark them. That is the point of the reachability probe: an unwired
toggle and a wired-but-useless toggle both measure as a zero delta, and reporting the number would
have written *"DROP — no measurable benefit"* against code that was simply never connected.

**`auto_indexer` is a different case and the NOT-WIRED label is misleading for it.** It has a full
CLI — `argparse`, `main()`, `__main__` at line 434 — and a documented manifest row
(`--index, --json`). It was never meant to be imported by the retriever; it is a standalone
index-maintenance tool, so absence from the retrieval import closure is *correct*, not a defect.
What is true is that nothing schedules it: no reflex, cron entry, or script invokes it. That is an
operations gap, not dead code, and `rag.auto_indexer.enabled` gates the tool's own behaviour rather
than anything retrieval does.

It should be dropped from the retrieval-toggle inventory entirely — it is not a retrieval toggle,
and listing it as one is what produced the "wire or delete" framing it does not deserve.

## What this run still cannot tell you

Stated plainly, because the quality deltas are small enough that these matter:

1. **Every non-zero quality delta is one query.** `+0.0208` is exactly 1/48. `rerank` and `raptor`
   each moved the **same single query** — `q-cx-admin-rights-creep`, 0.0 → 1.0 recall. They are
   almost certainly surfacing the same missing candidate, so the two are **not additive** and
   neither has been shown to generalise. `n=1` is a signal to investigate, not a mandate to ship.
   Both DROP verdicts above rest on the *cost* side, which is measured over all 48 queries and is
   therefore the sturdier half of each decision.
2. **Latency was measured on one machine, once per arm.** Unlike the quality metrics — where the
   control was verified byte-identical across three consecutive runs — the timings are a single
   sample per arm on a developer workstation sharing a box with the dashboard, the scheduler and
   PostgreSQL. The `rerank` p95 (+2044.9 ms) is far too large to be noise; the `binary_prefilter`
   p50 (−2.4 ms) is well inside it. Treat large deltas as real and small ones as unproven.
3. **The RAPTOR tree may be incomplete.** PR #763 tracks a full-corpus build at 534/2,029; this run
   saw 1,963 level-1 summaries. A finished tree could move the quality number either way — though
   it would only *increase* the build cost behind the DROP.

## Recommended next steps

- **Flip `binary_prefilter` on** (`oss-meas-01-d5`). It is the one measured free win: identical
  results, mean −43%.
- Leave `rerank` and `raptor` OFF, now on cost evidence rather than absent evidence.
- Repeat the latency arms a few times on a quiet machine before treating the smaller deltas as
  settled.
- Widen the golden set further before treating any 1-query quality delta as a KEEP.
- Decide wire-or-delete on `reflective_rerank` and `adaptive_routing`, and drop `auto_indexer` from
  the retrieval-toggle inventory — it is a CLI, not a retrieval toggle.
