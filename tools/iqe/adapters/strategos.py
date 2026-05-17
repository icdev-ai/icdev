# CUI // SP-CTI
"""IQE adapter — Strategos Intelligence Canvas.

Registers collections:
  strategos.signals         — prioritized OSINT signals (score, title, source)
  strategos.conflict_events — conflict events (type, actor, aoi, goldstein)
  strategos.leadership_briefs — generated leadership briefings (tier, score, theater)
  strategos.sio_assessments   — SIO Oracle lens assessments
"""
from __future__ import annotations

from tools.db.storage import get_connection


def _signals_adapter(conn=None) -> list[dict]:
    c = conn or get_connection()
    _local = conn is None
    try:
        rows = c.execute(
            "SELECT p.id, p.composite_score, p.is_read, p.promoted_to_kg, "
            "p.annotation, r.title, r.source, r.geo_hint, p.created_at "
            "FROM sg_prioritized_signals p "
            "LEFT JOIN sg_raw_signals r ON r.id = p.raw_signal_id "
            "ORDER BY p.composite_score DESC LIMIT 200"
        ).fetchall()
    except Exception:
        return []
    finally:
        if _local:
            c.close()

    cols = ("id", "composite_score", "is_read", "promoted_to_kg",
            "annotation", "title", "source", "geo_hint", "created_at")
    return [dict(zip(cols, r)) for r in rows]


def _conflict_events_adapter(conn=None) -> list[dict]:
    c = conn or get_connection()
    _local = conn is None
    try:
        rows = c.execute(
            "SELECT id, event_type, actor1, actor2, aoi, goldstein_scale, "
            "avg_tone, lat, lon, source_url, event_ts, created_at "
            "FROM sg_conflict_events ORDER BY event_ts DESC LIMIT 200"
        ).fetchall()
    except Exception:
        return []
    finally:
        if _local:
            c.close()

    cols = ("id", "event_type", "actor1", "actor2", "aoi", "goldstein_scale",
            "avg_tone", "lat", "lon", "source_url", "event_ts", "created_at")
    return [dict(zip(cols, r)) for r in rows]


def _leadership_briefs_adapter(conn=None) -> list[dict]:
    c = conn or get_connection()
    _local = conn is None
    try:
        rows = c.execute(
            "SELECT id, theater, sio_composite_score, iw_triggered, threat_tier, "
            "signal_count_24h, signal_velocity, p_war_posterior, "
            "goldstein_avg, dti_score, generated_at "
            "FROM sg_leadership_briefs ORDER BY generated_at DESC LIMIT 100"
        ).fetchall()
    except Exception:
        return []
    finally:
        if _local:
            c.close()

    cols = ("id", "theater", "sio_composite_score", "iw_triggered", "threat_tier",
            "signal_count_24h", "signal_velocity", "p_war_posterior",
            "goldstein_avg", "dti_score", "generated_at")
    return [dict(zip(cols, r)) for r in rows]


def _sio_assessments_adapter(conn=None) -> list[dict]:
    c = conn or get_connection()
    _local = conn is None
    try:
        rows = c.execute(
            "SELECT id, lens_source, score, confidence, nato_reliability, "
            "narrative, created_at "
            "FROM sg_sio_assessments ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    except Exception:
        return []
    finally:
        if _local:
            c.close()

    cols = ("id", "lens_source", "score", "confidence", "nato_reliability",
            "narrative", "created_at")
    return [dict(zip(cols, r)) for r in rows]


try:
    from tools.iqe.registry import register_collection
    register_collection("strategos.signals",          _signals_adapter)
    register_collection("strategos.conflict_events",  _conflict_events_adapter)
    register_collection("strategos.leadership_briefs", _leadership_briefs_adapter)
    register_collection("strategos.sio_assessments",  _sio_assessments_adapter)
except ImportError:
    pass
