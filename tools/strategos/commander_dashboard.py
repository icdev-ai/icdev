# CUI // SP-CTI
"""Commander's Dashboard — aggregates the J2 battle picture into one view.

Pulls: active CCIR status, latest triggered CCIRs, most recent INTSUM summary,
War Council recommended COA, top threats, signal activity counts.

No LLM call — pure data aggregation for the commander's single-pane view.

Usage:
    from tools.strategos.commander_dashboard import get_commander_picture
    picture = get_commander_picture()
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timedelta, timezone
from typing import Any

logger = get_logger("icdev.strategos.commander_dashboard")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_commander_picture() -> dict[str, Any]:
    """Return a unified battle picture dict for the Commander's Dashboard."""
    from tools.db.storage import get_connection, is_pg

    conn = get_connection()
    ph = "%s" if is_pg() else "?"
    pic: dict[str, Any] = {
        "generated_at": _now_utc(),
        "ccir_summary": {},
        "triggered_ccirs": [],
        "latest_intsum": None,
        "top_threats": [],
        "signal_activity": {},
        "war_council_coa": None,
        "orbat_summary": {},
    }

    try:
        # ── CCIR summary ────────────────────────────────────────────────────
        try:
            rows = conn.execute(
                "SELECT pir_type, status, COUNT(*) AS cnt "
                "FROM sg_pir_requirements GROUP BY pir_type, status"
            ).fetchall()
            active_ccirs = sum(
                (r["cnt"] if isinstance(r, dict) else r[2])
                for r in rows
                if (r["pir_type"] if isinstance(r, dict) else r[0]) == "CCIR"
                and (r["status"] if isinstance(r, dict) else r[1]) == "active"
            )
            active_pirs = sum(
                (r["cnt"] if isinstance(r, dict) else r[2])
                for r in rows
                if (r["pir_type"] if isinstance(r, dict) else r[0]) == "PIR"
                and (r["status"] if isinstance(r, dict) else r[1]) == "active"
            )
            satisfied = sum(
                (r["cnt"] if isinstance(r, dict) else r[2])
                for r in rows
                if (r["status"] if isinstance(r, dict) else r[1]) == "satisfied"
            )
            pic["ccir_summary"] = {
                "active_ccirs": active_ccirs,
                "active_pirs": active_pirs,
                "satisfied": satisfied,
            }
        except Exception as exc:
            logger.debug("ccir_summary failed: %s", exc)
            pic["ccir_summary"] = {"active_ccirs": 0, "active_pirs": 0, "satisfied": 0}

        # ── Triggered CCIRs ─────────────────────────────────────────────────
        try:
            cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            rows = conn.execute(
                f"SELECT e.id, e.ccir_id, e.signal_source, e.match_score, "  # nosec B608
                f"e.created_at, p.topic, p.collection_priority "
                f"FROM sg_ccir_trigger_events e "
                f"LEFT JOIN sg_pir_requirements p ON p.id = e.ccir_id "
                f"WHERE e.resolved = 0 AND e.created_at >= {ph} "
                f"ORDER BY e.created_at DESC LIMIT 5",
                (cutoff_24h,),
            ).fetchall()
            cols = ("id", "ccir_id", "signal_source", "match_score",
                    "created_at", "topic", "priority")
            pic["triggered_ccirs"] = [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.debug("triggered_ccirs failed: %s", exc)

        # ── Latest INTSUM ────────────────────────────────────────────────────
        try:
            row = conn.execute(
                "SELECT id, period_start, period_end, theater, status, created_at "  # nosec B608
                "FROM sg_intsums ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                intsum = dict(zip(("id", "period_start", "period_end",
                                   "theater", "status", "created_at"), row))
                # Grab the Assessment paragraph for the summary
                para = conn.execute(
                    f"SELECT content FROM sg_intsum_paragraphs "  # nosec B608
                    f"WHERE intsum_id = {ph} AND para_num = 5 LIMIT 1",
                    (intsum["id"],),
                ).fetchone()
                intsum["assessment_snippet"] = para[0][:300] if para else ""
                pic["latest_intsum"] = intsum
        except Exception as exc:
            logger.debug("latest_intsum failed: %s", exc)

        # ── Signal activity (24h) ─────────────────────────────────────────
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        activity: dict[str, int] = {}
        for table, ts_col in [
            ("sg_sigint_events", "collected_at"),
            ("sg_eo_signals", "collected_at"),
            ("sg_socmint_signals", "posted_at"),
            ("sg_vessel_tracks", "timestamp"),
        ]:
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {ts_col} >= {ph}",  # nosec B608
                    (cutoff_24h,),
                ).fetchone()
                activity[table.replace("sg_", "").replace("_", " ").title()] = row[0] if row else 0
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
        pic["signal_activity"] = activity

        # ── War Council recommended COA (latest brief) ──────────────────────
        try:
            row = conn.execute(
                "SELECT id, theater, generated_at FROM sg_war_council_briefs "  # nosec B608
                "ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
            if row:
                pic["war_council_coa"] = {
                    "brief_id": row[0],
                    "theater": row[1],
                    "generated_at": row[2],
                }
        except Exception as exc:
            logger.debug("war_council_coa failed: %s", exc)

        # ── ORBAT summary ────────────────────────────────────────────────────
        try:
            rows = conn.execute(
                "SELECT side, COUNT(*) FROM sg_orbat_units GROUP BY side"  # nosec B608
            ).fetchall()
            pic["orbat_summary"] = {r[0]: r[1] for r in rows}
        except Exception as exc:
            logger.debug("orbat_summary failed: %s", exc)

    finally:
        conn.close()

    return pic


def get_war_endurance() -> dict[str, Any]:
    """Return war endurance metrics derived from wargame/ORBAT data."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        endurance: dict[str, Any] = {
            "supply_days": None,
            "attrition_rate_pct": None,
            "force_ratio": None,
            "trend": "unknown",
            "data_source": "wargame",
        }

        # Derive from latest wargame turn if available
        try:
            row = conn.execute(
                "SELECT blue_remaining, red_remaining, blue_losses, red_losses "  # nosec B608
                "FROM sg_wargame_turns ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row and row[0] and row[1]:
                blue_rem, red_rem, blue_loss, red_loss = row
                force_ratio = round(blue_rem / max(red_rem, 1), 2)
                total_start = (blue_rem + (blue_loss or 0))
                attrition = round((blue_loss or 0) / max(total_start, 1) * 100, 1)
                endurance["force_ratio"] = force_ratio
                endurance["attrition_rate_pct"] = attrition
                endurance["supply_days"] = max(1, int(blue_rem / max((blue_loss or 1) / 7, 1)))
                endurance["trend"] = "favourable" if force_ratio >= 1.5 else (
                    "neutral" if force_ratio >= 0.8 else "adverse"
                )
        except Exception as exc:
            logger.debug("wargame endurance failed: %s", exc)

        return endurance
    finally:
        conn.close()
