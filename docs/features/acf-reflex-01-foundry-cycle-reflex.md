# ACF Foundry — Genesis Reflex: Foundry Cycle on Cadence (acf-reflex-01)

CUI // SP-CTI

## Summary

Closes the ACF autonomy loop by giving the Autonomous Capability Foundry its own
Genesis reflex. The reflex fires on a 12-hour cadence and calls
`tools.foundry.engine.run_cycle()` once per cycle, which owns the heavy lifting
(harvest → synthesize → novelty-gate → score → CoD go/no-go → SIPA self-vet →
seed kanban). When `ICDEV_FOUNDRY_ENABLED` is off the reflex is a clean no-op
(`status="skipped"`, `success=True`) so it never trips the daemon's outer
circuit breaker while the canvas is dark.

## Behaviour

* **Flag off** — `ICDEV_FOUNDRY_ENABLED` is anything other than
  `1 | true | yes | on | enabled`. The reflex returns
  `{status: "skipped", success: True, harvested: 0, concepts_proposed: 0,
  tasks_emitted: 0, metric_value: 0.0, details: {enabled: False, reason:
  "ICDEV_FOUNDRY_ENABLED off"}}`. Zero DB / engine / token cost. Never imports
  the engine.
* **Flag on** — delegates to `tools.foundry.engine.run_cycle(dry_run, max_concepts)`
  using `_call_run_cycle`, which introspects the engine signature and forwards
  only the kwargs it accepts (forward-compatible with sibling-task signature
  evolution). The engine enforces intra-cycle rate limits and its own
  circuit-breaker / approval gate; the Genesis daemon supplies the OUTER
  per-reflex breaker (`max_consecutive_failures` in
  `args/genesis_config.yaml::defaults`). Roll-up mapped onto the reflex-spec
  keys (`harvested` / `concepts_proposed` / `tasks_emitted`); the engine's
  `status` of `error | failed` is surfaced as `success=False`.
* **Engine absent** — until a sibling task ships `run_cycle` the import fails;
  the reflex degrades to `status="skipped"` (`success=True`) rather than
  crashing the daemon or tripping the breaker prematurely.

## Per-reflex watchdog

Honored via `args/genesis_config.yaml::foundry_cycle.timeout_seconds: 600` (a
full cycle is expensive: harvest + LLM synth + scoring + CoD + SIPA). The
daemon's `run_reflex_impl` wraps every reflex in a `threading.Thread.join()`
with this timeout — a hung cycle is abandoned and counted as a failure, so
repeated hangs eventually trip the outer circuit breaker.

## Air-gap & safety

* No network probes here. Any probe added later MUST use `127.0.0.1`, never
  `localhost` (per the daemon-registration gotcha).
* No LLM calls in the reflex itself; the engine owns all LLM usage and is
  LLM-optional (deterministic clustering when `synthesis.llm_assist.enabled`
  is false).
* CUI markings: `// SP-CTI` in source. No CUI/banner content emitted in logs.

## Files

1. **`tools/genesis/reflexes/foundry_cycle.py`** — reflex module
   * `CADENCE_HOURS = 12` (mirrors `args/foundry_config.yaml::foundry_cycle.cadence_hours`)
   * `FEATURE_FLAG = "ICDEV_FOUNDRY_ENABLED"`
   * `_call_run_cycle(run_cycle, *, dry_run, max_concepts)` — signature-aware
     forwarder
   * `run(config, conn)` — daemon contract: `config` is the reflex context,
     `conn` is the TrustKernel (unused; the engine owns DB). Returns
     `{success, metric_value, status, harvested, concepts_proposed,
     tasks_emitted, cadence_hours, details}`.
2. **`tools/genesis/daemon.py::REFLEX_NAMES`** — `foundry_cycle` registered at
   line 100 (the daemon-registration gotcha — this MUST be present alongside
   the registry + config or the reflex silently never runs).
3. **`tools/genesis/reflex_registry.py::REGISTRY`** — `DOMAIN` tier,
   `interval_h=12.0`.
4. **`args/genesis_config.yaml::reflexes.foundry_cycle`** —
   `timeout_seconds: 600`, `schedule: "every 12h"`, `interval_seconds: 43200`,
   `cooldown_minutes: 120`, `risk_tier: green`, `success_metric:
   foundry_tasks_emitted` (gated on `>= 0`).
5. **`args/foundry_config.yaml::foundry_cycle`** — reflex-facing mirror of
   cadence + cap + dry-run.
6. **`tests/test_foundry_cycle_reflex.py`** — 13 tests covering:
   * Contract (`CADENCE_HOURS > 0`, `IMPLEMENTATION_STATUS == "full"`)
   * Flag off (with `monkeypatch.delenv`) — clean no-op, never calls engine
   * Falsy flag values (parametrized: `""`, `"0"`, `"false"`, `"no"`, `"off"`)
   * Flag on — delegates + maps engine roll-up, forwards `max_concepts`
   * `dry_run` forwarded
   * Engine `status=error` → `success=False`
   * Engine raises → `status="error"`, error in `details.errors`, no crash
   * Engine module absent → `status="skipped"`, `success=True`
   * Signature-aware `_call_run_cycle` (no-arg, single-kwarg, **kwargs engines)

## Verification

```
$ pytest tests/test_foundry_cycle_reflex.py -v --noconftest
============================== 13 passed in 0.44s ==============================
```

CLI smoke (flag off):

```
$ python tools/genesis/reflexes/foundry_cycle.py
{
  "success": true,
  "status": "skipped",
  "harvested": 0,
  ...
  "details": {"feature_flag": "ICDEV_FOUNDRY_ENABLED", "enabled": false,
              "reason": "ICDEV_FOUNDRY_ENABLED off"}
}
```

## Dependencies

* **`acf-mcp-01`** — `tools/mcp/tool_registry.py` registers `foundry_run` +
  `foundry_status` so the MCP gateway can drive the canvas (required per the
  task dependency).
* **`acf-engine-01..05`** — `tools/foundry/engine.py::run_cycle` (the reflex
  delegates to it; engine is the layer that does the real work).
* **`acf-db-01`** — `foundry_runs` / `foundry_concepts` / `foundry_outcomes`
  tables the engine persists to (engine creates them via `init_db()` so
  absence is non-fatal for the reflex).

## Sibling tasks

| Task | Role |
|------|------|
| acf-engine-* | The engine this reflex drives |
| acf-dash-*   | `/foundry` canvas (manual trigger) |
| acf-ada-01   | ACF ↔ Genesis Harness eval bridge (precision/recall) |
| acf-mcp-01   | MCP gateway registration (required dep) |
| acf-learn-01 | `learner.py` — bounded scorer-weight tuning from outcomes |
