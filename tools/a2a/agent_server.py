#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A Agent Server — HTTP-based agent server implementing the A2A protocol.

Uses Flask for HTTP. Implements:
- GET /.well-known/agent.json  -> Agent Card
- POST /tasks/send             -> Create and process task (JSON-RPC 2.0)
- GET /tasks/<task_id>         -> Get task status
- POST /tasks/<task_id>/cancel -> Cancel task
- Skill registration with handler functions
- Task lifecycle management with history tracking
- Tasks stored in icdev.db a2a_tasks table
- Mutual TLS support (configurable, optional for dev)
"""

import argparse
import json
import logging
import os
import sqlite3
import ssl
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from flask import Flask, jsonify, request
except ImportError:
    Flask = None  # Handled at runtime

from tools.a2a.task import Task, TaskStatus

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("a2a.server")


class A2AAgentServer:
    """Base A2A agent server with skill registration and task management."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        description: str,
        host: str = "0.0.0.0",
        port: int = 8443,
        version: str = "1.0.0",
        db_path: Optional[Path] = None,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
        tls_ca: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.host = host
        self.port = port
        self.version = version
        self.db_path = db_path or DB_PATH
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.tls_ca = tls_ca

        # Skill registry: skill_id -> handler function
        self._skills: Dict[str, Dict[str, Any]] = {}
        # In-memory task cache for fast access (also persisted to DB)
        self._tasks: Dict[str, Task] = {}
        # Thread pool for async task execution (Phase B)
        self._executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix=f"{agent_id}-worker")

        if Flask is None:
            raise ImportError("Flask is required. Install with: pip install flask")

        self.app = Flask(self.name)
        self._register_routes()

    def _register_routes(self) -> None:
        """Register all HTTP routes."""

        @self.app.route("/.well-known/agent.json", methods=["GET"])
        def agent_card():
            return jsonify(self.get_agent_card())

        @self.app.route("/tasks/send", methods=["POST"])
        def send_task():
            return self._handle_send_task()

        @self.app.route("/tasks/<task_id>", methods=["GET"])
        def get_task(task_id):
            return self._handle_get_task(task_id)

        @self.app.route("/tasks/<task_id>/cancel", methods=["POST"])
        def cancel_task(task_id):
            return self._handle_cancel_task(task_id)

        @self.app.route("/health", methods=["GET"])
        def health():
            return jsonify({"status": "healthy", "agent_id": self.agent_id})

        # ── Mailbox Routes (Phase C) ──────────────────────────────

        @self.app.route("/messages/send", methods=["POST"])
        def send_message():
            return self._handle_send_message()

        @self.app.route("/messages/inbox", methods=["GET"])
        def inbox():
            return self._handle_inbox()

    def register_skill(
        self,
        skill_id: str,
        handler: Callable[[Task], Task],
        name: str = "",
        description: str = "",
        input_modes: Optional[list] = None,
        output_modes: Optional[list] = None,
    ) -> None:
        """Register a skill handler function.

        Args:
            skill_id: Unique skill identifier.
            handler: Function that takes a Task and returns the processed Task.
            name: Human-readable skill name.
            description: What the skill does.
            input_modes: Accepted input MIME types (default: ["application/json"]).
            output_modes: Produced output MIME types (default: ["application/json"]).
        """
        self._skills[skill_id] = {
            "id": skill_id,
            "name": name or skill_id,
            "description": description or f"Skill: {skill_id}",
            "handler": handler,
            "inputModes": input_modes or ["application/json"],
            "outputModes": output_modes or ["application/json"],
        }
        logger.info(f"Registered skill: {skill_id}")

    def get_agent_card(self) -> dict:
        """Build the Agent Card JSON for /.well-known/agent.json."""
        skills = []
        for sid, info in self._skills.items():
            skills.append({
                "id": info["id"],
                "name": info["name"],
                "description": info["description"],
                "inputModes": info["inputModes"],
                "outputModes": info["outputModes"],
            })

        card = {
            "name": self.name,
            "description": self.description,
            "url": f"https://{self.host}:{self.port}",
            "version": self.version,
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
            },
            "authentication": {
                "schemes": ["mutual_tls", "api_key"],
            },
            "skills": skills,
        }
        return card

    # ── Task Handlers ──────────────────────────────────────────────

    def _handle_send_task(self):
        """Handle POST /tasks/send (JSON-RPC 2.0 envelope)."""
        body = request.get_json(silent=True)
        if not body:
            return self._jsonrpc_error(None, -32700, "Parse error: invalid JSON")

        rpc_id = body.get("id")

        # Validate JSON-RPC 2.0 structure
        if body.get("jsonrpc") != "2.0":
            return self._jsonrpc_error(rpc_id, -32600, "Invalid JSON-RPC version")

        method = body.get("method")
        if method != "tasks/send":
            return self._jsonrpc_error(rpc_id, -32601, f"Unknown method: {method}")

        params = body.get("params", {})
        skill_id = params.get("skill_id")
        input_data = params.get("input_data", {})
        task_id = params.get("id", str(uuid.uuid4()))
        metadata = params.get("metadata", {})

        # Extract correlation ID from incoming A2A request (D149)
        try:
            from tools.resilience.correlation import set_correlation_id
            cid = metadata.get("correlation_id")
            if cid:
                set_correlation_id(cid)
        except ImportError:
            pass
        # D285: Extract W3C traceparent and restore trace context
        try:
            from tools.observability.trace_context import parse_traceparent, set_current_context
            tp = metadata.get("traceparent")
            if tp:
                ctx = parse_traceparent(tp)
                if ctx:
                    set_current_context(ctx)
        except ImportError:
            pass

        if not skill_id:
            return self._jsonrpc_error(rpc_id, -32602, "Missing required param: skill_id")

        if skill_id not in self._skills:
            return self._jsonrpc_error(rpc_id, -32602, f"Unknown skill: {skill_id}")

        # Create the task
        task = Task(
            id=task_id,
            skill_id=skill_id,
            input_data=input_data,
            metadata=metadata,
        )

        # Check for async mode (default: async for better throughput)
        sync_mode = params.get("sync", False)

        # Persist to DB
        self._persist_task(task)
        self._tasks[task.id] = task

        if sync_mode:
            # Synchronous execution (backward compatible)
            self._execute_task_sync(task, skill_id)
        else:
            # Async execution via ThreadPoolExecutor (Phase B)
            task.update_status(TaskStatus.WORKING.value, "Queued for async processing")
            self._persist_task(task)
            self._executor.submit(self._execute_task_async, task.id, skill_id)

        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": task.to_dict(),
        })

    def _execute_task_sync(self, task: Task, skill_id: str) -> None:
        """Execute a task synchronously in the request thread."""
        try:
            task.update_status(TaskStatus.WORKING.value, "Processing started")
            self._persist_task(task)

            handler = self._skills[skill_id]["handler"]
            task = handler(task)

            if task.status == TaskStatus.WORKING.value:
                task.update_status(TaskStatus.COMPLETED.value, "Processing completed")

            self._persist_task(task)
        except Exception as e:
            logger.exception(f"Skill handler failed for {skill_id}")
            task.update_status(TaskStatus.FAILED.value, f"Handler error: {str(e)}")
            self._persist_task(task)
        self._tasks[task.id] = task

    def _execute_task_async(self, task_id: str, skill_id: str) -> None:
        """Execute a task asynchronously in a worker thread."""
        task = self._tasks.get(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in cache for async execution")
            return

        try:
            handler = self._skills[skill_id]["handler"]
            task = handler(task)

            if task.status == TaskStatus.WORKING.value:
                task.update_status(TaskStatus.COMPLETED.value, "Processing completed")

            self._persist_task(task)
            self._tasks[task.id] = task
            logger.info(f"Async task {task_id} completed with status: {task.status}")

        except Exception as e:
            logger.exception(f"Async skill handler failed for {skill_id} (task {task_id})")
            task.update_status(TaskStatus.FAILED.value, f"Handler error: {str(e)}")
            self._persist_task(task)
            self._tasks[task.id] = task

    def _handle_get_task(self, task_id: str):
        """Handle GET /tasks/<task_id>."""
        # Try memory cache first
        if task_id in self._tasks:
            return jsonify(self._tasks[task_id].to_dict())

        # Fall back to DB
        task_dict = self._load_task_from_db(task_id)
        if task_dict:
            return jsonify(task_dict)

        return jsonify({"error": f"Task not found: {task_id}"}), 404

    def _handle_cancel_task(self, task_id: str):
        """Handle POST /tasks/<task_id>/cancel."""
        task = self._tasks.get(task_id)
        if not task:
            task_dict = self._load_task_from_db(task_id)
            if task_dict:
                task = Task.from_dict(task_dict)
            else:
                return jsonify({"error": f"Task not found: {task_id}"}), 404

        if task.is_terminal():
            return jsonify({"error": f"Task already in terminal state: {task.status}"}), 400

        task.update_status(TaskStatus.CANCELED.value, "Canceled by request")
        self._tasks[task.id] = task
        self._persist_task(task)

        return jsonify(task.to_dict())

    # ── Mailbox Handlers (Phase C) ─────────────────────────────────

    def _handle_send_message(self):
        """Handle POST /messages/send — deliver a message to this agent's mailbox."""
        body = request.get_json(silent=True)
        if not body:
            return jsonify({"error": "Invalid JSON"}), 400

        try:
            from tools.agent.mailbox import send
            msg_id = send(
                from_agent_id=body.get("from_agent_id", "unknown"),
                to_agent_id=self.agent_id,
                message_type=body.get("message_type", "notification"),
                subject=body.get("subject", ""),
                body=body.get("body", ""),
                priority=body.get("priority", 5),
                in_reply_to=body.get("in_reply_to"),
                db_path=self.db_path,
            )
            return jsonify({"message_id": msg_id, "status": "delivered"})
        except ImportError:
            return jsonify({"error": "Mailbox module not available"}), 501
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def _handle_inbox(self):
        """Handle GET /messages/inbox — retrieve messages for this agent."""
        try:
            from tools.agent.mailbox import receive
            unread_only = request.args.get("unread_only", "true").lower() == "true"
            message_type = request.args.get("message_type")
            limit = int(request.args.get("limit", 50))

            messages = receive(
                agent_id=self.agent_id,
                unread_only=unread_only,
                message_type=message_type,
                limit=limit,
                db_path=self.db_path,
            )
            return jsonify({"agent_id": self.agent_id, "messages": messages, "count": len(messages)})
        except ImportError:
            return jsonify({"error": "Mailbox module not available"}), 501
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Database Persistence ────────────────────────────────────────

    def _get_db(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _persist_task(self, task: Task) -> None:
        """Save or update a task in the a2a_tasks table."""
        conn = self._get_db()
        try:
            c = conn.cursor()
            # Upsert the task
            c.execute(
                """INSERT INTO a2a_tasks (id, skill_id, status, input_data, output_data,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   status = excluded.status,
                   output_data = excluded.output_data,
                   updated_at = excluded.updated_at,
                   completed_at = CASE
                       WHEN excluded.status IN ('completed', 'failed', 'canceled')
                       THEN CURRENT_TIMESTAMP
                       ELSE a2a_tasks.completed_at
                   END""",
                (
                    task.id,
                    task.skill_id,
                    task.status,
                    json.dumps(task.input_data) if task.input_data else None,
                    json.dumps(task.output_data) if task.output_data else None,
                    task.created_at,
                    task.updated_at,
                ),
            )

            # Persist the latest history event
            if task.history:
                latest = task.history[-1]
                c.execute(
                    """INSERT INTO a2a_task_history (task_id, status, message, timestamp)
                       VALUES (?, ?, ?, ?)""",
                    (task.id, latest.status, latest.message, latest.timestamp),
                )

            # Persist artifacts
            for artifact in task.artifacts:
                c.execute(
                    """INSERT OR IGNORE INTO a2a_task_artifacts
                       (task_id, name, content_type, data, classification)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        task.id,
                        artifact.name,
                        artifact.content_type,
                        json.dumps(artifact.data) if artifact.data else None,
                        artifact.classification,
                    ),
                )

            conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist task {task.id}: {e}")
            conn.rollback()
        finally:
            conn.close()

    def _load_task_from_db(self, task_id: str) -> Optional[dict]:
        """Load a task from the database."""
        conn = self._get_db()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM a2a_tasks WHERE id = ?", (task_id,))
            row = c.fetchone()
            if not row:
                return None

            # Load history
            c.execute(
                "SELECT status, message, timestamp FROM a2a_task_history WHERE task_id = ? ORDER BY id",
                (task_id,),
            )
            history = [
                {"status": r["status"], "message": r["message"], "timestamp": r["timestamp"]}
                for r in c.fetchall()
            ]

            # Load artifacts
            c.execute(
                "SELECT name, content_type, data, classification FROM a2a_task_artifacts WHERE task_id = ?",
                (task_id,),
            )
            artifacts = [
                {
                    "name": r["name"],
                    "content_type": r["content_type"],
                    "data": json.loads(r["data"]) if r["data"] else None,
                    "classification": r["classification"],
                }
                for r in c.fetchall()
            ]

            return {
                "id": row["id"],
                "status": row["status"],
                "skill_id": row["skill_id"],
                "input_data": json.loads(row["input_data"]) if row["input_data"] else None,
                "output_data": json.loads(row["output_data"]) if row["output_data"] else None,
                "artifacts": artifacts,
                "history": history,
                "metadata": {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    # ── JSON-RPC Helpers ────────────────────────────────────────────

    @staticmethod
    def _jsonrpc_error(rpc_id, code: int, message: str):
        """Return a JSON-RPC 2.0 error response."""
        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": message},
        }), 400 if code != -32700 else 422

    # ── Server Control ──────────────────────────────────────────────

    def run(self, debug: bool = False) -> None:
        """Start the agent server.

        If TLS cert/key are provided, uses SSL context with optional
        mutual TLS (client certificate verification via CA).
        """
        ssl_ctx = None
        if self.tls_cert and self.tls_key:
            if os.path.exists(self.tls_cert) and os.path.exists(self.tls_key):
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(self.tls_cert, self.tls_key)
                if self.tls_ca and os.path.exists(self.tls_ca):
                    ssl_ctx.verify_mode = ssl.CERT_REQUIRED
                    ssl_ctx.load_verify_locations(self.tls_ca)
                    logger.info("Mutual TLS enabled (client cert required)")
                else:
                    logger.info("TLS enabled (server-side only)")
            else:
                logger.warning(
                    f"TLS cert/key not found ({self.tls_cert}, {self.tls_key}). "
                    "Starting without TLS."
                )

        logger.info(f"Starting {self.name} on {self.host}:{self.port}")
        self.app.run(
            host=self.host,
            port=self.port,
            debug=debug,
            ssl_context=ssl_ctx,
        )


def main():
    parser = argparse.ArgumentParser(description="Run an A2A agent server")
    parser.add_argument("--agent-id", default="test-agent", help="Agent ID")
    parser.add_argument("--name", default="Test Agent", help="Agent name")
    parser.add_argument("--description", default="A test A2A agent", help="Agent description")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8443, help="Bind port")
    parser.add_argument("--tls-cert", help="TLS certificate path")
    parser.add_argument("--tls-key", help="TLS private key path")
    parser.add_argument("--tls-ca", help="TLS CA cert for mutual TLS")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    server = A2AAgentServer(
        agent_id=args.agent_id,
        name=args.name,
        description=args.description,
        host=args.host,
        port=args.port,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        tls_ca=args.tls_ca,
    )

    # Register a default echo skill for testing
    def echo_handler(task: Task) -> Task:
        task.output_data = {"echo": task.input_data}
        task.update_status(TaskStatus.COMPLETED.value, "Echo complete")
        return task

    server.register_skill(
        skill_id="echo",
        handler=echo_handler,
        name="Echo",
        description="Echoes back the input data (for testing)",
    )

    server.run(debug=args.debug)


if __name__ == "__main__":
    main()
