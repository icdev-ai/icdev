# CUI // SP-CTI
"""learner.py — ACF build-outcome capture + bounded scorer-weight tuning (acf-learn-01).

Closes the ACF feedback loop: for every approved ``foundry_concepts`` row, reconcile
its emitted kanban tasks (``foundry_tasks_emitted`` ⨝ ``kanban_tasks``) against
their final statuses and persist the rollup to ``foundry_outcomes`` (append-only,
NIST AU-2). The terminal build state (``shipped`` / ``failed``) is also reflected on
``foundry_concepts.status`` (mutable) so downstream queries can see at a glance
which ideas worked.

A second routine, ``tune_weights``, computes a bounded per-dimension adjustment
from shipped vs failed concept score profiles and **persists it directly** to
``args/foundry_config.yaml -> scoring.weights`` — a fast, automatic, in-bounds
retune, separate from the slow human-merged ``heuristic_learner`` proposals
(acf-ada-07). The step is clamped to ``[_WEIGHT_FLOOR, _WEIGHT_CEIL]`` and
multiplied by ``_STEP`` per dimension, mirroring the bounded tuning pattern in
``tools/genesis/goal_learner.py``.

Pipeline::

    foundry_concepts(approved)   ─┐
                                   ├─► record_outcomes()  → foundry_outcomes row
    foundry_tasks_emitted ────────┤                     + foundry_concepts.status
    kanban_tasks.status ──────────┘                          (shipped | failed)

    foundry_concepts(shipped)    ─┐
                                   ├─► tune_weights()    → scoring.weights
    foundry_concepts(failed)     ─┘                       in args/foundry_config.yaml

Determinism / air-gap: no LLM, no network. Both functions are pure statistics over
the ``foundry_*`` tables. Append-only writes go through the RLS-aware
``tools.db.storage.get_connection()`` (NEVER ``get_canvas_connection()``) with the
caller's ``tenant_id`` / ``classification`` stamped.

Public API
----------
    record_outcomes(*, conn=None)          -> dict
    tune_weights(*, conn=None, config=None) -> dict

CLI
---
    python tools/foundry/learner.py --record [--json]
    python tools/foundry/learner.py --tune   [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

try:  # optional — only needed for the DB path
    pass
except Exception:  # pragma: no cover
    pass

from tools.foundry.db.init_db import _is_pg, init_db
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.foundry.learner")


# =========================================================================
# PATHS / CONSTANTS
# =========================================================================
def _find_repo_root() -> Path:
    """Anchor on a marker that exists only at the true repo root (handles the
    tools/ + icdev/tools/ mirror)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CLAUDE.md").exists() or (parent / "goals" / "manifest.md").exists():
            return parent
    return here.parent.parent.parent


BASE_DIR = _find_repo_root()
CONFIG_PATH = BASE_DIR / "args" / "foundry_config.yaml"

# Default weights mirror args/foundry_config.yaml -> foundry.scoring.weights so
# tune_weights behaves identically when the config file is missing/unreadable.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "novelty": 0.25,
    "feasibility": 0.25,
    "strategic_fit": 0.25,
    "market_timing": 0.25,
}

# Safe bounds — the weight retune is bounded so a noisy / small sample can never
# crush a dimension to 0 (unusable) or blow it past 0.5 (dominant). Same shape
# as heuristic_learner's _WEIGHT_BUMP / _WEIGHT_CAP clamps.
_WEIGHT_FLOOR: float = 0.05
_WEIGHT_CEIL: float = 0.50
_STEP: float = 0.05  # per-dimension step size for the retune

# Kanban status values that mean "still in flight" — used by record_outcomes to
# decide if a concept is abandoned vs still-being-built.
_IN_FLIGHT_STATUSES: frozenset[str] = frozenset(
    {"backlog", "suggested", "in_progress", "blocked", "review", "scheduled", "running"}
)
_TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled"})
_DONE_STATUS = "done"
_FAILED_STATUS = "failed"

# Outcome strings (single source of truth: tools.foundry.constants.OUTCOME_VALUES)
OUTCOME_SHIPPED = "shipped"
OUTCOME_VV_PASS = "vv_pass"
OUTCOME_VV_FAIL = "vv_fail"
OUTCOME_ABANDONED = "abandoned"


# =========================================================================
# DB ACCESS
# =========================================================================
def _caller_context() -> tuple[str, str]:
    """(tenant_id, classification) from the active security context, with
    platform defaults when no request context is bound (mirrors
    engine._caller_context / harvester._caller_context)."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    return tenant_id, classification


def _conn(conn: Any) -> Any:
    """Return the caller's connection or open a fresh one (caller owns the
    close on a borrowed connection)."""
    if conn is not None:
        return conn
    from tools.db.storage import get_connection

    return get_connection()


def _row_dict(row: Any) -> dict:
    """Best-effort row -> dict (sqlite3.Row, RealDictCursor, tuple)."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        try:
            return {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]
        except Exception:
            return {}


# =========================================================================
# record_outcomes
# =========================================================================
def _emitted_tasks(conn: Any, concept_id: int) -> list[dict]:
    """Return the kanban task rows linked to a concept via foundry_tasks_emitted."""
    rows = conn.execute(
        """
        SELECT k.id, k.status, k.last_failure_reason
          FROM kanban_tasks k
          JOIN foundry_tasks_emitted e ON e.kanban_task_id = k.id
         WHERE e.concept_id = ?
        """,
        (concept_id,),
    ).fetchall()
    return [_row_dict(r) for r in rows]


def _has_terminal_outcome(conn: Any, concept_id: int, outcome: str) -> bool:
    """Append-only but idempotent: a concept is recorded once per (concept, outcome)."""
    row = conn.execute(
        "SELECT 1 FROM foundry_outcomes WHERE concept_id = ? AND outcome = ? LIMIT 1",
        (concept_id, outcome),
    ).fetchone()
    return bool(row)


def _record_outcome(
    conn: Any, *, concept_id: int, outcome: str, detail: dict, tenant_id: str, classification: str
) -> None:
    """Append-only INSERT into foundry_outcomes + UPDATE foundry_concepts.status
    when the concept was previously 'approved' (forward-only transition)."""
    if _is_pg():
        cur = conn.execute(
            "INSERT INTO foundry_outcomes (concept_id, outcome, detail, tenant_id, classification) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (concept_id, outcome, json.dumps(detail), tenant_id, classification),
        )
        try:
            cur.fetchone()
        except Exception:  # pragma: no cover
            pass
    else:
        conn.execute(
            "INSERT INTO foundry_outcomes (concept_id, outcome, detail, tenant_id, classification) "
            "VALUES (?, ?, ?, ?, ?)",
            (concept_id, outcome, json.dumps(detail), tenant_id, classification),
        )

    if outcome in (OUTCOME_SHIPPED, OUTCOME_VV_PASS):
        new_status = "shipped"
    elif outcome in (OUTCOME_VV_FAIL, OUTCOME_ABANDONED):
        new_status = "failed"
    else:
        new_status = None
    if new_status is not None:
        conn.execute(
            "UPDATE foundry_concepts SET status = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'approved'",
            (new_status, concept_id),
        )
    conn.commit()


def _decide_outcome(tasks: list[dict]) -> Optional[str]:
    """Decide an outcome from a list of kanban task dicts (id/status/last_failure_reason).

    Returns one of OUTCOME_SHIPPED / OUTCOME_VV_PASS / OUTCOME_VV_FAIL /
    OUTCOME_ABANDONED, or None if the concept is still in flight (no decision yet).
    """
    if not tasks:
        return OUTCOME_ABANDONED

    statuses = [(t.get("status") or "").lower() for t in tasks]
    reasons = [(t.get("last_failure_reason") or "").lower() for t in tasks]
    has_vv_fail = any("vv_" in r or "verif" in r or "oracle" in r for r in reasons)
    has_done = any(s == _DONE_STATUS for s in statuses)
    has_in_flight = any(s in _IN_FLIGHT_STATUSES for s in statuses)
    has_failed = any(s == _FAILED_STATUS for s in statuses)
    all_done = all(s == _DONE_STATUS for s in statuses) if statuses else False

    # V&V failure (oracle-reported) dominates — even if some other tasks are done.
    if has_vv_fail:
        return OUTCOME_VV_FAIL
    # Any non-vv task failure is also a build failure (treated as vv_fail so the
    # heuristic learner has data; could split later).
    if has_failed and not has_in_flight:
        return OUTCOME_VV_FAIL
    # All emitted tasks done → shipped (or vv_pass when an oracle verifier row
    # is present — the oracle_verifiers table is optional so we degrade cleanly).
    if all_done:
        return OUTCOME_SHIPPED
    # No task reached terminal state yet (and not abandoned) — skip this pass.
    if has_in_flight:
        return None
    # Mixed terminal without done → abandoned (e.g. all cancelled).
    if not has_done and not has_in_flight:
        return OUTCOME_ABANDONED
    return None


def _vv_pass_check(conn: Any, concept_id: int) -> bool:
    """Best-effort: did an oracle verifier pass for this concept?

    Returns True when the oracle_verifiers table exists AND has a row for this
    concept with outcome='pass' (or similar). Returns False on any error or
    when the table is missing — record_outcomes still works without the oracle.
    """
    try:
        row = conn.execute(
            "SELECT outcome FROM oracle_verifiers WHERE concept_id = ? LIMIT 1",
            (concept_id,),
        ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    outcome = (_row_dict(row).get("outcome") or "").lower()
    return outcome in ("pass", "vv_pass", "shipped", "ok")


def record_outcomes(*, conn: Any = None) -> dict[str, Any]:
    """Reconcile every approved concept's emitted kanban tasks and persist outcomes.

    For each ``foundry_concepts.status='approved'`` row that has at least one
    ``foundry_tasks_emitted`` link, fetch the linked ``kanban_tasks`` and decide
    an outcome in this order (first match wins):

      * any task with ``last_failure_reason`` mentioning ``vv_`` / ``verif`` /
        ``oracle`` → ``vv_fail`` (concept.status -> ``failed``)
      * all linked tasks ``done`` + oracle_verifiers pass → ``vv_pass`` (-> ``shipped``)
      * all linked tasks ``done`` (no oracle) → ``shipped`` (-> ``shipped``)
      * any non-vv ``failed`` task → ``vv_fail`` (-> ``failed``)
      * all linked tasks cancelled / no done + no in-flight → ``abandoned`` (-> ``failed``)
      * otherwise: skip (build still in flight)

    Append-only: a concept is recorded once per ``(concept_id, outcome)`` — calling
    ``record_outcomes()`` twice in a row is a no-op for the second call.

    Returns a rollup dict::

        {recorded, shipped, vv_pass, vv_fail, abandoned, skipped_in_flight,
         skipped_non_approved, skipped_already_terminal, error?}
    """
    init_db()
    own = conn is None
    counts: dict[str, int] = {
        "shipped": 0,
        "vv_pass": 0,
        "vv_fail": 0,
        "abandoned": 0,
        "skipped_in_flight": 0,
        "skipped_non_approved": 0,
        "skipped_already_terminal": 0,
    }
    recorded = 0
    err: Optional[str] = None
    try:
        c = _conn(conn)
        try:
            tenant_id, classification = _caller_context()
            rows = c.execute(
                "SELECT id, slug, status FROM foundry_concepts WHERE status = 'approved'"
            ).fetchall()
            for row in rows:
                d = _row_dict(row)
                cid = d.get("id")
                slug = d.get("slug")
                if cid is None:
                    counts["skipped_non_approved"] += 1
                    continue
                tasks = _emitted_tasks(c, int(cid))
                if not tasks:
                    counts["skipped_in_flight"] += 1
                    continue
                outcome = _decide_outcome(tasks)
                if outcome is None:
                    counts["skipped_in_flight"] += 1
                    continue
                # If all-done, see if an oracle verifier already passed → vv_pass.
                if outcome == OUTCOME_SHIPPED and _vv_pass_check(c, int(cid)):
                    outcome = OUTCOME_VV_PASS
                if _has_terminal_outcome(c, int(cid), outcome):
                    counts["skipped_already_terminal"] += 1
                    continue
                _record_outcome(
                    c,
                    concept_id=int(cid),
                    outcome=outcome,
                    detail={
                        "slug": slug,
                        "n_tasks": len(tasks),
                        "task_statuses": [t.get("status") for t in tasks],
                    },
                    tenant_id=tenant_id,
                    classification=classification,
                )
                counts[outcome] += 1
                recorded += 1
        finally:
            if own:
                try:
                    c.close()
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001 - degrade, never raise
        logger.warning("[learner] record_outcomes failed: %s", exc)
        err = str(exc)
    return {
        "recorded": recorded,
        "shipped": counts["shipped"],
        "vv_pass": counts["vv_pass"],
        "vv_fail": counts["vv_fail"],
        "abandoned": counts["abandoned"],
        "skipped_in_flight": counts["skipped_in_flight"],
        "skipped_non_approved": counts["skipped_non_approved"],
        "skipped_already_terminal": counts["skipped_already_terminal"],
        **({"error": err} if err else {}),
    }


# =========================================================================
# tune_weights
# =========================================================================
def _load_config(config: Optional[dict]) -> dict:
    """Full foundry_config dict (or the passed override)."""
    if config is not None:
        return config
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[learner] could not read %s: %s", CONFIG_PATH, exc)
    return {}


def _extract_weights(cfg: dict) -> dict[str, float]:
    """Pull ``foundry.scoring.weights`` (or ``scoring.weights``) into a flat dict."""
    weights = dict(_DEFAULT_WEIGHTS)
    # foundry_config.yaml uses ``foundry:`` as a top-level namespace.
    block = cfg.get("foundry") if isinstance(cfg.get("foundry"), dict) else cfg
    scoring = (block.get("scoring") or {}) if isinstance(block, dict) else {}
    raw = scoring.get("weights") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                weights[k] = float(v)
            except (TypeError, ValueError):
                continue
    return weights


def _dim_to_concept_columns() -> dict[str, str]:
    """Map a scoring.weights key to the foundry_concepts column it tunes."""
    # The foundry config uses market_timing/feasibility/strategic_fit labels; the
    # concept columns use the canonical market_score/fit_score/etc. names. Bridge
    # them so the diff can run against the actual stored values.
    return {
        "novelty": "novelty_score",
        "feasibility": "effort_estimate",   # inverse: low effort = high feasibility
        "strategic_fit": "fit_score",
        "market_timing": "market_score",
    }


def _fetch_score_profiles(
    conn: Any, statuses: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Return concept score profiles for the given foundry_concepts.status values."""
    placeholders = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT id, slug, status,
               novelty_score, market_score, fit_score,
               effort_estimate, compliance_risk
          FROM foundry_concepts
         WHERE status IN ({placeholders})
        """,
        statuses,
    ).fetchall()
    return [_row_dict(r) for r in rows]


def _f(v: Any) -> float:
    """Coerce (possibly NULL) score column to float in [0, 1]."""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _compute_adjustments(
    shipped: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Per-dimension bounded adjustment: shift the weight toward features of
    shipped concepts, clamped to ``[_WEIGHT_FLOOR, _WEIGHT_CEIL]``.

    Returns a list of ``{dim, from, to, delta, n_shipped, n_failed, direction}``.
    Empty when there's no contrast (one side empty or all-same-mean).
    """
    if not shipped or not failed:
        return []
    mapping = _dim_to_concept_columns()
    adjustments: list[dict[str, Any]] = []
    for wkey, col in mapping.items():
        ship_mean = _mean([_f(c.get(col)) for c in shipped])
        fail_mean = _mean([_f(c.get(col)) for c in failed])
        delta = ship_mean - fail_mean
        if abs(delta) < 1e-6:
            continue  # no contrast on this dimension
        # For 'feasibility' (effort), low effort is GOOD — invert the sign.
        if wkey == "feasibility":
            delta = -delta
        # Convert raw mean delta to a bounded weight step. One full step
        # (``_STEP``) per "unit" of contrast; max one step per pass to keep the
        # retune smooth.
        step = max(-_STEP, min(_STEP, delta * _STEP))
        current = round(weights.get(wkey, 0.25), 4)
        proposed = round(max(_WEIGHT_FLOOR, min(_WEIGHT_CEIL, current + step)), 4)
        if proposed == current:
            continue
        direction = "up" if proposed > current else "down"
        adjustments.append(
            {
                "dim": wkey,
                "from": current,
                "to": proposed,
                "delta": round(proposed - current, 4),
                "direction": direction,
                "n_shipped": len(shipped),
                "n_failed": len(failed),
                "shipped_mean": round(ship_mean, 4),
                "failed_mean": round(fail_mean, 4),
            }
        )
    return adjustments


def _persist_config(
    cfg: dict, weights: dict[str, float], adjustments: list[dict[str, Any]]
) -> bool:
    """Write the adjusted weights back to ``args/foundry_config.yaml`` in place,
    preserving every other top-level key (rate_limits, circuit, etc.)."""
    try:
        # Use the same nesting as the existing config (default: foundry: ...).
        if isinstance(cfg.get("foundry"), dict):
            cfg["foundry"].setdefault("scoring", {})
            cfg["foundry"]["scoring"]["weights"] = weights
        else:
            cfg.setdefault("scoring", {})
            cfg["scoring"]["weights"] = weights
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[learner] write %s failed: %s", CONFIG_PATH, exc)
        return False


def tune_weights(*, conn: Any = None, config: Optional[dict] = None) -> dict[str, Any]:
    """Compute a bounded per-dimension weight adjustment from shipped vs failed
    concepts and persist it to ``args/foundry_config.yaml -> foundry.scoring.weights``.

    The step is clamped to ``[_WEIGHT_FLOOR, _WEIGHT_CEIL]`` and bounded by
    ``_STEP`` per pass (mirroring the bounded tuning pattern in
    ``tools/genesis/goal_learner.py``). Other top-level config keys are
    preserved verbatim.

    Returns::

        {adjustments: [{dim, from, to, delta, direction, n_shipped, n_failed,
                        shipped_mean, failed_mean}],
         yaml_path: str, written: bool, error?}
    """
    init_db()
    own = conn is None
    err: Optional[str] = None
    result: dict[str, Any] = {
        "adjustments": [],
        "yaml_path": str(CONFIG_PATH),
        "written": False,
    }
    try:
        c = _conn(conn)
        try:
            shipped = _fetch_score_profiles(c, ("shipped", "vv_pass"))
            failed = _fetch_score_profiles(c, ("failed",))
            if not shipped or not failed:
                logger.info(
                    "[learner] tune_weights: no contrast (shipped=%d failed=%d)",
                    len(shipped),
                    len(failed),
                )
                return result
            cfg = _load_config(config)
            weights = _extract_weights(cfg)
            adjustments = _compute_adjustments(shipped, failed, weights)
            if not adjustments:
                return result
            # Apply adjustments in place on a copy of the weights.
            for a in adjustments:
                weights[a["dim"]] = a["to"]
            written = _persist_config(cfg, weights, adjustments)
            result["adjustments"] = adjustments
            result["written"] = written
        finally:
            if own:
                try:
                    c.close()
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001 - degrade, never raise
        logger.warning("[learner] tune_weights failed: %s", exc)
        err = str(exc)
        result["error"] = err
    return result


# =========================================================================
# CLI
# =========================================================================
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="foundry-learner",
        description=(
            "ACF learner — capture build outcomes + bounded scorer-weight tuning "
            "(CUI // SP-CTI)"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="Reconcile kanban tasks -> foundry_outcomes")
    mode.add_argument("--tune", action="store_true", help="Tune scoring.weights from shipped vs failed contrast")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args(argv)

    if args.record:
        payload = record_outcomes()
    else:
        payload = tune_weights()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        if args.record:
            print(
                f"recorded={payload.get('recorded', 0)} "
                f"(shipped={payload['shipped']} vv_pass={payload['vv_pass']} "
                f"vv_fail={payload['vv_fail']} abandoned={payload['abandoned']} "
                f"skipped_in_flight={payload['skipped_in_flight']})"
            )
        else:
            n = len(payload.get("adjustments") or [])
            print(f"adjustments: {n}  written={payload.get('written', False)}")
            for a in payload.get("adjustments", []):
                print(
                    f"  {a['dim']}: {a['from']:.3f} -> {a['to']:.3f} "
                    f"({a['direction']}, ship={a['shipped_mean']:.2f} fail={a['failed_mean']:.2f})"
                )
    return 0


if __name__ == "__main__":  # pragma: no cover
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    raise SystemExit(main())
