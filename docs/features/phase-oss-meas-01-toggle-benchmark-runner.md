# CUI // SP-CTI

# oss-meas-01-d2 — Benchmark runner with single-toggle isolation

**Status:** shipped (runner only — the KEEP/DROP analysis is oss-meas-01-d3)
**Module:** `tools/rag/rag_benchmark.py` (mirrored to `icdev/tools/rag/`)
**Tests:** `tests/test_rag_benchmark_toggles.py` (33 tests), `tests/test_rag_benchmark.py` (13, unchanged)

## Why

The oss adaptation spike's first finding was that a meaningful slice of what
RAGFlow is admired for is **already built here and switched off**. Five
retrieval toggles ship `false`. Before adapting anything from upstream, measure
them — either we gain retrieval quality for the price of a config change, or we
learn they were correctly disabled and can say so with numbers.

The measurement only means something if a delta is attributable to **one**
toggle. Flipping several at once, or measuring against a baseline recorded on a
different corpus, produces numbers that cannot support a KEEP/DROP decision.

## What isolation means here

`run_toggle_matrix()` performs 6 runs over the golden query set:

1. an **all-off control**, then
2. one run per toggle, with that toggle forced **on** and the other four forced
   **off**.

Every toggle is written explicitly on every run rather than left at whatever
`args/rag_config.yaml` happens to say, so a run is reproducible even if someone
flips a default between runs. The control is measured in the same process on
the same corpus as the variants, so the deltas are internally comparable
regardless of corpus drift since the committed baselines were recorded.

### The toggles

| # | Toggle | What it does |
|---|---|---|
| 1 | `rag.rerank.enabled` | Cross-encoder re-ranking (BGE + LLM providers exist) |
| 2 | `rag.reflective_rerank.enabled` | Self-RAG per-document reflection |
| 3 | `rag.adaptive_routing.enabled` | Query-complexity pre-routing |
| 4 | `rag.quantization.binary_prefilter.enabled` | Binary Hamming pre-filter (perf) |
| 5 | `rag.auto_indexer.enabled` | Filesystem auto-indexing |

RAPTOR (`rag.raptor.enabled`) is deliberately **not** in the matrix — rce-eval-05-d4/d5
already measured it as a regression. It is carried in `MEASURED_REGRESSIONS` so
the writeup can cite the number instead of re-running it.

## The isolation mechanism (the non-obvious part)

The retrieval modules do **not** share one config object. `RAGRetriever` accepts
`config=`, but the vector-store factory and the quantization pre-filter each
resolve config through their own module-level loader, so a single constructor
argument reaches only the reranker. `isolated_toggle_config()` patches each
loader for the duration of one run and restores it afterwards:

- `tools.rag.retriever._load_rag_config` (also covers `adaptive_router`, which
  imports it lazily from there)
- `tools.rag.vector_store_factory._load_rag_config`
- `tools.rag.sqlite_vector_store._load_quantization_config`

Both the `tools.*` and `icdev.tools.*` module objects are patched when already
imported — they are distinct modules under the compat shim, and patching only
one leaves the other serving on-disk defaults.

The retriever is constructed **inside** the patched scope: it snapshots config
at `__init__`, so building it outside would silently measure the same
configuration five times.

## Metrics

Per run: `recall@k`, `mrr`, `ndcg@k`, `citation_hit_rate` (all reusing
`tools/rag/evaluator.py` for the ranking metrics), plus `latency` (mean / p95 /
max ms). Latency is reported under its own key rather than inside `aggregate`,
so `aggregate` stays a uniform dict of `[0,1]` scores that `compare_to_baseline`
can delta without special-casing a millisecond value. p95 is nearest-rank and
ships with its sample count — calling it "p95" over 33 queries is only honest
with the count alongside.

Ground truth: `data/rag/rce_baseline_compliance.json` and
`data/rag/rce_contextual_compliance.json` are loaded as historical reference. A
missing artifact is reported as an `error` entry rather than raised, so the
runner still works in a fresh worktree without the data payload.

## Usage

```bash
python tools/rag/rag_benchmark.py --dry-run          # list the 5 toggles; retrieves nothing
python tools/rag/rag_benchmark.py --toggle-matrix --json
python tools/rag/rag_benchmark.py --toggle-matrix --matrix-out data/rag/oss_toggle_matrix.json --json
```

`--dry-run` is the cheap pre-flight: it proves the golden set parses, the toggle
registry is intact, and the ground-truth artifacts are where the runner expects
them — none of which needs a populated vector store.

## Verification

`--dry-run` exits 0 and prints all five toggle names (the task's acceptance
criterion). A live `--toggle-matrix` run against the local corpus produced a
control of `recall@5 0.9545 / mrr 0.9545 / ndcg@5 0.9534 / citation_hit_rate
0.9697` — an exact match to the committed `rce_contextual_compliance.json`
aggregate, which is good evidence the harness is measuring the same thing the
baseline recorded.

**The numbers from that run are not a decision.** Interpreting them, and
flipping any winners on in `args/rag_config.yaml`, is oss-meas-01-d3.
