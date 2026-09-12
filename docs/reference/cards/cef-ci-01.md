# The Cortex federation layer is UNDER that gate (cef-ci-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/capability_consumption.py --class cortex_backend --json
python tools/awareness/capability_consumption.py --class cortex_facade --json
python tools/workflow/coherence_checker.py --check capability_liveness --gate
python tools/workflow/coherence_checker.py --check substrate_liveness --gate
```

Three rungs (`currency` cef-bck-01, `external` cef-bck-02, `sme` cef-bck-03)
behind one governed facade (`cortex.resolve`, cef-rsv-01), all registered in
CORTEX_BACKENDS, importable, weighted in args/cortex_config.yaml — and
reachable only if something ASKS. Exactly the shape that shipped three times
as a reflex before it got a check.

CONSUMPTION IS `used`, NEVER `consulted`, and the difference is the whole
design. A governed call now records which rungs it reached on its existing
append-only `cortex_audit` row, under `gates_json.backends` — the same
free-form blob ctx-obs-02's timings and trust-kg-03's kg_grounding ride in,
so NO new table and NO migration. Three lists, never merged:
  used       the rung RETURNED A HIT. The only one counted as consumption.
  consulted  the rung set the call ASKED — a read of `resolve.backends` in
             args/cortex_config.yaml. Counting it would have reported 5 of the
             7 rungs live on a board where 4 of those 5 die on every call.
  failed     the rung DIED. Not consumption, and not inertness either: a rung
             that is reached and broken must not read the same as one nothing
             ever wired.
`sme` counts on the same terms as the rest. Its hits are excluded from
citations and can never move a verdict (TRUST rule 1) — that is CITABILITY,
not reachability, and conflating them makes the one advisory backend
permanently unmeasurable.

MEASURED on the live board 2026-08-19, and the numbers in
args/liveness_gate.yaml ARE the measurement, not a chosen allowance:
  cortex_backend  7 declared, 1 consumed. `currency` answers; rag/dic/graph/kb
                  are ROUTED AND FAILING (embeddings 401 on this host, `kb`
                  carries the known `column "use_count" does not exist`
                  defect) and appear in extra.consulted_never_used AND
                  extra.failed_events; `external`/`sme` are NEVER ROUTED, on
                  purpose, and appear in extra.opt_in_only. Do NOT "drain"
                  those two by adding them to search.fan_out.backends — that
                  is an egress decision and an evidence-ranking decision
                  respectively. The repair is a caller.
  cortex_facade   9 declared, 5 consumed (resolve 127, classify 65, complete
                  37, search 26, extract 22). ask/reason/govern/agent have
                  never been invoked through the governed door. A BLOCKED call
                  still counts — the verb ran and the TRUST chain refused it.
                  `cortex.verify` shows up under undeclared_units_observed: 29
                  audit rows under an operation CORTEX_FACADES does not
                  declare. Reported, never folded into either count.

Declarations are read from source with `ast`, never imported: `tools.cortex`
pulls in the retrieval stack and the LLM router, so an importing probe would
go UNMEASURABLE on precisely the deployment where a backend is broken.
NOT drained by this card, and pre-existing: `mcp_dispatch_tool` is 468 over a
467 budget because cef-rsv-01 registered the `cortex_resolve` MCP tool and
only 4 tools in the whole 472-entry registry have ever been dispatched through
the Studio gate. Raising that budget is forbidden; the repair is routing the
other MCP entry points through the same audit.
