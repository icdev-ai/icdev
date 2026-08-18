# CUI // SP-CTI

# cef-di-03 — DocDrift's SSP evidence through the governed `cortex.resolve()` seam

**Card:** `cef-di-03` · **Canvas:** Document Intelligence (DIC) · **Date:** 2026-08-18

## The defect

`tools/document_intelligence/acoic.py` — DocDrift, the DIC compliance sink — drafts
cited SSP fragments. Its evidence half was `_retrieve_evidence`:

```python
retriever = RAGRetriever(tenant_id or "")
results = retriever.search(query, top_k=k)
return [chunk_text(r) for r in results]     # texts. and nothing else.
```

Two things followed from *and nothing else*:

1. **The citations named nothing.** A drafted fragment carries `[SOURCE-N]` tags,
   and `verifier.verify` replays each claim against `evidence[N-1]` *inside the
   same call*. After that call returned, the list was discarded. So a persisted
   fragment's `[SOURCE-1]` pointed at an index into something no reader could
   recover — a citation in shape only. This is the "file existence as compliance
   evidence" shape one layer down: the artifact has the right structure and backs
   nothing.
2. **One rung was consulted.** The currency store, DIC, the knowledge graph and
   the KB all hold evidence about a NIST control. None of them were asked,
   because asking would have meant acoic learning four more table names.

## The change

`icdev/tools/document_intelligence/ssp_evidence.py` — one governed seam, the sibling of
`tools/doc_modernization/evidence.py` (cef-di-01) on the other side of the same
canvas.

```python
from icdev.tools.document_intelligence.ssp_evidence import resolve_evidence
bundle = resolve_evidence("AC-2", frameworks=["fedramp_high"])
bundle.texts[i]        # what the drafter and the CoD verifier consume
bundle.citations[i]    # the source id / table / provenance id backing texts[i]
```

`texts` and `citations` are **index-aligned**, so `[SOURCE-N]` finally means
`citations[N-1]`. `generate_ssp_fragment` persists them on the fragment under
`citations_json.sources`, alongside `citations_json.evidence_path` naming which
chain produced the draft (`cortex` | `cortex_empty_fallback` | `legacy` |
`caller`).

Under the hood it is one `cortex.resolve(control_id)`: five backends, the 8-gate
TRUST chain, one `cortex_audit` row and one `source_citation_registry` row per
lookup.

### The toggle

`args/dic_acoic_config.yaml`:

```yaml
cortex:
  enabled: false            # DEFAULT OFF — the rollback is this flag, not a revert
  top_k: 5                  # matches the legacy _retrieve_evidence(..., k=5)
  max_resolves_per_run: 100
  fallback_on_empty: true
```

Off means the seam is **never consulted** — `_gather_evidence` short-circuits to
the original call before Cortex is imported. That is what makes "flip the flag"
a real rollback rather than an approximate one, and
`test_toggle_off_never_consults_cortex` asserts it by arming `cortex.resolve` to
raise.

## What deliberately did NOT change

The card names two air-gap-safe paths that must survive. Neither is reachable
from this module and both are asserted with the LLM router armed to raise:

| Path | Why it stays |
|------|--------------|
| `map_changed_controls` — the RICOAS / NIST 800-53 crosswalk + the best-effort compliance-KG path | A pure JSON lookup. It never went near retrieval, and a ranked evidence seam cannot return an exact cross-framework mapping. |
| `_draft_fragment_text`'s cited-template fallback | The no-LLM draft. It consumes evidence texts and does not care where they came from, so it works identically on both paths — `test_cited_template_fallback_is_reached_on_the_cortex_path_too` proves that on the migrated path too. |

`cortex.resolve` makes no model call of its own (it passes `corrective=False`, so
even the CRAG rewrite does not run), so the toggle does not put an LLM anywhere
one was not already.

`pack_evidence` citations are dropped at the seam. Those are a `DomainPack`'s own
verdict rationale coming back through the fan-out; letting one become a cited
sentence in an SSP narrative would make a derived verdict the ground truth for a
control implementation.

## Every degradation lands on the legacy path

`None` from the seam always means "do what you did before" — and each cause is
logged with its own reason, because they send you to four different places:

| Cause | Result |
|-------|--------|
| toggle off | `PATH_LEGACY` |
| re-entrant (`resolve` → `assess` → `pack.evaluate` → here) | `PATH_LEGACY`, thread-local guard |
| outbound budget spent | `PATH_LEGACY`, counted in `run_stats()["capped"]` |
| Cortex not importable | `PATH_LEGACY` |
| governance **refusal** | bundle with `blocked`, then `PATH_CORTEX_EMPTY_FALLBACK` |

No drafting run can be failed by this module.

## Measured, before and after — live DIC canvas, 2026-08-18

Read-only comparison over `_gather_evidence` for the two controls the live drift
payloads actually carry (`CM-12`, `SA-5`), toggle off then on, `fallback_on_empty:
false` so the governed path is unvarnished:

| | toggle OFF (legacy) | toggle ON (cortex) |
|---|---|---|
| evidence texts | 5 | 5 |
| **with a source id** | **0** | **5** |
| text lengths | 112–667 chars | 152–200 chars |
| tables named | *(none recorded)* | `rag_compliance_corpus`, `dic_documents` |
| deterministic draft | 1402 / 1419 chars, 5 `[SOURCE-N]` | 970 / 934 chars, 5 `[SOURCE-N]` |
| warm latency | 0.3s | 4.1–4.8s |

The count of evidence pieces and of `[SOURCE-N]` tags is unchanged; what changed
is that every one of them is now attributable.

### The cold-process caveat, stated because it is not free

The same call in a **cold** process spends 10.3s and abandons `rag`, `dic` and
`graph` at the 10.0 / 10.0 / 8.0 second budgets in `args/cortex_config.yaml`,
answering from `currency` alone — 4 catalog citations, none of them control text.
The direct retriever needs 17.4s on that same cold cache and has no timeout, so
it wins there.

That answer is **thin, not empty**, so `fallback_on_empty` does not fire for it.
What covers it instead is `SSPEvidence.errors`, persisted on the fragment under
`citations_json.evidence_detail.backend_errors` — an abandoned backend stays
legible as an infrastructure event rather than becoming a statement about the
corpus. Raising the global Cortex timeouts would change every Cortex consumer and
is out of this card's scope.

Also observed on every resolution: `kb` fails with `column "use_count" does not
exist`. A pre-existing Cortex backend defect, reported through the same channel
rather than swallowed.

Evidence text is shorter because a citation snippet is capped at 200 characters
by `tools/cortex/search_service.py`. That is accepted rather than worked around:
a drafted sentence must be replayable against the *exact* evidence its
`[SOURCE-N]` names, and handing the verifier a fuller chunk than the citation
records would make the persisted provenance a summary of what was verified
instead of the thing itself.

## `dic_ssp_fragments` — the honest answer

**It held 0 rows before this change and it still holds 0 rows.** Measured against
the live PostgreSQL board:

```
dic_ssp_fragments      0
dic_drift_events      72
dic_acoic_regen_queue 72   (all 72 in state 'queued')
```

The reason is not the evidence chain and was never the evidence chain: **the
drafting entry point has never been invoked.** `generate_ssp_fragment` is reached
from `process_regen_item`, and `process_regen_item` is reached from exactly one
place — the module's own CLI. Nothing schedules it, no reflex calls it, and the
DIC blueprint does not expose it. Every one of the 72 queue items is still
`queued`; not one has ever reached `regenerating`.

(21 of the 72 drift events do carry `control_ids` — `['CM-12', 'SA-5']` — so
fragments *would* be drafted if the entry point ran. 51 carry none and would
draft nothing either way.)

This card migrated the evidence chain, which is what it was scoped to do.
Wiring a trigger for `process_regen_item` is a separate change and deliberately
not smuggled in here: the epic's rule is one surface per task, and adding an
autonomous drafting trigger to a HITL compliance surface is a decision that wants
its own review, not a line in an evidence migration.

## Files

| File | Role |
|------|------|
| `args/dic_acoic_config.yaml` | new — the `cortex:` toggle block, default off |
| `icdev/tools/document_intelligence/ssp_evidence.py` | new — the seam (canonical namespace) |
| `tools/document_intelligence/ssp_evidence.py` | new — a **re-export**, not a copy: the module holds thread-local run state and two copies would be two budgets, and `tools.X is icdev.tools.X` is False |
| `tools/document_intelligence/acoic.py` | `_gather_evidence` + provenance persisted on the fragment; `_retrieve_evidence` untouched |
| `tests/test_acoic_cortex_evidence.py` | 18 tests, gated via `args/ci_test_files/core.d/cef-di-03.txt` |
