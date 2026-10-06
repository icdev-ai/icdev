---
ontology_id: icdev:mission:m-docgen-01-session-lifecycle:step:1
step_class: icdev:Lesson
---
# DocGen: Session Lifecycle

DocGen is ICDEV's document generation workflow (its tables carry the older name IDR, `idr_sessions`). It turns uploaded source material (diagrams, configs, IaC, documents, emails) into structured documents such as runbooks, playbooks, SOPs, policies and ATO evidence packages. The dashboard lives at `/docgen`.

## Two layers

```
Session layer   tools/docgen/session_manager.py
  ├── Session CRUD on idr_sessions (title, domain, doc_type, template_id, stage, status)
  ├── Uploads (idr_uploads), analyses, conflicts
  └── Artifacts: the published HTML / PDF / DOCX files

Workflow layer  tools/docgen/workflow.py
  ├── Stage functions: ingest, analyze, conflict detection, ACE generation
  ├── WriteGuard quality gate (score >= 70, up to 3 auto-fix attempts)
  └── Publish: export, CUI stamp, citation gate
```

## The nine stages

A session moves through numbered stages; each stage has a status name:

| Stage | Status | What happens |
|-------|--------|--------------|
| 0 | `setup` | Session created |
| 1 | `ingesting` | Source files uploaded |
| 2 | `analyzing` | Each upload is routed to its analyzer |
| 3 | `conflicts` | Conflicting facts are detected; a human must resolve them |
| 4 | `synthesizing` | Context is assembled from the analyses |
| 5 | `generating` | The document is drafted (optionally by an ACE team) |
| 6 | `writeguard` | Quality gate; must pass before review |
| 7 | `reviewing` | A human reviews the draft |
| 8 | `publishing` | Exported to HTML + PDF (+ DOCX) |

Two transitions are **hard gates**: 3 → 4 refuses while conflicts are pending HITL resolution, and 6 → 7 refuses until WriteGuard has passed.

## Your task

Sessions are created with `POST /docgen/api/sessions`. `title` is required; `domain` must be one of `network`, `security`, `devops`, `developer`, `compliance`, `standard_guide`; `doc_type` and `template_id` come from the template gallery in `args/docgen/templates.yaml`. Write the request body for an ATO evidence package for "ICDEV Training Platform" (hint: the `tpl-ato-package` template, `doc_type: "ato_ssp"`, domain `security`). The response is the new session row; its `id` is what every later call uses.
