# CUI // SP-CTI

# xrv-cost-05 — the MCP dispatch audit on the SERVER surface

**Measured 2026-09-12** against the live PostgreSQL board (`icdev`) on this host.

## The defect

`tools/awareness/capability_consumption.py --class mcp_dispatch_tool` compares
every entry in `tools/mcp/tool_registry.py::TOOL_REGISTRY` (472) against
`studio_mcp_dispatch_audit`. That table had **exactly two writers, both
Studio-internal**:

- `tools/studio/executors/mcp_executor.py::record_dispatch_audit`
- `tools/studio/executors/agent_tool_gate.py::_record`

Nothing under `tools/mcp/` wrote a row. Claude Code talks to
`tools/mcp/unified_server.py` (`.mcp.json` → `icdev-unified`), and every MCP
server in this tree routes a call through ONE choke point,
`tools/mcp/base_server.py::MCPServer._handle_method` →
`_handle_tools_call`. None of those dispatches were recorded anywhere.

So a tool used daily from a Claude Code session read `inert`, and the number
CLAUDE.md quotes — "468 over a 467 budget … only 4 tools in the whole registry
have ever been dispatched through the Studio gate" — was **a measurement of one
caller wearing the name of all callers.**

`tools/cost/waste_survey.py` (xrv-cost-03) measured the same disagreement from
the transcripts and deliberately stopped at reporting it: *"an MCP-server-side
audit write is a new writer to an append-only table and is its own card."*
This is that card.

## What changed

One call site, in `base_server.py::_handle_tools_call`, on **all three** of its
exits — unknown tool, raising handler, dispatched. The row is written by the
**existing** writer, `mcp_executor.record_dispatch_audit`, imported; there is no
second `INSERT` into the append-only table, and an AST test refuses one anywhere
under `tools/mcp/`. Arguments are never stored: `params_sha256` is
`mcp_executor.params_digest`, the same canonicalisation the Studio side uses.

`caller_source` carries `mcp_server:<server name>`, which is the whole point —
`capability_consumption`'s new `extra.by_caller_source` reports the Studio-gate
and MCP-server figures **apart and never summed**, because they answer different
questions ("did a governed workflow dispatch this" vs "did an interactive
session use this").

Authorization is untouched: `tool_registry.tool_authorization` and the Studio
gate are unchanged, and this writer cannot refuse a call.

## Before / after — the inert count

Lifetime, through the shipped probe, live PostgreSQL board:

| | declared | consumed | **inert** | events |
|---|---|---|---|---|
| before (2026-09-12 07:52 UTC) | 472 | 4 | **468** | 25 |
| after  (2026-09-12, same session) | 472 | 12 | **460** | 34 |

`args/liveness_gate.yaml::mcp_dispatch_tool` is ratcheted **467 → 460**. It is
lowered, never raised. Note the gate had been RED on main before this card
(468 against a 467 budget); it is now 7 below its budget.

### The split that had not existed before

```
default (no caller declared)   studio_gate   events 25   declared tools  4
  health_check, nist_lookup, studio_run_start, studio_run_status

mcp_server:icdev-unified       mcp_server    events 10   declared tools  9
  agent_status, cloud_mode_status, gateway_status, get_icdev_metadata,
  health_check, kanban_board_summary, project_list, rag_status,
  studio_tool_catalog
  + 1 undeclared observed: no_such_tool_xrv_cost_05 (refused, by design)
```

4 + 9 = 13 and the union is **12**: `health_check` appears on both sides. That
is the "never merged into one number" property proving itself on the first real
reading — summing the two callers would have over-counted a tool used by both.

### End-to-end proof

The nine `mcp_server:icdev-unified` rows come from driving the **real**
`tools/mcp/unified_server.py` over stdio exactly the way Claude Code does
(`initialize`, then ten `tools/call` frames). The handshake answered:

```json
{"dispatchAudit": {"enabled": true, "table": "studio_mcp_dispatch_audit",
                   "reason": "server-side dispatch audit active"}}
```

Eight tools returned; `studio_tool_catalog` raised inside its handler and
`no_such_tool_xrv_cost_05` is not registered. All ten left a row — eight
`allowed`, two `refused` with the exception class / `mcp_tool_unknown` as the
reason — and the JSON-RPC answers were byte-for-byte what they were before the
change.

**These are driven dispatches from this session, not a day of organic use.**
The card asked for a re-measurement "after one day of live rows"; that is not
available inside one session and the figure above is stated for what it is. The
number to trust for organic traffic is the one taken after this merges and the
`icdev-unified` server process restarts — Claude Code's running server was
launched from `C:/AI/ICDev` before this change and executes the pre-change
`base_server.py`, so none of its calls are recorded yet.

## Beside the transcript count (xrv-cost-03)

`python -m tools.cost.waste_survey --json --since-days 30`, same day:

| | distinct `icdev-unified` tools | calls |
|---|---|---|
| transcript (30 d, retrospective) | 3 — `kanban_update_task` 8, `kanban_get_task` 7, `kanban_delete_task` 1 | 16 |
| server-side audit (lifetime) | 9 | 10 |

**They do not agree yet, and that is not a disagreement about the same
population.** The transcript looks back thirty days over calls made against a
server that did not record anything; the audit's history starts today. The two
should converge on the tools Claude Code uses once the writer has been live for
a window, and the transcript remains the only evidence for anything before that.
Two residual reasons they will never be identical:

- the transcript counts `icdev-unified` only, while the audit covers **every**
  server subclassing `MCPServer`;
- the transcript cannot see a call that never reached a tool block, and the
  audit records `refused` rows (unknown tool, raising handler) that the
  transcript records as ordinary calls.

## Measured cost

Live PostgreSQL board, this host, 2026-09-12:

| | p50 | p90 | max | n |
|---|---|---|---|---|
| enqueue — what a tool result pays | **0.0034 ms** | 0.0043 ms | 0.91 ms | 500 |
| synchronous write, PG reachable | 1.93 ms | 2.70 ms | 18.25 ms | 25 |
| synchronous write, PG **unreachable** | — | — | **20,038 ms** | 1 |

The card set 5 ms as the ceiling above which the write must be asynchronous.
**The synchronous p50 is under it (1.93 ms) and the write was kept asynchronous
anyway**, on the third row: a synchronous audit on a stdio transport hands its
*failure mode* to the tool result, and an unreachable database blocks the
connect for twenty seconds per call while the client waits. That is the audit
becoming load-bearing, which a best-effort observability writer must never be,
and no threshold on the healthy case can see it. The tail is the lesser
argument — 18.25 ms is already over the ceiling on a warm local board, and this
host runs four CI runners.

The queue is bounded (`MAX_QUEUED = 1000`) and an overflow is counted in
`stats()['dropped']`, never silent.

## Disclosure — 525 bench rows on the live board

The latency measurement above was taken **against the live PostgreSQL board**
before it occurred to the author to point it at a scratch database. It appended
**525 rows** under `caller_source = 'mcp_server:bench'`, `tool = 'noop_tool'`
(500 async enqueue samples + 25 synchronous samples). `noop_tool` is **not** in
`TOOL_REGISTRY`, so those rows move no `consumed`/`inert` figure — they appear
only as `undeclared_tools_observed: 1` under a `mcp_server:bench` source, which
is visible in the split above and in any future reading.

They are **not deleted**: `studio_mcp_dispatch_audit` is append-only under NIST
800-53 AU and is registered in `APPEND_ONLY_TABLES`. Recording them here is the
correct remedy; quietly removing them is not. Future measurements of this writer
go to a `tmp_path` database.

## Reproduce

```bash
python -m tools.awareness.capability_consumption --class mcp_dispatch_tool --json
python tools/workflow/coherence_checker.py --check capability_liveness --gate
python -m tools.cost.waste_survey --json --since-days 30
python -m pytest tests/mcp/test_dispatch_audit_on_server.py -q
```

Stand the writer down with `ICDEV_MCP_DISPATCH_AUDIT=0` — never a silent no-op:
`initialize` then reports `enabled: false` with the switch that closed it. The
pytest suite sets it to `0` by default (`tests/conftest.py`) so fixture servers
in unrelated tests do not write real rows into the ambient database.

## Found on the way, NOT fixed here

`tests/test_mcp_instrumentation.py` is **6 failed / 2 passed** and has nothing
to do with this card — proven by running it with this branch's changes removed
from the working tree, which reproduces the same 6/2 exactly. The file is
**ungated** (absent from `args/ci_test_files/core.txt` and every `core.d/`
fragment), which is why it has been able to sit red.

The diagnosis, recorded so the next card does not have to re-derive it: the
`tracer` fixture calls `configure_tracer(SQLiteTracer(db_path=tmp_db))` and
*then* constructs `MCPServer`, whose `__init__` calls
`tools.observability.enable_tracing_if_enabled()` — which **re-configures the
global proxy**, replacing the tracer the test just installed. Every span then
lands somewhere other than `tmp_db` and `SELECT ... FROM otel_spans` returns
zero rows. The repair is either ordering in the test or
`enable_tracing_if_enabled` declining to clobber an explicitly configured
backend; both are obx-trc-01's subsystem, not this one, and widening this
change into the tracer wiring is exactly the scope creep the card forbids.

Separately, `tests/studio/test_workflow_parallel.py::test_human_gate_parks_only_its_own_branch`
failed once under load on a 0.25 ms overlap margin (46097.2426 vs 46097.2428)
and passed alone and on an identical re-run of the same 677-test slice. A
timing flake, and not reachable from this change: the dispatch-audit thread
never starts during the suite, because `tests/conftest.py` sets
`ICDEV_MCP_DISPATCH_AUDIT=0`.

## Not done, and named

- **Nothing re-measures organic traffic here.** The figure above is a driven
  proof; the honest organic reading needs the writer live for a window.
- **The other MCP entry points are still unaudited.** `tools/saas/mcp_http.py`
  serves MCP over HTTP with an authenticated principal and does not subclass
  `MCPServer`, so this choke point does not cover it. That is where a *real*
  `principal_id` would come from — a stdio server has none, and this writer
  records the launching session (`ICDEV_SESSION_ID` / `ICDEV_AGENT`) rather than
  inventing one.
- **`resources/read` and `prompts/get` leave no row.** The card scoped this to
  `tools/call`, and widening it changes what `mcp_dispatch_tool` counts.
