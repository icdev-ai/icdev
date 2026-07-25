# Phase RCE — RAG Context Engineering

**Classification:** CUI // SP-CTI
**Card:** RCE (RAG Context Engineering)
**Status:** Shipped (rce-gate-00 held for manual release)
**Source analysis:** `C:\AI\searches\archive\rag_alt.md`

## Thesis

The ICDEV™ RAG stack is already advanced (pgvector HNSW + tsvector hybrid RRF,
two-stage BGE/qwen3 reranking, GraphRAG, citation/provenance, dual PG↔SQLite
backends). The next gains come from **evolving the existing pipeline** —
better chunk context, a summary hierarchy, and cheaper vectors — **not** from
swapping backends. This card is "context engineering," per Jeff Huber's framing:
retrieval is largely solved; the leverage is in *what goes in the context box*.

Every change is **measured** against a committed baseline (rce-eval-01), is
**opt-in / default-OFF**, is **pure-Python and air-gap safe**, and preserves the
TRUST invariants (citations resolve to original leaf chunks; provenance
persisted).

## What shipped

| Task | PR | What |
|------|----|------|
| rce-eval-01 | #658 | Retrieval-quality baseline harness — golden query set + `rag_benchmark.py` (recall@k, MRR, citation-hit-rate, ndcg; reuses `evaluator.mrr`/`ndcg_at_k`), committed `data/rag/rce_baseline.json`. |
| rce-ctx-01 | #664 | Contextual retrieval — ~50-100 token LLM context prefix per chunk at ingestion; embed contextualized text, cite/store original; `VectorChunk.text_for_embedding()`. |
| rce-ctx-02 | #670 | Contextual re-index CLI (`reindex_contextual.py`) + measure-vs-baseline wiring. |
| rce-quant-01 | #666 | float16 vector packing (self-describing `RVQ1` header, float32 back-compat, fixes latent headerless-float16 `migrate_tier` bug). Default `sqlite_dtype: float16` → ~48% DB shrink. |
| rce-quant-02 | #674 | Optional binary quantization + Hamming pre-filter (`sign_bits` column), default OFF, cosine re-rank of candidates. |
| rce-raptor-01 | #675 | RAPTOR summary hierarchy — `rag_chunk_summaries` table + `raptor.py` tree builder (cheap-LLM summaries, graceful no-op). |
| rce-raptor-02 | #679 | Multi-level retrieval + lineage dedup in `RAGRetriever.search`. |
| rce-eval-02 | #663 | SPIKE — domain-adapted embedding feasibility: **NO-GO / DEFER** + `embedding_feasibility.py` re-runnable probe. |
| rce-xcut-01 | #689 | Cross-cutting docs, manifest, skip-decision ADRs, coherence/companion sync. |
| rce-eval-03 | #743 | Re-baseline on a matched 3552-chunk compliance corpus — `data/rag/rce_baseline_compliance.json`. |
| rce-eval-04-d3 | #762 | Contextual re-index of all 3552 chunks (+ `embedding_vec` write fix); flipped `contextual_retrieval.enabled → true`. |
| rce-eval-04-d4 | #764 | Benchmark comparison vs the compliance baseline — `data/rag/rce_contextual_compliance.json`. |
| rce-eval-04-d5 | (this) | **KEEP decision** for `contextual_retrieval`, backed by the measured delta + per-query analysis below. |
| rce-eval-05 | #760 | RAPTOR live tree build (rce-eval-05-d3). |
| rce-eval-05-d4/d5 | (this) | **DROP decision** for `raptor` — measured 0.0 recall / 0.0 MRR / −0.0005 nDCG vs OFF; toggle stays OFF. |

## Toggles

`args/rag_config.yaml`:

```yaml
rag:
  contextual_retrieval:
    enabled: true         # rce-ctx: prepend LLM context prefix before embedding
                          #   ON since rce-eval-04 — KEEP, measured (see below)
  quantization:
    sqlite_dtype: float16 # rce-quant-01: 16-bit SQLite vectors (float32 back-compat)
    binary_prefilter:
      enabled: false      # rce-quant-02: Hamming pre-filter then cosine re-rank
  raptor:
    enabled: false        # rce-raptor: search summary tiers + dedup with leaves
```

- **contextual_retrieval** shipped OFF, was measured in rce-eval-04, and is now
  **ON** — the only toggle on this card to have earned a flip with a number
  behind it. It still needs an LLM at ingestion time and no-ops gracefully when
  the provider is unavailable (air-gap).
- **sqlite_dtype: float16** ships ON — reads are back-compat for legacy float32
  and headered float16, so no re-index is forced.
- **binary_prefilter** is OFF pending per-corpus recall validation (random
  Gaussian embeddings are its documented worst case).
- **raptor** was built and then measured (rce-eval-05-d4): the retrieval toggle
  stays OFF permanently — **DROP**, 0.0 recall/MRR gain vs OFF (see below).

## Benchmark deltas

### The original baseline measured the wrong corpus (rce-eval-01)

Baseline (`data/rag/rce_baseline.json`), generated through the full retriever
against the then-live 1397-chunk corpus: **recall@5 = 0.12, MRR = 0.20,
citation_hit_rate = 0.24**.

Those numbers were a deliberate **low-water-mark**: that corpus was
research/innovation-heavy and held ~0 NIST/compliance chunks, while the golden
set targets the compliance product. The corpus and the query set were measuring
different things, so no per-change delta taken against it was meaningful.

### Re-baselined on a matched compliance corpus (rce-eval-03)

A compliance corpus was ingested into `rag_chunks` and the harness re-run
unchanged. New baseline: **`data/rag/rce_baseline_compliance.json`**.
`rce_baseline.json` is deliberately kept so the corpus-mismatch story stays
legible.

| Metric | `rce_baseline.json` (mismatched corpus) | `rce_baseline_compliance.json` (matched corpus) | Δ |
|---|---|---|---|
| recall@5 | 0.1212 | **0.9394** | +0.8182 |
| MRR | 0.2045 | **0.9343** | +0.7298 |
| ndcg@5 | 0.1999 | **0.9429** | +0.7430 |
| citation_hit_rate | 0.2424 | **0.9697** | +0.7273 |

Same 33 golden queries, same `top_k=5`, same retriever, same metric code
(`tools/rag/evaluator.py` `mrr` / `ndcg_at_k`). The only variable changed is the
corpus. **The RCE retrieval stack was never underperforming — it was being
scored against documents that did not contain the answers.**

#### Corpus composition (`source_type = 'compliance_reference'`, 3552 chunks)

Live PG (`ICDEV_STORAGE_BACKEND=postgresql`), ingested 2026-07-25, all 3552
chunks embedded (`embedding` and `embedding_vec` both populated):

| Regime | Source document | Sources | Chunks |
|---|---|---|---|
| NIST_800_53 | Electronic (OSCAL) Version of NIST SP 800-53 Rev 5.2.0 Controls + SP 800-53A Rev 5.2.0 Assessment Procedures | 1196 | 2523 |
| FEDRAMP | FedRAMP High Baseline Controls | 336 | 368 |
| CMMC | CMMC Model v2.0 — Practice Catalog | 150 | 205 |
| NIST_800_171 | NIST SP 800-171 Rev 2 — CUI Protection Requirements | 110 | 204 |
| FIPS | NIST SP 800-60 Vol 2 Rev 1 Information Type Catalog | 131 | 132 |
| STIG | Web Application Security STIG | 15 | 45 |
| FEDRAMP | FedRAMP 20x Key Security Indicators (KSIs) | 43 | 43 |
| FIPS | FIPS 200 Minimum Security Requirements | 17 | 20 |
| DOD_IL | DoD Impact Level Profiles | 4 | 6 |
| CUI | ICDEV CUI marking templates (32 CFR Part 2002 / DoDI 5200.48) | 3 | 6 |
| **Total** | | **2005** | **3552** |

Total `rag_chunks` at benchmark time: **4111** (3552 compliance_reference +
559 pre-existing `dic_document`).

#### Headroom is now the binding constraint

Per-query breakdown of the new baseline: **30 of 33 queries score a perfect
recall@5 = 1.0**. Only 4 of 66 individual targets are missed:

- `q-stig-hardening` — full miss (0/2 targets); the STIG slice is the thinnest
  in the corpus (45 chunks, one STIG document).
- `q-sc13-cryptographic-protection` — 1/2 targets.
- `q-fedramp-authorization-boundary` — 1/2 targets.

Maximum remaining gain for *any* retrieval change is therefore **+0.0606
recall@5 and +0.0303 citation_hit_rate**. This ceiling is the dominant fact for
the two dark toggles below.

Storage delta (rce-quant-01, measured): 768-dim × 2000 vectors → SQLite DB
**8.50 MB → 4.39 MB (−48%)**, query latency within noise.

### Toggle decisions: `contextual_retrieval` and `raptor`

**Status: `contextual_retrieval` is MEASURED, decided **KEEP**, and is ON
(measured rce-eval-04-d4, decided rce-eval-04-d5). `raptor` is now also
MEASURED (rce-eval-05-d4) and decided **DROP / keep-OFF** — the retrieval
toggle stays `false`.**

The sizing below was done before either build, against the real 3552-chunk
corpus, and is kept because it is what set the cost expectation:

| Toggle | Build command | Measured cost to enable |
|---|---|---|
| `contextual_retrieval` | `python tools/rag/reindex_contextual.py --reindex --execute` | **3552 chunks × (1 LLM prefix call + 1 re-embed)** — `--dry-run` reports `total_chunks: 3552, documents: 2005`. Neither the CLI nor the reindexer exposes a `--limit`; `--source` only narrows to a `source_type`, and all 3552 share one. |
| `raptor` | `python tools/rag/raptor.py --build` | **Exceeds 240 s in `--dry-run` alone** (plan-only, no writes) over this corpus, so the live clustering + summary pass is substantially longer. |

Each build exceeds a single autonomous dispatch budget, and a *partial* reindex
is worse than none: it leaves the store with a mix of contextualized and
non-contextualized embeddings, which makes any before/after delta
uninterpretable.

The interim decision was to keep both OFF until each had a number. That number
now exists for `contextual_retrieval`.

#### `contextual_retrieval`: MEASURED → **KEEP** (rce-eval-04-d5)

> **Decision: KEEP.** `rag.contextual_retrieval.enabled` stays `true` in
> `args/rag_config.yaml`, backed by a measured **+0.0151 recall@5 / +0.0202 MRR
> / +0.0105 nDCG@5** on the matched compliance corpus, with two of the three
> recall-limited golden queries fully recovered and no aggregate regression.

Config change commit: **[`1cdcf855c`](https://github.com/icdev-ai/icdev/commit/1cdcf855c4996ca10cf7d125bc6c70f73837c8fb)**
— `fix(rce-eval-04-d3): write embedding_vec on re-index + enable contextual retrieval`,
merged to `main` via **PR #762**. It flipped `args/rag_config.yaml:123`
`enabled: false → true` (full SHA
`1cdcf855c4996ca10cf7d125bc6c70f73837c8fb`). The flip therefore *preceded* the
measurement — d3 enabled it so the re-index would produce contextual
embeddings — and this record is what retroactively justifies it. rce-eval-04-d5
corrected the then-stale `# master toggle (default OFF)` comment on that line
(both `args/` and `icdev/data/args/` copies) and wrote this decision.

Full record: [rce-eval-04-contextual-benchmark.md](rce-eval-04-contextual-benchmark.md).
Re-index run record: [rce-eval-04-contextual-reindex-run.md](rce-eval-04-contextual-reindex-run.md).
After-run artifact: `data/rag/rce_contextual_compliance.json`.

##### Aggregate before/after (4 metrics)

| Metric | Before — `rce_baseline_compliance.json` | After — `rce_contextual_compliance.json` | Δ |
|--------|----------------------------------------:|-----------------------------------------:|---:|
| `recall_at_5` | 0.9394 | **0.9545** | **+0.0151** |
| `mrr` | 0.9343 | **0.9545** | **+0.0202** |
| `ndcg_at_5` | 0.9429 | **0.9534** | **+0.0105** |
| `citation_hit_rate` | 0.9697 | 0.9697 | 0.0000 |

Same 33 golden queries, same `top_k=5`, same embedding model on both sides
(`nomic-embed-text`, 768-dim). Deltas are the harness's own
`comparison.deltas` block, not recomputed by hand.

##### Per-query delta — the 3 target queries

The three queries the rce-eval-03 headroom analysis identified as the *only*
places a retrieval change could gain anything (every other query was already at
recall@5 = 1.0):

| Target query | recall@5 | MRR | nDCG@5 | targets hit | Outcome |
|---|---:|---:|---:|---:|---|
| `q-sc13-cryptographic-protection` | 0.5 → **1.0** (+0.5) | 0.3333 → **0.5** (+0.1667) | 0.5706 → **0.7328** (+0.1622) | 1/2 → **2/2** | **Recovered** |
| `q-fedramp-authorization-boundary` | 0.5 → **1.0** (+0.5) | 1.0 → 1.0 (0) | 1.0 → 1.0 (0) | 1/2 → **2/2** | **Recovered** |
| `q-stig-hardening` | 0.0 → 0.0 (0) | 0.0 → 0.0 (0) | 0.0 → 0.0 (0) | 0/2 → 0/2 | Unwinnable — golden-set defect |

`q-sc13`: `fedramp:FRM-H-SC-13` (the one chunk carrying both `SC-13` and
`cryptographic protection`) enters at rank 4 on a *heuristic* family-tag prefix,
so that win is the prefix's structure, not LLM prose. `q-fedramp`: `nist53:sc-7`
enters at rank 5 with `authorization boundary` in-body; MRR/nDCG stay 1.0
because rank 1 already matched. `q-stig-hardening`: **zero** of 3552 chunks
contain both `STIG` and `hardening`, and `score_query` matches `substring`
targets against `content` only — never the prefix or `source_id` — so no index
change can satisfy it. Fixing the golden set, not the index, is the next step.

Two off-target movements bound the decision honestly:

- **Gain:** `q-ac2-account-mgmt` MRR 0.5 → **1.0**, nDCG 0.6934 → **0.9829**
  (both targets already found; the LLM prefix promoted the match to rank 1) —
  the run's largest single improvement.
- **Regression:** `q-cmmc-cui-protection` recall 1.0 → **0.5**, targets 2/2 →
  1/2. Prefix homogenisation ("This chunk presents the complete CMMC Level 2 …"
  on every sibling) crowds out the single chunk spelling out "controlled
  unclassified information". This is the one recall loss in the run and is the
  same substring literalism that makes `q-stig-hardening` unwinnable.

12 of 33 queries moved at least one metric — confirming the benchmark read the
contextual index rather than a stale one, since a no-op would have produced
exactly zero movement everywhere.

##### Why KEEP and not DROP

+1.5 pp recall@5 against a bounded ceiling of +6.1 pp is a real but modest
return on a 3552-call LLM pass. KEEP rests on three things: the aggregate delta
is positive on 3 of 4 metrics and negative on none; 2 of the 3 addressable
queries went to full recall; and the LLM pass is already paid for, so the
marginal cost of leaving it ON is zero. The single recall regression
(`q-cmmc-cui-protection`) is outweighed by the two recoveries and is a scoring
artifact of literal-substring targets.

The corollary is a cost caveat, not a reversal: **this result would not justify
a second 3552-call pass on a comparable corpus.** Control text already carries a
`control_id: … title: …` header supplying most of what a prefix adds, and 404
chunks fell back to the heuristic anyway. Re-enabling on a *new* corpus should
be re-measured, not assumed.

**Prefix-generation provider** (recorded per the rule below): router function
`rag_evaluate` → `ollama_cloud` / **`kimi-k2.6:cloud`**, prompt `ctx-v1`;
3148 chunks LLM-generated, 404 heuristic fallback. Retrieval embeddings on both
sides of the comparison: `nomic-embed-text` (768-dim) via
`OllamaEmbeddingProvider`. Note that `context_provenance` does **not** persist
the model id, and `ollama_cloud` is a cloud egress path — fine for this public
reference corpus, but a CUI corpus must pin the local provider before
re-indexing.

#### `raptor`: MEASURED → **DROP / keep-OFF** (rce-eval-05-d4/d5)

> **Decision: DROP.** `rag.raptor.enabled` stays `false` in
> `args/rag_config.yaml`. Enabling the RAPTOR summary tier produced
> **+0.0 recall@5 / +0.0 MRR / −0.0005 nDCG@5** vs OFF on the compliance
> golden set — i.e. no retrieval benefit and a fractional ranking regression —
> against a real per-build cost (a 3830-row LLM-summarized tier plus per-query
> summary search). An always-OFF, always-cost toggle is dead weight; it is
> dropped rather than left ambiguous.

The tree was built live (rce-eval-05-d3) — 4111 `rag_chunks`, 3830
`rag_chunk_summaries`, `max_levels: 2`; summarization served by **Ollama Cloud
(`kimi-k2.6:cloud`)**, embeddings by the local Ollama provider
([rce-eval-05-raptor-live-build.md](rce-eval-05-raptor-live-build.md)). The
d4 measurement ran the golden set through `RAGRetriever` back-to-back, same
corpus and embedder, toggling only `rag.raptor.enabled`:

| Metric | raptor OFF | raptor ON | Δ (ON−OFF) |
|---|---|---|---|
| recall@5 | 0.9545 | 0.9545 | **+0.0000** |
| MRR | 0.9545 | 0.9545 | **+0.0000** |
| nDCG@5 | 0.9534 | 0.9529 | **−0.0005** |
| citation_hit_rate | 0.9697 | 0.9697 | +0.0000 |

Target queries the summary tier was expected to help were **byte-identical**
ON vs OFF: `q-stig-hardening` stayed at **0.0 recall** (raptor did not rescue
the one failing target), `q-sc13-cryptographic-protection` and
`q-fedramp-authorization-boundary` were unchanged. Only 3 of 33 queries moved
at all, nDCG-only, and net roughly neutral. Full numbers:
[`data/rag/rce_eval05_raptor_results.json`](../../data/rag/rce_eval05_raptor_results.json).

The +0.015 recall the ON run shows *versus the committed
`rce_baseline_compliance.json` (0.9394)* is **corpus growth, not raptor** — the
OFF-now run already reads 0.9545 on the grown corpus. Attributing that delta to
raptor would be the corpus-mismatch error this phase was created to avoid.

Interpretation: the flat-leaf baseline is already near-perfect on this golden
set (30/33 queries at perfect recall), so there is almost no headroom a
summary-abstraction tier can capture; `contextual_retrieval` already took the
one recoverable slice. RAPTOR's value shows up on multi-hop / thematic
questions over long narrative corpora, which this compliance-clause corpus is
not. No default is flipped; the toggle remains `false`.

## Evaluated and SKIPPED (see ADRs D-RCE-*)

- **TurboQuant** — no pgvector integration; HNSW already covers ANN. Skip.
- **Turbopuffer** — cloud-only, no local/embedded mode; breaks the SQLite
  air-gap fallback. Skip.
- **Qdrant** — has a local mode but adds a Rust dependency, breaking the
  pure-Python + SQL fallback philosophy. Deferred (revisit only if the SQLite
  fallback becomes a measured bottleneck).
- **Domain-adapted embedding fine-tune** — NO-GO / DEFER (rce-eval-02): no
  in-domain training data yet; re-evaluate after compliance-corpus ingestion.

## Guardrails honored

Pure-Python / air-gap safe (no new heavy deps, no npm). New behavior default-OFF.
`rag_chunk_summaries` carries `tenant_id` + `classification` (RLS parity) and is
**not** append-only. `sign_bits` is a nullable, back-compat column. All `tools/`
changes mirrored to `icdev/tools/`. New tools registered in
`tools/manifest/rag-subsystem.md`.
