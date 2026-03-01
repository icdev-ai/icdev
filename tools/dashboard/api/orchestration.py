#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Real-Time Orchestration Dashboard (Phase 61).

Provides endpoints for monitoring multi-agent workflow execution,
DAG visualization, mailbox feed, collaboration history, and agent grid status.

All queries are read-only against existing tables:
    agent_workflows, agent_subtasks, agent_mailbox, agents,
    agent_collaboration_history, agent_token_usage, a2a_tasks
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

orchestration_api = Blueprint("orchestration_api", __name__, url_prefix="/api/orchestration")


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn, table_name):
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] > 0


def _safe_count(conn, sql, params=()):
    """Execute a count query, returning 0 if table doesn't exist."""
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


def _safe_query(conn, sql, params=()):
    """Execute a query, returning empty list if table doesn't exist."""
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


# =====================================================================
# Agent Grid
# =====================================================================

@orchestration_api.route("/agents")
def get_agent_grid():
    """Return all agents with current status, active task, and context usage."""
    try:
        conn = _get_db()
        agents = _safe_query(conn, "SELECT * FROM agents ORDER BY name")

        for agent in agents:
            agent_id = agent.get("id", "")

            # Active subtask (currently working)
            active_subtasks = _safe_query(
                conn,
                """SELECT id, skill_id, description, started_at, duration_ms
                   FROM agent_subtasks
                   WHERE agent_id = ? AND status = 'working'
                   ORDER BY started_at DESC LIMIT 1""",
                (agent_id,),
            )
            if active_subtasks:
                task = active_subtasks[0]
                agent["active_task"] = task.get("skill_id") or task.get("description", "")
                started = task.get("started_at")
                if started:
                    try:
                        start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                        now_dt = datetime.now(timezone.utc)
                        agent["elapsed_ms"] = int((now_dt - start_dt).total_seconds() * 1000)
                    except (ValueError, TypeError):
                        agent["elapsed_ms"] = task.get("duration_ms", 0)
                else:
                    agent["elapsed_ms"] = 0
            else:
                agent["active_task"] = None
                agent["elapsed_ms"] = 0

            # Tool call count (subtasks completed by this agent)
            agent["tool_calls"] = _safe_count(
                conn,
                "SELECT COUNT(*) FROM agent_subtasks WHERE agent_id = ? AND status = 'completed'",
                (agent_id,),
            )

            # Estimate context usage from token tracking
            token_row = _safe_query(
                conn,
                """SELECT SUM(input_tokens) as total_in, SUM(output_tokens) as total_out
                   FROM agent_token_usage WHERE agent_id = ?""",
                (agent_id,),
            )
            if token_row and token_row[0].get("total_in"):
                total = (token_row[0]["total_in"] or 0) + (token_row[0]["total_out"] or 0)
                # Rough context percentage estimate (200k context window)
                agent["context_pct"] = min(100, round(total / 2000, 1))
            else:
                agent["context_pct"] = 0

            # Determine tier from agent config (heuristic based on name/port)
            name_lower = (agent.get("name") or "").lower()
            if any(k in name_lower for k in ("orchestrator", "architect")):
                agent["tier"] = "Core"
            elif any(k in name_lower for k in ("knowledge", "monitor")):
                agent["tier"] = "Support"
            else:
                agent["tier"] = "Domain"

        conn.close()
        return jsonify({"status": "ok", "agents": agents})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Workflows
# =====================================================================

@orchestration_api.route("/workflows")
def get_workflows():
    """Return active and recent workflows."""
    try:
        conn = _get_db()
        limit = int(request.args.get("limit", 20))
        status_filter = request.args.get("status")

        if status_filter:
            workflows = _safe_query(
                conn,
                """SELECT id, name, project_id, status, total_subtasks,
                          completed_subtasks, failed_subtasks, created_by,
                          classification, created_at, updated_at, completed_at
                   FROM agent_workflows
                   WHERE status = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (status_filter, limit),
            )
        else:
            workflows = _safe_query(
                conn,
                """SELECT id, name, project_id, status, total_subtasks,
                          completed_subtasks, failed_subtasks, created_by,
                          classification, created_at, updated_at, completed_at
                   FROM agent_workflows
                   ORDER BY CASE WHEN status = 'running' THEN 0
                                 WHEN status = 'pending' THEN 1
                                 ELSE 2 END,
                            created_at DESC
                   LIMIT ?""",
                (limit,),
            )

        # Calculate duration for each workflow
        for wf in workflows:
            if wf.get("completed_at") and wf.get("created_at"):
                try:
                    start = datetime.fromisoformat(wf["created_at"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(wf["completed_at"].replace("Z", "+00:00"))
                    wf["duration_ms"] = int((end - start).total_seconds() * 1000)
                except (ValueError, TypeError):
                    wf["duration_ms"] = None
            elif wf.get("status") == "running" and wf.get("created_at"):
                try:
                    start = datetime.fromisoformat(wf["created_at"].replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    wf["duration_ms"] = int((now - start).total_seconds() * 1000)
                except (ValueError, TypeError):
                    wf["duration_ms"] = None
            else:
                wf["duration_ms"] = None

        conn.close()
        return jsonify({"status": "ok", "workflows": workflows})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Workflow DAG
# =====================================================================

@orchestration_api.route("/workflows/<workflow_id>/dag")
def get_workflow_dag(workflow_id):
    """Return DAG structure for SVG rendering."""
    try:
        conn = _get_db()

        subtasks = _safe_query(
            conn,
            """SELECT id, agent_id, skill_id, description, status,
                      depends_on, started_at, completed_at, duration_ms
               FROM agent_subtasks
               WHERE workflow_id = ?
               ORDER BY created_at""",
            (workflow_id,),
        )

        nodes = []
        edges = []
        for st in subtasks:
            nodes.append({
                "id": st["id"],
                "agent": st.get("agent_id", ""),
                "status": st.get("status", "pending"),
                "label": st.get("skill_id") or st.get("description") or st["id"][:8],
                "duration_ms": st.get("duration_ms"),
            })
            # Parse dependencies
            deps = st.get("depends_on")
            if deps:
                try:
                    dep_list = json.loads(deps)
                    if isinstance(dep_list, list):
                        for dep_id in dep_list:
                            edges.append({"from": dep_id, "to": st["id"]})
                    elif isinstance(dep_list, str):
                        edges.append({"from": dep_list, "to": st["id"]})
                except (json.JSONDecodeError, TypeError):
                    # Try comma-separated
                    for dep_id in str(deps).split(","):
                        dep_id = dep_id.strip()
                        if dep_id:
                            edges.append({"from": dep_id, "to": st["id"]})

        conn.close()
        return jsonify({"status": "ok", "nodes": nodes, "edges": edges})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Mailbox
# =====================================================================

@orchestration_api.route("/mailbox")
def get_mailbox():
    """Return recent agent mailbox messages."""
    try:
        conn = _get_db()
        limit = int(request.args.get("limit", 50))

        messages = _safe_query(
            conn,
            """SELECT id, from_agent_id, to_agent_id, message_type, subject,
                      priority, read_at, created_at
               FROM agent_mailbox
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )

        conn.close()
        return jsonify({"status": "ok", "messages": messages})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@orchestration_api.route("/mailbox/stream")
def stream_mailbox():
    """SSE stream for real-time mailbox updates (D29 — SSE over WebSocket)."""
    def generate():
        last_id = request.args.get("since", "")
        while True:
            try:
                conn = _get_db()
                if last_id:
                    messages = _safe_query(
                        conn,
                        """SELECT id, from_agent_id, to_agent_id, message_type,
                                  subject, priority, created_at
                           FROM agent_mailbox
                           WHERE created_at > (SELECT created_at FROM agent_mailbox WHERE id = ?)
                           ORDER BY created_at DESC LIMIT 10""",
                        (last_id,),
                    )
                else:
                    messages = _safe_query(
                        conn,
                        """SELECT id, from_agent_id, to_agent_id, message_type,
                                  subject, priority, created_at
                           FROM agent_mailbox
                           ORDER BY created_at DESC LIMIT 10""",
                    )
                conn.close()

                if messages:
                    last_id = messages[0].get("id", last_id)
                    yield f"data: {json.dumps({'messages': messages})}\n\n"
                else:
                    yield f"data: {json.dumps({'messages': []})}\n\n"
            except Exception:
                yield f"data: {json.dumps({'messages': [], 'error': 'db_unavailable'})}\n\n"

            time.sleep(3)  # D99: SSE debounced to 3-second batches

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# =====================================================================
# Stats
# =====================================================================

@orchestration_api.route("/stats")
def get_stats():
    """Return stat grid data for the orchestration dashboard."""
    try:
        conn = _get_db()

        active_workflows = _safe_count(
            conn,
            "SELECT COUNT(*) FROM agent_workflows WHERE status = 'running'",
        )
        total_agents = _safe_count(conn, "SELECT COUNT(*) FROM agents")
        agents_running = _safe_count(
            conn,
            "SELECT COUNT(*) FROM agents WHERE status = 'active'",
        )
        subtasks_pending = _safe_count(
            conn,
            "SELECT COUNT(*) FROM agent_subtasks WHERE status IN ('pending', 'queued')",
        )
        subtasks_completed = _safe_count(
            conn,
            "SELECT COUNT(*) FROM agent_subtasks WHERE status = 'completed'",
        )
        subtasks_failed = _safe_count(
            conn,
            "SELECT COUNT(*) FROM agent_subtasks WHERE status = 'failed'",
        )
        mailbox_unread = _safe_count(
            conn,
            "SELECT COUNT(*) FROM agent_mailbox WHERE read_at IS NULL",
        )

        # Average response time from completed subtasks
        avg_row = _safe_query(
            conn,
            "SELECT AVG(duration_ms) as avg_ms FROM agent_subtasks WHERE status = 'completed' AND duration_ms IS NOT NULL",
        )
        avg_response_ms = round(avg_row[0]["avg_ms"] or 0) if avg_row and avg_row[0].get("avg_ms") else 0

        conn.close()
        return jsonify({
            "status": "ok",
            "active_workflows": active_workflows,
            "total_agents": total_agents,
            "agents_running": agents_running,
            "subtasks_pending": subtasks_pending,
            "subtasks_completed": subtasks_completed,
            "subtasks_failed": subtasks_failed,
            "mailbox_unread": mailbox_unread,
            "avg_response_ms": avg_response_ms,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Collaboration History
# =====================================================================

@orchestration_api.route("/collaboration")
def get_collaboration():
    """Return recent collaboration events between agents."""
    try:
        conn = _get_db()
        limit = int(request.args.get("limit", 30))

        collabs = _safe_query(
            conn,
            """SELECT id, project_id, agent_a_id, agent_b_id,
                      collaboration_type, outcome, lesson_learned,
                      duration_ms, created_at
               FROM agent_collaboration_history
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )

        conn.close()
        return jsonify({"status": "ok", "collaborations": collabs})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Prompt Chains (graceful — table may not exist)
# =====================================================================

@orchestration_api.route("/chains")
def get_prompt_chains():
    """Return prompt chain execution history (if table exists)."""
    try:
        conn = _get_db()
        limit = int(request.args.get("limit", 20))

        if not _table_exists(conn, "prompt_chain_executions"):
            conn.close()
            return jsonify({"status": "ok", "chains": [], "note": "prompt_chain_executions table not yet created"})

        chains = _safe_query(
            conn,
            """SELECT id, chain_name, status, steps_completed, steps_total, created_at
               FROM prompt_chain_executions
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )

        conn.close()
        return jsonify({"status": "ok", "chains": chains})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# ATLAS Critiques (graceful — table may not exist)
# =====================================================================

@orchestration_api.route("/critiques")
def get_critiques():
    """Return ATLAS critique sessions (if table exists)."""
    try:
        conn = _get_db()
        limit = int(request.args.get("limit", 20))

        if not _table_exists(conn, "atlas_critique_sessions"):
            conn.close()
            return jsonify({"status": "ok", "critiques": [], "note": "atlas_critique_sessions table not yet created"})

        critiques = _safe_query(
            conn,
            """SELECT id, status, consensus, total_findings, critical_count, created_at
               FROM atlas_critique_sessions
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )

        conn.close()
        return jsonify({"status": "ok", "critiques": critiques})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
