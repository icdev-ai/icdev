# Workflow Forms Canvas (WFC)

Promotes the existing Studio form builder and workflow editor into a first-class
canvas with enterprise branding, form-to-workflow integration, and multi-format export.

## Key Files

| File | Purpose |
|------|---------|
| `tools/workflow_canvas/blueprint.py` | Flask blueprint — all WFC routes and API endpoints |
| `tools/workflow_canvas/constants.py` | INDUSTRY_CATEGORIES, EXPORT_FORMATS, color defaults |
| `tools/workflow_canvas/export_engine.py` | export_form() / export_workflow() → PPT/PDF/DOCX |
| `tools/workflow_canvas/db/migrations/001_wfc_init.sql` | wfc_branding table |
| `tools/studio/form_builder.py` | FIELD_TYPES (10), FORM_TEMPLATES (20+ across 10 industries) |
| `tools/iqe/adapters/wfc.py` | IQE adapter — wfc.forms, wfc.workflows, wfc.templates, wfc.submissions |
| `tools/workflow_canvas/doc_regenerator.py` | Process-Ify Document Regenerator — regenerates source documents after a workflow is processified, preserving style/tone/structure via LLM fingerprinting. Exports DOCX/PDF via pptx_builder / build_pdf / python-docx. |
| `tools/workflow_canvas/bpmn_export.py` | BPMN 2.0 XML export for WFC workflows |
| `tools/workflow_canvas/chain_deps.py` | Chain dependency analyzer for phase ordering and prerequisites |
| `tools/workflow_canvas/chain_simulator.py` | Process-chain simulator; loads phases from DB, computes timeline + completion estimates |
| `tools/workflow_canvas/multi_source_synth.py` | Multi-source workflow synthesis from existing forms, templates, and Studio artifacts |
| `tools/workflow_canvas/process_code.py` | Process-code generator / exporter for rendered workflows |
| `tools/workflow_canvas/template_library.py` | WFC industry template library loader and schema bootstrap |
| `tools/workflow_canvas/workflow_diff.py` | Workflow diff / comparison utility across versions |
| `tools/workflow_canvas/conformance_checker.py` | Process conformance checker against expected workflow definition |
| `tools/workflow_canvas/handoff_wizard.py` | Phase handoff ceremony brief generator |
| `tools/workflow_canvas/multi_source_synthesizer.py` | Multi-source workflow synthesizer (merges multiple workflow definitions) |
| `tools/workflow_canvas/process_as_code.py` | Process-as-code YAML export / import for chains |
| `tools/workflow_canvas/process_coach.py` | AI process coach for individual workflow steps |
| `tools/workflow_canvas/reverse_processify.py` | Reverse process-ify — reconstruct as-executed document from kanban run |
| `tools/workflow_canvas/reverse_regen.py` | Reverse document regeneration from a workflow definition |
| `tools/workflow_canvas/sla_checker.py` | SLA gate enforcement / status checker for workflows |
| `args/component_registry.yaml` | Canvas registration under key: wfc |
| `tools/workflow_canvas/form_node.py` | FORM_INTAKE_TOOL definition for DAG integration |
| `tools/workflow_canvas/db/migrations/001_wfc_init.sql` | wfc_branding, wfc_workflow_form_nodes tables |

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
