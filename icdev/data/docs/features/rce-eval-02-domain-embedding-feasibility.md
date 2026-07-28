# RCE — Domain-Adapted Embedding Model Feasibility (rce-eval-02)

**Classification:** CUI // SP-CTI
**Card:** RCE (RAG Context Engineering)
**Type:** SPIKE — decision record (go/no-go), plus a small dependency-free feasibility probe
**Status:** Shipped
**Verdict:** **NO-GO / DEFER** — favor contextual retrieval + RAPTOR + reranking and compliance-corpus ingestion first; re-evaluate after ingestion.

---

## 1. The question

The source analysis (`C:\AI\searches\archive\rag_alt.md`, §3 "Fine-Tune Your Embedding
Model") rates a domain-adapted embedding model **High impact / Medium effort** and
recommends: *"Use `sentence-transformers` + existing `rag_chunks` table to train a
domain-adapted embedding model. Swap the model ID in `args/rag_config.yaml`."*

Two questions for this spike:

1. **Is a domain-adapted (fine-tuned or stronger off-the-shelf) embedding model worth
   adopting for ICDEV's compliance/NIST corpus right now?**
2. **How would it plug in** — given this codebase does *not* embed via a hardcoded
   Ollama/nomic call, but through a provider abstraction?

## 2. Premise correction — the source doc is partly stale

`rag_alt.md` assumes *"Currently using `nomic-embed-text` via Ollama"* and proposes
swapping the model id in `args/rag_config.yaml`. In **this** codebase that is inaccurate
in two load-bearing ways:

- Embeddings are obtained through a **provider abstraction**, not a hardcoded Ollama
  call: `from tools.llm import get_embedding_provider` →
  `LLMRouter.get_embedding_provider()` (`tools/llm/__init__.py:53`,
  `tools/llm/router.py:2585`). Providers implement the `EmbeddingProvider` ABC
  (`tools/llm/provider.py:160`): `provider_name`, `dimensions`, `embed(text)`,
  `embed_batch(texts)`, `check_availability()`.
- The model id / dims are configured in the **`embeddings:` section of
  `args/llm_config.yaml`** (not `rag_config.yaml`). `nomic-embed-text` is only *one*
  fallback in the chain, not the default:

  ```yaml
  # args/llm_config.yaml (line ~1199)
  embeddings:
    default_chain: [openai-embed, gemini-embed, nomic-embed-local]
    models:
      openai-embed:      { provider: openai,  model_id: text-embedding-3-small, dimensions: 1536 }
      gemini-embed:      { provider: gemini,  model_id: text-embedding-004,      dimensions: 768  }
      titan-embed:       { provider: bedrock, model_id: amazon.titan-embed-text-v2:0, dimensions: 1024 }
      nomic-embed-local: { provider: ollama,  model_id: nomic-embed-text,        dimensions: 768  }
      # + azure-embed (1536), oci-embed (1024), ibm-slate-embed (768)
  ```

  `args/rag_config.yaml` only pins **dims=768** and batch size for the RAG pipeline
  (`embedding: { dimensions: 768, batch_size: 20 }`).

**Consequence:** swapping the embedding model is a **config / provider change behind the
abstraction**, not a code fork. A domain-adapted model must be evaluated as *a new
provider registered in the `embeddings:` chain* — never as a hardcoded Ollama edit.

## 3. Current state — how embeddings are obtained today

| Concern | Reality |
|---------|---------|
| Entry point | `get_embedding_provider()` → `LLMRouter.get_embedding_provider()` picks the first available model in `default_chain` |
| Interface a new model must satisfy | `EmbeddingProvider` ABC: `provider_name`, `dimensions`, `embed`, `embed_batch`, `check_availability` |
| Configured model id / dims | `args/llm_config.yaml` `embeddings:` block; RAG pipeline pins **dims=768** (`args/rag_config.yaml`) |
| Vector store | `data/rag/rag_vectors.db` (SQLite fallback) / PG-primary `rag_chunks`; stored vectors are fixed-width — changing dims forces a **full re-index** |
| Retrieval quality is now measurable | `tools/rag/rag_benchmark.py` (rce-eval-01) against golden set `args/rag/golden_query_set.yaml`, baseline `data/rag/rce_baseline.json` |

Because everything runs through the ABC, **option (b) — a stronger off-the-shelf model —
is a one-line config change** (add a model to the `embeddings:` chain). Option (a) —
self-hosted fine-tune — is the only path that adds a new provider *and* heavy deps.

## 4. Options considered

### (a) Fine-tune sentence-transformers on `rag_chunks` (self-hosted, domain-adapted)
- **Plug-in shape:** a new `SentenceTransformerEmbeddingProvider(EmbeddingProvider)`
  wrapping the fine-tuned checkpoint, registered as e.g. `st-compliance-local` in the
  `embeddings:` chain. Clean against the ABC — but note the 7 existing providers
  (`tools/llm/embedding_provider.py`: OpenAI, Bedrock/Titan, Gemini, Azure, OCI/Cohere,
  IBM watsonx, Ollama) are all **API-client wrappers**; there is currently **no
  sentence-transformers / local-weights provider in the abstraction**, so option (a)
  means introducing an entirely new provider *class and dependency*, not reusing one.
- **Cost:** adds **torch + sentence-transformers (~2–3 GB)** — heavy deps, *off by
  default*. Air-gap distribution must ship framework **and** model weights, pinned and
  hash-verified. Introduces a **retrain treadmill**: every material corpus change →
  re-mine pairs, retrain, re-embed the whole store (dims must stay 768 or full re-index).
- **Prerequisite:** a labeled in-domain **(query → positive passage)** pair set mined
  from compliance chunks. **This is the blocker — see §5.**

### (b) Stronger off-the-shelf embedding model via the existing provider
- **Plug-in shape:** config-only. Add / reprioritize a retrieval-tuned model already
  reachable through a provider (e.g. a larger `text-embedding-3-*`, a 1024-d Titan/Cohere,
  or a local retrieval model). No new code, no training.
- **Cost:** dims change (e.g. 1536) → **full re-index** and a `rag_config.yaml` dims bump;
  cloud models conflict with strict air-gap unless a local retrieval model is used.
- **Effort/lift:** lowest effort; measurable in an afternoon via `rag_benchmark --compare`.
  This is the correct **first** experiment if embedding quality is ever the bottleneck.

### (c) Do nothing on embeddings — lean on the rest of the RCE card first
- Contextual-retrieval prefixes (rce-ctx), RAPTOR summary hierarchy (rce-raptor), and the
  two-stage reranker already in the pipeline (`tools/rag/reranker_provider.py`) attack the
  same recall/precision target at **far lower cost and zero new deps**, and `rag_alt.md`
  itself rates contextual retrieval "✅ Do this / Low effort / 67% failure reduction."
- Plus the real lever for the compliance golden set: **ingest the NIST/compliance corpus**
  (see §5) — no model change moves a metric on content that isn't in the store.

## 5. Cost/benefit — the decisive evidence

### Training-data availability — the corpus is empty of the target domain
The whole premise of a *domain-adapted compliance* embedding model is that there is
compliance text to adapt to. There is not. Measured directly against the committed RCE
vector store (`tools/rag/embedding_feasibility.py`, this card):

| source_type | chunks |
|-------------|-------:|
| research_challenges | 1259 |
| innovation_signals | 83 |
| creative_pain_points | 26 |
| research_dossiers | 25 |
| creative_feature_gaps | 3 |
| creative_specs | 1 |
| **compliance / NIST (any)** | **0** |
| **total** | **1397** |

**Eligible (in-domain compliance) chunks = 0 / 1397 (0.0%).** There are **no in-domain
positives to mine** — you cannot fine-tune a domain-adapted compliance embedding model on
a corpus with zero compliance chunks. (The separate `data/icdev.db` `rag_chunks` holds 559
`dic_document` chunks, but those are not in the benchmarked RCE store and are document-
generic, not a NIST training set.)

### The low baseline is a corpus gap, not an embedding-quality gap
The rce-eval-01 baseline over 33 NIST/compliance golden queries:

| Metric | Baseline |
|--------|---------:|
| recall@5 | 0.12 |
| MRR | 0.20 |
| ndcg@5 | 0.20 |
| citation_hit_rate | 0.24 |

The corpus is ~90% research_challenges and **contains no NIST content**, so a compliance
golden set *must* score near the floor **regardless of embedding model**. Fine-tuning
embeddings cannot retrieve documents that were never ingested. This is the
rce-eval-01 doc's own "deliberate low-water-mark" observation, now quantified: the gap is
**coverage**, not vector quality.

### Chicken-and-egg + treadmill
To fine-tune you must first ingest the compliance corpus (to mine pairs). But **once it is
ingested, an off-the-shelf model — option (b) — will likely already retrieve it well**,
because these queries are lexically strong (control IDs like "AC-2", "AU-3"), which hybrid
BM25+vector + reranking handles without a bespoke model. So the ingestion that *enables*
fine-tuning is also the step that most likely makes it *unnecessary*.

### Dependency & air-gap impact
torch + sentence-transformers is the single largest dep footprint any RCE option would add,
directly against this environment's "no new heavy deps by default / prefer pure-Python /
air-gap" constraints. Distributing and re-distributing model weights to air-gapped enclaves
on every retrain is real operational cost for an unproven lift.

## 6. Recommendation — NO-GO / DEFER

**Do not adopt a fine-tuned domain-adapted embedding model now.** Rationale, from the
evidence above:

1. **No training data** — 0/1397 in-domain chunks; nothing to fine-tune on.
2. **Wrong lever** — the low baseline is a *coverage* gap; ingesting the compliance corpus,
   not swapping embeddings, is what moves the golden-set metrics.
3. **Cheaper options rank ahead** — contextual retrieval, RAPTOR, and the existing reranker
   (all in the RCE card) target the same metrics at lower cost and zero new deps; if
   embedding quality is ever shown to be the bottleneck, **option (b) (config-only stronger
   model) is the correct first move**, tried *before* any self-hosted fine-tune.
4. **Deps / air-gap / treadmill** — torch + sentence-transformers and a per-retrain
   re-embed/redistribute cycle are disproportionate to an unproven, currently
   unmeasurable, lift.

### What would flip this to GO (revisit triggers)
Re-open the decision only when **all** hold:

1. The **compliance/NIST corpus is ingested** — `tools/rag/embedding_feasibility.py`
   reports eligible (compliance) chunks **≥ ~2,000** (the probe's default viability
   threshold, tunable via `--min-eligible`).
2. With the corpus ingested, the **rce-eval-01 baseline plateaus** — i.e. after rce-ctx +
   rce-raptor + reranking + **option (b) stronger off-the-shelf embedding**, `rag_benchmark`
   recall/MRR stops improving and analysis points at embedding quality specifically.
3. A **labeled in-domain pair set** (query → positive NIST passage, a few thousand pairs)
   can be mined from the KG / compliance crosswalk to train and, critically, to *hold out*
   an eval slice.

Only then is a self-hosted fine-tune (option (a)) worth its deps and treadmill — and even
then, prove it with the A/B plan below before swapping the default.

## 7. Reproducible eval plan (how to prove any candidate model)

Any future embedding change — option (a) fine-tune or option (b) stronger model — is
proven the same way, against the committed rce-eval-01 baseline. **No metric is asserted;
it is measured via `rag_benchmark --compare`.**

```bash
# 0. (Re-runnable) confirm there is in-domain data to adapt to before spending effort.
python tools/rag/embedding_feasibility.py \
    --db data/rag/rag_vectors.db \
    --baseline data/rag/rce_baseline.json --json
#    -> gate on assessment.signal == "TRAIN-DATA-SUFFICIENT"

# 1. Register the candidate model as a provider in the embeddings chain
#    (args/llm_config.yaml `embeddings:` — a new model_id / provider entry).
#    For a self-hosted fine-tune: a SentenceTransformerEmbeddingProvider(EmbeddingProvider)
#    exposing embed()/dimensions()/check_availability(), added to default_chain.

# 2. Re-index the corpus with the candidate (dims must match the store, else full re-embed).
python tools/rag/rag_benchmark.py --baseline-out /tmp/rce_candidate.json --json

# 3. A/B the candidate against the committed baseline — the go/no-go number.
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline.json --json
#    -> comparison.deltas.{recall_at_5,mrr,ndcg_at_5,citation_hit_rate}.delta

# Adopt only if the deltas are materially positive on a HELD-OUT golden slice
# (never train and evaluate on the same queries), and the deps/air-gap cost is
# justified by the measured lift.
```

`--compare` emits a per-metric `{baseline, current, delta}` block
(`compare_to_baseline`, `tools/rag/rag_benchmark.py:282`) — that delta, on a held-out slice,
is the sole basis for adopting a domain-adapted model.

## 8. Feasibility probe shipped with this spike

`tools/rag/embedding_feasibility.py` (mirrored to `icdev/tools/rag/`, tested by
`tests/test_embedding_feasibility.py`, 7 fixture-driven cases, no live DB) makes the §5
decision **re-runnable** as the corpus evolves: it counts in-domain (compliance) vs other
chunks in the vector store and emits a `TRAIN-DATA-SUFFICIENT | TRAIN-DATA-INSUFFICIENT`
signal. When compliance ingestion crosses the threshold, the signal flips — that is the
concrete trigger to re-run the §7 eval plan. Dependency-free (stdlib `sqlite3`/`json`),
air-gap safe; adds **no** torch / sentence-transformers.

## 9. Design notes / constraints honored
- **Behind the provider abstraction** — every option is framed as an `EmbeddingProvider`
  registered in `args/llm_config.yaml embeddings:`; no hardcoded Ollama/nomic edit.
- **No new heavy deps by default** — the probe is stdlib-only; torch/sentence-transformers
  are explicitly *not* added to `requirements.txt`.
- **PG-primary + SQLite fallback** — the probe reads whichever `rag_chunks` store it is
  pointed at; dims changes are called out as forcing a full re-index on either backend.
- **Measured, not asserted** — the go/no-go and any future adoption route through the
  rce-eval-01 harness; ADR file (`docs/reference/adrs.md`) intentionally left untouched
  (hot file) — this standalone doc is the decision record.
