# CUI // SP-CTI
"""NDC (Network Design Canvas) cross-canvas event bus subscriber.

The Network Design Canvas historically only *emitted* bus events (see
``tools/network/event_emitter.py``); it had no reactive listener, so it never
responded to events published by other canvases. This module closes that gap by
registering in-process handlers over ``tools/canvas/event_bus.py``.

Subscribes to (keyed on the producing/source canvas id):

  * ``sdc`` / ``sdc.assessment.completed`` — the Security Design Canvas finished
    assessing an NDC topology. Closes the NDC->SDC loop by recording the
    assessment result (risk score, posture grade, CAT1 count) back on the NDC
    side in ``nc_sdc_assessments`` so the topology row can surface its latest
    security posture.

  * ``sdc`` / ``sdc.cve.published`` — the threat pipeline (cyber_feed_refresh
    reflex / CISA KEV importer) published new CVE / vulnerability data. Marks the
    vulnerability overlays of affected topologies stale (``nc_vuln_overlay_stale``)
    so the canvas re-runs matching / triage on next view. When the event carries
    an explicit ``cve_ids`` list only the topologies whose scans reference those
    CVEs are marked; otherwise every topology with an existing vuln scan is
    marked stale (broad refresh).

Design rules (mirrors ``tools/security_canvas/bus_subscriber.py``):
  * Handlers are small, idempotent (PK upserts) and fully exception-guarded — a
    malformed or hostile event must never crash the bus.
  * Lightweight status tables are created on demand (``CREATE TABLE IF NOT
    EXISTS``) so no migration is required and missing tables degrade gracefully.
  * All canvas DB writes go through ``tools.network.db.init_db.get_connection``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection

logger = get_logger("icdev.network.bus_subscriber")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Lightweight status tables (created on demand — no migration required)
# ---------------------------------------------------------------------------
_SDC_ASSESSMENT_DDL = """
CREATE TABLE IF NOT EXISTS nc_sdc_assessments (
    topology_id   TEXT PRIMARY KEY,
    design_id     TEXT,
    assessment_id TEXT,
    risk_score    REAL DEFAULT 0,
    posture_grade TEXT DEFAULT '',
    cat1_count    INTEGER DEFAULT 0,
    updated_at    TEXT
)
"""

_VULN_STALE_DDL = """
CREATE TABLE IF NOT EXISTS nc_vuln_overlay_stale (
    topology_id TEXT PRIMARY KEY,
    is_stale    INTEGER DEFAULT 1,
    reason      TEXT DEFAULT '',
    cve_ids     TEXT DEFAULT '[]',
    marked_at   TEXT
)
"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _on_sdc_assessment_completed(
    event_id: str, source_canvas: str, event_type: str, payload: dict
) -> None:
    """Record an SDC assessment result on the NDC side (idempotent upsert)."""
    try:
        payload = payload or {}
        topology_id = payload.get("topology_id") or ""
        if not topology_id:
            logger.debug(
                "NDC bus: sdc.assessment.completed missing topology_id (event %s) — ignored",
                event_id,
            )
            return

        def _num(val, default=0):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        conn = get_connection()
        try:
            conn.execute(_SDC_ASSESSMENT_DDL)
            conn.execute(
                """
                INSERT INTO nc_sdc_assessments
                    (topology_id, design_id, assessment_id, risk_score,
                     posture_grade, cat1_count, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(topology_id) DO UPDATE SET
                    design_id     = excluded.design_id,
                    assessment_id = excluded.assessment_id,
                    risk_score    = excluded.risk_score,
                    posture_grade = excluded.posture_grade,
                    cat1_count    = excluded.cat1_count,
                    updated_at    = excluded.updated_at
                """,
                (
                    topology_id,
                    payload.get("design_id") or "",
                    payload.get("assessment_id") or "",
                    _num(payload.get("risk_score"), 0),
                    str(payload.get("posture_grade") or ""),
                    int(_num(payload.get("cat1_count"), 0)),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "NDC bus: recorded SDC assessment for topology %s (grade=%s, score=%s)",
            topology_id,
            payload.get("posture_grade"),
            payload.get("risk_score"),
        )
    except Exception as exc:  # noqa: BLE001 — a bad event must never crash the bus
        logger.warning("NDC bus: sdc.assessment.completed handler failed: %s", exc)


def _on_cve_published(
    event_id: str, source_canvas: str, event_type: str, payload: dict
) -> None:
    """Mark affected topologies' vuln overlays stale on a CVE/threat update."""
    try:
        payload = payload or {}
        raw_ids = payload.get("cve_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        cve_ids = [str(c).strip() for c in raw_ids if str(c).strip()]

        conn = get_connection()
        try:
            conn.execute(_VULN_STALE_DDL)

            # Resolve the set of affected topologies.
            topo_ids: list[str] = []
            try:
                if cve_ids:
                    clauses = " OR ".join(["f.cve LIKE %s"] * len(cve_ids))
                    params = [f"%{c}%" for c in cve_ids]
                    rows = conn.execute(
                        "SELECT DISTINCT s.topology_id "
                        "FROM nc_vuln_findings f "
                        "JOIN nc_vuln_scans s ON f.scan_id = s.id "
                        f"WHERE s.topology_id IS NOT NULL AND ({clauses})",
                        params,
                    ).fetchall()
                else:
                    # Broad refresh — every topology that has a vuln scan.
                    rows = conn.execute(
                        "SELECT DISTINCT topology_id FROM nc_vuln_scans "
                        "WHERE topology_id IS NOT NULL"
                    ).fetchall()
                for r in rows:
                    tid = r[0] if isinstance(r, (list, tuple)) else r["topology_id"]
                    if tid:
                        topo_ids.append(tid)
            except Exception as exc:  # noqa: BLE001 — vuln tables may be absent
                logger.debug(
                    "NDC bus: vuln scan lookup skipped (tables absent?): %s", exc
                )
                topo_ids = []

            import json as _json

            reason = (
                f"CVE update: {len(cve_ids)} CVE(s)"
                if cve_ids
                else "CVE feed refresh (broad)"
            )
            cve_json = _json.dumps(cve_ids)
            now = _now()
            for tid in topo_ids:
                conn.execute(
                    """
                    INSERT INTO nc_vuln_overlay_stale
                        (topology_id, is_stale, reason, cve_ids, marked_at)
                    VALUES (%s, 1, %s, %s, %s)
                    ON CONFLICT(topology_id) DO UPDATE SET
                        is_stale  = 1,
                        reason    = excluded.reason,
                        cve_ids   = excluded.cve_ids,
                        marked_at = excluded.marked_at
                    """,
                    (tid, reason, cve_json, now),
                )
            conn.commit()
        finally:
            conn.close()

        if topo_ids:
            logger.info(
                "NDC bus: marked vuln overlay stale for %d topology(ies) (%s)",
                len(topo_ids),
                reason,
            )
        else:
            logger.debug(
                "NDC bus: sdc.cve.published — no affected topologies (event %s)",
                event_id,
            )
    except Exception as exc:  # noqa: BLE001 — a bad event must never crash the bus
        logger.warning("NDC bus: sdc.cve.published handler failed: %s", exc)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register() -> None:
    """Register NDC handlers with the canvas event bus.

    Called once from ``create_network_blueprint()`` — the same init path the
    Security Design Canvas uses for its own subscriber.
    """
    from tools.canvas.event_bus import subscribe

    subscribe("sdc", "sdc.assessment.completed", _on_sdc_assessment_completed)
    subscribe("sdc", "sdc.cve.published", _on_cve_published)
    logger.debug(
        "NDC bus_subscriber: registered handlers for "
        "sdc/sdc.assessment.completed and sdc/sdc.cve.published"
    )
