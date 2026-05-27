#!/usr/bin/env python3
# CUI // SP-CTI
"""Diplomatic Activity Tracker (DAT) — DTI computation engine.

Ingests three signal streams and produces a Diplomatic Tension Index (DTI)
score in [0, 100] refreshed on a 6-hour cadence:

  1. State Department cable traffic  (sg_dat_cables)
  2. UNSC meeting schedules          (sg_dat_unsc_events)
  3. Back-channel communication meta (sg_dat_backchannel)

DTI scoring weights:
  Cable tension signals   40 %
  UNSC meeting stress     30 %
  Back-channel patterns   30 %

Usage:
    from tools.strategos.dat import (
        ensure_tables, ingest_cable, ingest_unsc_event,
        ingest_backchannel, compute_dti, refresh_dti,
        get_latest_dti, get_dti_history,
        get_cables, get_unsc_events, get_backchannels,
    )
    refresh_dti("global")
    snap = get_latest_dti("global")
    print(snap["dti_score"])
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = get_logger("icdev.strategos.dat")

REFRESH_HOURS = 6  # target cadence
CABLE_WEIGHT = 0.40
UNSC_WEIGHT = 0.30
BACKCHANNEL_WEIGHT = 0.30
WINDOW_DAYS = 30  # lookback window for scoring


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _cutoff(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _is_pg(conn) -> bool:
    try:
        from tools.db.storage import is_pg
        return is_pg()
    except Exception:
        return False


def _ph(conn) -> str:
    return "%s" if _is_pg(conn) else "?"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sg_dat_cables (
    id            TEXT PRIMARY KEY,
    theater_id    TEXT NOT NULL DEFAULT 'global',
    cable_ref     TEXT,
    origin        TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    subject       TEXT,
    tension_level TEXT NOT NULL DEFAULT 'low',
    keywords_json TEXT,
    summary       TEXT,
    received_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sg_dat_unsc_events (
    id            TEXT PRIMARY KEY,
    theater_id    TEXT NOT NULL DEFAULT 'global',
    event_type    TEXT NOT NULL DEFAULT 'scheduled',
    topic         TEXT,
    agenda_item   TEXT,
    emergency     INTEGER NOT NULL DEFAULT 0,
    veto_cast     INTEGER NOT NULL DEFAULT 0,
    walkout       INTEGER NOT NULL DEFAULT 0,
    participating_states_json TEXT,
    scheduled_at  TEXT,
    outcome       TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sg_dat_backchannel (
    id            TEXT PRIMARY KEY,
    theater_id    TEXT NOT NULL DEFAULT 'global',
    channel_type  TEXT NOT NULL DEFAULT 'diplomatic',
    parties_json  TEXT,
    frequency_delta REAL NOT NULL DEFAULT 0.0,
    escalation_flag INTEGER NOT NULL DEFAULT 0,
    communication_breakdown INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT,
    observed_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sg_dat_dti_snapshots (
    id              TEXT PRIMARY KEY,
    theater_id      TEXT NOT NULL DEFAULT 'global',
    dti_score       REAL NOT NULL,
    cable_score     REAL NOT NULL DEFAULT 0.0,
    unsc_score      REAL NOT NULL DEFAULT 0.0,
    backchannel_score REAL NOT NULL DEFAULT 0.0,
    cable_count     INTEGER NOT NULL DEFAULT 0,
    unsc_count      INTEGER NOT NULL DEFAULT 0,
    backchannel_count INTEGER NOT NULL DEFAULT 0,
    detail_json     TEXT,
    computed_at     TEXT NOT NULL
);
"""


def ensure_tables() -> None:
    conn = _conn()
    try:
        for stmt in SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()


# ── Ingest ────────────────────────────────────────────────────────────────────

TENSION_LEVELS = ("low", "medium", "high", "critical")


def ingest_cable(
    *,
    theater_id: str = "global",
    cable_ref: str | None = None,
    origin: str | None = None,
    subject: str | None = None,
    tension_level: str = "low",
    keywords: list[str] | None = None,
    summary: str | None = None,
    received_at: str | None = None,
    classification: str = "CUI",
) -> dict[str, Any]:
    """Insert a State Department cable traffic record."""
    ensure_tables()
    if tension_level not in TENSION_LEVELS:
        tension_level = "low"
    now = _now_iso()
    row_id = str(uuid.uuid4())
    conn = _conn()
    ph = _ph(conn)
    try:
        conn.execute(
            f"INSERT INTO sg_dat_cables "
            f"(id, theater_id, cable_ref, origin, classification, subject, "
            f"tension_level, keywords_json, summary, received_at, created_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                row_id, theater_id, cable_ref, origin, classification, subject,
                tension_level, json.dumps(keywords or []), summary,
                received_at or now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": row_id, "theater_id": theater_id, "tension_level": tension_level}


def ingest_unsc_event(
    *,
    theater_id: str = "global",
    event_type: str = "scheduled",
    topic: str | None = None,
    agenda_item: str | None = None,
    emergency: bool = False,
    veto_cast: bool = False,
    walkout: bool = False,
    participating_states: list[str] | None = None,
    scheduled_at: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Insert a UNSC meeting schedule/event record."""
    ensure_tables()
    now = _now_iso()
    row_id = str(uuid.uuid4())
    conn = _conn()
    ph = _ph(conn)
    try:
        conn.execute(
            f"INSERT INTO sg_dat_unsc_events "
            f"(id, theater_id, event_type, topic, agenda_item, emergency, veto_cast, "
            f"walkout, participating_states_json, scheduled_at, outcome, created_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                row_id, theater_id, event_type, topic, agenda_item,
                1 if emergency else 0, 1 if veto_cast else 0,
                1 if walkout else 0, json.dumps(participating_states or []),
                scheduled_at or now, outcome, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": row_id, "theater_id": theater_id, "emergency": emergency}


def ingest_backchannel(
    *,
    theater_id: str = "global",
    channel_type: str = "diplomatic",
    parties: list[str] | None = None,
    frequency_delta: float = 0.0,
    escalation_flag: bool = False,
    communication_breakdown: bool = False,
    metadata: dict | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Insert a back-channel communication metadata record."""
    ensure_tables()
    now = _now_iso()
    row_id = str(uuid.uuid4())
    conn = _conn()
    ph = _ph(conn)
    try:
        conn.execute(
            f"INSERT INTO sg_dat_backchannel "
            f"(id, theater_id, channel_type, parties_json, frequency_delta, "
            f"escalation_flag, communication_breakdown, metadata_json, "
            f"observed_at, created_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                row_id, theater_id, channel_type, json.dumps(parties or []),
                float(frequency_delta),
                1 if escalation_flag else 0,
                1 if communication_breakdown else 0,
                json.dumps(metadata or {}), observed_at or now, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": row_id, "theater_id": theater_id, "escalation_flag": escalation_flag}


# ── DTI Computation ───────────────────────────────────────────────────────────

_TENSION_WEIGHTS = {"low": 0.1, "medium": 0.4, "high": 0.75, "critical": 1.0}


def _score_cables(conn, theater_id: str, ph: str, cutoff: str) -> dict[str, Any]:
    try:
        rows = conn.execute(
            f"SELECT tension_level, received_at FROM sg_dat_cables "
            f"WHERE theater_id = {ph} AND received_at >= {ph} "
            f"ORDER BY received_at DESC",
            (theater_id, cutoff),
        ).fetchall()
    except Exception:
        return {"score": 0.0, "count": 0}

    if not rows:
        return {"score": 0.0, "count": 0}

    now = _now()
    total_weight = 0.0
    weighted_tension = 0.0
    for tension_level, received_at in rows:
        try:
            ts = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (now - ts).total_seconds() / 86400
            decay = math.exp(-0.05 * age_days)
        except Exception:
            decay = 0.5
        w = _TENSION_WEIGHTS.get(str(tension_level).lower(), 0.1)
        weighted_tension += w * decay
        total_weight += decay

    raw = weighted_tension / total_weight if total_weight > 0 else 0.0
    critical_count = sum(1 for r in rows if str(r[0]).lower() == "critical")
    high_count = sum(1 for r in rows if str(r[0]).lower() == "high")

    return {
        "score": _clamp(raw * 100),
        "count": len(rows),
        "critical_cables": critical_count,
        "high_cables": high_count,
    }


def _score_unsc(conn, theater_id: str, ph: str, cutoff: str) -> dict[str, Any]:
    try:
        rows = conn.execute(
            f"SELECT emergency, veto_cast, walkout, created_at "
            f"FROM sg_dat_unsc_events "
            f"WHERE theater_id = {ph} AND created_at >= {ph}",
            (theater_id, cutoff),
        ).fetchall()
    except Exception:
        return {"score": 0.0, "count": 0}

    if not rows:
        return {"score": 0.0, "count": 0}

    emergency_count = sum(1 for r in rows if int(r[0] or 0))
    veto_count = sum(1 for r in rows if int(r[1] or 0))
    walkout_count = sum(1 for r in rows if int(r[2] or 0))

    # Each emergency session = +25 pts, veto = +20, walkout = +15
    raw = (emergency_count * 25 + veto_count * 20 + walkout_count * 15) / max(len(rows), 1)
    # Cap contribution per signal density
    density_bonus = min(10.0, len(rows) * 0.5)

    return {
        "score": _clamp(raw + density_bonus),
        "count": len(rows),
        "emergency_sessions": emergency_count,
        "vetoes": veto_count,
        "walkouts": walkout_count,
    }


def _score_backchannel(conn, theater_id: str, ph: str, cutoff: str) -> dict[str, Any]:
    try:
        rows = conn.execute(
            f"SELECT frequency_delta, escalation_flag, communication_breakdown, created_at "
            f"FROM sg_dat_backchannel "
            f"WHERE theater_id = {ph} AND created_at >= {ph}",
            (theater_id, cutoff),
        ).fetchall()
    except Exception:
        return {"score": 0.0, "count": 0}

    if not rows:
        return {"score": 0.0, "count": 0}

    escalations = sum(1 for r in rows if int(r[1] or 0))
    breakdowns = sum(1 for r in rows if int(r[2] or 0))
    neg_freq = sum(1 for r in rows if float(r[0] or 0) < -0.2)

    total = len(rows)
    esc_rate = escalations / total if total else 0.0
    brk_rate = breakdowns / total if total else 0.0
    freq_rate = neg_freq / total if total else 0.0

    raw = esc_rate * 50 + brk_rate * 35 + freq_rate * 15

    return {
        "score": _clamp(raw),
        "count": total,
        "escalations": escalations,
        "breakdowns": breakdowns,
        "negative_frequency": neg_freq,
    }


def compute_dti(theater_id: str = "global", window_days: int = WINDOW_DAYS) -> dict[str, Any]:
    """Compute DTI score for a theater without persisting."""
    ensure_tables()
    conn = _conn()
    ph = _ph(conn)
    cutoff = _cutoff(window_days)
    try:
        cable_detail = _score_cables(conn, theater_id, ph, cutoff)
        unsc_detail = _score_unsc(conn, theater_id, ph, cutoff)
        bc_detail = _score_backchannel(conn, theater_id, ph, cutoff)
    finally:
        conn.close()

    cable_score = cable_detail["score"]
    unsc_score = unsc_detail["score"]
    bc_score = bc_detail["score"]

    dti = _clamp(
        cable_score * CABLE_WEIGHT
        + unsc_score * UNSC_WEIGHT
        + bc_score * BACKCHANNEL_WEIGHT
    )

    return {
        "theater_id": theater_id,
        "dti_score": round(dti, 2),
        "cable_score": round(cable_score, 2),
        "unsc_score": round(unsc_score, 2),
        "backchannel_score": round(bc_score, 2),
        "cable_count": cable_detail["count"],
        "unsc_count": unsc_detail["count"],
        "backchannel_count": bc_detail["count"],
        "cable_detail": cable_detail,
        "unsc_detail": unsc_detail,
        "backchannel_detail": bc_detail,
        "window_days": window_days,
        "computed_at": _now_iso(),
    }


def refresh_dti(theater_id: str = "global") -> dict[str, Any]:
    """Compute and persist a DTI snapshot. Returns the snapshot dict."""
    result = compute_dti(theater_id)
    conn = _conn()
    ph = _ph(conn)
    snap_id = str(uuid.uuid4())
    try:
        conn.execute(
            f"INSERT INTO sg_dat_dti_snapshots "
            f"(id, theater_id, dti_score, cable_score, unsc_score, backchannel_score, "
            f"cable_count, unsc_count, backchannel_count, detail_json, computed_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                snap_id, theater_id,
                result["dti_score"], result["cable_score"],
                result["unsc_score"], result["backchannel_score"],
                result["cable_count"], result["unsc_count"], result["backchannel_count"],
                json.dumps({
                    "cable_detail": result["cable_detail"],
                    "unsc_detail": result["unsc_detail"],
                    "backchannel_detail": result["backchannel_detail"],
                }),
                result["computed_at"],
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("DTI snapshot persist failed: %s", exc)
    finally:
        conn.close()
    result["snapshot_id"] = snap_id
    return result


def get_latest_dti(theater_id: str = "global") -> dict[str, Any] | None:
    """Return the most recent persisted DTI snapshot."""
    ensure_tables()
    conn = _conn()
    ph = _ph(conn)
    try:
        row = conn.execute(
            f"SELECT id, theater_id, dti_score, cable_score, unsc_score, "
            f"backchannel_score, cable_count, unsc_count, backchannel_count, "
            f"detail_json, computed_at "
            f"FROM sg_dat_dti_snapshots "
            f"WHERE theater_id = {ph} "
            f"ORDER BY computed_at DESC LIMIT 1",
            (theater_id,),
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()

    if not row:
        return None
    keys = [
        "id", "theater_id", "dti_score", "cable_score", "unsc_score",
        "backchannel_score", "cable_count", "unsc_count", "backchannel_count",
        "detail_json", "computed_at",
    ]
    snap = dict(zip(keys, row)) if not isinstance(row, dict) else row
    try:
        snap["detail"] = json.loads(snap.get("detail_json") or "{}")
    except Exception:
        snap["detail"] = {}
    return snap


def get_dti_history(theater_id: str = "global", limit: int = 48) -> list[dict]:
    """Return DTI snapshot history (newest first)."""
    ensure_tables()
    conn = _conn()
    ph = _ph(conn)
    try:
        rows = conn.execute(
            f"SELECT dti_score, cable_score, unsc_score, backchannel_score, computed_at "
            f"FROM sg_dat_dti_snapshots "
            f"WHERE theater_id = {ph} "
            f"ORDER BY computed_at DESC LIMIT {int(limit)}",
            (theater_id,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    keys = ["dti_score", "cable_score", "unsc_score", "backchannel_score", "computed_at"]
    return [
        dict(zip(keys, r)) if not isinstance(r, dict) else r
        for r in rows
    ]


def get_cables(theater_id: str = "global", limit: int = 50) -> list[dict]:
    """Return most recent cable records."""
    ensure_tables()
    conn = _conn()
    ph = _ph(conn)
    try:
        rows = conn.execute(
            f"SELECT id, cable_ref, origin, subject, tension_level, summary, received_at "
            f"FROM sg_dat_cables WHERE theater_id = {ph} "
            f"ORDER BY received_at DESC LIMIT {int(limit)}",
            (theater_id,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    keys = ["id", "cable_ref", "origin", "subject", "tension_level", "summary", "received_at"]
    return [
        dict(zip(keys, r)) if not isinstance(r, dict) else r
        for r in rows
    ]


def get_unsc_events(theater_id: str = "global", limit: int = 30) -> list[dict]:
    """Return most recent UNSC events."""
    ensure_tables()
    conn = _conn()
    ph = _ph(conn)
    try:
        rows = conn.execute(
            f"SELECT id, event_type, topic, emergency, veto_cast, walkout, scheduled_at, outcome "
            f"FROM sg_dat_unsc_events WHERE theater_id = {ph} "
            f"ORDER BY scheduled_at DESC LIMIT {int(limit)}",
            (theater_id,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    keys = ["id", "event_type", "topic", "emergency", "veto_cast", "walkout", "scheduled_at", "outcome"]
    return [
        dict(zip(keys, r)) if not isinstance(r, dict) else r
        for r in rows
    ]


def get_backchannels(theater_id: str = "global", limit: int = 30) -> list[dict]:
    """Return most recent back-channel records."""
    ensure_tables()
    conn = _conn()
    ph = _ph(conn)
    try:
        rows = conn.execute(
            f"SELECT id, channel_type, parties_json, frequency_delta, "
            f"escalation_flag, communication_breakdown, observed_at "
            f"FROM sg_dat_backchannel WHERE theater_id = {ph} "
            f"ORDER BY observed_at DESC LIMIT {int(limit)}",
            (theater_id,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    keys = [
        "id", "channel_type", "parties_json", "frequency_delta",
        "escalation_flag", "communication_breakdown", "observed_at",
    ]
    result = []
    for r in rows:
        d = dict(zip(keys, r)) if not isinstance(r, dict) else r
        try:
            d["parties"] = json.loads(d.get("parties_json") or "[]")
        except Exception:
            d["parties"] = []
        result.append(d)
    return result
