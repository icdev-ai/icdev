# CUI // SP-CTI
"""AADC Confidence Gate — Safety layer for the Agentic Research Pipeline.

Implements the `confidence-threshold` node type.  When the Synthesis LLM
produces a draft answer the Confidence Gate decides whether to:

  * **Proceed**  (score >= threshold) → route to output-validator downstream
  * **Escalate** (score < threshold)  → route to hitl-gate / human review

Public API
----------
evaluate(design_id, output_text, score, *, threshold, node_id) -> dict
    Core gate evaluation.  Returns a routing decision with full audit context.

get_config(design_id) -> dict
    Return current gate configuration for a design.

update_config(design_id, threshold, *, low_confidence_action) -> dict
    Persist updated gate configuration.

Persistence
-----------
Gate decisions are written to `aadc_confidence_events` (append-only, NIST AU-2)
and gate configs are stored in `aadc_gate_configs`.  Both live in the AADC
canvas database returned by `get_canvas_connection`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 0.70          # minimum confidence to auto-proceed
DEFAULT_LOW_ACTION = "escalate"   # "escalate" | "block" | "retry"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    """Return a canvas DB connection for the AADC tables."""
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("AADC_DB_PATH")


def _ensure_tables(conn) -> None:
    """Create gate tables if they don't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS aadc_confidence_events (
            id          TEXT PRIMARY KEY,
            design_id   TEXT NOT NULL,
            node_id     TEXT,
            score       REAL NOT NULL,
            threshold   REAL NOT NULL,
            decision    TEXT NOT NULL,
            detail      TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS aadc_gate_configs (
            design_id           TEXT PRIMARY KEY,
            threshold           REAL NOT NULL DEFAULT 0.70,
            low_confidence_action TEXT NOT NULL DEFAULT 'escalate',
            updated_at          TEXT NOT NULL
        );
    """)
    conn.commit()


def _audit_event(conn, design_id: str, node_id: Optional[str],
                 score: float, threshold: float, decision: str, detail: str) -> str:
    """Append a confidence gate decision to aadc_confidence_events."""
    eid = f"cg-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "INSERT INTO aadc_confidence_events "
        "(id, design_id, node_id, score, threshold, decision, detail, created_at) "
        f"VALUES ({"","".join([sql_placeholder(conn)]*8)""})",
        (eid, design_id, node_id, score, threshold, decision, detail,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return eid


# ---------------------------------------------------------------------------
# Gate configuration
# ---------------------------------------------------------------------------

def get_config(design_id: str) -> dict:
    """Return the confidence gate configuration for a design.

    Falls back to defaults when no config row exists.
    """
    try:
        conn = _get_conn()
        try:
            _ensure_tables(conn)
            row = conn.execute(
                "SELECT threshold, low_confidence_action FROM aadc_gate_configs "
                f"WHERE design_id = {sql_placeholder(conn)}", (design_id,)
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("confidence_gate: get_config DB error: %s", exc)
        row = None

    if row:
        return {
            "design_id": design_id,
            "threshold": row[0],
            "low_confidence_action": row[1],
        }
    return {
        "design_id": design_id,
        "threshold": DEFAULT_THRESHOLD,
        "low_confidence_action": DEFAULT_LOW_ACTION,
    }


def update_config(design_id: str, threshold: float,
                  low_confidence_action: str = DEFAULT_LOW_ACTION) -> dict:
    """Persist gate configuration for a design (upsert).

    Args:
        design_id: AADC design ID.
        threshold: Minimum confidence score to auto-proceed (0.0–1.0).
        low_confidence_action: One of 'escalate' | 'block' | 'retry'.

    Returns:
        Updated configuration dict.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be 0.0–1.0, got {threshold}")
    if low_confidence_action not in ("escalate", "block", "retry"):
        raise ValueError("low_confidence_action must be escalate|block|retry")

    try:
        conn = _get_conn()
        try:
            _ensure_tables(conn)
            conn.execute(
                "INSERT INTO aadc_gate_configs (design_id, threshold, low_confidence_action, updated_at) "
                f"VALUES ({"","".join([sql_placeholder(conn)]*4)""}) "
                "ON CONFLICT(design_id) DO UPDATE SET "
                "  threshold=excluded.threshold, "
                "  low_confidence_action=excluded.low_confidence_action, "
                "  updated_at=excluded.updated_at",
                (design_id, threshold, low_confidence_action,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("confidence_gate: update_config DB error: %s", exc)

    return {
        "design_id": design_id,
        "threshold": threshold,
        "low_confidence_action": low_confidence_action,
    }


# ---------------------------------------------------------------------------
# Core gate evaluation
# ---------------------------------------------------------------------------

def evaluate(
    design_id: str,
    output_text: str,
    score: float,
    *,
    threshold: Optional[float] = None,
    node_id: Optional[str] = None,
) -> dict:
    """Evaluate a confidence score against the gate threshold.

    Args:
        design_id:   AADC design ID (e.g. 'aadc-400a241d').
        output_text: LLM-generated draft text being evaluated.
        score:       Confidence score in [0.0, 1.0].  Callers derive this from
                     LLM token probabilities, a meta-prompt self-rating, citation
                     coverage, or any domain-appropriate signal.
        threshold:   Override the persisted threshold for this call.  Defaults
                     to the design's configured threshold (or DEFAULT_THRESHOLD).
        node_id:     ID of the confidence-threshold node in the design graph
                     (used for audit correlation).

    Returns:
        dict with keys:
            decision        : "proceed" | "escalate" | "block"
            allowed         : True when decision == "proceed"
            score           : float — the supplied confidence score
            threshold       : float — the threshold applied
            output_text_len : int — length of the evaluated text (not stored)
            event_id        : str — audit event ID
            detail          : str — human-readable rationale
    """
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score must be 0.0–1.0, got {score}")

    cfg = get_config(design_id)
    effective_threshold = threshold if threshold is not None else cfg["threshold"]
    low_action = cfg["low_confidence_action"]

    if score >= effective_threshold:
        decision = "proceed"
        allowed = True
        detail = (
            f"Confidence {score:.2f} >= threshold {effective_threshold:.2f} — "
            "routing to output-validator."
        )
    else:
        decision = low_action  # "escalate" | "block" | "retry"
        allowed = False
        detail = (
            f"Confidence {score:.2f} < threshold {effective_threshold:.2f} — "
            f"action: {low_action}.  Routing to HITL / human review."
        )
        logger.info(
            "confidence_gate: LOW confidence for design %s (score=%.2f threshold=%.2f) → %s",
            design_id, score, effective_threshold, low_action,
        )

    event_id = _write_event(design_id, node_id, score, effective_threshold,
                            decision, detail)

    return {
        "decision": decision,
        "allowed": allowed,
        "score": score,
        "threshold": effective_threshold,
        "output_text_len": len(output_text),
        "event_id": event_id,
        "detail": detail,
    }


def _write_event(design_id: str, node_id: Optional[str], score: float,
                 threshold: float, decision: str, detail: str) -> str:
    """Write a gate event to the audit table.  Returns event ID."""
    try:
        conn = _get_conn()
        try:
            _ensure_tables(conn)
            eid = _audit_event(conn, design_id, node_id, score, threshold,
                               decision, detail)
        finally:
            conn.close()
        return eid
    except Exception as exc:
        logger.warning("confidence_gate: event write failed: %s", exc)
        return f"cg-mem-{uuid.uuid4().hex[:8]}"  # fallback in-memory ID


# ---------------------------------------------------------------------------
# Pipeline integration helper
# ---------------------------------------------------------------------------

def evaluate_pipeline_node(
    design_id: str,
    llm_output: dict,
    *,
    threshold: Optional[float] = None,
    node_id: Optional[str] = None,
) -> dict:
    """Convenience wrapper for Agentic Research Pipeline node execution.

    Extracts ``score`` from the LLM output dict (key ``confidence``) and
    delegates to :func:`evaluate`.  Suitable for use in
    ``tools/agentic_ai_canvas/workflow.py`` and the AADC execution engine.

    Args:
        design_id:   AADC design ID.
        llm_output:  Dict returned by the Synthesis LLM step, expected to
                     contain a ``confidence`` key (float 0–1).  Falls back to
                     0.5 when absent so the gate degrades gracefully.
        threshold:   Optional threshold override.
        node_id:     Graph node ID for audit correlation.

    Returns:
        Same dict as :func:`evaluate` plus ``raw_llm_output`` (the full dict).
    """
    score = float(llm_output.get("confidence", 0.5))
    text = str(llm_output.get("text", llm_output.get("content", "")))
    result = evaluate(design_id, text, score, threshold=threshold, node_id=node_id)
    result["raw_llm_output"] = llm_output
    return result


# ---------------------------------------------------------------------------
# Assessment helper (used by agentic_engine.py)
# ---------------------------------------------------------------------------

def check_confidence_gate_path(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Verify that every LLM node in the design has a confidence-threshold
    downstream before the output-validator.

    This enforces the canonical Agentic Research Pipeline safety pattern:
        Synthesis LLM → Confidence Gate → Output Validator

    Returns a list of findings (empty = all good).
    """
    findings: list[dict] = []
    llm_ids = [n["id"] for n in nodes if n.get("type") in {"llm", "llm-local"}]
    if not llm_ids:
        return findings

    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.get("source", ""), set()).add(e.get("target", ""))

    node_map = {n["id"]: n for n in nodes}

    def _reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj.get(n, set()) - seen)
        return seen

    for lid in llm_ids:
        reachable = _reachable(lid)
        rt = {node_map.get(r, {}).get("type") for r in reachable}
        has_gate = "confidence-threshold" in rt
        has_validator = "output-validator" in rt
        if has_validator and not has_gate:
            findings.append({
                "id": f"cg-missing-{lid[:8]}",
                "framework": "NIST AI RMF",
                "function": "MEASURE",
                "category": "MEA-1",
                "severity": "HIGH",
                "title": "LLM output reaches validator without confidence gate",
                "detail": (
                    f"LLM node '{node_map.get(lid, {}).get('label', lid)}' routes "
                    "directly to output-validator with no confidence-threshold node "
                    "on the path.  NIST AI RMF MEA-1 requires hallucination bounding "
                    "before output leaves the AI system."
                ),
                "recommendation": (
                    "Insert a confidence-threshold node between the Synthesis LLM "
                    "and the output-validator.  Configure threshold ≥ 0.70 and wire "
                    "the low-confidence path to a hitl-gate."
                ),
            })
    return findings
