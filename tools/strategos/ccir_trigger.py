# CUI // SP-CTI
"""CCIR Trigger Engine — auto-evaluate active CCIRs against recent signals.

Scans the last N hours of SIGINT, EO, and SOCMINT signals for keyword
matches against active CCIR topics and descriptions. Records trigger events
in sg_ccir_trigger_events and returns a list of newly-triggered CCIRs.

Usage:
    from tools.strategos.ccir_trigger import evaluate_ccirs
    result = evaluate_ccirs(lookback_hours=12)
    print(result["triggered"])
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = get_logger("icdev.strategos.ccir_trigger")

from tools.strategos.economic_importer import _cusum_alert
from tools.canvas.event_bus import publish
from tools.notifications.gateway import NotificationGateway


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keywords(text: str) -> list[str]:
    """Extract meaningful keywords from CCIR topic/description text."""
    stopwords = {"a", "an", "the", "of", "in", "on", "at", "to", "for",
                 "and", "or", "is", "are", "was", "were", "be", "been",
                 "by", "with", "from", "that", "this", "which", "what"}
    words = [w.strip(".,;:?!\"'()[]").lower() for w in text.split()]
    return [w for w in words if len(w) > 3 and w not in stopwords]


def _match_score(keywords: list[str], signal_text: str) -> float:
    """Return keyword match density [0.0, 1.0]."""
    if not keywords or not signal_text:
        return 0.0
    lower = signal_text.lower()
    hits = sum(1 for kw in keywords if kw in lower)
    return hits / len(keywords)


def _get_active_ccirs(conn) -> list[dict]:
    from tools.db.storage import is_pg
    ph = "%s" if is_pg() else "?"
    rows = conn.execute(
        f"SELECT id, pir_type, topic, description, collection_priority "  # nosec B608
        f"FROM sg_pir_requirements "
        f"WHERE pir_type = {ph} AND status = {ph}",
        ("CCIR", "active"),
    ).fetchall()
    cols = ("id", "pir_type", "topic", "description", "collection_priority")
    return [dict(zip(cols, r)) for r in rows]


def _get_recent_signals(conn, lookback_hours: int) -> list[dict]:
    from tools.db.storage import is_pg
    ph = "%s" if is_pg() else "?"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    signals: list[dict] = []

    signal_tables = [
        ("sg_sigint_events",   "id", "description",  "event_type",  "collected_at"),
        ("sg_eo_signals",      "id", "description",  "signal_type", "collected_at"),
        ("sg_socmint_signals", "id", "text",          "platform",    "posted_at"),
    ]
    for table, id_col, text_col, src_col, ts_col in signal_tables:
        try:
            rows = conn.execute(
                f"SELECT {id_col}, {text_col}, {src_col} "  # nosec B608
                f"FROM {table} WHERE {ts_col} >= {ph}",
                (cutoff,),
            ).fetchall()
            for r in rows:
                signals.append({
                    "source_table": table,
                    "sig_id": r[0],
                    "text": r[1] or "",
                    "source": r[2] or table,
                })
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.debug("Skipping %s: %s", table, exc)

    return signals


def _already_triggered_today(conn, ccir_id: str) -> bool:
    from tools.db.storage import is_pg
    ph = "%s" if is_pg() else "?"
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        f"SELECT COUNT(*) FROM sg_ccir_trigger_events "  # nosec B608
        f"WHERE ccir_id = {ph} AND created_at >= {ph} AND resolved = 0",
        (ccir_id, today),
    ).fetchone()
    return (row[0] if row else 0) > 0


def _get_historical_scores(conn, ccir_id: str) -> list[float]:
    from tools.db.storage import is_pg
    ph = "%s" if is_pg() else "?"
    rows = conn.execute(
        f"SELECT match_score FROM sg_ccir_trigger_events "  # nosec B608
        f"WHERE ccir_id = {ph} "
        f"ORDER BY created_at",
        (ccir_id,),
    ).fetchall()
    return [r[0] for r in rows if r[0] is not None]


def _validate_baseline(conn, ccir_id: str, new_score: float) -> bool:
    """Return True if new_score deviates significantly from historical baseline."""
    scores = _get_historical_scores(conn, ccir_id)
    if len(scores) < 3:
        return True  # No baseline established yet
    return _cusum_alert(scores, new_score)


def evaluate_ccirs(lookback_hours: int = 12, threshold: float = 0.15) -> dict[str, Any]:
    """Evaluate all active CCIRs against recent signals.

    Returns:
        {triggered: [...], evaluated: int, signal_count: int}
    """
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        ccirs = _get_active_ccirs(conn)
        signals = _get_recent_signals(conn, lookback_hours)
        triggered = []

        for ccir in ccirs:
            keywords = _keywords((ccir["topic"] or "") + " " + (ccir["description"] or ""))
            if not keywords:
                continue
            if _already_triggered_today(conn, ccir["id"]):
                continue

            best_score = 0.0
            best_signal: dict | None = None
            for sig in signals:
                score = _match_score(keywords, sig["text"])
                if score > best_score:
                    best_score = score
                    best_signal = sig

            if best_score >= threshold and best_signal:
                baseline_valid = _validate_baseline(conn, ccir["id"], best_score)
                event_id = str(uuid.uuid4())
                now = _now_utc()
                conn.execute(
                    "INSERT INTO sg_ccir_trigger_events "
                    "(id, ccir_id, signal_source, signal_text, match_score, resolved, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?)",
                    (
                        event_id,
                        ccir["id"],
                        best_signal["source"],
                        best_signal["text"][:500],
                        round(best_score, 4),
                        now,
                    ),
                )
                triggered.append({
                    "ccir_id": ccir["id"],
                    "topic": ccir["topic"],
                    "match_score": round(best_score, 4),
                    "signal_source": best_signal["source"],
                    "signal_snippet": best_signal["text"][:200],
                    "event_id": event_id,
                    "baseline_valid": baseline_valid,
                })

                if baseline_valid:
                    try:
                        publish(
                            "strategos",
                            "pir.baseline.validated",
                            {
                                "ccir_id": ccir["id"],
                                "topic": ccir["topic"],
                                "match_score": round(best_score, 4),
                                "signal_source": best_signal["source"],
                                "signal_snippet": best_signal["text"][:200],
                                "event_id": event_id,
                            },
                            target_canvas="commander",
                            security_context={
                                "clearance": "CUI",
                                "compartment": None,
                                "tenant_id": None,
                            },
                        )
                    except Exception:
                        logger.debug("Event bus publish failed for PIR alert", exc_info=True)

                    try:
                        gateway = NotificationGateway()
                        gateway.send(
                            event_type="pir_alert",
                            severity="critical",
                            title=f"PIR baseline threshold crossed: {ccir['topic']}",
                            body=(
                                f"CCIR {ccir['id']} triggered with match score {round(best_score, 4)}. "
                                f"Source: {best_signal['source']}. "
                                f"Snippet: {best_signal['text'][:200]}"
                            ),
                            metadata={"ccir_id": ccir["id"], "event_id": event_id},
                        )
                    except Exception:
                        logger.debug("Notification gateway send failed for PIR alert", exc_info=True)

        conn.commit()
        return {
            "triggered": triggered,
            "evaluated": len(ccirs),
            "signal_count": len(signals),
        }
    finally:
        conn.close()


def get_recent_trigger_events(limit: int = 20) -> list[dict]:
    """Return most recent unresolved trigger events with CCIR details."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT e.id, e.ccir_id, e.signal_source, e.signal_text, "  # nosec B608
            f"e.match_score, e.created_at, p.topic, p.collection_priority "
            f"FROM sg_ccir_trigger_events e "
            f"LEFT JOIN sg_pir_requirements p ON p.id = e.ccir_id "
            f"WHERE e.resolved = 0 "
            f"ORDER BY e.created_at DESC LIMIT {ph}",
            (limit,),
        ).fetchall()
        cols = ("id", "ccir_id", "signal_source", "signal_text",
                "match_score", "created_at", "ccir_topic", "priority")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def resolve_trigger_event(event_id: str) -> bool:
    """Mark a trigger event resolved (acknowledged by analyst)."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE sg_ccir_trigger_events SET resolved=1, resolved_at={ph} WHERE id={ph}",  # nosec B608
            (_now_utc(), event_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("resolve_trigger_event failed: %s", exc)
        return False
    finally:
        conn.close()
