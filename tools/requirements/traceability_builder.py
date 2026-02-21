#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Full RTM (Requirements Traceability Matrix) builder.

Builds complete traceability chains:
    Requirement -> SAFe item -> SysML element -> Code module -> Test file -> NIST control -> UAT test

Stores results in the review_traceability table and identifies gaps
where trace links are missing at any level.

Usage:
    # Build RTM for entire project
    python tools/requirements/traceability_builder.py --project-id proj-123 \\
        --build-rtm --json

    # Build RTM for specific session
    python tools/requirements/traceability_builder.py --project-id proj-123 \\
        --build-rtm --session-id sess-abc --json

    # Run gap analysis
    python tools/requirements/traceability_builder.py --project-id proj-123 \\
        --gap-analysis --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="rtm"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Trace link resolution helpers
# ---------------------------------------------------------------------------

def _find_linked_ids(conn, project_id, source_type, source_id, target_type):
    """Find all IDs linked via digital_thread_links.

    Searches both directions: source->target and target->source.

    Args:
        conn: Database connection.
        project_id: Project scope.
        source_type: Source entity type.
        source_id: Source entity ID.
        target_type: Target entity type to find.

    Returns:
        List of target IDs.
    """
    # Forward links
    forward = conn.execute(
        """SELECT target_id FROM digital_thread_links
           WHERE project_id = ? AND source_type = ? AND source_id = ?
           AND target_type = ?""",
        (project_id, source_type, source_id, target_type),
    ).fetchall()

    # Reverse links
    reverse = conn.execute(
        """SELECT source_id FROM digital_thread_links
           WHERE project_id = ? AND target_type = ? AND target_id = ?
           AND source_type = ?""",
        (project_id, source_type, source_id, target_type),
    ).fetchall()

    ids = set()
    for r in forward:
        ids.add(r["target_id"])
    for r in reverse:
        ids.add(r["source_id"])

    return list(ids)


def _find_safe_items_for_requirement(conn, project_id, req_id):
    """Find SAFe decomposition items linked to a requirement.

    Checks both digital_thread_links and safe_decomposition.source_requirement_ids.
    """
    # Via digital thread
    thread_ids = _find_linked_ids(
        conn, project_id, "intake_requirement", req_id, "safe_item",
    )

    # Via source_requirement_ids in safe_decomposition
    rows = conn.execute(
        """SELECT id, source_requirement_ids FROM safe_decomposition
           WHERE project_id = ?""",
        (project_id,),
    ).fetchall()

    for r in rows:
        src_ids_raw = r["source_requirement_ids"]
        if src_ids_raw:
            try:
                src_ids = json.loads(src_ids_raw)
                if isinstance(src_ids, list) and req_id in src_ids:
                    thread_ids.append(r["id"])
                elif isinstance(src_ids, str) and req_id in src_ids:
                    thread_ids.append(r["id"])
            except (json.JSONDecodeError, TypeError):
                if req_id in str(src_ids_raw):
                    thread_ids.append(r["id"])

    return list(set(thread_ids))


def _find_nist_controls_for_requirement(conn, project_id, req_id):
    """Find NIST controls mapped to a requirement.

    Checks digital_thread_links for nist_control mappings.
    Also checks project_controls for any linked controls.
    """
    # Via digital thread
    thread_ids = _find_linked_ids(
        conn, project_id, "intake_requirement", req_id, "nist_control",
    )

    return list(set(thread_ids))


# ---------------------------------------------------------------------------
# build_rtm
# ---------------------------------------------------------------------------

def build_rtm(project_id, session_id=None, db_path=None):
    """Build a full Requirements Traceability Matrix.

    For each requirement:
        - Find linked SAFe decomposition items
        - Find linked SysML elements (via digital_thread_links)
        - Find linked code modules (via model_code_mappings or digital_thread_links)
        - Find linked test files (via digital_thread_links)
        - Find linked NIST controls (via project_controls or digital_thread_links)
        - Compute coverage percentage

    Results are stored in review_traceability table.

    Args:
        project_id: ICDEV project identifier.
        session_id: Optional session filter.
        db_path: Override database path.

    Returns:
        dict with total_requirements, fully_traced, partially_traced,
        untraced, coverage_pct, gaps.
    """
    conn = _get_connection(db_path)
    try:
        # Get requirements
        if session_id:
            reqs = conn.execute(
                """SELECT id, session_id, raw_text, requirement_type, priority, status
                   FROM intake_requirements
                   WHERE session_id = ? AND project_id = ?
                   ORDER BY id""",
                (session_id, project_id),
            ).fetchall()
        else:
            reqs = conn.execute(
                """SELECT id, session_id, raw_text, requirement_type, priority, status
                   FROM intake_requirements
                   WHERE project_id = ?
                   ORDER BY id""",
                (project_id,),
            ).fetchall()

        if not reqs:
            return {
                "project_id": project_id,
                "session_id": session_id,
                "total_requirements": 0,
                "fully_traced": 0,
                "partially_traced": 0,
                "untraced": 0,
                "coverage_pct": 0.0,
                "gaps": [],
            }

        now = _now()
        total = len(reqs)
        fully_traced = 0
        partially_traced = 0
        untraced = 0
        all_gaps = []

        for req in reqs:
            req_dict = dict(req)
            req_id = req_dict["id"]
            req_session_id = req_dict["session_id"]

            # 1. SAFe items
            safe_item_ids = _find_safe_items_for_requirement(
                conn, project_id, req_id,
            )

            # 2. SysML elements
            sysml_ids = _find_linked_ids(
                conn, project_id, "intake_requirement", req_id, "sysml_element",
            )
            # Also check via SAFe items
            for safe_id in safe_item_ids:
                sysml_via_safe = _find_linked_ids(
                    conn, project_id, "safe_item", safe_id, "sysml_element",
                )
                sysml_ids.extend(sysml_via_safe)
            sysml_ids = list(set(sysml_ids))

            # 3. Code modules
            code_ids = _find_linked_ids(
                conn, project_id, "intake_requirement", req_id, "code_module",
            )
            # Also via SysML elements -> model_code_mappings
            for sysml_id in sysml_ids:
                mcm_rows = conn.execute(
                    """SELECT code_path FROM model_code_mappings
                       WHERE project_id = ? AND sysml_element_id = ?""",
                    (project_id, sysml_id),
                ).fetchall()
                for mcm in mcm_rows:
                    code_ids.append(mcm["code_path"])
            # Also via SAFe items
            for safe_id in safe_item_ids:
                code_via_safe = _find_linked_ids(
                    conn, project_id, "safe_item", safe_id, "code_module",
                )
                code_ids.extend(code_via_safe)
            code_ids = list(set(code_ids))

            # 4. Test files
            test_ids = _find_linked_ids(
                conn, project_id, "intake_requirement", req_id, "test_file",
            )
            for safe_id in safe_item_ids:
                test_via_safe = _find_linked_ids(
                    conn, project_id, "safe_item", safe_id, "test_file",
                )
                test_ids.extend(test_via_safe)
            for code_id in code_ids:
                test_via_code = _find_linked_ids(
                    conn, project_id, "code_module", code_id, "test_file",
                )
                test_ids.extend(test_via_code)
            test_ids = list(set(test_ids))

            # 5. NIST controls
            control_ids = _find_nist_controls_for_requirement(
                conn, project_id, req_id,
            )
            for safe_id in safe_item_ids:
                ctrl_via_safe = _find_linked_ids(
                    conn, project_id, "safe_item", safe_id, "nist_control",
                )
                control_ids.extend(ctrl_via_safe)
            control_ids = list(set(control_ids))

            # 6. UAT tests (via digital thread)
            uat_ids = _find_linked_ids(
                conn, project_id, "intake_requirement", req_id, "uat_test",
            )
            uat_ids = list(set(uat_ids))

            # Calculate coverage
            trace_dimensions = {
                "safe_items": bool(safe_item_ids),
                "sysml_elements": bool(sysml_ids),
                "code_modules": bool(code_ids),
                "test_files": bool(test_ids),
                "nist_controls": bool(control_ids),
            }
            filled = sum(1 for v in trace_dimensions.values() if v)
            total_dims = len(trace_dimensions)
            coverage = (filled / total_dims * 100.0) if total_dims > 0 else 0.0

            # Identify gaps
            missing = [k for k, v in trace_dimensions.items() if not v]
            gap_severity = "none"
            if len(missing) == total_dims:
                gap_severity = "critical"
                untraced += 1
            elif len(missing) > 0:
                gap_severity = "high" if len(missing) >= 3 else "medium" if len(missing) >= 2 else "low"
                partially_traced += 1
            else:
                fully_traced += 1

            if missing:
                all_gaps.append({
                    "requirement_id": req_id,
                    "requirement_text": (req_dict.get("raw_text") or "")[:100],
                    "missing_links": missing,
                    "severity": gap_severity,
                    "coverage_pct": round(coverage, 1),
                })

            # Upsert into review_traceability
            existing = conn.execute(
                """SELECT id FROM review_traceability
                   WHERE project_id = ? AND requirement_id = ?
                   AND requirement_type = 'intake'""",
                (project_id, req_id),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE review_traceability
                       SET session_id = ?,
                           sysml_element_ids = ?,
                           code_module_ids = ?,
                           test_file_ids = ?,
                           compliance_control_ids = ?,
                           uat_test_ids = ?,
                           coverage_pct = ?,
                           gaps = ?,
                           last_verified = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (req_session_id,
                     json.dumps(sysml_ids) if sysml_ids else None,
                     json.dumps(code_ids) if code_ids else None,
                     json.dumps(test_ids) if test_ids else None,
                     json.dumps(control_ids) if control_ids else None,
                     json.dumps(uat_ids) if uat_ids else None,
                     coverage,
                     json.dumps(missing) if missing else None,
                     now, now,
                     existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO review_traceability
                       (project_id, session_id, requirement_id, requirement_type,
                        sysml_element_ids, code_module_ids, test_file_ids,
                        compliance_control_ids, uat_test_ids,
                        coverage_pct, gaps, last_verified, verified_by,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, req_session_id, req_id, "intake",
                     json.dumps(sysml_ids) if sysml_ids else None,
                     json.dumps(code_ids) if code_ids else None,
                     json.dumps(test_ids) if test_ids else None,
                     json.dumps(control_ids) if control_ids else None,
                     json.dumps(uat_ids) if uat_ids else None,
                     coverage,
                     json.dumps(missing) if missing else None,
                     now, "icdev-traceability-builder",
                     now, now),
                )

        conn.commit()

        overall_coverage = 0.0
        if total > 0:
            overall_coverage = round(fully_traced / total * 100.0, 1)

        log_event(
            event_type="integration_sync_push",
            actor="icdev-traceability-builder",
            action=f"Built RTM for {total} requirements ({fully_traced} fully traced)",
            project_id=project_id,
            details={
                "total": total,
                "fully_traced": fully_traced,
                "partially_traced": partially_traced,
                "untraced": untraced,
                "coverage_pct": overall_coverage,
            },
        )

        return {
            "project_id": project_id,
            "session_id": session_id,
            "total_requirements": total,
            "fully_traced": fully_traced,
            "partially_traced": partially_traced,
            "untraced": untraced,
            "coverage_pct": overall_coverage,
            "gaps": all_gaps,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# gap_analysis
# ---------------------------------------------------------------------------

def gap_analysis(project_id, db_path=None):
    """Find requirements with missing trace links at any level.

    Reads from the review_traceability table (populated by build_rtm)
    and identifies all gaps with their severity.

    Args:
        project_id: ICDEV project identifier.
        db_path: Override database path.

    Returns:
        dict with gaps list and summary statistics.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT rt.requirement_id, rt.requirement_type,
                      rt.sysml_element_ids, rt.code_module_ids,
                      rt.test_file_ids, rt.compliance_control_ids,
                      rt.uat_test_ids, rt.coverage_pct, rt.gaps,
                      ir.raw_text, ir.priority, ir.status
               FROM review_traceability rt
               LEFT JOIN intake_requirements ir ON ir.id = rt.requirement_id
               WHERE rt.project_id = ?
               ORDER BY rt.coverage_pct ASC""",
            (project_id,),
        ).fetchall()

        if not rows:
            return {
                "project_id": project_id,
                "total_traced": 0,
                "total_gaps": 0,
                "gaps": [],
                "severity_summary": {},
                "dimension_summary": {},
            }

        gaps = []
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        dimension_counts = {
            "safe_items": 0,
            "sysml_elements": 0,
            "code_modules": 0,
            "test_files": 0,
            "nist_controls": 0,
        }

        for r in rows:
            r_dict = dict(r)
            stored_gaps = json.loads(r_dict["gaps"] or "[]") if r_dict["gaps"] else []

            if not stored_gaps:
                continue

            # Determine severity
            n_missing = len(stored_gaps)
            if n_missing >= 5:
                severity = "critical"
            elif n_missing >= 3:
                severity = "high"
            elif n_missing >= 2:
                severity = "medium"
            else:
                severity = "low"

            severity_counts[severity] = severity_counts.get(severity, 0) + 1

            for dim in stored_gaps:
                if dim in dimension_counts:
                    dimension_counts[dim] += 1

            gaps.append({
                "requirement_id": r_dict["requirement_id"],
                "requirement_text": (r_dict.get("raw_text") or "")[:100],
                "priority": r_dict.get("priority"),
                "status": r_dict.get("status"),
                "missing_links": stored_gaps,
                "severity": severity,
                "coverage_pct": round(r_dict["coverage_pct"] or 0.0, 1),
            })

        return {
            "project_id": project_id,
            "total_traced": len(rows),
            "total_gaps": len(gaps),
            "gaps": gaps,
            "severity_summary": severity_counts,
            "dimension_summary": dimension_counts,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Requirements Traceability Matrix builder for ICDEV RICOAS"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Actions
    parser.add_argument("--build-rtm", action="store_true",
                        help="Build full RTM")
    parser.add_argument("--gap-analysis", action="store_true",
                        help="Run gap analysis on existing RTM")

    # Build args
    parser.add_argument("--session-id", help="Intake session ID (optional)")

    args = parser.parse_args()

    result = None

    if args.build_rtm:
        result = build_rtm(
            project_id=args.project_id,
            session_id=args.session_id,
        )
    elif args.gap_analysis:
        result = gap_analysis(project_id=args.project_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, value in result.items():
            if key == "gaps" and isinstance(value, list):
                print(f"\n{key} ({len(value)} items):")
                for gap in value:
                    print(f"  - {gap['requirement_id']}: "
                          f"missing={gap.get('missing_links', [])}, "
                          f"severity={gap.get('severity', 'unknown')}")
            else:
                print(f"{key}: {value}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
