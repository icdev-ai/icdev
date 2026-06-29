---
ontology_id: icdev:mission:m-docgen-01-session-lifecycle:step:1
step_class: icdev:Lesson
---
# DocGen: Session Lifecycle

DocGen is ICDEV's document generation system. It turns raw briefs, scan results, and system descriptions into structured compliance documents (SSP, POAM, ATO packages, AAR reports).

## Two-layer architecture

```
Session Layer (session_manager.py)
  ├── Session CRUD: create, read, pause, resume, complete
  ├── State machine: pending → active → generating → review → complete
  └── Artifact store: links generated docs to session

Workflow Layer (workflow.py)
  ├── Section dispatcher: parallel section writers
  ├── Merge engine: assembles sections into final doc
  └── Post-processor: formatting, CUI marking, template application
```

## Session states

| State | Meaning |
|-------|---------|
| `pending` | Created, waiting for workflow to start |
| `active` | Workflow running, sections being generated |
| `generating` | Section writers dispatched, awaiting all results |
| `review` | All sections merged, awaiting human review |
| `complete` | Approved and finalized |
| `failed` | Workflow encountered an unrecoverable error |

## Your task

Create a DocGen session for an SSP. POST to `/api/docgen/sessions` with: `doc_type: "ssp"`, `system_name: "ICDEV Training Platform"`, `il_level: "IL4"`. Note the `session_id` — you'll use it in every subsequent step.
