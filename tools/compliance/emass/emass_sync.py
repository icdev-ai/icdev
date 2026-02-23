#!/usr/bin/env python3
# CUI // SP-CTI
"""eMASS sync orchestrator -- coordinates data flow between ICDEV and eMASS.

Supports three modes:
    - api: Push data directly via the eMASS REST API (EMASSClient)
    - export: Generate CSV/ZIP exports for manual upload
    - hybrid: Try API first, fall back to export on failure

Also supports pulling ATO status from eMASS and querying sync history.

Usage:
    python tools/compliance/emass/emass_sync.py --project-id proj-123 --mode hybrid
    python tools/compliance/emass/emass_sync.py --project-id proj-123 --mode api
    python tools/compliance/emass/emass_sync.py --project-id proj-123 --mode export
    python tools/compliance/emass/emass_sync.py --project-id proj-123 --pull-ato
    python tools/compliance/emass/emass_sync.py --project-id proj-123 --history
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

sys.path.insert(0, str(BASE_DIR))


# ============================================================
# Database helpers
# ============================================================

def _get_connection(db_path=None):
    """Get a database connection.

    Args:
        db_path: Optional override for database file path.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(conn, project_id, action, details=None):
    """Log an eMASS sync event to the immutable audit trail.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.
        action: Short action description (e.g., ``sync_completed_hybrid``).
        details: Optional dict of additional context.
    """
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification, created_at)
           VALUES (?, 'emass_sync', 'icdev-emass-sync', ?, ?, 'CUI', datetime('now'))""",
        (project_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


def _ensure_sync_tables(conn):
    """Ensure the emass_sync_log and emass_systems tables exist.

    These tables are created idempotently so the sync module works
    even if the main ICDEV database schema has not been updated to
    include them yet.

    Args:
        conn: Active database connection.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emass_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            sync_mode TEXT NOT NULL,
            sync_status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            controls_synced INTEGER DEFAULT 0,
            poam_synced INTEGER DEFAULT 0,
            artifacts_synced INTEGER DEFAULT 0,
            test_results_synced INTEGER DEFAULT 0,
            error_message TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emass_systems (
            project_id TEXT PRIMARY KEY,
            emass_system_id TEXT,
            system_name TEXT,
            authorization_status TEXT,
            authorization_date TEXT,
            authorization_termination_date TEXT,
            last_sync TEXT,
            last_sync_status TEXT,
            sync_mode TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _log_sync(conn, project_id, mode, status, started_at, details=None,
              controls=0, poam=0, artifacts=0, test_results=0, error=None):
    """Write a record to the emass_sync_log table.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.
        mode: Sync mode used (api, export, hybrid).
        status: Final sync status (success, partial, error).
        started_at: ISO timestamp when sync started.
        details: Optional JSON-serializable details dict.
        controls: Number of controls synced.
        poam: Number of POA&M items synced.
        artifacts: Number of artifacts synced.
        test_results: Number of test results synced.
        error: Error message if sync failed.
    """
    conn.execute(
        """INSERT INTO emass_sync_log
           (project_id, sync_mode, sync_status, started_at, completed_at,
            controls_synced, poam_synced, artifacts_synced, test_results_synced,
            error_message, details, classification)
           VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, 'CUI')""",
        (
            project_id, mode, status, started_at,
            controls, poam, artifacts, test_results,
            error,
            json.dumps(details) if details else None,
        ),
    )
    conn.commit()


def _update_emass_system(conn, project_id, sync_status, sync_mode,
                         emass_system_id=None, auth_status=None,
                         auth_date=None, auth_term_date=None):
    """Update or insert a record in the emass_systems table.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.
        sync_status: Last sync status.
        sync_mode: Last sync mode used.
        emass_system_id: eMASS system identifier (if known).
        auth_status: Authorization status from eMASS.
        auth_date: Authorization date from eMASS.
        auth_term_date: Authorization termination date from eMASS.
    """
    existing = conn.execute(
        "SELECT project_id FROM emass_systems WHERE project_id = ?",
        (project_id,),
    ).fetchone()

    if existing:
        updates = [
            "last_sync = datetime('now')",
            "last_sync_status = ?",
            "sync_mode = ?",
            "updated_at = datetime('now')",
        ]
        params = [sync_status, sync_mode]
        if emass_system_id is not None:
            updates.append("emass_system_id = ?")
            params.append(emass_system_id)
        if auth_status is not None:
            updates.append("authorization_status = ?")
            params.append(auth_status)
        if auth_date is not None:
            updates.append("authorization_date = ?")
            params.append(auth_date)
        if auth_term_date is not None:
            updates.append("authorization_termination_date = ?")
            params.append(auth_term_date)
        params.append(project_id)
        conn.execute(
            f"UPDATE emass_systems SET {', '.join(updates)} WHERE project_id = ?",
            params,
        )
    else:
        conn.execute(
            """INSERT INTO emass_systems
               (project_id, emass_system_id, authorization_status,
                authorization_date, authorization_termination_date,
                last_sync, last_sync_status, sync_mode, classification)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?, 'CUI')""",
            (
                project_id,
                emass_system_id or "",
                auth_status or "Unknown",
                auth_date or "",
                auth_term_date or "",
                sync_status,
                sync_mode,
            ),
        )
    conn.commit()


# ============================================================
# Load project compliance data
# ============================================================

def _load_project_data(conn, project_id):
    """Load all project compliance data needed for sync.

    Args:
        conn: Active database connection.
        project_id: ICDEV project identifier.

    Returns:
        Dict with project, controls, poam_items, stig_findings,
        and vuln_scans data.

    Raises:
        ValueError: If the project is not found.
    """
    data = {}

    # Project info
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    data["project"] = dict(row)

    # Control implementations
    rows = conn.execute(
        "SELECT * FROM project_controls WHERE project_id = ? ORDER BY control_id",
        (project_id,),
    ).fetchall()
    data["controls"] = [dict(r) for r in rows]

    # POA&M items
    rows = conn.execute(
        "SELECT * FROM poam_items WHERE project_id = ? ORDER BY severity",
        (project_id,),
    ).fetchall()
    data["poam_items"] = [dict(r) for r in rows]

    # STIG findings
    rows = conn.execute(
        "SELECT * FROM stig_findings WHERE project_id = ? ORDER BY severity, finding_id",
        (project_id,),
    ).fetchall()
    data["stig_findings"] = [dict(r) for r in rows]

    # Vulnerability management / scan results
    try:
        rows = conn.execute(
            "SELECT * FROM cssp_vuln_management WHERE project_id = ? ORDER BY scan_date DESC LIMIT 10",
            (project_id,),
        ).fetchall()
        data["vuln_scans"] = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        data["vuln_scans"] = []

    return data


# ============================================================
# API sync
# ============================================================

def _sync_via_api(project_id, db_path=None):
    """Push controls, POA&M, artifacts, and test results via eMASS REST API.

    Uses the EMASSClient from emass_client.py. Each data type is pushed
    independently so partial failures do not block other pushes.

    Args:
        project_id: ICDEV project ID.
        db_path: Database path (optional).

    Returns:
        Dict with per-step results and overall status.
    """
    try:
        from tools.compliance.emass.emass_client import EMASSClient
    except ImportError:
        return {
            "status": "error",
            "error": "EMASSClient import failed. Ensure emass_client.py exists.",
            "fallback": True,
        }

    conn = _get_connection(db_path)
    try:
        data = _load_project_data(conn, project_id)
    finally:
        conn.close()

    client = EMASSClient(db_path=db_path)
    results = {"mode": "api", "steps": {}}

    try:
        # Resolve eMASS system ID from local mapping or project metadata
        emass_system_id = data["project"].get("emass_system_id", project_id)

        # Step 1: Push control implementations
        if data["controls"]:
            try:
                ctrl_result = client.push_controls(emass_system_id, data["controls"])
                results["steps"]["push_controls"] = {
                    "count": len(data["controls"]),
                    "result": ctrl_result,
                }
            except Exception as e:
                results["steps"]["push_controls"] = {
                    "count": len(data["controls"]),
                    "error": str(e),
                }

        # Step 2: Push POA&M items
        if data["poam_items"]:
            try:
                poam_result = client.push_poam(emass_system_id, data["poam_items"])
                results["steps"]["push_poam"] = {
                    "count": len(data["poam_items"]),
                    "result": poam_result,
                }
            except Exception as e:
                results["steps"]["push_poam"] = {
                    "count": len(data["poam_items"]),
                    "error": str(e),
                }

        # Step 3: Push artifacts (SSP, SBOM metadata)
        artifacts = []
        conn2 = _get_connection(db_path)
        try:
            ssp_rows = conn2.execute(
                "SELECT file_path FROM ssp_documents WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in ssp_rows:
                artifacts.append({
                    "filename": Path(row["file_path"]).name,
                    "type": "Procedure",
                    "category": "Authorization Package",
                    "description": f"SSP document for {project_id}",
                })
            sbom_rows = conn2.execute(
                "SELECT file_path FROM sbom_records WHERE project_id = ? AND file_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            for row in sbom_rows:
                artifacts.append({
                    "filename": Path(row["file_path"]).name,
                    "type": "Procedure",
                    "category": "Supply Chain",
                    "description": f"SBOM for {project_id}",
                })
        finally:
            conn2.close()

        if artifacts:
            try:
                art_result = client.push_artifacts(emass_system_id, artifacts)
                results["steps"]["push_artifacts"] = {
                    "count": len(artifacts),
                    "result": art_result,
                }
            except Exception as e:
                results["steps"]["push_artifacts"] = {
                    "count": len(artifacts),
                    "error": str(e),
                }

        # Step 4: Push test results (STIG findings as test results)
        if data["stig_findings"]:
            try:
                test_result = client.push_test_results(emass_system_id, data["stig_findings"])
                results["steps"]["push_test_results"] = {
                    "count": len(data["stig_findings"]),
                    "result": test_result,
                }
            except Exception as e:
                results["steps"]["push_test_results"] = {
                    "count": len(data["stig_findings"]),
                    "error": str(e),
                }

        # Step 5: Pull back authorization (ATO) status
        try:
            auth_status = client.get_authorization_status(emass_system_id)
            if auth_status and auth_status.get("status") != "error":
                results["steps"]["pull_ato_status"] = auth_status
        except Exception as e:
            results["steps"]["pull_ato_status"] = {"error": str(e)}

        # Determine overall API sync status
        step_errors = [
            s for s in results["steps"].values()
            if isinstance(s, dict) and s.get("error")
        ]
        if not step_errors:
            results["status"] = "success"
        elif len(step_errors) < len(results["steps"]):
            results["status"] = "partial"
        else:
            results["status"] = "error"
            results["fallback"] = True

    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)
        results["fallback"] = True

    finally:
        client.close()

    return results


# ============================================================
# Export sync (file-based)
# ============================================================

def _sync_via_export(project_id, db_path=None):
    """Generate all CSV/ZIP exports for manual upload to eMASS.

    Uses emass_export.export_all_emass() to produce every export file.

    Args:
        project_id: ICDEV project ID.
        db_path: Database path (optional).

    Returns:
        Dict with export results.
    """
    try:
        from tools.compliance.emass.emass_export import export_all_emass
    except ImportError:
        return {
            "status": "error",
            "error": "emass_export module import failed. Ensure emass_export.py exists.",
        }

    result = export_all_emass(project_id, output_dir=None, db_path=db_path)
    result["mode"] = "export"
    return result


# ============================================================
# Main sync orchestrator
# ============================================================

def sync_to_emass(project_id, mode="hybrid", db_path=None):
    """Main sync orchestrator between ICDEV and eMASS.

    Coordinates pushing compliance data (controls, POA&M, artifacts,
    test results) to eMASS using the specified mode.

    Modes:
        - ``api``: Push via eMASS REST API (EMASSClient). Requires
          PKI/CAC certificates and network access.
        - ``export``: Generate CSV/ZIP exports for manual upload.
          Works in air-gapped environments.
        - ``hybrid``: Try API first; if it fails, fall back to export.

    Results are recorded in:
        - ``emass_sync_log`` table (per-sync record)
        - ``emass_systems`` table (last_sync timestamp update)
        - ``audit_trail`` table (immutable audit event)

    Args:
        project_id: ICDEV project ID.
        mode: Sync mode -- ``"api"``, ``"export"``, or ``"hybrid"``
              (default: ``"hybrid"``).
        db_path: Database path (optional).

    Returns:
        Dict with comprehensive sync results including:
        - project_id, mode, status
        - data_counts (controls, poam, findings, scans)
        - results (per-mode details)
        - sync_start, sync_end timestamps
    """
    conn = _get_connection(db_path)
    sync_start = datetime.now(timezone.utc).isoformat() + "Z"

    try:
        _ensure_sync_tables(conn)

        # Load project data for summary counts
        data = _load_project_data(conn, project_id)

        summary = {
            "project_id": project_id,
            "mode": mode,
            "sync_start": sync_start,
            "classification": "CUI // SP-CTI",
            "data_counts": {
                "controls": len(data["controls"]),
                "poam_items": len(data["poam_items"]),
                "stig_findings": len(data["stig_findings"]),
                "vuln_scans": len(data["vuln_scans"]),
            },
            "results": {},
        }

        if mode == "api":
            summary["results"] = _sync_via_api(project_id, db_path)
            summary["mode_used"] = "api"

        elif mode == "export":
            summary["results"] = _sync_via_export(project_id, db_path)
            summary["mode_used"] = "export"

        elif mode == "hybrid":
            # Try API first
            api_result = _sync_via_api(project_id, db_path)
            summary["results"]["api"] = api_result

            # Fall back to export if API failed or flagged for fallback
            if api_result.get("fallback") or api_result.get("status") == "error":
                print("[eMASS Sync] API sync failed, falling back to export mode...")
                export_result = _sync_via_export(project_id, db_path)
                summary["results"]["export"] = export_result
                summary["mode_used"] = "export (fallback)"
            else:
                summary["mode_used"] = "api"
        else:
            raise ValueError(f"Unknown sync mode: {mode}. Must be api, export, or hybrid.")

        summary["sync_end"] = datetime.now(timezone.utc).isoformat() + "Z"
        summary["status"] = "completed"

        # Determine the effective result status
        effective_result = summary["results"]
        if "api" in effective_result and "export" in effective_result:
            # Hybrid with fallback -- use export status
            effective_status = effective_result.get("export", {}).get("status", "error")
        elif isinstance(effective_result, dict) and "status" in effective_result:
            effective_status = effective_result["status"]
        else:
            effective_status = "completed"

        # Write to emass_sync_log
        _log_sync(
            conn, project_id, mode, effective_status, sync_start,
            details=summary,
            controls=len(data["controls"]),
            poam=len(data["poam_items"]),
            artifacts=0,  # Counted during export
            test_results=len(data["stig_findings"]),
        )

        # Update emass_systems with last_sync
        _update_emass_system(
            conn, project_id,
            sync_status=effective_status,
            sync_mode=summary.get("mode_used", mode),
        )

        # Log to immutable audit trail
        _log_audit(conn, project_id, f"sync_completed_{mode}", {
            "mode": mode,
            "mode_used": summary.get("mode_used", mode),
            "data_counts": summary["data_counts"],
            "status": summary["status"],
        })

        return summary

    except Exception as e:
        error_result = {
            "project_id": project_id,
            "mode": mode,
            "status": "error",
            "error": str(e),
            "classification": "CUI // SP-CTI",
            "sync_start": sync_start,
            "sync_end": datetime.now(timezone.utc).isoformat() + "Z",
        }

        # Best-effort logging on failure
        try:
            _log_sync(conn, project_id, mode, "error", sync_start, error=str(e))
        except Exception:
            pass
        try:
            _update_emass_system(conn, project_id, sync_status="error", sync_mode=mode)
        except Exception:
            pass
        try:
            _log_audit(conn, project_id, f"sync_failed_{mode}", {"error": str(e)})
        except Exception:
            pass

        return error_result

    finally:
        conn.close()


# ============================================================
# Pull ATO status
# ============================================================

def pull_ato_status(project_id, db_path=None):
    """Pull ATO (Authorization to Operate) status from eMASS.

    Queries the eMASS REST API for the current authorization status
    of the project's registered system and updates the local
    ``emass_systems`` table.

    Args:
        project_id: ICDEV project ID.
        db_path: Database path (optional).

    Returns:
        Dict with authorization status fields:
        - authorizationStatus, authorizationDate,
          authorizationTerminationDate, authorizationType
    """
    try:
        from tools.compliance.emass.emass_client import EMASSClient
    except ImportError:
        return {
            "status": "error",
            "error": "EMASSClient import failed. Ensure emass_client.py exists.",
        }

    conn = _get_connection(db_path)
    try:
        _ensure_sync_tables(conn)

        # Resolve eMASS system ID
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")
        project = dict(project)

        # Check emass_systems for a mapped system ID
        emass_row = conn.execute(
            "SELECT emass_system_id FROM emass_systems WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        emass_system_id = None
        if emass_row:
            emass_system_id = emass_row["emass_system_id"]
        if not emass_system_id:
            emass_system_id = project.get("emass_system_id", project_id)

        client = EMASSClient(db_path=db_path)
        try:
            auth_result = client.get_authorization_status(emass_system_id)
        finally:
            client.close()

        if not auth_result or auth_result.get("status") == "error":
            error_msg = auth_result.get("error", "Unknown error") if auth_result else "No response"
            _log_audit(conn, project_id, "pull_ato_status_failed", {"error": error_msg})
            return {
                "status": "error",
                "project_id": project_id,
                "emass_system_id": emass_system_id,
                "error": error_msg,
            }

        # Update local emass_systems record
        _update_emass_system(
            conn, project_id,
            sync_status="success",
            sync_mode="api",
            emass_system_id=str(emass_system_id),
            auth_status=auth_result.get("authorizationStatus"),
            auth_date=auth_result.get("authorizationDate"),
            auth_term_date=auth_result.get("authorizationTerminationDate"),
        )

        result = {
            "status": "success",
            "project_id": project_id,
            "emass_system_id": emass_system_id,
            "authorizationStatus": auth_result.get("authorizationStatus", "Unknown"),
            "authorizationDate": auth_result.get("authorizationDate", ""),
            "authorizationTerminationDate": auth_result.get("authorizationTerminationDate", ""),
            "authorizationType": auth_result.get("authorizationType", ""),
            "systemLifecycle": auth_result.get("systemLifecycle", ""),
            "pulled_at": datetime.now(timezone.utc).isoformat() + "Z",
        }

        _log_audit(conn, project_id, "pull_ato_status", {
            "emass_system_id": str(emass_system_id),
            "authorization_status": result["authorizationStatus"],
        })

        print(f"[eMASS Sync] ATO Status for {project_id}: {result['authorizationStatus']}")
        return result

    finally:
        conn.close()


# ============================================================
# Sync history
# ============================================================

def get_sync_history(project_id, db_path=None):
    """Query the emass_sync_log table for past sync events.

    Returns all sync log entries for the given project, ordered by
    most recent first.

    Args:
        project_id: ICDEV project ID.
        db_path: Database path (optional).

    Returns:
        Dict with sync history entries and current emass_systems record.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_sync_tables(conn)

        # Sync log entries
        rows = conn.execute(
            """SELECT * FROM emass_sync_log
               WHERE project_id = ?
               ORDER BY started_at DESC""",
            (project_id,),
        ).fetchall()
        history = [dict(r) for r in rows]

        # Current system record
        sys_row = conn.execute(
            "SELECT * FROM emass_systems WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        system_record = dict(sys_row) if sys_row else None

        result = {
            "project_id": project_id,
            "classification": "CUI // SP-CTI",
            "sync_count": len(history),
            "system_record": system_record,
            "history": history,
        }

        return result

    finally:
        conn.close()


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    """CLI entry point for eMASS sync tool."""
    parser = argparse.ArgumentParser(
        description="Sync ICDEV compliance data to eMASS"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="ICDEV project ID",
    )
    parser.add_argument(
        "--mode", default="hybrid",
        choices=["api", "export", "hybrid"],
        help="Sync mode (default: hybrid -- try API, fall back to export)",
    )
    parser.add_argument(
        "--pull-ato", action="store_true",
        help="Pull ATO status from eMASS instead of pushing data",
    )
    parser.add_argument(
        "--history", action="store_true",
        help="Show sync history for the project",
    )
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH,
        help="Database path (default: data/icdev.db)",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.history:
        result = get_sync_history(args.project_id, args.db_path)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.pull_ato:
        result = pull_ato_status(args.project_id, args.db_path)
        print(json.dumps(result, indent=2, default=str))
        if result.get("status") == "error":
            sys.exit(1)
        return

    result = sync_to_emass(args.project_id, args.mode, args.db_path)
    print(json.dumps(result, indent=2, default=str))

    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

# CUI // SP-CTI
