# External Benchmark Map — Major Subsystems

CUI // SP-CTI · First pass 2026-08-02

Benchmarks ICDEV's major subsystems against best-in-class external projects, to find
concepts worth adapting and to establish where ICDEV is already ahead.

**Method.** Every "ICDEV today" claim below is *measured* — row counts against the live
PostgreSQL instance, `grep` over the tree, or a function invoked — not inferred from
documentation. Where a subsystem turned out stronger than expected, that is recorded as
plainly as the gaps, because a benchmark that only finds deficits is not a benchmark.

**Scope.** The ten major subsystems. It deliberately does *not* cover every canvas and
engine individually — that exhaustive sweep is a separate pass, and this one exists partly
to tell it where to look.

**This document is now re-checked automatically.** Its measured half — module counts,
verdicts, and what is still outstanding per subsystem — is regenerated from config into
[external-benchmark-map.generated.md](external-benchmark-map.generated.md) by
`python tools/innovation/benchmark_report.py --write`, and CI fails when that file drifts
from its sources. **Read the generated file for the current numbers**; the numbers quoted
below are the first pass and are only as fresh as the last hand edit. This file remains the
source of the *declared* half — what the external projects are, why a finding matters, and
the ranked recommendations — none of which any config carries, which is why the generator
writes beside it rather than over it.

---

## Summary

| # | Subsystem | Benchmarked against | Verdict |
|---|---|---|---|
| 1 | Developer portal / catalog | Backstage, Cortex.io, Port, OpsLevel | **Gap** — catalog exists, no ownership, no scorecard |
| 2 | Observability / LLM telemetry | OpenTelemetry GenAI conventions, Langfuse | **Ahead** — already OTel-GenAI-native |
| 3 | Agent runtime & orchestration | LangGraph + Temporal, OpenAI Agents SDK | **Parity, with named gaps** |
| 4 | Security ops / threat analysis | TheHive Cortex, MISP, OpenCTI | **Gap** — no analyzer contract |
| 5 | RAG & knowledge graph | LlamaIndex, Haystack, Microsoft GraphRAG | **Parity** — retrieval strong, graph edges empty |
| 6 | Compliance & ATO | NIST OSCAL, compliance-trestle | **Ahead** — likely ICDEV's strongest differentiator |
| 7 | Delivery pipeline | Temporal, Argo, GitHub Actions | **Ahead on concept, weak on hygiene** |
| 8 | Data quality & lineage | OpenLineage, DataHub, Great Expectations | **Gap** — engines exist, largely unfed |
| 9 | LLM evaluation & red teaming | promptfoo, DeepEval, Giskard | **Gap** — 2 modules |
| 10 | IaC & infrastructure | Crossplane, Checkov, Atlantis | **Parity** |

Three of ten are areas where ICDEV is genuinely ahead of the commercial field. That is the
most important result in this document, and it should shape positioning as much as the gaps do.

---

## 1. Developer portal / catalog — **Gap**

**External.** The market has settled into a layered pattern: [Backstage](https://backstage.io)
is the open-source catalog *framework* (and needs a dedicated platform team to run),
with a scorecard product — [Cortex.io](https://www.cortex.io/) or
[OpsLevel](https://www.opslevel.com/) — layered on top. [Port](https://port.io) is the
no-code SaaS alternative. The recurring criticism of all the commercial ones is that they
are **portal-only, with no execution layer**: they surface a gap and wait for a human.

**ICDEV today.** `args/component_registry.yaml` registers **66 components** (36 canvases,
17 core extensions, 9 features, 4 child apps) and genuinely drives runtime registration,
nav, IQE dispatch and CLI toggles — a stronger catalog than most Backstage installs. But:
no owner/team/on-call field for any component anywhere; `developer_scorecards` exists with
A–F grading and five dimensions and has **0 rows, zero writers, zero readers**;
`kg_edges` is **0** against 2,432 nodes, so no blast radius is computable.

**Adapt.** Cortex.io's scorecard-as-code — ladder of ranked levels, weighted rules,
per-rule exemptions with approval, evaluation windows. Use **IQE as the rule language**
rather than inventing a DSL.

**Where ICDEV can beat the field.** The execution layer the commercial products lack. A
failing rule can become a kanban task and get *built*. That is the differentiator, and it
is not available to anyone selling a portal.

→ Tracked: card `idp` (14 tasks).

---

## 2. Observability / LLM telemetry — **Ahead**

**External.** The 2026 landscape converged on **OpenTelemetry GenAI semantic conventions**
(`gen_ai.*`) as the vendor-neutral baseline for LLM telemetry. The practical differentiator
is whether a tool is OTel-native or needs a proprietary SDK and a parallel instrumentation
path. [Langfuse](https://langfuse.com) is the open-source leader and is OTel-native.

**ICDEV today.** Already OTel-GenAI-native. `tools/observability/` has 12 modules including
a dedicated `genai_attributes.py` and `otel_tracer.py`; `tools/llm/router.py` — the main
LLM path, so effectively all routed calls — emits `gen_ai.*` spans, and
`tools/agent/bedrock_client.py` emits `gen_ai.operation.name`, `gen_ai.system`,
`gen_ai.request.model`. Plus W3C PROV-AGENT provenance and `/traces`, `/provenance`, `/xai`.

**Verdict.** No adaptation needed. This was benchmarked expecting a gap and there isn't
one — ICDEV independently landed on what the market standardised.

**Only real opportunity.** Because the telemetry is already standards-compliant, it could
be *exported* to any OTel backend rather than only rendered in ICDEV's own dashboards.
That is a config and packaging question, not an engineering one.

---

## 3. Agent runtime & orchestration — **Parity, with named gaps**

**External.** The dominant 2026 production pattern is two-layer: **LangGraph as the agent
brain, Temporal as the durable-execution muscle** — the inner loop does stateful reasoning,
the outer layer handles fault tolerance, long-running lifecycle and cross-service
coordination. LangGraph, CrewAI, Google ADK, OpenAI Agents SDK and Semantic Kernel all
implement variants of state-graph orchestration.

**ICDEV today.** Has both layers already, arrived at independently: `agent_loop.py` for the
inner loop and the **DWO (Durable Workflow Orchestration)** card, complete, for the outer.
`tools/workflow_hitl/` adds 20 modules of human-in-the-loop. Budgets are more mature than
expected — `max_total_tokens`, `max_cost_usd`, context/compression budgets, per-tool and
per-LLM-call timeouts, `stall_threshold`, `max_iterations`, all config-driven from
`args/llm_config.yaml`, with a `truncation_reason` taxonomy.

**Named gaps** (measured by grep over `agent_loop.py`): semantic loop detection (0 hits —
`stall_threshold` catches *no progress*, not steady progress through equivalent actions),
approval workflow (0 hits), session wall-clock ceiling (0 hits — per-call timeouts exist,
total duration does not). Plus no path/tool scoping in skill frontmatter, a good idea taken
from [xichan96/cortex](https://github.com/xichan96/cortex).

→ Tracked: card `ars` (6 tasks).

---

## 4. Security ops / threat analysis — **Gap**

**External.** [TheHive Project's Cortex](https://github.com/TheHive-Project/Cortex) —
39+ analyzers behind one REST API. The value is the **contract**: each analyzer declares
the observable types it accepts, runs containerized under a per-analyzer rate limit, and
emits a taxonomy-tagged report. [MISP](https://www.misp-project.org/) and
[OpenCTI](https://www.filigran.io/opencti) are the adjacent standards for threat-intel
sharing and structured CTI.

**ICDEV today.** ~79 analyzer-shaped modules across `tools/strategos/`, `tools/security/`
and `tools/supply_chain/` — and **no shared base class among them**. Every feed, importer,
scorer and triage path is hand-wired; the outputs share no vocabulary. The containerization
half already exists as `sandbox_execute` (Docker, resource limits, network isolation).

**Adapt.** The contract, declared as data rather than as a base class — 79 modules will not
all be refactored, and a contract requiring a rewrite to adopt does not get adopted.

**Worth a later look.** MISP/STIX taxonomy alignment, so ICDEV's threat output is
interoperable rather than bespoke. Not scoped yet.

→ Tracked: card `anz` (5 tasks).

---

## 5. RAG & knowledge graph — **Parity**

**External.** [LlamaIndex](https://www.llamaindex.ai/) and
[Haystack](https://haystack.deepset.ai/) for retrieval pipelines;
[Microsoft GraphRAG](https://github.com/microsoft/graphrag) for graph-augmented retrieval.

**ICDEV today.** Strong: `tools/rag/` has 38 modules, `tools/knowledge_graph/` 16, with
two-stage retrieval (vector + re-rank), CRAG corrective loops, RRF fusion, adaptive
chunking, tiered retention and PG-native GraphRAG. The Cortex facade fans out across four
backends in parallel with per-backend timeouts. This is a mature subsystem.

**The gap is not retrieval, it is the graph.** `kg_edges` = 0 for the self-awareness graph;
`derive_edges()` implements one heuristic and defers the rest. GraphRAG's premise is that
*relationships* carry the signal, and ICDEV has nodes without them.

**Adapt.** GraphRAG's community-detection and hierarchical-summary approach, once edges
exist. Sequenced behind `idp-cat-02`, which creates them.

---

## 6. Compliance & ATO — **Ahead**

**External.** [NIST OSCAL](https://pages.nist.gov/OSCAL/) is the standard;
[compliance-trestle](https://github.com/oscal-compass/compliance-trestle) is the reference
tooling. Commercial GRC platforms are largely document-management with workflow.

**ICDEV today.** OSCAL generate/validate/convert/profile-resolve, FedRAMP 20x KSI, CMMC,
eMASS and Xacta sync, STIG checking, SSP/POAM/SBOM generation, a multi-framework crosswalk
engine, cATO monitoring, IL4–IL6 classification handling, and append-only NIST AU audit.

**Verdict.** Nothing in the open-source or commercial field does this breadth with this
depth of DoD specificity. **This is likely ICDEV's strongest genuine differentiator** and
the benchmark found no external project worth adapting *from*.

**Implication for positioning.** The IDP and scorecard work should treat compliance posture
as a first-class scorecard dimension — it is the thing ICDEV can grade that competitors
cannot.

---

## 7. Delivery pipeline — **Ahead on concept, weak on hygiene**

**External.** [Temporal](https://temporal.io) for durable execution,
[Argo Workflows](https://argoproj.github.io/workflows/) for Kubernetes-native pipelines.
None of them close the loop from *detected gap* to *merged fix*.

**ICDEV today.** Genuinely novel: kanban → worktree → build → PR → CI → merge-verified done,
with `kanban_tasks` at 2,586 rows and `kanban_verifications` at 2,372 — a real, dated build
ledger. Cortex.io's 2026 positioning is "mission control for the AI software factory";
ICDEV *operates* one.

**One hygiene problem, measurably so.** 109 registered git worktrees, recursively nested,
several locked — creation is bounded, reclamation is not. Re-measured hours later: **117**.
The leak is ongoing.

**Correction — the verification-bypass finding was wrong.** The first pass of this document
reported "59% of `kanban_verifications` are `bypassed`, because the gates are non-blocking
unless `KANBAN_PIPELINE_ENFORCE=1`". That aggregate is real but it is *historical*, and the
stated cause was false: `KANBAN_PIPELINE_ENFORCE=1` **is** set in `.env`. Broken down by
month:

| Month | Verifications | Bypassed |
|---|---|---|
| 2026-06 | 1,991 | 1,321 (66%) |
| 2026-07 | 297 | 70 (24%) |
| 2026-08 | 86 | **0 (0%)** |

Enforcement is working. August shows zero bypasses, and 32 of 86 verifications **failed** —
the gate is biting rather than waving work through. The 59% figure was dominated by June,
before the flag was set, and quoting the lifetime aggregate as a current state
misrepresented a subsystem that had already been fixed.

Worth keeping as a method note: a lifetime aggregate over a ledger that spans a policy
change describes the policy change, not the present.

**Adapt.** Nothing external. The one real fix is worktree reclamation — `ars-wt-01`. The
bypass rate needs no decision; it was already made.

---

## 8. Data quality & lineage — **Gap**

**External.** [OpenLineage](https://openlineage.io/) is the emerging lineage standard,
[DataHub](https://datahubproject.io/) and [OpenMetadata](https://open-metadata.org/) for
catalog and governance, [Great Expectations](https://greatexpectations.io/) for
declarative data quality.

**ICDEV today.** `tools/data_canvas/` has 18 modules — profiler, quality engine, anomaly
detector, freshness guardian, PII scanner — plus a data-mesh model with domains, products
and contracts. The *engines* are there.

**The gap is data, not code.** `dm_domains` holds 4 rows and `dm_data_products` 8; the
`cf_applications` and `mc_app_inventory` inventories are empty. Sophisticated machinery
with nothing running through it.

**Adapt.** OpenLineage's event spec, so lineage is emitted in a standard format rather than
a bespoke one — cheap, and it makes the existing engines interoperable.

---

## 9. LLM evaluation & red teaming — **Gap**

**External.** [promptfoo](https://promptfoo.dev) for eval-as-config and CI gating,
[DeepEval](https://github.com/confident-ai/deepeval) for 50+ research-backed metrics
(faithfulness, relevance, safety), [Giskard](https://www.giskard.ai/) for vulnerability
scanning. The 2026 norm is evals in CI, gating releases.

**ICDEV today.** `tools/evaluation/` has **2 modules** against 38 in `tools/rag/` — the
thinnest major subsystem measured. There is ATLAS red-teaming and an OWASP-agentic
assessor, so *security* evaluation exists, but not systematic *quality* evaluation.

**Why this matters disproportionately.** ICDEV ships LLM-generated artifacts into compliance
contexts. The TRUST chain checks grounding and citations per call, but nothing measures
whether output quality is regressing release over release. The CXO card found the
provenance gate had been silently failing since inception — that class of defect is exactly
what a standing eval suite catches.

**Adapt.** promptfoo's eval-as-config model with CI gating. Fits ICDEV's args-over-code
convention directly, and `context/iqe/queries/` already establishes the pattern of
config-declared test corpora.

---

## 10. IaC & infrastructure — **Parity**

**External.** [Crossplane](https://www.crossplane.io/) for control-plane IaC,
[Checkov](https://www.checkov.io/)/[Terrascan](https://runterrascan.io/) for policy
scanning, [Atlantis](https://www.runatlantis.io/) for PR-driven Terraform.

**ICDEV today.** IDC canvas with Terraform/Pulumi emitters, importers for `tf_state`,
`pulumi_state` and AWS Resource Groups Tagging, Ansible execution, K8s deploy, Helm charts,
Iron Bank generation/validation, SLSA and SBOM. Comparable coverage with better compliance
integration.

**Adapt.** Nothing urgent. Checkov's policy-pack model is the closest thing worth borrowing
if IaC policy scanning becomes a priority.

---

## Recommendations, ranked

1. **Say the ahead parts out loud.** Compliance/ATO, the closed delivery loop, and
   OTel-native LLM telemetry are three areas where ICDEV leads the commercial field. That
   is a positioning asset and it is currently undocumented.
2. **`idp` first among the gaps** — it is the largest surface, it is half-built already
   (`developer_scorecards`, `awareness_component_health`, IQE), and it is the one that makes
   every other subsystem's health legible.
3. **Evaluation is the most underweight subsystem relative to risk.** 2 modules guarding
   LLM output that lands in compliance artifacts. Cheap to start with promptfoo's model.
4. **Reclaim leaked worktrees.** 117 registered and climbing, recursively nested — the one
   hygiene problem that genuinely undercuts the strongest differentiator in the list. The
   verification-bypass rate is *not* a second one; see the correction in §7.
5. **`anz` and `ars` are well-scoped and independent** — they can run whenever there is
   capacity.
6. **Defer** RAG/GraphRAG community detection (blocked on edges), MISP/STIX alignment, and
   IaC policy packs.

## What this pass does not cover

Individual canvases and engines (~25+), which is the exhaustive sweep. This map should
direct it: the sweep is most likely to pay off in **data canvases** (engines built, no data)
and **evaluation surfaces**, and least likely to pay off in compliance and observability,
which this pass found healthy.

---

# Appendix A — Promotion path: how a finding in this map becomes a card

Research note for `xbm-promote-01-d1`, written 2026-08-07 against
`tools/innovation/kanban_promoter.py`, `tools/awareness/suggested_card_writer.py`,
and `tools/kanban/task_factory.py` at commit `bc35e50bb`.

**Sequencing caveat.** This card was scheduled as the research that precedes
`xbm-promote-01` wiring, but the wiring shipped first (commit `10836c035`, merged via
PR #1301 on 2026-08-06). What follows therefore describes the promoter **as it now
stands**, and states the orphan condition as history rather than as the current state.

## A.1 `kanban_promoter.py` — structure, and what "orphaned" meant

Pipeline, in `promote_findings_to_kanban()`, `dry_run=True` by default:

| Stage | Function | Behaviour |
|---|---|---|
| 1. Query | `find_promotable_signals` (:312) | SQL over `innovation_signals` LEFT JOIN `innovation_solutions`; filters `triage_result`, `source_type`, `innovation_score >= min`, and `id NOT IN (SELECT source_prediction_id FROM kanban_tasks)`. `limit` is a query-size guard, **not** the rate limit. |
| 2. Gap gate | `classify_signals` (:196) | Maps each signal to a subsystem (`metadata.subsystem` first, then `category`), reads that subsystem's verdict from `args/innovation_promoter.yaml`, keeps only verdicts in `gap_verdicts`. Unmapped and non-gap signals are counted and reported, never silently dropped. |
| 3. Caps | `apply_caps` (:242) | `max_per_subsystem` then `max_per_run`, applied to a score-descending sort. |
| 4. Write | `promote_signals` (:470) | Builds specs via `build_kanban_task` (:354), re-asserts `status == 'suggested'`, then calls `task_factory.create_tasks`. |
| 5. Audit | `_write_audit` (:434) | Best-effort append-only `audit_trail` row; failures are logged, never raised. |

The verdicts in `args/innovation_promoter.yaml` are a **hand transcription of the summary
table above**. There is no automatic link — editing a verdict in §1–§10 does not change
what the promoter treats as a gap. Note also that `ahead_weak_hygiene` (§7) is deliberately
*not* in `gap_verdicts`, because those hygiene gaps are already tracked by live `kpr`/`tch`
cards.

**The orphan condition.** The module was complete and correct but *reachable by nothing*:
no reflex, no MCP tool registration, no scheduler entry, no goal, no
CLAUDE.md/commands.md line. Its only inbound reference was its own test file. A tool that
nothing calls does not run, so approved benchmark findings accumulated in
`innovation_signals` and never became work.

**Current wiring (post-`10836c035`).** Two entry points, both bounded:

* *Operator CLI* — `goals/innovation_to_kanban.md`, `docs/reference/commands.md:2389-2392`,
  `.claude/plans/external-repo-adaptation-scan.md:99`. Writing requires `--promote`; the
  bare invocation is a preview.
* *Scout reflex* — `tools/genesis/reflexes/scout.py::_promote_findings` (:288) calls
  `promote_findings_to_kanban()` and never raises, so a promotion failure cannot wedge the loop.
  It is gated on `promotion.enabled` in `args/scout_config.yaml:217`, which is **`false`
  today**. So the autonomous path exists but is off: nothing writes cards unattended until
  someone flips that flag. Note that when flipped, the shipped `dry_run: false` beneath it
  means the reflex writes on its first pass — the enable flag is the only thing standing
  between the scout and live card creation. There is still no MCP registration.

## A.2 `suggested_card_writer.py` — the pattern being mirrored

The awareness writer is the reference implementation for "machine proposes, human
disposes". Three properties carry over; one deliberately does not.

* **`status='suggested'` is a hard ceiling.** Cards land in `suggested`; only an operator
  action (board move, or `--promote-all`) reaches `backlog`. Nothing dispatchable is
  created without a human. `kanban_promoter` enforces this twice — the `SUGGESTED_STATUS`
  constant and a `ValueError` raised in `promote_signals` if any spec's status differs
  (:480-485), because `task_factory` will happily insert whatever status it is handed.
* **Layered dedup.** Open-card by `source_prediction_id`, then by exact title, then
  subject-level `(prediction_type, subject_id)` against all `OPEN_STATUSES`
  (`backlog/scheduled/in_progress/suggested`), plus a re-verify step that re-runs the gap
  rule and drops predictions whose gap has since been fixed.
* **Volume control by consolidation, not by a cap.** >N findings for one rule become one
  batch card (`consolidation.threshold`, default 5), and cards idle in `suggested` past
  `auto_dismiss.stale_days` (default 30) are auto-dismissed. There is no per-run ceiling.
  `kanban_promoter` chose explicit caps instead — see A.3.

**Divergence worth knowing:** `suggested_card_writer._insert_card` (:485) writes
`kanban_tasks` with a **raw INSERT**, not `task_factory.create_tasks`, and so has no
`idempotency_key` at all — its idempotency comes entirely from the query-side dedup plus
marking `oracle_predictions.outcome = 'promoted:<task_id>'`. `kanban_promoter` does *not*
copy that; it goes through the factory. Do not treat the awareness writer's INSERT as the
pattern to reuse.

## A.3 Cap semantics — the exact evaluation order

`apply_caps` sorts by `innovation_score` descending (missing score sorts as `0.0`), then
walks the list once:

1. If `per_subsystem[subsystem] >= max_per_subsystem` → drop to `dropped_by_subsystem_cap`,
   `continue`. **This check runs first**, so a signal held back by a full subsystem never
   consumes run-cap budget and is never attributed to the run cap.
2. Else if `len(kept) >= max_per_run` → drop to `dropped_by_run_cap`.
3. Else keep, and increment that subsystem's counter.

Consequences to reason with, not around:

* Defaults are `max_per_run: 5`, `max_per_subsystem: 2`. Effective ceiling per run is
  `min(5, 2 × number_of_gap_subsystems)`. With five gap-verdict subsystems configured
  today (`developer_portal`, `agent_runtime`, `security_ops`, `data_lineage`,
  `evaluation`), the binding constraint is `max_per_run`.
* **Caps defer, they do not discard.** Dropped signals are untouched in
  `innovation_signals` and are re-queried next run. Truncation is logged at WARNING and
  surfaced as `truncated: true` with per-id drop lists — the stated rationale is that a
  cap which silently slices is indistinguishable from a promoter that found nothing.
* Caps are applied **after** the gap gate, on purpose: capping at query time would let
  non-gap findings eat the budget.
* Caps are **per invocation**, with no cross-run or time-window memory. Two runs in one
  hour create up to 10 cards.

## A.4 `task_factory.create_tasks` — the exact call contract

```python
from tools.kanban.task_factory import create_tasks
created: list[str] = create_tasks(task_specs: list[dict])
```

Returns **only the ids actually inserted** — so `len(specs) - len(created)` is the skipped
duplicate count, which is precisely how `promote_signals` computes `skipped_existing`
(:509). Calls `init_kanban_tables()` first, opens one connection, inserts each spec, then a
single `commit()`; any exception rolls back and **re-raises**.

Recognised spec keys (defaults in parentheses): `id` **(required — a spec with no id is
warned and skipped)**, `title` (`"Untitled task"`, truncated to 255), `description` (`""`),
`task_type` (`build`), `priority` (`high`), `status` (`backlog`), `depends_on_task_id`,
`source_prediction_id`, `source_doc_id`, `source_collection_id`, `dispatch_source`
(`dic_notebook`), `idempotency_key`, `max_retries` (`5`), `max_runtime_seconds`,
`loop_type` (`deterministic`), `adversarial_enabled` (`0`), `acceptance_criteria`.
`executor_type` is **not** settable through the factory — the column default
(`claude_cli`) applies.

Dedup is two sequential SELECTs per spec: existing `id`, then existing `idempotency_key`
if one is supplied. `kanban_promoter` feeds both from the signal id and nothing else:

```python
stable_task_id(sid)  -> f"task-innov-{sha256(sid)[:10]}"
idempotency_key(sid) -> f"innovation-promoter:{source_table}:{sid}"
```

Both are derived from the signal id, never from the clock — a timestamp-seeded id makes
every re-run look novel and defeats the dedup entirely. Re-running the promoter over the
same findings therefore creates nothing.

**Known limit — dedup is advisory, not enforced.** `kanban_tasks.idempotency_key` is a
plain `TEXT` column with a non-unique index (`tools/kanban/init_db.py:46,160`). The check
is read-then-write inside one transaction with no unique constraint behind it, so two
concurrent promoter runs can both read "absent" and both insert. In practice the `id`
collision catches this (the id is equally deterministic and `id` *is* the primary key), so
the second insert fails the transaction rather than creating a duplicate card — but the
idempotency key alone is not what saves it.

## A.5 The contract as one SELECT — which rows are actually gap-verdict findings

Research note for `xbm-promote-01-d2`, 2026-08-07, measured against the live PostgreSQL
instance. A.1–A.4 describe the pipeline; this section answers the narrower question a
reviewer actually asks — *which rows in `innovation_signals` are benchmark findings with a
real gap verdict, and what card would each become?*

That answer was previously spread across one SQL query, one YAML file and three Python
functions. It is now also stated as a single reviewable SELECT:
`PROMOTION_CONTRACT_SQL` in `tools/innovation/kanban_promoter.py`, runnable read-only via
`python tools/innovation/kanban_promoter.py --contract-sql`.

**Scope of the source table.** `innovation_signals` holds 1,179 rows; only 79 carry a
benchmark `source_type`, and only **11** of those are `approved` — every one of the 11
scores ≥ 0.5, so the score gate does no filtering on today's data. The other 68 are
`blocked` (64), `suggested` (3) and `logged` (1). `innovation_trends` and
`innovation_competitor_scans` are deliberately **not** joined: neither carries a
`signal_id`, so neither can narrow or enrich a per-finding row. `innovation_solutions` is a
LEFT JOIN — it supplies `spec_content` and `estimated_effort` to the card body when a spec
exists, and must not drop a finding when one does not.

**The gap gate, measured.** Of the 11 approved findings, **8** carry a gap verdict:

| Category | → subsystem | Verdict | Count |
|---|---|---|---|
| `developer_experience` | `developer_portal` | `gap` | 3 |
| `ai_tooling`, `workflow`, `architecture` | `agent_runtime` | `parity_with_named_gaps` | 5 |

The 3 excluded are excluded for stated reasons, not silently: `performance` and `ui` map to
no benchmark subsystem at all, and `knowledge` maps to `rag_knowledge_graph`, whose verdict
is `parity` (§5). The SQL constant and `classify_signals()` were run side by side over the
same rows and agree exactly — same ids, subsystems, verdicts, task ids and priorities.

**`metadata.subsystem` is currently dead weight.** `resolve_subsystem` prefers it over
`category` (A.1), but the column is **NULL on all 11** approved findings, so classification
runs entirely on `category` today. The SQL constant therefore reproduces only the category
path; a tagged signal could classify differently in Python than in the constant, which is
one of the reasons the constant is a diagnostic and not the gate.

**Idempotency, verified across the language boundary.** PostgreSQL's
`substr(encode(sha256(s.id::bytea),'hex'),1,10)` and Python's `hashlib.sha256(...)` produce
identical task ids on live rows, so the SQL restatement of `stable_task_id` is exact rather
than approximate. Both keys derive from the signal id alone — never the clock.

**Why the query reports `already_promoted` instead of filtering on it.** All 8 gap-verdict
findings already have cards, from the predecessor `genesis_scheduler` path described in
A.1. Applied as the runtime's `NOT IN` anti-join the query returns **zero** rows, which is
correct rather than broken. Exposing the check as a column keeps the population visible, so
a future reader who sees `candidates: 0` can tell working dedup from an empty table —
without loosening the filters and re-promoting eleven finished cards.

**The cost of writing it down.** The verdict map now exists in three places: this document,
`args/innovation_promoter.yaml`, and the SQL constant. The YAML remains the single source
of truth for the runtime gate; `tests/innovation/test_kanban_promoter.py` pins the constant
to it — category map, section numbers, gap-verdict list, scope filters, score thresholds
and both key derivations — so drift fails a test rather than misleading a reviewer.
