# CUI // SP-CTI
# Manifest Shard — HITL Workflow Management

## Module: `tools/workflow_hitl/`

Unified Human-In-The-Loop approval layer for all ICDEV canvases, modules, and child apps.
Policy-driven workflow templates, team management, structured feedback with kickback cycles,
per-stage document conformance, AI citation/sourcing, and external step integration.

---

## Files

| File | Purpose |
|------|---------|
| `tools/workflow_hitl/__init__.py` | Package marker |
| `tools/workflow_hitl/constants.py` | `ApprovalPolicy`, `StageType`, `WfStatus`, `DocType` enums; `CANVAS_ROLE_DEFAULTS` |
| `tools/workflow_hitl/template_manager.py` | Template CRUD: `get_default(canvas_type)`, `get(id)`, `create()`, `update()`, `fork()`, `list_templates()`, `seed_system_templates()` |
| `tools/workflow_hitl/team_manager.py` | Team/member/assignment CRUD: `create_team()`, `add_member()`, `remove_member()`, `assign_team()`, `resolve_team(task_id)` |
| `tools/workflow_hitl/engine.py` | `WorkflowEngine`: `create_instance()`, `get_status()`, `advance_stage()`, `kickback()`, `escalate()`, `cancel()` |
| `tools/workflow_hitl/gate.py` | `HITLGate`: `should_gate(task_id)`, `get_pending(task_id)` — blocks Kanban `in_progress→done` |
| `tools/workflow_hitl/feedback.py` | `submit_feedback()`, `kickback()`, `get_by_instance()` — append-only |
| `tools/workflow_hitl/notifier.py` | `send_review_request()`, `send_kickback_notice()`, `send_approval_granted()`, `send_escalation_notice()` |
| `tools/workflow_hitl/document_manager.py` | Document template CRUD; `submit_document()`; `all_required_submitted(approval_id, stage_config)` gate; `get_applicable_standards(canvas_type, stage)` for AI |
| `tools/workflow_hitl/citation_manager.py` | `add_citation()`, `get_by_instance()`, `format_citation_ref()` (MLA-style), `format_citation_block()` |
| `tools/workflow_hitl/external_steps.py` | External step lifecycle: `create()`, `send()`, `mark_complete()`, `check_all_pending()`, `verify_webhook_token()`. `mark_complete()` rejects a second decision on an already-decided step, and releases the paused Studio run when the step is Studio-produced (`external_system='studio'`) |
| `tools/workflow_hitl/canvas_hooks.py` | `CANVAS_DEFAULT_TEMPLATES`; `auto_create_instance_if_assigned()` |
| `tools/workflow_hitl/childapp_hooks.py` | `get_inherited_template(canvas_type, childapp_key)` — child app inherits parent canvas default |
| `tools/workflow_hitl/blueprint.py` | Flask blueprint factory `create_wf_blueprint()`; 42 routes at `/api/v1/wf/` |
| `tools/workflow_hitl/intake_promote_handler.py` | Post-HITL-approval callback: `maybe_promote(instance_id)` reads `hitl_intake_pending` mapping, calls `intake_kanban_promoter.promote()` when a chat-intake review instance reaches final approval |
| `tools/workflow_hitl/report_schema.py` | `ReportSection` dataclass; `get_sections(report_type)`, `list_report_types()`, `create_custom_report_type()` |
| `tools/workflow_hitl/document_ingestion.py` | `ingest_file()` (PDF/DOCX/HTML/MD/TXT → rag_chunks); SHA-256 dedup; `get_ingested_files()`, `delete_ingested_file()` |
| `tools/workflow_hitl/section_router.py` | `SectionRouter.route()` — per-section RAG/text-search; greedy cross-section deduplication; `RoutedChunk`, `SectionRouteResult` |
| `tools/workflow_hitl/report_generator.py` | `generate_report()` — LLM synthesis + no-LLM fallback; Jinja2 HTML render; citation auto-population; `get_report()`, `list_reports()` |

---

## Unified Approval Surface (dwo-dur-04)

`wf_external_steps` is the **single reviewer inbox** for the platform. Other subsystems that
pause for a human decision register their gate here rather than standing up a second approval
surface — they are *producers* of `wf_external_steps` rows, and `tools/workflow_hitl` keeps
ownership of notification routing, teams, templates and the webhook-token completion path.

**Registered producer: ICDEV Studio** (`node_type: human` workflow gates).

| Direction | Path |
|-----------|------|
| Studio → HITL | `workflow_runner._notify_approval_gate()` → `tools/studio/gate_bridge.py:open_gate()` → `external_steps.create()`, then `external_ref=<step_run_id>`, `status='sent'` |
| Studio decision → HITL | `workflow_runner.approve_step()` / `reject_step()` → `gate_bridge.complete_external_step()` → `external_steps.mark_complete()` |
| HITL decision → Studio | `external_steps.mark_complete()` → `gate_bridge.is_studio_step()` → `gate_bridge.release_studio_gate()` → `workflow_runner.approve_step()` |
| Telegram decision → HITL | `tools/notifications/adapters/telegram_listener.py` writes the gate decision straight to `studio_workflow_run_steps`, so it calls `gate_bridge.complete_external_step()` itself — otherwise a Telegram approval leaves an orphan row in the inbox |

**Linkage carries no new schema.** A Studio-produced step is identified by
`external_system='studio'` with `external_ref` holding the Studio `step_run_id` — lookup works
in both directions off columns that already exist. `step_type` is `'manual'` (a human decision),
so the CHECK constraint on `wf_external_steps.step_type` is not widened.

**FK satisfaction:** one shared system template `wft-studio-gate` (`canvas_type='studio'`) plus a
per-run shadow instance `wfi-studio-<run_id>`, created idempotently by `gate_bridge._ensure_instance()`.

**Exactly-once, enforced two ways** — the reviewer inbox and the Studio Details modal are two
doors onto the same gate:
1. A `_bridging` re-entrancy guard (module-level set + lock in `gate_bridge`) keyed by
   `step_run_id`, so releasing one surface never loops back into the surface that initiated the
   decision.
2. Terminal-status checks on **both** sides. `external_steps.mark_complete()` refuses a step
   already in `('completed','failed','timed_out')`; `gate_bridge.complete_external_step()`
   makes the same check and writes a `studio_gate_duplicate_decision_rejected` audit event.
   Every bridge decision lands in the shared append-only audit trail via
   `tools/audit/audit_logger.py:log_event()`.

**Studio steps do not advance wf_ stages.** A Studio-produced gate has no `stages_json` to walk,
so `mark_complete()` releases the Studio run and sets its shadow instance to `approved` instead
of calling `WorkflowEngine.advance_stage()`.

**Degradation:** `open_gate()` returns `None` when the `wf_` tables are unavailable, and Studio
falls back to its own Details-modal gate unchanged. The bridge is imported lazily on both sides,
so neither module hard-depends on the other.

**Poller interaction:** `check_all_pending()` selects `status IN ('sent','waiting')`, which
includes bridged Studio gates. `get_adapter('manual', …)` raises `ValueError` — caught and
logged — so the poller never auto-completes a Studio gate; the timeout branch still applies, and
a gate older than `polling.max_wait_hours` (default 72) is marked `timed_out` and escalated.

Studio-side detail (functions, run-state effects): `tools/manifest/icdev-studio-low-code-no-code-platform.md` → *Gate Bridge*.

---

## Module: `tools/idr/` — IDR Conflict Gate (HITL)

Intelligent Dispatch Retry: blocks `MERGE_CONFLICT → IN_PROGRESS` state-machine
transitions until a human explicitly resolves all registered conflicts.

| File | Purpose |
|------|---------|
| `tools/idr/__init__.py` | Package marker; re-exports `IDRConflictGate` |
| `tools/idr/conflict_gate.py` | `IDRConflictGate`: `record_conflict()`, `resolve_conflict()`, `resolve_all()`, `has_unresolved_conflicts()`, `can_resume()`, `assert_can_resume()`, `clear_conflicts()`; `get_gate()` singleton; `ConflictNotResolved` exception |

**Storage:** conflicts serialised into `kanban_tasks.hitl_stage` JSON envelope under key `idr_conflicts`.
No schema migration required.

**Integration:** call `IDRConflictGate().assert_can_resume(task_id)` before emitting the
`resume_from_conflict` transition in the kanban auto-remediator / pr_watcher.

**Tests:** `tests/test_idr_conflict_gate.py` — 18 tests, all in-memory SQLite.

---

### External Step Adapters

| File | Purpose |
|------|---------|
| `tools/workflow_hitl/adapters/__init__.py` | `get_adapter(step_type, system)` registry |
| `tools/workflow_hitl/adapters/base.py` | `ExternalStepAdapter(ABC)`: `send()`, `poll_status()`, `build_payload()` |
| `tools/workflow_hitl/adapters/email.py` | SMTP email via `tools/notifications/adapters/email_adapter.py` |
| `tools/workflow_hitl/adapters/ticket.py` | Strategy router → Jira / ServiceNow / GitHub |
| `tools/workflow_hitl/adapters/wiki.py` | Strategy router → Confluence / SharePoint |
| `tools/workflow_hitl/adapters/strategies/jira.py` | Jira REST API v3 |
| `tools/workflow_hitl/adapters/strategies/servicenow.py` | ServiceNow Table API |
| `tools/workflow_hitl/adapters/strategies/github.py` | GitHub Issues REST API |
| `tools/workflow_hitl/adapters/strategies/confluence.py` | Confluence REST API v2 |
| `tools/workflow_hitl/adapters/strategies/sharepoint.py` | SharePoint via MS Graph OAuth2 |

---

## Database Tables (Migration 080 — Report Ingestion)

| Table | Purpose |
|-------|---------|
| `wf_ingested_files` | Tracks ingested documents (PDF/DOCX/HTML/MD/TXT) with status, hash, chunk counts |
| `wf_report_section_defs` | Per-report-type section definitions (key, name, description, sort_order, chunk limits) |
| `wf_generated_reports` | Generated report records (HTML + JSON content, word/section/citation counts) |
| `wf_report_section_chunks` | Maps report sections to the rag_chunks used to generate them |

**Seeded report types:** `standard_audit` (6 sections), `compliance_assessment` (5 sections), `security_review` (6 sections).

**Report HTML templates** in `context/workflow_report_templates/`: `standard_audit.html`, `compliance_assessment.html`, `security_review.html`.

---

## Database Tables (Migration 079)

| Table | Purpose |
|-------|---------|
| `wf_templates` | Policy-as-data workflow templates; system templates are immutable |
| `wf_teams` | Teams scoped to canvas_type or cross-canvas |
| `wf_team_members` | Team membership with free-form `role_label` |
| `wf_team_assignments` | Maps teams to project / task / task_group scope |
| `wf_instances` | One active instance per task/project going through HITL |
| `wf_approvals` | Active approval gate per stage (pending / approved / kickback) |
| `wf_feedback` | **Append-only** reviewer submissions with ratings, tags, citations |
| `wf_feedback_insights` | Aggregated insights written by Genesis 6h reflex |
| `wf_external_steps` | External step state (email / Jira / SNOW / GitHub / Confluence / SharePoint). Also the **single reviewer inbox for ICDEV Studio approval gates** — a Studio gate lands here with `external_system='studio'` and `external_ref=<studio step_run_id>` (dwo-dur-04); see `tools/studio/gate_bridge.py` |
| `wf_document_templates` | Checklists, forms, SOP references, AI standards — dual-use |
| `wf_document_submissions` | **Append-only** human-submitted document completions |
| `wf_citations` | **Append-only** citation records linked to any stage output |

**Append-only** tables: `wf_feedback`, `wf_document_submissions`, `wf_citations` — listed in `.claude/hooks/pre_tool_use.py:APPEND_ONLY_TABLES`.

---

## Genesis Reflexes

| Reflex | Cadence | Tier | Purpose |
|--------|---------|------|---------|
| `tools/genesis/reflexes/wf_feedback_aggregation.py` | 6h | SUPPORT | Aggregates `wf_feedback` → `wf_feedback_insights` per canvas/template/type |
| `tools/genesis/reflexes/wf_ext_poller.py` | 15 min | SUPPORT | Polls ticket/wiki APIs for `waiting_external` instances; advances engine on resolution |

---

## Configuration

| File | Purpose |
|------|---------|
| `args/workflow_hitl_config.yaml` | Per-canvas default templates and role mappings |
| `args/workflow_hitl_integrations.yaml` | External system config (Jira, SNOW, GitHub, Confluence, SharePoint, SMTP) |
| `context/workflow_email_templates/review_kickoff.html` | Jinja2 review-request email template |
| `context/workflow_email_templates/kickback_notice.html` | Kickback notification email template |

### .env Feature Flags

```env
ICDEV_HITL_ENABLED=false              # Master switch (default off)
ICDEV_HITL_SCOPE=both                 # project | task | both
ICDEV_HITL_REQUIRE_FEEDBACK=true      # Kickback requires structured feedback
ICDEV_HITL_KANBAN_GATE=false          # Gate kanban in_progress→done transitions
```

---

## Dashboard Pages

| Page | Route | Template |
|------|-------|----------|
| HITL Queue | `/workflow-hitl` | `tools/dashboard/templates/workflow_hitl.html` |
| Teams & Assignments | `/workflow-teams` | `tools/dashboard/templates/workflow_teams.html` |

---

## API Routes (`/api/v1/wf/`)

```
GET/POST  /templates                           Template CRUD
GET/PUT   /templates/<id>
POST      /templates/<id>/fork

GET/POST  /teams                               Team CRUD
GET       /teams/<id>
POST      /teams/<id>/members                  Add member
DELETE    /teams/<id>/members/<uid>            Remove member
GET/POST  /teams/<id>/assignments              Scope assignments

GET       /instances                           List instances
GET       /instances/<id>
POST      /instances/<id>/approve              Submit approval
POST      /instances/<id>/kickback             Submit kickback
POST      /instances/<id>/escalate             Escalate

GET       /feedback                            Feedback list
GET       /feedback/insights                   Aggregated insights

GET       /coverage                            Tasks/projects with active HITL

GET       /external                            Pending external steps
POST      /external/<step_id>/complete         Mark done (in-app or webhook)
POST      /external/<step_id>/retry            Retry failed send
GET       /integrations                        Integration status

GET/POST  /doc-templates                       Document template CRUD
GET       /doc-templates/<id>
POST      /doc-submissions                     Submit filled checklist/form

GET       /citations/<instance_id>             Citations for an instance
POST      /citations                           Add citation

POST      /doc-templates/<id>/ingest           Ingest file (PDF/DOCX/HTML/MD/TXT) → rag_chunks
GET       /doc-templates/<id>/ingested-files   List ingested files for a template
DELETE    /doc-templates/<id>/ingested-files/<ingest_id>  Remove ingested file + chunks

GET       /report-types                        List all report types with section counts
GET       /report-types/<type>/sections        Section definitions for a report type
POST      /instances/<id>/generate-report      Generate report (async 202, polls GET /reports/<id>)
GET       /reports                             List reports (?instance_id= filter)
GET       /reports/<id>                        Get report (?format=html for raw HTML)
```

---

## Seeds

| File | Purpose |
|------|---------|
| `tools/db/seeds/seed_workflow_templates.py` | Idempotent seed: 8 system `wf_templates` (NDC/SDC/PDC/BDC/DDC/ODC/IDC + global) + 3 `wf_document_templates` (peer-review checklist, security sign-off, NDC naming standard). Called automatically by `init_icdev_db.py`. |

---

## CLI

```bash
# Run migrations 079+080
python tools/db/migrate.py --up

# Seed system templates (idempotent — safe to re-run)
python tools/db/seeds/seed_workflow_templates.py
python tools/db/seeds/seed_workflow_templates.py --json

# Ingest a document via CLI
# (use POST /api/v1/wf/doc-templates/<id>/ingest or call ingest_file() directly)
python -c "from tools.workflow_hitl.document_ingestion import ingest_file; print(ingest_file('dt-001', 'path/to/sop.pdf', ingested_by='user'))"

# Coherence check
python tools/workflow/coherence_checker.py --check hitl_workflow

# Companion sync
python tools/dx/companion.py --sync --write --json
```

---

## Kanban Gate Integration

`tools/genesis/reflexes/kanban.py:_move_task()` checks `HITLGate.get_pending(task_id)` before
advancing `in_progress → done` when `ICDEV_HITL_KANBAN_GATE=true`. If a pending approval exists,
the transition is refused and logged as `HITL_PENDING`.

---

## Document Conformance

Each stage in `stages_json` can specify `required_docs`:
```json
{"name": "review", "step_type": "manual", "role": "reviewer",
 "required_docs": [
   {"doc_template_id": "checklist-peer-review-v1", "required": true}
 ]}
```

`engine.advance_stage()` calls `document_manager.all_required_submitted(approval_id, stage_config)`
before advancing. If required docs are missing, returns `{"blocked": true, "missing_docs": [...]}`.

Document templates with `doc_type='standard'` and `is_ai_reference=1` are returned by
`document_manager.get_applicable_standards(canvas_type, stage)` for use by automated build steps
as constraints and citation sources.

---

## Citation Format (MLA-style)

`citation_manager.format_citation_ref()` returns:
```
NIST SP 800-53 Rev 5, AC-2(a), p.47
```

`format_citation_block()` returns:
```
Sources:
  [1] NIST SP 800-53 Rev 5, AC-2(a), p.47
      "The organization manages information system accounts..."
```
