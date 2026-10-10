# CUI // SP-CTI

# obs-cov-03 — the sixteen declared `agent_*` audit event types

Task obs-cov-03 reported that `audit_logger.VALID_EVENT_TYPES` declares sixteen
`agent_*` types and only three had ever been written, and asked for each of the
remaining thirteen to be either wired or removed.

Measured again on the live board (PostgreSQL) 2026-08-07 — unchanged from the
2026-08-02 measurement in the card:

```
agent_task_completed           62
agent_collaboration_completed  33
agent_execution_completed       1
```

## What the row counts turned out to mean

Two of the three "live" types are not live.

* **`agent_collaboration_completed` (33).** Every row is `action =
  'swp-audit-01-probe'`, written 2026-08-02 00:34 by a prior audit task, with
  actors like `teaming_hub` and `shipley_mapper` — govcon modules that do not
  participate in agent collaboration. `tools/agent/collaboration.py` emits
  `agent_collaboration_started` immediately before the work and
  `agent_collaboration_completed` immediately after, in all four patterns. A
  count of 33 completions against 0 starts is not possible from that code; the
  probe rows are the whole population, and the module has emitted nothing.
* **`agent_execution_completed` (1).** One row, `claude_cli` / `db_reconcile`,
  2026-06-19. Nothing in the tree writes this type — the row was hand-written.

So exactly **one** `agent_*` type has ever been emitted by production code:
`agent_task_completed`, from `tools/browser/scope.py`, which is genuinely live
(most recent row 2026-08-07 17:57, `browser_agent` / `browser.screenshot`).

The card's framing was right about the problem and generous about the baseline.

## Reachable vs exercised

The two are different and the distinction is the whole finding.

Of the thirteen, **ten were wired all along** and are simply cold — this board
rarely drives the multi-agent surface:

| Type | Emit site |
|---|---|
| `agent_task_submitted` | `tools/mcp/core_server.py`, `tools/kanban/state_machine.py` |
| `agent_task_failed` | `tools/mcp/core_server.py`, `tools/browser/scope.py` (deny branch) |
| `agent_health_stale` | `tools/agent/skill_router.py` |
| `agent_veto_issued` | `tools/agent/collaboration.py`, `tools/agent/authority.py` |
| `agent_veto_overridden` | `tools/agent/authority.py` |
| `agent_collaboration_started` | `tools/agent/collaboration.py` (×4 patterns) |
| `agent_message_sent` | `tools/agent/mailbox.py` |
| `agent_memory_stored` | `tools/agent/agent_memory.py` |
| `agent_memory_recalled` | `tools/agent/agent_memory.py` |
| `agent_escalation_created` | `tools/agent/collaboration.py` |

Each of those modules has real importers (`team_orchestrator`, `a2a/agent_server`,
`ace/message_bus`, `monitor/auto_resolver`, `saas/rest_api`, `agent_executor`), so
the sites are on live paths. Nothing to fix: a cold path is not a dead one, and
deleting these declarations would delete working instrumentation.

**Three were genuinely dead** — `agent_execution_started`, `_failed`, `_retried`
had zero emit sites anywhere in the tree. `agent_execution_completed` had none
either; its single stale row is the only reason it fell outside the card's
thirteen.

## Resolution: wire, not remove

All four `agent_execution_*` types are now emitted by `tools/genesis/reflexes/kanban.py`
at the same choke points #1304 established for `runtime_invocations`:

| Type | Site |
|---|---|
| `agent_execution_started` | `_dispatch_via_claude_cli`, after the `Popen` |
| `agent_execution_completed` / `_failed` | `_check_completed`, split on the exit code |
| `agent_execution_retried` | `_increment_retry_count`, `_increment_timeout_count` |

That subprocess **is** the agent execution. #1196 instrumented
`tools/agent/agent_executor.py::execute_agent` and reported the agent surface
covered; the runner never calls it, and the surface held zero rows until #1304
moved the instrumentation to the dispatch path. Attaching the audit events to
`execute_agent` would have repeated that exactly.

The retry emit lives in the two counters rather than at re-dispatch because
dispatch cannot see why it was called — a retry and a first attempt are the same
code path from inside `_dispatch_via_claude_cli`.

No event type was removed, so `VALID_EVENT_TYPES` is unchanged and no migration
or `rebuild_event_type_constraint()` call is needed. The parity suite
(`tests/test_audit_event_type_parity.py`) still passes.

`agent_execution_*` duplicates status and duration that `runtime_invocations`
already records. That is deliberate: `runtime_invocations` is operational
telemetry, `audit_trail` is the append-only NIST AU record with a hash chain and
a retention guarantee. "When did agents run, and which failed" has to be
answerable from `audit_trail` alone.

## The gate

`tests/test_agent_event_type_reachability.py` fails if any declared `agent_*`
type has no emit site under `tools/`, so the next person to add one has to add
the writer in the same change. It understands the three emit shapes present in
this tree — a plain `event_type=` kwarg, a ternary (`tools/browser/scope.py`,
where the never-fired branch is the deny branch), and a literal inside an
`INSERT INTO audit_trail` parameter tuple (`tools/kanban/state_machine.py`) —
and it does **not** count a `SELECT` naming a type as an emit site, which is
what would otherwise let a dashboard query pass for coverage.

Scope: `agent_*` only. The same audit has not been run over the other ~205
declared types.

## Known limitation

Reachable is not exercised. Ten of these types now have a proven writer and no
production row, and only a live multi-agent run will change that. A test that
asserted on row counts would pass or fail on how busy the week was, so this gate
deliberately asserts on the code instead.
