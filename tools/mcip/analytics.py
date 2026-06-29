# CUI // SP-CTI
"""MCIP DAT analytics — ingestion, DTI scoring, and query helpers."""
from __future__ import annotations

import uuid
import zlib
from datetime import datetime, timedelta, timezone

from tools.mcip.constants import DAT_SOURCE_TYPES, DTI_WEIGHTS, classify_dti

from tools.logging.icdev_logger import get_logger
log = get_logger("mcip.analytics")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_event(
    source_type: str,
    content_hash: str,
    sender: str,
    recipient: str,
    classification: str,
    tension_signal: float,
) -> str:
    """Insert one DAT event; returns the new event_id."""
    if source_type not in DAT_SOURCE_TYPES:
        raise ValueError(f"unknown source_type: {source_type!r}")
    tension_signal = max(0.0, min(1.0, float(tension_signal)))
    event_id = str(uuid.uuid4())
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO mcip_dat_events
                (id, source_type, content_hash, sender, recipient,
                 classification, tension_signal, ingested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (event_id, source_type, content_hash, sender, recipient,
             classification, tension_signal, _now_iso()),
        )
        conn.commit()
    except Exception as exc:
        log.warning("mcip ingest_event error: %s", exc)
    finally:
        conn.close()
    return event_id


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_dti(window_hours: int = 6) -> dict:
    """Compute weighted DTI over the last *window_hours* of events.

    Returns dict with keys: score, band, sub_scores, event_count.
    """
    since = (
        datetime.now(timezone.utc) - timedelta(hours=window_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = _get_conn()
    sub_scores: dict[str, float] = {}
    event_count = 0
    try:
        for src in DAT_SOURCE_TYPES:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt, AVG(tension_signal) as avg_sig
                FROM mcip_dat_events
                WHERE source_type = %s AND ingested_at >= %s
                """,
                (src, since),
            ).fetchone()
            cnt = row["cnt"] if row else 0
            avg_sig = row["avg_sig"] if (row and row["avg_sig"] is not None) else 0.0
            sub_scores[src] = float(avg_sig)
            event_count += int(cnt)
    except Exception as exc:
        log.warning("mcip compute_dti query error: %s", exc)
    finally:
        conn.close()

    raw = sum(DTI_WEIGHTS[src] * sub_scores.get(src, 0.0) for src in DAT_SOURCE_TYPES)
    score = round(min(100.0, max(0.0, raw * 100)), 2)
    return {
        "score": score,
        "band": classify_dti(score),
        "sub_scores": {src: round(sub_scores.get(src, 0.0) * 100, 2) for src in DAT_SOURCE_TYPES},
        "event_count": event_count,
    }


def record_dti_score(score: float, sub_scores: dict, event_count: int) -> str:
    """Append a DTI snapshot to mcip_dti_scores; returns the row id."""
    row_id = str(uuid.uuid4())
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO mcip_dti_scores
                (id, score, cable_sub, unsc_sub, backchannel_sub,
                 event_count, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row_id,
                score,
                sub_scores.get("cable_traffic", 0.0),
                sub_scores.get("unsc_schedule", 0.0),
                sub_scores.get("backchannel_metadata", 0.0),
                event_count,
                _now_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:
        log.warning("mcip record_dti_score error: %s", exc)
    finally:
        conn.close()
    return row_id


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_current_dti() -> dict | None:
    """Return the most recent DTI score row, or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM mcip_dti_scores ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
    except Exception as exc:
        log.warning("mcip get_current_dti error: %s", exc)
    finally:
        conn.close()
    return None


def get_dti_history(hours: int = 48) -> list[dict]:
    """Return DTI score rows for the last *hours* window, newest first."""
    since = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM mcip_dti_scores WHERE computed_at >= %s ORDER BY computed_at DESC",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("mcip get_dti_history error: %s", exc)
    finally:
        conn.close()
    return []


def get_recent_events(source_type: str | None = None, limit: int = 50) -> list[dict]:
    """Return recent DAT events, optionally filtered by source_type."""
    conn = _get_conn()
    try:
        if source_type:
            rows = conn.execute(
                "SELECT * FROM mcip_dat_events WHERE source_type = %s ORDER BY ingested_at DESC LIMIT %s",
                (source_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mcip_dat_events ORDER BY ingested_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("mcip get_recent_events error: %s", exc)
    finally:
        conn.close()
    return []


def content_hash(text: str) -> str:
    """Deterministic CRC-32 fingerprint for dedup of cable content."""
    return format(zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF, "08x")
