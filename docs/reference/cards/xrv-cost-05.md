# Every `tools/call` Claude Code makes leaves ONE audit row (xrv-cost-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.awareness.capability_consumption --class mcp_dispatch_tool --json  # extra.by_caller_source
python -m tools.cost.waste_survey --json --since-days 30      # the transcript-derived count, beside it
python -m pytest tests/mcp/test_dispatch_audit_on_server.py -q
```

`studio_mcp_dispatch_audit` had exactly TWO writers, both Studio-internal
(mcp_executor.py, agent_tool_gate.py), and NOTHING under `tools/mcp/` -- the
servers Claude Code actually talks to -- wrote a row. So the consumption
figure was A MEASUREMENT OF ONE CALLER WEARING THE NAME OF ALL CALLERS: 468
of 472 tools read `inert` while the transcripts showed Claude Code invoking
`kanban_get_task`/`kanban_update_task`/`kanban_delete_task` 16 times in 30
days (xrv-cost-03 measured exactly that disagreement and stopped there, on
purpose -- a new writer to an append-only table is its own card).
ONE CALL SITE, NEVER PER SERVER: `base_server.py::_handle_tools_call`, on all
THREE of its exits (unknown tool | raising handler | dispatched). Fifteen
per-server hooks is fifteen chances to forget one, which is the same defect
one layer up. NO SECOND `INSERT`: the row is written by the EXISTING
`mcp_executor.record_dispatch_audit`, imported; an AST test refuses an
`INSERT INTO studio_mcp_dispatch_audit` literal anywhere under `tools/mcp/`
(scanning the AST, not the text -- the module docstring NAMES the statement
to say it does not use one, the model_id_gate precedent). ARGUMENTS ARE NEVER
STORED: `params_sha256` is `mcp_executor.params_digest`, the same
canonicalisation, so a digest means one thing whichever caller produced it.
AUTHORIZATION IS UNTOUCHED -- an audit store that can refuse a dispatch is a
gate, which is `tool_registry.tool_authorization`'s job.
THE TWO CALLERS ARE COUNTED APART AND NEVER SUMMED. `extra.by_caller_source`
splits on the `caller_source` column: Studio-gate rows and `mcp_server:*` rows
answer DIFFERENT questions ("did a governed workflow dispatch this" vs "did an
interactive session use this"). Measured 2026-09-12, lifetime: 4 tools via the
Studio gate, 9 via `mcp_server:icdev-unified`, `health_check` on BOTH -- which
is why the union is 12 and not 13. `consumed`/`inert` stay the union; this is
the attribution BESIDE it, never instead of it.
OFF THE RESPONSE PATH, AND THE p50 IS NOT WHY. Bounded queue + one daemon
thread. Measured against the live PG board: enqueue p50 0.0034 ms; a
SYNCHRONOUS write p50 1.93 ms -- UNDER the 5 ms ceiling -- and 20,038 ms
against an UNREACHABLE database. Async was kept for that third number: a
synchronous audit hands its FAILURE MODE to the tool result, and no threshold
on the healthy case can see it. Overflow is COUNTED (`stats()['dropped']`).
`ICDEV_MCP_DISPATCH_AUDIT=0` stands it down and `initialize` SAYS SO
(`result.icdev.dispatchAudit.enabled` + the switch that closed it) -- never a
silent no-op. `tests/conftest.py` sets it to `0` for the suite so a fixture
server in an unrelated test cannot write real rows into the ambient database.
NOT DONE, and named: the figure above is a DRIVEN proof (the real
unified_server over stdio, this session), not a day of organic use -- Claude
Code's running server executes the pre-change file until it restarts;
`tools/saas/mcp_http.py` does not subclass `MCPServer` and is still unaudited
(and is the only surface with a REAL authenticated principal -- a stdio
transport has none, so this writer records the LAUNCHING SESSION,
ICDEV_SESSION_ID/ICDEV_AGENT, rather than inventing one); and
`resources/read` / `prompts/get` leave no row.
DISCLOSED: the latency run appended 525 `noop_tool` rows under
`mcp_server:bench` to the LIVE board before the author pointed it at a scratch
database. `noop_tool` is undeclared so it moves no consumed/inert figure, and
the table is append-only -- they are recorded, not removed.
Survey: docs/audits/xrv-cost-05-mcp-dispatch-audit.md
