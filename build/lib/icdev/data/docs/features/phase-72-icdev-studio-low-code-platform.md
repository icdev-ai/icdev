# Phase 72: ICDEV™ Studio — Low-Code/No-Code Platform

**CUI // SP-CTI**

## Executive Summary

ICDEV™ Studio is a visual, AI-first low-code platform that transforms ICDEV™ from a developer-centric CLI framework into an accessible platform for Program Managers, ISSOs, Contracting Officers, and business analysts. It competes head-to-head with Appian ($APPN, FedRAMP High, IL5, 200+ agencies) while offering three decisive advantages Appian cannot match:

1. **AI-First Generation** — "Describe what you want" → working app (Base44-style UX, gov-grade compliance)
2. **Real Code Output** — Generates actual Python/Java/Go/Rust/TS/C#, not proprietary platform apps
3. **IL6/SECRET + Air-Gap** — Full SIPR capability that Appian stops at IL5

**Target users:** Non-technical government stakeholders who currently need developers to operate ICDEV™.

---

## Competitive Positioning

### Why This Matters Now

| Platform | Users | Revenue | Gov Presence | Weakness ICDEV™ Exploits |
|----------|-------|---------|--------------|------------------------|
| **Appian** | Enterprise | ~$600M ARR | FedRAMP High, IL5, 200+ agencies | Proprietary lock-in, no IL6, opaque pricing, no code generation |
| **Base44** | SMB/Startup | Acquired $80M | None (SOC 2 only) | Zero gov compliance, vendor lock-in, immature security |
| **n8n** | Technical teams | ~$50M ARR | Air-gap capable, no FedRAMP | No compliance, no gov focus, workflow-only (not full SDLC) |
| **ICDEV™ Studio** | Gov + Commercial | — | FedRAMP, CMMC, IL4/IL5/IL6 | NEW — this plan |

### ICDEV™ Studio Value Proposition

> "The only platform where a Program Manager can describe a need in plain English, get a compliant application with full ATO artifacts, and deploy to GovCloud — without writing a single line of code."

**For Program Managers:** Describe requirements conversationally → get a working app with compliance artifacts
**For ISSOs:** Visual compliance workflow builder → drag-and-drop STIG/POAM/SSP pipelines
**For Contracting Officers:** Case management for procurement lifecycle → proposal tracking without spreadsheets
**For Business Analysts:** Build custom dashboards, forms, and automations → no developer dependency
**For Developers:** Everything above + full code access, extensibility, and override capability

---

## Architecture: Six Pillars

### Pillar 1: Natural Language App Builder (Base44-style)
**"Describe what you want → get a working app"**

Wraps the existing child app generator (`tools/builder/child_app_generator.py`) with a conversational AI layer.

**User Flow:**
```
User: "I need an app that tracks CDRL deliverables for my Navy contract,
       sends alerts when due dates approach, and generates CPARS-ready reports."

ICDEV™ Studio:
  1. Intake engine extracts requirements (tools/requirements/intake_engine.py)
  2. Blueprint generator creates capability map (tools/builder/app_blueprint.py)
  3. Visual preview shows: 3 pages, 2 workflows, 5 DB tables, IL4 classification
  4. User confirms or adjusts via visual editor
  5. Child app generator builds it (tools/builder/child_app_generator.py)
  6. Compliance artifacts auto-generated (SSP, POAM, SBOM)
  7. App is live on dashboard with monitoring
```

**New Components:**
- `tools/studio/nl_app_builder.py` — NL prompt → blueprint → generation pipeline
- `tools/studio/blueprint_preview.py` — JSON blueprint → visual preview (pages, tables, workflows)
- `tools/dashboard/templates/studio/app_builder.html` — Conversational app creation UI
- `tools/dashboard/api/studio.py` — Studio API endpoints

**Builds On:** intake_engine.py, app_blueprint.py, child_app_generator.py, nlq_processor.py

---

### Pillar 2: Visual Workflow Studio
**"Drag-and-drop compliance and business workflows"**

Visual DAG editor that wraps `tools/orchestration/workflow_composer.py`.

**Capabilities:**
- **Tool palette** — Browse all 200+ ICDEV™ tools, grouped by category (compliance, security, build, deploy)
- **Canvas editor** — Drag tools onto canvas, draw dependency arrows, configure args via forms
- **Template library** — Start from existing templates (ATO acceleration, build/deploy, security hardening)
- **Custom workflows** — Users create their own, ICDEV™ validates and executes
- **Compliance workflows** — Pre-built: STIG check → POAM generate → SSP update → OSCAL export
- **Real-time execution** — Watch steps execute with live status, logs, and output previews
- **Workflow marketplace** — Share/discover workflows across organizations

**New Components:**
- `tools/studio/workflow_editor.py` — Workflow CRUD, validation, template management
- `tools/dashboard/templates/studio/workflow_studio.html` — Visual DAG editor (JS canvas)
- `tools/dashboard/static/js/workflow-studio.js` — DAG rendering (nodes, edges, status indicators)
- `tools/dashboard/static/css/workflow-studio.css` — Studio styling

**Serialization:** Visual canvas → YAML template (same format as `args/workflow_templates/*.yaml`) → workflow_composer.py executes. No new runtime — the visual layer is pure UI.

**User-Created Custom Workflows:**
When a user describes a custom workflow in natural language:
1. NLQ-style parser identifies tool references and dependencies
2. Studio generates a draft YAML template
3. Visual editor shows the DAG for user review/adjustment
4. User saves → template lands in their workspace's `args/workflow_templates/`
5. ICDEV™ validates (no circular deps, tools exist, args valid)

---

### Pillar 3: Form Builder & Case Management
**"Create forms and track cases without code"**

**Form Builder:**
- Drag-and-drop form designer for intake questionnaires, proposal templates, compliance checklists
- Field types: text, number, date, dropdown, multi-select, file upload, rich text, signature
- Conditional logic: show/hide fields based on answers
- Validation rules: required, regex, range, custom
- Output: JSON schema → auto-generates DB table + API endpoints + Jinja2 template
- Pre-built form templates: RFP response, compliance questionnaire, risk assessment, change request

**Case Management:**
- Visual lifecycle designer (states + transitions + rules)
- Pre-built lifecycles: Finding → Triage → Remediate → Verify → Close
- Customizable: users define their own states, transitions, and automation triggers
- SLA tracking with escalation rules
- Assignment rules (round-robin, skill-based, manual)
- Activity timeline with audit trail
- Dashboard widgets: case volume, aging, SLA compliance, assignment load

**New Components:**
- `tools/studio/form_builder.py` — Form schema CRUD, field validation, template management
- `tools/studio/case_manager.py` — Case lifecycle engine, state machine, SLA tracking
- `tools/dashboard/templates/studio/form_builder.html` — Visual form designer
- `tools/dashboard/templates/studio/case_manager.html` — Case management dashboard
- `tools/dashboard/static/js/form-builder.js` — Drag-and-drop form editor
- `tools/dashboard/static/js/case-manager.js` — Lifecycle designer + case board

**Database:**
- `studio_forms` — Form definitions (JSON schema)
- `studio_form_submissions` — Form responses
- `studio_cases` — Case instances
- `studio_case_types` — Lifecycle definitions (states, transitions)
- `studio_case_history` — State change audit trail (append-only)
- `studio_sla_rules` — SLA definitions and escalation config

---

### Pillar 4: Marketplace Storefront
**"Browse, preview, and one-click install"**

Visual marketplace UI replacing CLI-only `tools/marketplace/install_manager.py`.

**Features:**
- **Category browsing** — Skills, goals, hardprompts, context, args, compliance extensions, workflows, forms, case types
- **Search with filters** — By framework, IL level, language, rating, author, compatibility
- **Asset preview** — README, screenshots, dependency tree, compliance certifications, install impact
- **One-click install** — Dependency resolution, compatibility check, sandbox test, install
- **Rating & reviews** — Community feedback, usage stats, quality scores
- **Publisher dashboard** — For asset creators: publish, version, analytics
- **Federation** — Cross-org marketplace sync (existing `tools/marketplace/federation_sync.py`)
- **OpenClaw bridge** — Import from ClawHub with 10-gate security scan (existing `tools/marketplace/openclaw_bridge.py`)

**New Components:**
- `tools/dashboard/templates/studio/marketplace.html` — Visual storefront
- `tools/dashboard/static/js/marketplace.js` — Search, filter, preview, install UX
- `tools/dashboard/api/marketplace_ui.py` — Storefront API (wraps existing marketplace tools)

**Builds On:** catalog_manager.py, search_engine.py, install_manager.py, compatibility_checker.py, review_queue.py, publish_pipeline.py, federation_sync.py, openclaw_bridge.py

---

### Pillar 5: Citizen Automation Studio
**"If X happens, do Y — no code required"**

Visual rule builder for creating Genesis-like reflexes and event-driven automations.

**Rule Types:**
- **Trigger** — Event occurs (new finding, SLA breach, deployment, scan complete, form submitted, case state change)
- **Condition** — Filter (severity = critical, framework = CMMC, project = X)
- **Action** — Execute (run tool, send notification, create case, update field, trigger workflow, call API)

**Pre-built Automation Templates:**
- "When critical STIG finding detected → create case → assign to ISSO → send Slack alert"
- "When POAM item overdue → escalate to PM → block deployment gate"
- "When new SAM.gov opportunity matches capabilities → create proposal draft → notify BD team"
- "When compliance scan completes → generate report → email stakeholders"
- "When form submitted → create case → route to approver"

**Visual Builder:**
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  TRIGGER     │────▶│  CONDITION   │────▶│   ACTION    │
│ New Finding  │     │ Severity=CAT1│     │ Create Case │
│              │     │ AND          │     │ Notify ISSO │
│              │     │ Framework=   │     │ Block Deploy│
│              │     │   FedRAMP    │     │             │
└─────────────┘     └──────────────┘     └─────────────┘
```

**New Components:**
- `tools/studio/automation_builder.py` — Rule CRUD, trigger registration, condition evaluation, action dispatch
- `tools/studio/automation_runner.py` — Event listener daemon, rule matching, action execution
- `tools/dashboard/templates/studio/automations.html` — Visual rule builder
- `tools/dashboard/static/js/automation-builder.js` — Trigger/condition/action canvas

**Database:**
- `studio_automations` — Rule definitions
- `studio_automation_runs` — Execution history (append-only)
- `studio_automation_triggers` — Registered event triggers

**Integration with Genesis:** Citizen automations are a simplified, visual layer on top of Genesis reflexes. Advanced users can "eject" an automation into a full Genesis reflex YAML for deeper customization.

---

### Pillar 6: Dashboard Builder
**"Pin widgets, customize layouts, build your view"**

**Features:**
- **Widget library** — Metric cards, charts (bar, line, pie, radar), tables, forms, case boards, compliance gauges, agent status, workflow monitors
- **Layout grid** — Drag widgets into responsive grid (mobile/tablet/desktop)
- **Data binding** — Connect widgets to any NLQ query, API endpoint, or database table
- **Role-based defaults** — PM sees project health; ISSO sees compliance posture; CO sees proposal pipeline
- **Save & share** — Named dashboards, org-wide or personal
- **Auto-refresh** — SSE-powered real-time updates (existing `tools/dashboard/sse_manager.py`)

**New Components:**
- `tools/studio/dashboard_builder.py` — Dashboard layout CRUD, widget registry, data binding
- `tools/dashboard/templates/studio/dashboard_builder.html` — Visual dashboard designer
- `tools/dashboard/static/js/dashboard-builder.js` — Grid layout + widget drag-and-drop

**Database:**
- `studio_dashboards` — Dashboard definitions (layout JSON)
- `studio_widgets` — Widget library (type, config schema, default size)

---

## Air-Gap Classification

Features available to ALL users vs. air-gap-only:

### Available Everywhere (Commercial + GovCloud + Air-Gap)
| Feature | Rationale |
|---------|-----------|
| Visual Workflow Studio | Pure local execution, YAML-based |
| Form Builder | Local DB, no external deps |
| Case Management | Local DB, no external deps |
| Citizen Automation Studio | Local event processing |
| Dashboard Builder | Local rendering, SSE |
| NL App Builder (with Ollama) | Local LLM, no cloud needed |
| Marketplace (local catalog) | SQLite-based catalog |
| Workflow execution | subprocess + local tools |

### Requires Network (Commercial + GovCloud, NOT Air-Gap)
| Feature | Rationale |
|---------|-----------|
| NL App Builder (Claude/cloud LLM) | Cloud LLM API call for higher quality |
| Marketplace federation sync | Cross-org HTTP sync |
| OpenClaw/ClawHub bridge | External registry |
| SAM.gov automation triggers | External API |
| Email/Slack notification actions | External services |
| Web-sourced research in automations | Internet access |

### Air-Gap Only (IL6/SECRET/SIPR)
| Feature | Rationale |
|---------|-----------|
| Classification banner enforcement | CUI/SECRET markings mandatory |
| NSA Type 1 encryption for all data | IL6 requirement |
| Audit trail tamper-proofing (HSM) | Enhanced integrity for classified |
| Cross-domain guard integration | SIPR↔NIPR data transfer |
| Air-gap LLM only (Ollama/local) | No external model access |

---

## Implementation Phases

### Phase 72a: Foundation (Visual Workflow Studio + Marketplace Storefront)
**Why first:** Workflow Studio is the backbone — every other pillar creates or consumes workflows. Marketplace gives immediate visual value with existing 10+ asset modules.

**Deliverables:**
- Visual DAG editor (JS canvas + YAML serialization)
- Tool palette with all 200+ ICDEV™ tools
- Template library browser
- Real-time workflow execution monitor
- Marketplace storefront UI (browse, search, preview, install)
- 4 new DB tables, 2 new API blueprints, 2 new dashboard pages

**Estimated scope:** ~15 new files, ~3,000 LOC

---

### Phase 72b: Natural Language App Builder
**Why second:** This is the "wow factor" — the Base44-style experience that differentiates ICDEV™ from Appian.

**Deliverables:**
- Conversational app creation UI
- Blueprint preview (visual capability map)
- NL → blueprint → child app generation pipeline
- Guided refinement ("Would you like to add compliance tracking?")
- App gallery (browse previously generated apps)

**Estimated scope:** ~8 new files, ~2,000 LOC

---

### Phase 72c: Form Builder + Case Management
**Why third:** Forms and cases are the most requested capabilities by non-technical gov users (Appian's #1 selling point).

**Deliverables:**
- Drag-and-drop form designer
- 10 field types with conditional logic
- Form → DB table auto-generation
- Case lifecycle designer (states + transitions)
- Case board (Kanban view)
- SLA tracking with escalation
- 6 new DB tables, pre-built templates

**Estimated scope:** ~10 new files, ~3,500 LOC

---

### Phase 72d: Citizen Automation Studio
**Why fourth:** Builds on forms + cases + workflows — automations connect them all.

**Deliverables:**
- Visual trigger/condition/action rule builder
- 20+ pre-built automation templates
- Event listener daemon
- Genesis reflex "eject" capability
- Automation run history + debugging

**Estimated scope:** ~8 new files, ~2,500 LOC

---

### Phase 72e: Dashboard Builder + Polish
**Why last:** The dashboard builder lets users create custom views of everything built in 72a-72d.

**Deliverables:**
- Widget library (15+ widget types)
- Drag-and-drop grid layout
- Data binding to NLQ queries, APIs, DB tables
- Role-based default dashboards (PM, ISSO, CO, Analyst)
- Save/share/export dashboards

**Estimated scope:** ~6 new files, ~2,000 LOC

---

## Database Schema (New Tables)

```sql
-- Studio Forms
CREATE TABLE studio_forms (
    form_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    schema_json TEXT NOT NULL,  -- JSON Schema for fields
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','published','archived'))
);

CREATE TABLE studio_form_submissions (
    submission_id TEXT PRIMARY KEY,
    form_id TEXT NOT NULL REFERENCES studio_forms(form_id),
    data_json TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at TEXT DEFAULT (datetime('now'))
);

-- Studio Cases
CREATE TABLE studio_case_types (
    type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lifecycle_json TEXT NOT NULL,  -- {states: [], transitions: [], sla_rules: []}
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE studio_cases (
    case_id TEXT PRIMARY KEY,
    type_id TEXT NOT NULL REFERENCES studio_case_types(type_id),
    title TEXT NOT NULL,
    description TEXT,
    current_state TEXT NOT NULL,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('critical','high','medium','low')),
    assigned_to TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    due_date TEXT,
    form_submission_id TEXT REFERENCES studio_form_submissions(submission_id)
);

CREATE TABLE studio_case_history (
    history_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES studio_cases(case_id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    changed_by TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    comment TEXT
);
-- APPEND-ONLY: studio_case_history (audit trail)

-- Studio Automations
CREATE TABLE studio_automations (
    automation_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    trigger_json TEXT NOT NULL,   -- {event_type, filters}
    condition_json TEXT,          -- {rules: [{field, operator, value}]}
    action_json TEXT NOT NULL,    -- [{action_type, config}]
    enabled INTEGER DEFAULT 1,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE studio_automation_runs (
    run_id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL REFERENCES studio_automations(automation_id),
    trigger_event TEXT,
    status TEXT CHECK(status IN ('triggered','running','success','failed','skipped')),
    result_json TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
-- APPEND-ONLY: studio_automation_runs (audit trail)

-- Studio Dashboards
CREATE TABLE studio_dashboards (
    dashboard_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    layout_json TEXT NOT NULL,   -- {grid: [{widget_id, x, y, w, h, config}]}
    role_default TEXT,           -- null = personal, 'pm'|'isso'|'co' = role default
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    shared INTEGER DEFAULT 0
);

-- Studio Workflows (user-created, supplements args/workflow_templates/)
CREATE TABLE studio_workflows (
    workflow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    template_yaml TEXT NOT NULL,  -- Same format as args/workflow_templates/*.yaml
    category TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    version INTEGER DEFAULT 1,
    shared INTEGER DEFAULT 0
);
```

---

## Key Technical Decisions

### D361: Build visual workflow engine, do not embed n8n
- **Why:** n8n is fair-code (not OSI open-source), cannot redistribute as part of ICDEV™. Building our own keeps ICDEV™'s architecture clean and avoids license complications for gov contracts.
- **Trade-off:** More development effort, but full control over UX and compliance integration.

### D362: Canvas rendering via vanilla JS + SVG, no heavy framework
- **Why:** Air-gap deployable, no npm build step, consistent with existing dashboard (Jinja2 + vanilla JS). Mermaid.js already bundled for diagrams.
- **Trade-off:** Less polished than React Flow or similar, but zero external dependencies.

### D363: Forms serialize to JSON Schema (draft-07)
- **Why:** Industry standard, validatable, portable. Can generate OpenAPI specs, DB schemas, and HTML forms from the same schema.

### D364: Case state machines use finite-state-machine pattern
- **Why:** Deterministic, auditable, testable. Each transition is logged to append-only history. Matches NIST AU requirements.

### D365: Citizen automations are event-sourced
- **Why:** Every trigger, condition evaluation, and action execution is recorded. Full replay capability for debugging and compliance audit.

### D366: NL App Builder uses two-tier LLM (Ollama draft + Claude refine)
- **Why:** Consistent with existing LLM architecture. Ollama handles initial blueprint generation (free, air-gap safe). Claude refines for quality (optional, commercial only).

---

## Appian Feature Parity Matrix

| Appian Capability | ICDEV™ Studio Equivalent | Phase |
|-------------------|------------------------|-------|
| Visual Process Modeler | Visual Workflow Studio | 72a |
| Data Fabric | NLQ + SQLite + RAG (existing) | — |
| Case Management Studio | Studio Case Manager | 72c |
| Form Builder | Studio Form Builder | 72c |
| AI Copilot | Chat + NLQ + NL App Builder (existing + 72b) | 72b |
| RPA | Citizen Automation Studio | 72d |
| Process Mining | Workflow execution analytics | 72a |
| Document Processing (IDP) | doc_extractor.py (existing) | — |
| Connected Systems | Marketplace + integrations (existing) | 72a |
| Mobile Apps | Responsive templates (existing) | — |
| FedRAMP High | FedRAMP + CMMC + IL4/5/6 (existing) | — |
| Expression Language | Python tools + NLQ | — |

**ICDEV™-only advantages (no Appian equivalent):**
- NL → full application generation (Base44-style)
- Real code output (6 languages, not proprietary)
- IL6/SECRET/SIPR deployment
- Multi-cloud LLM (not locked to AWS Bedrock)
- Open FORGE architecture (not black box)
- SBOM/SLSA/supply-chain built-in
- Full SDLC (TDD/BDD, CI/CD, IaC) not just app building
- Marketplace with federated cross-org sync

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Non-technical user task completion | 80%+ tasks without developer help | Studio usage analytics |
| Time to first app (NL builder) | < 5 minutes from prompt to live app | Generation pipeline timing |
| Workflow creation (visual) | 10x faster than YAML editing | Comparison study |
| Form creation | < 2 minutes for standard forms | Form builder analytics |
| Case resolution time | 30% reduction vs manual tracking | Case lifecycle metrics |
| Marketplace installs via UI | 90%+ via storefront (vs CLI) | Install source tracking |
| Automation rules created by non-devs | 50+ per org per month | Automation analytics |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Visual editor complexity | Phase incrementally; start with workflow DAG (simplest canvas) |
| JS bundle size (air-gap) | Vanilla JS + SVG, no React/Angular/Vue; Mermaid already bundled |
| Form builder scope creep | Limit to 10 field types initially; extensible via JSON Schema |
| NL app quality | Two-tier LLM + human review step before generation |
| Appian switching costs | Target *new* projects, not Appian migration (different value prop) |
| Performance at scale | SQLite → PostgreSQL migration path already exists (Phase 68) |
