# [TEMPLATE: CUI // SP-CTI]

# Changelog

All notable changes to ICDEV™ are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.40] - 2026-07-27

Grounding work aimed at one problem: working with a corpus far larger than any available context window, without the answers quietly inventing things. 1.2.35 introduced TRUST citations; this release makes the grounding underneath them work.

### Added
- **Derivation disclosure.** `tools/quality/derivation.py` classifies every claim as `verbatim` / `derived-text` / `derived-numeric`, and for computed figures recovers the arithmetic — formula, each operand's value, each operand's source. A cited answer previously presented a quotation, a paraphrase and a computed figure identically; the computed case passes citation validation because the cited chunk genuinely exists, even when the number is not on the page. Classification is deterministic (D391 picker rule) — the model is never asked whether it quoted or computed, because a model that fabricated a number will equally happily report that it quoted one. `derived-numeric` with **no** recoverable derivation is the loud case. Surfaced on `/document-intelligence`, amber only when a derivation could not be recovered. `Provenance` gains a `derivation` field, defaulting to `""` rather than `verbatim` (which would assert a quotation nobody checked).
- **Context budgeting.** `tools/llm/context_budget.py` — a real token account plus a per-model `context_window` (now declared for all 30 routed models), budgeting to the floor of the routed chain and reporting explicitly what it dropped. `_llm_synthesize` previously sent a fixed `results[:5]` at 1,000 chars regardless of model, after the retriever had fetched 50 candidates and discarded 45.
- **Per-claim grounding on the DIC chat path** — claim decomposition, token-F1 span binding, and an anchor guard (numbers, dates, currency, acronyms, proper nouns must appear in the bound span), with the shipped CoVe guard enforced at the publish gate.
- **Embedding-provider circuit breaker** — persisted and TTL'd, with a bounded 5s availability probe (`ICDEV_EMBED_PROBE_TIMEOUT`) and `LLMRouter.reset_embedding_availability()`.

### Fixed
- **DIC search returned zero results for every query in the browser.** RLS predicate injection appended a `classification` filter to `SELECT 1 FROM pg_extension` — a PostgreSQL system catalog with no such column. The probe raised, `PgVectorStore` reported pgvector unavailable, and retrieval returned nothing. A script has no Flask request context, so no RLS and no injection: every reproduction outside the browser looked healthy, and nothing logged an error. Row predicates are no longer injected into system catalogs; application tables still receive theirs (asserted in both directions).
- **Collection-scoped search lost 85% of the corpus.** The result filter re-derived each chunk's collection from `dic_chunk_links`, written by only one ingest path and covering 168 of 559 live chunks — so a scoped query against a 236-chunk collection returned zero while the retriever had correctly returned its chunks. It now reads the column the retriever filtered on.
- **All three security audit trails were permanently empty.** `_write_rls_audit`, `_write_column_audit` and `_write_field_audit` open a raw `sqlite3` connection (bypassing `translate_sql`) but bound `%s`. Every insert raised, each bare `except: pass` swallowed it, and the tables recorded nothing while reporting as enabled.
- **BM25 keyword fallback could not execute on PostgreSQL** — mixed `?`/`%s` in one statement, so psycopg2 raised and the caller returned `[]`. The keyword safety net had been dead on the primary backend.
- **Embeddings bypassed the LPX proxy gateway.** `apply_gateway_to_provider_cfg` was called only on the chat path, so with `ICDEV_LLM_PROXY_ENABLED=true` chat used a virtual key while embeddings called the real endpoint with the real provider key.
- **Local embedding fallback was slow, not broken** — nothing remembered a failed cloud probe, so every cold process re-probed both providers (~12s) ahead of a 0.06s local embed.
- **Layout probe imported the packages it was only meant to detect** — `importlib.import_module` on PaddleOCR/doclayout-yolo at DIC module load, executing torch and a model-weight download the probe's own docstring said it existed to avoid. Now `importlib.util.find_spec`, and the first available backend wins rather than requiring all of them.
- **HTML ingestion silently dropped body text** on small documents — the web-page boilerplate pruner treats a short paragraph as chrome, so `<p>Hello world</p>` disappeared and only the heading survived.
- **The DIC answer badge asserted verification that never ran** — now driven from the response payload.
- **~150 failing tests repaired** across DIC, router, workflow-HITL and pattern-classifier. Fixtures injecting raw `sqlite3` connections into PG-dialect code also un-hid three tests that had been passing on the empty list a swallowed exception produced. 98 tests exercised `pattern_classifier` code that never reached main — landed by a bulk kanban merge whose implementation did not survive it — and were removed; a mixed file was pruned surgically so its 16 live tests still run.

### Changed
- **Version sources reconciled.** `args/brand.yaml` (dashboard badge) had drifted to 1.2.30 and `CHANGELOG.md` to 1.2.37 while the package was 1.2.39 — three numbers across four files. All now track `icdev/_version.py`.

## [1.2.39] - 2026-07-24

### Fixed
- **`pip install icdev` could not resolve its own component registry.** `_find_repo_root()` probed only `<parent>/args/component_registry.yaml`, but the wheel installs that file under `icdev/data/args/`. Every PyPI install loaded **zero** components. Both layouts are now probed, source checkout taking precedence. Invisible from a repo clone, which is why it survived since 1.2.37.
- **`icdev status` crashed on a fresh install** — `ValueError: max() iterable argument is empty` on an empty registry; now prints an actionable message.
- **`icdev init` recommended a command that does not exist** (`icdev enable --list`); the working command is `icdev list`.
- **Framework counts derive from the source of truth** — the README advertised 42 over a table of 36 while the registry declared 35. Now 35 compliance frameworks (enumerated from the registry) and 12 AI governance & assurance standards, stated separately.

## [1.2.38] - 2026-07-22

A hardening release, not a feature release: 170 fixes to 121 features across 338 merged PRs.

### Security
- **Fail-closed authentication across the canvas surface** — mutating routes auth-gated by default (DSOC, QDC, OHC/NOCC, AADC, AIMC, AI-ify, GameDay, Strategos, ZIG, Migration Intelligence, `/canvas-compliance`, Second Brain). Three fail-open paths removed: the Admin Console's conditional RBAC, the usage API's admin fallback, and the `'default'` user fallback on `/me`. Canvas access defaults to deny.
- **XSS / CSRF / IDOR / upload sweep** — shared `escapeHtml` helper replacing ad-hoc interpolation, a CSRF guard for cookie-authed mutating JSON APIs, a global upload cap with JSON 413s, tenant-scoped IDOR guards, a path-traversal fix in `api_regen_download`, and Academy's `code_runner` hardened against secret exfiltration.

### Fixed
- **Surfaces that overstated what they knew** were retired rather than papered over — the PDC Studio trio returning invented results, a hollow Info Ops canvas, a dead NDC health endpoint, and a raw-`sqlite3` fail-soft fallback in Strategos that masked failures.
- **PostgreSQL-primary runtime** — `%s`/`?` reconciliation, `sqlite_master`/`PRAGMA` introspection removed from runtime paths, and the missing PDC / Data-canvas / Security-canvas tables reconciled into `pg_consolidated.sql` with a schema-parity test.

### Added
- **TRUST extended to every drafting surface** — citation grounding and the publish gate now cover docgen, the Migration Canvas and AI-ify's AI Boost; WriteGuard is fail-closed; Second Brain masks PII at LLM egress.
- 10 FORGE Academy platform-subsystem missions and 3 AI GameDay TTX scenarios.

## [1.2.37] - 2026-07-15

### Added
- **ICDEV Cortex — unified governed AI facade.** `cortex.complete() / reason() / search() / extract() / classify() / govern()` over the LLM router, RAG, KG, DIC, and IQE. Policy-routed with end-to-end token accounting (result → audit → metrics), governed CoT / debate / council reasoning over REST, Reciprocal Rank Fusion for cross-backend search merge, an opt-in audited LRU+TTL response cache (tenant-safe), and a governance-first home monitor card over `cortex_audit`. `governance.fail_closed` is now live config.
- **Cortex external exposure** — scoped service keys + client SDK, DataBridge connectors for `icdev_cpmp` (contract/delivery bridge with `cpars_assessments` + `negative_events`, `mod_recommendations` write path) and `icdev_demand` (RFI demand signals), a RICOAS intake bridge at `/cortex/api/v1/intake/*`, and an award endpoint where a won bid proposes the `/cpmp` delivery baseline.
- **Policy-routed LLM (Pillar 0)** — request content is classified to decide egress: CUI / local-only chains stay on-host, releasable content may use cloud models. Playwright / e2e execution chains centralized on the configured test-execution provider (`chain_groups`).
- **Kanban** — repo-aware external dispatch (build into the target repo instead of parking `prem-*` tasks), a Manual Build checkbox + build-model selector, and the two previously-unwired board columns.
- **GovCon PTW** — bid-side LCAT→person registry, auditable pricing carried into `/cpmp`, cited win-theme intake, PTW-posture Council consult, and whole-dashboard BI export.

### Fixed
- **Kanban manual-gate integrity** — manual-mode gate tasks are now exempt from the reaper, scheduler startup recovery, dispatch, and the backlog→scheduled promoter, closing four paths that could release (and then erase) gated work.
- **External done-gate** — an external task is done only when its work landed on that repo's `origin/main`; the repo-aware git check runs in the task's own repo and bypass cannot skip it. External worktrees no longer die on ICDev-shaped structural checks.
- **Worktree sweeper** — stopped reporting removals it never performed; prune can now clear a locked worktree entry.
- **Dashboard** — an unescaped apostrophe on the home page had killed every JS function on `/`.
- **`specialist_consult` fails closed**; a zero rate is treated as real data, not a missing one.
- **Tests** — 76 failing kanban tests repaired alongside 3 real production/schema bugs.

### Changed
- **Employer identity removed from the repo** — no company name in ICDEV.
- **CI lint gate** no longer runs `--fix` (stops the gate hiding lint debt); dead imports removed.

## [1.2.36] - 2026-07-09

### Security
- **ABAC ownership references no longer collapse to match-all (fail-open).** `${subject.user_id}` was resolved against a flattened context, so the dotted path returned `None`, which the matcher treats as match-all. With first-match-wins evaluation, `proposal_section_writer_own` (Permit) preceded `proposal_section_writer_deny_unassigned` (Deny), letting any `section_writer` edit **any** proposal section. References now resolve against a nested context and unresolvable references become a non-matching sentinel, so evaluation falls through to deny. `developer_readwrite_own` ownership scoping was affected the same way (#131).

### Fixed
- **Canvases invisible after `pip install`** — the packaged `.env.template` (derived from `.env.example`) was missing ~90 capability flags, hiding Document Intelligence, Tech Writer, Notebook, Slides, and the RFI canvas on a fresh `icdev init`. It now documents all 62 registry-declared enablement flags, guarded by two new release checks: `env_files_sync` and `env_flags_documented` (#120).
- **DIC AI Assist silently abstained** on a transient empty LLM completion, leaving the section blank with no feedback. Empty completions are now retried (bounded, `ICDEV_DIC_LLM_RETRIES`), the per-attempt timeout is configurable (`ICDEV_DIC_LLM_TIMEOUT`, default 90s), and abstentions are surfaced to the reviewer (#127).
- **`rag_queries` / `rag_citations` materialized** in `pg_consolidated.sql`, `init_icdev_db.py`, and migration 252 — the RAG result-card renderer already queried them, so PostgreSQL raised `UndefinedTable` (#128).
- **RLS columns on capture/pWin/competitor tables** — `tenant_id` / `classification` added to `pg_pwin_assessments`, `pg_competitor_awards`, `pg_capture_gate_decisions` (ensure-table helpers, PG schema, migration 253). The injected RLS predicate previously raised `UndefinedColumn` on every read (#130).

## [1.2.35] - 2026-07-09

### Added
- **TRUST initiative** — universal source citations with data provenance on every generated artifact (proposals, RFI, DIC, Tech Writer, generated child apps), enforced by a blocking `citation_guard` on promote/export with HITL override + audit, built on a shared `tools/quality/citation_grounding.py` core and backed by the materialized `rag_provenance_ledger` (#121, #122).
- **Fail-closed-capable data masking** — LLM egress can abort rather than send raw PII/CUI when the sanitizer is unavailable (`redaction.fail_closed`); ingestion-time masking (`redaction.mask_at_ingestion`); a scheduled `redaction_scan_reflex` files remediation cards for unmasked data at rest. All toggles default off.
- **Anti-hallucination consistency** — the deterministic confabulation detector is wired into RFI, proposals, and DIC generation as a reviewer signal; `coherence_checker.check_trust_coverage` enforces TRUST invariants.

### Fixed
- **DIC generation leaked Chain-of-Thought / Chain-of-Debate reasoning** into published prose. Reasoning scaffolding is scrubbed, CoT is gated off by default, and any residue flags the section for human review (#125).

## [1.2.34] - 2026-07-02

### Added
- **BI Dashboard Canvas** — NL-driven 2D/3D chart canvas at `/bi_dashboard`: describe a chart in plain English, rendered via a ported VIZ kernel + ECharts-gl against real project data (bar, scatter3d, surface3d, bar3d). Two new ACE Quick Launch presets (`bi_build_dashboard`, `bi_kg_insights`) (#78).
- **Rubric-gated agent loop** — `run_agent_loop_with_rubric()` in `icdev/tools/llm/agent_loop.py`: declares a rubric up front, runs the agent loop, then has a separate tool-free grader LLM judge the result (satisfied / needs_revision / failed), resuming from the existing transcript on `needs_revision` for up to `max_grading_iterations` rounds. Adapted from deepagents' RubricMiddleware pattern, framework-agnostic (no LangGraph dependency). 12 new tests (#79).

### Fixed
- **BI Dashboard bar3d aggregation** — `_structure_to_spec()` treated bar3d's categorical x/y fields as raw floats, silently dropping every row; now builds `x_categories`/`y_categories` from real column values and aggregates z per (x, y) pair per the ECharts `bar3D` contract (#78).
- **Coherence checker nav-link false positive** — `check_new_page_completeness`'s nav-link sub-check only recognized hardcoded `href="/<canvas>"` strings, false-positiving on registry-driven canvases whose nav link renders dynamically from `component_registry.yaml`. The check is now registry-aware (#78).
- **Untrusted SVG parsing (Bandit B314)** — `tools/viz/svg_to_pptx.py` now parses SVG input via `defusedxml` instead of stdlib `xml.etree.ElementTree` (#77).
- **Config duplicate-key sweep** — removed silent duplicate top-level mapping keys across `args/llm_config.yaml`, `args/genesis_config.yaml`, `args/security_gates.yaml`, and `args/simulation_canvas_registry.yaml`, and fixed a YAML indentation parse error in `args/package_exclusions.yaml` that broke the installer's exclusion loader outright (#80).

---

## [1.2.33] - 2026-07-01

### Added
- **RFI Response Engine** — full GovCon RFI Response Workbench canvas (`/rfi`) with HITL review, WriteGuard V&V, cross-section consistency checking, deadline countdown, and one-click "Generate Why Us" narrative. New ACE evaluator team (`rfi_researcher`, `rfi_writer`, `rfi_compliance_reviewer`, `rfi_editor`, `rfi_reviewer` roles under `args/ace/roles/`). 104 tests.
- **Slides: SVG → Native PPTX Shapes** — `tools/viz/svg_to_pptx.py` is a deterministic SVG-subset parser (rect/circle/ellipse/line/polyline/polygon/path/text, nested `<g transform>`) that emits native `python-pptx` `FreeformBuilder` vector shapes instead of rasterized pictures. Curves are flattened to line segments. New `slide_type="svg_art"` in `pptx_builder`.
- **Slides: Template-Fill Workflow** — `tools/slides/template_fill.py` adds `/slides/templates`: upload a customer-supplied `.pptx`, inspect its fillable shapes (title/body/table/chart) via `inspect_template()`, then overwrite selected slides' content in place with `fill_and_export()` — format-preserving (reuses existing run/paragraph XML), no LLM step, deletes unselected slides. New `slides_templates` table.
- 43 new/updated tests for the slides changes above; Playwright-verified upload → inspect → fill → download round-trip.

### Fixed
- **Slides schema dialect mismatch** — `tools/slides/db/init_db.py` picked its SQL dialect from the configured backend (`_SLIDES_BACKEND`, default `postgresql`) instead of what `get_connection()` actually resolved to, so `SERIAL PRIMARY KEY` silently landed on a SQLite-backed connection whenever PG was unreachable, breaking autoincrement for every table in the canvas. Schema selection is now resolved from the live connection.
- A latent bug where a `.svg` `image_path` silently failed under `add_picture` (`python-pptx` cannot rasterize SVG) is now handled by the new native-shape renderer.
- **Bandit B314 (SVG XML parsing)** — `tools/viz/svg_to_pptx.py` parsed untrusted SVG input with stdlib `xml.etree.ElementTree`, flagged by Bandit as XML-attack-surface (B314). Parsing now goes through `defusedxml`, eliminating the finding; `docs/security/sandbox-coverage.md` updated to match.

---

## [1.2.32] - 2026-06-29

### Added
- **Second Brain / AI Executive Assistant** — persona-adaptive onboarding, daily briefing digest, ACE SOUL injection, and `/me` canvas. 30+ features across `tools/second_brain/` including proactive advisor, retro engine, and Microsoft/Slack connectors; 77 tests; enabled via `ICDEV_SECOND_BRAIN_ENABLED=true` (#64).
- **Co-Workers Canvas** (`/coworkers`) — config-driven roster of AI co-worker personas loaded from `args/chat_personas.yaml`; launch/terminate sessions with live status panel; paired with Reasoned Codegen Advisor MCP tool (`reasoned_codegen_advise`) and routing chain in `args/llm_config.yaml` (#66).
- **External Repo Adaptations** — patterns from sideshow, loopy, trilium, and agentcn integrated into ICDEV™ architecture (#67).
- **`.env.sample`** — comprehensive 60+ key sample environment file covering all LLM providers, canvas toggles, subsystem flags, and integrations; safe to commit.
- **Kanban Branch-First Enforcement** — executor requires a branch before dispatch; `pr-watcher` webhook wired into kanban scheduler lifecycle.

### Fixed
- 43 pre-existing Ruff lint errors (F401/F841/F821/W191/E101) resolved across the codebase.
- Pre-existing Security Scan and Test CI failures resolved for a clean CI baseline.
- `_auto_provision_env_key` patched before module import in `ks_app` fixture.
- Document Intelligence `freshness_engine` added to tool manifest.
- QA defects, OIDC manifest entry, and tier gate module resolved (#65).
- Broken test references in `test_proposals_detail_action_bar`, `test_proposals_detail_ai_drafts_tab`, and `test_govcon_capabilities`.

### Changed
- README Quick Start (Option 2) updated with `cp .env.sample .env` step for new contributors.

---

## [1.2.31] - 2026-06-21

### Added
- **Enterprise-Configurable Platform** — component registration is now 100% registry-driven via `args/component_registry.yaml`; core profiles in `args/core_profiles.yaml`; tenant-level overrides in `tenant_component_overrides` (migration 207); append-only `component_audit_log` (migration 208).
- **ACE File Access Broker** — `icdev/tools/ace/file_access_broker.py` enforces three-tier file access for co-worker agents: `zero_access` (`.env`, `*.pem`, `*.tfstate`), `read_only` (lock files, compliance catalogs), `no_delete` (`CLAUDE.md`, goals, IaC).
- **ACE Skill Promoter & Soul Manager** — `skill_promoter.py` autonomously proposes new skills from co-worker discoveries queued for human review; `soul_manager.py` manages SOUL personality configs per co-worker role.
- **ACE Agent Coordination** — `agent_coordination.py` + migration 222 bring cross-session advisory locks so concurrent co-worker and kanban agents negotiate file ownership; coordination state visible in HITL dashboard.
- **Agent Loop Persistence** — migrations 220 (`agent_loop_sessions`) and 221 (`agent_hitl_pending`) give `run_agent_loop` durable session state: resume on restart, HITL item queue with approver assignments, cost/token tracking.
- **Processify Canvas** — BPMN-style process design canvas at `/processify` with drag-and-drop swimlane editor, BPMN 2.0 primitives, JSON export, compliance overlay (maps lanes to NIST 800-53 process controls), and IQE query support.
- **Canvas Health Dashboard** — real-time health panel showing record counts, last-indexed timestamp, IQE adapter status, missing ACE roles, and pending HITL items.
- **Updates Feed** — system-wide chronological feed of component config changes, migration runs, and reflex activity at `/updates`.
- **Coworker HITL Workflow** — dedicated HITL queue UI with approve/reject/comment, full audit trail, priority ranking, and bulk-action support.
- **Billing Module** — `icdev/tools/billing/` adds tenant billing: usage metering (API calls, LLM tokens, storage), tier enforcement, invoice generation, and billing dashboard at `/billing`.
- **Onboarding Wizard** — 5-step guided first-run setup covering DB backend, LLM provider, canvas selection, profile application, and dashboard tour.
- **Migration Topology Visualization** — interactive Sankey-style migration wave diagram at `/migration/topology` showing workloads, target environments, risk bands, and STIG compliance readiness.
- **Network Topology Neighbors** — migration 218 adds `net_topology_neighbors` table with pre-computed neighbor sets for O(1) blast-radius lookup.
- **Capability Sheet Reflex** — 6-hour cadence reflex auto-generating `.agents/skills/icdev-capability-sheet` from live manifests, MCP registrations, and canvas inventory.
- **Child-App Flavor Templates** — generator overlay refactor adds flavor-specific Jinja2 templates for child-app scaffolding.
- **FathomDesk Market Data Integrity** — Phase 1 data integrity layer with disclaimer banner and naming reconciliation.

### Fixed
- CMMI L3 assessor hardening: refined evidence-weight scoring for PA 3.1–3.6, automatic detection of process asset library gaps, new HTML evidence report template.
- Canvas auto-remediation confidence threshold raised to 0.75; sub-threshold findings surface as HITL items.
- 264 Ruff style and correctness issues resolved across 128 files.

### Changed
- AI platform MCP configs and skill files synced across Claude Code, Cursor, Windsurf, Amazon Q, Copilot, Gemini, Goose, Junie, and Cline.
- `icdev profile apply <name>` now applies environment presets from `args/core_profiles.yaml`.

---

## [1.2.30] - 2026-06-20

### Added
- **ACE Co-Worker Engine — 14 New Roles** — monitoring/observability, GovCon, CPMP, and FathomDesk roles added; co-worker intent classified via LLM + catalog constraint for automatic role assembly.
- **ACE Hardening & Traceability** — activity timeline, trust leaderboard, and audit API at `/coworker/<id>/timeline`; pre-insert pending row on launch; `listen_topics` guard against circular deadlocks; `canvas_placeholder_style` coherence gate for SQLite `?` vs PG `%s`.
- **AI Augmentation Canvas (AAC) — 11-Pillar Agent Readiness Checker** — assessment suite at `/ai-augmentation/` evaluating structure, configuration, dependencies, documentation, IL classification, NIST 800-53 controls, STIG compliance, audit, code quality, security hardening, and test coverage.
- **Kanban Scheduler PR Flow** — tasks follow push → `gh pr create` → store PR URL; `pr_watcher` daemon polls CI and auto-merges chore/test/fix when green via `ICDEV_KANBAN_PR_FLOW=true`.
- **Proposal Inline Annotations** — section-level annotations with compliance/risk/strength/gap tagging and margin notes surfaced in WriteGuard V&V.
- **ACE Preflight Decisions** — `ace_preflight_decisions` table gates co-worker launches with structured go/no-go decisions.
- **ClaWhub Risk-Score Blocking** — dependency imports with risk score > 50 blocked at the gate; cached risk scores and severity shown per import.
- **Centralized Logging** — `icdev_logger.get_logger()` adopted by the 36 remaining tools that used raw `logging.getLogger()`.

### Changed
- AI-ify posture score raised to A/99 after undercount fix; Agentic AI posture raised to B/84.7 after hardening Autonomous Coder and Customer Service Agent designs.
- Gap detector now scans the `icdev/` package tree for `CREATE TABLE` statements.

### Fixed
- 14 Ruff lint errors resolved; CI passing.
- `validated_commit.py` resolves `BASE_DIR` to the main worktree via `git rev-parse --show-toplevel`.

## [1.2.29] - 2026-06-13

### Added
- **AI-ify Compliance Posture Engine** — full posture scoring at `/ai-ify/posture` with AI-ify and Agentic AI canvases wired into `/compliance`.
- **Document Intelligence Canvas (DIC) — Intelligence Hub** — collaboration hub, freshness engine, document explorer, and HITL handoff workflow with `dic_doc_freshness`, `dic_handoff_sessions`, and `dic_handoff_items` tables.
- **Proposals WriteGuard V&V Pipeline** — section-level V&V gate before finalization; draft rendering shows WriteGuard score, compliance flags, and confidence band inline.
- **GovCon DHS Proposal Seeding** — 3 DHS solicitations seeded with ICDEV-branded proposal content.
- **CPMP Contract Modifications** — `cpmp_contract_mods` table with request/approval workflow.
- **IQE Security ZIG Queries** — seed query library at `context/iqe/queries/security/zig_queries.iqe` covering pillar coverage, unassessed controls, and remediation priority queues.
- **IQE Data Mapping Queries** — three new Data-canvas IQE query files and adapter registration updates.
- **AIForge IRAD Diagrams** — high-level concept, solution overview, and three progressive draw.io architecture diagrams committed under `docs/irad/`.
- **Kanban Bulk-Promote UI** — gated bulk-promote action for suggested cards.

## [1.2.28] - 2026-06-06

### Added
- **Data Mesh module** (`/data/mesh`) — domain-driven data mesh with domain registry, data product catalog, SLA enforcement, stewardship ownership matrix, and contract lifecycle management.
- **Data Canvas Governance Engine** (`/data/governance`) — policy enforcement dashboard aligned to NIST 800-188 and DoDI 8320.02 with stewardship workflows and audit-trail-backed policy decisions.
- **Data Canvas Products Page** (`/data/products`) — data product catalog with ownership, classification zone, lineage graph, SLA status, and consumer subscription tracking.
- **CSP Analysis Module** (`/data/csp`) — Cloud Service Provider overlay with cost projection, compliance posture per CSP, risk tiering, and data sovereignty tagging across 6 cloud providers.
- **60+ dashboard templates synced to icdev/ package** — GovLift, Info Ops, Innovation pipeline, Network sub-pages, Studio, Security Canvas, FORGE Academy, GameDay, IL5 classification, MFA, proposals, intake, supply chain.

### Changed
- Genesis meta-harness CRLF line endings normalized across `daemon.py`, `eval_harness.py`, `heuristic_writer.py`, `llm_triage.py`, and `reflexes/harness.py` for cross-platform compatibility.
- STIG compliance pillar scoring logic tightened in `ai_augmentation/agent_readiness/pillars/stig_compliance.py`.

## [1.2.27] - 2026-05-18

### Added
- **Supply Chain SCRM Dashboard** (`/supply_chain`) — 11th design canvas with vendor registry, SCRM risk tiering, CVE triage queue, ISA agreement lifecycle, SBOM records, and Section 889 compliance status; IQE adapter with 4 registered collections.
- **Chat AI Governance panels** — GOV and INTEL right-sidebar tabs with live data on every context switch: AI model, classification marking, session metadata, RAG readiness, Bayesian compliance score, and session health.
- **Spinning indicators across all AI panels** — RICOAS, GOV, and INTEL tabs show a processing bar while the AI is handling a request.
- **Poll backoff on disconnect** — `pollContextState` uses exponential backoff (2ⁿ seconds, capped at 30s) after connection-refused failures.

### Fixed
- Context-limit `POST /api/chat/contexts` 429 responses now surface a clear "Context limit reached" message.
- Browser favicon requests no longer flood logs with 404 errors.
- `chat_manager.get_context()` falls back to the `chat_contexts` DB table after a dashboard restart.
- 6 broken `quick_action` URLs in `args/use_cases.yaml` repaired.

## [1.2.26] - 2026-05-18

### Added
- **AADC Solution Packs** — 7 pre-wired agentic AI templates added to the Agentic AI Design Canvas, each with pre-placed nodes, wired edges, seeded risk register, compliance baseline, MITRE ATLAS scenario mappings, and quick-start wizard.
- **Autonomous Coder — Live Sample App** — multi-agent pipeline at `/autonomous-coder/` with Input Sanitizer, Orchestrator, Planner, Schema Enforcer, Coder, Validator, and Audit Logger; validated E2E against Claude Sonnet.
- **Sample Applications gallery** — `/agentic-ai/` now shows a Sample Applications section alongside Solution Packs and design templates.

### Changed
- Lesson-Learned LL-001 applied universally: Schema Enforcer nodes added at every LLM→agent handoff.
- Lesson-Learned LL-002 applied universally: circuit breaker `max_duration_s` defaulted to 300s for multi-step LLM pipelines.

### Fixed
- Standalone app generator variance sign formatting and vendor dropdown bugs.

## [1.2.25] - 2026-05-17

### Added
- **10 Government Use Cases** — pre-seeded catalog in `/chat` left sidebar covering Modernization, Budget Sprint, Doc Refresh, SBOM Attestation, OSCAL Package, Compliance Gap Analysis, FedRAMP Assessment, Incident Playbook, Architecture Review, and Zero Trust Alignment.
- **Compact mode** — use-case sidebar collapses to icon-only chips with category filters; Ctrl+click chains multiple use cases into a sequential intake workflow.
- **Canvas seeding** — activating a use case auto-seeds relevant design-canvas nodes and pre-populates `template_requirements`.
- **Standalone app generator** — every use case can generate a downloadable standalone HTML app.
- **Workflow step bar** — use cases with defined workflow stages display a progress indicator in the RICOAS sidebar.
- **All 12 post-export actions** — Send to Kanban, Dry Run, Validate PRD, Generate PRD, and Standalone App are now available for all use cases.

### Fixed
- Variance sign formatting (`-$50.00`) in standalone app generator output.
- Vendor dropdown population and column manager extended to all 13 use case types.

## [1.2.24] - 2026-05-11

### Added
- **Digital Twin for all 5 canvases** — NDC, SDC, BDC, DDC, and ODC each get a `/digital-twin` page with graphical simulation results, AI chat-to-delta, and "Load from Canvas" integration; air-gap safe.
- **Strategos OSINT Phase 2** — conflict intel pipeline with STIX 2.1 / CERT-UA importer, signal priority queue, AIS track processor, Kalibr threat ring overlay, historical pre-war baselines, and supply-degradation coefficients in COA attrition.
- **Strategos predictive intel** — leadership briefing dashboard, War Council brief with corrective RAG, information signal scorer, and targeting package optimizer.
- **FathomDesk multi-agent panel** — bull/bear debate engine, decision audit trail, panel confidence flag, Vol Deleveraging and Crowding Ratio alerts, cross-asset rotation engine, and IV rank computation.
- **Cross-canvas event bus** — DB-persisted events fire across canvases (e.g., `pipeline_deployed` triggers SDC threat model refresh; BDC ISA expiry fires 90-day alerts).
- **GNS3 + ZTP integration** — full GNS3 topology builder with Zero Touch Provisioning workflow and console push tool in NDC.
- **Ontology Explorer** — D3 hierarchy tree visualization for the ICDEV knowledge graph ontology loaded from `args/ontology/*.ttl`.

## [1.2.23] - 2026-05-04

### Added
- **Ask any canvas** — natural-language Q&A over the knowledge graph of each design canvas with `/<canvas>/ask` page and `/<canvas>/api/ask` endpoint.
- **IQE v0.1 — ICDEV Query Engine** — declarative `foreach / where / select` DSL with recursive-descent parser, typed AST, SQL-injection-safe executor, and seed query libraries for all canvases.
- **IQE rollout to all 10 canvases** — NDC, SDC, PDC, BDC, DDC, ODC, IDC, AADC, QDC, MDC each have ≥3 seed queries and a registered IQE adapter.
- **MITRE ATT&CK matrix dashboard** — ODC attack matrix page with drill-through, Sigma rule generator, Splunk SPL export, and Caldera REST adapter.
- **IaC generation** — IDC emits Terraform, CloudFormation, Pulumi, Ansible, and Helm artifacts from canvas designs.
- **Instant KG freshness** — save-hooks on every canvas design `POST`/`PUT` re-index the knowledge graph in <1s; 6-hour `canvas_indexer` Genesis reflex as safety net.
- **Failure Triage auto-fix loop** — Genesis daemon runs `failure_triage` on a 30-min cadence with two-tier LLM routing, confidence threshold 0.85, and 5-apply/hour rate cap.

## [1.2.22] - 2026-04-27

### Added
- **AADC Solution Packs** — 7 pre-wired agentic AI templates added to the Agentic AI Design Canvas.
- **Autonomous Coder — Live Sample App** — multi-agent code generation pipeline at `/autonomous-coder/`.
- **Sample Applications gallery** — `/agentic-ai/` now shows a Sample Applications section.

### Changed
- Lesson-Learned LL-001/LL-002 applied universally: Schema Enforcer nodes at every LLM→agent handoff and circuit breaker `max_duration_s` defaulted to 300s.

## [1.2.21] - 2026-04-20

### Added
- **Ask any canvas** — natural-language Q&A over the knowledge graph of each design canvas.
- **Instant KG freshness** — save-hooks re-index the KG in <1s; 6-hour `canvas_indexer` Genesis reflex as safety net.
- **Backend-aware indexer** — `tools/knowledge_graph/canvas_indexer.py` speaks SQLite or PostgreSQL per-canvas.
- **Failure Triage auto-fix loop** — 30-min Genesis daemon cadence with two-tier LLM routing, 0.85 confidence threshold, 5-apply/hour rate cap, and worktree isolation.
- **IQE v0.1 — ICDEV Query Engine** — declarative DSL for compliance and network-health checks with 5-query NDC seed library.
- **FathomDesk Phase 7+** — complex options, crypto spot, tax-lots with wash-sale flag, and day-trader hot-keys.

### Changed
- Scheduler detaches worktree before merge to prevent the 52-branch preserved-triage pile from regrowing.
- Single license: commercial tier removed; ICDEV™ is Apache-2.0, full stop.

## [1.2.20] - 2026-04-18

### Fixed
- NDC canvas orphan-edge filter and missing `canvas-tooltips.js` restored.
- Scheduler worktree-before-rebase fix cleared the 52-branch preserved-triage merge pile.

## [1.2.19] - 2026-04-17

### Added
- **Failure Triage Reflex** (`tools/genesis/reflexes/failure_triage.py`) — wires `failure_triage.triage_once` into the Genesis daemon on a 30-min cadence (`args/genesis_config.yaml`). YELLOW tier. With `ICDEV_AUTOFIX_ENABLED=true` in `.env`, auto-review of failed kanban tasks now runs without manual invocation.

### Changed
- **Task-type whitelist** corrected from `{build, bug, chore, test, research}` → `{build, chore, fix, research, test}`. Counts from the live table (2026-04-17): `chore=742, build=307, fix=200, test=89, research=49, deploy=12`. `bug` had zero instances; `fix` was the third-most-common type but incorrectly excluded. `deploy` stays excluded — higher blast radius.

## [1.2.18] - 2026-04-17

### Added
- **Failure Triage — Worktree-Apply Stage** (`tools/workflow/failure_triage.py`) — `apply_patch_in_worktree()` completes the auto-fix loop. Creates an isolated worktree at `.tmp/autofix/<task>__<sig>/` on branch `autofix/<task>-<sig>`, applies the LLM patch, runs the verification command, commits on success, rolls back the whole worktree + branch on failure. Pre-apply validation rejects: shell metacharacters / non-allowlisted prefixes in `verification_command`; path traversal, nonexistent files, files not in `diag.suspect_files`, non-unique `old_string`, deny-list prefix matches. Rate budget is only consumed after validation passes. Audit trail at `.tmp/kanban/autofix-audit/<task>__<sig>.json`.
- **Second opt-in: `ICDEV_AUTOFIX_AUTOMERGE`** — even when a patch applies cleanly and verifies green, the `autofix/*` branch is NOT fast-forward merged into main unless this env is also set. Default off. Refuses to merge from a non-main checkout or a dirty working tree.

### Tests
- 35 unit tests (up from 20) — full coverage of the apply stage validation (allowlist, traversal, uniqueness, deny-list) and rejection paths (validation failures must not consume rate budget or create worktrees).

## [1.2.17] - 2026-04-17

### Added
- **Failure Triage** (`tools/workflow/failure_triage.py`) — LLM-assisted review of kanban failures bridging the gap between `self_debug.py` (recurrence-only) and manual intervention. Two-tier LLM routing: Claude for diagnose (`failure_triage_diagnose`), Ollama for patch generation (`failure_triage_patch`). Conservative defaults — `ICDEV_AUTOFIX_ENABLED=false` by default, `--dry-run` default, confidence threshold 0.85, task-type whitelist, file deny-list, rolling 5-apply/hour rate cap. Generated patches are attached to `status='suggested'` Oracle cards for human review — auto-apply dispatcher is a separate follow-up.

### Fixed
- **NIST AI 600-1 Assessor** (`tools/compliance/nist_ai_600_1_assessor.py`) — typo `self._db_path` → `self.db_path`. Was being swallowed by broad `except Exception: pass`; every GAI-* check silently returned empty. Surfaced via regression pytest `-x` hitting `test_confabulation_check_satisfied_with_data`.
- **Kanban depends-on E2E** (`tests/e2e_kanban_depends_on.py`) — step 5 (unblock-on-parent-done) now passes `bypass_verification: true` + audit reason to satisfy guard-22 (the move→done verification gate added 2026-04-14).

## [1.2.8] - 2026-03-31

### Added
- **Network Design Canvas** — ACAS/Nessus vulnerability scan overlay with host-to-node matching and severity heat maps
- **Network Design Canvas** — Natural language topology queries powered by deterministic graph algorithms (NetworkX) with local LLM fallback
- **Network Design Canvas** — Cloud architecture diagram generation for all 6 CSPs
- **Network Design Canvas** — Compliance audit page with per-device STIG tracking and exportable checklists
- **Network Design Canvas** — 1,000+ device types, protocols, and interface constants
- **Network Design Canvas** — Enhanced inventory export (CSV, JSON, YAML) with advanced filtering
- **IBM Cloud IaC** — New Terraform generator for IBM Cloud (VPC, IKS, KMS, COS, LogDNA, SysDig)
- **Azure IaC** — Major expansion: AKS, Key Vault, Front Door, Application Gateway, NSG rules
- **GCP IaC** — Major expansion: GKE, Cloud KMS, Cloud Armor, VPC Service Controls
- **OCI IaC** — Major expansion: OKE, Vault, WAF, Network Security Groups, Bastion
- **SRE Operations** — Dashboard, runbook library, incident tracking, toil budgets, SLO monitoring
- **Pipeline Canvas** — Visual CI/CD pipeline designer with drag-and-drop stages and YAML export

## [Unreleased]

### Security
- Remove unauthorized `process_exec` capability from `subprocess_backend.py` (finding #38, Task-9366644d1d-d5).

### Added
- **IQE v0.1 — ICDEV Query Engine** (`tools/iqe/`) — declarative `foreach / where / select` DSL for running compliance and network-health checks across all seven design-canvas databases. Ships with: hand-rolled recursive-descent parser (`parser.py`), typed AST dataclasses (`ast_nodes.py`), adapter-dispatching executor with SQL-injection-safe fallback (`executor.py`), and a 5-query NDC seed library under `context/iqe/queries/network/` (vendor inventory, BGP peer asymmetry, admin/oper mismatch, CAT I STIG open findings, capacity threshold). See `docs/features/phase-iqe-v0-1.md`.
- Comprehensive test suite expansion (324+ new tests across 21 test files)
- CI/CD pipeline for ICDEV™ itself (GitHub Actions + GitLab CI)
- REST API endpoints for Phases 22-28 capabilities
- Helm chart completion for all 15 agents
- Project documentation (README, CONTRIBUTING, CHANGELOG)

## [Phase 29-32] - 2026-02-XX

### Added
- Dashboard authentication with per-user API keys and SHA-256 hashing (D169-D172)
- RBAC with 5 roles: admin, pm, developer, isso, co (D172)
- Activity feed merging audit trail and hook events via UNION ALL query (D174)
- BYOK (Bring Your Own Key) LLM key management with Fernet AES-256 encryption (D175-D178)
- Usage tracking and cost dashboard per-user and per-provider (D177)
- Spec-kit pattern tools: quality checker, consistency analyzer, constitution manager, clarification engine, spec organizer (D156-D161)
- Proactive monitoring heartbeat daemon with 7 configurable checks (D162-D163)
- Auto-resolver for webhook-triggered issue fix with branch/PR creation (D164-D166)
- Selective skill injection via deterministic keyword-based category matching (D167)
- Time-decay memory ranking with exponential formula and per-type half-lives (D168)
- Resilience patterns: circuit breaker (3-state machine), retry with exponential backoff, correlation IDs (D146-D149)
- Database migration runner with checksum validation and multi-tenant support (D150-D151)
- Database backup and restore with optional AES-256-CBC encryption (D152)
- OpenAPI 3.0.3 spec with Swagger UI for the API gateway (D153)
- Prometheus metrics endpoint with 8 metrics (D154)
- Cross-platform compatibility module for Windows, macOS, Linux (D145)
- CUI markings added to all Python files

### Changed
- Dashboard login page updated for API key authentication flow
- Settings page expanded with LLM key management section
- Team management page updated with role-based controls

## [Phase 23-28] - 2026-01-XX

### Added
- Universal Compliance Platform with 10 data categories and composable markings (D109)
- Dual-hub crosswalk model: NIST 800-53 (US) + ISO 27001 (international) with bidirectional bridge (D111)
- 6 Wave 1 compliance frameworks: CJIS, HIPAA, HITRUST CSF v11, SOC 2 Type II, PCI DSS v4.0, ISO/IEC 27001:2022 (D116)
- Compliance auto-detection from data types with ISSO confirmation gate (D110)
- Multi-regime assessment with crosswalk deduplication (D113)
- BaseAssessor ABC pattern reducing per-framework code to approximately 60 LOC (D116)
- DevSecOps profile management with 10 stages and 5 maturity levels (D119)
- Pipeline security generation with Kyverno and OPA policy-as-code (D121)
- Image signing and attestation management
- Zero Trust Architecture: 7-pillar maturity scoring aligned to DoD ZTA Strategy (D120)
- NIST SP 800-207 compliance assessment and gate (D118)
- Service mesh generation for Istio and Linkerd (D121)
- Network segmentation with namespace isolation and microsegmentation
- PDP/PEP configuration for DISA ICAM, Zscaler, Palo Alto (D124)
- ZTA posture score as cATO evidence dimension (D123)
- DoD MOSA compliance framework (10 U.S.C. section 4401) via BaseAssessor pattern (D127)
- Modularity analysis: coupling, cohesion, circular dependency detection (D129)
- ICD and TSP document generation with CUI markings (D128)
- MOSA code enforcement via static analysis (D129)
- MOSA auto-trigger for DoD/IC customers during intake (D125)
- CLI capabilities with 4 independent optional toggles and tenant ceiling (D132)
- Remote Command Gateway with 5 channel adapters: Telegram, Slack, Teams, Mattermost, internal chat (D133)
- 8-gate security chain for remote commands (D136)
- IL-aware response filtering (D135)
- User binding ceremony for remote command authorization (D136)
- Air-gapped mode auto-disabling internet-dependent channels (D139)
- Command allowlist with per-channel overrides (D137)
- Auto-scaling: HPA manifests for all 15 agents + dashboard + API gateway (D141)
- Pod Disruption Budgets with tier-based policies (D143)
- Cross-AZ topology spread constraints (D144)
- Cloud-agnostic node autoscaler reference (D142)

## [Phase 19-22] - 2026-01-XX

### Added
- Agentic application generation producing mini-ICDEV™ clone child applications (D44-D53)
- 6-dimension fitness scoring for agentic suitability assessment (D46)
- Blueprint-driven generation with 12-step pipeline (D47)
- Grandchild prevention mechanism (D52)
- CSP MCP integration registry for AWS, GCP, Azure, Oracle
- FIPS 199 security categorization with SP 800-60 information types and high watermark (D54)
- FIPS 200 validation across all 17 minimum security areas (D55)
- CNSSI 1253 overlay auto-application for IL6/SECRET (D57)
- Dynamic SSP baseline selection from categorization (D56)
- SaaS multi-tenancy platform with API gateway (D58-D65)
- Per-tenant database isolation with strongest isolation model (D60)
- 3 authentication methods: API key, OAuth 2.0/OIDC, CAC/PIV
- 3 subscription tiers: Starter, Professional, Enterprise
- REST API and MCP Streamable HTTP transport (D62)
- Per-tenant K8s namespace provisioning (D63)
- Offline license keys with RSA-SHA256 signatures for on-prem (D64)
- Helm chart for on-prem deployment (D65)
- Federated FORGE marketplace with 3-tier catalog (D74-D81)
- 7-gate automated security scanning for marketplace assets (D76)
- IL-aware compatibility checking with high-watermark consumption rule (D77)
- Community ratings and reviews for marketplace assets
- Marketplace SBOM generation for executable assets (D81)
- LLM provider abstraction with vendor-agnostic routing (D66-D73)
- Function-level LLM routing for best-of-breed model selection (D68)
- Ollama support for air-gapped environments (D69)
- Vision LLM support for diagram extraction and UI analysis (D82-D87)

## [Phase 13-18] - 2025-XX-XX

### Added
- ATO acceleration: FedRAMP Moderate/High, CMMC Level 2/3, OSCAL generation (Phase 17)
- eMASS bidirectional sync with hybrid mode
- cATO continuous monitoring with evidence freshness tracking
- PI compliance velocity tracking
- Control crosswalk engine with dual-hub model
- Classification manager for IL2 through IL6
- MBSE integration: SysML XMI import, DOORS NG ReqIF import (Phase 18)
- Digital thread with auto-linking and coverage reporting
- Model-code generation from SysML block definitions
- Model-code drift detection and sync engine
- DoDI 5000.87 Digital Engineering Strategy compliance assessment
- Diagram extraction from SysML screenshots via vision LLM
- Application modernization: 7Rs assessment framework
- Version migration (Python 2.7 to 3.11, Java 8 to 17, etc.)
- Framework migration (Struts to Spring Boot, etc.)
- Monolith decomposition with microservice extraction
- Database migration DDL planning
- Strangler fig pattern management
- ATO compliance bridge for migration tracking

## [Phase 7-12] - 2025-XX-XX

### Added
- RICOAS Requirements Intake: conversational AI-driven intake with 5-stage pipeline
- Gap detection across 5 dimensions (completeness, clarity, feasibility, compliance, testability)
- SAFe decomposition: Epic, Capability, Feature, Story, Enabler with WSJF scoring
- Document extraction from SOW/CDD/CONOPS (shall/must/should statements)
- ATO boundary impact assessment with 4-tier classification (GREEN/YELLOW/ORANGE/RED)
- Supply chain intelligence: dependency graph, ISA lifecycle, SCRM assessment, CVE triage
- Digital Program Twin simulation: 6-dimension what-if analysis
- Monte Carlo estimation for schedule and cost
- COA generation (Speed/Balanced/Comprehensive) with comparison and selection
- External integration: bidirectional Jira, ServiceNow, GitLab sync
- DOORS NG ReqIF export
- Approval workflow management
- RTM traceability builder
- CI/CD integration: GitHub webhooks, GitLab webhooks, issue polling
- Observability: hook-based agent monitoring, HMAC-signed events, SIEM forwarding
- NLQ compliance queries via Bedrock with read-only SQL enforcement
- Parallel CI/CD via git worktree task isolation

## [Phase 1-6] - 2025-XX-XX

### Added
- Initial ICDEV™ platform with FORGE 6-layer agentic framework
- ANVIL and M-ANVIL build workflows (Architect, Navigate, Verify, Integrate, Launch)
- 15 multi-agent architecture across 3 tiers (Core, Domain, Support)
- A2A protocol (JSON-RPC 2.0 over mutual TLS) for inter-agent communication
- 14 MCP servers for Claude Code integration (stdio transport)
- Memory system: dual storage (markdown + SQLite), hybrid search (BM25 + semantic)
- Project management: create, list, status tracking
- TDD workflow: RED, GREEN, REFACTOR cycle with 6 language support
- Builder tools: scaffolding, code generation, linting, formatting
- NIST 800-53 Rev 5 compliance: SSP, POAM, STIG checklist, SBOM generation
- CUI marking system for IL4/IL5/IL6 artifacts
- DoD CSSP (DI 8530.01) compliance assessment
- CISA Secure by Design assessment
- IEEE 1012 IV&V assessment
- Security scanning: SAST (Bandit), dependency audit, secret detection, container scanning
- Infrastructure generation: Terraform, Ansible, K8s manifests, CI/CD pipelines
- Knowledge base with self-healing pattern detection
- Monitoring: log analysis, metrics, alerts, health checks
- Web dashboard with Flask (project status, compliance, security, agent management)
- STIG-hardened Docker containers for all agents
- Kubernetes manifests with network policies (default deny)
- Audit trail: append-only, immutable, NIST AU compliant

# [TEMPLATE: CUI // SP-CTI]
