#!/usr/bin/env python3
# CUI // SP-CTI
"""ACE A2A Server — exposes the ACE Co-Worker Engine as an A2A agent on port 8460.

Routes:
  GET  /.well-known/agent.json  — agent card (name='ace', skill='coworker.launch')
  POST /task                    — simplified launch endpoint; returns {"task_id": ...}
  POST /tasks/send              — full JSON-RPC 2.0 A2A endpoint (inherited)
"""

import argparse
from pathlib import Path
from typing import Optional

from tools.a2a.agent_server import A2AAgentServer
from tools.a2a.task import Task, TaskStatus
from tools.logging.icdev_logger import get_logger

logger = get_logger("a2a.ace")

PORT = 8460
AGENT_ID = "ace"
AGENT_NAME = "ace"
AGENT_DESCRIPTION = "ACE Co-Worker Engine — autonomous multi-role collaborative AI team"

SKILL_ID = "coworker.launch"
SKILL_NAME = "Launch ACE coworker team"
SKILL_DESCRIPTION = "Launch ACE coworker team"
SKILL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "problem_text": {"type": "string", "description": "Problem or task description"},
        "role_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Role IDs to include",
        },
    },
    "required": ["problem_text"],
}


def _make_launch_handler():
    """Return a Task handler that delegates to ACEController.launch()."""

    def handle(task: Task) -> Task:
        try:
            from icdev.tools.ace.controller import ACEController

            instance_id = ACEController.get_instance().launch(
                problem_text=task.input_data.get("problem_text", ""),
                trigger_source="a2a",
                trigger_ref=task.id,
            )
            task.output_data = {"task_id": instance_id, "status": "launched"}
            task.update_status(TaskStatus.COMPLETED.value, f"ACE launched: {instance_id}")
        except Exception as exc:
            logger.error("ACE launch failed: %s", exc)
            task.output_data = {"error": str(exc)}
            task.update_status(TaskStatus.FAILED.value, f"Launch error: {exc}")
        return task

    return handle


def create_ace_server(
    host: str = "0.0.0.0",  # nosec B104 -- intentional bind-all for containerised/dev deployment
    port: int = PORT,
    db_path: Optional[Path] = None,
    register: bool = True,
) -> A2AAgentServer:
    """Build and configure the ACE A2A agent server.

    Args:
        host: Bind address.
        port: Bind port (default 8460).
        db_path: Override agent registry DB path.
        register: If True, call register_agent() on startup.
    """
    server = A2AAgentServer(
        agent_id=AGENT_ID,
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        host=host,
        port=port,
        db_path=db_path,
    )

    server.register_skill(
        skill_id=SKILL_ID,
        handler=_make_launch_handler(),
        name=SKILL_NAME,
        description=SKILL_DESCRIPTION,
    )

    # Simplified /task endpoint: accepts {problem_text, role_ids?}, returns {task_id}
    @server.app.route("/task", methods=["POST"])
    def post_task():
        from flask import jsonify, request

        body = request.get_json(silent=True) or {}
        problem_text = body.get("problem_text", "")
        if not problem_text:
            return jsonify({"error": "problem_text is required"}), 400

        try:
            from icdev.tools.ace.controller import ACEController

            task_id = ACEController.get_instance().launch(
                problem_text=problem_text,
                trigger_source="a2a",
                trigger_ref="http",
            )
            return jsonify({"task_id": task_id})
        except Exception as exc:
            logger.error("ACE /task failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    if register:
        try:
            from tools.a2a.agent_registry import register_agent

            register_agent(
                agent_id=AGENT_ID,
                name=AGENT_NAME,
                description=AGENT_DESCRIPTION,
                url=f"http://{host}:{port}",
                capabilities={
                    "skills": [
                        {
                            "id": SKILL_ID,
                            "name": SKILL_NAME,
                            "description": SKILL_DESCRIPTION,
                            "input_schema": SKILL_INPUT_SCHEMA,
                        }
                    ]
                },
                db_path=db_path,
            )
            logger.info("ACE registered in agent registry at http://%s:%d", host, port)
        except Exception as exc:
            logger.warning("Could not register ACE in agent registry: %s", exc)

    return server


def main():
    parser = argparse.ArgumentParser(description="ACE A2A Agent Server (port 8460)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")  # nosec B104
    parser.add_argument("--port", type=int, default=PORT, help="Bind port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    server = create_ace_server(host=args.host, port=args.port)
    logger.info("Starting ACE A2A server on %s:%d", args.host, args.port)
    server.run(debug=args.debug)


if __name__ == "__main__":
    main()
