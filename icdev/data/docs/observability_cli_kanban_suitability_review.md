# Suitability Review — Observability → CLI & Kanban Integration Analysis

**Reviews:** [`observability_cli_kanban_integration_analysis.md`](observability_cli_kanban_integration_analysis.md)
**Date:** 2026-08-02
**Method:** every load-bearing claim checked against the live PostgreSQL board and the
current tree, rather than accepted as written.

---

## Verdict

**2 of 12 proposals are suitable now. 1 is already implemented. The rest rest on tables
that no code writes.**

The analysis is a good survey of what ICDEV *could* surface, and its central observation is
correct and worth acting on: `audit_trail` is the richest thing ICDEV produces and nothing
outside the dashboard could read it. But it infers the value of each proposal from the
*existence of a schema*, not from whether anything populates it. Most of the schemas are
empty.

| # | Proposal | Verdict | Measured basis |
|---|---|---|---|
| 4.1.2 | `icdev audit tail --follow` | **BUILD** | `audit_trail` 15,173 rows / 165 write sites / 246 event types, no CLI reader |
| 4.4.2 | `AuditStore` query layer | **BUILD (scoped)** | Needed to back the above. Callback/`subscribe()` API dropped — no consumer |
| 4.1.1 | `icdev status` health CLI | **REJECT** | Name already taken; 2 of its 3 data sources are empty |
| 4.1.3 | `icdev trace show` waterfall | **BLOCKED** | `otel_spans`: **0 rows, 0 `INSERT` sites in the entire tree** |
| 4.2.1 | Event-driven task creation | **DECLINE** | Board holds 2,618 tasks; 55% of dispatches already produce nothing |
| 4.2.2 | Alert → Kanban bridge | **DECLINE** | Same; `alerts` has 10 rows, 0 firing |
| 4.2.3 | Batch progress → Kanban | **BLOCKED** | `batch_runs` / `batch_run_steps`: 0 rows |
| 4.3.1 | AADC runtime trace validation | **BLOCKED** | Depends on `otel_spans` emission |
| 4.3.2 | Persist drift alerts | **DEFER** | Plausible, but feeds the declined alert bridge |
| 4.4.1 | `ICDEVEventType` enum | **DEFER** | `VALID_EVENT_TYPES` already has 246 entries; a parallel enum is a drift risk |
| 4.4.3 | Background telemetry wrapper | **PARTLY DONE** | Hook egress made non-blocking in #1181 |
| — | Scheduler overlap guard | **ALREADY DONE** | Three independent guards exist and were observed working |

---

## Measurements

Live PostgreSQL board, 2026-08-02:

| Table | Rows | `INSERT INTO` sites in `tools/` |
|---|---:|---:|
| `audit_trail` | 15,173 | 165 |
| `kanban_status_transitions` | 11,465 | — |
| `prov_relations` | 3,694 | — |
| `failure_log` | 258 | — |
| `hook_events` | 208 | 1 |
| `canvas_ai_decisions` | 77 | — |
| `alerts` | 10 | 4 |
| **`otel_spans`** | **0** | **0** |
| **`metric_snapshots`** | **0** | 2 |
| `cross_agency_transfers` | 0 | — |
| `shap_attributions` | 0 | — |
| `self_healing_events` | 0 | — |
| `container_metrics` | 0 | — |
| `batch_runs` / `batch_run_steps` | 0 | — |

### `otel_spans` is the significant one

The analysis' marquee item — the Langfuse-style trace-tree viewer — is built on
`otel_spans`. The table is created (`init_icdev_db.py`, migration `122_trace_linkage`) and
**read** by `tools/dashboard/api/traces.py`, but a repo-wide search finds **zero**
`INSERT INTO otel_spans` statements. Several modules reference the table name
(`tools/canvas/ai_trace_mixin.py`, `tools/finetune/trajectory_capture.py`,
`tools/genesis/daemon.py`) without writing spans to it.

So the gap is not "we have spans but no CLI to view them" — it is "we have no spans". A
waterfall renderer would render an empty table, and shipping one would create the
appearance of tracing coverage that does not exist. The prerequisite is a design decision
about what a span *is* in ICDEV (an LLM call? a tool call? a reflex cycle? a kanban
dispatch?), and that decision has to come before any viewer.

### `icdev status` is already taken

The analysis proposes `icdev status` as a health command. That name exists and means
something else — it reports which canvases and subsystems are toggled on
(`tools/cli/__main__.py`, `icdev status [--json]`). Health is separately served by
`python tools/testing/health_check.py --json`, which `CLAUDE.md` already documents. Two of
the three tables the proposed command would read (`metric_snapshots`, `otel_spans`) are
empty.

### The scheduler overlap guard already exists — but verifying it found a real bug

The analysis recommends adopting CoWorker's `_running_ids` guard, on the basis that ICDEV's
scheduler "may have overlap". It has three independent guards:

1. a single-instance PID lockfile re-checked every cycle,
2. an in-process `_running` dict gating dispatch at `MAX_IN_PROGRESS`,
3. DB-side `_count_in_progress()` slot math.

Guard 1 was observed refusing a duplicate launch:
`Another kanban scheduler is alive (pid=29028). Exiting to avoid duplicate dispatch.`

Verifying it did surface a genuine defect the analysis did not predict: **the lockfile and
the pause sentinel are both resolved from `__file__`**, so a scheduler started from a git
worktree gets its own copies of both. Two were found running concurrently — one from
`C:\ai\icdev` and one from `C:\AI\.wt-tsh-d4-audit5` — and only the canonical one honoured a
pause. Filed as `obs-guard-02`.

### Auto-creating tasks would make the board worse

Priority 2 proposes generating Kanban tasks from high-severity events and firing alerts.
Declined on measured grounds: an analysis of 4,115 dispatch cycles the same day found
**55% end in `backlog` having produced nothing**, against a board already holding 2,618
tasks. Adding an automated task *source* to a queue whose problem is that half its existing
work is discarded worsens the signal. Revisit when the discard rate is understood — every
`in_progress → backlog` transition now records a reason (PR #1183), so that data is
arriving.

---

## What was built

| Path | Purpose |
|---|---|
| `tools/audit/store.py` | `AuditStore` — read-only merged query over `audit_trail` + `hook_events` |
| `tools/cli/audit_tail.py` | `icdev audit tail` |
| `tests/test_audit_tail_cli.py` | 19 tests; 2 are PostgreSQL-only |

```bash
icdev audit tail                      # last 50 events, oldest-first on screen
icdev audit tail --follow             # poll for new events; Ctrl-C exits 0
icdev audit tail --json | jq .        # one JSON object per line
icdev audit tail --list-types         # event types this deployment emits, with counts
icdev audit tail --project P --event-type compliance_check --since <iso>
icdev audit tail --source hook_events
```

Deliberately **not** built, though the analysis specifies them:

- `AuditStore.subscribe()` / the `audit_sink` callback. No consumer needs it, and an
  unexercised callback surface is a liability.
- A `UNION ALL` in SQL. The two tables have different column names and id spaces; each is
  queried with its own dialect-neutral `SELECT`, bounded server-side, and merged in Python.
  This keeps the SQL identical on PostgreSQL and SQLite.

### Two things the implementation had to get right for PostgreSQL

**The `--follow` cursor.** `audit_trail.created_at` is `timestamp WITHOUT time zone` on
PostgreSQL, while the cursor is a timezone-aware ISO string. PostgreSQL casts that text by
*dropping* the offset, which is correct only because the column stores UTC. If either fact
changes, `--follow` silently returns nothing forever. `test_pg_since_cursor_is_strictly_newer`
pins both, and `tests/test_audit_tail_cli.py` is registered in `tests/pg_tier_allowlist.txt`
so CI actually runs it against a live PostgreSQL service — the SQLite suite cannot catch this.

**Empty results name their source.** `tools/db/storage.py` resolves `.env` from its own repo
root, so running from a git worktree (no `.env` — it is gitignored) silently falls back to an
empty SQLite file. An empty feed and the wrong database looked identical. The CLI now reports
which backend it read when a query returns nothing.

---

## Recommendation on the source document

Keep it as a survey; do not treat its file list as a work plan. Its `## 7. Files
Created / Modified` table reads as approved scope, and nine of those eleven files would
implement features over empty tables.

The productive follow-up is not any of the twelve proposals: it is deciding whether ICDEV
wants real span emission. That single decision unblocks the trace viewer, the AADC runtime
validation, and a meaningful `metric_snapshots`. Tracked as `obs-trace-01`.
