# Plan: Co-Worker Engine — Usage Guide + Instance Management (Delete)

## Goal
1. Explain how to use `/coworker` for Agentic AI coding and software testing workflows.
2. Add **individual delete** and **Delete All** buttons to the Co-Worker Engine canvas (`/coworker/`) for completed/cancelled instances in the "Recent" list.

---

## Part 1 — Using `/coworker` for Coding & Testing

### What the Co-Worker Engine Does
The ACE (ANVIL Co-Worker Engine) assembles **trust-tiered AI co-worker teams** at runtime based on the problem you describe. It is built on top of ICDEV's A2A agent mesh, LLM Router, and HITL workflow engine.

### Roles Relevant to Coding & Testing
| Role ID | Trust | Purpose |
|---------|-------|---------|
| `ai_developer` | yellow | Writes, tests, and refactors code; follows TDD and ANVIL workflows |
| `qa_manager` | yellow | Validates code quality, runs tests, enforces acceptance criteria |

### How to Launch a Coding / Testing Team
1. Go to **`http://localhost:5050/coworker/`**
2. In the **"Launch a Co-Worker Team"** textbox, describe the problem:
   - *Coding:* `"Build a Python module that validates NIST 800-53 control mappings against a CSV input and outputs a JSON report with findings."`
   - *Testing:* `"Write pytest test cases for the LLM router's fallback logic when Ollama is unreachable, including mocked provider responses."`
   - *Refactoring:* `"Refactor the kanban scheduler to use PostgreSQL advisory locks instead of SQLite row-level locking, with backward compatibility."`
3. Click **Launch Team**.
4. The engine:
   - Classifies your problem → selects roles (`ai_developer` + `qa_manager` for coding tasks)
   - Assembles the team (persisted to `ace_instances` + `ace_coworkers`)
   - Spawns co-worker threads that execute step loops
   - Emits messages and artifacts into the instance timeline
5. Monitor on the **instance detail page** (`/coworker/<instance_id>`) — see co-worker states, messages, and any artifacts (code, test plans, review comments).

### Headless / CLI Usage
```bash
# Launch from terminal
$env:PYTHONPATH="C:\AI\ICDev"
python -m icdev.tools.ace.controller --launch "Build a pytest suite for the RAG server" --json

# Check status
python -m icdev.tools.ace.controller --status ace-<id> --json

# Abort if it goes off the rails
python -m icdev.tools.ace.controller --abort ace-<id>
```

### IQE (Natural Language Queries)
The canvas includes an IQE widget. Ask plain-English questions:
- `"Show active co-worker teams"`
- `"List failed instances"`
- `"How many coworkers are active?"`

---

## Part 2 — Instance Management: Delete Feature

### Problem
The "Recent" list on `/coworker/` grows unbounded. Users need:
- **🗑️ per-instance delete** — remove a specific completed/cancelled instance from history
- **🗑️ Delete All** — bulk-remove all non-active instances from the Recent list

### Assumptions
- Only **inactive** instances (`state NOT IN ('assembling','pending','active','paused')`) are deletable. Active instances must be aborted first.
- Deletion is **hard delete** from `ace_instances` (cascades to `ace_coworkers`, `ace_messages`, `ace_artifacts`, `ace_agent_workflows` via `ON DELETE CASCADE`).
- `ace_audit_log` is **append-only** and intentionally NOT cascaded — audit trail is preserved.
- The blueprint already uses `get_canvas_connection()` — no RLS issues.
- A confirmation modal prevents accidental deletion.

### Approach A — Recommended: Soft-Cascading Hard Delete

**Backend (blueprint.py)**
1. Add `POST /api/ace/<instance_id>/delete`:
   - Read instance, verify it is not in `_ACTIVE_STATES`
   - Execute `DELETE FROM ace_instances WHERE id = %s`
   - Return `{"deleted": True, "instance_id": ...}`
2. Add `POST /api/ace/delete-all`:
   - Accept optional `?except=` comma-separated IDs to preserve
   - Build `DELETE FROM ace_instances WHERE state NOT IN (...active...)`
   - Return `{"deleted": N, "instance_ids": [...]}`

**Frontend (coworker/index.html)**
1. Add a small **🗑️** icon button on each "Recent" team card (not on Active cards).
2. Add a **"Delete All Completed"** button in the "Recent" section header.
3. Both trigger a JS `confirm()` then call the respective API.
4. On success, remove the card from the DOM (no page reload needed).

**Trade-offs:**
- ✅ Clean, matches existing dashboard delete patterns (SOPs, runbooks)
- ✅ Cascading FKs mean one DELETE statement per instance
- ⚠️ Irreversible; mitigated by confirmation dialog + only targeting inactive instances

### Approach B — Soft Delete (rejected)
- Add an `is_deleted` flag to `ace_instances`
- Pro: recoverable
- Con: Requires schema migration, complicates all queries, inconsistent with other dashboard delete patterns (runbooks, SOPs use hard delete)
- **Rejected** — overkill for a scratch-pad canvas; users can re-launch instantly.

---

## Implementation Checklist

### Backend
- [ ] `POST /api/ace/<instance_id>/delete` in `icdev/tools/ace/blueprint.py`
- [ ] `POST /api/ace/delete-all` in `icdev/tools/ace/blueprint.py`
- [ ] Verify both endpoints reject deletion of active-state instances

### Frontend
- [ ] Add `.btn-delete` CSS to `coworker/index.html` (consistent with boundary_canvas/sops.html)
- [ ] Add 🗑️ per-card delete button in Recent loop
- [ ] Add "Delete All" button in Recent section header
- [ ] Add JS `deleteInstance(id)` and `deleteAllRecent()` handlers
- [ ] Add confirmation modals (simple `confirm()` or reusable modal)

### Tests
- [ ] API test: `POST /api/ace/<id>/delete` → 200, verify row gone
- [ ] API test: try delete active instance → 409 Conflict
- [ ] API test: `POST /api/ace/delete-all` → count returned, verify rows gone
- [ ] Playwright E2E: click 🗑️ on a Recent card → card disappears

### Documentation
- [ ] Update `tools/manifest/ace-coworker-engine.md` with delete endpoints
- [ ] Update `CLAUDE.md` Quick Reference if new CLI commands added

---

## Files to Modify
| File | Change |
|------|--------|
| `icdev/tools/ace/blueprint.py` | Add `POST /api/ace/<id>/delete` and `POST /api/ace/delete-all` |
| `tools/dashboard/templates/coworker/index.html` | Add delete buttons, CSS, JS handlers |
| `tools/manifest/ace-coworker-engine.md` | Document new endpoints |
| `tests/test_ace_foundation.py` or new `tests/dashboard/test_coworker_e2e.py` | API + UI tests |

---

## Security / Guardrails
- Only inactive states are deletable (never `assembling`, `pending`, `active`, `paused`)
- Confirmation dialog required for both individual and bulk delete
- `ace_audit_log` survives deletion (append-only table)
- No tenant_id/classification columns on canvas tables — RLS-free, correct

## Success Criteria
1. User can click 🗑️ on a single Recent instance card → it disappears, DB row gone
2. User can click "Delete All" → all inactive instances disappear, count returned
3. Trying to delete an active instance is blocked with a clear error
4. The usage guide above is communicated to the user
