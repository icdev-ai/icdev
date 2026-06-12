# CUI // SP-CTI
# Feature: HITL Workflow Management

**Phase:** WF — Workflow HITL  
**Migration:** 079  
**Status:** Implemented  
**Routes:** `/workflow-hitl`, `/workflow-teams`  
**API Prefix:** `/api/v1/wf/`

---

## Summary

Adds a unified, policy-driven Human-In-The-Loop (HITL) approval layer across all 7 ICDEV
canvases, modules, and child apps. Previously individual approval mechanisms (Strategos HITL,
`approval_workflows`, `finding_approvals`) were siloed and didn't support teams, structured
feedback, or Kanban gating. This feature provides a single coherent layer.

---

## What's New

### Policy-as-Data Workflow Templates

Each workflow is defined as a JSON stage pipeline stored in `wf_templates`. Templates are
system-managed (immutable) or team-forked (editable). Teams can customize stages, approval
policies (`any_one`, `all_must`, `majority`, `sequential`), kickback limits, and required
documents per stage.

### Team Management

Teams have named members with free-form `role_label` (e.g., "engineer", "senior_reviewer",
"security_manager"). Teams are assigned to projects, tasks, or task groups. Assignment
resolution: task → project → task_group_tag, with fallback to the canvas default team.

### Structured Feedback & Kickback Cycles

Reviewers submit structured feedback with:
- **Decision**: approve / kickback / conditional
- **Feedback types**: correctness, completeness, clarity, safety, compliance
- **Rating**: 1–5 stars
- **Improvement tags**: structured taxonomy (missing_tests, scope_creep, etc.)
- **Kickback reason**: required when kicking back

Kickback resets the instance to the `build` stage and increments `kickback_count`. When
`kickback_count >= template.kickback_limit`, the instance escalates to manager automatically.

### Per-Stage Document Conformance

Each stage can require specific documents before it can advance. Document templates support
four types:

| Type | Purpose |
|------|---------|
| `checklist` | Ordered checklist items; human marks each done before advancing |
| `form` | Structured form with typed fields; submitted data stored in `wf_document_submissions` |
| `sop_reference` | Reference to a specific SOP/policy document section |
| `standard` | AI reference standard — ICDEV uses these as constraints when generating solutions |

The `engine.advance_stage()` method checks `document_manager.all_required_submitted()` before
allowing a stage transition. Missing required documents produce a `{"blocked": true, "missing_docs": [...]}` response.

### AI Standards & Citation/Sourcing

Document templates with `doc_type='standard'` and `is_ai_reference=1` are returned by
`document_manager.get_applicable_standards(canvas_type, stage)` for use by automated build
steps. ICDEV consults these as constraints when generating solutions (e.g., network device
naming conventions, software coding standards, security baselines, IaC naming policies).

Every AI-generated output cites its sources via `wf_citations` records:
- Source document title, type, version
- Section, page number
- Excerpt (direct quote or summary)
- MLA-style formatted reference: `NIST SP 800-53 Rev 5, AC-2(a), p.47`

Citations are linked to the generating record via `cited_in_type` / `cited_in_id` and can
be attached inline to feedback via `citations_json`.

### External Step Integration

Stages can be typed as `external_email`, `external_ticket`, or `external_wiki` to trigger
actions in external systems and wait for confirmation before advancing:

- **Email**: SMTP formatted email with Jinja2 template; reply-webhook or manual confirm
- **Ticket**: Jira / ServiceNow / GitHub Issues — creates ticket, polls until Closed/Done
- **Wiki**: Confluence / SharePoint — creates/updates page; auto-advances or waits for
  page approval

External steps resolve via webhook (`POST /api/v1/wf/external/<step_id>/complete?token=X`
with HMAC verification), polling (Genesis 15-min reflex), or manual in-app confirmation.

### Kanban Gate

When `ICDEV_HITL_KANBAN_GATE=true`, `tools/genesis/reflexes/kanban.py:_move_task()` checks
`HITLGate.get_pending(task_id)` before allowing `in_progress → done`. If a pending approval
exists, the transition is refused (`HITL_PENDING` logged) and the task stays `in_progress`.

### Genesis Reflexes

- **`wf_feedback_aggregation`** (6h, SUPPORT): Aggregates `wf_feedback` rows into
  `wf_feedback_insights` per canvas/template/feedback_type — `avg_rating`, `kickback_rate`,
  `issue_count`, `top_tags`.
- **`wf_ext_poller`** (15 min, SUPPORT): Polls ticket/wiki APIs for `waiting_external`
  instances; marks complete and advances engine on resolution; escalates on timeout.

---

## Architecture

```
.env flags (ICDEV_HITL_ENABLED / _SCOPE / _REQUIRE_FEEDBACK / _KANBAN_GATE)
        │
args/workflow_hitl_config.yaml  ← per-canvas default templates + role mappings
        │
tools/workflow_hitl/
├── constants.py          Enums + CANVAS_ROLE_DEFAULTS
├── template_manager.py   Template CRUD (policy-as-data)
├── team_manager.py       Team/member/assignment CRUD; resolve_team()
├── engine.py             WorkflowEngine state machine
├── gate.py               HITLGate — Kanban integration
├── feedback.py           Append-only feedback capture
├── notifier.py           Review/kickback/approval notifications
├── document_manager.py   Document conformance + AI standards lookup
├── citation_manager.py   Citation tracking + MLA formatting
├── external_steps.py     External step lifecycle + HMAC webhook
├── canvas_hooks.py       Per-canvas template defaults
├── childapp_hooks.py     Child app template inheritance
├── adapters/             Email / Ticket / Wiki adapters (strategy pattern)
└── blueprint.py          Flask blueprint (34 routes)

tools/genesis/reflexes/wf_feedback_aggregation.py  (6h)
tools/genesis/reflexes/wf_ext_poller.py            (15min)
```

---

## Database Tables

12 new tables in migration 079: `wf_templates`, `wf_teams`, `wf_team_members`,
`wf_team_assignments`, `wf_instances`, `wf_approvals`, `wf_feedback`,
`wf_feedback_insights`, `wf_external_steps`, `wf_document_templates`,
`wf_document_submissions`, `wf_citations`.

Append-only (NIST AU compliance): `wf_feedback`, `wf_document_submissions`, `wf_citations`.

---

## Usage

### Enable the Feature

```env
ICDEV_HITL_ENABLED=true
ICDEV_HITL_KANBAN_GATE=true
```

### Create a Team and Assign to a Task

1. Navigate to `/workflow-teams`
2. Create a team, add 2+ members with role labels
3. Assign the team to a project, task, or task group tag

### Review a Task

1. Navigate to `/workflow-hitl`
2. Pending gates appear when a task reaches the `review` or `approve` stage
3. Submit approval with rating, feedback types, improvement tags, and citations
4. Or kickback with a reason — task returns to `build` stage

### Seed System Templates

```bash
python -c "
from tools.workflow_hitl.template_manager import seed_system_templates
seed_system_templates()
print('Seeded')
"
```

### Run Migration

```bash
python tools/db/migrate.py --up
```

---

## Verification

```bash
# Migration applies cleanly
python tools/db/migrate.py --up

# HITL coherence check passes
python tools/workflow/coherence_checker.py --check hitl_workflow

# Blueprint responds (requires ICDEV_HITL_ENABLED=true + auth token)
curl -s http://localhost:5050/api/v1/wf/templates \
  -H "Authorization: Bearer $TOKEN"

# Pages load
curl -s -o /dev/null -w "%{http_code}" http://localhost:5050/workflow-hitl
curl -s -o /dev/null -w "%{http_code}" http://localhost:5050/workflow-teams

# Unit tests
pytest tests/test_workflow_hitl_engine.py -v
pytest tests/test_workflow_hitl_api.py -v
```
