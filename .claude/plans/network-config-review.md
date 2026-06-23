# Plan: Network Configuration Review — Guided Role-Based AI Assistant

## User Intent
Add a new capability inside the Network Design Canvas (`/network/config-review`) that lets a user upload or paste a device configuration (Cisco IOS/NX-OS, Juniper JunOS, or generic), pick a network role, answer a short yes/no questionnaire, and receive AI-generated guidance with three tabs: **Security/Compliance**, **Optimization**, and **Remediation**. The output must include vendor-specific sample templates and detailed explanations. Findings must be actionable enough to generate a new device config or topology diagram, and the page must be tightly integrated with the ICDEV ecosystem (persistent audit trail, IQE query widget, registry-driven nav).

## Decisions received from user
1. **URL/menu:** `/network/config-review`, added under the existing Operate mega-menu as "Config Review".
2. **Persistence:** Yes — create DB tables and append-only audit events; support listing/revisiting past reviews.
3. **Ecosystem integration:** No one-click Kanban/RICOAS; instead, every finding must include actionable remediation/recommendation that can be used to generate a new config or new diagram.
4. **Role-adaptive questions:** Yes — per-role question bank shapes the LLM prompt.
5. **IQE:** Required by the 8-point dashboard-page completeness gate (see CLAUDE.md), so this page ships with IQE integration.

## Scope boundaries
- Build one new page inside the existing Network Canvas blueprint, not a new canvas.
- Reuse existing parsers (`tools.network.config_parser`, `tools.network.config_import`) and LLM router (`tools.llm.router`).
- Do NOT add broad network-wide refactoring or redesign the NDC menu structure.
- Do NOT add one-click Kanban/RICOAS task creation (user declined), but expose export/copy actions so users can paste findings into other ICDEV workflows.

## Proposed file changes

### 1. New backing module: `tools/network/config_review.py`
Pure functions, no Flask dependency.
- `ROLES`: dict of role metadata + per-role prompt focus and question bank.
- `generate_guided_prompts(role_key, config_summary)`: returns 5–10 prompt cards with title, preview, and full prompt text.
- `build_llm_prompt(role_key, config_text, vendor, answers, focus)`: constructs the prompt used for the main review.
- `parse_llm_review_response(raw_text)`: extracts JSON sections `security_compliance`, `optimization`, `remediation`, `sample_template`, `explanation`.
- `generate_config_from_findings(vendor, findings)`: deterministic fallback that builds a sanitized starter template when LLM is unavailable.

### 2. New constants: `tools/network/constants.py` additions (or `tools/network/config_review.py`)
- `_CONFIG_REVIEW_ROLES`: roles such as `network_engineer`, `network_architect`, `network_admin`, `security_auditor`, `technical_writer`.
- Per-role yes/no question banks (5–7 questions each) stored as structured dicts so the UI can render them without hardcoding HTML.

### 3. Database schema: `tools/network/db/init_db.py`
Add inside the existing `SCHEMA` string (network_canvas.db is self-managing via init_db.py):
- `nc_config_reviews` (id, title, vendor, role_key, answers_json, config_text_hash, status, created_at, updated_at)
- `nc_config_review_findings` (id, review_id, category, severity, title, detail, remediation, sample_config_snippet, generated_config_json, created_at)
- Append-only audit already uses `nc_audit`; add audit writes on create/submit/export.

Graceful handling: wrap new table creation in `CREATE TABLE IF NOT EXISTS` and catch `OperationalError` in the page so existing deployments that have not re-run init still work.

### 4. Blueprint routes: `tools/network/blueprint.py`
Add inside `create_network_blueprint()`:
- `GET /config-review` → render page.
- `POST /api/config-review` → create review record, parse config, return role + questions.
- `POST /api/config-review/<id>/analyze` → run LLM review, store findings, return JSON.
- `GET /api/config-review/<id>` → retrieve stored review + findings.
- `POST /api/config-review/<id>/export-config` → return deterministic generated config from findings.
- `POST /api/config-review/<id>/export-topology` → return topology graph dict for import into `/network/canvas/new`.
- `POST /network/api/iqe-query` → per-canvas IQE endpoint (8-point gate requirement).

All routes use `nc_login_required` and write to `nc_audit`.

### 5. Template: `tools/dashboard/templates/network/config_review.html`
Extends `network/base.html`.
- Section 1: upload or paste config; detect vendor; pick role from persona cards.
- Section 2: role-adaptive yes/no questionnaire (rendered from JSON).
- Section 3: prompt cards — 5–10 AI-guided prompts derived from role + answers.
- Section 4: results panel with three tabs:
  - **Security/Compliance**: findings list with severity badges and `Copy`/`Export config` actions.
  - **Optimization**: performance/management recommendations.
  - **Remediation**: step-by-step actions; each item can generate a starter config snippet.
- Include `includes/iqe_query_widget.html` with `iqe_api_route="/network/api/iqe-query"` and `iqe_canvas="ndc"`.
- Add a "New diagram from findings" button that POSTs to `/api/config-review/<id>/export-topology` and opens `/network/canvas/new` seeded with the result.

### 6. Network menu update: `tools/dashboard/templates/network/base.html`
Under the Operate mega-menu, add:
```html
<a href="/network/config-review" class="mega-item">Config Review</a>
```

### 7. IQE integration
- Extend `tools/iqe/adapters/ndc.py` with collections `network.config_reviews` and `network.config_review_findings`.
- Add the two collections to `args/component_registry.yaml` under the `ndc` component's `iqe.collections` list.
- Add `POST /network/api/iqe-query` route in `tools/network/blueprint.py` mirroring the pattern in `tools/ai_observatory/blueprint.py`.
- Add ≥3 seed queries in `context/iqe/queries/network/config_review.txt`.

### 8. LLM routing: `args/llm_config.yaml`
Add a new function entry `ndc_config_review` with appropriate provider/model chain and fallback so air-gap deployments degrade to Ollama/local.

### 9. Tests: `tests/test_network_config_review.py`
- Unit tests for `config_review.py`: role loading, question bank, prompt construction, parser.
- Blueprint route tests with test client: create review, analyze with mocked LLM, retrieve findings, export config/topology, IQE endpoint.
- Ruff + pytest must pass.

### 10. Companion sync & coherence
After implementation, run:
- `python tools/dx/companion.py --sync --write --json`
- `python tools/workflow/coherence_checker.py --all --fix --gate`

## Implementation approach
1. Create `tools/network/config_review.py` with deterministic prompt/question logic first.
2. Add DB tables to `tools/network/db/init_db.py`.
3. Add routes to `tools/network/blueprint.py` and the template `config_review.html`.
4. Add the menu link, IQE adapter/route/collections, seed queries, and LLM config entry.
5. Write tests, run `pytest tests/test_network_config_review.py`, then full suite.
6. Run ruff, bandit, coherence checker, and companion sync.
7. Verify with Playwright MCP: navigate to `/network/config-review`, upload a sample Cisco config, select role, answer questions, verify Security/Compliance/Optimization/Remediation tabs render.

## Success criteria
- `/network/config-review` loads and is reachable from the Operate menu.
- Uploading/pasting a Cisco or Juniper config returns a role selection + 5–10 guided prompts.
- Submitting the yes/no questionnaire returns an AI-generated report with the three required tabs.
- Each tab contains vendor-appropriate sample snippets and detailed explanation.
- Findings can be exported as a starter config or as a topology graph for the canvas.
- IQE widget on the page can answer "show config reviews with CAT1 findings" and similar queries.
- All tests pass; coherence checker reports zero new high-severity issues.

## Risks / assumptions
- **Assumption:** Existing `tools.network.config_parser.detect_vendor()` is accurate enough for the upload use case; if not, we add a small correction step in the UI.
- **Assumption:** LLM provider returns parseable structured text. We wrap parsing in a deterministic fallback (`generate_config_from_findings`) so the page still works when the LLM is offline.
- **Risk:** The NDC `blueprint.py` is very large (577 KB); changes will be localized to the bottom of the function to minimize merge conflicts.
