#!/usr/bin/env python3
# ////////////////////////////////////////////////////////////////////
# CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
# Distribution: Distribution D -- Authorized DoD Personnel Only
# ////////////////////////////////////////////////////////////////////
"""Continuous ATO (cATO) monitoring engine.

Collects, tracks, and refreshes compliance evidence on a continuous basis
to support Continuous Authority to Operate workflows. Monitors evidence
freshness, computes cATO readiness scores, and triggers automatic
re-assessment of stale or expired evidence items.

Evidence is stored in the cato_evidence table of icdev.db and mapped
to NIST 800-53 controls. Each evidence item has an automation_frequency
that determines its expiration window and refresh cadence.

Database table: cato_evidence
  - id, project_id, control_id, evidence_type, evidence_source
  - evidence_path, evidence_hash, collected_at, expires_at
  - is_fresh, freshness_check_at, status, automation_frequency
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Evidence type constants
EVIDENCE_TYPES = (
    "scan_result", "test_result", "config_check",
    "manual_review", "attestation", "artifact",
)

# Status constants
EVIDENCE_STATUSES = ("current", "stale", "expired", "superseded")

# Automation frequency constants
AUTOMATION_FREQUENCIES = (
    "continuous", "daily", "weekly", "monthly", "per_change", "manual",
)

# Expiration windows (in days) by automation frequency
EXPIRY_WINDOWS = {
    "continuous": 1,
    "daily": 2,
    "weekly": 14,
    "monthly": 45,
    "per_change": 30,
    "manual": 90,
}

# Staleness threshold: evidence is stale when 80% of its expiry window has elapsed
STALENESS_RATIO = 0.80


def _get_connection(db_path=None):
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _hash_file(file_path):
    """Compute SHA-256 hash of a file, reading in 8KB chunks.

    Returns:
        Hex digest string, or None if the file cannot be read.
    """
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except (OSError, PermissionError):
        return None


def _log_audit_event(conn, project_id, action, details):
    """Log an audit trail event for cATO evidence collection.

    Appends to the audit_trail table (append-only, NIST AU compliant).
    """
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "cato_evidence_collected",
                "icdev-cato-monitor",
                action,
                json.dumps(details, default=str),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def _verify_project(conn, project_id):
    """Verify project exists in the database.

    Returns:
        Dict of project row data.

    Raises:
        ValueError if project not found.
    """
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _compute_expires_at(collected_at_str, automation_frequency):
    """Compute the expiration datetime for evidence based on its frequency.

    Args:
        collected_at_str: ISO-format datetime string of collection time.
        automation_frequency: One of AUTOMATION_FREQUENCIES.

    Returns:
        ISO-format datetime string for expiration.
    """
    try:
        collected_at = datetime.fromisoformat(collected_at_str)
    except (ValueError, TypeError):
        collected_at = datetime.utcnow()

    days = EXPIRY_WINDOWS.get(automation_frequency, 90)
    expires_at = collected_at + timedelta(days=days)
    return expires_at.isoformat()


# --------------------------------------------------------------------------
# Public API functions
# --------------------------------------------------------------------------

def collect_evidence(
    project_id,
    control_id,
    evidence_type,
    evidence_source,
    evidence_path=None,
    automation_frequency="manual",
    db_path=None,
):
    """Collect and store evidence for a NIST 800-53 control.

    Creates or updates a cato_evidence record. If evidence_path points to an
    existing file, its SHA-256 hash is computed and stored. The expires_at
    timestamp is set based on the automation_frequency.

    Args:
        project_id: Project identifier.
        control_id: NIST 800-53 control ID (e.g. 'AC-2', 'AU-6').
        evidence_type: One of EVIDENCE_TYPES.
        evidence_source: Descriptive source label (e.g. 'bandit_sast', 'pytest').
        evidence_path: Optional filesystem path to evidence artifact.
        automation_frequency: One of AUTOMATION_FREQUENCIES.
        db_path: Optional database path override.

    Returns:
        Dict with evidence_id, status, collected_at, expires_at, evidence_hash.
    """
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(
            f"Invalid evidence_type '{evidence_type}'. "
            f"Valid types: {EVIDENCE_TYPES}"
        )
    if automation_frequency not in AUTOMATION_FREQUENCIES:
        raise ValueError(
            f"Invalid automation_frequency '{automation_frequency}'. "
            f"Valid frequencies: {AUTOMATION_FREQUENCIES}"
        )

    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        now = datetime.utcnow()
        collected_at = now.isoformat()
        expires_at = _compute_expires_at(collected_at, automation_frequency)

        # Compute file hash if path provided and file exists
        evidence_hash = None
        if evidence_path and Path(evidence_path).is_file():
            evidence_hash = _hash_file(evidence_path)

        # Upsert: the table has UNIQUE(project_id, control_id, evidence_type, evidence_source)
        existing = conn.execute(
            """SELECT id FROM cato_evidence
               WHERE project_id = ? AND control_id = ?
               AND evidence_type = ? AND evidence_source = ?""",
            (project_id, control_id, evidence_type, evidence_source),
        ).fetchone()

        if existing:
            # Mark old record as superseded if hash changed, else just refresh
            old_row = conn.execute(
                "SELECT evidence_hash, status FROM cato_evidence WHERE id = ?",
                (existing["id"],),
            ).fetchone()

            conn.execute(
                """UPDATE cato_evidence
                   SET evidence_path = ?,
                       evidence_hash = ?,
                       collected_at = ?,
                       expires_at = ?,
                       is_fresh = 1,
                       freshness_check_at = ?,
                       status = 'current',
                       automation_frequency = ?
                   WHERE id = ?""",
                (
                    str(evidence_path) if evidence_path else None,
                    evidence_hash,
                    collected_at,
                    expires_at,
                    collected_at,
                    automation_frequency,
                    existing["id"],
                ),
            )
            conn.commit()
            evidence_id = existing["id"]
            action = "Evidence refreshed"
        else:
            cursor = conn.execute(
                """INSERT INTO cato_evidence
                   (project_id, control_id, evidence_type, evidence_source,
                    evidence_path, evidence_hash, collected_at, expires_at,
                    is_fresh, freshness_check_at, status, automation_frequency)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'current', ?)""",
                (
                    project_id, control_id, evidence_type, evidence_source,
                    str(evidence_path) if evidence_path else None,
                    evidence_hash, collected_at, expires_at,
                    collected_at, automation_frequency,
                ),
            )
            conn.commit()
            evidence_id = cursor.lastrowid
            action = "Evidence collected"

        # Audit trail
        _log_audit_event(conn, project_id, action, {
            "evidence_id": evidence_id,
            "control_id": control_id,
            "evidence_type": evidence_type,
            "evidence_source": evidence_source,
            "automation_frequency": automation_frequency,
            "expires_at": expires_at,
        })

        result = {
            "evidence_id": evidence_id,
            "control_id": control_id,
            "evidence_type": evidence_type,
            "evidence_source": evidence_source,
            "status": "current",
            "collected_at": collected_at,
            "expires_at": expires_at,
            "evidence_hash": evidence_hash,
        }

        print(f"cATO evidence {action.lower()}: {control_id} "
              f"[{evidence_type}] from {evidence_source}")

        return result

    finally:
        conn.close()


def check_evidence_freshness(project_id, db_path=None):
    """Check all evidence for staleness and expiration.

    Iterates over all cato_evidence records for the project and updates
    their status based on the current time relative to expires_at:
      - 'expired' if now >= expires_at
      - 'stale' if now >= collected_at + (STALENESS_RATIO * expiry_window)
      - 'current' otherwise

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with total, current, stale, expired counts and by_control breakdown.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        rows = conn.execute(
            """SELECT id, control_id, evidence_type, evidence_source,
                      collected_at, expires_at, status, automation_frequency
               FROM cato_evidence
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        now = datetime.utcnow()
        now_str = now.isoformat()
        summary = {
            "total": len(rows),
            "current": 0,
            "stale": 0,
            "expired": 0,
            "by_control": {},
        }

        for row in rows:
            row_id = row["id"]
            control_id = row["control_id"]
            collected_at_str = row["collected_at"]
            expires_at_str = row["expires_at"]
            freq = row["automation_frequency"] or "manual"

            try:
                collected_at = datetime.fromisoformat(collected_at_str)
            except (ValueError, TypeError):
                collected_at = now - timedelta(days=365)

            try:
                expires_at = datetime.fromisoformat(expires_at_str)
            except (ValueError, TypeError):
                expires_at = now - timedelta(days=1)

            # Determine new status
            if now >= expires_at:
                new_status = "expired"
                is_fresh = 0
            else:
                # Check staleness: 80% of expiry window elapsed
                window_days = EXPIRY_WINDOWS.get(freq, 90)
                stale_threshold = collected_at + timedelta(
                    days=window_days * STALENESS_RATIO
                )
                if now >= stale_threshold:
                    new_status = "stale"
                    is_fresh = 0
                else:
                    new_status = "current"
                    is_fresh = 1

            # Update record
            conn.execute(
                """UPDATE cato_evidence
                   SET status = ?, is_fresh = ?, freshness_check_at = ?
                   WHERE id = ?""",
                (new_status, is_fresh, now_str, row_id),
            )

            # Tally
            summary[new_status] = summary.get(new_status, 0) + 1

            if control_id not in summary["by_control"]:
                summary["by_control"][control_id] = {
                    "current": 0, "stale": 0, "expired": 0,
                }
            summary["by_control"][control_id][new_status] += 1

        conn.commit()

        # Audit trail
        _log_audit_event(conn, project_id, "Freshness check completed", {
            "total": summary["total"],
            "current": summary["current"],
            "stale": summary["stale"],
            "expired": summary["expired"],
        })

        print(f"cATO freshness check: {summary['total']} items checked")
        print(f"  Current: {summary['current']}  Stale: {summary['stale']}  "
              f"Expired: {summary['expired']}")

        return summary

    finally:
        conn.close()


def auto_reassess(project_id, project_dir=None, db_path=None):
    """Automatically re-assess controls with stale or expired evidence.

    For each stale/expired evidence item, attempts to re-collect evidence
    by checking for updated artifacts:
      - scan_result: look for SAST scan output files
      - test_result: look for pytest/test result files
      - artifact: look for SBOM files
      - config_check: look for STIG finding records in DB

    Args:
        project_id: Project identifier.
        project_dir: Optional project directory for file-based checks.
        db_path: Optional database path override.

    Returns:
        List of dicts describing controls that were re-assessed.
    """
    conn = _get_connection(db_path)
    try:
        project = _verify_project(conn, project_id)

        # Determine scan directory
        if project_dir:
            scan_dir = Path(project_dir)
        else:
            dir_path = project.get("directory_path", "")
            scan_dir = Path(dir_path) if dir_path else None

        can_scan = scan_dir is not None and scan_dir.is_dir()

        # Find stale and expired evidence
        rows = conn.execute(
            """SELECT id, control_id, evidence_type, evidence_source,
                      evidence_path, automation_frequency
               FROM cato_evidence
               WHERE project_id = ? AND status IN ('stale', 'expired')
               ORDER BY control_id""",
            (project_id,),
        ).fetchall()

        reassessed = []

        for row in rows:
            evidence_id = row["id"]
            control_id = row["control_id"]
            evidence_type = row["evidence_type"]
            evidence_source = row["evidence_source"]
            evidence_path = row["evidence_path"]
            freq = row["automation_frequency"] or "manual"

            refreshed = False
            new_hash = None
            new_path = evidence_path

            # Attempt re-collection based on evidence type
            if evidence_type == "scan_result" and can_scan:
                # Look for SAST scan result files
                for pattern_dir in ["security", "compliance", "reports"]:
                    check_dir = scan_dir / pattern_dir
                    if check_dir.is_dir():
                        for f in sorted(check_dir.iterdir(), reverse=True):
                            if f.is_file() and "sast" in f.name.lower():
                                new_hash = _hash_file(f)
                                new_path = str(f)
                                refreshed = True
                                break
                    if refreshed:
                        break

            elif evidence_type == "test_result" and can_scan:
                # Look for test result files (pytest output, junit XML)
                for pattern in ["test-results", "reports", "."]:
                    check_dir = scan_dir / pattern if pattern != "." else scan_dir
                    if check_dir.is_dir():
                        for f in sorted(check_dir.iterdir(), reverse=True):
                            if f.is_file() and (
                                "test" in f.name.lower() or
                                "junit" in f.name.lower()
                            ) and f.suffix in (".xml", ".json", ".html"):
                                new_hash = _hash_file(f)
                                new_path = str(f)
                                refreshed = True
                                break
                    if refreshed:
                        break

            elif evidence_type == "artifact" and can_scan:
                # Look for SBOM or other artifact files
                for pattern_dir in ["compliance", "sbom", "reports", "."]:
                    check_dir = scan_dir / pattern_dir if pattern_dir != "." else scan_dir
                    if check_dir.is_dir():
                        for f in sorted(check_dir.iterdir(), reverse=True):
                            if f.is_file() and (
                                "sbom" in f.name.lower() or
                                "bom" in f.name.lower()
                            ):
                                new_hash = _hash_file(f)
                                new_path = str(f)
                                refreshed = True
                                break
                    if refreshed:
                        break

            elif evidence_type == "config_check":
                # Check DB for recent STIG findings as config evidence
                try:
                    stig_row = conn.execute(
                        """SELECT COUNT(*) as cnt FROM stig_findings
                           WHERE project_id = ?
                           AND assessed_at > datetime('now', '-7 days')""",
                        (project_id,),
                    ).fetchone()
                    if stig_row and stig_row["cnt"] > 0:
                        refreshed = True
                        new_path = None
                        new_hash = None
                except sqlite3.OperationalError:
                    pass

            elif evidence_path and Path(evidence_path).is_file():
                # For any evidence type, if the file still exists, re-hash it
                current_hash = _hash_file(evidence_path)
                if current_hash:
                    new_hash = current_hash
                    refreshed = True

            if refreshed:
                now = datetime.utcnow()
                collected_at = now.isoformat()
                expires_at = _compute_expires_at(collected_at, freq)

                conn.execute(
                    """UPDATE cato_evidence
                       SET evidence_path = ?,
                           evidence_hash = ?,
                           collected_at = ?,
                           expires_at = ?,
                           is_fresh = 1,
                           freshness_check_at = ?,
                           status = 'current'
                       WHERE id = ?""",
                    (
                        new_path, new_hash,
                        collected_at, expires_at,
                        collected_at, evidence_id,
                    ),
                )

                reassessed.append({
                    "evidence_id": evidence_id,
                    "control_id": control_id,
                    "evidence_type": evidence_type,
                    "evidence_source": evidence_source,
                    "new_status": "current",
                    "collected_at": collected_at,
                    "expires_at": expires_at,
                })

        conn.commit()

        # Audit trail
        _log_audit_event(conn, project_id, "Auto-reassessment completed", {
            "stale_expired_checked": len(rows),
            "reassessed": len(reassessed),
            "controls_refreshed": list(set(r["control_id"] for r in reassessed)),
        })

        print(f"cATO auto-reassess: {len(rows)} stale/expired items checked, "
              f"{len(reassessed)} refreshed")
        for r in reassessed:
            print(f"  Refreshed: {r['control_id']} [{r['evidence_type']}] "
                  f"from {r['evidence_source']}")

        return reassessed

    finally:
        conn.close()


def compute_cato_readiness(project_id, db_path=None):
    """Compute cATO readiness score for a project.

    Calculates the percentage of controls with fresh, current evidence
    and the percentage of evidence collection that is automated.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with total_controls, controls_with_evidence,
        controls_with_fresh_evidence, readiness_pct, automated_pct,
        and by_frequency breakdown.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        # Get all evidence records
        rows = conn.execute(
            """SELECT control_id, evidence_type, status, is_fresh,
                      automation_frequency
               FROM cato_evidence
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        if not rows:
            return {
                "total_controls": 0,
                "controls_with_evidence": 0,
                "controls_with_fresh_evidence": 0,
                "readiness_pct": 0.0,
                "automated_pct": 0.0,
                "total_evidence_items": 0,
                "by_frequency": {},
            }

        # Gather distinct controls
        all_controls = set()
        controls_with_evidence = set()
        controls_with_fresh = set()
        freq_counts = {}
        automated_count = 0

        for row in rows:
            control_id = row["control_id"]
            status = row["status"]
            is_fresh = row["is_fresh"]
            freq = row["automation_frequency"] or "manual"

            all_controls.add(control_id)
            controls_with_evidence.add(control_id)

            if status == "current" and is_fresh:
                controls_with_fresh.add(control_id)

            # Track frequency distribution
            freq_counts[freq] = freq_counts.get(freq, 0) + 1

            # Automated = anything that is not 'manual'
            if freq != "manual":
                automated_count += 1

        total_controls = len(all_controls)
        total_evidence = len(rows)

        # Also check project_controls for total mapped controls
        try:
            ctrl_row = conn.execute(
                "SELECT COUNT(DISTINCT control_id) as cnt FROM project_controls WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            mapped_controls = ctrl_row["cnt"] if ctrl_row else 0
            if mapped_controls > total_controls:
                total_controls = mapped_controls
        except sqlite3.OperationalError:
            pass

        readiness_pct = 0.0
        if total_controls > 0:
            readiness_pct = round(
                len(controls_with_fresh) / total_controls * 100, 1
            )

        automated_pct = 0.0
        if total_evidence > 0:
            automated_pct = round(automated_count / total_evidence * 100, 1)

        result = {
            "total_controls": total_controls,
            "controls_with_evidence": len(controls_with_evidence),
            "controls_with_fresh_evidence": len(controls_with_fresh),
            "readiness_pct": readiness_pct,
            "automated_pct": automated_pct,
            "total_evidence_items": total_evidence,
            "by_frequency": freq_counts,
        }

        print(f"cATO readiness: {readiness_pct}% "
              f"({len(controls_with_fresh)}/{total_controls} controls fresh)")
        print(f"  Automation: {automated_pct}% of evidence is automated")

        return result

    finally:
        conn.close()


def get_cato_dashboard_data(project_id, db_path=None):
    """Get comprehensive cATO dashboard data for display.

    Aggregates readiness score, evidence freshness chart data,
    upcoming expirations, controls needing attention, and trend data.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with readiness, freshness_chart, upcoming_expirations,
        controls_needing_attention, and trend sections.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        # --- Readiness score ---
        readiness = compute_cato_readiness(project_id, db_path=db_path)

        # --- Freshness chart data ---
        rows = conn.execute(
            """SELECT status, COUNT(*) as cnt
               FROM cato_evidence
               WHERE project_id = ?
               GROUP BY status""",
            (project_id,),
        ).fetchall()

        freshness_chart = {
            "current": 0, "stale": 0, "expired": 0, "superseded": 0,
        }
        for row in rows:
            freshness_chart[row["status"]] = row["cnt"]

        # --- Upcoming expirations (next 30 days) ---
        cutoff = (datetime.utcnow() + timedelta(days=30)).isoformat()
        now_str = datetime.utcnow().isoformat()

        expiring_rows = conn.execute(
            """SELECT id, control_id, evidence_type, evidence_source,
                      expires_at, automation_frequency, status
               FROM cato_evidence
               WHERE project_id = ?
               AND expires_at <= ?
               AND expires_at > ?
               AND status != 'expired'
               ORDER BY expires_at ASC""",
            (project_id, cutoff, now_str),
        ).fetchall()

        upcoming_expirations = []
        for row in expiring_rows:
            try:
                exp_dt = datetime.fromisoformat(row["expires_at"])
                days_until = (exp_dt - datetime.utcnow()).days
            except (ValueError, TypeError):
                days_until = -1

            upcoming_expirations.append({
                "evidence_id": row["id"],
                "control_id": row["control_id"],
                "evidence_type": row["evidence_type"],
                "evidence_source": row["evidence_source"],
                "expires_at": row["expires_at"],
                "days_until_expiry": days_until,
                "automation_frequency": row["automation_frequency"],
                "status": row["status"],
            })

        # --- Controls needing attention ---
        attention_rows = conn.execute(
            """SELECT DISTINCT control_id, status, evidence_type, evidence_source,
                      expires_at
               FROM cato_evidence
               WHERE project_id = ? AND status IN ('stale', 'expired')
               ORDER BY status DESC, control_id""",
            (project_id,),
        ).fetchall()

        controls_needing_attention = []
        for row in attention_rows:
            controls_needing_attention.append({
                "control_id": row["control_id"],
                "status": row["status"],
                "evidence_type": row["evidence_type"],
                "evidence_source": row["evidence_source"],
                "expires_at": row["expires_at"],
            })

        # --- Trend data: evidence collected per day (last 30 days) ---
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()

        trend_rows = conn.execute(
            """SELECT DATE(collected_at) as day, COUNT(*) as cnt
               FROM cato_evidence
               WHERE project_id = ? AND collected_at >= ?
               GROUP BY DATE(collected_at)
               ORDER BY day""",
            (project_id, thirty_days_ago),
        ).fetchall()

        trend_data = [
            {"date": row["day"], "evidence_collected": row["cnt"]}
            for row in trend_rows
        ]

        # --- Evidence by type distribution ---
        type_rows = conn.execute(
            """SELECT evidence_type, COUNT(*) as cnt
               FROM cato_evidence
               WHERE project_id = ?
               GROUP BY evidence_type""",
            (project_id,),
        ).fetchall()

        evidence_by_type = {row["evidence_type"]: row["cnt"] for row in type_rows}

        # --- ZTA posture (ADR D123) ---
        zta_posture = check_zta_posture(project_id, db_path=db_path)

        # --- MOSA evidence (D130, optional) ---
        mosa_evidence = collect_mosa_evidence(project_id, db_path=db_path)

        result = {
            "project_id": project_id,
            "generated_at": datetime.utcnow().isoformat(),
            "readiness": readiness,
            "freshness_chart": freshness_chart,
            "upcoming_expirations": upcoming_expirations,
            "controls_needing_attention": controls_needing_attention,
            "trend_data": trend_data,
            "evidence_by_type": evidence_by_type,
            "zta_posture": zta_posture,
            "mosa_evidence": mosa_evidence,
        }

        print(f"cATO dashboard data generated for project {project_id}")
        print(f"  Readiness: {readiness['readiness_pct']}%")
        print(f"  Upcoming expirations (30d): {len(upcoming_expirations)}")
        print(f"  Controls needing attention: {len(controls_needing_attention)}")

        return result

    finally:
        conn.close()


def expire_old_evidence(project_id, db_path=None):
    """Mark all past-due evidence as 'expired'.

    Scans all non-expired evidence and marks any items where
    the current time has passed their expires_at timestamp.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with expired_count and list of expired evidence IDs.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        now = datetime.utcnow()
        now_str = now.isoformat()

        # Find all evidence that should be expired
        rows = conn.execute(
            """SELECT id, control_id, evidence_type, evidence_source, expires_at
               FROM cato_evidence
               WHERE project_id = ?
               AND status NOT IN ('expired', 'superseded')
               AND expires_at <= ?""",
            (project_id, now_str),
        ).fetchall()

        expired_ids = []
        for row in rows:
            conn.execute(
                """UPDATE cato_evidence
                   SET status = 'expired', is_fresh = 0, freshness_check_at = ?
                   WHERE id = ?""",
                (now_str, row["id"]),
            )
            expired_ids.append(row["id"])

        conn.commit()

        # Audit trail
        if expired_ids:
            _log_audit_event(conn, project_id, "Evidence expired", {
                "expired_count": len(expired_ids),
                "expired_ids": expired_ids,
            })

        print(f"cATO expire: {len(expired_ids)} evidence items marked as expired")

        return {
            "expired_count": len(expired_ids),
            "expired_ids": expired_ids,
        }

    finally:
        conn.close()


def check_zta_posture(project_id, db_path=None):
    """Check ZTA posture and include as cATO evidence dimension.

    Queries the zta_maturity_scores and zta_posture_evidence tables to
    compute a ZTA posture summary. The ZTA maturity score feeds into
    cATO readiness as an additional evidence dimension (ADR D123).

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with zta_maturity, pillar_scores, posture_evidence_freshness,
        and cato_contribution.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        result = {
            "project_id": project_id,
            "zta_available": False,
            "overall_maturity": "traditional",
            "overall_score": 0.0,
            "pillar_scores": {},
            "posture_evidence": {"total": 0, "current": 0, "stale": 0, "expired": 0},
            "cato_contribution": 0.0,
        }

        # Query ZTA maturity scores
        try:
            maturity_rows = conn.execute(
                """SELECT pillar, score, maturity_level
                   FROM zta_maturity_scores
                   WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()

            if maturity_rows:
                result["zta_available"] = True
                for row in maturity_rows:
                    pillar = row["pillar"]
                    if pillar == "overall":
                        result["overall_score"] = row["score"] or 0.0
                        result["overall_maturity"] = row["maturity_level"] or "traditional"
                    else:
                        result["pillar_scores"][pillar] = {
                            "score": row["score"] or 0.0,
                            "maturity_level": row["maturity_level"] or "traditional",
                        }
        except sqlite3.OperationalError:
            pass  # Table may not exist yet

        # Query ZTA posture evidence freshness
        try:
            posture_rows = conn.execute(
                """SELECT status, COUNT(*) as cnt
                   FROM zta_posture_evidence
                   WHERE project_id = ?
                   GROUP BY status""",
                (project_id,),
            ).fetchall()

            for row in posture_rows:
                status = row["status"]
                if status in result["posture_evidence"]:
                    result["posture_evidence"][status] = row["cnt"]
                result["posture_evidence"]["total"] += row["cnt"]
        except sqlite3.OperationalError:
            pass  # Table may not exist yet

        # Compute cATO contribution: ZTA maturity score scaled to 0-100
        if result["zta_available"]:
            result["cato_contribution"] = round(result["overall_score"] * 100, 1)

        print(f"ZTA posture check: maturity={result['overall_maturity']} "
              f"score={result['overall_score']:.2f} "
              f"evidence={result['posture_evidence']['total']} items")

        return result

    finally:
        conn.close()


def collect_mosa_evidence(project_id, db_path=None):
    """Collect MOSA architecture review evidence for cATO (D130).

    Queries mosa_modularity_metrics and mosa_assessments tables to build
    an evidence summary for controls SA-3, SA-8, SA-17. Only runs when
    mosa_config.yaml has cato_integration.enabled = true.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with mosa_available, modularity_score, icd_coverage,
        tsp_current, mapped_controls, and cato_contribution.
    """
    # Check config flag
    config_path = Path(__file__).resolve().parent.parent.parent / "args" / "mosa_config.yaml"
    mosa_enabled = False
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            mosa_enabled = cfg.get("mosa", {}).get("cato_integration", {}).get("enabled", False)
        except Exception:
            pass

    if not mosa_enabled:
        return {"project_id": project_id, "mosa_available": False,
                "reason": "cato_integration.enabled is false in mosa_config.yaml"}

    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)
        result = {
            "project_id": project_id,
            "mosa_available": False,
            "modularity_score": 0.0,
            "icd_coverage": {"approved": 0, "total_required": 0, "pct": 0.0},
            "tsp_current": False,
            "mapped_controls": ["SA-3", "SA-8", "SA-17"],
            "cato_contribution": 0.0,
        }

        try:
            metrics = conn.execute(
                """SELECT overall_modularity_score, approved_icd_count,
                          total_icd_required, tsp_current
                   FROM mosa_modularity_metrics
                   WHERE project_id = ?
                   ORDER BY assessment_date DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
            if metrics:
                result["mosa_available"] = True
                result["modularity_score"] = metrics["overall_modularity_score"] or 0.0
                result["icd_coverage"]["approved"] = metrics["approved_icd_count"] or 0
                result["icd_coverage"]["total_required"] = metrics["total_icd_required"] or 0
                if metrics["total_icd_required"]:
                    result["icd_coverage"]["pct"] = round(
                        (metrics["approved_icd_count"] or 0) / metrics["total_icd_required"] * 100, 1)
                result["tsp_current"] = bool(metrics["tsp_current"])
                result["cato_contribution"] = round(result["modularity_score"] * 100, 1)
        except Exception:
            pass

        print(f"MOSA evidence check: available={result['mosa_available']} "
              f"modularity={result['modularity_score']:.2f} "
              f"ICD={result['icd_coverage']['approved']}/{result['icd_coverage']['total_required']}")
        return result
    finally:
        conn.close()


def get_evidence_for_control(project_id, control_id, db_path=None):
    """Get all evidence items for a specific control.

    Args:
        project_id: Project identifier.
        control_id: NIST 800-53 control ID (e.g. 'AC-2').
        db_path: Optional database path override.

    Returns:
        List of dicts with evidence details for the specified control.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        rows = conn.execute(
            """SELECT id, control_id, evidence_type, evidence_source,
                      evidence_path, evidence_hash, collected_at, expires_at,
                      is_fresh, freshness_check_at, status, automation_frequency
               FROM cato_evidence
               WHERE project_id = ? AND control_id = ?
               ORDER BY collected_at DESC""",
            (project_id, control_id),
        ).fetchall()

        results = []
        for row in rows:
            results.append({
                "evidence_id": row["id"],
                "control_id": row["control_id"],
                "evidence_type": row["evidence_type"],
                "evidence_source": row["evidence_source"],
                "evidence_path": row["evidence_path"],
                "evidence_hash": row["evidence_hash"],
                "collected_at": row["collected_at"],
                "expires_at": row["expires_at"],
                "is_fresh": bool(row["is_fresh"]),
                "freshness_check_at": row["freshness_check_at"],
                "status": row["status"],
                "automation_frequency": row["automation_frequency"],
            })

        print(f"cATO evidence for {control_id}: {len(results)} items found")
        return results

    finally:
        conn.close()


# --------------------------------------------------------------------------
# CLI formatting helpers
# --------------------------------------------------------------------------

def _format_readiness_report(readiness):
    """Format readiness data as a console report."""
    lines = [
        "=" * 65,
        "  cATO READINESS REPORT",
        "=" * 65,
        "",
        f"  Total controls tracked:       {readiness['total_controls']}",
        f"  Controls with evidence:        {readiness['controls_with_evidence']}",
        f"  Controls with FRESH evidence:  {readiness['controls_with_fresh_evidence']}",
        "",
        f"  Readiness Score:  {readiness['readiness_pct']}%",
        f"  Automation Rate:  {readiness['automated_pct']}%",
        "",
        "  Evidence by Automation Frequency:",
    ]

    for freq, count in sorted(readiness.get("by_frequency", {}).items()):
        lines.append(f"    {freq:<15} {count} items")

    lines.append("")
    lines.append("=" * 65)
    return "\n".join(lines)


def _format_dashboard_report(dashboard):
    """Format dashboard data as a console report."""
    readiness = dashboard.get("readiness", {})
    freshness = dashboard.get("freshness_chart", {})
    upcoming = dashboard.get("upcoming_expirations", [])
    attention = dashboard.get("controls_needing_attention", [])

    lines = [
        "=" * 65,
        "  cATO MONITORING DASHBOARD",
        "=" * 65,
        f"  Project: {dashboard.get('project_id', 'N/A')}",
        f"  Generated: {dashboard.get('generated_at', 'N/A')}",
        "",
        "  --- Readiness ---",
        f"  Score:       {readiness.get('readiness_pct', 0)}%",
        f"  Automation:  {readiness.get('automated_pct', 0)}%",
        "",
        "  --- Evidence Freshness ---",
        f"  Current:     {freshness.get('current', 0)}",
        f"  Stale:       {freshness.get('stale', 0)}",
        f"  Expired:     {freshness.get('expired', 0)}",
        f"  Superseded:  {freshness.get('superseded', 0)}",
        "",
    ]

    # Evidence by type
    by_type = dashboard.get("evidence_by_type", {})
    if by_type:
        lines.append("  --- Evidence by Type ---")
        for etype, cnt in sorted(by_type.items()):
            lines.append(f"    {etype:<18} {cnt}")
        lines.append("")

    # Upcoming expirations
    if upcoming:
        lines.append(f"  --- Upcoming Expirations (next 30 days): {len(upcoming)} ---")
        for item in upcoming[:15]:
            lines.append(
                f"    {item['control_id']:<10} {item['evidence_type']:<16} "
                f"expires in {item['days_until_expiry']}d  [{item['automation_frequency']}]"
            )
        if len(upcoming) > 15:
            lines.append(f"    ... and {len(upcoming) - 15} more")
        lines.append("")

    # Controls needing attention
    if attention:
        lines.append(f"  --- Controls Needing Attention: {len(attention)} ---")
        for item in attention[:15]:
            lines.append(
                f"    {item['control_id']:<10} {item['evidence_type']:<16} "
                f"status={item['status']}"
            )
        if len(attention) > 15:
            lines.append(f"    ... and {len(attention) - 15} more")
        lines.append("")

    lines.append("=" * 65)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

def main():
    """CLI entry point for cATO monitoring engine."""
    parser = argparse.ArgumentParser(
        description="Continuous ATO (cATO) monitoring engine"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="Project ID in ICDEV database"
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override database path"
    )
    parser.add_argument(
        "--project-dir", type=Path, default=None,
        help="Project directory for auto-reassessment file checks"
    )

    # Action flags (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check-freshness", action="store_true",
        help="Check all evidence for staleness and expiration"
    )
    group.add_argument(
        "--auto-reassess", action="store_true",
        help="Auto-reassess stale/expired evidence"
    )
    group.add_argument(
        "--readiness", action="store_true",
        help="Compute cATO readiness score"
    )
    group.add_argument(
        "--dashboard", action="store_true",
        help="Generate dashboard data"
    )
    group.add_argument(
        "--expire", action="store_true",
        help="Expire all past-due evidence"
    )
    group.add_argument(
        "--control", type=str, default=None,
        help="Get evidence for a specific control ID"
    )
    group.add_argument(
        "--zta-posture", action="store_true",
        help="Check ZTA posture for cATO readiness (ADR D123)"
    )
    group.add_argument(
        "--mosa-evidence", action="store_true",
        help="Collect MOSA architecture evidence for cATO (D130)"
    )

    # Output format
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    try:
        if args.check_freshness:
            result = check_evidence_freshness(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.auto_reassess:
            result = auto_reassess(
                project_id=args.project_id,
                project_dir=args.project_dir,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if not result:
                    print("No evidence items could be refreshed.")

        elif args.readiness:
            result = compute_cato_readiness(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_readiness_report(result))

        elif args.dashboard:
            result = get_cato_dashboard_data(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_dashboard_report(result))

        elif args.expire:
            result = expire_old_evidence(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.control:
            result = get_evidence_for_control(
                project_id=args.project_id,
                control_id=args.control,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if not result:
                    print(f"No evidence found for control {args.control}")
                else:
                    for item in result:
                        print(f"  [{item['status']}] {item['evidence_type']} "
                              f"from {item['evidence_source']} "
                              f"(collected {item['collected_at']}, "
                              f"expires {item['expires_at']})")

        elif args.zta_posture:
            result = check_zta_posture(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"ZTA Posture for {args.project_id}:")
                print(f"  Available:  {result['zta_available']}")
                print(f"  Maturity:   {result['overall_maturity']}")
                print(f"  Score:      {result['overall_score']:.2f}")
                print(f"  Evidence:   {result['posture_evidence']['total']} items "
                      f"({result['posture_evidence']['current']} current)")
                if result['pillar_scores']:
                    print("  Pillar Scores:")
                    for pillar, data in result['pillar_scores'].items():
                        print(f"    {pillar:<30} {data['score']:.2f} ({data['maturity_level']})")

        elif args.mosa_evidence:
            result = collect_mosa_evidence(
                project_id=args.project_id,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"MOSA Evidence for {args.project_id}:")
                print(f"  Available:       {result['mosa_available']}")
                if result['mosa_available']:
                    print(f"  Modularity:      {result['modularity_score']:.2f}")
                    print(f"  ICD Coverage:    {result['icd_coverage']['approved']}"
                          f"/{result['icd_coverage']['total_required']}"
                          f" ({result['icd_coverage']['pct']}%)")
                    print(f"  TSP Current:     {result['tsp_current']}")
                    print(f"  Mapped Controls: {', '.join(result['mapped_controls'])}")
                    print(f"  cATO Score:      {result['cato_contribution']}")
                else:
                    print(f"  Reason: {result.get('reason', 'No metrics found')}")

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
