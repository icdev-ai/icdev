
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Quality Design Canvas — Cross-canvas agent hooks.

Called by other canvases when designs are saved.
Re-assesses quality gates and updates cross-canvas links.
"""

from tools.db.storage import get_connection
from datetime import datetime, timezone

logger = get_logger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _update_cross_canvas_link(design_id, source_canvas, source_design_id, score):
    """Update or insert a cross-canvas quality link."""
    try:
        from tools.qdc_canvas.db.init_db import get_connection

        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT id FROM qdc_cross_canvas_links WHERE design_id = ? AND source_canvas = ?",
                (design_id, source_canvas),
            ).fetchone()
            now = _now()
            if existing:
                conn.execute(
                    "UPDATE qdc_cross_canvas_links "
                    "SET source_design_id = ?, quality_score = ?, last_synced = ? "
                    "WHERE id = ?",
                    (source_design_id, score, now, existing[0]),
                )
            else:
                import uuid

                conn.execute(
                    "INSERT INTO qdc_cross_canvas_links "
                    "(id, design_id, source_canvas, source_design_id, "
                    "quality_score, last_synced) VALUES (?,?,?,?,?,?)",
                    (
                        f"xcl-{uuid.uuid4().hex[:10]}",
                        design_id,
                        source_canvas,
                        source_design_id,
                        score,
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Cross-canvas link update failed: %s", exc)


def _get_latest_qdc_design_for_project(source_canvas):
    """Find the most recent QDC design that links to this canvas."""
    try:
        from tools.qdc_canvas.db.init_db import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT design_id FROM qdc_cross_canvas_links "
                "WHERE source_canvas = ? ORDER BY last_synced DESC LIMIT 1",
                (source_canvas,),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _get_canvas_assessment_score(canvas_key, design_id):
    """Fetch latest assessment score from a source canvas."""
    table_map = {
        "idc": ("infra_canvas", "idc_assessments"),
        "sdc": ("security_canvas", "sc_assessments"),
        "bdc": ("boundary_canvas", "bdc_assessments"),
        "pdc": ("pipeline_canvas", "pdc_assessments"),
        "odc": ("observability_canvas", "odc_assessments"),
        "ddc": ("data_canvas", "ddc_assessments"),
        "ndc": ("network_canvas", "ndc_assessments"),
        "mdc": ("migration_canvas", "mc_assessments"),
    }
    info = table_map.get(canvas_key)
    if not info:
        return 0.0
    db_name, table = info
    try:
        from pathlib import Path

        db_path = Path(__file__).resolve().parents[2] / "data" / f"{db_name}.db"
        if not db_path.exists():
            return 0.0
        conn = get_connection(str(db_path))
        try:
            row = conn.execute(
                f"SELECT score FROM [{table}] "  # noqa: S608  # nosec B608
                f"WHERE design_id = ? ORDER BY created_at DESC LIMIT 1",
                (design_id,),
            ).fetchone()
            return float(row[0]) if row else 0.0
        finally:
            conn.close()
    except Exception:
        return 0.0


def _on_canvas_saved(canvas_key, design_id):
    """Generic handler for any canvas design save event."""
    qdc_design_id = _get_latest_qdc_design_for_project(canvas_key)
    if not qdc_design_id:
        return
    score = _get_canvas_assessment_score(canvas_key, design_id)
    _update_cross_canvas_link(qdc_design_id, canvas_key, design_id, score)
    logger.info(
        "QDC cross-canvas update: %s design %s → score %.1f",
        canvas_key,
        design_id,
        score,
    )


# ── Public hooks (called from other canvas blueprints) ───────────────────────


def on_idc_design_saved(design_id):
    """Triggered when an Infrastructure Design Canvas design is saved."""
    _on_canvas_saved("idc", design_id)


def on_ndc_design_saved(design_id):
    """Triggered when a Network Design Canvas design is saved."""
    _on_canvas_saved("ndc", design_id)


def on_sdc_design_saved(design_id):
    """Triggered when a Security Design Canvas design is saved."""
    _on_canvas_saved("sdc", design_id)


def on_bdc_design_saved(design_id):
    """Triggered when a Boundary Design Canvas design is saved."""
    _on_canvas_saved("bdc", design_id)


def on_pdc_design_saved(design_id):
    """Triggered when a Pipeline Design Canvas design is saved."""
    _on_canvas_saved("pdc", design_id)


def on_odc_design_saved(design_id):
    """Triggered when an Observability Design Canvas design is saved."""
    _on_canvas_saved("odc", design_id)


def on_ddc_design_saved(design_id):
    """Triggered when a Data Design Canvas design is saved."""
    _on_canvas_saved("ddc", design_id)


def on_mdc_design_saved(design_id):
    """Triggered when a Migration Design Canvas design is saved."""
    _on_canvas_saved("mdc", design_id)
