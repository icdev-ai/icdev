# E2E Test: Governed Delivery Pipeline — Full-Lifecycle Proof

Verify that the delivery pipeline the system always follows is both **modeled**
(the `/api/kanban/tasks/<id>/pipeline` endpoint) and **rendered** (the shared
`task_pipeline.js` stepper on the board), with `merged` as the terminal DONE
gate. This is both a regression test and the canonical example of the pipeline
(Phase 4). Spec: `tests/e2e/kanban_pipeline.spec.ts`.

## Prerequisites
- Flask dashboard running on http://localhost:5050 (Playwright starts it unless `ICDEV_NO_SERVER`).
- Kanban tables initialized (`kanban_tasks`, `kanban_verifications`).
- `ICDEV_AUTH_BYPASS=true` (CI e2e job) so the API is reachable without login.

## Canonical stages (mirror `args/pipeline.yaml`)
`implement → code_quality → coherence → conformance → unit_tests → e2e → pr → ci → merged`

## Steps

1. `POST /api/kanban/tasks` to create a low-priority test task (status `backlog`)
   with an `acceptance_criteria`; capture the returned `id`.
2. `GET /api/kanban/tasks/<id>/pipeline` and assert:
   - `stages[].key` equals the 9 canonical keys **in order**.
   - every stage has a non-empty `tooltip` and `label` (the pipeline is self-explanatory).
   - every stage `state` is one of `completed` / `current` / `pending`.
   - the last stage is `merged`, its tooltip mentions "done", and it is NOT
     `completed` for a fresh task (done only after merge-verify).
3. `POST /api/kanban/tasks/<id>/move` to `in_progress`, re-`GET` the pipeline, and
   assert `current_stage` is still a canonical key and `merged` is not completed.
4. Navigate to `/kanban`, assert no "internal server error".
5. Click the task card (`[data-task-id="<id>"]`) to open the edit modal, which
   reveals the Lifecycle section and calls `renderLifecycle()`.
6. Wait for `#task-lifecycle-body .progress-pipeline` and assert it renders
   exactly 9 `.pipeline-step` elements.
7. Assert the stepper text contains the stage labels: Implement, Code Quality,
   Coherence, Conformance, Unit Tests, Merged.
8. Screenshot to `.tmp/test_runs/screenshots/kanban_pipeline_stepper.png`.

## Pass criteria
- The pipeline API and the UI stepper both expose all 9 stages with tooltips.
- `merged` is terminal and not reached by an unfinished task.
- No server error on the board.
