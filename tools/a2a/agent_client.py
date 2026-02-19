#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A Agent Client — sends tasks to remote A2A agents.

Implements:
- discover_agent(url) -> GET /.well-known/agent.json
- send_task(url, skill_id, input_data, project_id) -> POST task (JSON-RPC 2.0)
- get_task_status(url, task_id) -> GET task status
- cancel_task(url, task_id) -> cancel
- wait_for_completion(url, task_id, timeout) -> poll until done
"""

import argparse
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # Handled at runtime

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class A2AAgentClient:
    """Client for interacting with A2A agent servers."""

    def __init__(
        self,
        client_cert: Optional[str] = None,
        client_key: Optional[str] = None,
        ca_cert: Optional[str] = None,
        api_key: Optional[str] = None,
        verify_ssl: bool = True,
        timeout: int = 30,
    ):
        """Initialize the A2A client.

        Args:
            client_cert: Path to client TLS certificate (for mutual TLS).
            client_key: Path to client TLS private key (for mutual TLS).
            ca_cert: Path to CA certificate for server verification.
            api_key: API key for authentication.
            verify_ssl: Whether to verify SSL certificates (disable for dev).
            timeout: Default request timeout in seconds.
        """
        if requests is None:
            raise ImportError("requests is required. Install with: pip install requests")

        self.session = requests.Session()
        self.timeout = timeout

        # Mutual TLS
        if client_cert and client_key:
            self.session.cert = (client_cert, client_key)
        if ca_cert:
            self.session.verify = ca_cert
        elif not verify_ssl:
            self.session.verify = False
        # API key
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

        self.session.headers["Content-Type"] = "application/json"

    def discover_agent(self, agent_url: str) -> dict:
        """Fetch the Agent Card from /.well-known/agent.json.

        Args:
            agent_url: Base URL of the agent (e.g. https://localhost:8443).

        Returns:
            Agent Card dictionary with name, description, skills, etc.

        Raises:
            ConnectionError: If the agent is unreachable.
            ValueError: If the response is not valid JSON.
        """
        url = f"{agent_url.rstrip('/')}/.well-known/agent.json"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        card = resp.json()
        return card

    def send_task(
        self,
        agent_url: str,
        skill_id: str,
        input_data: Dict[str, Any],
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Send a task to an A2A agent using JSON-RPC 2.0.

        Args:
            agent_url: Base URL of the target agent.
            skill_id: The skill to invoke on the agent.
            input_data: Input data for the task.
            project_id: Optional project ID for tracking.
            task_id: Optional task ID (auto-generated if not provided).
            metadata: Optional metadata dict.

        Returns:
            Task result dictionary from the agent.
        """
        url = f"{agent_url.rstrip('/')}/tasks/send"
        rpc_id = str(uuid.uuid4())
        t_id = task_id or str(uuid.uuid4())

        meta = metadata or {}
        if project_id:
            meta["project_id"] = project_id

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tasks/send",
            "params": {
                "id": t_id,
                "skill_id": skill_id,
                "input_data": input_data,
                "metadata": meta,
            },
        }

        resp = self.session.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()

        if "error" in body:
            raise RuntimeError(
                f"A2A error ({body['error']['code']}): {body['error']['message']}"
            )

        return body.get("result", body)

    def get_task_status(self, agent_url: str, task_id: str) -> dict:
        """Get the status of a task.

        Args:
            agent_url: Base URL of the agent.
            task_id: ID of the task to query.

        Returns:
            Task dictionary with current status, history, artifacts.
        """
        url = f"{agent_url.rstrip('/')}/tasks/{task_id}"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def cancel_task(self, agent_url: str, task_id: str) -> dict:
        """Cancel a running task.

        Args:
            agent_url: Base URL of the agent.
            task_id: ID of the task to cancel.

        Returns:
            Updated task dictionary.
        """
        url = f"{agent_url.rstrip('/')}/tasks/{task_id}/cancel"
        resp = self.session.post(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(
        self,
        agent_url: str,
        task_id: str,
        timeout: int = 300,
        poll_interval: float = 2.0,
    ) -> dict:
        """Poll a task until it reaches a terminal state.

        Args:
            agent_url: Base URL of the agent.
            task_id: ID of the task to monitor.
            timeout: Maximum seconds to wait.
            poll_interval: Seconds between polls.

        Returns:
            Final task dictionary.

        Raises:
            TimeoutError: If task does not complete within timeout.
        """
        terminal_states = {"completed", "failed", "canceled"}
        start = time.time()

        while (time.time() - start) < timeout:
            task = self.get_task_status(agent_url, task_id)
            status = task.get("status", "")

            if status in terminal_states:
                return task

            time.sleep(poll_interval)

        raise TimeoutError(
            f"Task {task_id} did not complete within {timeout} seconds. "
            f"Last status: {status}"
        )


    def send_tasks_parallel(
        self,
        tasks: List[Dict[str, Any]],
        max_workers: int = 5,
    ) -> List[Dict[str, Any]]:
        """Send multiple tasks to agents in parallel using ThreadPoolExecutor.

        Args:
            tasks: List of task dicts, each with keys:
                - agent_url (str): Target agent URL
                - skill_id (str): Skill to invoke
                - input_data (dict): Input data
                - project_id (str, optional): Project ID
                - task_id (str, optional): Task ID
            max_workers: Max concurrent dispatches.

        Returns:
            List of result dicts with task_id, status, result/error.
        """
        results = []

        def _dispatch(task_spec):
            t_id = task_spec.get("task_id", str(uuid.uuid4()))
            try:
                result = self.send_task(
                    agent_url=task_spec["agent_url"],
                    skill_id=task_spec["skill_id"],
                    input_data=task_spec.get("input_data", {}),
                    project_id=task_spec.get("project_id"),
                    task_id=t_id,
                )
                return {"task_id": t_id, "status": "dispatched", "result": result}
            except Exception as exc:
                return {"task_id": t_id, "status": "error", "error": str(exc)}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_dispatch, t): t for t in tasks}
            for future in as_completed(futures):
                results.append(future.result())

        return results


def main():
    parser = argparse.ArgumentParser(description="A2A Agent Client CLI")
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    p_discover = sub.add_parser("discover", help="Discover an agent's capabilities")
    p_discover.add_argument("--url", required=True, help="Agent base URL")

    # send
    p_send = sub.add_parser("send", help="Send a task to an agent")
    p_send.add_argument("--url", required=True, help="Agent base URL")
    p_send.add_argument("--skill", required=True, help="Skill ID to invoke")
    p_send.add_argument("--input", required=True, help="JSON input data string")
    p_send.add_argument("--project", help="Project ID")
    p_send.add_argument("--wait", action="store_true", help="Wait for completion")
    p_send.add_argument("--timeout", type=int, default=300, help="Wait timeout (seconds)")

    # status
    p_status = sub.add_parser("status", help="Get task status")
    p_status.add_argument("--url", required=True, help="Agent base URL")
    p_status.add_argument("--task-id", required=True, help="Task ID")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel a task")
    p_cancel.add_argument("--url", required=True, help="Agent base URL")
    p_cancel.add_argument("--task-id", required=True, help="Task ID")

    # Common options
    parser.add_argument("--no-verify-ssl", action="store_true", help="Disable SSL verification")
    parser.add_argument("--api-key", help="API key for authentication")
    parser.add_argument("--client-cert", help="Client TLS certificate path")
    parser.add_argument("--client-key", help="Client TLS key path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    client = A2AAgentClient(
        client_cert=args.client_cert if hasattr(args, "client_cert") else None,
        client_key=args.client_key if hasattr(args, "client_key") else None,
        api_key=args.api_key if hasattr(args, "api_key") else None,
        verify_ssl=not args.no_verify_ssl,
    )

    if args.command == "discover":
        card = client.discover_agent(args.url)
        print(json.dumps(card, indent=2))

    elif args.command == "send":
        input_data = json.loads(args.input)
        result = client.send_task(
            agent_url=args.url,
            skill_id=args.skill,
            input_data=input_data,
            project_id=getattr(args, "project", None),
        )

        if args.wait and result.get("status") not in ("completed", "failed", "canceled"):
            task_id = result.get("id")
            result = client.wait_for_completion(
                agent_url=args.url, task_id=task_id, timeout=args.timeout
            )

        print(json.dumps(result, indent=2))

    elif args.command == "status":
        result = client.get_task_status(args.url, args.task_id)
        print(json.dumps(result, indent=2))

    elif args.command == "cancel":
        result = client.cancel_task(args.url, args.task_id)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
