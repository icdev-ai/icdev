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
| rce-xcut-01 | (this) | Cross-cutting docs, manifest, skip-decision ADRs, coherence/companion sync. |

## Toggles (all default OFF except the float16 storage win)

`args/rag_config.yaml`:

```yaml
rag:
  contextual_retrieval:
    enabled: false        # rce-ctx: prepend LLM context prefix before embedding
  quantization:
    sqlite_dtype: float16 # rce-quant-01: 16-bit SQLite vectors (float32 back-compat)
    binary_prefilter:
      enabled: false      # rce-quant-02: Hamming pre-filter then cosine re-rank
  raptor:
    enabled: false        # rce-raptor: search summary tiers + dedup with leaves
```

- **contextual_retrieval** and **raptor** need an LLM at ingestion/build time;
  both no-op gracefully when the provider is unavailable (air-gap).
- **sqlite_dtype: float16** is the one behavior change that ships ON — reads are
  back-compat for legacy float32 and headered float16, so no re-index is forced.
- **binary_prefilter** is OFF pending per-corpus recall validation (random
  Gaussian embeddings are its documented worst case).

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

**Status: NOT YET MEASURED against the new baseline. Both remain default-OFF.**

This is a deliberate, recorded gap rather than an ambiguous one. Sizing done in
this task, against the real 3552-chunk corpus:

| Toggle | Build command | Measured cost to enable |
|---|---|---|
| `contextual_retrieval` | `python tools/rag/reindex_contextual.py --reindex --execute` | **3552 chunks × (1 LLM prefix call + 1 re-embed)** — `--dry-run` reports `total_chunks: 3552, documents: 2005`. Neither the CLI nor the reindexer exposes a `--limit`; `--source` only narrows to a `source_type`, and all 3552 share one. |
| `raptor` | `python tools/rag/raptor.py --build` | **Exceeds 240 s in `--dry-run` alone** (plan-only, no writes) over this corpus, so the live clustering + summary pass is substantially longer. |

Each build exceeds a single autonomous dispatch budget, and a *partial* reindex
is worse than none: it leaves the store with a mix of contextualized and
non-contextualized embeddings, which makes any before/after delta
uninterpretable.

**Interim decision for both: stay OFF, and do not treat them as shippable.**
The cost/benefit is now bounded by a number rather than by a guess — each toggle
requires a multi-thousand-call LLM pass over the corpus to chase **at most
+6.1 pp recall@5**, against a retriever already at 0.94/0.97. That is a poor
trade unless the measurement shows the gain lands specifically on the three
under-served queries above.

**To close this out** (the remaining work, deliberately split because each needs
its own long-running dispatch against the live environment):

```bash
# 1. contextual_retrieval
python tools/rag/reindex_contextual.py --reindex --execute
#    then set rag.contextual_retrieval.enabled: true in args/rag_config.yaml
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline_compliance.json --json

# 2. raptor
python tools/rag/raptor.py --build
#    then set rag.raptor.enabled: true in args/rag_config.yaml
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline_compliance.json --json
```

Record the LLM provider actually used for the build — both toggles depend on
generation quality at ingestion/build time, and a small local model may
under-perform in a way that is a property of the model, not of the technique.
Neither default may be flipped to ON without a number in this document.

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
