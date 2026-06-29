#!/usr/bin/env python3
# CUI // SP-CTI
"""File Sync Engine — main orchestrator and CLI entry point.

Syncthing-inspired file/folder synchronization with SHA-256 change detection,
configurable conflict resolution, ignore patterns, and optional file watching.

Architecture: D-SYNC-1 through D-SYNC-12.
Pattern: tools/creative/creative_engine.py (daemon, CLI, YAML config).
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "filesync_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

from tools.filesync.change_detector import (  # noqa: E402
    detect_changes_bidirectional,
    detect_changes_push,
    summarize_actions,
)
from tools.filesync.conflict_resolver import resolve_conflicts  # noqa: E402
from tools.filesync.providers.local import LocalSyncProvider  # noqa: E402
from tools.filesync.scanner import compute_file_hash, configure_hash, scan_directory  # noqa: E402
from tools.filesync.transfer import execute_actions  # noqa: E402
from tools.filesync.versioner import FileVersioner  # noqa: E402


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_print(msg):
    """Print with Unicode safety on Windows (charmap codec)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def _gen_id(prefix="fsync"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db(db_path=None):
    path = db_path or DB_PATH
    conn = get_connection(db_path=str(path))
    return conn


def _load_config():
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("filesync", {})


def _audit(event_type, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="filesync-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="filesync",
            )
        except Exception:
            pass


def _get_provider(provider_name: str, config: dict = None):
    """Get sync target provider by name."""
    if provider_name == "local":
        return LocalSyncProvider()
    elif provider_name == "sftp":
        try:
            from tools.filesync.providers.sftp import SFTPSyncProvider

            return SFTPSyncProvider(config or {})
        except ImportError:
            raise ValueError("SFTP provider requires paramiko or ssh command")
    elif provider_name in ("s3", "azure", "gcs"):
        try:
            from tools.filesync.providers.cloud import CloudSyncProvider

            return CloudSyncProvider(provider_name, config or {})
        except ImportError:
            raise ValueError(f"Cloud provider '{provider_name}' not available")
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def _in_quiet_hours(config):
    sched = config.get("scheduling", {})
    quiet = sched.get("quiet_hours", {})
    if not quiet:
        return False
    start_str = quiet.get("start", "02:00")
    end_str = quiet.get("end", "06:00")
    current_time = datetime.now(timezone.utc).strftime("%H:%M")
    if start_str <= end_str:
        return start_str <= current_time < end_str
    return current_time >= start_str or current_time < end_str


def _log_sync_event(
    conn,
    job_id,
    action,
    relative_path=None,
    source_hash=None,
    dest_hash=None,
    bytes_transferred=0,
    duration_ms=0,
    resolution=None,
    error_detail=None,
):
    """Insert into sync_log (append-only)."""
    conn.execute(
        """INSERT INTO sync_log (job_id, action, relative_path, source_hash,
           dest_hash, bytes_transferred, duration_ms, resolution, error_detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            job_id,
            action,
            relative_path,
            source_hash,
            dest_hash,
            bytes_transferred,
            duration_ms,
            resolution,
            error_detail,
        ),
    )


def _load_cached_state(conn, job_id, side="source"):
    """Load cached file state from sync_state table."""
    rows = conn.execute(
        "SELECT relative_path, content_hash, file_size, mtime_epoch FROM sync_state WHERE job_id=%s AND side=%s",
        (job_id, side),
    ).fetchall()
    return {
        row["relative_path"]: {
            "hash": row["content_hash"],
            "size": row["file_size"],
            "mtime_epoch": row["mtime_epoch"],
        }
        for row in rows
    }


def _save_state(conn, job_id, manifest, side="source"):
    """Upsert file state to sync_state table."""
    now = _now()
    for rel_path, info in manifest.items():
        conn.execute(
            """INSERT INTO sync_state (job_id, relative_path, content_hash, file_size,
               mtime_epoch, side, last_synced_at, last_synced_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(job_id, relative_path, side)
               DO UPDATE SET content_hash=excluded.content_hash,
                  file_size=excluded.file_size, mtime_epoch=excluded.mtime_epoch,
                  last_synced_at=excluded.last_synced_at,
                  last_synced_hash=excluded.last_synced_hash""",
            (job_id, rel_path, info["hash"], info["size"], info["mtime_epoch"], side, now, info["hash"]),
        )


# =========================================================================
# HASH CONFIGURATION (D-SYNC-13 — FIPS 140-2 toggle)
# =========================================================================
def _configure_hash_from_config(config):
    """Apply hash algorithm settings from config."""
    detection = config.get("detection", {})
    algo = detection.get("hash_algorithm", "sha256")
    fips = detection.get("fips_mode", False)
    try:
        configure_hash(algo, fips)
    except ValueError:
        configure_hash("sha256", False)


# =========================================================================
# FILE INTEGRITY MONITORING (FIM — D-SYNC-15)
# =========================================================================
def check_fim(job_id: str, db_path=None) -> Dict:
    """Check file integrity against last-known sync state (FIM baseline).

    Detects unauthorized changes between syncs by comparing current file
    hashes against the cached sync_state. Logs mismatches to audit trail.

    Returns:
        Dict with mismatches found.
    """
    config = _load_config()
    fim_cfg = config.get("fim", {})
    if not fim_cfg.get("enabled", False):
        return {"status": "disabled", "message": "FIM is not enabled in config"}

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sync_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"Job not found: {job_id}"}
        job = dict(row)
    finally:
        conn.close()

    src_provider = _get_provider(job["source_provider"])
    conn = _get_db(db_path)
    try:
        cached = _load_cached_state(conn, job_id, "source")
    finally:
        conn.close()

    if not cached:
        return {"status": "no_baseline", "message": "No FIM baseline yet — run sync first"}

    mismatches = []
    for rel_path, state in cached.items():
        data = src_provider.read_file(job["source_path"], rel_path)
        if data is None:
            mismatches.append({"path": rel_path, "issue": "file_missing", "expected_hash": state["hash"]})
            continue
        current_hash = compute_file_hash(data)
        if current_hash != state["hash"]:
            mismatches.append(
                {
                    "path": rel_path,
                    "issue": "hash_mismatch",
                    "expected_hash": state["hash"],
                    "current_hash": current_hash,
                }
            )

    # Log to audit trail
    if mismatches and fim_cfg.get("alert_on_mismatch", True):
        _audit(
            "filesync.fim_mismatch",
            f"FIM detected {len(mismatches)} unauthorized change(s) in job '{job['name']}'",
            {"job_id": job_id, "mismatches": mismatches[:10]},
        )
        conn = _get_db(db_path)
        try:
            for m in mismatches:
                _log_sync_event(
                    conn,
                    job_id,
                    "fim_alert",
                    m["path"],
                    source_hash=m.get("expected_hash"),
                    dest_hash=m.get("current_hash"),
                    error_detail=m["issue"],
                )
            conn.commit()
        finally:
            conn.close()

    return {
        "status": "checked",
        "job_id": job_id,
        "total_files": len(cached),
        "mismatches": len(mismatches),
        "details": mismatches[:50],
    }


# =========================================================================
# JOB MANAGEMENT
# =========================================================================
def create_job(
    name: str,
    source: str,
    dest: str,
    source_provider: str = "local",
    dest_provider: str = "local",
    mode: str = "push",
    conflict_strategy: str = "last_write_wins",
    schedule_interval: int = 0,
    bandwidth_limit: int = 0,
    max_workers: int = 4,
    delete_orphans: bool = False,
    project_id: str = None,
    created_by: str = None,
    db_path=None,
) -> Dict:
    """Create a new sync job."""
    config = _load_config()
    defaults = config.get("defaults", {})

    job_id = _gen_id("fsync")
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO sync_jobs (id, name, source_path, source_provider, dest_path,
               dest_provider, sync_mode, conflict_strategy, delete_orphans,
               schedule_interval_seconds, bandwidth_limit_kbps, max_workers,
               project_id, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                job_id,
                name,
                source,
                source_provider,
                dest,
                dest_provider,
                mode or defaults.get("sync_mode", "push"),
                conflict_strategy or defaults.get("conflict_strategy", "last_write_wins"),
                1 if delete_orphans else 0,
                schedule_interval or defaults.get("schedule_interval_seconds", 0),
                bandwidth_limit or defaults.get("bandwidth_limit_kbps", 0),
                max_workers or defaults.get("max_workers", 4),
                project_id,
                created_by,
                _now(),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(
        "filesync.job_created",
        f"Created sync job '{name}'",
        {"job_id": job_id, "source": source, "dest": dest, "mode": mode},
    )

    return {"status": "created", "job_id": job_id, "name": name}


def list_jobs(db_path=None) -> Dict:
    """List all sync jobs."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM sync_jobs ORDER BY created_at DESC").fetchall()
        jobs = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"status": "success", "jobs": jobs, "count": len(jobs)}


def get_job_status(job_id: str, db_path=None) -> Dict:
    """Get detailed status for a sync job."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sync_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"Job not found: {job_id}"}
        job = dict(row)

        # Recent log entries
        logs = conn.execute(
            "SELECT * FROM sync_log WHERE job_id=%s ORDER BY created_at DESC LIMIT 20",
            (job_id,),
        ).fetchall()
        job["recent_log"] = [dict(row) for row in logs]

        # Pending conflicts
        conflicts = conn.execute(
            "SELECT * FROM sync_conflicts WHERE job_id=%s AND resolution='pending'",
            (job_id,),
        ).fetchall()
        job["pending_conflicts"] = [dict(c) for c in conflicts]
    finally:
        conn.close()
    return {"status": "success", "job": job}


def delete_job(job_id: str, db_path=None) -> Dict:
    """Delete a sync job and its state."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT name FROM sync_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"Job not found: {job_id}"}
        name = row["name"]
        conn.execute("DELETE FROM sync_state WHERE job_id=%s", (job_id,))
        conn.execute("DELETE FROM sync_conflicts WHERE job_id=%s", (job_id,))
        conn.execute("DELETE FROM sync_jobs WHERE id=%s", (job_id,))
        conn.commit()
    finally:
        conn.close()
    _audit("filesync.job_deleted", f"Deleted sync job '{name}'", {"job_id": job_id})
    return {"status": "deleted", "job_id": job_id}


# =========================================================================
# SYNC EXECUTION
# =========================================================================
def _scan_both_sides(job, config, job_id, db_path):
    """Scan source and dest directories; return (src_manifest, dst_manifest, src_cached, dst_cached)."""
    src_provider = _get_provider(job["source_provider"])
    dst_provider = _get_provider(job["dest_provider"])

    conn = _get_db(db_path)
    try:
        src_cached = _load_cached_state(conn, job_id, "source")
        dst_cached = _load_cached_state(conn, job_id, "dest")
    finally:
        conn.close()

    fast_skip = config.get("detection", {}).get("fast_skip_mtime", True)
    ignore_file = job.get("ignore_file", ".syncignore")

    src_manifest = scan_directory(
        src_provider, job["source_path"], ignore_file=ignore_file,
        cached_state=src_cached, fast_skip_mtime=fast_skip,
    )
    dst_manifest = scan_directory(
        dst_provider, job["dest_path"], ignore_file=ignore_file,
        cached_state=dst_cached, fast_skip_mtime=fast_skip,
    )

    conn = _get_db(db_path)
    try:
        _log_sync_event(conn, job_id, "scan_completed")
        conn.execute("UPDATE sync_jobs SET status='syncing', updated_at=%s WHERE id=%s", (_now(), job_id))
        conn.commit()
    finally:
        conn.close()

    return src_manifest, dst_manifest, src_cached, dst_cached, src_provider, dst_provider


def _build_actions(job, src_manifest, dst_manifest, src_cached, dst_cached):
    """Detect changes and resolve conflicts; return (actions, summary)."""
    delete_orphans = bool(job.get("delete_orphans", 0))
    mode = job["sync_mode"]

    if mode == "bidirectional":
        actions = detect_changes_bidirectional(src_manifest, dst_manifest, src_cached, dst_cached)
    elif mode == "pull":
        actions = detect_changes_push(dst_manifest, src_manifest, delete_orphans)
        for a in actions:
            a["direction"] = "dest_to_source"
    else:
        actions = detect_changes_push(src_manifest, dst_manifest, delete_orphans)

    actions = resolve_conflicts(actions, job["conflict_strategy"])
    return actions, summarize_actions(actions)


def _snapshot_before_transfer(job, job_id, actions, versioning_cfg, db_path):
    """Snapshot dest files before overwrite if versioning is enabled."""
    if not versioning_cfg.get("enabled", True):
        return
    if job["dest_provider"] != "local":
        return
    versioner = FileVersioner(
        db_path=str(db_path or DB_PATH),
        max_versions=versioning_cfg.get("max_versions_per_file", 10),
        versions_dir_name=versioning_cfg.get("versions_dir", ".versions"),
    )
    for act in actions:
        if act["action"] == "update" and act.get("direction", "source_to_dest") == "source_to_dest":
            versioner.snapshot_before_overwrite(job_id, job["dest_path"], act["relative_path"])


def _tally_and_persist(job, job_id, results, src_manifest, dst_manifest, t0, db_path):
    """Log transfer results, save state, update job stats; return result dict."""
    synced = sum(1 for r in results if r.success and r.action in ("copy", "update"))
    skipped = sum(1 for r in results if r.action == "skip")
    errored = sum(1 for r in results if not r.success)
    total_bytes = sum(r.bytes_transferred for r in results)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    status = "completed" if errored == 0 else "failed"

    conn = _get_db(db_path)
    try:
        for r in results:
            _log_sync_event(
                conn, job_id, r.action, r.relative_path,
                bytes_transferred=r.bytes_transferred, duration_ms=r.duration_ms,
                error_detail=r.error if not r.success else None,
            )
        _save_state(conn, job_id, src_manifest, "source")
        _save_state(conn, job_id, dst_manifest, "dest")
        conn.execute(
            """UPDATE sync_jobs SET status=%s, last_run_at=%s, files_synced=%s,
               files_skipped=%s, bytes_transferred=%s, error_message=%s, updated_at=%s
               WHERE id=%s""",
            (status, _now(), synced, skipped, total_bytes,
             f"{errored} errors" if errored else None, _now(), job_id),
        )
        if status == "completed":
            conn.execute("UPDATE sync_jobs SET last_success_at=%s WHERE id=%s", (_now(), job_id))
        _log_sync_event(
            conn, job_id,
            "sync_completed" if status == "completed" else "sync_failed",
            duration_ms=elapsed_ms,
        )
        conn.commit()
    finally:
        conn.close()

    return status, synced, skipped, errored, total_bytes, elapsed_ms


def run_sync(job_id: str, dry_run: bool = False, db_path=None) -> Dict:
    """Run a sync job.

    Args:
        job_id: Job ID to run.
        dry_run: If True, plan actions without executing.
        db_path: Optional DB path override.

    Returns:
        Sync result dict with stats and any errors.
    """
    config = _load_config()
    _configure_hash_from_config(config)

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sync_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"Job not found: {job_id}"}
        job = dict(row)
    finally:
        conn.close()

    if job["status"] == "paused":
        return {"status": "error", "error": "Job is paused. Resume first."}

    fim_cfg = config.get("fim", {})
    if fim_cfg.get("enabled", False):
        fim_result = check_fim(job_id, db_path)
        if fim_result.get("mismatches", 0) > 0:
            _audit("filesync.fim_pre_sync_alert",
                   f"FIM: {fim_result['mismatches']} changes detected before sync",
                   {"job_id": job_id})

    conn = _get_db(db_path)
    try:
        conn.execute("UPDATE sync_jobs SET status='scanning', updated_at=%s WHERE id=%s", (_now(), job_id))
        conn.commit()
        _log_sync_event(conn, job_id, "scan_started")
        conn.commit()
    finally:
        conn.close()

    t0 = time.monotonic()
    _audit("filesync.sync_started", f"Sync started for '{job['name']}'", {"job_id": job_id, "dry_run": dry_run})

    try:
        src_manifest, dst_manifest, src_cached, dst_cached, src_provider, dst_provider = \
            _scan_both_sides(job, config, job_id, db_path)

        actions, summary = _build_actions(job, src_manifest, dst_manifest, src_cached, dst_cached)

        if dry_run:
            conn = _get_db(db_path)
            try:
                conn.execute("UPDATE sync_jobs SET status='idle', updated_at=%s WHERE id=%s", (_now(), job_id))
                conn.commit()
            finally:
                conn.close()
            return {"status": "dry_run", "job_id": job_id, "summary": summary, "actions": actions}

        _snapshot_before_transfer(job, job_id, actions, config.get("versioning", {}), db_path)

        progress_cfg = config.get("progress", {})
        progress_enabled = progress_cfg.get("enabled", True)
        progress_interval = progress_cfg.get("log_interval_files", 10)
        _progress_count = [0]
        _total_actions = len([a for a in actions if a["action"] not in ("skip", "unchanged")])

        def _on_progress(result):
            _progress_count[0] += 1
            if progress_enabled and _progress_count[0] % progress_interval == 0:
                pct = int((_progress_count[0] / max(_total_actions, 1)) * 100)
                _audit("filesync.progress",
                       f"Sync progress: {_progress_count[0]}/{_total_actions} files ({pct}%)",
                       {"job_id": job_id, "completed": _progress_count[0],
                        "total": _total_actions, "pct": pct})

        transfer_cfg = config.get("transfer", {})
        results = execute_actions(
            actions, src_provider, job["source_path"], dst_provider, job["dest_path"],
            max_workers=job.get("max_workers", 4),
            verify=transfer_cfg.get("verify_after_transfer", True),
            bandwidth_limit_kbps=job.get("bandwidth_limit_kbps", 0),
            progress_callback=_on_progress if progress_enabled else None,
            compression=transfer_cfg.get("compression", "none"),
            compression_level=transfer_cfg.get("compression_level", 6),
        )

        status, synced, skipped, errored, total_bytes, elapsed_ms = \
            _tally_and_persist(job, job_id, results, src_manifest, dst_manifest, t0, db_path)

        _audit("filesync.sync_completed", f"Sync completed for '{job['name']}'",
               {"job_id": job_id, "synced": synced, "skipped": skipped,
                "errors": errored, "bytes": total_bytes, "ms": elapsed_ms})

        return {
            "status": status, "job_id": job_id,
            "files_synced": synced, "files_skipped": skipped, "files_errored": errored,
            "bytes_transferred": total_bytes, "duration_ms": elapsed_ms, "summary": summary,
        }

    except Exception as e:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        conn = _get_db(db_path)
        try:
            conn.execute("UPDATE sync_jobs SET status='failed', error_message=%s, updated_at=%s WHERE id=%s",
                         (str(e), _now(), job_id))
            _log_sync_event(conn, job_id, "sync_failed", error_detail=str(e), duration_ms=elapsed_ms)
            conn.commit()
        finally:
            conn.close()
        _audit("filesync.sync_failed", f"Sync failed for '{job['name']}'", {"job_id": job_id, "error": str(e)})
        return {"status": "error", "job_id": job_id, "error": str(e), "duration_ms": elapsed_ms}


def run_all_jobs(db_path=None) -> Dict:
    """Run all non-paused sync jobs."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute("SELECT id FROM sync_jobs WHERE status NOT IN ('paused', 'syncing', 'scanning')").fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        result = run_sync(row["id"], db_path=db_path)
        results.append(result)

    return {
        "status": "success",
        "jobs_run": len(results),
        "results": results,
    }


def pause_job(job_id: str, db_path=None) -> Dict:
    """Pause a sync job."""
    conn = _get_db(db_path)
    try:
        conn.execute("UPDATE sync_jobs SET status='paused', updated_at=%s WHERE id=%s", (_now(), job_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "paused", "job_id": job_id}


def resume_job(job_id: str, db_path=None) -> Dict:
    """Resume a paused sync job."""
    conn = _get_db(db_path)
    try:
        conn.execute("UPDATE sync_jobs SET status='idle', updated_at=%s WHERE id=%s", (_now(), job_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "resumed", "job_id": job_id}


def get_sync_log(job_id: str, limit: int = 50, db_path=None) -> Dict:
    """Get sync log entries for a job."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM sync_log WHERE job_id=%s ORDER BY created_at DESC LIMIT %s",
            (job_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return {"status": "success", "entries": [dict(r) for r in rows], "count": len(rows)}


def get_conflicts(job_id: str, db_path=None) -> Dict:
    """Get pending conflicts for a job."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM sync_conflicts WHERE job_id=%s AND resolution='pending' ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"status": "success", "conflicts": [dict(r) for r in rows], "count": len(rows)}


def resolve_conflict(conflict_id: str, resolution: str, resolved_by: str = "user", db_path=None) -> Dict:
    """Resolve a specific conflict."""
    valid = ("source_wins", "dest_wins", "renamed", "skipped", "manual")
    if resolution not in valid:
        return {"status": "error", "error": f"Invalid resolution. Must be one of: {valid}"}
    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE sync_conflicts SET resolution=%s, resolved_at=%s, resolved_by=%s WHERE id=%s",
            (resolution, _now(), resolved_by, conflict_id),
        )
        conn.commit()
    finally:
        conn.close()
    _audit(
        "filesync.conflict_resolved",
        f"Conflict {conflict_id} resolved: {resolution}",
        {"conflict_id": conflict_id, "resolution": resolution},
    )
    return {"status": "resolved", "conflict_id": conflict_id, "resolution": resolution}


def get_health(db_path=None) -> Dict:
    """Overall file sync health status."""
    conn = _get_db(db_path)
    try:
        jobs = conn.execute("SELECT COUNT(*) as cnt FROM sync_jobs").fetchone()["cnt"]
        active = conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_jobs WHERE status IN ('scanning', 'syncing', 'watching')"
        ).fetchone()["cnt"]
        failed = conn.execute("SELECT COUNT(*) as cnt FROM sync_jobs WHERE status='failed'").fetchone()["cnt"]
        pending_conflicts = conn.execute(
            "SELECT COUNT(*) as cnt FROM sync_conflicts WHERE resolution='pending'"
        ).fetchone()["cnt"]
        recent_syncs = conn.execute(
            """SELECT COUNT(*) as cnt FROM sync_log
               WHERE action='sync_completed' AND created_at >= datetime('now', '-24 hours')"""
        ).fetchone()["cnt"]
    finally:
        conn.close()
    return {
        "status": "success",
        "total_jobs": jobs,
        "active_jobs": active,
        "failed_jobs": failed,
        "pending_conflicts": pending_conflicts,
        "syncs_last_24h": recent_syncs,
    }


# =========================================================================
# WATCH MODE (D-SYNC-8 — real-time file watching)
# =========================================================================
# Track active watchers globally for stop/status
_active_watchers: Dict[str, "FileWatcher"] = {}  # noqa: F821


def watch_job(job_id: str, db_path=None, blocking: bool = True) -> Dict:
    """Start watching a sync job's source directory for changes.

    When a change is detected (via watchdog or mtime polling), triggers
    an incremental sync automatically with a configurable debounce.

    Args:
        job_id: Sync job ID.
        db_path: Optional DB path override.
        blocking: If True, block until stopped (Ctrl+C). If False, return immediately.

    Returns:
        Dict with watch status.
    """
    from tools.filesync.watcher import FileWatcher

    config = _load_config()
    watcher_cfg = config.get("watcher", {})
    debounce = watcher_cfg.get("debounce_seconds", 2.0)
    recursive = watcher_cfg.get("recursive", True)
    fallback_interval = watcher_cfg.get("fallback_scan_interval_seconds", 60)

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sync_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"Job not found: {job_id}"}
        job = dict(row)
    finally:
        conn.close()

    if job["status"] == "paused":
        return {"status": "error", "error": "Job is paused. Resume first."}

    watch_path = job["source_path"]
    if not os.path.isdir(watch_path):
        return {"status": "error", "error": f"Source path is not a directory: {watch_path}"}

    # Stop existing watcher for this job if any
    if job_id in _active_watchers:
        _active_watchers[job_id].stop()
        del _active_watchers[job_id]

    _sync_lock = threading.Lock()
    _sync_count = [0]

    def _on_changes(changed_paths):
        """Callback when file changes detected — trigger incremental sync."""
        if _in_quiet_hours(config):
            return
        with _sync_lock:
            _sync_count[0] += 1
            count = _sync_count[0]
        n = len(changed_paths)
        _safe_print(f"[{_now()}] Watcher detected {n} change(s) -- triggering sync #{count}")
        _audit(
            "filesync.watcher_triggered",
            f"Watcher detected {n} change(s) in job '{job['name']}'",
            {"job_id": job_id, "changes": n, "sync_number": count},
        )
        result = run_sync(job_id, db_path=db_path)
        synced = result.get("files_synced", 0)
        status = result.get("status", "unknown")
        _safe_print(f"[{_now()}] Sync #{count} {status}: {synced} file(s) synced")
        # Restore status to 'watching' after watcher-triggered sync
        _conn = _get_db(db_path)
        try:
            _conn.execute("UPDATE sync_jobs SET status='watching', updated_at=%s WHERE id=%s", (_now(), job_id))
            _conn.commit()
        finally:
            _conn.close()

    watcher = FileWatcher(
        watch_path=watch_path,
        on_changes=_on_changes,
        debounce_seconds=debounce,
        recursive=recursive,
        fallback_scan_interval=fallback_interval,
    )
    watcher.start()
    _active_watchers[job_id] = watcher

    # Update job status to 'watching'
    conn = _get_db(db_path)
    try:
        conn.execute("UPDATE sync_jobs SET status='watching', updated_at=%s WHERE id=%s", (_now(), job_id))
        conn.commit()
    finally:
        conn.close()

    backend = "watchdog (real-time)" if watcher.using_watchdog else f"polling (every {fallback_interval}s)"
    _safe_print(f"[{_now()}] Watching '{job['name']}' via {backend}")
    _safe_print(f"[{_now()}] Source: {watch_path}")
    _safe_print(f"[{_now()}] Dest:   {job['dest_path']}")
    _safe_print(f"[{_now()}] Debounce: {debounce}s | Recursive: {recursive}")
    if not blocking:
        _safe_print(f"[{_now()}] Running in background (non-blocking)")

    _audit(
        "filesync.watcher_started",
        f"Started watching job '{job['name']}' via {backend}",
        {"job_id": job_id, "backend": backend, "path": watch_path},
    )

    info = {
        "status": "watching",
        "job_id": job_id,
        "name": job["name"],
        "source_path": watch_path,
        "dest_path": job["dest_path"],
        "backend": backend,
        "debounce_seconds": debounce,
        "recursive": recursive,
    }

    if blocking:
        _safe_print(f"[{_now()}] Press Ctrl+C to stop watching")
        try:
            while watcher.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            stop_watching(job_id, db_path)
            info["status"] = "stopped"

    return info


def stop_watching(job_id: str, db_path=None) -> Dict:
    """Stop watching a sync job."""
    if job_id in _active_watchers:
        _active_watchers[job_id].stop()
        del _active_watchers[job_id]

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT name, status FROM sync_jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            return {"status": "error", "error": f"Job not found: {job_id}"}
        if row["status"] == "watching":
            conn.execute("UPDATE sync_jobs SET status='idle', updated_at=%s WHERE id=%s", (_now(), job_id))
            conn.commit()
    finally:
        conn.close()

    _safe_print(f"[{_now()}] Stopped watching job {job_id}")
    _audit("filesync.watcher_stopped", f"Stopped watching job {job_id}", {"job_id": job_id})
    return {"status": "stopped", "job_id": job_id}


def get_watch_status(db_path=None) -> Dict:
    """Get status of all active watchers."""
    watchers = []
    for job_id, watcher in _active_watchers.items():
        watchers.append(
            {
                "job_id": job_id,
                "running": watcher.is_running,
                "backend": "watchdog" if watcher.using_watchdog else "polling",
            }
        )
    return {"status": "success", "active_watchers": watchers, "count": len(watchers)}


# =========================================================================
# DAEMON MODE (D-SYNC-9 — scheduled + watcher)
# =========================================================================
import threading  # noqa: E402


def run_daemon(db_path=None):
    """Run daemon mode — scheduled syncs + optional file watchers.

    Jobs with schedule_interval > 0 run periodically.
    Jobs with status 'watching' or watcher config enabled get file watchers.
    """
    config = _load_config()
    watcher_cfg = config.get("watcher", {})
    watcher_enabled = watcher_cfg.get("enabled", False)
    _safe_print(f"[{_now()}] File sync daemon started (watcher={watcher_enabled})")

    # Start watchers for jobs that have watching enabled
    if watcher_enabled:
        conn = _get_db(db_path)
        try:
            rows = conn.execute("SELECT id, name FROM sync_jobs WHERE status NOT IN ('paused')").fetchall()
        finally:
            conn.close()

        for row in rows:
            job_id = row["id"]
            if job_id not in _active_watchers:
                _safe_print(f"[{_now()}] Starting watcher for: {row['name']} ({job_id})")
                watch_job(job_id, db_path=db_path, blocking=False)

    while True:
        if _in_quiet_hours(config):
            time.sleep(60)
            continue

        conn = _get_db(db_path)
        try:
            rows = conn.execute(
                """SELECT id, name, schedule_interval_seconds, last_run_at
                   FROM sync_jobs
                   WHERE status NOT IN ('paused', 'watching')
                     AND schedule_interval_seconds > 0"""
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            job_id = row["id"]
            interval = row["schedule_interval_seconds"]
            last_run = row["last_run_at"]

            # Check if due
            if last_run:
                try:
                    last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                    if elapsed < interval:
                        continue
                except (ValueError, TypeError):
                    pass

            _safe_print(f"[{_now()}] Running scheduled sync: {row['name']} ({job_id})")
            result = run_sync(job_id, db_path=db_path)
            _safe_print(f"[{_now()}] Result: {result.get('status')} ({result.get('files_synced', 0)} files)")

        # Check for new jobs that need watchers
        if watcher_enabled:
            conn = _get_db(db_path)
            try:
                new_jobs = conn.execute(
                    "SELECT id, name FROM sync_jobs WHERE status NOT IN ('paused') AND id NOT IN ({})".format(  # nosec B608 -- table/column names are internal constants, not user input
                        ",".join(f"'{jid}'" for jid in _active_watchers) if _active_watchers else "''"
                    )
                ).fetchall()
            finally:
                conn.close()
            for row in new_jobs:
                if row["id"] not in _active_watchers:
                    _safe_print(f"[{_now()}] Starting watcher for new job: {row['name']}")
                    watch_job(row["id"], db_path=db_path, blocking=False)

        time.sleep(30)  # Check every 30 seconds


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="ICDEV™ File Sync Engine — Syncthing-inspired file synchronization")

    # Command group
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create a new sync job")
    group.add_argument("--list", action="store_true", help="List all sync jobs")
    group.add_argument("--status", action="store_true", help="Get job status")
    group.add_argument("--run", action="store_true", help="Run a sync job")
    group.add_argument("--run-all", action="store_true", help="Run all due sync jobs")
    group.add_argument("--log", action="store_true", help="View sync log")
    group.add_argument("--conflicts", action="store_true", help="View pending conflicts")
    group.add_argument("--resolve", action="store_true", help="Resolve a conflict")
    group.add_argument("--pause", action="store_true", help="Pause a job")
    group.add_argument("--resume", action="store_true", help="Resume a job")
    group.add_argument("--delete", action="store_true", help="Delete a job")
    group.add_argument("--daemon", action="store_true", help="Run daemon mode")
    group.add_argument("--health", action="store_true", help="Health status")
    group.add_argument(
        "--quick-setup",
        action="store_true",
        help="Quick one-command job creation: --quick-setup --source /src --dest /dst",
    )
    group.add_argument("--fim", action="store_true", help="Run File Integrity Monitoring check against baseline")
    group.add_argument(
        "--watch", action="store_true", help="Watch job source directory and auto-sync on changes (D-SYNC-8)"
    )
    group.add_argument("--stop-watch", action="store_true", help="Stop watching a job")
    group.add_argument("--versions", action="store_true", help="List file versions for a job (D-SYNC-16)")
    group.add_argument("--restore-version", action="store_true", help="Restore a specific file version")
    group.add_argument(
        "--install-service", action="store_true", help="Register daemon as OS startup service (survives reboot)"
    )
    group.add_argument("--uninstall-service", action="store_true", help="Remove daemon from OS startup")
    group.add_argument("--service-status", action="store_true", help="Check if daemon is registered as OS service")

    # Job parameters
    parser.add_argument("--name", help="Job name")
    parser.add_argument("--source", help="Source path")
    parser.add_argument("--dest", help="Destination path")
    parser.add_argument("--source-provider", default="local", choices=["local", "sftp", "s3", "azure", "gcs"])
    parser.add_argument("--dest-provider", default="local", choices=["local", "sftp", "s3", "azure", "gcs"])
    parser.add_argument("--mode", default="push", choices=["push", "pull", "bidirectional"])
    parser.add_argument(
        "--conflict-strategy",
        default="last_write_wins",
        choices=["last_write_wins", "rename_both", "source_wins", "skip"],
    )
    parser.add_argument("--schedule-interval", type=int, default=0, help="Daemon interval in seconds (0=one-shot)")
    parser.add_argument("--bandwidth-limit", type=int, default=0, help="Bandwidth limit in KB/s (0=unlimited)")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--delete-orphans", action="store_true", help="Delete dest files not in source")

    # Identifiers
    parser.add_argument("--job-id", help="Sync job ID")
    parser.add_argument("--conflict-id", help="Conflict ID to resolve")
    parser.add_argument("--version-id", help="Version ID for restore")
    parser.add_argument("--file-path", help="File path filter for --versions")
    parser.add_argument(
        "--resolution", help="Conflict resolution", choices=["source_wins", "dest_wins", "renamed", "skipped", "manual"]
    )
    parser.add_argument("--project-id", help="ICDEV™ project ID")
    parser.add_argument("--limit", type=int, default=50, help="Log entry limit")

    # Execution flags
    parser.add_argument("--dry-run", action="store_true", help="Preview without executing")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None)

    args = parser.parse_args()
    db_path = args.db_path

    try:
        if args.create:
            if not args.name or not args.source or not args.dest:
                parser.error("--create requires --name, --source, --dest")
            result = create_job(
                args.name,
                args.source,
                args.dest,
                args.source_provider,
                args.dest_provider,
                args.mode,
                args.conflict_strategy,
                args.schedule_interval,
                args.bandwidth_limit,
                args.max_workers,
                args.delete_orphans,
                args.project_id,
                db_path=db_path,
            )
        elif args.list:
            result = list_jobs(db_path)
        elif args.status:
            if not args.job_id:
                parser.error("--status requires --job-id")
            result = get_job_status(args.job_id, db_path)
        elif args.run:
            if not args.job_id:
                parser.error("--run requires --job-id")
            result = run_sync(args.job_id, args.dry_run, db_path)
        elif args.run_all:
            result = run_all_jobs(db_path)
        elif args.log:
            if not args.job_id:
                parser.error("--log requires --job-id")
            result = get_sync_log(args.job_id, args.limit, db_path)
        elif args.conflicts:
            if not args.job_id:
                parser.error("--conflicts requires --job-id")
            result = get_conflicts(args.job_id, db_path)
        elif args.resolve:
            if not args.conflict_id or not args.resolution:
                parser.error("--resolve requires --conflict-id and --resolution")
            result = resolve_conflict(args.conflict_id, args.resolution, db_path=db_path)
        elif args.pause:
            if not args.job_id:
                parser.error("--pause requires --job-id")
            result = pause_job(args.job_id, db_path)
        elif args.resume:
            if not args.job_id:
                parser.error("--resume requires --job-id")
            result = resume_job(args.job_id, db_path)
        elif args.delete:
            if not args.job_id:
                parser.error("--delete requires --job-id")
            result = delete_job(args.job_id, db_path)
        elif args.daemon:
            run_daemon(db_path)
            return 0
        elif args.health:
            result = get_health(db_path)
        elif args.quick_setup:
            if not args.source or not args.dest:
                parser.error("--quick-setup requires --source and --dest")
            name = args.name or f"Sync {Path(args.source).name} → {Path(args.dest).name}"
            result = create_job(
                name,
                args.source,
                args.dest,
                args.source_provider,
                args.dest_provider,
                args.mode,
                args.conflict_strategy,
                args.schedule_interval,
                args.bandwidth_limit,
                args.max_workers,
                args.delete_orphans,
                args.project_id,
                db_path=db_path,
            )
            if result.get("status") == "created":
                # Auto-run the first sync immediately
                run_result = run_sync(result["job_id"], args.dry_run, db_path)
                result["first_run"] = run_result
        elif args.fim:
            if not args.job_id:
                parser.error("--fim requires --job-id")
            result = check_fim(args.job_id, db_path)
        elif args.watch:
            if not args.job_id:
                parser.error("--watch requires --job-id")
            result = watch_job(args.job_id, db_path, blocking=True)
        elif args.stop_watch:
            if not args.job_id:
                parser.error("--stop-watch requires --job-id")
            result = stop_watching(args.job_id, db_path)
        elif args.versions:
            if not args.job_id:
                parser.error("--versions requires --job-id")
            versioner = FileVersioner(str(db_path or DB_PATH))
            versions = versioner.list_versions(args.job_id, args.file_path, args.limit)
            stats = versioner.get_version_stats(args.job_id)
            result = {"status": "success", "versions": versions, "count": len(versions), "stats": stats}
        elif args.restore_version:
            if not args.version_id or not args.job_id:
                parser.error("--restore-version requires --version-id and --job-id")
            # Look up dest_path from job
            job_info = get_job_status(args.job_id, db_path)
            if job_info.get("status") == "error":
                result = job_info
            else:
                dest_path = job_info["job"]["dest_path"]
                versioner = FileVersioner(str(db_path or DB_PATH))
                result = versioner.restore_version(args.version_id, dest_path)
        elif args.install_service:
            from tools.filesync.service_manager import install_service

            result = install_service()
        elif args.uninstall_service:
            from tools.filesync.service_manager import uninstall_service

            result = uninstall_service()
        elif args.service_status:
            from tools.filesync.service_manager import service_status

            result = service_status()
        else:
            parser.print_help()
            return 1

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(json.dumps(result, indent=2, default=str))

        return 0 if result.get("status") != "error" else 1

    except Exception as e:
        output = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
