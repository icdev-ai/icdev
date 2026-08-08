# External Benchmark Map — generated

CUI // SP-CTI

<!-- GENERATED FILE. Do not edit by hand: regenerate with `python tools/innovation/benchmark_report.py --write`.
     Edit the sources instead — args/xbm_subsystem_inventory.yaml, args/innovation_promoter.yaml, context/genesis/competitors.yaml, docs/research/external-benchmark-map.md. -->

Every verdict below is produced by `tools/innovation/benchmark_report.py` from the four sources named at the
foot of this document. The hand-written companion, [docs/research/external-benchmark-map.md](external-benchmark-map.md),
stays the human reading that supplies the *declared* half; this file is that reading
re-checked against the tree on every run.

**What is measured and what is declared.** Module counts are globs over this checkout,
so they move when the tree moves. The external half is *declared* — no tool here clones
Backstage and counts its features — and it is dated by whoever last edited the map. The
measured half can overrule the declared one in **both** directions: a declared gap whose
closing condition is now satisfied is reported retired, and a declared lead that has
fallen below its module floor becomes a gap.

**Row counts were not measured.** This file is generated offline so it reproduces byte-for-byte anywhere, which is what lets CI diff it. Offline, nothing retires a declared finding — the conservative reading is the one that gets committed. `--live` opens the database and re-checks every closing condition.

---

## Summary

15 subsystems: ahead=1, gap=4, no_adaptation_needed=3, parity=4, not_comparable=3

| # | Subsystem | Benchmarked against | Position | Verdict | Modules | Outstanding |
|---|---|---|---|---|---|---|
| 1 | Developer portal / catalog | Backstage, Cortex.io, Port, OpsLevel | Behind | **Gap** | 48 | 2 |
| 2 | Observability / LLM telemetry | OpenTelemetry GenAI conventions, Langfuse | Ahead | **Ahead, with items outstanding** | 31 | 1 |
| 3 | Agent runtime & orchestration | LangGraph, Temporal, OpenAI Agents SDK | Parity | **Parity, with named work** | 117 | 9 |
| 4 | Security ops / threat analysis | TheHive Cortex, MISP, OpenCTI | Behind | **Gap** | 153 | 1 |
| 5 | RAG & knowledge graph | LlamaIndex, Haystack, Microsoft GraphRAG | Parity | **Parity, with named work** | 61 | 4 |
| 6 | Compliance & ATO | NIST OSCAL, compliance-trestle | Ahead | **No adaptation needed** | 98 | 0 |
| 7 | Delivery pipeline | Temporal, Argo, GitHub Actions | Ahead | **No adaptation needed** | 106 | 0 |
| 8 | Data quality & lineage | OpenLineage, DataHub, Great Expectations | Behind | **Gap** | 18 | 1 |
| 9 | LLM evaluation & red teaming | promptfoo, DeepEval, Giskard | Behind | **Gap** | 11 | 1 |
| 10 | IaC & infrastructure | Crossplane, Checkov, Atlantis | Parity | **No adaptation needed** | 45 | 0 |

Subsystems the benchmark map has not covered:

| # | Subsystem | Benchmarked against | Position | Verdict | Modules | Outstanding |
|---|---|---|---|---|---|---|
| — | Genesis / autoresearch loops | — not benchmarked — | Not positioned | **Parity, with named work** | 54 | 2 |
| — | Generated-UI and presentation quality | — not benchmarked — | Not positioned | **Parity, with named work** | 53 | 1 |
| — | SparkPilot / RTOS | — not benchmarked — | Not positioned | **Not comparable** | 0 | 1 |
| — | Local model serving | — not benchmarked — | Not positioned | **Not comparable** | 69 | 0 |
| — | Model lifecycle | — not benchmarked — | Not positioned | **Not comparable** | 33 | 0 |

---

## 1. Developer portal / catalog — **Gap**

**External.** Backstage, Cortex.io, Port, OpsLevel

| Project | Tracking | What it is |
|---|---|---|
| [backstage/backstage](https://github.com/backstage/backstage) | watched | Internal developer portal — golden path pattern. The open-source catalog framework the commercial scorecard products layer on top of. |
| [Cortex.io](https://www.cortex.io/) | manual review | Commercial SaaS scorecard product — no public product repo, so there is nothing to watch programmatically. Review docs and changelog by hand when the idp card needs a scorecard-as-code reference. Source of the scorecard ladder ICDEV is adapting: ranked levels, weighted rules, per-rule exemptions with approval. |
| [OpsLevel](https://docs.opslevel.com/) | manual review | Commercial service-maturity scorecard SaaS — no public product repo. Manual doc review. Same portal-only limitation as Cortex.io: it surfaces a gap and waits for a human, where ICDEV can turn a failing rule into a kanban task. |
| [Port](https://docs.port.io/) | manual review | Commercial no-code IDP SaaS — no public product repo. port-labs publishes a docs site and integration clients, not the platform, so there is no release stream that tracks the product. Manual doc review; the blueprint/entity model is the part worth reading. |
| [shadcn-labs/agentcn](https://github.com/shadcn-labs/agentcn) | watched | Open-source collection of production-ready AI agent templates distributed as CLI recipes with zero install configuration. Each template ships complete: instructions, tool definitions, workflow steps. IDE integrations for Cursor and VS Code. Built on Next.js + TypeScript + shadcn/ui + Radix. 'npx agentcn add <name>' model. Adaptation: simplify ICDEV /initialize to 3 commands; kanban starter card library modeled on recipe distribution; Cursor dev-profile bridge improvement. |

**ICDEV today.** developer_scorecards ships A-F grading across five dimensions and holds 0 rows, with zero writers and zero readers.

Measured: **48 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `args/component_registry.yaml`.

**Verdict.** Gap; position **Behind** — the benchmark pass found ICDEV behind and nothing has retired that.

- benchmark pass declared behind
- code_state=built, data_state=not_assessed
- the declared finding has not been retired by measurement

**Adapt.**

- Cortex.io's scorecard-as-code — a ladder of ranked levels, weighted rules, per-rule exemptions with approval, and evaluation windows, with IQE as the rule language rather than a new DSL.  *(docs/research/external-benchmark-map.md)*
- agentcn — Zero-Config AI Agent Templates (shadcn model)  *(watchlist:shadcn-labs/agentcn)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## 2. Observability / LLM telemetry — **Ahead, with items outstanding**

**External.** OpenTelemetry GenAI conventions, Langfuse

| Project | Tracking | What it is |
|---|---|---|
| [cortexproject/cortex](https://github.com/cortexproject/cortex) | watched | Horizontally scalable long-term Prometheus storage. Note the name collision — unrelated to both ICDEV Cortex and TheHive Cortex. |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | watched | Open-source LLM observability leader, OTel-native. Closest external analogue to ICDEV's /traces and /provenance surfaces. |
| [open-telemetry/semantic-conventions](https://github.com/open-telemetry/semantic-conventions) | watched | Home of the gen_ai.* GenAI semantic conventions. ICDEV is already OTel-GenAI-native, so this is a conformance watch rather than a gap watch — it tells us when the spec moves under us. |
| [SigNoz/signoz](https://github.com/SigNoz/signoz) | watched | Open-source APM, traces, metrics, logs |
| [yeet-src/httpinspect](https://github.com/yeet-src/httpinspect) | watched | Terminal dashboard for live inter-service HTTP traffic captured at kernel level via eBPF/TCX — no proxy, no app modifications, no restarts. Requires Linux 6.6+ with BTF and Clang. Real-time endpoint metrics (request rate, latency, status codes) in a `top`-like TUI. Adaptation: ICDEV monitoring canvas zero-instrumentation layer for IL5/IL6 air-gapped deployments where inserting a proxy is prohibited; informs ZTA network segmentation canvas traffic analysis without sidecar overhead. |

**ICDEV today.** Already OTel-GenAI-native. tools/observability/ carries a dedicated genai_attributes.py and otel_tracer.py, and tools/llm/router.py — the path effectively all routed calls take — emits gen_ai.* spans.

Measured: **31 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/observability/`.

**Verdict.** Ahead, with items outstanding; position **Ahead** — ICDEV leads and there is still something worth taking.

- benchmark pass declared ahead
- code_state=built, data_state=not_assessed
- 1 item(s) still outstanding

**Adapt.**

- httpinspect — Zero-Instrumentation eBPF HTTP Monitor  *(watchlist:yeet-src/httpinspect)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## 3. Agent runtime & orchestration — **Parity, with named work**

**External.** LangGraph, Temporal, OpenAI Agents SDK

| Project | Tracking | What it is |
|---|---|---|
| [anthropics/claude-code](https://github.com/anthropics/claude-code) | watched | Our runtime platform |
| [chopratejas/headroom](https://github.com/chopratejas/headroom) | watched | Content-aware compression layer (SmartCrusher, CodeCompressor) that reduces token usage 60-95% for AI agent workflows. Provides MCP server, HTTP proxy, and library interfaces. Includes CacheAligner for KV cache hits, cross-agent memory deduplication, and failure mining (headroom learn). Integration point: tools/llm/router.py compression layer, genesis daemon A2A message compression. Python library (pip install headroom). |
| [Forward-Future/loopy](https://github.com/Forward-Future/loopy) | watched | Platform providing named 'loops' — iterative, feedback-driven workflows with explicit before/after examples and measurable success criteria. Agents run a loop, receive structured feedback, and refine until criteria are met. Safety-first design: no auto-scheduling, no production changes without HITL gate. Skill distribution via npx. Registry-driven loop catalog. Supports Codex, Cursor, Claude Code. Adaptation: agent_loop.py structured refinement stages with named loop taxonomy; goals/manifest.md before/after examples; kanban starter card library. |
| [iii-hq/iii](https://github.com/iii-hq/iii) | watched | Runtime platform that eliminates point-to-point service integrations via auto-discovery and a unified function/trigger interface. Workers spawn workers. Built-in distributed tracing across TypeScript, Python, Rust, Go. Zero-integration architecture. Maps to ICDEV's 15-agent A2A system (ports 8443-8458): the unified trigger model could replace JSON-RPC 2.0 point-to-point wiring. Observability consolidation would unify scheduler.ndjson, .logs, audit_trail. Adaptation: extract architectural patterns for tools/agents/a2a_registry.py; multi-language support is a significant integration lift. |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | watched | State-graph agent orchestration — the 'agent brain' half of the dominant two-layer production pattern. ICDEV's counterpart is agent_loop.py. |
| [microsoft/autogen](https://github.com/microsoft/autogen) | watched | Multi-agent conversation framework |
| [modem-dev/sideshow](https://github.com/modem-dev/sideshow) | watched | Live browser view for coding agents: renders HTML, Mermaid diagrams, diffs, code, and images directly from agent stdout in real-time. Threaded user feedback lets agents iterate without full re-runs. MCP-integrated, built on Solid.js + Hono + Cloudflare Workers. Key patterns: composable output cards, streaming render pipeline, in-browser feedback threading. Adaptation: ACE /coworker canvas needs real-time visual output rendering and a threaded feedback loop for agent refinement cycles. |
| [nousresearch/hermes-agent](https://github.com/nousresearch/hermes-agent) | watched | Self-improving AI assistant (NousResearch) with learning loop, persistent memory via FTS5 session history + LLM summarization, autonomous skill creation, multi-platform messaging (Telegram, Discord, Slack, CLI), 40+ tool support + MCP, and Honcho dialectic user modeling. Six terminal backends (local/Docker/SSH/Modal). Relevant patterns: FTS5 full-text session search for tools/memory/hybrid_search.py, skill auto-generation for Continuous Harness, multi-platform messaging for ICDEV notifications beyond current PushNotification MCP. |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | watched | OpenAI Agents SDK — handoffs, guardrails and sessions. Watch for the approval-workflow primitive ICDEV's agent loop currently lacks. |
| [temporalio/temporal](https://github.com/temporalio/temporal) | watched | Durable execution — the 'muscle' half of the same two-layer pattern; ICDEV's counterpart is the DWO card. Also benchmarks delivery_pipeline (§7). |

**ICDEV today.** Both layers of the 2026 two-layer pattern are already present — the agent_loop.py inner loop and the completed DWO card for durable outer execution — with budgets more mature than expected.

Measured: **117 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/ace/`.

**Verdict.** Parity, with named work; position **Parity** — ICDEV's side clears its floor with named work outstanding.

- benchmark pass declared parity
- code_state=built, data_state=not_assessed
- 9 item(s) still outstanding

**Adapt.**

- Semantic loop detection — stall_threshold catches no progress, not steady progress through equivalent actions (0 hits).  *(docs/research/external-benchmark-map.md)*
- Approval workflow in the agent loop (0 hits).  *(docs/research/external-benchmark-map.md)*
- Session wall-clock ceiling — per-call timeouts exist, total duration does not (0 hits).  *(docs/research/external-benchmark-map.md)*
- Path/tool scoping in skill frontmatter, taken from xichan96/cortex.  *(docs/research/external-benchmark-map.md)*
- Headroom — Reversible Context Compression for AI Agents  *(watchlist:chopratejas/headroom)*
- Loopy — Structured Feedback-Driven Workflow Loops for AI Agents  *(watchlist:Forward-Future/loopy)*
- iii — Unified Function+Trigger Runtime for Service Composition  *(watchlist:iii-hq/iii)*
- Sideshow — Real-Time Agent Output Rendering Panel  *(watchlist:modem-dev/sideshow)*
- Hermes Agent — Self-Improving Agent with Persistent Memory  *(watchlist:nousresearch/hermes-agent)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## 4. Security ops / threat analysis — **Gap**

**External.** TheHive Cortex, MISP, OpenCTI

| Project | Tracking | What it is |
|---|---|---|
| [anchore/grype](https://github.com/anchore/grype) | watched | Vulnerability scanner for container images |
| [aquasecurity/trivy](https://github.com/aquasecurity/trivy) | watched | Comprehensive security scanner |
| [MISP/MISP](https://github.com/MISP/MISP) | watched | Threat-intel sharing platform. The taxonomy and galaxy vocabulary is the interoperability target for ICDEV's analyzer output. |
| [OpenCTI-Platform/opencti](https://github.com/OpenCTI-Platform/opencti) | watched | Structured CTI platform, STIX2-native. Adjacent standard for making ICDEV threat output interoperable rather than bespoke. |
| [TheHive-Project/Cortex](https://github.com/TheHive-Project/Cortex) | watched | 39+ analyzers behind one REST API. The value is the contract, not the analyzers — declared observable types, per-analyzer rate limit, taxonomy-tagged report. Direct source for the anz card. |

**ICDEV today.** ~79 analyzer-shaped modules across tools/strategos/, tools/security/ and tools/supply_chain/ with no shared base class among them; every feed, importer, scorer and triage path is hand-wired and the outputs share no vocabulary.

Measured: **153 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/analyzers/`.

**Verdict.** Gap; position **Behind** — the benchmark pass found ICDEV behind and nothing has retired that.

- benchmark pass declared behind
- code_state=built, data_state=not_assessed
- the declared finding has not been retired by measurement

**Adapt.**

- TheHive Cortex's analyzer contract — declared observable types, a per-analyzer rate limit and a taxonomy-tagged report — expressed as data rather than as a base class, since 79 modules will not be rewritten to adopt one.  *(docs/research/external-benchmark-map.md)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.
- the declared finding has no measurable closing condition, so this tool cannot retire it — re-reading docs/research/external-benchmark-map.md §4 is a human step

---

## 5. RAG & knowledge graph — **Parity, with named work**

**External.** LlamaIndex, Haystack, Microsoft GraphRAG

| Project | Tracking | What it is |
|---|---|---|
| [deepset-ai/haystack](https://github.com/deepset-ai/haystack) | watched | The other major retrieval pipeline framework. Named alongside LlamaIndex in the benchmark map. |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | watched | Graph-augmented retrieval — community detection and hierarchical summaries. The technique ICDEV cannot use until kg_edges is non-empty; sequenced behind idp-cat-02. |
| [microsoft/markitdown](https://github.com/microsoft/markitdown) | watched | Python utility (pip install markitdown) converting PDF, DOCX, PPTX, XLSX, images (with LLM descriptions), audio transcription, HTML, and YouTube video URLs to Markdown for LLM ingestion. Modular plugin architecture with optional Azure Document Intelligence integration for layout-aware PDF extraction. Multiple API security levels. Directly addresses DIC's limited format support. Adaptation: tools/document_intelligence/converters/markitdown_adapter.py — additive wrapper that feeds MarkItDown output into DIC RAG pipeline. |
| [refactoringhq/tolaria](https://github.com/refactoringhq/tolaria) | watched | Desktop app (Tauri + React) for managing markdown-based knowledge bases. Files-first with git, plain markdown + YAML frontmatter, AI-agnostic (Claude, Codex, Gemini), keyboard-centric UI. Types as navigation (not enforcement). Offline and air-gap-safe by design. ICDEV already has tools/knowledge/ canvas; Tolaria's git-backed vault pattern and keyboard-shortcut philosophy could improve knowledge authoring DX and offline export for IL5/IL6 environments. Adaptation: context/ + hardprompts/ directory structure patterns; air-gap vault export. |
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | watched | Retrieval pipeline framework. ICDEV's tools/rag/ is at parity on retrieval; watch for ingestion and index-construction patterns. |
| [TriliumNext/Trilium](https://github.com/TriliumNext/Trilium) | watched | Open-source personal knowledge base (TypeScript/Node.js) scaling to 100k+ notes. Key patterns: hierarchical cloning (single note placed in multiple tree locations simultaneously), typed attribute system (labels + typed relation types), bidirectional relation maps, per-note AES encryption, self-hosted sync server, REST API + JS scripting automation, Excalidraw canvas sketching, Leaflet geo maps. Adaptation: Second Brain canvas note-cloning model for multi-context entity placement; KG entity multi-parent hierarchy; DIC per-document encryption; /components-map relation map visualization; RAG index sharding strategy for large corpora. |

**ICDEV today.** Retrieval is mature; the gap is the graph. kg_edges was 0 for the self-awareness graph and derive_edges() implemented one heuristic — nodes without the relationships GraphRAG's premise rests on.

Measured: **61 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/rag/`.

**Verdict.** Parity, with named work; position **Parity** — ICDEV's side clears its floor with named work outstanding.

- benchmark pass declared parity
- code_state=built, data_state=not_assessed
- 4 item(s) still outstanding

**Adapt.**

- Microsoft GraphRAG's community detection and hierarchical summaries, sequenced behind the edges existing.  *(docs/research/external-benchmark-map.md)*
- MarkItDown — Multi-Format to Markdown Converter (Microsoft)  *(watchlist:microsoft/markitdown)*
- Tolaria — Offline Git-Backed Markdown Knowledge Vault  *(watchlist:refactoringhq/tolaria)*
- Trilium — Hierarchical Knowledge Base with Relation Maps & Attribute System  *(watchlist:TriliumNext/Trilium)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## 6. Compliance & ATO — **Ahead, no adaptation needed**

**External.** NIST OSCAL, compliance-trestle

| Project | Tracking | What it is |
|---|---|---|
| [ComplianceAsCode/content](https://github.com/ComplianceAsCode/content) | watched | SCAP/STIG automation content |
| [oscal-compass/compliance-trestle](https://github.com/oscal-compass/compliance-trestle) | watched | NIST OSCAL tooling — direct competitor for compliance automation |
| [usnistgov/OSCAL](https://github.com/usnistgov/OSCAL) | watched | The OSCAL standard itself. Compliance is ICDEV's strongest differentiator, so this watch is defensive — schema changes are a compatibility risk, not an idea source. |

**ICDEV today.** OSCAL generate/validate/convert/profile-resolve, FedRAMP 20x KSI, CMMC, eMASS and Xacta sync, STIG checking, SSP/POAM/SBOM generation, a multi-framework crosswalk, cATO monitoring, IL4-IL6 classification and append-only NIST AU audit.

Measured: **98 modules** (floor 5) → `built`; rows `not_expected`. Surface: `tools/compliance/`.

**Verdict.** No adaptation needed; position **Ahead** — ICDEV matched or exceeded the field and there is nothing to adapt.

- benchmark pass declared ahead
- code_state=built, data_state=not_expected
- nothing outstanding — neither the benchmark pass nor the watchlist names an item

**Adapt.** Nothing outstanding — neither the benchmark pass nor the watchlist names an item.

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## 7. Delivery pipeline — **Ahead, no adaptation needed**

**External.** Temporal, Argo, GitHub Actions

| Project | Tracking | What it is |
|---|---|---|
| [argoproj/argo-cd](https://github.com/argoproj/argo-cd) | watched | GitOps continuous delivery |
| [argoproj/argo-workflows](https://github.com/argoproj/argo-workflows) | watched | Kubernetes-native pipelines. Named in the benchmark map alongside Temporal; neither closes the loop from detected gap to merged fix, which is where ICDEV leads. |
| [kyverno/kyverno](https://github.com/kyverno/kyverno) | watched | Kubernetes-native policy engine |

**ICDEV today.** kanban -> worktree -> build -> PR -> CI -> merge-verified done, with a real dated build ledger behind it. None of the external tools close the loop from detected gap to merged fix.

Measured: **106 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/kanban/`.

**Verdict.** No adaptation needed; position **Ahead** — ICDEV matched or exceeded the field and there is nothing to adapt.

- benchmark pass declared ahead
- code_state=built, data_state=not_assessed
- nothing outstanding — neither the benchmark pass nor the watchlist names an item

**Adapt.** Nothing outstanding — neither the benchmark pass nor the watchlist names an item.

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## 8. Data quality & lineage — **Gap**

**External.** OpenLineage, DataHub, Great Expectations

| Project | Tracking | What it is |
|---|---|---|
| [datahub-project/datahub](https://github.com/datahub-project/datahub) | watched | Metadata catalog and governance. Adjacent to ICDEV's data-mesh model (dm_domains, dm_data_products). |
| [fivetran/great_expectations](https://github.com/fivetran/great_expectations) | watched | Declarative data quality expectations. ICDEV's quality engine exists; the gap is data flowing through it, not the code. Moved from great-expectations/great_expectations (Fivetran acquisition) — the old slug 301s, so it still resolves, but record the canonical name. |
| [OpenLineage/OpenLineage](https://github.com/OpenLineage/OpenLineage) | watched | The emerging lineage event spec. Cheapest adaptation in the benchmark map — emitting OpenLineage events makes ICDEV's existing data_canvas engines interoperable without rewriting them. |

**ICDEV today.** The engines are there — profiler, quality engine, anomaly detector, freshness guardian, PII scanner, plus a data-mesh model. The gap is data, not code: dm_domains held 4 rows and dm_data_products 8, and the cf_applications and mc_app_inventory inventories were empty.

Measured: **18 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/data_quality/`.

**Verdict.** Gap; position **Behind** — the benchmark pass found ICDEV behind and nothing has retired that.

- benchmark pass declared behind
- code_state=built, data_state=not_assessed
- the declared finding has not been retired by measurement

**Adapt.**

- OpenLineage's event spec, so lineage is emitted in a standard format and the existing engines become interoperable.  *(docs/research/external-benchmark-map.md)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.
- the declared finding has no measurable closing condition, so this tool cannot retire it — re-reading docs/research/external-benchmark-map.md §8 is a human step

---

## 9. LLM evaluation & red teaming — **Gap**

**External.** promptfoo, DeepEval, Giskard

| Project | Tracking | What it is |
|---|---|---|
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | watched | 50+ research-backed metrics — faithfulness, relevance, safety. The metric definitions are the reusable part; ICDEV has TRUST per call but nothing measuring quality regression release over release. |
| [Giskard-AI/giskard-oss](https://github.com/Giskard-AI/giskard-oss) | watched | LLM vulnerability scanning. Complements the existing ATLAS red-teaming and OWASP-agentic assessor on the quality side. Renamed from Giskard-AI/giskard — the old slug 301s, so it still resolves, but record the canonical name. |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | watched | Eval-as-config with CI gating. Fits ICDEV's args-over-code convention directly — context/iqe/queries/ already establishes config-declared test corpora. Primary adaptation target for the thinnest subsystem measured. |

**ICDEV today.** tools/evaluation/ holds 2 modules against 38 in tools/rag/ — the thinnest major subsystem measured. ATLAS red-teaming and an OWASP-agentic assessor mean SECURITY evaluation exists; systematic QUALITY evaluation does not.

Measured: **11 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/genesis/harness/`.

**Verdict.** Gap; position **Behind** — the benchmark pass found ICDEV behind and nothing has retired that.

- benchmark pass declared behind
- code_state=built, data_state=not_assessed
- the declared finding has not been retired by measurement

**Adapt.**

- promptfoo's eval-as-config model with CI gating — it fits the args-over-code convention, and context/iqe/queries/ already establishes config-declared test corpora.  *(docs/research/external-benchmark-map.md)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.
- the declared finding has no measurable closing condition, so this tool cannot retire it — re-reading docs/research/external-benchmark-map.md §9 is a human step

---

## 10. IaC & infrastructure — **Parity, no adaptation needed**

**External.** Crossplane, Checkov, Atlantis

| Project | Tracking | What it is |
|---|---|---|
| [bridgecrewio/checkov](https://github.com/bridgecrewio/checkov) | watched | IaC policy scanning. The policy-pack model is the closest thing worth borrowing if IaC policy scanning becomes a priority. |
| [crossplane/crossplane](https://github.com/crossplane/crossplane) | watched | Control-plane IaC. ICDEV is at parity here with better compliance integration; nothing urgent to adapt. |
| [opentofu/opentofu](https://github.com/opentofu/opentofu) | watched | Terraform fork — IaC ecosystem evolution |
| [runatlantis/atlantis](https://github.com/runatlantis/atlantis) | watched | PR-driven Terraform. Named in the benchmark map; ICDEV's PR-driven loop is broader but not Terraform-specific. |

**ICDEV today.** IDC canvas with Terraform/Pulumi emitters, tf_state / pulumi_state / AWS Resource Groups Tagging importers, Ansible, K8s deploy, Helm, Iron Bank, SLSA and SBOM — comparable coverage with better compliance integration.

Measured: **45 modules** (floor 5) → `built`; rows `not_assessed`. Surface: `tools/deploy/`.

**Verdict.** No adaptation needed; position **Parity** — ICDEV matched or exceeded the field and there is nothing to adapt.

- benchmark pass declared parity
- code_state=built, data_state=not_assessed
- nothing outstanding — neither the benchmark pass nor the watchlist names an item

**Adapt.** Nothing outstanding — neither the benchmark pass nor the watchlist names an item.

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

# Subsystems the benchmark map has not benchmarked

Declared in the inventory and measured here, but no external pass has judged them. They are listed so their absence from the map is visible rather than silent.

---

## Genesis / autoresearch loops — **Parity, with named work**

**External.** No benchmark pass has named an external counterpart for this subsystem.

| Project | Tracking | What it is |
|---|---|---|
| [karpathy/autoresearch](https://github.com/karpathy/autoresearch) | watched | Autoresearch pattern — tight experiment loops |
| [mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill) | watched | Agent skill that simultaneously searches Reddit, X, YouTube, TikTok, GitHub, and Hacker News to synthesize what's trending about a topic in the last 30 days. Key patterns: intelligent entity disambiguation (handle→subreddit→repo), parallel multi-source orchestration, cross-source cluster merging with dedup, dual-scoring (relevance + fun), offline-shareable HTML briefs. 1,012 tests. Adaptation: implement as a Research engine source adapter at tools/research/source_scanners/social_trend_scanner.py. |
| [Panniantong/agent-reach](https://github.com/Panniantong/agent-reach) | watched | Single-entry platform connector framework for AI agents: Twitter, YouTube, Reddit, GitHub, and more via agent-reach install. Patterns: multi-backend routing (primary + fallbacks), zero-config where possible (free APIs prioritized), health monitoring (agent-reach doctor), MCP integration with Exa search, yt-dlp fallback for blocked YouTube. Addresses fragmented per-engine platform connectors in ICDEV. Adaptation: consolidate tools/research/source_scanners/* and tools/innovation/web_scanner.py platform fetches into a shared tools/platform_connectors/ adapter registry. |

**ICDEV today.** No finding declared.

Measured: **54 modules** (floor 5) → `built`; rows `not_assessed`.

**Verdict.** Parity, with named work; position **Not positioned** — ICDEV's side clears its floor with named work outstanding.

- code_state=built, data_state=not_assessed
- 2 item(s) still outstanding

**Adapt.**

- last30days — Parallel Multi-Source Social Trend Synthesis  *(watchlist:mvanhorn/last30days-skill)*
- Agent Reach — Unified Internet Access Layer for AI Agents  *(watchlist:Panniantong/agent-reach)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## Generated-UI and presentation quality — **Parity, with named work**

**External.** No benchmark pass has named an external counterpart for this subsystem.

| Project | Tracking | What it is |
|---|---|---|
| [leonxlnx/taste-skill](https://github.com/leonxlnx/taste-skill) | watched | Framework for reusable agent skills that improve AI-generated UI quality. Modular skill architecture (npx skills add), tunable parameters (DESIGN_VARIANCE, MOTION_INTENSITY, VISUAL_DENSITY), image-first workflows, multi-agent portability via SKILL.md format (works across ChatGPT, Codex, Cursor, Claude). Anti-generic output research with parametric dials. Adaptation: hardprompts/ directory — add design-quality instruction templates for Creative canvas and Slides generator to improve generated UI/presentation code quality. |

**ICDEV today.** No finding declared.

Measured: **53 modules** (floor 5) → `built`; rows `not_assessed`.

**Verdict.** Parity, with named work; position **Not positioned** — ICDEV's side clears its floor with named work outstanding.

- code_state=built, data_state=not_assessed
- 1 item(s) still outstanding

**Adapt.**

- Taste Skill — Parametric Design-Quality Instruction Skills  *(watchlist:leonxlnx/taste-skill)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## SparkPilot / RTOS — **Not comparable**

**External.** No benchmark pass has named an external counterpart for this subsystem.

| Project | Tracking | What it is |
|---|---|---|
| [espressif/esp-idf](https://github.com/espressif/esp-idf) | watched | ESP32 development framework |
| [FreeRTOS/FreeRTOS](https://github.com/FreeRTOS/FreeRTOS) | watched | Core RTOS — SparkPilot foundation |
| [ruvnet/RuView](https://github.com/ruvnet/RuView) | watched | ESP32-based passive WiFi Channel State Information sensing for occupancy detection, vital signs, and movement — no cameras or wearables. Self-supervised learning (ADR-024), 8KB quantized edge model, Ed25519 witness chain attestation. 105-module inference catalog (health, retail, industrial). Low immediate ICDEV relevance: hardware-dependent, out-of-scope for current roadmap. Potential future value if ICDEV integrates IoT monitoring for data-center occupancy or equipment health. Self-supervised anomaly detection patterns may be relevant to MONITOR canvas. Defer unless IoT use case confirmed. |

**ICDEV today.** No finding declared.

Measured: **0 modules** (floor 5) → `absent`; rows `not_expected`.

**Verdict.** Not comparable; position **Not positioned** — not owned in this tree, so its absence is intentional, not a deficit.

- inventory declares owned: false

**Adapt.**

- RuView — WiFi CSI Human Sensing and Edge Inference  *(watchlist:ruvnet/RuView)*

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## Local model serving — **Not comparable**

**External.** No benchmark pass has named an external counterpart for this subsystem.

| Project | Tracking | What it is |
|---|---|---|
| [ollama/ollama](https://github.com/ollama/ollama) | watched | Local LLM runtime — our scanner-tier backbone |

**ICDEV today.** No finding declared.

Measured: **69 modules** (floor 5) → `built`; rows `not_assessed`.

**Verdict.** Not comparable; position **Not positioned** — no benchmark pass has declared what is outstanding here and nothing is queued against it, so there is no external reading to compare against.

- no declared outstanding list and no open adaptation candidate

**Adapt.** Nothing outstanding — neither the benchmark pass nor the watchlist names an item.

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## Model lifecycle — **Not comparable**

**External.** No benchmark pass has named an external counterpart for this subsystem.

| Project | Tracking | What it is |
|---|---|---|
| [mlflow/mlflow](https://github.com/mlflow/mlflow) | watched | ML lifecycle management |

**ICDEV today.** No finding declared.

Measured: **33 modules** (floor 5) → `built`; rows `not_assessed`.

**Verdict.** Not comparable; position **Not positioned** — no benchmark pass has declared what is outstanding here and nothing is queued against it, so there is no external reading to compare against.

- no declared outstanding list and no open adaptation candidate

**Adapt.** Nothing outstanding — neither the benchmark pass nor the watchlist names an item.

**Not measured.**

- row counts were not measured — this report is generated without a database so that it reproduces byte-for-byte on any machine. Run with --live for the measured half.

---

## Where ICDEV leads

Positioned ahead of the field, measured against this tree:

- **Observability / LLM telemetry** — benchmarked against OpenTelemetry GenAI conventions, Langfuse; ahead, with items outstanding.
- **Compliance & ATO** — benchmarked against NIST OSCAL, compliance-trestle; no adaptation needed.
- **Delivery pipeline** — benchmarked against Temporal, Argo, GitHub Actions; no adaptation needed.

---

## Everything outstanding

23 item(s) outstanding across 10 subsystem(s); 9 subsystem(s) carry `adaptation_needed`. The two differ because a subsystem with no verdict can still carry a watchlist candidate — an item to take is not the same thing as a judged deficit.

| Subsystem | Item | Source |
|---|---|---|
| Agent runtime & orchestration | Approval workflow in the agent loop (0 hits). | docs/research/external-benchmark-map.md |
| Agent runtime & orchestration | Headroom — Reversible Context Compression for AI Agents | watchlist:chopratejas/headroom |
| Agent runtime & orchestration | Hermes Agent — Self-Improving Agent with Persistent Memory | watchlist:nousresearch/hermes-agent |
| Agent runtime & orchestration | iii — Unified Function+Trigger Runtime for Service Composition | watchlist:iii-hq/iii |
| Agent runtime & orchestration | Loopy — Structured Feedback-Driven Workflow Loops for AI Agents | watchlist:Forward-Future/loopy |
| Agent runtime & orchestration | Path/tool scoping in skill frontmatter, taken from xichan96/cortex. | docs/research/external-benchmark-map.md |
| Agent runtime & orchestration | Semantic loop detection — stall_threshold catches no progress, not steady progress through equivalent actions (0 hits). | docs/research/external-benchmark-map.md |
| Agent runtime & orchestration | Session wall-clock ceiling — per-call timeouts exist, total duration does not (0 hits). | docs/research/external-benchmark-map.md |
| Agent runtime & orchestration | Sideshow — Real-Time Agent Output Rendering Panel | watchlist:modem-dev/sideshow |
| Data quality & lineage | OpenLineage's event spec, so lineage is emitted in a standard format and the existing engines become interoperable. | docs/research/external-benchmark-map.md |
| Developer portal / catalog | agentcn — Zero-Config AI Agent Templates (shadcn model) | watchlist:shadcn-labs/agentcn |
| Developer portal / catalog | Cortex.io's scorecard-as-code — a ladder of ranked levels, weighted rules, per-rule exemptions with approval, and evaluation windows, with IQE as the rule language rather than a new DSL. | docs/research/external-benchmark-map.md |
| Generated-UI and presentation quality | Taste Skill — Parametric Design-Quality Instruction Skills | watchlist:leonxlnx/taste-skill |
| Genesis / autoresearch loops | Agent Reach — Unified Internet Access Layer for AI Agents | watchlist:Panniantong/agent-reach |
| Genesis / autoresearch loops | last30days — Parallel Multi-Source Social Trend Synthesis | watchlist:mvanhorn/last30days-skill |
| LLM evaluation & red teaming | promptfoo's eval-as-config model with CI gating — it fits the args-over-code convention, and context/iqe/queries/ already establishes config-declared test corpora. | docs/research/external-benchmark-map.md |
| Observability / LLM telemetry | httpinspect — Zero-Instrumentation eBPF HTTP Monitor | watchlist:yeet-src/httpinspect |
| RAG & knowledge graph | MarkItDown — Multi-Format to Markdown Converter (Microsoft) | watchlist:microsoft/markitdown |
| RAG & knowledge graph | Microsoft GraphRAG's community detection and hierarchical summaries, sequenced behind the edges existing. | docs/research/external-benchmark-map.md |
| RAG & knowledge graph | Tolaria — Offline Git-Backed Markdown Knowledge Vault | watchlist:refactoringhq/tolaria |
| RAG & knowledge graph | Trilium — Hierarchical Knowledge Base with Relation Maps & Attribute System | watchlist:TriliumNext/Trilium |
| Security ops / threat analysis | TheHive Cortex's analyzer contract — declared observable types, a per-analyzer rate limit and a taxonomy-tagged report — expressed as data rather than as a base class, since 79 modules will not be rewritten to adopt one. | docs/research/external-benchmark-map.md |
| SparkPilot / RTOS | RuView — WiFi CSI Human Sensing and Edge Inference | watchlist:ruvnet/RuView |

---

## What this report does not cover

These subsystems get no verdict. That is the intended outcome, not a failure — a subsystem nobody has benchmarked is scored against nothing, never against zero.

- **SparkPilot / RTOS** (`embedded`) — not owned in this tree, so its absence is intentional, not a deficit.
- **Local model serving** (`llm_infrastructure`) — no benchmark pass has declared what is outstanding here and nothing is queued against it, so there is no external reading to compare against.
- **Model lifecycle** (`mlops`) — no benchmark pass has declared what is outstanding here and nothing is queued against it, so there is no external reading to compare against.

The external half of every comparison is a dated human reading, and no run of this tool refreshes it. When an external project moves, the watchlist and [docs/research/external-benchmark-map.md](external-benchmark-map.md) are edited by hand; this document then re-checks ICDEV's half against it.

## Sources

- `args/xbm_subsystem_inventory.yaml`
- `args/innovation_promoter.yaml`
- `context/genesis/competitors.yaml`
- `docs/research/external-benchmark-map.md`

Generated by `tools/innovation/benchmark_report.py`. This file is checked in, so its as-of date is its git commit date — no wall-clock stamp is written into the body, because one would make the file differ on every run and there would be nothing left for CI to diff.

CUI // SP-CTI
