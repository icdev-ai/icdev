# CUI // SP-CTI
"""Genesis Reflex — Foundry Cycle (ACF continuous autonomy, 12h cadence).

Drives the Autonomous Capability Foundry (``tools/foundry``) on a schedule so the
0→1 product factory keeps inventing, scoring, gating, and seeding net-new ICDEV
capabilities without a human kicking each cycle. This is the autonomy loop behind
the ``/foundry`` canvas — the reflex equivalent of POSTing ``/api/foundry/run``.

Behaviour:
  * **Flag off** — when ``ICDEV_FOUNDRY_ENABLED`` is not truthy the reflex is a
    clean no-op: ``status='skipped'``, ``success=True`` (so it never trips the
    circuit breaker while the canvas is dark). Zero DB / engine / token cost.
  * **Quiet hours** — when the local wall-clock falls inside the configured
    ``foundry_cycle.quiet_hours`` window the reflex is a clean no-op:
    ``status='skipped'``, ``success=True``, ``details.reason='skipped_quiet_hours'``.
    No engine import, no token spend — keeps the autonomous foundry from waking
    users or burning API quota at night. Mirrors the pattern in
    ``tools/creative/creative_engine.py`` (D359). When the config is missing or
    empty the gate is disabled (backwards compatible).
  * **Flag on** — delegates one cycle to :func:`tools.foundry.engine.run_cycle`,
    which owns the heavy lifting: harvest → synthesize → novelty-gate → score →
    CoD go/no-go → SIPA self-vet → seed kanban. The engine enforces intra-cycle
    rate limits (``max_concepts_per_cycle`` from ``args/foundry_config.yaml``) and
    its own circuit breaker / approval gate. The Genesis daemon supplies the
    OUTER per-reflex circuit breaker — repeated failures here trip
    ``circuit_breaker_open`` after ``max_consecutive_failures`` and the reflex
    stops being attempted (see ``tools/daemon/base``).
  * **Engine absent** — until the sibling engine task ships ``run_cycle`` the
    import fails; the reflex degrades to ``status='skipped'`` (``success=True``)
    rather than crashing the daemon or tripping the breaker prematurely.

Air-gap safe: no network probes here (the engine owns any I/O). Per the daemon
gotcha, any probe added later MUST use ``127.0.0.1`` and never ``localhost``.

Daemon contract: ``run(config, trust)`` is dispatched by ``GenesisDaemon`` (the
second positional is the TrustKernel, not a DB handle — this reflex doesn't touch
the DB directly, the engine does). Returns a dict carrying both the daemon keys
(``success`` / ``metric_value`` / ``details``) and the reflex-spec keys
(``harvested`` / ``concepts_proposed`` / ``tasks_emitted`` / ``status``).

CLI:
    python tools/genesis/reflexes/foundry_cycle.py            # run one cycle
    python tools/genesis/reflexes/foundry_cycle.py --dry-run  # no persistence
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

IMPLEMENTATION_STATUS = "full"

import inspect
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Feature flag — mirrors tools/foundry/blueprint.py so the reflex and the canvas
# agree on exactly when ACF is "on".
FEATURE_FLAG = "ICDEV_FOUNDRY_ENABLED"

# Repo root: tools/genesis/reflexes/foundry_cycle.py -> repo root is 4 up.
BASE_DIR = Path(__file__).resolve().parents[3]
_CONFIG_PATH = BASE_DIR / "args" / "foundry_config.yaml"

# Fallback config — used when args/foundry_config.yaml is missing or malformed.
# ``quiet_hours`` defaults to an empty dict (= feature disabled), preserving
# backwards compatibility for deployments that pre-date the gate.
_FALLBACK_CFG: Dict[str, Any] = {
    "cadence_hours": 12,
    "max_concepts_per_cycle": 5,
    "dry_run": False,
    "quiet_hours": {},
}


def _load_config() -> Dict[str, Any]:
    """Load the ``foundry_cycle`` section of args/foundry_config.yaml."""
    try:
        import yaml

        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and isinstance(cfg.get("foundry_cycle"), dict):
            merged = dict(_FALLBACK_CFG)
            merged.update(cfg["foundry_cycle"])
            return merged
    except Exception:  # noqa: BLE001 — never let a config read break import
        pass
    return dict(_FALLBACK_CFG)


_cfg = _load_config()
CADENCE_HOURS: int = int(_cfg.get("cadence_hours", 12))
_MAX_CONCEPTS: int = int(_cfg.get("max_concepts_per_cycle", 5))
_CFG_DRY_RUN: bool = bool(_cfg.get("dry_run", False))
_QUIET_HOURS: Dict[str, Any] = dict(_cfg.get("quiet_hours") or {})


def _in_quiet_hours(quiet: Optional[Dict[str, Any]] = None, *, now: Optional[datetime] = None) -> bool:
    """Return True when the current local wall-clock is inside the quiet window.

    Adapted from ``tools.creative.creative_engine._in_quiet_hours`` (D359). The
    reflex runs on whatever host the Genesis daemon is on, so "local" means the
    server's local timezone (``datetime.now()`` with no tzinfo). ``now`` is
    injected for deterministic tests.

    ``quiet`` schema (matches the YAML keys):

        {
          "start": "22:00",   # HH:MM, inclusive
          "end":   "06:00",   # HH:MM, exclusive
        }

    An empty / missing ``quiet`` dict disables the gate (returns False). When
    ``start < end`` the window is a single daytime band. When ``start > end``
    the window wraps midnight (e.g. 22:00 → 06:00 covers 22:00–23:59 AND
    00:00–05:59).
    """
    q = quiet if quiet is not None else _QUIET_HOURS
    if not q:
        return False
    start_str = str(q.get("start") or "").strip()
    end_str = str(q.get("end") or "").strip()
    if not start_str or not end_str:
        return False

    current = (now or datetime.now()).strftime("%H:%M")

    if start_str <= end_str:
        return start_str <= current < end_str
    # Wraparound (e.g. 22:00 → 06:00).
    return current >= start_str or current < end_str


def _is_enabled() -> bool:
    """True when ACF is toggled on via ``ICDEV_FOUNDRY_ENABLED`` (matches blueprint)."""
    return str(os.environ.get(FEATURE_FLAG, "")).strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


def _call_run_cycle(run_cycle, *, dry_run: bool, max_concepts: int) -> Any:
    """Invoke the engine's ``run_cycle`` passing only the kwargs it actually accepts.

    The engine is built by a sibling task and its exact signature may evolve, so we
    introspect it and forward only the recognized arguments (``dry_run``,
    ``max_concepts`` / ``max_concepts_per_cycle``). ``**kwargs``-accepting engines
    receive both. This keeps the reflex forward-compatible without a brittle chain
    of ``TypeError`` fallbacks.
    """
    candidates = {
        "dry_run": dry_run,
        "max_concepts": max_concepts,
        "max_concepts_per_cycle": max_concepts,
    }
    try:
        sig = inspect.signature(run_cycle)
    except (TypeError, ValueError):
        return run_cycle()

    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if accepts_var_kw:
        # Don't pass both aliases to a **kwargs sink — prefer the canonical one.
        return run_cycle(dry_run=dry_run, max_concepts=max_concepts)

    kwargs = {name: candidates[name] for name in sig.parameters if name in candidates}
    return run_cycle(**kwargs)


def run(config: Optional[Dict[str, Any]] = None, conn: Any = None) -> Dict[str, Any]:
    """Run one ACF cycle on cadence (no-op when the canvas flag is off).

    Args:
        config: reflex context. Recognized keys:
            ``dry_run`` (compute a cycle without persisting / seeding),
            ``max_concepts`` (per-cycle rate limit; defaults to the configured
            ``max_concepts_per_cycle``).
        conn: the Genesis daemon passes its TrustKernel here — unused. The engine
            owns its own RLS-aware DB connections.

    Returns:
        Dict with daemon keys (``success`` / ``metric_value`` / ``details``) plus
        reflex-spec keys (``harvested`` / ``concepts_proposed`` / ``tasks_emitted``
        / ``status``). ``status`` is one of ``ok`` | ``skipped`` | ``error``.
    """
    ctx = config or {}
    dry_run = bool(ctx.get("dry_run", _CFG_DRY_RUN))
    max_concepts = int(ctx.get("max_concepts", _MAX_CONCEPTS))

    result: Dict[str, Any] = {
        "success": True,
        "metric_value": 0.0,
        "status": "ok",
        "harvested": 0,
        "concepts_proposed": 0,
        "tasks_emitted": 0,
        "cadence_hours": CADENCE_HOURS,
        "details": {
            "feature_flag": FEATURE_FLAG,
            "enabled": False,
            "dry_run": dry_run,
            "max_concepts": max_concepts,
            "errors": [],
        },
    }
    details = result["details"]

    # 1. Clean no-op when the canvas is dark — never trips the circuit breaker.
    if not _is_enabled():
        result["status"] = "skipped"
        details["reason"] = f"{FEATURE_FLAG} off"
        logger.info("foundry_cycle: %s off — clean no-op", FEATURE_FLAG)
        return result

    details["enabled"] = True

    # 2. Quiet hours — no engine import, no token spend; mirrors creative engine.
    if _in_quiet_hours():
        result["status"] = "skipped"
        details["reason"] = "skipped_quiet_hours"
        details["quiet_hours"] = _QUIET_HOURS
        logger.info(
            "foundry_cycle: skipped_quiet_hours (window=%s-%s)",
            _QUIET_HOURS.get("start"), _QUIET_HOURS.get("end"),
        )
        return result

    # 3. Delegate one cycle to the engine (which owns rate limits + CoD/SIPA gates).
    try:
        from tools.foundry.engine import run_cycle  # type: ignore
    except Exception as exc:  # noqa: BLE001 — engine not shipped yet -> skip, don't fail
        result["status"] = "skipped"
        details["reason"] = "foundry engine not available"
        details["errors"].append(str(exc))
        logger.info("foundry_cycle: engine unavailable (%s) — skipping", exc)
        return result

    try:
        cycle = _call_run_cycle(run_cycle, dry_run=dry_run, max_concepts=max_concepts)
        if not isinstance(cycle, dict):
            cycle = {"result": cycle}

        # Map the engine's foundry_runs roll-up onto the reflex-spec keys.
        result["harvested"] = int(cycle.get("harvested", 0) or 0)
        result["concepts_proposed"] = int(cycle.get("concepts_proposed", 0) or 0)
        result["tasks_emitted"] = int(cycle.get("tasks_emitted", 0) or 0)
        # ROI metric = work actually emitted to the board this cycle.
        result["metric_value"] = float(result["tasks_emitted"])

        engine_status = str(cycle.get("status", "ok"))
        result["status"] = "error" if engine_status in ("error", "failed") else "ok"
        result["success"] = result["status"] != "error"

        details["run_id"] = cycle.get("run_id") or cycle.get("id")
        details["concepts_approved"] = cycle.get("concepts_approved")
        details["engine_status"] = engine_status
        # Surface any rate-limit / circuit-breaker signal the engine reports.
        for key in ("rate_limited", "circuit_open", "circuit_breaker_open", "skipped_reason"):
            if key in cycle:
                details[key] = cycle[key]

        logger.info(
            "foundry_cycle: harvested=%d proposed=%d emitted=%d status=%s (dry_run=%s)",
            result["harvested"], result["concepts_proposed"], result["tasks_emitted"],
            result["status"], dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — surface as a failed cycle, never crash the daemon
        logger.exception("foundry_cycle reflex error: %s", exc)
        result["status"] = "error"
        result["success"] = False
        details["errors"].append(str(exc))

    return result


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        prog="foundry_cycle",
        description="Genesis reflex — run one Autonomous Capability Foundry cycle on cadence",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute a cycle without persisting / seeding")
    parser.add_argument("--max-concepts", type=int, default=None, help="Per-cycle concept rate limit")
    args = parser.parse_args()

    ctx: Dict[str, Any] = {"dry_run": args.dry_run}
    if args.max_concepts is not None:
        ctx["max_concepts"] = args.max_concepts
    print(_json.dumps(run(ctx), indent=2, ensure_ascii=False))
