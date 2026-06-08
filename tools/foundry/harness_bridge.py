# CUI // SP-CTI
"""harness_bridge.py — ACF <-> Genesis Harness bridge (acf-ada-01).

Closes the ACF feedback loop into the Genesis Continuous Evaluation Harness so
``compute_metrics(reflex="acf")`` returns precision/recall/ECE for the foundry's
own concept approval decisions.

Two write-paths:

  * :func:`record_acf_decision` — called from :func:`tools.foundry.engine.run_cycle`
    AFTER the novelty/score/CoD stages, once per *approved* concept. It writes a
    ``harness_eval`` row with ``reflex='acf'`` and ``decision='acf_approve'`` (or
    ``'acf_reject'`` for concepts that fell below the score gate). The
    composite_score is forwarded as the model's *confidence*.

  * :func:`record_acf_outcome` — called from :func:`tools.foundry.learner.record_outcomes`
    AFTER the concept's build is reconciled to a terminal state
    (``shipped`` / ``vv_pass`` / ``vv_fail`` / ``abandoned``). It updates the
    matching ``harness_eval`` row's ``actual_outcome`` so precision/recall can be
    computed by the harness reflex.

Graceful degradation: if ``tools.genesis.harness.eval_harness`` is missing,
``harness_eval`` table is absent, or any DB error fires, the bridge logs a
warning and returns a sentinel — neither :func:`tools.foundry.engine.run_cycle`
nor :func:`tools.foundry.learner.record_outcomes` is ever crashed by it.

The task_id used for both halves is the foundry concept's ``slug`` (stable,
unique, human-readable) — NOT the kanban task id, because one concept fans out
to N kanban tasks but only ONE approval decision. This lets the harness reflex
join cleanly back to ``foundry_concepts.slug`` when a concept's outcome arrives.

Public API
----------
    record_acf_decision(*, slug, decision_type, confidence, metadata=None) -> str | None
    record_acf_outcome(*, slug, actual_outcome) -> bool
    compute_acf_metrics(*, window_days=30) -> dict
    is_bridge_available() -> bool

Decision-type vocabulary (single source of truth — keep in sync with
:data:`tools.foundry.constants` outcomes + ``eval_harness`` schema)::

    "acf_approve"   — engine approved the concept (CoD build OR score-gate fallback)
    "acf_reject"    — engine rejected the concept (CoD no_build OR score-gate miss)
    "acf_skip"      — concept was proposed but not yet decided (rate-limited cycle)

Outcome vocabulary (forwarded from ``tools.foundry.learner.OUTCOME_*``)::

    "shipped"       — concept was built; all emitted tasks are done
    "vv_pass"       — concept was built and V&V oracle passed
    "vv_fail"       — concept was built but a V&V/verification step failed
    "abandoned"     — concept was approved but the build was never finished
"""
from __future__ import annotations

from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.foundry.harness_bridge")


# Decision-type constants — exported so engine.py / learner.py don't hard-code strings.
DECISION_APPROVE = "acf_approve"
DECISION_REJECT = "acf_reject"
DECISION_SKIP = "acf_skip"

# Reflex name registered in harness_eval.reflex for all ACF rows.
REFLEX_ACF = "acf"

# Bridge disabled sentinel — returned by record_*() when eval_harness is unavailable
# or the harness_eval table does not exist (e.g. a hermetic test that didn't seed it).
_BRIDGE_DISABLED = "__bridge_disabled__"


# ---------------------------------------------------------------------------
# Import / availability detection
# ---------------------------------------------------------------------------
def _try_import_harness() -> Optional[Any]:
    """Best-effort import of ``tools.genesis.harness.eval_harness``.

    Returns the module object on success, or None on any failure (missing
    module, import error, package absent in air-gap mode). Never raises.
    """
    for modname in (
        "tools.genesis.harness.eval_harness",
        "icdev.tools.genesis.harness.eval_harness",
    ):
        try:
            import importlib
            return importlib.import_module(modname)
        except Exception:  # noqa: BLE001 - air-gap / pre-init / schema mismatch
            continue
    return None


def is_bridge_available() -> bool:
    """True if the Genesis Harness module is importable and the harness_eval
    table can be opened. Cheap: a single import + a single SELECT 1."""
    harness = _try_import_harness()
    if harness is None:
        return False
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            conn.execute("SELECT 1 FROM harness_eval LIMIT 1").fetchone()
            return True
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:  # noqa: BLE001 - missing table / no DB / RLS denial
        return False


# ---------------------------------------------------------------------------
# Decision writer (called from engine.run_cycle)
# ---------------------------------------------------------------------------
def record_acf_decision(
    *,
    slug: str,
    decision_type: str,
    confidence: Optional[float] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Record a single ACF approval/rejection decision in harness_eval.

    Args:
        slug: foundry_concepts.slug (stable, unique). Becomes the
            harness_eval.task_id so the matching ``record_acf_outcome`` call
            can locate this row deterministically.
        decision_type: one of ``DECISION_APPROVE`` / ``DECISION_REJECT`` /
            ``DECISION_SKIP``. Stored verbatim in the ``decision`` column.
        confidence: composite score from the ACF pipeline (0.0-1.0). Used
            by the harness for ECE computation.
        metadata: extra context (run_id, verdicts, stage timings). Stored
            in the ``metadata_json`` column.

    Returns the new harness_eval row id, or ``_BRIDGE_DISABLED`` if the
    bridge is unavailable. Never raises — engine.run_cycle must not crash
    because the harness is down.
    """
    if decision_type not in (DECISION_APPROVE, DECISION_REJECT, DECISION_SKIP):
        logger.warning(
            "[harness_bridge] unknown decision_type=%r for slug=%s; recording as 'acf_skip'",
            decision_type, slug,
        )
        decision_type = DECISION_SKIP

    harness = _try_import_harness()
    if harness is None:
        logger.debug("[harness_bridge] eval_harness unavailable; skipping record_acf_decision")
        return _BRIDGE_DISABLED

    payload_meta: dict = {"source": "acf_engine", "decision_type": decision_type}
    if metadata:
        # Always store the run_id + whatever else the caller forwarded; keep it
        # flat (no nested objects) so SQL JSON1 / jsonb queries stay simple.
        for key, val in (metadata or {}).items():
            payload_meta[key] = val

    try:
        row_id = harness.record_decision(
            task_id=slug or "",
            reflex=REFLEX_ACF,
            decision=decision_type,
            confidence=_coerce_confidence(confidence),
            metadata=payload_meta,
        )
        logger.debug(
            "[harness_bridge] recorded ACF decision row_id=%s slug=%s decision=%s conf=%.3f",
            row_id, slug, decision_type, float(confidence or 0.0),
        )
        return str(row_id or "")
    except Exception as exc:  # noqa: BLE001 - never crash the engine
        logger.warning("[harness_bridge] record_acf_decision failed for slug=%s: %s", slug, exc)
        return _BRIDGE_DISABLED


# ---------------------------------------------------------------------------
# Outcome writer (called from learner.record_outcomes)
# ---------------------------------------------------------------------------
def record_acf_outcome(*, slug: str, actual_outcome: str) -> bool:
    """Update the matching harness_eval decision row with the build outcome.

    Maps the learner's outcome vocabulary (``shipped`` / ``vv_pass`` /
    ``vv_fail`` / ``abandoned``) directly into ``harness_eval.actual_outcome``
    — the harness compute_metrics function knows these strings and computes
    precision against ``'resolved'``. To keep precision comparable across ACF,
    we map ``vv_pass`` -> ``resolved`` (positive) and everything else stays
    as-is so the harness's own normalization (in :func:`compute_metrics`)
    treats them as the appropriate terminal.

    Returns True if the row was located and updated, False otherwise
    (no matching decision row, bridge unavailable, DB error). Never raises.
    """
    if not slug:
        return False
    harness = _try_import_harness()
    if harness is None:
        logger.debug("[harness_bridge] eval_harness unavailable; skipping record_acf_outcome")
        return False

    # The harness treats 'resolved' as the positive class for precision.
    # Treat vv_pass as a success too — forward it as 'resolved' so the
    # harness's standard precision formula covers both shipped & vv_pass.
    normalized = "resolved" if actual_outcome in ("shipped", "vv_pass") else actual_outcome

    try:
        # We can't call harness.record_outcome() directly because it would
        # update *every* row for this task_id, including any non-ACF rows the
        # harness may have recorded (e.g. oracle_triage). Instead, do a
        # targeted UPDATE that scopes to reflex='acf'.
        from tools.db.storage import get_connection
        from datetime import datetime, timezone

        conn = get_connection()
        try:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cur = conn.execute(
                """
                UPDATE harness_eval
                   SET actual_outcome = ?,
                       resolved_at    = ?
                 WHERE task_id = ?
                   AND reflex  = ?
                   AND actual_outcome IS NULL
                """,
                (normalized, now, slug, REFLEX_ACF),
            )
            updated = int(getattr(cur, "rowcount", 0) or 0)
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if updated:
            logger.debug(
                "[harness_bridge] recorded ACF outcome slug=%s outcome=%s (normalized=%s)",
                slug, actual_outcome, normalized,
            )
            return True
        logger.debug(
            "[harness_bridge] no pending ACF decision row for slug=%s (already resolved?)",
            slug,
        )
        return False
    except Exception as exc:  # noqa: BLE001 - never crash the learner
        logger.warning("[harness_bridge] record_acf_outcome failed for slug=%s: %s", slug, exc)
        return False


# ---------------------------------------------------------------------------
# Metrics wrapper
# ---------------------------------------------------------------------------
def compute_acf_metrics(*, window_days: int = 30) -> dict:
    """Wrapper over :func:`harness.compute_metrics` for reflex='acf'.

    Returns an empty dict on bridge failure (the caller — typically the
    foundry status page — should treat absence-of-data as "no metrics yet").
    """
    harness = _try_import_harness()
    if harness is None:
        return {
            "reflex": REFLEX_ACF,
            "window_days": window_days,
            "available": False,
            "reason": "eval_harness module not importable",
            "total_decisions": 0,
        }
    try:
        m = harness.compute_metrics(REFLEX_ACF, window_days=window_days)
        m["available"] = True
        return m
    except Exception as exc:  # noqa: BLE001
        logger.warning("[harness_bridge] compute_acf_metrics failed: %s", exc)
        return {
            "reflex": REFLEX_ACF,
            "window_days": window_days,
            "available": False,
            "reason": str(exc),
            "total_decisions": 0,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _coerce_confidence(value: Any) -> Optional[float]:
    """Clamp confidence to [0.0, 1.0] and convert to float. None stays None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f
