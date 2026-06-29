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
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from tools.logging.icdev_logger import get_logger
from tools.db.storage import sql_placeholder

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
            detail      TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_aadc_confidence_design
            ON aadc_confidence_events(design_id, created_at);

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
    placeholders = ",".join([sql_placeholder(conn)] * 8)
    conn.execute(
        "INSERT INTO aadc_confidence_events "
        "(id, design_id, node_id, score, threshold, decision, detail, created_at) "
        f"VALUES ({placeholders})",
        (eid, design_id, node_id, score, threshold, decision, detail,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return eid


# ---------------------------------------------------------------------------
# Core gate evaluation
# ---------------------------------------------------------------------------

def evaluate(
    design_id: str,
    output_text: str,
    score: float,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    node_id: Optional[str] = None,
) -> dict:
    """Evaluate a single gate decision.

    Args:
        design_id: AADC design identifier.
        output_text: The LLM-generated text being evaluated (stored in audit).
        score: Confidence score from the LLM (0.0–1.0).
        threshold: Minimum score to auto-proceed (default: DEFAULT_THRESHOLD).
        node_id: Optional pipeline node identifier for the audit trail.

    Returns:
        Dict with keys: id, design_id, score, threshold, decision, allowed, node_id.
    """
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score must be 0.0–1.0, got {score}")

    if score >= threshold:
        decision = "proceed"
    else:
        decision = DEFAULT_LOW_ACTION

    allowed = decision == "proceed"
    detail_msg = f"score={score:.2f}, threshold={threshold:.2f}"
    audit_detail = f"{detail_msg}; {(output_text or '')[:400]}"

    try:
        conn = _get_conn()
        try:
            _ensure_tables(conn)
            eid = _audit_event(conn, design_id, node_id, score, threshold, decision, audit_detail)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("confidence_gate: evaluate DB error: %s", exc)
        eid = f"cg-{uuid.uuid4().hex[:10]}"

    return {
        "event_id": eid,
        "design_id": design_id,
        "score": score,
        "threshold": threshold,
        "decision": decision,
        "allowed": allowed,
        "node_id": node_id,
        "output_text_len": len(output_text or ""),
        "detail": detail_msg,
    }


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
            placeholders = ",".join([sql_placeholder(conn)] * 4)
            conn.execute(
                "INSERT INTO aadc_gate_configs (design_id, threshold, low_confidence_action, updated_at) "
                f"VALUES ({placeholders}) "
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
# Pipeline node helper
# ---------------------------------------------------------------------------

def evaluate_pipeline_node(
    design_id: str,
    llm_output: dict,
    *,
    threshold: float | None = None,
    node_id: str | None = None,
) -> dict:
    """Evaluate an LLM pipeline node output through the confidence gate.

    Args:
        design_id: AADC design identifier.
        llm_output: Dict with optional ``confidence`` (0–1) and ``text`` keys.
        threshold: Override; defaults to the persisted config for *design_id*.
        node_id: Optional pipeline node ID for the audit trail.
    """
    score = float(llm_output.get("confidence", 0.5))
    text = str(llm_output.get("text", ""))
    if threshold is None:
        threshold = get_config(design_id)["threshold"]
    result = evaluate(design_id, text, score=score, threshold=threshold, node_id=node_id)
    result["raw_llm_output"] = llm_output
    return result


# ---------------------------------------------------------------------------
# Graph helpers (local — mirrors observability_nodes._build_adjacency/_reachable)
# ---------------------------------------------------------------------------

def _build_adjacency(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
    return adj


def _reachable(start: str, adj: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    q: deque[str] = deque([start])
    while q:
        node = q.popleft()
        if node in visited:
            continue
        visited.add(node)
        for nxt in adj.get(node, ()):
            if nxt not in visited:
                q.append(nxt)
    return visited


# ---------------------------------------------------------------------------
# Path-level gate check
# ---------------------------------------------------------------------------

def check_confidence_gate_path(
    nodes: list[dict], edges: list[dict]
) -> list[dict]:
    """Check that every LLM node reaches an output-validator via a confidence gate.

    For each ``llm`` node that can reach an ``output-validator`` in the forward
    graph, emits a HIGH/MEA-1 finding when no ``confidence-threshold`` node is
    reachable from that LLM.

    Returns:
        List of finding dicts (empty when all paths are safe).
    """
    adj = _build_adjacency(edges)

    llm_nodes = {n["id"]: n for n in nodes if n.get("type") == "llm"}
    validator_ids = {n["id"] for n in nodes if n.get("type") == "output-validator"}
    gate_ids = {n["id"] for n in nodes if n.get("type") == "confidence-threshold"}

    if not llm_nodes or not validator_ids:
        return []

    findings: list[dict] = []
    for llm_id, llm_node in llm_nodes.items():
        reachable = _reachable(llm_id, adj)
        if not (reachable & validator_ids):
            continue  # this LLM doesn't reach any validator — no gate required
        if reachable & gate_ids:
            continue  # at least one confidence-threshold is reachable — ok
        label = llm_node.get("label", llm_id)
        findings.append({
            "id": f"cg-missing-{llm_id[:6]}",
            "framework": "MEA-1 Model Evaluation & Assurance",
            "category": "MEA-1 Model Evaluation & Assurance",
            "severity": "HIGH",
            "title": "LLM node missing confidence-threshold gate before output-validator",
            "detail": (
                f"LLM node '{label}' can reach an output-validator without passing "
                "through a confidence-threshold gate."
            ),
            "recommendation": (
                "Add a confidence-threshold node between the LLM and output-validator "
                "to filter low-confidence outputs before downstream processing."
            ),
        })

    return findings
