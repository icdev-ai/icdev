#!/usr/bin/env python3
# CUI // SP-CTI
"""E2E Test: Remote-first multi-agent dispatch across 3+ agents.

Starts 3 A2A agent servers (orchestrator, builder, compliance) as subprocesses,
registers them in the SQLite agents table, then runs a TeamOrchestrator DAG
workflow and verifies end-to-end A2A HTTP dispatch.

Usage:
    pytest tests/e2e/test_remote_agent_dispatch.py -v
    python tests/e2e/test_remote_agent_dispatch.py --demo   # manual run with output

Requires: flask, requests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from tools.a2a.agent_registry import register_agent
from tools.agent.team_orchestrator import Subtask, TeamOrchestrator, Workflow

AGENT_PORTS = {
    "orchestrator-agent": 18443,
    "builder-agent": 18445,
    "compliance-agent": 18446,
}


@pytest.fixture(scope="module")
def agent_servers():
    """Start 3 agent servers on ephemeral ports, yield, then terminate."""
    procs = []
    log_files = []
    try:
        for agent_id, port in AGENT_PORTS.items():
            log_path = ROOT / ".tmp" / f"e2e_{agent_id}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_f = open(log_path, "a", encoding="utf-8")
            log_files.append(log_f)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "tools/a2a/agent_entrypoint.py",
                    "--agent-id", agent_id,
                    "--port", str(port),
                    "--no-auto-register",
                    "--no-tls",
                ],
                stdout=log_f,
                stderr=log_f,
                cwd=str(ROOT),
                env={**os.environ, "PYTHONPATH": str(ROOT)},
            )
            procs.append(proc)

        # Wait for all agents to be healthy
        import urllib.request

        for agent_id, port in AGENT_PORTS.items():
            health_url = f"http://127.0.0.1:{port}/health"
            for _ in range(30):
                try:
                    with urllib.request.urlopen(health_url, timeout=1) as resp:
                        if resp.status == 200:
                            break
                except Exception:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError(f"Agent {agent_id} did not start on port {port}")

        # Register agents in DB
        for agent_id, port in AGENT_PORTS.items():
            register_agent(
                agent_id=agent_id,
                name=agent_id.replace("-", " ").title(),
                description=f"E2E test {agent_id}",
                url=f"http://127.0.0.1:{port}",
                capabilities={"port": port},
            )

        yield procs

    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log_f in log_files:
            log_f.close()


def test_agent_health(agent_servers):
    """Verify all 3 agents respond to health checks."""
    import urllib.request

    for agent_id, port in AGENT_PORTS.items():
        url = f"http://127.0.0.1:{port}/health"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            assert data["status"] == "healthy"
            assert data["agent_id"] == agent_id


def test_agent_card_skills(agent_servers):
    """Verify builder agent has skills from its agent card."""
    import urllib.request

    url = f"http://127.0.0.1:{AGENT_PORTS['builder-agent']}/.well-known/agent.json"
    with urllib.request.urlopen(url, timeout=5) as resp:
        card = json.loads(resp.read())
        skill_ids = {s["id"] for s in card.get("skills", [])}
        assert "write-tests" in skill_ids or "generate-code" in skill_ids


def test_orchestrator_dispatches_to_builder(agent_servers):
    """Remote-first mode: TeamOrchestrator dispatches a task to builder over HTTP."""
    os.environ["ICDEV_AGENT_MODE"] = "remote"
    try:
        orch = TeamOrchestrator(db_path=str(ROOT / "data" / "icdev.db"))
        wf = Workflow(
            id=f"e2e-wf-{uuid.uuid4().hex[:8]}",
            name="Remote dispatch test",
        )
        st = Subtask(
            id=f"e2e-st-{uuid.uuid4().hex[:8]}",
            agent_id="builder-agent",
            skill_id="echo",
            description="Ping builder agent",
            input_data={"message": "hello"},
        )
        wf.subtasks[st.id] = st

        result = orch._execute_subtask(st, {"workflow_id": wf.id, "project_id": "e2e-test"})

        # In remote mode, if builder is reachable it should complete (echo skill is always registered)
        assert result.status == "completed", f"Expected completed, got {result.status}: {result.error_message}"
        assert result.output_data is not None
    finally:
        del os.environ["ICDEV_AGENT_MODE"]


def test_orchestrator_fails_hard_on_unreachable_agent():
    """Remote-first mode: fail hard when agent URL is unreachable."""
    # Register a fake agent with a port that nothing is listening on
    fake_agent_id = f"fake-agent-{uuid.uuid4().hex[:4]}"
    fake_port = 19999
    register_agent(
        agent_id=fake_agent_id,
        name="Fake Agent",
        description="Unreachable test agent",
        url=f"http://127.0.0.1:{fake_port}",
        capabilities={},
    )

    os.environ["ICDEV_AGENT_MODE"] = "remote"
    try:
        orch = TeamOrchestrator(db_path=str(ROOT / "data" / "icdev.db"))
        st = Subtask(
            id=f"e2e-st-{uuid.uuid4().hex[:8]}",
            agent_id=fake_agent_id,
            skill_id="echo",
            description="Ping unreachable agent",
            input_data={},
        )

        result = orch._execute_subtask(st, {"workflow_id": "e2e-fake", "project_id": "e2e-test"})

        assert result.status == "failed"
        assert "REMOTE-MODE" in result.error_message
        assert "unreachable" in result.error_message.lower()
    finally:
        del os.environ["ICDEV_AGENT_MODE"]


if __name__ == "__main__":
    if "--demo" in sys.argv:
        print("Starting E2E remote dispatch demo...")
        # Manual run: start agents, run tests, stop agents
        servers = None
        try:
            gen = agent_servers()
            servers = next(gen)
            print("Agents started and registered.")

            test_agent_health(servers)
            print("[PASS] Agent health checks")

            test_agent_card_skills(servers)
            print("[PASS] Agent card skills")

            test_orchestrator_dispatches_to_builder(servers)
            print("[PASS] Remote dispatch to builder")

            test_orchestrator_fails_hard_on_unreachable_agent()
            print("[PASS] Fail hard on unreachable agent")

            print("\nAll E2E tests passed!")
        except Exception as exc:
            print(f"[FAIL] {exc}")
            raise
        finally:
            if servers is not None:
                try:
                    next(gen)
                except StopIteration:
                    pass
    else:
        pytest.main([__file__, "-v"])
