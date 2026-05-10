"""Seed remaining wex (Workflow Execution) tasks into the Kanban board.

EP7  — Canvas Bridge (canvas_bridge.py + from-canvas API + trigger partial)
EP8  — Canvas Wiring (12 canvases in 4 parallel groups)
EP9  — Template Library (seed YAMLs + API route + UI rendering)
EP10 — Saved Workflows + Run History tab rendering
EP11 — Manifest + Companion chore + Playwright V&V
"""
# CUI // SP-CTI
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DB = _ROOT / "data" / "icdev.db"

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _insert(conn, task):
    conn.execute(
        """INSERT OR IGNORE INTO kanban_tasks
           (id, title, description, task_type, priority, status,
            scheduled_at, depends_on_task_id, executor_type, dispatch_source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            task["id"],
            task["title"],
            task["description"],
            task.get("task_type", "feature"),
            task.get("priority", "high"),
            task.get("status", "backlog"),
            task.get("scheduled_at", NOW),
            task.get("depends_on"),
            task.get("executor_type", "claude_cli"),
            "wex-seed",
        ),
    )


TASKS = [
    # ─── EP7: Canvas Bridge ───────────────────────────────────────────────────
    {
        "id": "wex-ep7-01",
        "title": "canvas_bridge.py — canvas→workflow context mapper",
        "priority": "critical",
        "description": """\
Create `tools/studio/canvas_bridge.py`.

Purpose: given a canvas_id (idc/ndc/sdc/bdc/pdc/odc/ddc/qdc/mdc/aadc/aimc/ohc)
return a dict with {description, category, steps} so the from-canvas API can
pre-populate a workflow draft without needing Ollama.

Structure:
```python
CANVAS_WORKFLOW_MAP = {
    "idc": {
        "description": "Infrastructure resource audit and IaC generation",
        "category": "deploy",
        "steps": [
            {"name": "Inventory Resources", "tool": "tools/infra_canvas/resource_scanner.py", "node_type": "tool"},
            {"name": "Generate IaC", "tool": "tools/iac/iac_generator.py", "node_type": "tool"},
            {"name": "Engineer Review", "tool": "", "node_type": "human", "role": "reviewer"},
        ],
    },
    "ndc": { ... },
    "sdc": { description: "Security posture assessment and STIG remediation", category: "security",
             steps: [scan → findings → remediation plan → ISSO approval] },
    "bdc": { description: "Boundary diagram → accreditation package", category: "compliance",
             steps: [boundary scan → diagram → authorization] },
    "pdc": { description: "CI/CD pipeline validation and deployment", category: "build",
             steps: [lint → test → security scan → deploy] },
    "odc": { description: "Observability stack — SLO definition and runbook generation", category: "general",
             steps: [SLO define → runbook generate → alert configure → oncall approve] },
    "ddc": { description: "Data lineage audit and schema migration", category: "general",
             steps: [lineage scan → schema check → migration plan → DBA approve] },
    "qdc": { description: "Quality gate — test coverage and DORA metrics", category: "testing",
             steps: [run tests → coverage report → dora metrics → QA approve] },
    "mdc": { description: "Cloud migration wave planning and execution", category: "deploy",
             steps: [discovery → wave plan → migration execute → validate] },
    "aadc": { description: "Agentic AI workflow — agent chain design and test", category: "general",
              steps: [agent design → safety review → test run → PM approve] },
    "aimc": { description: "ML model training, evaluation and deployment", category: "build",
              steps: [data prep → train → evaluate → deploy → monitor] },
    "ohc": { description: "Operations runbook automation and incident response", category: "general",
             steps: [runbook generate → SLA check → deploy → ops approve] },
}

def get_canvas_workflow_template(canvas_id: str) -> dict:
    \"\"\"Return pre-filled workflow dict for a canvas_id, or a generic fallback.\"\"\"
    ...

def list_canvas_ids() -> list[str]:
    \"\"\"Return all supported canvas_ids.\"\"\"
    return list(CANVAS_WORKFLOW_MAP.keys())
```

Assign step IDs (step_1, step_2, ...), depends_on chain, timeout=300, required=True.
No imports from Flask or dashboard — pure data module.

Registration checklist:
- Add entry to tools/manifest/studio.md (in EP11)
""",
    },
    {
        "id": "wex-ep7-02",
        "title": "API route — POST /api/studio/workflows/from-canvas",
        "priority": "critical",
        "depends_on": "wex-ep7-01",
        "description": """\
Add route to `tools/dashboard/api/studio.py`:

```python
@studio_bp.route("/workflows/from-canvas", methods=["POST"])
def create_workflow_from_canvas():
    \"\"\"Pre-populate a workflow from a canvas context.\"\"\"
    body = request.get_json(silent=True) or {}
    canvas_id = body.get("canvas_id", "")
    context_label = body.get("context_label", "")   # e.g. topology name or design name
    from tools.studio.canvas_bridge import get_canvas_workflow_template
    template = get_canvas_workflow_template(canvas_id)
    if context_label:
        template["description"] = f"{template['description']} — {context_label}"
    # Persist as a new workflow draft
    import uuid, yaml
    from tools.studio.workflow_editor import save_workflow
    wf_id = str(uuid.uuid4())
    wf_name = f"{canvas_id.upper()} Workflow" + (f" — {context_label}" if context_label else "")
    yaml_str = yaml.dump(template, default_flow_style=False, allow_unicode=True)
    save_workflow(wf_id, wf_name, yaml_str, category=template.get("category","general"))
    return jsonify({"workflow_id": wf_id, "name": wf_name, "redirect": f"/studio/workflows?load={wf_id}"})
```

Important:
- Import guard: if canvas_bridge import fails return 503 with error
- Validate canvas_id is in list_canvas_ids() → 400 if unknown
- save_workflow must handle the case where the function doesn't exist yet; check tools/studio/workflow_editor.py for the correct save API
""",
    },
    {
        "id": "wex-ep7-03",
        "title": "Shared partial — templates/includes/workflow_trigger_btn.html",
        "priority": "critical",
        "depends_on": "wex-ep7-01",
        "description": """\
Create `tools/dashboard/templates/includes/workflow_trigger_btn.html`.

This Jinja2 partial is included by any canvas template page to render a
"Send to Workflow Studio" button that pre-populates a workflow from the current
canvas context.

Variables the including template must set before include:
- `wf_canvas_id`      — e.g. "sdc"
- `wf_context_label`  — e.g. current design name or topology name (can be empty)

Content:
```html
<!-- workflow_trigger_btn.html -->
<button class="btn btn-sm btn-outline-primary ms-2"
        id="wf-trigger-btn-{{ wf_canvas_id }}"
        onclick="sendToWorkflowStudio('{{ wf_canvas_id }}', '{{ wf_context_label }}')"
        title="Open this context as a Workflow in Workflow Studio">
  &#9881; Workflow
</button>
<script>
function sendToWorkflowStudio(canvasId, contextLabel) {
  const btn = document.getElementById('wf-trigger-btn-' + canvasId);
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  fetch('/api/studio/workflows/from-canvas', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({canvas_id: canvasId, context_label: contextLabel})
  })
  .then(r => r.json())
  .then(d => { if (d.redirect) window.open(d.redirect, '_blank'); })
  .catch(e => { console.error(e); if (btn) { btn.disabled = false; btn.textContent = '⚙ Workflow'; } });
}
</script>
```

Also mirror to `icdev/tools/dashboard/templates/includes/workflow_trigger_btn.html` if the icdev/ mirror directory exists.
""",
    },

    # ─── EP8: Canvas Wiring (4 parallel groups, all depend on ep7-03) ─────────
    {
        "id": "wex-ep8-01",
        "title": "Wire workflow trigger into idc + ndc + sdc canvas templates",
        "depends_on": "wex-ep7-03",
        "description": """\
Add `{% include 'includes/workflow_trigger_btn.html' %}` (with wf_canvas_id set) to:
- `tools/dashboard/templates/infra_canvas/` — main infra canvas page header area
- `tools/dashboard/templates/network/` — network topology detail or list page header
- `tools/dashboard/templates/security_canvas/` — security design detail page header

For each canvas, find the primary page template (the one rendered by the main route, not sub-pages).
Set the Jinja2 variables before the include:
```jinja
{% set wf_canvas_id = "idc" %}   {# or "ndc" / "sdc" #}
{% set wf_context_label = current_design.name if current_design else "" %}
{% include 'includes/workflow_trigger_btn.html' %}
```

Place the button near the top action bar (next to export/print buttons).
Do NOT break existing template structure — insert only the set+include lines.
""",
    },
    {
        "id": "wex-ep8-02",
        "title": "Wire workflow trigger into bdc + pdc + odc canvas templates",
        "depends_on": "wex-ep7-03",
        "description": """\
Same pattern as wex-ep8-01 but for:
- `tools/dashboard/templates/boundary_canvas/` — wf_canvas_id="bdc"
- `tools/dashboard/templates/pipeline/` — wf_canvas_id="pdc"
- `tools/dashboard/templates/observability_canvas/` — wf_canvas_id="odc"

Find the main page template for each canvas, add set + include near the action bar.
""",
    },
    {
        "id": "wex-ep8-03",
        "title": "Wire workflow trigger into ddc + qdc + mdc canvas templates",
        "depends_on": "wex-ep7-03",
        "description": """\
Same pattern for:
- `tools/dashboard/templates/data_canvas/` — wf_canvas_id="ddc"
- `tools/dashboard/templates/qdc_canvas/` — wf_canvas_id="qdc"
- `tools/dashboard/templates/migration_canvas/` — wf_canvas_id="mdc"

Find the main page template for each, add set + include near the action bar.
""",
    },
    {
        "id": "wex-ep8-04",
        "title": "Wire workflow trigger into aadc + aimc + ohc canvas templates",
        "depends_on": "wex-ep7-03",
        "description": """\
Same pattern for:
- `tools/dashboard/templates/agentic_ai_canvas/` — wf_canvas_id="aadc"
- `tools/dashboard/templates/aiml_canvas/` — wf_canvas_id="aimc"
- `tools/dashboard/templates/ops_hub/` — wf_canvas_id="ohc"

Find the main page template for each, add set + include near the action bar.
""",
    },

    # ─── EP9: Template Library ────────────────────────────────────────────────
    {
        "id": "wex-ep9-01",
        "title": "Seed 18 YAML workflow templates in context/workflow_templates/",
        "description": """\
Create directory `context/workflow_templates/` (if not exists) and seed 18 YAML files.

12 canvas-specific templates (one per canvas, filename = <canvas_id>_workflow.yaml):
Each file is a valid workflow YAML with description, category, and 3-5 steps.
Use tool paths from the real tool catalog (tools/compliance/, tools/security/, etc.).

6 general templates:
- fedramp_ato_pipeline.yaml — FIPS 199 → NIST 800-53 → ISSO → SSP → SBOM → ATO package
- cmmc_assessment.yaml — CMMC level 2 assessment → gap analysis → POA&M → remediation
- cicd_security_gate.yaml — lint → bandit scan → SBOM → container scan → deploy gate
- govcon_proposal.yaml — SAM.gov scan → bid decision → proposal gen → color review → submit
- incident_response.yaml — alert triage → runbook exec → postmortem → SLO update
- data_lineage_audit.yaml — lineage scan → quality check → drift detect → report

Template YAML format (must be importable by the existing YAML import in Workflow Studio):
```yaml
description: "FedRAMP ATO Pipeline — end-to-end authorization package"
category: compliance
tags: [fedramp, ato, compliance]
author: ICDEV™
steps:
  - id: step_1
    name: FIPS 199 Categorization
    tool: tools/compliance/fips199_categorizer.py
    depends_on: []
    args: {}
    timeout: 300
    required: true
    node_type: tool
  - ...
```

No Python code needed — just 18 YAML files.
""",
        "task_type": "chore",
    },
    {
        "id": "wex-ep9-02",
        "title": "API route — GET /api/studio/workflows/templates",
        "depends_on": "wex-ep9-01",
        "description": """\
Add route to `tools/dashboard/api/studio.py`:

```python
@studio_bp.route("/workflows/templates", methods=["GET"])
def list_workflow_templates():
    \"\"\"List all built-in workflow YAML templates from context/workflow_templates/.\"\"\"
    import yaml as _yaml
    from pathlib import Path
    tpl_dir = Path(__file__).resolve().parents[3] / "context" / "workflow_templates"
    templates = []
    if tpl_dir.exists():
        for f in sorted(tpl_dir.glob("*.yaml")):
            try:
                data = _yaml.safe_load(f.read_text(encoding="utf-8"))
                templates.append({
                    "id": f.stem,
                    "name": f.stem.replace("_", " ").title(),
                    "description": data.get("description", ""),
                    "category": data.get("category", "general"),
                    "tags": data.get("tags", []),
                    "author": data.get("author", "ICDEV™"),
                    "steps_count": len(data.get("steps", [])),
                    "yaml": f.read_text(encoding="utf-8"),
                })
            except Exception:
                pass
    # Group stats
    categories = {}
    for t in templates:
        categories[t["category"]] = categories.get(t["category"], 0) + 1
    return jsonify({
        "templates": templates,
        "total": len(templates),
        "builtin": len(templates),
        "community": 0,
        "categories": categories,
    })
```

Also update `wex-ep9-03` template tab JS to call this route.
""",
    },
    {
        "id": "wex-ep9-03",
        "title": "Template Library tab — JS rendering + Load button",
        "depends_on": "wex-ep9-02",
        "description": """\
Update `tools/dashboard/static/js/workflow-studio-exec.js` to implement
`StudioWF.loadTemplates()` which is called when the Template Library tab is shown.

Implementation:
```javascript
StudioWF.loadTemplates = async function() {
  const grid = document.getElementById('wf-template-grid');
  if (!grid) return;
  grid.innerHTML = '<div class="studio-spinner studio-spinner--lg" style="margin:48px auto;"></div>';
  try {
    const data = await fetch('/api/studio/workflows/templates').then(r => r.json());
    // Update stat counters
    const bi = document.getElementById('tpl-builtin-count');
    const co = document.getElementById('tpl-community-count');
    const ca = document.getElementById('tpl-category-count');
    if (bi) bi.textContent = data.builtin || 0;
    if (co) co.textContent = data.community || 0;
    if (ca) ca.textContent = Object.keys(data.categories || {}).length;
    // Render cards
    if (!data.templates || data.templates.length === 0) {
      grid.innerHTML = '<div class="studio-empty"><div class="studio-empty__title">No templates</div></div>';
      return;
    }
    grid.innerHTML = data.templates.map(t => `
      <div class="studio-card studio-animate-in" style="cursor:pointer;" onclick="StudioWF.installTemplate(${JSON.stringify(JSON.stringify(t))})">
        <div class="studio-card__header">
          <span class="studio-badge studio-badge--${_categoryColor(t.category)}">${t.category}</span>
          <span style="font-size:0.75rem;color:var(--studio-text-muted);">${t.steps_count} steps</span>
        </div>
        <div class="studio-card__title">${t.name}</div>
        <div class="studio-card__description" style="font-size:0.8rem;">${t.description}</div>
        <div style="margin-top:8px;">
          ${(t.tags||[]).map(tag=>`<span class="studio-badge studio-badge--neutral" style="font-size:0.7rem;">${tag}</span>`).join(' ')}
        </div>
        <div style="margin-top:12px;">
          <button class="studio-btn studio-btn--primary studio-btn--sm"
                  onclick="event.stopPropagation();StudioWF.installTemplate(${JSON.stringify(JSON.stringify(t))})">
            Load Template
          </button>
        </div>
      </div>
    `).join('');
  } catch(e) {
    grid.innerHTML = '<div class="studio-empty"><div class="studio-empty__title">Failed to load templates</div></div>';
  }
};

StudioWF.installTemplate = function(tJson) {
  const t = typeof tJson === 'string' ? JSON.parse(tJson) : tJson;
  StudioWF.switchTab(document.querySelector('[data-tab="editor"]'));
  StudioWF.doImportYAML(t.yaml);
  if (document.getElementById('wf-name')) document.getElementById('wf-name').value = t.name;
  StudioWF.toast('Template loaded: ' + t.name, 'success');
};
```

Helper `_categoryColor(cat)` maps category → badge color (success/warning/info/neutral).
Also hook `loadTemplates()` into the tab switch handler so it's called on first view.
Wire into `StudioWF.switchTab()`: when tab === 'templates', call `StudioWF.loadTemplates()`.
""",
    },

    # ─── EP10: Saved Workflows + Run History tab rendering ───────────────────
    {
        "id": "wex-ep10-01",
        "title": "Saved Workflows tab — JS rendering with Edit/Run/Delete actions",
        "description": """\
Update `tools/dashboard/static/js/workflow-studio-exec.js` to implement
`StudioWF.loadSavedWorkflows()` — called when the Saved Workflows tab is shown.

Implementation:
```javascript
StudioWF.loadSavedWorkflows = async function() {
  const grid = document.getElementById('wf-saved-grid');
  const countEl = document.getElementById('wf-saved-count');
  if (!grid) return;
  try {
    const data = await fetch('/api/studio/workflows').then(r => r.json());
    const wfs = data.workflows || [];
    if (countEl) countEl.textContent = wfs.length;
    if (wfs.length === 0) {
      grid.innerHTML = `<div class="studio-empty">...</div>`;
      return;
    }
    grid.innerHTML = wfs.map(w => `
      <div class="studio-card studio-animate-in">
        <div class="studio-card__header">
          <span class="studio-badge studio-badge--${_categoryColor(w.category)}">${w.category||'general'}</span>
          <span style="font-size:0.75rem;color:var(--studio-text-muted);">${w.steps_count||0} steps</span>
        </div>
        <div class="studio-card__title">${w.name}</div>
        <div class="studio-card__description" style="font-size:0.78rem;color:var(--studio-text-muted);">
          ${w.description||''}
        </div>
        <div style="margin-top:12px;display:flex;gap:6px;">
          <button class="studio-btn studio-btn--primary studio-btn--sm"
                  onclick="StudioWF.loadWorkflow('${w.id}')">Edit</button>
          <button class="studio-btn studio-btn--success studio-btn--sm"
                  onclick="StudioWF.loadWorkflow('${w.id}',true)"
                  style="background:#22c55e;border-color:#22c55e;color:#fff;">&#9654; Run</button>
          <button class="studio-btn studio-btn--ghost studio-btn--sm"
                  onclick="StudioWF.deleteWorkflow('${w.id}','${w.name}')">Delete</button>
        </div>
      </div>
    `).join('');
  } catch(e) {
    grid.innerHTML = '<div class="studio-empty"><div class="studio-empty__title">Failed to load</div></div>';
  }
};

StudioWF.loadWorkflow = function(id, runAfter=false) {
  // Switch to editor tab, fetch workflow YAML, populate canvas, optionally run
  StudioWF.switchTab(document.querySelector('[data-tab="editor"]'));
  fetch('/api/studio/workflows/' + id).then(r => r.json()).then(w => {
    StudioWF.doImportYAML(w.yaml || '');
    if (document.getElementById('wf-name')) document.getElementById('wf-name').value = w.name;
    StudioWF._currentWorkflowId = id;
    const runBtn = document.getElementById('wf-run-btn');
    const codeBtn = document.getElementById('wf-gen-code-btn');
    if (runBtn) runBtn.style.display = '';
    if (codeBtn) codeBtn.style.display = '';
    if (runAfter) setTimeout(() => StudioWF.run(), 400);
  });
};

StudioWF.deleteWorkflow = async function(id, name) {
  if (!confirm('Delete workflow "' + name + '"?')) return;
  await fetch('/api/studio/workflows/' + id, {method: 'DELETE'});
  StudioWF.loadSavedWorkflows();
  StudioWF.toast('Deleted: ' + name, 'info');
};
```

Also hook `loadSavedWorkflows()` into tab switch (tab === 'saved').
""",
    },
    {
        "id": "wex-ep10-02",
        "title": "Run History tab — JS rendering with step-breakdown modal",
        "description": """\
Update `tools/dashboard/static/js/workflow-studio-exec.js` to make the Run History tab
fully functional. Currently `StudioWF.loadRunHistory()` is partially wired; complete it.

```javascript
StudioWF.loadRunHistory = async function() {
  const tbody = document.getElementById('wf-runs-body');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;"><div class="studio-spinner"></div></td></tr>';
  try {
    const data = await fetch('/api/studio/workflows/runs').then(r => r.json());
    const runs = data.runs || [];
    if (runs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="studio-text-muted" style="text-align:center;padding:32px;">No runs yet</td></tr>';
      return;
    }
    tbody.innerHTML = runs.map(r => `
      <tr>
        <td>${r.workflow_name||r.workflow_id||'-'}</td>
        <td>${_runStatusBadge(r.status)}</td>
        <td>${r.steps_total||0} / ${r.steps_done||0} done</td>
        <td>${r.duration_s ? r.duration_s + 's' : '-'}</td>
        <td style="font-size:0.75rem;">${r.started_at||'-'}</td>
        <td>
          <button class="studio-btn studio-btn--ghost studio-btn--sm"
                  onclick="StudioWF.showRunDetail('${r.run_id}')">Detail</button>
        </td>
      </tr>
    `).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="6" class="studio-text-muted" style="text-align:center;">Failed to load</td></tr>';
  }
};
```

`_runStatusBadge(status)` renders colored badge (running→info, success→success, failed→danger, cancelled→neutral).

`StudioWF.showRunDetail(runId)` already partially exists — verify it fetches
`GET /api/studio/workflows/runs/{runId}` (includes steps array), renders a table of step
name / status / duration / stdout preview in the `#wf-run-detail-modal`.

Hook into tab switch (tab === 'runs').
""",
    },

    # ─── EP11: Manifest + Companion + V&V ─────────────────────────────────────
    {
        "id": "wex-ep11-01",
        "title": "Manifest — update tools/manifest/studio.md with new modules",
        "task_type": "chore",
        "depends_on": "wex-ep9-02",
        "description": """\
Update `tools/manifest/studio.md` (create if it doesn't exist under tools/manifest/).

Add entries for these new modules:
- `tools/studio/workflow_runner.py` — per-run SSE execution engine; start_run(), stream_run(), get_run(), list_runs()
- `tools/studio/workflow_chat.py` — chat-to-YAML; generate_workflow_yaml(user_message, conversation_history)
- `tools/studio/canvas_bridge.py` — canvas→workflow template mapper; get_canvas_workflow_template(canvas_id), list_canvas_ids()
- `context/workflow_templates/` — 18 pre-built YAML workflow templates (12 canvas-specific + 6 general)
- `tools/dashboard/templates/includes/workflow_trigger_btn.html` — reusable "Send to Workflow" partial

Format: follow the existing manifest shard format (name, path, purpose, key functions, CLI usage).
""",
    },
    {
        "id": "wex-ep11-02",
        "title": "Companion sync + coherence check",
        "task_type": "chore",
        "depends_on": "wex-ep11-01",
        "description": """\
Run the mandatory post-implementation sync sequence:

```bash
python tools/dx/companion.py --sync --write --json
python tools/workflow/coherence_checker.py --all --fix --gate
```

If coherence_checker fails, fix the flagged issues before marking done.
Log output to .tmp/wex-companion-sync.log.
""",
    },
    {
        "id": "wex-ep11-03",
        "title": "Playwright V&V — full workflow lifecycle E2E test",
        "task_type": "test",
        "priority": "critical",
        "depends_on": "wex-ep11-02",
        "description": """\
Run a full end-to-end Playwright verification of the Workflow Studio execution feature.

Test scenarios (all via Playwright MCP or playwright-cli):

1. **Chat → Generate → Save → Run**
   - Navigate to http://localhost:5050/studio/workflows
   - Click AI Chat button
   - Type: "ATO pipeline — FIPS categorization, ISSO approval, SSP generation"
   - Click Send, wait for YAML to appear (up to 30s for Ollama)
   - Click Apply to load into canvas
   - Verify nodes appear in canvas
   - Click Save Workflow
   - Verify Run button appears
   - Click Run
   - Verify progress bar appears and SSE events arrive (step_started/step_done)
   - Wait for run to complete (success or failed — either is fine)

2. **Template Library**
   - Click Template Library tab
   - Verify cards appear (>0)
   - Click Load Template on any card
   - Verify canvas populates with nodes

3. **Run History**
   - Click Run History tab
   - Verify the run from scenario 1 appears in the table
   - Click Detail — verify step breakdown modal opens

4. **Canvas Bridge (one canvas)**
   - Navigate to /security/ask or any SDC page
   - Verify "⚙ Workflow" button is present
   - Click it — verify new tab opens at /studio/workflows?load=<id>

Screenshot each step. Save to playwright/screenshots/wex_e2e_<step>.png.
Report pass/fail for each scenario. If any scenario fails, fix the root cause before marking done.
""",
    },
]


def main():
    conn = sqlite3.connect(str(DB))
    inserted = 0
    skipped = 0
    for task in TASKS:
        before = conn.execute("SELECT 1 FROM kanban_tasks WHERE id=?", (task["id"],)).fetchone()
        _insert(conn, task)
        if not before:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    conn.close()
    print(f"Done. Inserted: {inserted}  Skipped (already existed): {skipped}")
    print("Tasks seeded:")
    for t in TASKS:
        dep = f" → depends_on {t['depends_on']}" if t.get("depends_on") else ""
        print(f"  {t['id']} [{t.get('priority','high')}]{dep}")


if __name__ == "__main__":
    main()
