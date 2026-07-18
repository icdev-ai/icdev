# IQE Triage — Canvas Classification (tch-fix-04)

CUI // SP-CTI

Every dashboard canvas under `tools/dashboard/templates/<canvas>/` was scored by
`tools/quality/completion_auditor.py` and classified for IQE (Intelligent Query
Engine) coverage. The machine-readable result lives in
[`args/completion_exemptions.yaml`](../../args/completion_exemptions.yaml), which
is consumed by `coherence_checker._load_page_completeness_whitelist` (the
`iqe_exempt` / `iqe_wired_via_alias` / `iqe_partial` buckets are skipped by the
completeness gate; `iqe_required` is **not** — those keep failing until backfilled).

> **Key finding.** The naive auditor matches the IQE adapter by *exact template
> dir name*, but most canvases register their adapter under an abbreviated
> `_CANVAS_MAP` key (`app.py`) — e.g. `document_intelligence → dic`,
> `security_canvas → sdc`, `noc_canvas → nocc`. So most "missing IQE adapter"
> reports are **false negatives**: the canvas is already fully wired. Only the
> `iqe_required` set below is a genuine, from-scratch backfill.

Scorecard snapshot: **1/58 fully complete** (`govlift`). Full table:
[`docs/quality/completion-scorecard.md`](completion-scorecard.md).

---

## iqe-required — genuine backfill targets (→ tch-fix-05)

Data-bearing, query-worthy canvases with **no working IQE adapter**. These are
the concrete backfill list. Each needs: `tools/iqe/adapters/<key>.py` (register
collections), `POST /api/iqe-query` wiring + `_CANVAS_MAP` entry in `app.py`,
`{% include "includes/iqe_query_widget.html" %}` in a template, and ≥3 seed
queries under `context/iqe/queries/<key>/`.

| Canvas | Why required | Candidate collections |
|--------|--------------|-----------------------|
| `finetune` | Fine-tune jobs/datasets/model registry/eval runs; rich backing modules, no adapter | `finetune.jobs`, `finetune.datasets`, `finetune.models`, `finetune.eval_runs` |
| `rag` | RAG source registry/chunks/eval runs/quality feedback; no adapter | `rag.sources`, `rag.chunks`, `rag.eval_runs`, `rag.quality_feedback` |
| `migration_intelligence` | Migration opportunities/strategies/SLA records/pipeline reports; no adapter (distinct from `migration_canvas` = `mc`/`cam`) | `migration_intel.opportunities`, `migration_intel.strategies`, `migration_intel.sla` |
| `mission_canvas` | **BROKEN**: `_CANVAS_MAP` already dispatches to `tools.iqe.adapters.mission_canvas` but that file is **absent** → ImportError on dispatch. Create the adapter. | `mission.sessions`, `mission.twins`, `mission.evidence`, `mission.alerts` (per existing map) |
| `proposals` | Pre-award proposal pipeline data; mini-bar routes `/proposals → govcon`, but the `govcon` collections (opportunities/awards/blackhat/competitors) do **not** cover proposal tables | `proposals.opportunities`, `proposals.pipeline`, `proposals.win_themes` |

## iqe-partial — adapter exists, small backfill (register + seed)

Adapter file present but **not registered in `app.py` `_CANVAS_MAP`** (and/or seed
queries present only as non-`.yaml`). Add the dispatch entry + `.yaml` seeds
(+ widget where missing). Excused from the gate meanwhile.

| Canvas | Adapter | Remaining |
|--------|---------|-----------|
| `studio` | `studio_sim.py` | add `_CANVAS_MAP` dispatch entry + `.yaml` seeds |
| `zta` | `zta.py` | add `_CANVAS_MAP` dispatch entry + IQE widget + `.yaml` seeds |
| `gameday` | `gameday.py` | add `_CANVAS_MAP` dispatch entry + `.yaml` seeds |

## iqe-wired-via-alias — already wired (no action; fix is auditor-side)

IQE is already wired under an abbreviated `_CANVAS_MAP` key with a real adapter
file. The proper fix is teaching the auditor/gate to resolve template-dir →
`_CANVAS_MAP` key (a separate TCH task); until then these are excused.

`network`→`ndc`, `security_canvas`→`sdc`, `data_canvas`→`ddc`,
`infra_canvas`→`idc`, `observability_canvas`→`odc`, `boundary_canvas`→`bdc`,
`migration_canvas`→`mc`/`cam`, `agentic_ai_canvas`→`aadc`, `aiml_canvas`→`aimc`,
`ops_hub`→`ohc`, `noc_canvas`→`nocc`, `pmc_canvas`→`pmc`, `ccc_canvas`→`ccc`,
`dsoc_canvas`→`dsoc`, `qdc_canvas`→`qdc`, `document_intelligence`→`dic`,
`coworker`→`ace`.

## iqe-exempt — utility/legacy, no query-worthy data model

`admin`, `agents`, `autonomous_coder`, `events`, `forge_academy`, `il5`,
`includes` (not a canvas — shared partials),
`mfa`, `monitoring`, `orchestration`, `poam`, `projects`, `query` (the IQE UI
itself), `safety_monitor`, `sre`, `system_graph`, `ai_gameday` (orphan template,
superseded by `gameday`).

---

## Cross-cutting note: seed-query gap is near-universal

Only `govlift` has `.yaml`/`.yml` seed queries under `context/iqe/queries/`.
Every other canvas — including fully-wired ones — fails the auditor's
`iqe_seed_queries` check (many have `.iqe` files, not `.yaml`). That is a
separate, systemic backfill, distinct from the adapter-level triage above.
