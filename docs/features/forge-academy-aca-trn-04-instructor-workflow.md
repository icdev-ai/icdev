# FORGE Academy — Instructor and Cohort Workflow (aca-trn-04)

**Classification:** CUI // SP-CTI
**Card:** `aca-trn-04` — Instructor and cohort workflow: assign, track, review
**Status:** shipped

---

## The problem

The Academy could *measure* a training programme but not *run* one.

`/academy/org-readiness` reported cohort counts, skill gaps and a composite
readiness score to the admin/pm/isso tier and stopped there. Every other Academy
route was self-service and per-learner. There was no way for anyone to assign a
curriculum, set a due date, look at what a learner submitted, record a verdict on
it, or override a grade — and no per-learner roster view at all.

The card also asked a second question: the platform has exactly **one** enrolled
learner, so every cross-learner query was correct-by-vacuity. Before building a
cohort surface on top of those queries, confirm they actually work with several
users.

Three of them did not.

---

## What shipped

### 1. The instructor surface

| Route | What it does |
|---|---|
| `GET /academy/instructor` | Console — assign work, assignment board, roster, audit trail |
| `GET /academy/instructor/learner/<id>` | Per-learner roster view — assigned work, submissions, verified evidence, review form, review history |
| `GET /api/academy/instructor/roster` | Roster JSON, optional `?role=` filter |
| `GET /api/academy/instructor/assignments` | Assignments JSON, optional `?user_id=` filter |
| `POST /api/academy/instructor/assign` | Create an assignment |
| `POST /api/academy/instructor/assignment/<id>/cancel` | Cancel an assignment |
| `POST /api/academy/instructor/review` | Record a verdict, optionally with a score override |
| `GET /api/academy/instructor/audit` | Append-only instructor audit trail |

Every route wears `@require_org_intel` — the **same** admin/pm/isso gate Org
Readiness and the Oracle already use. A second RBAC model for instructors was
explicitly not built: two authorisation systems over one dataset is how a surface
ends up authorised in one place and open in the other.

### 2. Design decisions worth keeping

**Cohort membership is resolved at read time, not frozen at assign time.** A
cohort assignment stores a role token (or `all`), not a member list. A learner who
picks up that role next week inherits the assignment. Freezing the roster at
assign time would silently exclude every later enrolee.

**A due date that cannot be parsed is refused, not dropped.** `due_at` is
nullable and NULL means "no deadline" — which never goes overdue. Storing NULL on
a malformed date would produce an assignment with a deadline nobody is ever told
about.

**An override moves the score and never mints XP.** `record_review` writes
`fa_mission_progress.score` and records `prior_score` alongside it, so the change
is reversible by inspection. It does **not** touch XP. Every XP point in this
schema has a provenance row in `fa_xp_ledger` (aca-int-07); an
instructor-mintable XP path would reopen exactly the hole that card closed — a
rank bought rather than demonstrated.

**A mission the learner never opened cannot be overridden.** There is no progress
row, so an override would fabricate one out of an instructor's opinion. Refused.

**`fa_instructor_audit` is append-only** and registered in `APPEND_ONLY_TABLES`.
A grade override that cannot be attributed to a person is indistinguishable from
a bug in the grader. Correcting an entry means recording the correction, not
editing what is already there.

---

## The multi-learner defects the card asked us to look for

All three were invisible with one learner in one tenant, and all three become
real the moment a second exists.

### `fa_guilds` had no `tenant_id`

The invite code was the only key `join_guild` checked, and it is global. A code
that leaked out of one tenant admitted a learner from another straight into
`get_guild_stats` — which returns every member's display name and XP.
`/api/academy/guild/<id>` takes the id from the URL and had no authorisation of
any kind beyond that, making it an id-enumeration read of the whole roster across
tenants.

Fixed: `tenant_id` added to `fa_guilds` (via the safe-column loop in
`db.migrate()`, not a bare `ALTER` — see the migration header for why), stamped
by `create_guild`, checked by `join_guild`, and enforced **unconditionally** by
`get_guild_stats`. A cross-tenant guild reads as missing — deliberately the same
answer as a bad id, so a probe cannot use the response to confirm someone else's
invite code exists. Members are filtered by tenant too, so guild rows predating
the column do not still list members who joined before the fix.

The check is unconditional rather than opt-in because `tenant_id=None` is itself
a real tenant (the default one); an opt-in check would leave the caller who most
needs it unprotected.

### `_leaderboard_cache_fresh` ignored `tenant_id`

Every read and write around it was tenant-scoped; the freshness probe was not. So
the first tenant to refresh made the cache look fresh for **all** of them, and
every other tenant's `refresh_leaderboard_cache` was skipped forever — their rows
were never written, the cache query returned nothing, and they silently fell
through to the uncached fallback, which has no `rank_pos`.

### `join_guild` returned `None` with a 200

An unresolvable invite code was indistinguishable from a successful join to any
caller that checked the status. Now 404 with an explicit error.

`NULL` and `''` are both written for "no tenant" (the SaaS middleware returns
`None`, `refresh_leaderboard_cache` stores `''`), so `_same_tenant()` treats them
as one population — comparing them raw would split one real tenant in two.

---

## Schema

Migration `323_fa_instructor_workflow.sql`, mirrored in `db._DDL` (a query against
a missing table inside a caller's open transaction aborts that transaction on
PostgreSQL).

| Table | Purpose |
|---|---|
| `fa_assignments` | One row per assignment. Target is one learner or a cohort; scope is one mission or a role track. |
| `fa_instructor_reviews` | One row per human verdict, optionally carrying a score override plus the value it replaced. |
| `fa_instructor_audit` | **Append-only.** Every assign / cancel / review with its actor and role. |

---

## Verification

- `tests/test_aca_instructor_workflow.py` — **48 tests**, covering tenant
  isolation on every read and write, cohort resolution, track expansion (whole
  role tokens, not substrings), overdue flagging, refusal paths, override
  accounting, and XP immutability.
- Full Academy slice: **727 passed, 2 skipped**, no regressions.
- `ruff check` clean; fast-tier coherence gate passes (0 failures).
- Live V&V against PostgreSQL on a worktree dashboard: both pages render with
  real data and zero console errors; assign → review → cancel exercised
  end-to-end through the API with the audit trail confirming attribution.
  Screenshots: `playwright/screenshots/aca-trn-04-instructor-console.png`,
  `playwright/screenshots/aca-trn-04-instructor-learner.png`.

## Known gap (pre-existing, not introduced here)

`forge_academy` has **no IQE adapter** and no entry in the registry's IQE
mapping, so `certificate.html` and `my_certificates.html` include the IQE widget
against a canvas that `iqe_dispatch` rejects as unknown. The instructor pages
follow the sibling admin page (`org_readiness.html`), which also has no widget.
Wiring IQE for the Academy is a canvas-wide task, not this card's scope.
