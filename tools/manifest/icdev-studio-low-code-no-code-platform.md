# ICDEV™ Studio — Low-Code/No-Code Platform (Phase 72 — D361-D366)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ICDEV™ Studio — Low-Code/No-Code Platform (Phase 72 — D361-D366)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Studio DB Init | tools/studio/init_db.py | Initialize 9 studio_* tables (PG + SQLite compatible, idempotent) | --json, --verbose | Table creation summary |
| Workflow Editor | tools/studio/workflow_editor.py | Workflow CRUD, tool catalog (5 categories, 22 tools), YAML validation | --json catalog, templates, list, get <id> | Tool catalog, templates, workflows |
| Studio API | tools/dashboard/api/studio.py | Flask Blueprint: workflow CRUD, tool catalog, marketplace storefront API | REST endpoints under /api/studio/ | JSON responses |
| Form Builder | tools/studio/form_builder.py | Form schema CRUD, JSON Schema output, 10 field types, 4 pre-built templates, submissions | --json field-types, templates, list, get <id> | Form schemas + submissions |
| Case Manager | tools/studio/case_manager.py | Case lifecycle engine: FSM state machine, Kanban board, SLA rules, 3 lifecycle templates | --json templates, types, cases, board <type_id> | Case data + board view |
| Dashboard Builder | tools/studio/dashboard_builder.py | Custom widget layouts: 15 widget types, 3 role defaults (PM/ISSO/Dev), save/share | --json widgets, roles, list, create-default <role> | Dashboard layouts |
| Automation Builder | tools/studio/automation_builder.py | Citizen automation: trigger/condition/action rules, 10 triggers, 8 actions, 5 templates, simulate | --json triggers, operators, actions, templates, list, runs, simulate <id> | Automation rules + run history |
| NL App Builder | tools/studio/nl_app_builder.py | NL-to-blueprint pipeline: extract capabilities from description, create session, refine, build child app | --json extract <desc>, create <desc> --name, refine <id> | Blueprint preview + build result |
| Studio CSS | tools/dashboard/static/css/studio.css | Premium design system: glass cards, gradients, animations, 8px grid | (asset) | CSS |
| Workflow Studio JS | tools/dashboard/static/js/workflow-studio.js | DAG canvas editor: drag-drop nodes, SVG edges, zoom, validate, YAML import/export | (asset) | JS |
| Marketplace JS | tools/dashboard/static/js/marketplace.js | Asset browser: search, filter, sort, detail modal, one-click install | (asset) | JS |
| WNE Budget Estimator | tools/studio/wne/budget_estimator.py | Phase-by-phase budget estimator using node-type cost heuristics; T-shirt sizing; no LLM; air-gap safe | WorkflowContext | Phased cost table dict |
| WNE COA Builder | tools/studio/wne/coa_builder.py | Builds 3-COA comparison (A/B/C) from composite nodes or parametric fallback; reuses coa_generation.md structure | WorkflowContext | 3-COA markdown table |
| WNE Export Pack Generator | tools/studio/wne/export_pack_generator.py | Orchestrates all WNE modules into a downloadable zip (exec_brief, COA, budget, ROI, slides, summary) | WorkflowContext + audience | zip file |
| WNE ROI Calculator | tools/studio/wne/roi_calculator.py | Computes ROI, NPV, payback period, and sensitivity table from WorkflowContext parameters; no LLM; air-gap safe | WorkflowContext.parameters | ROI/NPV/payback dict |

