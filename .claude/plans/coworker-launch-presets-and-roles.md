# Plan: Co-Worker Engine Launch Presets + Expanded Role Catalog

## Goal
1. Add a **preset quick-launch panel** to the `/coworker/` Launch Box — dropdown + tag chips that auto-fill the textarea with pre-built QA / build / compliance / etc. prompts.
2. Add **6 new common roles** mapped to ICDEV canvases (security, compliance, data, devops, requirements, business).
3. Associate each preset with a **canvas tag** so users know which domain they are launching into.

---

## Part 1 — Launch Presets (`args/ace/launch_presets.yaml`)

A single YAML file consumed by the blueprint and rendered in the template.

```yaml
presets:
  - label: "QA — Lint + Security Scan"
    icon: "🔍"
    canvas: "qa"
    prompt: "Run ruff, bandit, and pytest across the ACE canvas modules (icdev/tools/ace/*.py). Report F401 unused imports, security findings, coverage gaps, and type errors as a markdown QA report with severity tags."
    suggested_roles: [ai_developer, qa_manager]

  - label: "QA — API Contract Validation"
    icon: "🔌"
    canvas: "qa"
    prompt: "QA the ACE API endpoints at /api/ace/*: verify HTTP status codes (200, 404, 409, 500), JSON response schemas, and parameterization safety. Document any deviations from the manifest spec."
    suggested_roles: [ai_developer, qa_manager]

  - label: "Build — Scaffold New Canvas"
    icon: "🏗️"
    canvas: "build"
    prompt: "Scaffold a new ICDEV canvas called 'ops_canvas' with: blueprint.py, db/init_db.py, templates/ops_canvas/index.html, constants.py, and IQE adapter. Follow the 8-point dashboard completeness gate (template, route, module, constants, migration, nav, IQE, mini-bar)."
    suggested_roles: [ai_developer, devops_engineer]

  - label: "Compliance — NIST 800-53 Gap Analysis"
    icon: "🛡️"
    canvas: "compliance"
    prompt: "Audit the current codebase against NIST 800-53 AC-3, AU-6, and CM-3 controls. Map existing implementations to controls, identify gaps, and generate a POAM-style remediation plan with prioritized tasks."
    suggested_roles: [compliance_manager, security_analyst]

  - label: "Security — ZIG Pillar Assessment"
    icon: "🔐"
    canvas: "security"
    prompt: "Assess the ZIG (Zero Trust Implementation Guide) canvas at /security/zig/. Verify all 7 pillars have populated capability counts, active assessment routes, and up-to-date roadmaps. Flag any missing data or broken links."
    suggested_roles: [security_analyst, compliance_manager]

  - label: "Data — Mapping Pipeline QA"
    icon: "📊"
    canvas: "data"
    prompt: "Validate the AI Data Mapping canvas at /data/mapping/. Test the field-mapper engine with synthetic datasets, check for data-type mismatches, null-handling, and performance on 1k-row inputs. Produce a quality scorecard."
    suggested_roles: [data_analyst, ai_developer]

  - label: "DevOps — Deployment Health Check"
    icon: "🚀"
    canvas: "devops"
    prompt: "Run a full DevOps health check: verify dashboard (:5050), API gateway (:8443), PostgreSQL connectivity, Kanban scheduler, and Genesis daemon are all healthy. Report any wedged processes or port conflicts."
    suggested_roles: [devops_engineer, system_monitor]

  - label: "Requirements — PRD Intake Review"
    icon: "📋"
    canvas: "requirements"
    prompt: "Review the latest PRD intake session for completeness: acceptance criteria, traceability matrix, risk register, and testability. Identify ambiguous requirements and propose clarifying questions."
    suggested_roles: [requirements_engineer, business_analyst]

  - label: "Business — Proposal Win/Loss Review"
    icon: "💼"
    canvas: "business"
    prompt: "Analyze recent proposal outcomes (win/loss records) in the CPMP canvas. Identify patterns in evaluator scores, competitor pricing, and compliance gaps. Recommend BD strategy adjustments."
    suggested_roles: [business_analyst, compliance_manager]
```

---

## Part 2 — New Role YAMLs (`args/ace/roles/`)

| Role File | Role ID | Display Name | Trust | LLM Function | Key Steps |
|-----------|---------|--------------|-------|--------------|-----------|
| `security_analyst.yaml` | `security_analyst` | Security Analyst | yellow | `security_analysis` | scan_vulnerabilities, audit_controls, assess_zig_pillar, report_findings |
| `compliance_manager.yaml` | `compliance_manager` | Compliance Manager | yellow | `compliance_analysis` | map_controls, gap_analysis, generate_poams, crosswalk_frameworks |
| `data_analyst.yaml` | `data_analyst` | Data Analyst | yellow | `data_analysis` | ingest_data, profile_schema, detect_anomalies, generate_quality_scorecard |
| `devops_engineer.yaml` | `devops_engineer` | DevOps Engineer | yellow | `infrastructure_planning` | probe_health, check_ports, validate_configs, restart_services |
| `requirements_engineer.yaml` | `requirements_engineer` | Requirements Engineer | yellow | `requirement_analysis` | parse_prd, build_traceability, identify_ambiguity, propose_clarifications |
| `business_analyst.yaml` | `business_analyst` | Business Analyst | yellow | `business_analysis` | analyze_win_loss, benchmark_competitors, price_to_win, recommend_strategy |

These extend the existing `ai_developer.yaml` and `qa_manager.yaml` patterns.

---

## Part 3 — Backend Changes

### `icdev/tools/ace/blueprint.py`
- Add `GET /api/ace/presets` — reads `args/ace/launch_presets.yaml`, returns JSON grouped by canvas.
- Add `GET /api/ace/roles` — returns all loaded roles (already exists indirectly via `ACEController.list_roles()`; expose as a lightweight JSON list for the template).

### `icdev/tools/ace/problem_classifier.py`
- `_DOMAIN_ROLES` already references the new role IDs; no change needed if role YAMLs exist.
- The `_CANVAS_KEYWORDS` mapping (if any) should include the new canvas tags so presets can influence classification.

---

## Part 4 — Frontend Changes (`tools/dashboard/templates/coworker/index.html`)

### Launch Box Enhancement
1. **Preset dropdown** above the textarea:
   ```html
   <select id="ace-preset-select">
     <option value="">— Quick Launch Preset —</option>
     <optgroup label="QA">
       <option value="qa-lint">🔍 QA — Lint + Security Scan</option>
       ...
     </optgroup>
   </select>
   ```
2. **Tag chips** below the dropdown, grouped by canvas:
   - Clicking a chip auto-fills the textarea and highlights the chip.
   - Chips are color-coded by canvas (e.g., QA = green, Security = red, Build = blue).
3. **Suggested roles badge** — when a preset is selected, a small line shows "Team: ai_developer + qa_manager".
4. **Auto-fill behavior**:
   - Selecting a preset fills the textarea.
   - User can edit before launching.
   - Clearing the textarea resets the preset selection.

### CSS (added to the existing `<style>` block)
- `.preset-chip` — inline tag style
- `.preset-group` — flex wrap container
- `.preset-canvas-qa { border-color: #27ae60; }` etc.

### JS
- Fetch `/api/ace/presets` on page load, populate dropdown and chips.
- `applyPreset(id)` — fills textarea, updates role preview.
- `clearPreset()` — resets.

---

## Part 5 — Tests

| Test File | What It Tests |
|-----------|---------------|
| `tests/test_ace_presets.py` | `GET /api/ace/presets` returns valid JSON, all canvases represented, no duplicate labels |
| `tests/test_ace_roles.py` | New roles load via `RoleLoader`, required fields present, `get_role()` works |
| Playwright E2E | Select a preset → textarea filled → click Launch → instance created with correct trigger_source |

---

## Files to Modify / Create
| File | Action |
|------|--------|
| `args/ace/launch_presets.yaml` | **Create** — 9 presets |
| `args/ace/roles/security_analyst.yaml` | **Create** |
| `args/ace/roles/compliance_manager.yaml` | **Create** |
| `args/ace/roles/data_analyst.yaml` | **Create** |
| `args/ace/roles/devops_engineer.yaml` | **Create** |
| `args/ace/roles/requirements_engineer.yaml` | **Create** |
| `args/ace/roles/business_analyst.yaml` | **Create** |
| `icdev/tools/ace/blueprint.py` | Add `GET /api/ace/presets`, `GET /api/ace/roles` |
| `tools/dashboard/templates/coworker/index.html` | Add preset UI (dropdown, chips, auto-fill, role preview) |
| `tests/test_ace_presets.py` | **Create** |
| `tests/test_ace_roles.py` | **Create** or extend existing |
| `tools/manifest/ace-coworker-engine.md` | Document presets and roles |

---

## Assumptions
- The `problem_classifier.py` `_DOMAIN_ROLES` dict already lists the new role IDs; it only needs the YAML files to exist for `RoleLoader` to validate them.
- The preset YAML is hand-curated, not auto-generated from canvas metadata.
- The frontend uses vanilla JS (no new dependencies); chips are CSS-only.
- If `launch_presets.yaml` is missing, the endpoint returns `[]` and the UI degrades gracefully (shows no presets).

## Success Criteria
1. User visits `/coworker/` and sees a **Quick Launch** dropdown with 9 presets grouped by canvas.
2. Selecting a preset auto-fills the textarea with a detailed, actionable prompt.
3. Clicking **Launch Team** creates an instance; the detail page shows the appropriate roles were assigned.
4. All 8 roles (2 old + 6 new) appear on `/coworker/roles`.
5. Tests pass: `test_ace_presets.py`, `test_ace_roles.py`, existing ACE tests.
