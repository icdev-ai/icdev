# CUI // SP-CTI

# kax-merge-02 — Policy copied into task descriptions goes stale

## The failure

Operating policy is COPIED into every kanban task description at seed time, so
changing the policy does not change the tasks that already exist.

Measured 2026-08-08. The HGX card and `tools/kanban/seed_hgx_kanban.py` were
both corrected to say "open PRs normally, not `--draft`". 35 of 38 hgx rows
already existed and kept the ORIGINAL sentence:

> "Open any PR as --draft (pr_watcher auto-merges green kanban/*)."

A dispatched session reads its TASK DESCRIPTION, not the card, so every one of
them kept opening drafts. `pr_watcher` may not merge a draft, so finished green
work piled up and needed a human three separate times (8 PRs, then 7, then 5).
The rows were corrected in place; nothing stopped the next policy change from
doing it again.

The same rows also still said "gated on hgx-gate-00" — a gate released
2026-08-08 and superseded by hgx-gate-02 (itself released 2026-08-09). Stale in
a second, quieter way, and still live when this card was picked up: **35 rows on
2026-08-09**.

## Decision

**Ship (b), the drift detector — but make its rules table describe only what is
STALE, never what is current.** The replacement text always comes from
`policy:` on the project entry in `args/projects.yaml`, which is the single
source (b) is normally accused of not having.

Two mechanisms, in precedence order:

1. **Marked block — the steady state.** A description may carry

   ```
   <!-- icdev:policy hgx -->
   ...the card's current policy text...
   <!-- /icdev:policy -->
   ```

   The body is compared against the card on every scan and rewritten when they
   disagree. No pattern matching, exact and idempotent.

2. **Legacy pattern — the one-time migration.** A rule in
   `args/kanban_policy_drift.yaml` names the stale phrasing with a regex and
   replaces the matched span with a marked block. After that the row is in
   case 1 and the rule never fires for it again.

New rows are born card-linked: `task_factory.create_tasks` calls
`policy_drift.apply_policy_block`, so a seeder no longer pastes its own copy and
the legacy path is only ever needed for rows that predate this card.

### Why the alternatives lose

**(a) Stop embedding policy; inject at dispatch time.** Architecturally the
right shape, and the design above converges on it — a marked block IS
"the policy lives on the card", just materialised into the row. As the *shipped
mechanism* it loses on the thing the card is specifically about: **the 3000+
rows that already exist.** Injecting correct policy into the dispatch prompt
while the description still says the opposite hands the session two
contradictory instructions, which is worse than one stale one. It also loses
the property that a task read straight from the board — by a human, by the
dashboard, by `kanban_get_task` — shows its policy inline. Its "one source, no
copies" advantage is kept here without the contradiction: there is exactly one
source, and the copy is machine-maintained and marked as such.

**(c) Reseedable descriptions.** Only works where the seeder still exists, still
matches the card, and can be re-run — most of the 60+ `seed_*.py` scripts are
one-shot and several describe cards that have moved on. An update path over
live rows would also have to clobber or diff a session's own edits to its
description, and a seeder rewrites the WHOLE description, so there is no way to
distinguish "the policy changed" from "the work text was refined by hand". The
mechanism shipped here rewrites only a delimited region and leaves everything
else byte-identical.

**Doing nothing / a bigger rules table.** (b)'s real weakness is that it catches
only the statements someone remembered to add. That is narrowed as far as it
goes: a rule is needed ONCE per legacy phrasing, and never again for that row.
The steady state needs no rules at all.

## Fail-closed scoping

`applies_to.task_id_prefix` is **required and non-empty**, and every prefix must
start with the project's own `task_prefix`. `load_rules()` raises rather than
narrowing, because every failure mode here is "a rule quietly edits rows nobody
meant it to":

| Condition | Result |
|---|---|
| no `applies_to.task_id_prefix` | `PolicyRuleError` — the rule is unscoped |
| a prefix outside the rule's own card | `PolicyRuleError` — it reaches another card |
| project listed in `exempt_projects` | `PolicyRuleError` — a decision may not be overruled |
| project has no `policy:` | `PolicyRuleError` — nothing to rewrite to |
| pattern does not compile | `PolicyRuleError` — it would silently match nothing |
| no `why` | `PolicyRuleError` |

### The deliberate exception (AC2)

AGOV is MANUAL-ONLY: it touches `.claude/hooks/pre_tool_use.py` and the approval
gate, `pr_watcher` auto-merges any CI-green `kanban/*` branch, and its 19 rows
legitimately instruct `gh pr create --draft`. `agov` is named in
`exempt_projects`, which is a veto that outranks everything — including a
`policy:` field, if someone later adds one. Verified by digest: the 20 `agov-`
descriptions are byte-identical before and after the migration run.

`tools/kanban/policy_drift.py::load_exemptions` reads only the veto list and
never validates rules, so a malformed rule cannot take down every seeder. If the
exemption list is unreadable it exempts **everything** — if we cannot prove a
card is safe to rewrite, none is.

## What writes, and what only reports

| Status | Behaviour | Why |
|---|---|---|
| `backlog`, `scheduled` | rewritten | a dispatcher can still hand these to a session — this is where the harm is, and no live session holds them |
| `in_progress` | reported | rewriting would clobber a session's own edits to its description |
| `done`, `pr_opened`, others | reported | archaeology; cannot misdirect anyone |

Every rewrite appends a `kanban_task_comments` row naming the rule and the
action, so the change is visible on the board rather than only in a log line.
Dry run is the default; `--fix` is required to write.

## Where it runs (AC3)

* **Genesis reflex `kanban_policy_drift`** — 6h, `risk_tier: amber`, registered
  at all four points (`daemon.REFLEX_NAMES`, `reflex_registry.py`,
  `args/genesis_config.yaml`, `tools/manifest/kanban.md`). Scans every status,
  rewrites dispatchable rows. `metric_value` is the number of *dispatchable*
  rows still out of sync, so a reported-only `done` row does not hold the metric
  permanently red. `dry_run: true` in config turns the rewrite off.
* **CI test `tests/test_kanban_policy_drift.py`** — 26 board-free tests in the
  `Test` job allowlist. Validates the rules file and the pure text transforms;
  in particular that **no rule's pattern matches its own card's current policy**,
  which would make the checker rewrite the board on a loop.

## Verification (2026-08-09, live PostgreSQL board, 3054 rows)

**AC1 — a policy change reaches an already-seeded task.**

```
$ python tools/kanban/policy_drift.py --json
drifted: 35   fixable: hgx-doc-01(scheduled) hgx-doc-02(backlog) hgx-vv-01(backlog)

$ python tools/kanban/policy_drift.py --fix --json
fixed: ['hgx-doc-01', 'hgx-doc-02', 'hgx-vv-01']   skipped: 32 (report-only)
```

Read back from a separate process, `hgx-doc-02` (seeded 2026-08-08) now opens
with a `<!-- icdev:policy hgx -->` block carrying the corrected text, and the
stale "MANUAL-ONLY, gated on hgx-gate-00" preamble is gone. `GROUND RULE:` and
everything after it are untouched.

Then the steady state, proving the rules table is not load-bearing after
migration: `policy:` on the hgx card was edited a second time, and the next scan
reported `block_updated | block body differed (492→645 chars)` for all three
rows — **no rule involved**. A separate process read back the new text.

**AC2 — the deliberate exception survives.** 20 `agov-` rows, SHA-256 digest of
`(id, description)` identical before and after: `3d54883c57f3036b7389c88d`.

The `kax-merge-02` row itself — which quotes the stale `--draft` sentence while
describing the bug — is also untouched, because no rule names a `kax-` prefix.
That is the fail-closed scoping working, not a special case.

## Known limit

The 31 `done` and 1 `pr_opened` hgx rows still carry the legacy preamble and are
reported on every cycle. That is deliberate (see the table above) and it means
`drifted_total` stays at 32 while `unresolved_dispatchable` — the metric the
reflex is graded on — is 0. Clearing the archive would need
`--fix-statuses done`, which is available but is not what the reflex runs.
