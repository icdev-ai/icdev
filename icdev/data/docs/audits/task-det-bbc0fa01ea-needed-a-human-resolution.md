<!-- CUI // SP-CTI -->
# task-det-bbc0fa01ea — `needed_a_human` finding for qa-fail-5f7cf03a0b0a4351, resolved

- **Task:** task-det-bbc0fa01ea (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `bbc0fa01eae45989`)
- **Subject:** qa-fail-5f7cf03a0b0a4351 — PR #1903, resumed 5x by pr_watcher,
  escalated
- **Date measured:** 2026-08-23, against the live PG board

## Verdict

Nothing is left to land, and no claim is held. Both acceptance criteria were
already true on the live board when this card was dispatched:

| Criterion | Measured 2026-08-23 |
|---|---|
| the derivation no longer reports `qa-fail-5f7cf03a0b0a4351` | `[]` (24h window) |
| `detector_findings.bbc0fa01eae45989` reads `cleared` | `status=cleared`, `seen_count=4`, `cleared_at=2026-08-23 03:50:39` |
| the claim is released | `restore_acts.py --plan`: `leases_state=measured`, `leases_held=0` |

`python -m tools.kanban.landed_check --task qa-fail-5f7cf03a0b0a4351` reports
`landed: true` at `25692314e` (the #1903 squash), evidence tier `subject`.

## The actual cause, and who fixed it

The card's instruction — *find the actual cause, land it by hand, and release
the claim* — was carried out by a human session ~24 hours before the card was
dispatched, and the watcher then merged the repaired branch:

| When (UTC) | Event |
|---|---|
| 2026-08-22 01:28 | `08244db41` pushed: the fix itself (`GET /coworker/<id>` renders again; a page-template failure is a 500, never 200 JSON) |
| 2026-08-22 01:29 | CI run 32543593977 — **failure**: `Test Gates`, `Test Shard 3 of 4`, `Test` |
| 2026-08-22 01:32 → 02:14 | `pr_watcher.resume` x5, `classification=ci_failed`, every one "injected resume context" |
| 2026-08-22 02:15 | `pr_watcher.escalate` — "resume cap reached (5/5) — manual intervention required" |
| 2026-08-22 02:19 | `detector_findings` row first seen; this card filed (`suggested`) |
| 2026-08-23 01:45 → 01:57 | `6d8292d75` (merge origin/main, branch was 10 behind), `27fbb43b2`, `e2127c5c1` pushed by hand |
| 2026-08-23 01:51, 01:58 | CI runs 32611384433 / 32611659077 — **success** |
| 2026-08-23 02:10 | `pr_watcher.merge` "auto-merge ok"; task → `done` |
| 2026-08-23 03:50 | finding `cleared` by the reflex |

The real cause was **not in the branch the resumes were asked to repair**, which
is exactly the class this detector separates from `recovered`. PR #1903's own
test file, `tests/test_ace_instance_page_render.py`, rendered `/coworker/...`
through the shared `tools.dashboard.app` singleton. The `ace` canvas is
`default_enabled: false` behind `ICDEV_ACE_ENABLED`, so on a CI runner with no
`.env` the singleton never registers `ace_bp` and every request is a 404 — on
BOTH trees. The red-first gate therefore read the file as "broken, not
red-first", while the same tests passed on a developer machine whose `.env`
enables the canvas. A host-dependent environment difference, invisible from
inside the diff, and five LLM resumes of that diff could never reach it.

The hand fix (`27fbb43b2`) rebuilt the `client` fixture on a fresh Flask app
carrying the singleton's template folder, config, filters, globals and context
processors and registered `ace_bp` on it — the gated convention in
`tests/cortex/test_blueprint_routes.py::_isolated_app`. Measured with
`ICDEV_ACE_ENABLED=false` (CI's shape): 1 passed / 14 errored-or-404 before,
15 passed after; red-first discriminating (merge base 14 failed / 1 passed).
`e2127c5c1` then swapped bare `?` placeholders for `%s` so the fast-tier
coherence gate passed. The identical defect had meanwhile been re-found by the
E2E sweep as qa-fail-9de4533aba26c880 (now `done` too), because the fix was
sitting in the red PR.

## The merge is correctly not a recovery — and the clear is by window, not by outcome

Re-derived over a **72h** window (83 `pr_watcher.*` rows), the subject still
reads:

```
{"task_id": "qa-fail-5f7cf03a0b0a4351", "attempts": 5, "kind": "resume",
 "escalated": true, "merged": true, "outcome": "needed_a_human"}
```

`merged` is now `true` and the outcome is unchanged: the merge at 02:10 on
08-23 followed the escalation at 02:15 on 08-22, so `summarize_recovery` keeps
`needed_a_human`. That is the rem-hyg-16 rule working. It also means the
`cleared` at 03:50 on 08-23 — 25h35m after the escalate row — happened because
the escalation aged out of the detector's 24h window, not because a measurable
cycle observed a different outcome. Stated here so nobody reads `cleared` as
"the watcher recovered it". This is deliberately **not** a change to the
detector, its threshold or its window (an actuator never edits what it
verifies); it is the same observation the
[task-det-c3cf418aed survey](task-det-c3cf418aed-needed-a-human-resolution.md)
made, where this finding was already row 5 of 6 — filed for a subject that was
then in flight and merged by hand ~24h later.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'qa-fail-5f7cf03a0b0a4351'])
EOF
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
python -m tools.kanban.landed_check --task qa-fail-5f7cf03a0b0a4351 --json
python tools/awareness/restore_acts.py --plan --json
```
