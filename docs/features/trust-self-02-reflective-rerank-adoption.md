# CUI // SP-CTI

# trust-self-02 — Adopt reflective reranking (Self-RAG) for the `chat_rag` surface

`tools/rag/reflective_reranker.py` (agx-rag-02, `VOCABULARY_VERSION =
"reflect-1.0"`) shipped built, unit-tested and `enabled: false`. oss-meas-01
wired it into `RAGRetriever.search()` step 5b, which moved the toggle harness
verdict from `NOT-WIRED` to `WIRED`. That closed the *reachability* half and
left the other half open: reachable, benchmarkable, and consumed by nothing in
production — this platform's signature defect wearing a healthy toggle's
verdict.

This card switches it on for one surface, makes the harness able to say so, and
records what the change actually measured.

## What shipped

### 1. The toggle is scoped to a surface, not global

```yaml
# args/rag_config.yaml
rag:
  reflective_rerank:
    enabled: true
    max_candidates: 5
    surfaces:
      - chat_rag
```

`surfaces` uses the `args/trust_gate.yaml` profile vocabulary, so the scope is
named in the same words the rest of the TRUST stack uses for the same surfaces.
`RAGRetriever.search()` takes a matching `surface=` kwarg and
`_reflective_enabled_for()` decides three cases deliberately differently:

| committed config | `surface=` | fires? |
|---|---|---|
| `enabled: false` | anything | no — unchanged |
| `enabled: true`, `surfaces` absent/empty | anything | yes (pre-scoping semantics preserved) |
| `enabled: true`, `surfaces: [chat_rag]` | `chat_rag` | yes |
| `enabled: true`, `surfaces: [chat_rag]` | `drafting` / `compliance_evidence` / `agent_output` | no |
| `enabled: true`, `surfaces: [chat_rag]` | `None` | **no** |

The last row is the load-bearing one. The cost of this feature is one cheap-tier
LLM call **per document**. Defaulting an unattributed caller into paying it is
how a scoped toggle silently becomes a global one: every retrieval site that was
never updated starts spending. Fourteen modules outside `tools/rag/` reach for
`RAGRetriever`; three call sites were updated, and every other one keeps the
previous path because it names no surface.

Adopted at the three sites the `chat_rag` profile names:

| surface | call site |
|---|---|
| Cortex ask / complete | `tools/cortex/search_service.py::search_rag` |
| `/ask-icdev` + components-map ask | `tools/dashboard/app.py::_cm_rag_search` |
| knowledge search | `tools/dashboard/app.py::api_rag_search` (`POST /api/rag/search`) |

`tools/dashboard/chat_manager.py` and `assistant_manager.py` were deliberately
NOT adopted: the profile does not name them, and widening an interactive
per-document LLM cost past what the card asked for is the caller's decision to
make, not a drive-by.

### 2. Degraded is not neutral

The reranker's neutral fallback keeps retrieval safe when the model is
unreachable — but it was *indistinguishable in the output* from a model that
genuinely judged every document `partial`. Both give every candidate 0.5, the
stable sort preserves the incoming order, and the caller reads a zero delta.
That is the laundering `toggle_harness` exists to stop, one layer down.

* `reflect_document()` returns `degraded: bool` (+ `reason` when degraded) —
  True when no axis was parsed from the model at all.
* `reflective_rerank(report={})` fills the caller's dict with
  `reflected / degraded / bounded_out / effective`.
* `RAGRetriever` labels its `retrieval_mode` `reflective_reranked` **only** when
  `effective`; otherwise it writes `reflective_degraded` and logs a warning. A
  dead capability no longer reports itself as live in the retrieval log.
* `DEGRADE_BAILOUT = 2`: after two consecutive degraded documents the whole
  reflection is abandoned and the **incoming order is returned untouched**. Two,
  not one, because one malformed response is plausibly that document while two
  in a row is the provider. Returning the incoming order rather than a partial
  ordering matters: an unjudged document sits at neutral 0.5 and would leapfrog
  a document the model actually scored below that.
* `ab_compare()` counts degraded reflections on the ranked documents and reports
  `unmeasurable_reflection_degraded` instead of
  `leave_disabled_negative_result` when nothing was judged.

### 3. The harness can now express adoption

`toggle_harness` reported `WIRED` for the entire period this toggle did nothing,
because its verdict answers *reachability* — could flipping this change
retrieval — and says nothing about whether anything flips it. Rather than
overloading that vocabulary (the module's whole thesis is refusing to collapse
distinct states into one word), each probe carries an orthogonal `adoption`:

| value | meaning |
|---|---|
| `UNADOPTED` | committed config leaves it off — no surface runs it |
| `ADOPTED-GLOBAL` | on, no surface scoping: every caller |
| `ADOPTED` | on for the surfaces it names |

Adoption is read from `args/rag_config.yaml` **on disk**, never through
`$ICDEV_RAG_CONFIG`: inside an `isolated_config()` arm every toggle is written
True or False artificially, so a sweep would otherwise report the arm it is
running as evidence that the toggle shipped on.

```
$ python tools/rag/toggle_harness.py --probe
WIRED              UNADOPTED                     rerank             imported by tools.rag.retriever
WIRED              ADOPTED         [chat_rag]    reflective_rerank  imported by tools.rag.retriever
WRAPPER-UNADOPTED  UNADOPTED                     adaptive_routing   ...
INERT-ON-BACKEND   UNADOPTED                     binary_prefilter   ...
CLI-UNSCHEDULED    UNADOPTED                     auto_indexer       ...
WIRED              UNADOPTED                     raptor             imported by tools.rag.retriever

3/6 toggles are measurable by a retrieval benchmark.
1/6 are switched on by the committed config.
```

`reflective_rerank` is the only toggle in the committed config that is on.

### 4. The retrieval log can record what happened (found while verifying)

Turning the toggle on and watching a live `chat_rag` retrieval surfaced a
pre-existing defect. `rag_retrieval_log.retrieval_mode` carries a CHECK
constraint whose committed value set is
`vector | bm25 | hybrid | rrf_hybrid | reranked`. oss-meas-01 started writing
`reflective_reranked` without widening it, and no migration in the tree adds
that value — the live board's constraint was patched out of band. Because
`_log_retrieval`'s INSERT is best-effort inside a `try/except`, the mismatch
does not raise: the row is simply dropped. Every reflectively reranked
retrieval would go unlogged, silently, on exactly the telemetry a reviewer would
consult to ask whether the feature ran. That is the swallowed-INSERT defect
CLAUDE.md describes.

Fixed here:

* Migration `20260815002727_rag_retrieval_log_reflective_modes` widens the
  constraint to include both `reflective_reranked` and the new
  `reflective_degraded`.
* `tools/db/init_icdev_db.py` and `tools/db/schema/pg_consolidated.sql` carry
  the same list, so a fresh database is not born with the old one.
* `retriever.RETRIEVAL_MODES` is the Python constant the three SQL sources are
  derived from, per the CLAUDE.md rule, and
  `test_ddl_sources_match_the_python_constant` fails if any of the four drift.
* The retriever writes the flat value `reflective_degraded` rather than
  composing `f"{retrieval_mode}+reflective_degraded"`, which would have been a
  new constraint violation of my own making.

The retrieval log now distinguishes three states: the base mode (step 5b never
ran), `reflective_reranked` (it ran and judged), and `reflective_degraded` (it
ran and judged nothing).

## The measurement

```bash
python tools/rag/rag_benchmark.py --reflective-ab --limit 12 --json
```

Retrieves **once** per golden query with every toggle off, then feeds that one
candidate set to both rankers through `reflective_reranker.ab_compare` —
baseline is the incoming order (exactly what the surface serves today),
reflective is `reflective_rerank` over the same list. Retrieving once rather
than once per arm means the measured delta is the reordering and nothing else;
vector-store nondeterminism cannot leak in. Relevance labels come from the
golden set's own `expect` targets, so no second ground truth is invented.

Record: `data/rag/trust_self_02_reflective_ab.json` (2026-08-14, golden set v2,
first 12 queries, top_k 5).

| field | value |
|---|---|
| `queries_labeled` | 12 |
| `baseline_precision_at_k` | 0.9167 |
| `reflective_precision_at_k` | 0.9167 |
| `quality_delta` | **0.0** |
| `documents_judged` | **0** |
| `documents_degraded` | 24 |
| `degraded_reason` | `ModuleBudgetExceededError: Module 'generative_intelligence' budget exceeded for function 'rag_rerank': Token cap: 418979 would exceed 400000` |
| `recommendation` | **`unmeasurable_reflection_degraded`** |

**The quality delta is 0.0 and that is not a result.** The
`generative_intelligence` module token budget
(`args/llm_config.yaml → module_budgets.per_module`, 400,000/month,
`hard_stop: true`) is exhausted for August 2026, so `LLMRouter.invoke` refuses
every `rag_rerank` call before it reaches the provider. Not one document was
judged. Before this card that run would have been recorded as
`leave_disabled_negative_result` — a DROP decision on evidence that does not
exist, which is exactly the failure mode oss-meas-01 built the toggle harness to
prevent at the wiring layer and this card closes at the reflection layer.

Two details in the record are worth reading precisely:

* `documents_degraded: 24` over 12 queries is **2 per query** — the bail-out
  working. Without it the run would have made 60 calls to learn the same thing.
* `reflective_llm_calls: 60` is the caller's *declared* cost proxy, an upper
  bound. The observed attempt count is `documents_judged + documents_degraded`.

To obtain a real quality number, re-run after the monthly budget rolls over, or
after an operator raises
`module_budgets.per_module.generative_intelligence.monthly_tokens`. The
measurement is deliberately not taken by routing around that control.

## Latency, stated rather than discovered

Reflection runs **after** the `final_top_k` truncation, so the per-query ceiling
is `final_top_k` (5) sequential cheap-tier calls, not `vector_top_k`.
`max_candidates` was lowered from a nominal 20 — unreachable from step 5b, and
misleading to anyone pricing the feature — to 5, the real ceiling. On a failure
the bail-out bounds it to 2.

That is still additive latency on an interactive path whose own trust profile
says "interactive latency budget rules out stage 2". It is a real cost, and it
is charged only to `chat_rag`. If the delta, once measurable, justifies keeping
this on, reflecting the shortlist concurrently is the obvious next lever — it is
deliberately out of scope here, since spending engineering on an optimisation
for an unmeasured benefit repeats the mistake this card is unwinding.

## Verification

```bash
pytest tests/test_reflective_reranker.py tests/rag/test_toggle_harness.py \
       tests/rag/test_reflective_surface_scoping.py -q          # 70 passed
python tools/rag/toggle_harness.py --probe                       # ADOPTED [chat_rag]
python tools/rag/rag_benchmark.py --reflective-ab --limit 12     # records the delta
```

Live against the pgvector corpus, `surface="chat_rag"` attaches a `reflection`
to each result (degraded, with the budget error as its `reason`, bailing out
after 2) and `surface="drafting"` attaches none — the scoping holds end to end,
not only in the config reader.

All three test files are gated in `args/ci_test_files/core.txt` as of this PR;
`tests/test_reflective_reranker.py` and `tests/rag/test_toggle_harness.py` were
promoted off the ungated census at the same time (`backlog_max` 1819 → 1810).
