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

**But the hygiene is poor, and measurably so.** 109 registered git worktrees, recursively
nested, several locked — creation is bounded, reclamation is not. And **59% of
`kanban_verifications` are `bypassed`**, because the pipeline gates are non-blocking unless
`KANBAN_PIPELINE_ENFORCE=1`. A verification ledger that bypasses more than half its checks
is recording activity, not enforcing quality.

**Adapt.** Nothing external. Fix the hygiene — that is `ars-wt-01`, and the bypass rate
deserves its own decision.

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
4. **Fix pipeline hygiene before scaling the pipeline.** 109 leaked worktrees and a 59%
   verification-bypass rate undercut the strongest differentiator in the list.
5. **`anz` and `ars` are well-scoped and independent** — they can run whenever there is
   capacity.
6. **Defer** RAG/GraphRAG community detection (blocked on edges), MISP/STIX alignment, and
   IaC policy packs.

## What this pass does not cover

Individual canvases and engines (~25+), which is the exhaustive sweep. This map should
direct it: the sweep is most likely to pay off in **data canvases** (engines built, no data)
and **evaluation surfaces**, and least likely to pay off in compliance and observability,
which this pass found healthy.
