# CUI // SP-CTI
"""Kanban HITL surface — per-document rollup predictions.

Writes one oracle_predictions row per document with open modernization
findings (insert shape mirrors tools/awareness/drift_detector.py) so
tools/awareness/suggested_card_writer.py promotes them to kanban_tasks
status='suggested' with its existing dedup/consolidation/auto-dismiss —
no changes to the card writer.

Sub-threshold findings (confidence < docmod_config.confidence_threshold)
never contribute to a rollup (plan risk rule). reconcile() closes predictions
whose findings are all resolved so the board never shows stale cards.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# lens_name MUST be 'internal_awareness' — tools/awareness/suggested_card_writer.py
# promotes rows WHERE lens_name = <awareness_config oracle.lens_name>; a custom
# lens_name would never reach the kanban board. lens_id carries the docmod marker.
LENS_ID = "doc_modernization"
LENS_NAME = "internal_awareness"
PREDICTION_PREFIX = "modernization::"

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


# 'Awaiting review' includes drafted redlines — a finding with a pending
# redline still needs a human; its kanban card must stay open.
_AWAITING_STATES = ("open", "redline_drafted")


def emit_rollups(conn=None) -> dict:
    """One prediction per document with awaiting-review, above-threshold findings."""
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.pack_loader import load_config

    own = conn is None
    if own:
        conn = _connect()
    try:
        threshold = float(load_config().get("confidence_threshold", 0.7) or 0.7)
        open_findings = [
            f for f in get_findings(conn=conn)
            if f.get("state") in _AWAITING_STATES
            and (f.get("confidence") or 0) >= threshold
        ]
        by_doc: dict[str, list[dict]] = {}
        for f in open_findings:
            by_doc.setdefault(f["doc_id"], []).append(f)

        created, skipped = [], 0
        for doc_id, findings in by_doc.items():
            # prediction-level dedup: one open rollup per document
            existing = conn.execute(
                "SELECT id FROM oracle_predictions WHERE lens_id = %s "
                "AND subject_id = %s AND prediction_type LIKE %s "
                "AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')",
                (LENS_ID, doc_id, PREDICTION_PREFIX + "%"),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            counts: dict[str, int] = {}
            for f in findings:
                counts[f["finding_type"]] = counts.get(f["finding_type"], 0) + 1
            dominant = max(counts, key=counts.get)
            severity = max(
                (f.get("severity") or "medium" for f in findings),
                key=lambda s: _SEVERITY_RANK.get(s, 2),
            )
            title_row = conn.execute(
                "SELECT title FROM dic_documents WHERE doc_id = %s", (doc_id,)
            ).fetchone()
            title = (dict(title_row).get("title") if title_row else None) or doc_id
            summary = ", ".join(f"{v}× {k}" for k, v in sorted(counts.items()))
            text = (
                f"Document '{title}' has {len(findings)} open modernization finding(s): "
                f"{summary}. Review at /document-intelligence/doc/{doc_id}"
            )
            pred_id = f"pred-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO oracle_predictions "
                "(id, lens_id, lens_name, prediction_text, confidence, "
                " created_at, subject_type, subject_id, prediction_type, "
                " severity, horizon_days, evidence_json, classification) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    pred_id, LENS_ID, LENS_NAME, text,
                    max(float(f.get("confidence") or 0) for f in findings),
                    _now(), "dic_document", doc_id,
                    f"{PREDICTION_PREFIX}{dominant}", severity, 0,
                    json.dumps({
                        "finding_ids": [f["finding_id"] for f in findings],
                        "counts": counts,
                        "doc_link": f"/document-intelligence/doc/{doc_id}",
                    }, ensure_ascii=False),
                    findings[0].get("classification") or "CUI // SP-CTI",
                ),
            )
            created.append(pred_id)
        conn.commit()
        return {"created": len(created), "skipped_existing": skipped,
                "docs_with_findings": len(by_doc), "prediction_ids": created}
    finally:
        if own:
            conn.close()


def reconcile(conn=None) -> dict:
    """Close open doc_modernization predictions whose documents no longer have
    open findings (accepted/rejected/superseded) so suggested cards go stale-free."""
    from tools.doc_modernization import get_findings

    own = conn is None
    if own:
        conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, subject_id FROM oracle_predictions WHERE lens_id = %s "
            "AND prediction_type LIKE %s "
            "AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')",
            (LENS_ID, PREDICTION_PREFIX + "%"),
        ).fetchall()]
        closed = []
        for r in rows:
            still_open = [f for f in get_findings(doc_id=r["subject_id"], conn=conn)
                          if f.get("state") in _AWAITING_STATES]
            if not still_open:
                # 'confirmed' per the oracle outcome vocabulary — the predicted
                # modernization need was addressed.
                conn.execute(
                    "UPDATE oracle_predictions SET outcome = 'confirmed', "
                    "outcome_at = %s WHERE id = %s",
                    (_now(), r["id"]),
                )
                closed.append(r["id"])
        conn.commit()
        return {"open_rollups": len(rows), "closed": len(closed)}
    finally:
        if own:
            conn.close()
