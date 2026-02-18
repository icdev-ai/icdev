# CUI // SP-CTI
# ICDEV Dashboard CI/CD API — pipeline status, conversations, connectors

"""
Dashboard API endpoints for CI/CD pipeline monitoring.

Provides:
    GET /api/cicd/pipelines         — List recent pipeline runs
    GET /api/cicd/pipelines/<run_id> — Pipeline detail with recovery attempts
    GET /api/cicd/conversations/<key> — Conversation thread view
    GET /api/cicd/connectors         — Enabled connector status
    GET /api/cicd/queue/<key>        — Queued events for a session
"""

import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

cicd_api = Blueprint("cicd_api", __name__)


def _get_db():
    """Get database connection with row_factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@cicd_api.route("/api/cicd/pipelines", methods=["GET"])
def list_pipelines():
    """List recent pipeline runs with status."""
    limit = request.args.get("limit", 50, type=int)
    status_filter = request.args.get("status", "")

    conn = _get_db()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT id, session_key, run_id, platform, workflow, status, "
                "trigger_source, created_at, completed_at "
                "FROM ci_pipeline_runs WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (status_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_key, run_id, platform, workflow, status, "
                "trigger_source, created_at, completed_at "
                "FROM ci_pipeline_runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        pipelines = [dict(r) for r in rows]

        # Summary counts
        summary = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM ci_pipeline_runs "
            "GROUP BY status"
        ).fetchall()

        return jsonify({
            "pipelines": pipelines,
            "total": len(pipelines),
            "summary": {r["status"]: r["cnt"] for r in summary},
        })
    except Exception as e:
        return jsonify({"pipelines": [], "total": 0, "error": str(e)})
    finally:
        conn.close()


@cicd_api.route("/api/cicd/pipelines/<run_id>", methods=["GET"])
def pipeline_detail(run_id):
    """Get pipeline detail including recovery attempts."""
    conn = _get_db()
    try:
        pipeline = conn.execute(
            "SELECT * FROM ci_pipeline_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        if not pipeline:
            return jsonify({"error": "Pipeline not found"}), 404

        result = dict(pipeline)

        # Fetch recovery attempts from audit trail
        try:
            recovery = conn.execute(
                "SELECT * FROM audit_trail "
                "WHERE event_type = 'ci.recovery' AND project_id = ? "
                "ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
            result["recovery_attempts"] = [dict(r) for r in recovery]
        except Exception:
            result["recovery_attempts"] = []

        # Fetch conversation if exists
        try:
            conversation = conn.execute(
                "SELECT * FROM ci_conversations "
                "WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if conversation:
                conv = dict(conversation)
                turns = conn.execute(
                    "SELECT * FROM ci_conversation_turns "
                    "WHERE session_id = ? ORDER BY turn_number ASC",
                    (conv["id"],),
                ).fetchall()
                conv["turns"] = [dict(t) for t in turns]
                result["conversation"] = conv
        except Exception:
            result["conversation"] = None

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@cicd_api.route("/api/cicd/conversations/<session_key>", methods=["GET"])
def conversation_view(session_key):
    """Get conversation thread for a session."""
    conn = _get_db()
    try:
        conversation = conn.execute(
            "SELECT * FROM ci_conversations "
            "WHERE session_key = ? ORDER BY created_at DESC LIMIT 1",
            (session_key,),
        ).fetchone()

        if not conversation:
            return jsonify({"error": "No conversation found"}), 404

        conv = dict(conversation)
        turns = conn.execute(
            "SELECT * FROM ci_conversation_turns "
            "WHERE session_id = ? ORDER BY turn_number ASC",
            (conv["id"],),
        ).fetchall()
        conv["turns"] = [dict(t) for t in turns]

        return jsonify(conv)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@cicd_api.route("/api/cicd/connectors", methods=["GET"])
def connector_status():
    """Get enabled connector status."""
    try:
        from tools.ci.connectors.connector_registry import ConnectorRegistry
        connectors = ConnectorRegistry.list_connectors()
    except ImportError:
        connectors = {}

    # Also load config to show all configured channels
    try:
        import yaml
        config_path = BASE_DIR / "args" / "cicd_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            channels = config.get("cicd", {}).get("channels", {})
        else:
            channels = {}
    except Exception:
        channels = {}

    result = {}
    for name in ["github", "gitlab", "slack", "mattermost"]:
        ch = channels.get(name, {})
        result[name] = {
            "configured": bool(ch),
            "enabled": ch.get("enabled", False),
            "registered": name in connectors,
            "webhook_path": ch.get("webhook_path", ""),
        }

    return jsonify({"connectors": result})


@cicd_api.route("/api/cicd/queue/<session_key>", methods=["GET"])
def event_queue(session_key):
    """Get queued events for a session."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, session_key, event_id, status, created_at, processed_at "
            "FROM ci_event_queue "
            "WHERE session_key = ? ORDER BY created_at ASC",
            (session_key,),
        ).fetchall()

        return jsonify({
            "session_key": session_key,
            "queued_events": [dict(r) for r in rows],
            "total": len(rows),
        })
    except Exception as e:
        return jsonify({"queued_events": [], "total": 0, "error": str(e)})
    finally:
        conn.close()
