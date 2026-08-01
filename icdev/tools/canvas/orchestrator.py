# CUI // SP-CTI
"""Cross-canvas integration engine.

Links all 9 design canvases (IDC, NDC, SDC, BDC, PDC, ODC, DDC, QDC, MDC)
via the ``canvas_projects`` entity in icdev.db.  Provides CRUD for projects,
canvas linking/unlinking, compliance aggregation, and readiness scoring.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.db.storage import column_exists, get_connection, list_tables, table_exists

logger = get_logger("icdev.canvas.orchestrator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CANVAS_KEYS = {"idc", "ndc", "sdc", "bdc", "pdc", "odc", "ddc", "qdc", "mdc", "aadc"}

# Canvas key -> (db filename, assessment table, score column)
CANVAS_DB_MAP: dict[str, tuple[str, str, str]] = {
    "idc": ("infra_canvas.db", "idc_assessments", "score"),
    "ndc": ("network_canvas.db", "ndc_assessments", "score"),
    "sdc": ("security_canvas.db", "sc_assessments", "risk_score"),
    "bdc": ("boundary_canvas.db", "bdc_assessments", "score"),
    "pdc": ("pipeline_canvas.db", "pdc_assessments", "score"),
    "odc": ("observability_canvas.db", "odc_assessments", "score"),
    "ddc": ("data_canvas.db", "ddc_assessments", "score"),
    "qdc": ("qdc_canvas.db", "qdc_assessments", "score"),
    "mdc": ("migration_canvas.db", "mc_assessments", "score"),
    "aadc": ("agentic_ai_canvas.db", "aadc_assessments", "score"),
}

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

GRADE_THRESHOLDS = [(90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")]


def _score_to_grade(score: float | None) -> str | None:
    """Convert a numeric score to a letter grade."""
    if score is None:
        return None
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | tuple, columns: list[str]) -> dict:
    """Convert a DB row to a dict, parsing links_json."""
    d = dict(zip(columns, row))
    raw = d.get("links_json", "{}")
    try:
        d["links"] = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        d["links"] = {}
    return d


_PROJECT_COLUMNS = [
    "id", "name", "description", "classification",
    "links_json", "created_at", "updated_at",
]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_project(
    name: str,
    description: str = "",
    classification: str = "CUI",
) -> dict:
    """Create a new canvas project and return it as a dict."""
    project_id = f"cp-{uuid.uuid4()}"
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO canvas_projects
               (id, name, description, classification, links_json, created_at, updated_at)
               VALUES (%s, %s, %s, %s, '{}', %s, %s)""",
            (project_id, name, description, classification, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Created canvas project %s (%s)", project_id, name)
    return get_project(project_id)  # type: ignore[return-value]


def get_project(project_id: str) -> dict | None:
    """Return a single project with parsed links, or None."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, name, description, classification, links_json, "
            "created_at, updated_at FROM canvas_projects WHERE id = %s",
            (project_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return _row_to_dict(row, _PROJECT_COLUMNS)


def list_projects() -> list[dict]:
    """Return all canvas projects sorted by updated_at DESC."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT id, name, description, classification, links_json, "
            "created_at, updated_at FROM canvas_projects ORDER BY updated_at DESC",
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r, _PROJECT_COLUMNS) for r in rows]


def update_project(project_id: str, **kwargs: Any) -> dict:
    """Update mutable fields (name, description, classification)."""
    allowed = {"name", "description", "classification"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_project(project_id)  # type: ignore[return-value]

    updates["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [project_id]

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE canvas_projects SET {set_clause} WHERE id = %s",  # noqa: S608  # nosec B608 — set_clause built from hardcoded column allowlist, not user input
            values,
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Updated canvas project %s: %s", project_id, list(updates.keys()))
    return get_project(project_id)  # type: ignore[return-value]


def delete_project(project_id: str) -> bool:
    """Delete a canvas project. Returns True if a row was removed."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM canvas_projects WHERE id = %s", (project_id,)
        )
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    if deleted:
        logger.info("Deleted canvas project %s", project_id)
    return deleted


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------

def _update_links(project_id: str, links: dict) -> dict:
    """Persist updated links_json and return refreshed project."""
    now = _now_iso()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE canvas_projects SET links_json = %s, updated_at = %s WHERE id = %s",
            (json.dumps(links), now, project_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_project(project_id)  # type: ignore[return-value]


def link_design(project_id: str, canvas_key: str, design_id: str) -> dict:
    """Add or update a canvas link in the project."""
    if canvas_key not in VALID_CANVAS_KEYS:
        raise ValueError(
            f"Invalid canvas_key '{canvas_key}'. Must be one of {sorted(VALID_CANVAS_KEYS)}"
        )
    project = get_project(project_id)
    if project is None:
        raise ValueError(f"Project '{project_id}' not found")
    links = project["links"]
    links[canvas_key] = design_id
    logger.info("Linked %s -> %s in project %s", canvas_key, design_id, project_id)
    return _update_links(project_id, links)


def unlink_design(project_id: str, canvas_key: str) -> dict:
    """Remove a canvas link from the project."""
    if canvas_key not in VALID_CANVAS_KEYS:
        raise ValueError(
            f"Invalid canvas_key '{canvas_key}'. Must be one of {sorted(VALID_CANVAS_KEYS)}"
        )
    project = get_project(project_id)
    if project is None:
        raise ValueError(f"Project '{project_id}' not found")
    links = project["links"]
    links.pop(canvas_key, None)
    logger.info("Unlinked %s from project %s", canvas_key, project_id)
    return _update_links(project_id, links)


# ---------------------------------------------------------------------------
# Compliance & Readiness
# ---------------------------------------------------------------------------

def _read_canvas_score(
    canvas_key: str, design_id: str,
) -> float | None:
    """Read the latest assessment score for a design from its canvas DB."""
    if canvas_key not in CANVAS_DB_MAP:
        return None
    db_file, table, col = CANVAS_DB_MAP[canvas_key]
    db_path = DATA_DIR / db_file
    if not db_path.exists():
        logger.debug("Canvas DB not found: %s", db_path)
        return None
    try:
        conn = get_connection(str(db_path))
        # Check table exists — backend-aware probe (pgrt-sweep-06).
        if not table_exists(conn, table):
            # NDC fallback: try nc_compliance_findings
            if canvas_key == "ndc":
                if table_exists(conn, "nc_compliance_findings"):
                    row = conn.execute(
                        "SELECT score FROM nc_compliance_findings "
                        "ORDER BY created_at DESC LIMIT 1",
                    ).fetchone()
                    conn.close()
                    return float(row[0]) if row else None
            conn.close()
            return None
        row = conn.execute(
            f"SELECT {col} FROM {table} ORDER BY rowid DESC LIMIT 1",  # noqa: S608  # nosec B608 — col/table from registry constants, not user input
        ).fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        logger.exception("Error reading score from %s.%s", db_file, table)
        return None


def get_compliance_summary(project_id: str) -> dict:
    """Aggregate compliance scores across all linked canvases."""
    project = get_project(project_id)
    if project is None:
        raise ValueError(f"Project '{project_id}' not found")

    links = project["links"]
    canvases: dict[str, dict] = {}
    scores: list[float] = []

    for key in sorted(VALID_CANVAS_KEYS):
        design_id = links.get(key)
        if design_id is None:
            canvases[key] = {"design_id": None, "score": None, "grade": None}
            continue
        score = _read_canvas_score(key, design_id)
        grade = _score_to_grade(score)
        canvases[key] = {"design_id": design_id, "score": score, "grade": grade}
        if score is not None:
            scores.append(score)

    overall_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    overall_grade = _score_to_grade(overall_score)

    return {
        "project_id": project_id,
        "canvases": canvases,
        "overall_score": overall_score,
        "overall_grade": overall_grade,
    }


def _count_cat1_findings(canvas_key: str) -> int:
    """Count CAT1 findings in a canvas DB (best-effort)."""
    if canvas_key not in CANVAS_DB_MAP:
        return 0
    db_file, _table, _col = CANVAS_DB_MAP[canvas_key]
    db_path = DATA_DIR / db_file
    if not db_path.exists():
        return 0
    try:
        conn = get_connection(str(db_path))
        # Look for findings table with severity column — backend-aware probes
        # (pgrt-sweep-06). The prior "name LIKE '%findings%'" against sqlite_master
        # only partial-translated on PG (left an invalid `name` column ref), so it
        # raised. list_tables() + a Python filter is portable.
        findings_tables = [t for t in list_tables(conn) if "findings" in t]
        count = 0
        for tbl in findings_tables:
            if column_exists(conn, tbl, "severity"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE severity = 'CAT1'",  # noqa: S608  # nosec B608 — tbl from list_tables(), not user input
                ).fetchone()
                count += row[0] if row else 0
        conn.close()
        return count
    except Exception:
        logger.exception("Error counting CAT1 findings in %s", db_file)
        return 0


def _count_cot_cod_enabled(project_id: str, links: dict) -> int:
    """Count canvases in this project that have CoT/CoD chain telemetry recorded."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(DISTINCT function) FROM llm_chain_telemetry",
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def compute_readiness(project_id: str) -> dict:
    """Compute ATO readiness across 5 dimensions (0-100).

    Dimensions:
    - completeness: fraction of 9 canvases linked
    - compliance: average score across scored canvases
    - coverage: fraction of scored canvases with score > 70
    - risk: penalty for canvases with CAT1 findings
    - explainability: CoT/CoD chain reasoning coverage across canvases
    """
    summary = get_compliance_summary(project_id)
    links = {k: v for k, v in summary["canvases"].items() if v["design_id"] is not None}
    total_canvases = len(VALID_CANVAS_KEYS)

    # Completeness — how many canvases are linked
    linked_count = len(links)
    completeness = round((linked_count / total_canvases) * 100, 1)

    # Compliance — average score of scored canvases
    scored = [v["score"] for v in links.values() if v["score"] is not None]
    compliance = round(sum(scored) / len(scored), 1) if scored else 0.0

    # Coverage — fraction of scored canvases above 70
    passing = sum(1 for s in scored if s > 70)
    coverage = round((passing / len(scored)) * 100, 1) if scored else 0.0

    # Risk — count canvases with CAT1 findings
    cat1_count = 0
    for key in links:
        cat1_count += _count_cat1_findings(key)
    # Each CAT1 finding deducts 5 points, floor at 0
    risk_score = max(0.0, 100.0 - cat1_count * 5.0)

    # Explainability — CoT/CoD reasoning chain coverage
    # Score based on whether chain telemetry exists (any usage = partial credit)
    cot_cod_count = _count_cot_cod_enabled(project_id, links)
    # Full credit (100) if ≥2 functions used CoT/CoD; proportional otherwise
    explainability = min(100.0, round(cot_cod_count * 50.0, 1))

    # Overall readiness: weighted average
    # 20% completeness, 30% compliance, 20% coverage, 15% risk, 15% explainability
    overall = round(
        completeness * 0.20
        + compliance * 0.30
        + coverage * 0.20
        + risk_score * 0.15
        + explainability * 0.15,
        1,
    )

    return {
        "project_id": project_id,
        "readiness_score": overall,
        "grade": _score_to_grade(overall),
        "breakdown": {
            "completeness": {"score": completeness, "linked": linked_count, "total": total_canvases},
            "compliance": {"score": compliance, "scored_canvases": len(scored)},
            "coverage": {"score": coverage, "passing": passing, "scored": len(scored)},
            "risk": {"score": risk_score, "cat1_findings": cat1_count},
            "explainability": {"score": explainability, "cot_cod_functions": cot_cod_count},
        },
    }
