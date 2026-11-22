# CUI // SP-CTI
"""IQE Agentic AI Design Canvas collection adapters.

Importing this module registers three collections on the module-level Executor:
  aadc.designs     — Agentic AI designs (aadc_designs); filter by
                     classification, domain, autonomy_max, hitl_required.
  aadc.assessments — Assessment results (aadc_assessments); filter by
                     design_id, score, omb_compliant, safety_impacting.
  aadc.artifacts   — Generated ATO/compliance artifacts (aadc_artifacts);
                     filter by artifact_type, design_id.
  aadc.lifecycle_states — Lifecycle transitions (aadc_lifecycle_states) joined
                     to their design: design_name, design_domain.
  aadc.ato_readiness — Each design joined to its ATO reports and scorecard
                     snapshots (aadc_ato_reports, aadc_scorecard_snapshots).

The two joined views exist because IQE's ``join()`` built-in joins on one
key name shared by both sides, and these joins pair ``design_id`` with the
design's ``id`` (and ato_readiness is three-way).
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection
from tools.logging.icdev_logger import get_logger

_log = get_logger(__name__)


def _aadc_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.agentic_ai_canvas.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def designs_adapter(conn: Any) -> list[dict]:
    """Return rows from aadc_designs."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, name, description, domain, classification, "
            "autonomy_max, safety_impacting, rights_impacting, hitl_required, "
            "created_at, updated_at FROM aadc_designs"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        _log.warning("iqe aadc.designs adapter failed: %s", exc)
        return []
    finally:
        if owned:
            c.close()


def assessments_adapter(conn: Any) -> list[dict]:
    """Return rows from aadc_assessments enriched with design name."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT a.id, a.design_id, d.name AS design_name, "
            "a.score, a.nist_rmf_score, a.owasp_score, a.omb_compliant, "
            "a.autonomy_max, a.safety_impacting, a.rights_impacting, a.created_at "
            "FROM aadc_assessments a "
            "LEFT JOIN aadc_designs d ON d.id = a.design_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        _log.warning("iqe aadc.assessments adapter failed: %s", exc)
        return []
    finally:
        if owned:
            c.close()


def artifacts_adapter(conn: Any) -> list[dict]:
    """Return rows from aadc_artifacts."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, artifact_type, title, created_at "
            "FROM aadc_artifacts"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        _log.warning("iqe aadc.artifacts adapter failed: %s", exc)
        return []
    finally:
        if owned:
            c.close()


def ai_decisions_adapter(conn: Any) -> list[dict]:  # noqa: ARG001
    """Return AI decision records for AADC from canvas_ai_decisions (main icdev.db)."""
    try:
        from tools.db.storage import get_connection as _main_conn  # noqa: PLC0415
        with _main_conn() as _c:
            cur = _c.execute(
                "SELECT * FROM canvas_ai_decisions WHERE canvas_type='aadc' ORDER BY created_at DESC"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        _log.warning("iqe aadc.ai_decisions adapter failed: %s", exc)
        return []


def twin_snapshots_adapter(conn: Any) -> list[dict]:
    """Return AADC twin snapshots (aadc_twin_snapshots) — the digital-twin surface."""
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, design_id, label, node_count, edge_count, created_by, created_at "
            "FROM aadc_twin_snapshots ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


def _fetch_rows(conn: Any, sql: str, name: str) -> list[dict]:
    c, owned = _aadc_conn(conn)
    try:
        cur = c.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 -- a canvas DB without the table (sqlite or PG error) answers no rows, like the adapters above
        _log.warning("iqe %s adapter failed: %s", name, exc)
        return []
    finally:
        if owned:
            c.close()


def lifecycle_states_adapter(conn: Any) -> list[dict]:
    """Return lifecycle transitions with the design they belong to (inner join)."""
    return _fetch_rows(
        conn,
        "SELECT l.id, l.design_id, d.name AS design_name, d.domain AS design_domain, "
        "l.from_state, l.to_state, l.actor, l.reason, l.created_at "
        "FROM aadc_lifecycle_states l "
        "JOIN aadc_designs d ON d.id = l.design_id",
        "aadc.lifecycle_states",
    )


def ato_readiness_adapter(conn: Any) -> list[dict]:
    """Return each design joined to its ATO reports and scorecard snapshots (inner joins)."""
    return _fetch_rows(
        conn,
        "SELECT d.id AS design_id, d.name AS design_name, d.domain AS design_domain, "
        "a.ato_ready, a.score_pct, a.passed, a.failed, a.critical_failed, "
        "s.overall_score, s.health "
        "FROM aadc_designs d "
        "JOIN aadc_ato_reports a ON a.design_id = d.id "
        "JOIN aadc_scorecard_snapshots s ON s.design_id = d.id",
        "aadc.ato_readiness",
    )


register_collection("aadc.designs", designs_adapter)
register_collection("aadc.assessments", assessments_adapter)
register_collection("aadc.artifacts", artifacts_adapter)
register_collection("aadc.ai_decisions", ai_decisions_adapter)
register_collection("aadc.twin_snapshots", twin_snapshots_adapter)
register_collection("aadc.lifecycle_states", lifecycle_states_adapter)
register_collection("aadc.ato_readiness", ato_readiness_adapter)
