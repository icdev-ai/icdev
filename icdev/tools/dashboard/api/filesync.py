#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: File Sync Module (D-SYNC-1 through D-SYNC-12).

REST endpoints for sync job CRUD, execution, conflicts, and health.
"""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

filesync_api = Blueprint("filesync_api", __name__, url_prefix="/api/filesync")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


# ── Stats ────────────────────────────────────────────────────────────


@filesync_api.route("/stats", methods=["GET"])
def filesync_stats():
    """GET /api/filesync/stats — Sync module overview statistics."""
    conn = _get_db()
    try:
        stats = {
            "total_jobs": 0,
            "active_jobs": 0,
            "completed_syncs": 0,
            "failed_syncs": 0,
            "pending_conflicts": 0,
            "total_bytes": 0,
        }
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM sync_jobs").fetchone()
            stats["total_jobs"] = row["cnt"]
        except Exception:
            pass
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM sync_jobs WHERE status IN ('scanning', 'syncing', 'watching')"
            ).fetchone()
            stats["active_jobs"] = row["cnt"]
        except Exception:
            pass
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM sync_log WHERE action = 'sync_completed'").fetchone()
            stats["completed_syncs"] = row["cnt"]
        except Exception:
            pass
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM sync_log WHERE action = 'error'").fetchone()
            stats["failed_syncs"] = row["cnt"]
        except Exception:
            pass
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM sync_conflicts WHERE resolution = 'pending'").fetchone()
            stats["pending_conflicts"] = row["cnt"]
        except Exception:
            pass
        try:
            row = conn.execute("SELECT COALESCE(SUM(bytes_transferred), 0) as total FROM sync_log").fetchone()
            stats["total_bytes"] = row["total"]
        except Exception:
            pass
        return jsonify(stats)
    finally:
        conn.close()


# ── Jobs CRUD ────────────────────────────────────────────────────────


@filesync_api.route("/jobs", methods=["GET"])
def list_jobs():
    """GET /api/filesync/jobs — List all sync jobs."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM sync_jobs ORDER BY created_at DESC").fetchall()
        return jsonify({"jobs": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"jobs": [], "count": 0, "error": str(e)})
    finally:
        conn.close()


@filesync_api.route("/jobs", methods=["POST"])
def create_job():
    """POST /api/filesync/jobs — Create a new sync job."""
    try:
        from tools.filesync.sync_engine import create_job as _create_job

        data = request.get_json(force=True)
        result = _create_job(
            name=data.get("name", "Unnamed"),
            source=data.get("source_path", ""),
            dest=data.get("dest_path", ""),
            source_provider=data.get("source_provider", "local"),
            dest_provider=data.get("dest_provider", "local"),
            mode=data.get("sync_mode", "push"),
            conflict_strategy=data.get("conflict_strategy", "last_write_wins"),
        )
        return jsonify(result), 201 if result.get("status") == "ok" else 400
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@filesync_api.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    """GET /api/filesync/jobs/<id> — Get job details."""
    try:
        from tools.filesync.sync_engine import get_job_status

        return jsonify(get_job_status(job_id))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@filesync_api.route("/jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    """DELETE /api/filesync/jobs/<id> — Delete a sync job."""
    try:
        from tools.filesync.sync_engine import delete_job as _delete_job

        return jsonify(_delete_job(job_id))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Sync Execution ───────────────────────────────────────────────────


@filesync_api.route("/jobs/<job_id>/run", methods=["POST"])
def run_job(job_id):
    """POST /api/filesync/jobs/<id>/run — Execute a sync job."""
    try:
        from tools.filesync.sync_engine import run_sync

        data = request.get_json(silent=True) or {}
        dry_run = data.get("dry_run", False)
        result = run_sync(job_id, dry_run=dry_run)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@filesync_api.route("/run-all", methods=["POST"])
def run_all_jobs():
    """POST /api/filesync/run-all — Execute all idle sync jobs."""
    try:
        from tools.filesync.sync_engine import run_all_jobs as _run_all

        return jsonify(_run_all())
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Sync Log ─────────────────────────────────────────────────────────


@filesync_api.route("/log", methods=["GET"])
def sync_log():
    """GET /api/filesync/log — Recent sync log entries."""
    limit = int(request.args.get("limit", "50"))
    job_id = request.args.get("job_id")
    conn = _get_db()
    try:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM sync_log WHERE job_id = %s ORDER BY created_at DESC LIMIT %s",
                (job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sync_log ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return jsonify({"entries": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"entries": [], "count": 0, "error": str(e)})
    finally:
        conn.close()


# ── Conflicts ────────────────────────────────────────────────────────


@filesync_api.route("/conflicts", methods=["GET"])
def list_conflicts():
    """GET /api/filesync/conflicts — List unresolved conflicts."""
    job_id = request.args.get("job_id")
    conn = _get_db()
    try:
        if job_id:
            rows = conn.execute(
                "SELECT * FROM sync_conflicts WHERE job_id = %s ORDER BY created_at DESC",
                (job_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sync_conflicts WHERE resolution = 'pending' ORDER BY created_at DESC"
            ).fetchall()
        return jsonify({"conflicts": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"conflicts": [], "count": 0, "error": str(e)})
    finally:
        conn.close()


@filesync_api.route("/conflicts/<conflict_id>/resolve", methods=["POST"])
def resolve_conflict(conflict_id):
    """POST /api/filesync/conflicts/<id>/resolve — Resolve a conflict."""
    try:
        from tools.filesync.sync_engine import resolve_conflict as _resolve

        data = request.get_json(force=True)
        resolution = data.get("resolution", "source_wins")
        return jsonify(_resolve(conflict_id, resolution))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── FIM ─────────────────────────────────────────────────────────────


@filesync_api.route("/jobs/<job_id>/fim", methods=["POST"])
def fim_check(job_id):
    """POST /api/filesync/jobs/<id>/fim — Run FIM integrity check."""
    try:
        from tools.filesync.sync_engine import check_fim

        return jsonify(check_fim(job_id))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Watch ────────────────────────────────────────────────────────────


@filesync_api.route("/jobs/<job_id>/watch", methods=["POST"])
def start_watch(job_id):
    """POST /api/filesync/jobs/<id>/watch — Start file watcher on job source."""
    try:
        from tools.filesync.sync_engine import watch_job

        return jsonify(watch_job(job_id, blocking=False))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@filesync_api.route("/jobs/<job_id>/watch", methods=["DELETE"])
def stop_watch(job_id):
    """DELETE /api/filesync/jobs/<id>/watch — Stop file watcher."""
    try:
        from tools.filesync.sync_engine import stop_watching

        return jsonify(stop_watching(job_id))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@filesync_api.route("/watchers", methods=["GET"])
def list_watchers():
    """GET /api/filesync/watchers — List active file watchers."""
    try:
        from tools.filesync.sync_engine import get_watch_status

        return jsonify(get_watch_status())
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Versions (D-SYNC-16) ─────────────────────────────────────────────


@filesync_api.route("/jobs/<job_id>/versions", methods=["GET"])
def list_versions(job_id):
    """GET /api/filesync/jobs/<id>/versions — List file versions."""
    try:
        from tools.filesync.versioner import FileVersioner

        rel_path = request.args.get("file_path")
        limit = int(request.args.get("limit", "50"))
        versioner = FileVersioner(str(DB_PATH))
        versions = versioner.list_versions(job_id, rel_path, limit)
        stats = versioner.get_version_stats(job_id)
        return jsonify({"versions": versions, "count": len(versions), "stats": stats})
    except Exception as e:
        return jsonify({"versions": [], "count": 0, "error": str(e)})


@filesync_api.route("/versions/<version_id>/restore", methods=["POST"])
def restore_version(version_id):
    """POST /api/filesync/versions/<id>/restore — Restore a file version."""
    try:
        from tools.filesync.versioner import FileVersioner

        data = request.get_json(force=True)
        dest_base = data.get("dest_base", "")
        if not dest_base:
            return jsonify({"status": "error", "error": "dest_base required"}), 400
        versioner = FileVersioner(str(DB_PATH))
        return jsonify(versioner.restore_version(version_id, dest_base))
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ── Health ───────────────────────────────────────────────────────────


@filesync_api.route("/health", methods=["GET"])
def filesync_health():
    """GET /api/filesync/health — Module health check."""
    try:
        from tools.filesync.sync_engine import get_health

        return jsonify(get_health())
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500
