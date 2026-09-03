# CUI // SP-CTI
"""ATO Compliance Dashboard — data layer for control tracking, RMF workflow,
artifact readiness, and NIST 800-53 crosswalk.

NIST 800-53 Controls: SA-11 (Developer Security Testing),
                      CM-3 (Configuration Change Control),
                      CA-7 (Continuous Monitoring),
                      RA-5 (Vulnerability Monitoring and Scanning)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Any

logger = get_logger("icdev.ato_compliance.dashboard")

# RMF step order per NIST SP 800-37 Rev 2
RMF_STAGES = [
    "categorize",
    "select",
    "implement",
    "assess",
    "authorize",
    "monitor",
]

# Human-readable labels for each RMF stage
RMF_STAGE_LABELS = {
    "categorize": "Categorize",
    "select": "Select",
    "implement": "Implement",
    "assess": "Assess",
    "authorize": "Authorize",
    "monitor": "Monitor",
}

# Implementation statuses that count as "positive" coverage
_POSITIVE_STATUSES = {"implemented", "partially_implemented", "compensating"}

# Artifact types tracked for ATO readiness
_ARTIFACT_TYPES = ["ssp", "poam", "stig", "sbom"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _table_exists(conn: Any, table_name: str) -> bool:
    """Check if a table exists; works for both SQLite and PostgreSQL."""
    # Backend-aware, translation-independent existence probe (pgrt-sweep-06).
    from tools.db.storage import table_exists as _table_exists_helper
    return _table_exists_helper(conn, table_name)


def _scalar(row: Any) -> int:
    """Extract integer scalar from a row (dict-like or tuple)."""
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(list(row.values())[0] or 0)
    return int(row[0] or 0)


def _get_default_conn() -> Any:
    """Get the default icdev.db connection via storage layer."""
    from pathlib import Path
    from tools.db.storage import get_connection
    db_path = Path(__file__).resolve().parent.parent.parent / "data" / "icdev.db"
    return get_connection(db_path=str(db_path))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_control_summary(project_id: str, *, conn: Any = None) -> dict:
    """Return per-family NIST 800-53 implementation summary for a project.

    Args:
        project_id: Project identifier.
        conn: Optional DB connection (injected for testing; uses default if None).

    Returns::

        {
            "project_id": str,
            "total_controls": int,
            "implemented": int,
            "partially_implemented": int,
            "planned": int,
            "not_applicable": int,
            "compensating": int,
            "posture_pct": float,       # (implemented + partial) / total * 100
            "families": [
                {"family": str, "total": int, "implemented": int,
                 "partially_implemented": int, "planned": int,
                 "not_applicable": int},
                ...
            ],
        }
    """
    _conn = conn or _get_default_conn()
    close_after = conn is None

    try:
        # Count by status
        rows = _conn.execute(
            """
            SELECT implementation_status, COUNT(*) AS cnt
            FROM project_controls
            WHERE project_id = %s
            GROUP BY implementation_status
            """,
            (project_id,),
        ).fetchall()

        status_counts: dict[str, int] = {}
        for row in rows:
            r = dict(row)
            status_counts[r["implementation_status"]] = int(r["cnt"])

        total = sum(status_counts.values())
        implemented = status_counts.get("implemented", 0)
        partial = status_counts.get("partially_implemented", 0)
        planned = status_counts.get("planned", 0)
        not_applicable = status_counts.get("not_applicable", 0)
        compensating = status_counts.get("compensating", 0)

        posture_pct = round((implemented + partial) / total * 100, 1) if total else 0.0

        # Per-family breakdown — join project_controls to compliance_controls if available
        families: list[dict] = []
        try:
            fam_rows = _conn.execute(
                """
                SELECT
                    cc.family,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pc.implementation_status = 'implemented' THEN 1 ELSE 0 END) AS implemented,
                    SUM(CASE WHEN pc.implementation_status = 'partially_implemented' THEN 1 ELSE 0 END) AS partially_implemented,
                    SUM(CASE WHEN pc.implementation_status = 'planned' THEN 1 ELSE 0 END) AS planned,
                    SUM(CASE WHEN pc.implementation_status = 'not_applicable' THEN 1 ELSE 0 END) AS not_applicable
                FROM project_controls pc
                JOIN compliance_controls cc ON cc.id = pc.control_id
                WHERE pc.project_id = %s
                GROUP BY cc.family
                ORDER BY cc.family
                """,
                (project_id,),
            ).fetchall()
            families = [
                {
                    "family": dict(r)["family"],
                    "total": int(dict(r)["total"]),
                    "implemented": int(dict(r)["implemented"] or 0),
                    "partially_implemented": int(dict(r)["partially_implemented"] or 0),
                    "planned": int(dict(r)["planned"] or 0),
                    "not_applicable": int(dict(r)["not_applicable"] or 0),
                }
                for r in fam_rows
            ]
        except Exception:
            # compliance_controls table may not be present; derive families from control ID prefix
            try:
                fam_rows2 = _conn.execute(
                    """
                    SELECT
                        SUBSTR(control_id, 1, INSTR(control_id || '-', '-') - 1) AS family,
                        COUNT(*) AS total,
                        SUM(CASE WHEN implementation_status = 'implemented' THEN 1 ELSE 0 END) AS implemented,
                        SUM(CASE WHEN implementation_status = 'partially_implemented' THEN 1 ELSE 0 END) AS partially_implemented,
                        SUM(CASE WHEN implementation_status = 'planned' THEN 1 ELSE 0 END) AS planned,
                        SUM(CASE WHEN implementation_status = 'not_applicable' THEN 1 ELSE 0 END) AS not_applicable
                    FROM project_controls
                    WHERE project_id = %s
                    GROUP BY family
                    ORDER BY family
                    """,
                    (project_id,),
                ).fetchall()
                families = [
                    {
                        "family": dict(r)["family"],
                        "total": int(dict(r)["total"]),
                        "implemented": int(dict(r)["implemented"] or 0),
                        "partially_implemented": int(dict(r)["partially_implemented"] or 0),
                        "planned": int(dict(r)["planned"] or 0),
                        "not_applicable": int(dict(r)["not_applicable"] or 0),
                    }
                    for r in fam_rows2
                ]
            except Exception as exc:
                logger.warning("get_control_summary: family breakdown failed: %s", exc)

        return {
            "project_id": project_id,
            "total_controls": total,
            "implemented": implemented,
            "partially_implemented": partial,
            "planned": planned,
            "not_applicable": not_applicable,
            "compensating": compensating,
            "posture_pct": posture_pct,
            "families": families,
        }

    except Exception as exc:
        logger.error("get_control_summary failed for %s: %s", project_id, exc)
        return {
            "project_id": project_id,
            "total_controls": 0,
            "implemented": 0,
            "partially_implemented": 0,
            "planned": 0,
            "not_applicable": 0,
            "compensating": 0,
            "posture_pct": 0.0,
            "families": [],
        }
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


def get_rmf_stages(project_id: str, *, conn: Any = None) -> list[dict]:
    """Return ordered RMF stage lifecycle data for a project.

    If no rows exist for the project the function returns 6 default stages
    all with status='not_started'.

    Each entry also carries the cycle-time fields migration 20260902233931
    added (rmf-cyc-01) — ``started_at``, ``submitted_at``, ``actor`` and
    ``evidence_ref``. ``submitted_at`` is deliberately separate from
    ``completed_at``: the span before it is the platform's automation time and
    the span after it is the Authorizing Official's queue, and a surface that
    shows one figure for both cannot attribute a change to either. See
    tools/compliance/rmf_cycle_time.py.

    Returns::

        [
            {"stage": str, "label": str, "status": str,
             "assigned_to": str|None, "completed_at": str|None, "notes": str|None,
             "started_at": str|None, "submitted_at": str|None,
             "actor": str|None, "evidence_ref": str|None},
            ...  # 6 entries in RMF order
        ]
    """
    _conn = conn or _get_default_conn()
    close_after = conn is None

    def _empty(stage_name: str) -> dict:
        """A stage nothing has recorded. Every clock field is None, never a
        timestamp and never a zero — an unrecorded stage must not render the
        same as one measured at no elapsed time."""
        return {
            "stage": stage_name,
            "label": RMF_STAGE_LABELS[stage_name],
            "status": "not_started",
            "assigned_to": None,
            "completed_at": None,
            "notes": None,
            "started_at": None,
            "submitted_at": None,
            "actor": None,
            "evidence_ref": None,
        }

    default_stages = [_empty(s) for s in RMF_STAGES]

    try:
        if not _table_exists(_conn, "rmf_workflow_stages"):
            return default_stages

        rows = _conn.execute(
            """
            SELECT * FROM rmf_workflow_stages WHERE project_id = %s
            """,
            (project_id,),
        ).fetchall()

        if not rows:
            return default_stages

        # Index by stage name
        stage_map: dict[str, dict] = {}
        for row in rows:
            r = dict(row)
            stage_map[r["stage"]] = r

        result = []
        for s in RMF_STAGES:
            if s not in stage_map:
                result.append(_empty(s))
                continue
            r = stage_map[s]
            result.append(
                {
                    "stage": s,
                    "label": RMF_STAGE_LABELS[s],
                    "status": r.get("status", "not_started"),
                    "assigned_to": r.get("assigned_to"),
                    "completed_at": r.get("completed_at"),
                    "notes": r.get("notes"),
                    # `.get` rather than indexing: a database that predates
                    # migration 20260902233931 has the row without the columns,
                    # and reporting those as None is correct — they were never
                    # recorded there.
                    "started_at": r.get("started_at"),
                    "submitted_at": r.get("submitted_at"),
                    "actor": r.get("actor"),
                    "evidence_ref": r.get("evidence_ref"),
                }
            )

        return result

    except Exception as exc:
        logger.error("get_rmf_stages failed for %s: %s", project_id, exc)
        return default_stages
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


def get_artifact_status(project_id: str, *, conn: Any = None) -> dict:
    """Return ATO artifact readiness status for a project.

    Checks ssp_documents, poam_items, stig_findings, and sbom_records.

    Returns::

        {
            "project_id": str,
            "readiness_score": int,   # 0-100 composite
            "artifacts": [
                {"type": str, "count": int, "status": str, "detail": str},
                ...
            ],
        }
    """
    _conn = conn or _get_default_conn()
    close_after = conn is None

    artifacts: list[dict] = []

    try:
        # --- SSP ---
        ssp_count = 0
        ssp_status = "missing"
        ssp_detail = "No SSP document found"
        try:
            ssp_count = _scalar(
                _conn.execute(
                    "SELECT COUNT(*) FROM ssp_documents WHERE project_id = %s",
                    (project_id,),
                ).fetchone()
            )
            if ssp_count > 0:
                # Check if any is approved
                approved = _scalar(
                    _conn.execute(
                        "SELECT COUNT(*) FROM ssp_documents "
                        "WHERE project_id = %s AND status = 'approved'",
                        (project_id,),
                    ).fetchone()
                )
                ssp_status = "approved" if approved > 0 else "draft"
                ssp_detail = f"{ssp_count} SSP(s); status: {ssp_status}"
        except Exception as e:
            ssp_detail = f"SSP check error: {e}"
        artifacts.append(
            {"type": "ssp", "count": ssp_count, "status": ssp_status, "detail": ssp_detail}
        )

        # --- POAM ---
        poam_count = 0
        poam_status = "ok"
        poam_detail = "No open POAMs"
        try:
            poam_count = _scalar(
                _conn.execute(
                    "SELECT COUNT(*) FROM poam_items WHERE project_id = %s",
                    (project_id,),
                ).fetchone()
            )
            critical = _scalar(
                _conn.execute(
                    "SELECT COUNT(*) FROM poam_items "
                    "WHERE project_id = %s AND severity = 'critical' AND status != 'completed'",
                    (project_id,),
                ).fetchone()
            )
            if critical > 0:
                poam_status = "critical"
            elif poam_count > 0:
                poam_status = "open"
            poam_detail = (
                f"{poam_count} item(s); {critical} critical"
                if poam_count > 0
                else "No POAMs"
            )
        except Exception as e:
            poam_detail = f"POAM check error: {e}"
        artifacts.append(
            {"type": "poam", "count": poam_count, "status": poam_status, "detail": poam_detail}
        )

        # --- STIG ---
        stig_open = 0
        stig_status = "ok"
        stig_detail = "No open STIG findings"
        try:
            stig_open = _scalar(
                _conn.execute(
                    "SELECT COUNT(*) FROM stig_findings "
                    "WHERE project_id = %s AND status = 'Open'",
                    (project_id,),
                ).fetchone()
            )
            cat1 = _scalar(
                _conn.execute(
                    "SELECT COUNT(*) FROM stig_findings "
                    "WHERE project_id = %s AND severity = 'CAT1' AND status = 'Open'",
                    (project_id,),
                ).fetchone()
            )
            if cat1 > 0:
                stig_status = "critical"
            elif stig_open > 0:
                stig_status = "open"
            stig_detail = f"{stig_open} open finding(s); {cat1} CAT I"
        except Exception as e:
            stig_detail = f"STIG check error: {e}"
        artifacts.append(
            {"type": "stig", "count": stig_open, "status": stig_status, "detail": stig_detail}
        )

        # --- SBOM ---
        sbom_count = 0
        sbom_status = "missing"
        sbom_detail = "No SBOM records found"
        try:
            sbom_count = _scalar(
                _conn.execute(
                    "SELECT COUNT(*) FROM sbom_records WHERE project_id = %s",
                    (project_id,),
                ).fetchone()
            )
            sbom_status = "present" if sbom_count > 0 else "missing"
            sbom_detail = f"{sbom_count} SBOM record(s)" if sbom_count > 0 else "No SBOM"
        except Exception as e:
            sbom_detail = f"SBOM check error: {e}"
        artifacts.append(
            {"type": "sbom", "count": sbom_count, "status": sbom_status, "detail": sbom_detail}
        )

        # Compute readiness score (0-100)
        # Weights: SSP=30, POAM=30, STIG=25, SBOM=15
        score = 0
        if ssp_status == "approved":
            score += 30
        elif ssp_status == "draft":
            score += 15
        if poam_status == "ok":
            score += 30
        elif poam_status == "open":
            score += 15
        # STIG: full points if no open findings
        if stig_status == "ok":
            score += 25
        elif stig_status == "open":
            score += 10
        if sbom_status == "present":
            score += 15

        return {
            "project_id": project_id,
            "readiness_score": score,
            "artifacts": artifacts,
        }

    except Exception as exc:
        logger.error("get_artifact_status failed for %s: %s", project_id, exc)
        return {
            "project_id": project_id,
            "readiness_score": 0,
            "artifacts": [],
        }
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


def get_crosswalk_summary(project_id: str, *, conn: Any = None) -> dict:
    """Return NIST 800-53 crosswalk coverage summary for a project.

    Attempts to load crosswalk data from context/compliance/control_crosswalk.json
    and compute coverage per framework based on implemented controls.

    Returns::

        {
            "project_id": str,
            "available": bool,
            "frameworks": [
                {"name": str, "key": str, "coverage_pct": float,
                 "mapped_controls": int, "covered_controls": int},
                ...
            ],
        }
    """
    _conn = conn or _get_default_conn()
    close_after = conn is None

    try:
        # Gather implemented controls for this project
        rows = _conn.execute(
            """
            SELECT control_id, implementation_status
            FROM project_controls
            WHERE project_id = %s
            """,
            (project_id,),
        ).fetchall()

        implemented_controls: set[str] = {
            dict(r)["control_id"].upper()
            for r in rows
            if dict(r)["implementation_status"] in _POSITIVE_STATUSES
        }

        # Try loading crosswalk data
        try:
            from tools.compliance.crosswalk_engine import (
                load_crosswalk,
                FRAMEWORK_KEYS,
            )
            crosswalk_data = load_crosswalk()
            available = True
        except Exception:
            crosswalk_data = None
            available = False

        frameworks: list[dict] = []
        if available and crosswalk_data:
            # Build framework → controls mapping
            fw_controls: dict[str, set[str]] = {}
            for ctrl_id, fw_list in crosswalk_data.items():
                if isinstance(fw_list, list):
                    for fw in fw_list:
                        fw_controls.setdefault(fw, set()).add(ctrl_id.upper())
                elif isinstance(fw_list, dict):
                    for fw in fw_list.keys():
                        fw_controls.setdefault(fw, set()).add(ctrl_id.upper())

            # Select top frameworks relevant to IL4 (DoD default)
            priority_frameworks = [
                "fedramp_moderate", "fedramp_high", "cmmc_level_2",
                "il4", "nist_800_171",
            ]
            for fw_key in priority_frameworks:
                ctrl_set = fw_controls.get(fw_key, set())
                total_fw = len(ctrl_set)
                if total_fw == 0:
                    continue
                covered = len(ctrl_set & implemented_controls)
                cov_pct = round(covered / total_fw * 100, 1)
                fw_name = FRAMEWORK_KEYS.get(fw_key, fw_key.replace("_", " ").title())
                frameworks.append(
                    {
                        "key": fw_key,
                        "name": fw_name,
                        "coverage_pct": cov_pct,
                        "mapped_controls": total_fw,
                        "covered_controls": covered,
                    }
                )
        else:
            # Fallback: infer coverage from implemented controls using simple families
            # Return placeholder framework summary based on control family coverage
            total = len(rows)
            covered_count = len(implemented_controls)
            base_pct = round(covered_count / total * 100, 1) if total else 0.0
            frameworks = [
                {
                    "key": "nist_800_53",
                    "name": "NIST 800-53 Rev 5",
                    "coverage_pct": base_pct,
                    "mapped_controls": total,
                    "covered_controls": covered_count,
                }
            ]

        return {
            "project_id": project_id,
            "available": available,
            "frameworks": frameworks,
        }

    except Exception as exc:
        logger.error("get_crosswalk_summary failed for %s: %s", project_id, exc)
        return {
            "project_id": project_id,
            "available": False,
            "frameworks": [],
        }
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


def get_posture_score(project_id: str, *, conn: Any = None) -> dict:
    """Return composite ATO posture score (0-100) with letter grade.

    Weighted composite of:
      - Control implementation coverage: 50%
      - RMF stage progress: 30%
      - Artifact readiness: 20%

    Returns::

        {
            "project_id": str,
            "score": int,          # 0-100
            "grade": str,          # A/B/C/D/F
            "components": {
                "control_pct": float,
                "rmf_pct": float,
                "artifact_pct": float,
            },
        }
    """
    _conn = conn or _get_default_conn()
    close_after = conn is None

    try:
        # 1. Control coverage
        ctrl = get_control_summary(project_id, conn=_conn)
        control_pct = float(ctrl.get("posture_pct", 0.0))

        # 2. RMF stage progress
        stages = get_rmf_stages(project_id, conn=_conn)
        completed = sum(1 for s in stages if s["status"] == "complete")
        in_prog = sum(1 for s in stages if s["status"] == "in_progress")
        rmf_pct = round((completed + in_prog * 0.5) / len(stages) * 100, 1)

        # 3. Artifact readiness
        artifacts = get_artifact_status(project_id, conn=_conn)
        artifact_pct = float(artifacts.get("readiness_score", 0))

        # Composite: 50% control, 30% RMF, 20% artifact
        composite = round(
            control_pct * 0.50 + rmf_pct * 0.30 + artifact_pct * 0.20,
            1,
        )
        score = min(100, max(0, int(composite)))

        # Letter grade
        if score >= 90:
            grade = "A"
        elif score >= 80:
            grade = "B"
        elif score >= 70:
            grade = "C"
        elif score >= 60:
            grade = "D"
        else:
            grade = "F"

        return {
            "project_id": project_id,
            "score": score,
            "grade": grade,
            "components": {
                "control_pct": control_pct,
                "rmf_pct": rmf_pct,
                "artifact_pct": artifact_pct,
            },
        }

    except Exception as exc:
        logger.error("get_posture_score failed for %s: %s", project_id, exc)
        return {
            "project_id": project_id,
            "score": 0,
            "grade": "F",
            "components": {
                "control_pct": 0.0,
                "rmf_pct": 0.0,
                "artifact_pct": 0.0,
            },
        }
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass
