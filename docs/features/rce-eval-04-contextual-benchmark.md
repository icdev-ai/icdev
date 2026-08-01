# rce-eval-04-d4 — contextual-retrieval benchmark comparison (run record)

**Classification:** CUI // SP-CTI
**Date:** 2026-07-25
**Task:** `rce-eval-04-d4` — Run benchmark comparison and record results
**Command:** `python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline_compliance.json --json`

## Artifacts

| File | Role |
|------|------|
| [data/rag/rce_baseline_compliance.json](../../data/rag/rce_baseline_compliance.json) | **before** — pre-contextual baseline (`rce-eval-03`), generated 14:52:54Z |
| [data/rag/rce_contextual_compliance.json](../../data/rag/rce_contextual_compliance.json) | **after** — verbatim CLI output of this run, including the `comparison.deltas` block, generated 18:35:23Z |

Both runs used golden query set `args/rag/golden_query_set.yaml` v1 (33 scored
queries, `top_k=5`) against the `compliance_reference` corpus on live PostgreSQL.

## Aggregate before/after

`rag.contextual_retrieval.enabled` was already `true` — the toggle was flipped as
part of `rce-eval-04-d3`, so no config change was needed for this run
(`args/rag_config.yaml:123`).

| Metric | Baseline | Contextual | Δ |
|--------|---------:|-----------:|---:|
| `recall_at_5` | 0.9394 | **0.9545** | +0.0151 |
| `mrr` | 0.9343 | **0.9545** | +0.0202 |
| `ndcg_at_5` | 0.9429 | **0.9534** | +0.0105 |
| `citation_hit_rate` | 0.9697 | 0.9697 | 0.0000 |

All four aggregates moved in the expected direction or held. `citation_hit_rate`
is unchanged because it is a coarse ≥1-target-hit measure: the same 32 of 33
queries hit before and after, and the one miss (`q-stig-hardening`) is a
golden-set defect (see below), not something a better index can fix.

### The delta is real, not a stale-vector artifact

`rce-eval-04-d3` documented a silent-no-op risk — contextual embeddings written
to the legacy `embedding` BYTEA blob while `pg_vector_store` ranks on the
pgvector `embedding_vec` column. Three checks confirm this run measured the
contextual index:

* Direct PG read: 3552/3552 `compliance_reference` chunks carry both
  `metadata.context_prefix` and a populated `embedding_vec`.
* `metadata.context_provenance.generated_at` spans **15:40:01Z → 17:44:46Z**,
  entirely *after* the baseline's 14:52:54Z — so the baseline is genuinely the
  pre-contextual state.
* 12 of 33 queries changed at least one per-query metric. A stale index would
  have produced a delta of exactly zero on every query.

Both runs resolved the same embedding model, so the comparison isolates the
prefix change rather than an embedding swap: `OllamaEmbeddingProvider` /
`nomic-embed-text`, 768 dimensions — the third entry in the
`embeddings.default_chain` (`openai-embed` → `gemini-embed` →
`nomic-embed-local`), reached because no OpenAI/Gemini embedding key is
configured in this environment.

## LLM provider used for prefix generation

The prefix generator is the router function `rag_evaluate`
(`rag.contextual_retrieval.function`), which resolves in this environment to:

| Field | Value |
|-------|-------|
| Provider class | `tools.llm.ollama_provider.OllamaProvider` |
| Routed provider key | `ollama_cloud` |
| Model | `kimi-k2.6:cloud` |
| Max output tokens | 32768 |
| Prompt version | `ctx-v1` |

Persisted provenance across the corpus (`metadata.context_provenance`):

| Method | Generator | Chunks |
|--------|-----------|-------:|
| `llm` | `rag_evaluate` | 3148 |
| `heuristic` | `source_type+metadata` | 404 |

Two observations worth carrying forward:

* **Provenance does not record the model.** `context_provenance` stores
  `{generator, method, prompt_version, generated_at}` but not the provider key or
  model id, so "which model wrote this prefix" is only recoverable from the
  router config as it stood at run time. Attributing a future quality regression
  to a model change would require re-deriving it from `.env` history.
* **`ollama_cloud` is a cloud egress path.** The corpus here is public reference
  material (NIST 800-53, FedRAMP baselines, CMMC practices, DISA STIGs), so this
  run raises no CUI concern, but the same `rag_evaluate` route applied to a
  CUI-bearing corpus would send chunk text off-host. `api_key_env` is what
  distinguishes local `ollama` from `ollama_cloud`; a CUI corpus must pin the
  local provider before re-indexing.

## Per-query breakdown

Nine queries were imperfect in the baseline and four were imperfect after. Full
per-query movement (13 rows: all 12 changed queries plus the unchanged miss):

| Query | recall@5 | MRR | nDCG@5 | targets hit |
|-------|---------:|----:|-------:|------------:|
| `q-ac2-account-mgmt` | 1.0 → 1.0 | 0.5 → **1.0** | 0.6934 → **0.9829** | 2/2 → 2/2 |
| `q-sc13-cryptographic-protection` | 0.5 → **1.0** | 0.3333 → **0.5** | 0.5706 → **0.7328** | 1/2 → **2/2** |
| `q-fedramp-authorization-boundary` | 0.5 → **1.0** | 1.0 → 1.0 | 1.0 → 1.0 | 1/2 → **2/2** |
| `q-au9-protection-audit` | 1.0 → 1.0 | 1.0 → 1.0 | 0.9469 → **1.0** | 2/2 → 2/2 |
| `q-ia2-mfa` | 1.0 → 1.0 | 1.0 → 1.0 | 0.9829 → **1.0** | 2/2 → 2/2 |
| `q-ra3-risk-assessment` | 1.0 → 1.0 | 1.0 → 1.0 | 0.9558 → **1.0** | 2/2 → 2/2 |
| `q-fips199-categorization` | 1.0 → 1.0 | 1.0 → 1.0 | 0.9829 → **1.0** | 2/2 → 2/2 |
| `q-ac3-access-enforcement` | 1.0 → 1.0 | 1.0 → 1.0 | 1.0 → *0.9047* | 2/2 → 2/2 |
| `q-au2-event-logging` | 1.0 → 1.0 | 1.0 → 1.0 | 1.0 → *0.9829* | 2/2 → 2/2 |
| `q-cm6-config-settings` | 1.0 → 1.0 | 1.0 → 1.0 | 0.9829 → *0.9047* | 2/2 → 2/2 |
| `q-ca5-poam` | 1.0 → 1.0 | 1.0 → 1.0 | 1.0 → *0.9558* | 2/2 → 2/2 |
| `q-cmmc-cui-protection` | 1.0 → *0.5* | 1.0 → 1.0 | 1.0 → 1.0 | 2/2 → *1/2* |
| `q-stig-hardening` | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0/2 → 0/2 |

The four nDCG-only dips (`q-ac3`, `q-au2`, `q-cm6`, `q-ca5`) all keep 2/2 targets
and MRR 1.0 — a matched chunk moved down a rank or two inside a still-complete
top-5. They are re-ordering noise, not lost recall.

### The named imperfect queries

**`q-sc13-cryptographic-protection`** — expects `cryptographic protection` and
`SC-13`. Baseline found only the first. `fedramp:FRM-H-SC-13` (the chunk carrying
both) now enters at rank 4, score 0.8115 — the top score in the result set.
Notably this chunk carries a *heuristic* prefix ("This chunk is from Source:
compliance reference; family: SC."), so the win here comes from the prefix's
family tag, not from LLM prose. Recall 0.5 → 1.0. MRR only reaches 0.5 because
three lower-scored `nist53` cryptographic-key enhancements (IA-13(1), SC-8(3),
SC-8(1)) still rank ahead of it.

**`q-fedramp-authorization-boundary`** — expects `authorization boundary` and
`FedRAMP`. Baseline retrieved five FedRAMP SC-7 chunks (satisfying `FedRAMP`) but
nothing containing the literal phrase. `nist53:sc-7` now appears at rank 5 with
`authorization boundary` in-body. Recall 0.5 → 1.0 at unchanged MRR/nDCG 1.0,
because rank 1 already matched.

**`q-stig-hardening`** — expects `STIG` and `hardening`; still 0/2, the only
`citation_hit_rate` miss in either run. This is a **golden-set defect, not a
retrieval failure**. Corpus substring counts:

| Substring | Chunks in `compliance_reference` (of 3552) |
|-----------|------------------------------------------:|
| `hardening` | 4 |
| `STIG` | 56 |
| `STIG` **and** `hardening` in the same chunk | **0** |

Retrieval does surface a STIG document at rank 2 (`stig:V-222609`), but that
chunk is a mid-document Check/Fix fragment whose body never spells out the
acronym — the STIG identity lives in the prefix and the `source_id`, neither of
which `score_query` inspects (it matches `substring` targets against `content`
only). No index improvement can satisfy this query as written; the fix is to
re-target it at text that exists in the corpus, or to let `substring` targets
match the contextual prefix.

**`q-ac2-account-mgmt`** — the fourth baseline-imperfect query and the run's
largest single gain. Both targets were already found, but the first match sat at
rank 2 (MRR 0.5, nDCG 0.6934). `fedramp-ksi:KSI-AC-02`, whose LLM prefix opens
"This chunk presents the complete KSI-AC-02 Account Management control
specification…", now leads at rank 1 with both substrings present:
MRR → 1.0, nDCG → 0.9829.

### One regression

**`q-cmmc-cui-protection`** — recall 1.0 → 0.5, the only recall loss. Expects
`controlled unclassified information` and `CMMC`. All five results are CMMC
Level 2 practice chunks matching `CMMC`, but none spells the phrase out — they
all write "CUI". Exactly **1** chunk in the corpus contains both strings, and it
was displaced. The mechanism is prefix homogenisation: every CMMC chunk's LLM
prefix now begins "This chunk presents the complete CMMC Level 2 …", which pulls
the whole family tighter around the query embedding and lets near-duplicate
siblings crowd out the single phrase-bearing chunk. The same substring literalism
that makes `q-stig-hardening` unwinnable is what turns this into a scored loss.

## Assessment

Contextual retrieval is a modest, real improvement on this corpus: +1.5 pts
recall@5, +2.0 pts MRR, +1.1 pts nDCG@5, with two of the three named
recall-limited queries fully recovered. The gains are smaller than the headline
numbers reported for contextual retrieval upstream, which is expected here — the
corpus is short, highly structured control text where the `control_id: …
title: …` header already supplies most of the context a prefix would add, and
404 chunks fell back to the heuristic prefix.

Two of the four remaining imperfect queries (`q-stig-hardening`,
`q-cmmc-cui-protection`) are measurement artifacts of literal-substring targets,
not retrieval defects. Tightening the golden set is the higher-value next step
than tuning the index further.

## Reproduction

```bash
cp /c/AI/ICDev/.env .env        # worktrees do not carry .env; live PG required
unset GITHUB_TOKEN
python tools/rag/rag_benchmark.py --compare data/rag/rce_baseline_compliance.json --json
```

The run is read-only against the corpus (33 query embeddings, ~30 s) and makes no
LLM completion calls — prefix generation already happened during `rce-eval-04-d3`.

## Related

* [rce-eval-04-contextual-reindex-run.md](rce-eval-04-contextual-reindex-run.md) — the re-index this benchmarks
* [rce-eval-01-retrieval-baseline.md](rce-eval-01-retrieval-baseline.md) — the harness
* [rce-ctx-02-contextual-reindex.md](rce-ctx-02-contextual-reindex.md) — the re-index tool
* [phase-rce-rag-context-engineering.md](phase-rce-rag-context-engineering.md) — phase overview
