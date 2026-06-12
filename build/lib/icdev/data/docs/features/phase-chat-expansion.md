# Phase Chat Expansion — /chat Use Cases Catalog & Workflow Engine

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | Chat Expansion |
| Title | /chat Use Cases Catalog & Workflow Engine |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 51 (Unified Chat Dashboard), Phase 44 (Innovation Adaptation — chat + extension hooks), Phase 60 (CPMP), Phase 50 (AI Governance) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-05-18 |

---

## 1. Problem Statement

The `/chat` interface previously required operators to manually construct intake sessions from scratch. There was no guided starting point, no pre-seeded expert context, and no way to combine related workflows. Operators building a budget package, an ATO submission, or an incident response plan all began from an empty prompt — duplicating setup work every time, missing domain-specific readiness requirements, and leaving AI Boost under-utilized.

This expansion introduces a curated, FORGE-pattern use case catalog that eliminates cold-start friction. Each use case ships with an expert system prompt, a seed message, RICOAS readiness requirements, canvas wiring, quick actions, and optional workflow steps. Operators select a use case from the sidebar panel, the system creates a typed chat context, auto-seeds the requirements pool, and AI Boost fires automatically when readiness falls below the configured threshold. Advanced operators can merge multiple use cases into a chain, overriding individual use case settings for their tenant, exporting bundles for reuse, or running use cases as standalone offline applications.

---

## 2. Goals

1. Seed a 13-use-case catalog across five categories (modernization, budget, knowledge, compliance, acquisition) in `args/use_cases.yaml`
2. Redesign the `/chat` left sidebar to expose a searchable, collapsible "Common Use Cases" panel below the context list
3. Implement a 5-step launch wizard (select → configure → review requirements → activate → monitor) with category-specific user-config panels (archetypes)
4. Build a chain mode that union-merges `template_requirements` across multiple use cases with dedup and priority preservation
5. Implement YAML bundle export and multipart import for sharing use cases across tenants
6. Generate category-aware standalone HTML apps from any use case (zero external dependencies, embedded column manager + summary bar)
7. Implement a workflow step engine that tracks structured progression through multi-step use cases
8. Build a canvas seeding pattern that pre-instantiates templates and snippets for a use case at activation time
9. Design tenant_id RLS using an empty-string sentinel so global (YAML) use cases match all tenants while allowing per-tenant DB overrides
10. Order `/use-cases/import` before `/<id>` in the Flask blueprint to prevent RLS from capturing the import request as a tenant-scoped lookup

---

## 3. Architecture

```
                   /chat Use Cases Expansion
    ┌──────────────────────────────────────────────────────┐
    │              args/use_cases.yaml                      │
    │  (FORGE seed: 13 use cases, ~1800 lines)              │
    │  id, label, category, icon, badge, agent_model,       │
    │  ricoas, fast_track, boost_threshold, system_prompt,  │
    │  seed_message, canvas_wiring, quick_actions,          │
    │  user_config, template_requirements, canvas_seeds,    │
    │  workflow_steps                                        │
    └───────────────────────┬──────────────────────────────┘
                            │  loaded at startup
                            ↓
          ┌─────────────────────────────────┐
          │  chat_use_cases table (SQLite)   │
          │  DB overrides union-merge on     │
          │  top of YAML defaults            │
          │  RLS: tenant_id='' sentinel      │
          └──────────────┬──────────────────┘
                         │
        ┌────────────────┼────────────────────┐
        ↓                ↓                    ↓
   Sidebar Panel    Chain Mode           Standalone HTML
   (chat.js)        /api/chat/chains     /api/.../standalone
        │                │                    │
   searchable        merge req pool       zero-dep HTML file
   collapse toggle   dedup by text        column manager
   launch wizard     activate → seed      budget summary bar
        │                │
        ↓                ↓
   ChatContext       RICOAS session
   (typed, with      (auto-seeded reqs
   use_case_id       + canvas seeds +
   in extra_context) AI Boost trigger)
        │
        ↓
   Workflow Step Engine
   POST .../workflow-step
   → uc_workflow_step in extra_context
```

The YAML file is the authoritative definition store. The database layer holds tenant-specific overrides and user-created use cases. At query time, the RLS WHERE clause matches `tenant_id = ? OR tenant_id = ''`, so a YAML-sourced row (empty string tenant) is always visible regardless of the caller's tenant context.

---

## 4. Use Case Catalog (13 entries)

| ID | Label | Category | Model | RICOAS | Fast Track |
|----|-------|----------|-------|--------|------------|
| `general_modernization` | General Modernization | modernization | Opus | yes | no |
| `year_end_budget_sprint` | Year-End Budget Sprint | budget | Opus | yes | yes |
| `document_refresh` | Crowd-Sourced Doc Refresh | knowledge | Sonnet | no | no |
| `ato_package_builder` | ATO Package Builder | compliance_ato | Opus | yes | no |
| `cdrl_generator` | CDRL Generator | acquisition | Sonnet | yes | no |
| `incident_response_plan` | Incident Response Plan | compliance | Opus | yes | no |
| `program_status_review` | Program Status Review | it_operations | Opus | yes | no |
| `sbom_attestation` | SBOM & Supply Chain Attestation | compliance | Sonnet | yes | no |
| `fedramp_auth_prep` | FedRAMP Authorization Prep | compliance_ato | Opus | yes | no |
| `privacy_impact_assessment` | Privacy Impact Assessment | compliance | Sonnet | yes | no |
| `section_508_audit` | Section 508 Accessibility Audit | compliance | Sonnet | no | no |
| `grant_tech_proposal` | Grant Tech Proposal | acquisition | Opus | yes | no |
| `cjis_compliance_prep` | CJIS Compliance Prep | compliance | Opus | yes | no |

Each entry declares a `boost_threshold` (default 70) — when RICOAS readiness falls below this value after the intake session is created, AI Boost fires automatically to fill gaps.

**Adding new use cases:** add an entry to `args/use_cases.yaml` only — no Python changes required. The loader reads the file at startup and upserts into the DB.

---

## 5. Sidebar Redesign

The `/chat` left sidebar now contains three sections (top to bottom):

1. **Active Contexts** — existing chat threads
2. **Common Use Cases** — new panel, below context list
   - Search input (client-side filter, debounced 200 ms)
   - Collapse/expand toggle persisted in `localStorage`
   - Use case cards: icon + label + category badge + description excerpt
   - Clicking a card enters the launch wizard

JS functions in `chat.js`:
- `loadUseCases()` — GET `/api/chat/use-cases`, strips system_prompt/seed_message for list performance
- `renderUseCases(useCases)` — builds card HTML, wires click handlers
- `startUseCase(id)` — fetches full detail (GET `/api/chat/use-cases/<id>`), opens wizard
- `initUseCasesPanel()` — registers search input, collapse toggle, initial load

---

## 6. Launch Wizard and Category Archetypes

Clicking a use case card opens a 5-step launch wizard overlay:

| Step | Label | What Happens |
|------|-------|--------------|
| 1 | Select | Use case detail loaded; description, badge, model, and quick actions displayed |
| 2 | Configure | Category-specific `user_config` panel rendered — see archetypes below |
| 3 | Review Requirements | `template_requirements` displayed as editable checklist; operator can toggle or annotate |
| 4 | Activate | POST to create ChatContext + RICOAS intake session; canvas seeds instantiated; seed message sent |
| 5 | Monitor | RICOAS readiness score rendered; AI Boost fires if score < `boost_threshold` |

### Category Archetypes

Each category maps to a specific `user_config` schema and UI panel:

**`budget` archetype**
- User config fields: `fiscal_year`, `ceiling_amount`, `fund_type` (O&M / RDTE / Procurement / MilCon)
- Column defaults: Vendor, Item, Qty, Estimate ($), Quotation ($), Expiration, POC, Description, Notes
- Extra summary bar: Est Total | Quotation Total | Variance (color-coded green/red)
- Quick actions: SOW template, IGCE spreadsheet, FAR/DFARS clause lookup

**`modernization` archetype**
- User config fields: `industries`, `equipment_types`, `vendors`, `migration_scope`
- Column defaults: Asset, 7R Classification, Phase, Dependencies, Status, Owner
- 7Rs tier dropdown (Retain / Retire / Replace / Replatform / Rehost / Refactor / Rearchitect)

**`compliance_ato` / `compliance` archetypes**
- User config fields: `classification_level`, `impact_level`, `agency_type`
- Federal agency type triggers OMB M-25-21 governance requirements automatically (Phase 50 integration)

**Default archetype** (all other categories)
- Column defaults: Item, Notes
- No extra summary bar

---

## 7. Chain Mode

Chain mode lets operators merge two or more use cases into a single intake session, combining their requirements pools.

**Endpoints:**
- `POST /api/chat/chains` — create chain (body: `{name, use_case_ids[]}`)
- `GET /api/chat/chains` — list chains for the calling tenant
- `POST /api/chat/chains/<chain_id>/activate` — activate: create RICOAS session, seed merged requirements, seed canvas artifacts

### Merge Algorithm (`_merge_chain_requirements`)

```
input:  ordered list of use_cases (each with template_requirements[])
output: deduplicated, priority-stable requirement list

for each use_case in use_cases:
    for each req in use_case.template_requirements:
        key = normalize(req.text)[:50]   # first 50 chars of lowercased/stripped text
        if key not in seen:
            seen[key] = req
            merged.append(req)
        else:
            # keep whichever has lower priority rank (higher urgency)
            if rank(req.priority) < rank(seen[key].priority):
                seen[key].priority = req.priority

return stable_sorted(merged, key=priority_rank)
```

Priority ranks: `critical=0`, `high=1`, `medium=2`, `low=3`. The merge never duplicates a requirement even when the same text appears in multiple use cases, but it promotes the requirement to the highest urgency declared by any member use case.

### Chain Data Structure

```python
{
    "id": "chain_YYYY-MM-DDTHH-MM-SS-MMMMMM",
    "tenant_id": str,
    "name": str,
    "use_case_ids": [str, ...],          # JSON array
    "merged_requirements": [req, ...],   # JSON array, deduplicated
    "linked_session_id": str | null,     # RICOAS session created on activate
    "status": "draft" | "active",
    "created_at": ISO 8601,
    "created_by": str,
    "updated_at": ISO 8601,
}
```

---

## 8. Export / Import Bundle Format

### Export (`GET /api/chat/use-cases/<use_case_id>/export`)

Returns a YAML bundle download (`{id}-bundle.yaml`):

```yaml
icdev_uc_bundle: "1.0"
use_cases:
  - id: year_end_budget_sprint
    label: "Year-End Budget Sprint"
    category: budget
    # ... all fields except is_user_created, created_by, created_at,
    #     updated_at, updated_by, tenant_id (stripped before export)
```

The `icdev_uc_bundle` key is the validation sentinel — import rejects files without it.

### Import (`POST /api/chat/use-cases/import`)

Accepts multipart file upload OR JSON body `{yaml_content: str, overwrite: bool}`.

**Conflict resolution:**
- If a YAML base entry exists with the same ID and `overwrite=false` → skip, record reason
- If a DB entry exists and `overwrite=false` → skip, record reason
- If `overwrite=true` → upsert both YAML catalog and DB row

**Response:**
```json
{
  "imported": ["year_end_budget_sprint"],
  "skipped": [{"id": "general_modernization", "reason": "already exists"}],
  "errors": []
}
```

---

## 9. Standalone App — Column Manager

`GET /api/chat/use-cases/<use_case_id>/standalone` returns a zero-dependency HTML file (`{id}-standalone.html`). The file is fully self-contained: all CSS, JavaScript, and initial data are inlined by `_build_standalone_html()`.

### Column Manager

Every standalone app includes an embedded column manager (accessible via ⚙ Columns toolbar button):

- Add columns — name + type (text / number / date / select / vendor)
- Remove columns — confirmed before removal
- Rename columns — inline edit
- Column order — drag-to-reorder

Supported column type keys: `item`, `notes`, `qty`, `estimate`, `quotation`, `vendor`, `tier`, `status`, `classification_7r`, `staleness`, `maturity`, `phase`, `description`, `note`

Default column sets are keyed first by `use_case_id`, then fall back to category archetype defaults (see Section 6). Budget apps auto-calculate Estimate Total, Quotation Total, and Variance in the summary bar whenever a number cell changes.

---

## 10. Workflow Step Engine

Use cases with multi-step workflows define `workflow_steps` in their YAML entry:

```yaml
workflow_steps:
  - step: 1
    label: IPT Agenda Review
    description: Review meeting agenda, attendees, and prior action items
    canvas: kanban
    action: prepare_agenda
  - step: 2
    label: Action Item & Deliverable Review
    description: Close prior action items, verify deliverable status
    canvas: kanban
    action: review_action_items
```

**Step advancement endpoint:** `POST /api/chat/use-cases/<use_case_id>/workflow-step`

Body: `{context_id: str, step: int, label: str, description: str, canvas: str, action: str}`

The endpoint merges the step state into the chat context's `extra_context` JSON column under keys `uc_workflow_step` and `uc_id`. This makes current workflow position available to the chat manager for system prompt augmentation and to the canvas wiring layer for targeted artifact generation.

**Current step is recoverable** — if a session is resumed after logout, the workflow step is reloaded from `extra_context` at context hydration.

---

## 11. Canvas Seeding Pattern

Canvas seeding pre-instantiates templates and snippets in a target canvas database when a use case is activated. This ensures that when the operator navigates to the wired canvas, relevant starting materials are already present.

**Configuration in use case YAML:**
```yaml
canvas_wiring:
  - migration_canvas
canvas_seeds:
  - canvas: migration_canvas
    templates: [hardware_refresh_plan, vendor_comparison]
    snippets: [7r_decision_tree]
```

**Execution (`_seed_canvas_artifacts`):**
1. Load canvas artifact catalog from `args/cloud_vendor_policy.yaml`
2. For each seed entry, validate the canvas key against the catalog
3. Open the canvas DB (`data/{canvas}.db` or equivalent)
4. Set security context to None — RLS bypass is intentional here; tenant isolation is enforced at the API boundary, not inside the seeding utility
5. Query `templates` and `snippets` tables by name; log warnings on missing entries (non-fatal, best-effort)
6. Return list of validated artifacts: `[{canvas, type, name}, ...]`

Seeding is **idempotent** — re-activating a use case does not duplicate templates.

---

## 12. RLS Design Decisions

### Empty-String Sentinel for Global Use Cases

Use cases defined in `args/use_cases.yaml` are loaded into the DB with `tenant_id = ''` (empty string). The RLS WHERE clause is:

```sql
WHERE tenant_id = ? OR tenant_id = ''
```

This means:
- YAML-sourced use cases (empty tenant) are always visible to every tenant — they are the global catalog
- Per-tenant overrides (non-empty tenant_id) are visible only to their owning tenant
- A tenant can override a global use case by inserting/upserting a row with the same `id` and their `tenant_id` — the DB row shadows the YAML entry for that tenant

This avoids a separate "global" table, keeping the schema simple while supporting full per-tenant customization.

### Route Ordering: `/import` Before `/<id>`

Flask matches routes in registration order. The import endpoint path is `/api/chat/use-cases/import` and the detail endpoint is `/api/chat/use-cases/<use_case_id>`. Without explicit ordering, Flask would match `/import` as the string literal value of `<use_case_id>`, and the detail handler (which applies RLS against the calling tenant) would attempt to look up a use case named `import` — returning 404 or leaking the tenancy check.

**Rule enforced in `tools/dashboard/api/chat.py` (line 649 comment):** the import route MUST be registered before the `/<id>` route. This is a structural constraint, not a runtime config.

The same principle applies to any route with a catch-all path parameter: `/<id>/intervene` must precede `/<id>/state`, etc.

---

## 13. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D340 | Use cases defined in `args/use_cases.yaml`, DB holds overrides only | FORGE D26 pattern: YAML is version-controlled, auditable, and diff-able; DB enables tenant customization without forking the catalog |
| D341 | Empty-string `tenant_id` sentinel for global use cases | Avoids a separate global table; single WHERE clause handles both global and tenant-scoped rows without a UNION |
| D342 | Import route registered before `/<id>` catch-all | Flask route-match order is deterministic; explicit ordering prevents `import` from being silently consumed as a use-case ID by the RLS-aware detail handler |
| D343 | Chain merge dedup key = first 50 chars of normalized text | Short prefix is sufficient for dedup in practice (requirements share few common prefixes); avoids LLM-based semantic dedup (expensive, non-deterministic) |
| D344 | Chain merge keeps lower priority rank (higher urgency) | Conservative: when two use cases disagree on a requirement's urgency, the system assumes the more urgent declaration is correct |
| D345 | Canvas seeding is best-effort (non-fatal on missing artifact) | Canvas catalogs evolve independently; a missing template name should not abort use case activation; operator is warned in the response metadata |
| D346 | RLS bypassed inside `_seed_canvas_artifacts()` with `conn.set_security_context(None)` | Seeding is an internal system operation; canvas DB tenant isolation is enforced at the API boundary (the calling endpoint checks the session tenant before invoking the seeder) |
| D347 | Export strips `is_user_created`, `created_by`, `created_at`, `updated_at`, `updated_by`, `tenant_id` | These are instance metadata, not use case definition; a bundle should be portable across tenants and installations |
| D348 | Standalone HTML is zero-dependency (all assets inlined by `_build_standalone_html()`) | Operationally deployed use cases may run in air-gapped or offline environments; no CDN or dashboard server should be required to use a standalone app |
| D349 | Workflow step stored in chat context `extra_context` JSON (not a separate table) | Step progression is conversational state, not a first-class audit record; `extra_context` JSON is the established pattern for mutable chat metadata |

---

## 14. Commands

```bash
# List all use cases
curl http://localhost:5050/api/chat/use-cases | python -m json.tool

# Get full detail (includes system_prompt + seed_message)
curl http://localhost:5050/api/chat/use-cases/year_end_budget_sprint | python -m json.tool

# Create a chain
curl -X POST http://localhost:5050/api/chat/chains \
  -H "Content-Type: application/json" \
  -d '{"name": "FY-End + ATO Sprint", "use_case_ids": ["year_end_budget_sprint", "ato_package_builder"]}'

# Activate a chain
curl -X POST http://localhost:5050/api/chat/chains/<chain_id>/activate | python -m json.tool

# Export a use case bundle
curl http://localhost:5050/api/chat/use-cases/year_end_budget_sprint/export -o fy-bundle.yaml

# Import a bundle
curl -X POST http://localhost:5050/api/chat/use-cases/import \
  -F "file=@fy-bundle.yaml"

# Download standalone app
curl http://localhost:5050/api/chat/use-cases/year_end_budget_sprint/standalone -o budget-sprint.html

# Advance workflow step
curl -X POST http://localhost:5050/api/chat/use-cases/program_status_review/workflow-step \
  -H "Content-Type: application/json" \
  -d '{"context_id": "ctx-xxx", "step": 2, "label": "Action Item Review", "canvas": "kanban", "action": "review_action_items"}'

# Add new use case (no Python changes needed):
# Edit args/use_cases.yaml, then restart dashboard or:
python tools/dashboard/app.py  # loader runs at startup
```

---

## 15. Related

- [Phase 51: Unified Chat Dashboard](phase-51-unified-chat-dashboard.md) — base chat infrastructure (ChatContext, ChatManager, extension hooks) that use cases activate into
- [Phase 44: Innovation Adaptation](phase-44-innovation-adaptation.md) — extension hook system (pre-LLM, post-tool) used by use case canvas wiring
- [Phase 50: AI Governance](phase-50-ai-governance-intake-chat.md) — federal agency type in `user_config` auto-triggers OMB M-25-21 requirements
- [Phase 60: CPMP](phase-60-cpmp.md) — `year_end_budget_sprint` wires CPMP Digital Twin artifacts via `canvas_wiring`
- [docs/security/sandbox-coverage.md](../security/sandbox-coverage.md) — standalone HTML and YAML bundle import coverage decisions (OPT-58)
