# Workflow Forms Canvas (WFC)

Promotes the existing Studio form builder and workflow editor into a first-class
canvas with enterprise branding, form-to-workflow integration, and multi-format export.

## Key Files

| File | Purpose |
|------|---------|
| `tools/workflow_canvas/blueprint.py` | Flask blueprint — all WFC routes and API endpoints |
| `tools/workflow_canvas/constants.py` | INDUSTRY_CATEGORIES, EXPORT_FORMATS, color defaults |
| `tools/workflow_canvas/export_engine.py` | export_form() / export_workflow() → PPT/PDF/DOCX |
| `tools/workflow_canvas/form_node.py` | FORM_INTAKE_TOOL definition for DAG integration |
| `tools/workflow_canvas/db/migrations/001_wfc_init.sql` | wfc_branding, wfc_workflow_form_nodes tables |
| `tools/studio/form_builder.py` | FIELD_TYPES (10), FORM_TEMPLATES (20+ across 10 industries) |
| `tools/iqe/adapters/wfc.py` | IQE adapter — wfc.forms, wfc.workflows, wfc.templates, wfc.submissions |
| `args/component_registry.yaml` | Canvas registration under key: wfc |

## Routes

- `GET /workflow-canvas/` — index
- `GET /workflow-canvas/forms` — form library
- `GET /workflow-canvas/forms/new` — new form builder
- `GET /workflow-canvas/forms/<id>` — detail
- `GET /workflow-canvas/forms/<id>/edit` — edit
- `POST /workflow-canvas/api/forms/<id>/export/<pptx|pdf|docx>` — export download
- `GET /workflow-canvas/workflows` — workflow library
- `GET /workflow-canvas/workflows/<id>` — detail
- `GET /workflow-canvas/templates` — industry template library (10 industries, 20+ templates)
- `POST /workflow-canvas/api/branding/<type>/<id>` — save enterprise branding

## Export Functions

- `export_form(form_id, fmt, conn)` → `(bytes, filename)` — delegates to pptx_builder / build_pdf / python-docx
- `export_workflow(workflow_id, fmt, conn)` → `(bytes, filename)`

## Industry Templates (20+)

Government/Federal, Healthcare, Finance/Banking, Legal, HR, IT/Security,
Construction/Engineering, Education, Manufacturing, Consulting/PMO
