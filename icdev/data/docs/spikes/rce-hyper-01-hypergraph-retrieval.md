# RCE Spike — N-ary Hypergraph Retrieval over the Compliance Crosswalk Subgraph

**Task:** rce-hyper-01 (time-boxed design spike, go/no-go)
**Classification:** CUI // SP-CTI
**Status:** COMPLETE — recommendation below
**Depends on:** rce-eval-03 (compliance baseline), informed by rce-eval-05 (raptor DROP)

> ### ⚠ Premise check — 2026-07-26 (oss-meas-01-d3)
>
> **The saturation argument this NO-GO rests on was an artefact of the golden
> set, not a property of the retrieval problem.** Read this before acting on the
> recommendation below.
>
> The reasoning quoted immediately after this box — "the compliance golden set is
> already retrieval-saturated (0.9545 recall@5, 30/33 queries perfect)" — is
> arithmetically right about the **v1 33-query set** (measured independently:
> 29/33 at both perfect recall and perfect MRR, not 30). But that saturation was
> a property of the *instrument*. On the v2 48-query set (`oss-meas-01-d1`, PR
> #817), against the **same corpus**, the control sits at **recall@5 0.7431** —
> roughly 21 points of headroom where the v1 set showed almost none.
>
> The `raptor DROP` this spike is "informed by" has itself been withdrawn for the
> same reason: same corpus, same toggle, opposite sign once measured with an
> instrument that could register a gain.
>
> **This box does not reopen the NO-GO.** The spike's other objections — build
> cost, schema complexity, maintenance surface — are untouched and may still
> carry the decision on their own. What is void is the specific claim that there
> is no measurable headroom for a retrieval improvement to occupy. If that claim
> was load-bearing for the NO-GO, the decision needs retaking; if it was
> supporting, the conclusion may stand on the remaining grounds.
>
> See [oss-meas-01-retrieval-toggle-benchmark.md](../features/oss-meas-01-retrieval-toggle-benchmark.md).

> **Recommendation up front: NO-GO for a production build now; DEFER as a
> documented design.** The engineering is feasible and cheap to prototype, but the
> adoption case does not clear the bar this phase has repeatedly set: the
> compliance golden set is already retrieval-saturated (0.9545 recall@5, 30/33
> queries perfect — see rce-eval-05), so the measurable headroom any retrieval
> enhancement can capture is near zero. The two source papers are single-lab and
> unreplicated, and the technique carries a ~3× serving-cost multiplier. Build only
> if a genuinely n-ary-heavy retrieval need emerges with *measured* headroom on a
> corpus the flat pipeline demonstrably fails.

---

## 1. What was asked

Assess whether n-ary **hyperedge** retrieval — one edge connecting *n* entities at
once, instead of ICDEV's strictly binary `kg_edges(source_id, target_id,
relationship)` — would improve retrieval over the compliance **crosswalk subgraph**,
where facts are natively n-ary and are currently shredded into lossy binary pairs.

Two 2025–26 papers motivate it:

| Paper | Venue | Claim | Cost |
|---|---|---|---|
| **HyperGraphRAG** (arXiv 2503.21322) | NeurIPS 2025 | +7.45 F1 over StandardRAG | build 3.08 s / \$0.0063 per 1k tok; serve **~\$3.18 vs \$1.02 per 1k queries (~3×)** |
| **Hyper-RAG** (arXiv 2504.08758) | Nature Comms, Feb 2026 | +6.3% vs GraphRAG, +6.0% vs LightRAG; flat accuracy as query complexity rises | same family |

**Provenance caveat (load-bearing):** both papers *and* the `hypergraph-db` library
they depend on were authored by the **same lab (iMoonLab)**. The magnitude is
single-lab and unreplicated; a GO must not rest on the papers' numbers.

## 2. The n-ary case in ICDEV is real

ICDEV's compliance facts genuinely bind more than two entities, and the binary KG
loses that:

- **Crosswalk:** `NIST AC-2 ↔ FedRAMP High ↔ CMMC L2 ↔ STIG V-xxxx` is **one 4-way
  fact**, currently ~6 disconnected binary edges. The joint constraint (this control,
  at this impact level, satisfied by this STIG) is not recoverable from any single
  pair.
- **Control applicability:** `control + impact level + baseline + parameter + artifact`.
- **Supply chain / SIPA:** `component + version + CVE + affected boundary + SLA`.

Reifying these as hyperedges is a faithful model of the domain. That is the strongest
argument in favor and it is a *modeling* argument, not yet a *retrieval-quality*
argument.

## 3. Feasibility — how it would extend the existing stack

### 3.1 Storage (PG is the only system of record)

Reify hyperedges bipartitely onto **two new PG tables** — which is exactly what
HyperGraphRAG's storage does anyway:

```
kg_hyperedges(
  id, graph_id, relationship, properties JSONB, embedding_vec,
  tenant_id, classification, created_at)          -- the n-ary fact as a node

kg_hyperedge_members(
  hyperedge_id, node_id, role, tenant_id, classification)  -- membership, n rows/fact
```

Both tables **must** carry `tenant_id + classification` for RLS parity with
`kg_nodes` / `kg_edges`. A separate embedded/vector store (e.g. loading everything
into `hypergraph-db` as persistence) would put compliance KG facts **outside the RLS
predicate** — disqualifying in an IL4/IL5 product. This is non-negotiable and is the
single most important design constraint.

This is a small, well-understood migration: two tables, standard RLS columns, mirror
to `icdev/tools/`, register in `tests/conftest.py` `MINIMAL_ICDEV_SCHEMA` and
`tools/manifest/rag-subsystem.md`. No novel storage engineering.

### 3.2 The `hypergraph-db` library — in-memory traversal ONLY

`hypergraph-db` (PyPI, Apache-2.0, `py3-none-any` wheel, **zero declared deps**,
`requires-python >=3.10`) is admissible **only as an in-memory traversal helper**:
load a subgraph from PG → expand → discard. Never as persistence.

**First question to answer in any build:** does its API even support *detached
in-memory construction* (build a `Hypergraph` object from tuples, no disk)? If yes,
it can host the bidirectional expansion step. **If not, hand-roll the expansion** —
for the bounded crosswalk subgraph it is a few set operations over
`kg_hyperedge_members` (given entities → hyperedges containing them → sibling
entities), not complex, and avoids a dependency entirely. Vendored air-gap via:

```
python tools/airgap/wheel_vendor.py --fetch --topic hypergraph
```

Given the zero-dependency, hand-rollable nature of the expansion, the pragmatic call
is: **prototype hand-rolled first; adopt the library only if it measurably simplifies
multi-level expansion.**

### 3.3 Retrieval — merge into the existing hybrid RRF pipeline

ICDEV already does Reciprocal Rank Fusion (`_RRF_K = 60`) over BM25 + semantic
rankings in `tools/knowledge_graph/graph_rag.py` (see `retrieve()` at line ~1263 and
the RRF block ~995–1010) and in the RAG retriever. Hyperedge retrieval slots in as an
**additional ranked list fused via RRF**, mirroring exactly how the raptor summary
tier was merged in `RAGRetriever.search` (`_merge_raptor_results`):

1. Embed query → top-k **entities** by cosine (reuse existing node embeddings).
2. Top-k **hyperedges** directly by cosine over `kg_hyperedges.embedding_vec`.
3. **Bidirectional expansion:** entity→hyperedge (members) and hyperedge→entity.
4. Merge the resulting nodes/edges into the candidate pool and RRF-fuse with the
   existing chunk + KG rankings.

No new metric code: measure with the **same harness** (`tools/rag/rag_benchmark.py`
against `data/rag/rce_baseline_compliance.json`) and the existing `evaluator.py`
metrics (recall@k, MRR, ndcg@k), comparing against GraphRAG's `retrieve()` on
crosswalk queries specifically. This is the same discipline rce-eval-04/05 used.

### 3.4 Construction — LLM n-ary extraction, air-gap degradable

Construction needs an LLM pass to extract n-ary facts from the crosswalk corpus.
Route through `LLMRouter` (cheap tier, no hardcoded model), **degrade to no-op when
unavailable** — the same pattern `contextual_retrieval` and `raptor` already use.
Record the provider actually used: **n-ary extraction is materially harder than
binary extraction**, and a small local model may emit malformed or low-precision
hyperedges. That is a *finding*, not a failure — and it is a real risk, because the
crosswalk source data is already semi-structured (`crosswalk_engine.py` +
`context/compliance/*.json` catalogs), which raises the question of whether an LLM
extraction pass is even the right tool versus a **deterministic loader** from those
catalogs. A deterministic crosswalk→hyperedge loader would sidestep the extraction-
quality risk entirely and is likely the better v0.

## 4. Cost

- **Construction:** bounded — crosswalk subgraph only, one-time LLM (or deterministic)
  pass. The papers report construction *cheaper* than GraphRAG (3.08 s vs 9.27 s per
  1k tokens), so build cost is not the blocker.
- **Serving:** the papers' own numbers show **~3× per-query serving cost** (~\$3.18 vs
  \$1.02 per 1k queries). This is the primary adoption risk and must be measured per
  query on ICDEV, not assumed from the papers.

## 5. Why NO-GO now (the decisive argument)

The feasibility is genuinely fine. The problem is **headroom**, and this phase just
produced the evidence:

1. **The baseline is near-saturated.** rce-eval-03/05 measured the compliance golden
   set at **recall@5 0.9545, 30/33 queries perfect**. rce-eval-05 then showed RAPTOR —
   a whole LLM-built summary tier — moved that by **+0.0 recall / +0.0 MRR /
   −0.0005 ndcg**. There is almost no retrieval headroom left on this golden set for
   *any* structural enhancement to capture, hypergraph included. A spike whose payoff
   is measured against a saturated baseline is very likely to come back flat — and a
   flat result is NO-GO, exactly as rce-eval-02 concluded for the fine-tune.
2. **The golden set may not even exercise the n-ary case.** The crosswalk queries
   (`q-stig-hardening`, `q-fedramp-authorization-boundary`) that *would* stress joint
   4-way facts are today answered by the flat pipeline at perfect recall or fail for
   reasons (e.g. `q-stig-hardening` at 0.0) that raptor could not fix and hyperedges
   have no obvious reason to fix either. Before any build, the golden set would need
   **new, genuinely n-ary crosswalk queries** designed to be unanswerable by binary
   retrieval — otherwise the measurement is unfalsifiable.
3. **Single-lab, unreplicated claims + ~3× serving cost.** Paying a 3× serving
   multiplier and a new subsystem's maintenance for an unreplicated +6–7% — on a
   corpus where our own baseline shows near-zero headroom — is a poor trade.

## 6. What would flip this to GO (falsifiable pre-conditions)

A future GO is legitimate **only** if, before building the engine:

1. A set of **n-ary crosswalk golden queries** is authored that the current flat +
   binary-KG pipeline **measurably fails** (recall@5 materially < 1.0). Without
   demonstrated failure there is nothing to improve.
2. On that harder set, a **cheap deterministic hyperedge loader** (from
   `crosswalk_engine.py` / the compliance catalogs, no LLM) plus hand-rolled
   bidirectional expansion shows a **measured recall gain** via `rag_benchmark.py`.
3. The **per-query serving-cost delta** is measured on ICDEV and is acceptable
   (the ~3× claim is validated or beaten locally).

If all three hold, the storage/retrieval design in §3 is ready to implement as-is.

## 7. Go/No-Go

- **Decision: NO-GO (build) / DEFER (design).**
- **Feasibility:** HIGH — PG bipartite reification with RLS parity is straightforward;
  retrieval fuses into the existing RRF pipeline exactly as the raptor tier did; the
  library is optional and the expansion is hand-rollable.
- **Value on current evidence:** LOW — baseline saturated (rce-eval-05 lesson), source
  claims single-lab/unreplicated, ~3× serving cost, extraction-quality risk.
- **Next step if revisited:** author n-ary golden queries that the flat pipeline fails
  (§6.1) — cheap, and it is the only thing that makes this spike falsifiable.

## 8. If built later — checklist (deferred, not done here)

Per the task's acceptance criteria, a future build must:

- Register `kg_hyperedges` + `kg_hyperedge_members` in `tests/conftest.py`
  `MINIMAL_ICDEV_SCHEMA`, mirror to `icdev/tools/`, and add to
  `tools/manifest/rag-subsystem.md`.
- Carry `tenant_id + classification` on both tables (RLS parity, non-negotiable).
- Default OFF behind a `rag.hypergraph.*` toggle in `args/rag_config.yaml`.
- Record an ADR with the go/no-go and measured numbers.

This spike lands the design and the NO-GO; no production code, tables, or toggles are
added.
