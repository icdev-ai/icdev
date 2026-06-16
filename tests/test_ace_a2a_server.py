#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ACE A2A Server (port 8460, skill coworker.launch)."""

import sys
import types

import pytest


def _make_server():
    from tools.a2a.servers.ace_server import create_ace_server

    return create_ace_server(register=False)


class TestAceA2AServer:
    """GET /.well-known/agent.json and POST /task endpoint tests."""

    def test_agent_json_returns_200(self):
        server = _make_server()
        client = server.app.test_client()
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

    def test_agent_json_name_is_ace(self):
        server = _make_server()
        client = server.app.test_client()
        data = client.get("/.well-known/agent.json").get_json()
        assert data["name"] == "ace"

    def test_agent_json_has_skills_array(self):
        server = _make_server()
        client = server.app.test_client()
        data = client.get("/.well-known/agent.json").get_json()
        assert isinstance(data.get("skills"), list)
        assert len(data["skills"]) > 0

    def test_agent_json_skill_coworker_launch_present(self):
        server = _make_server()
        client = server.app.test_client()
        data = client.get("/.well-known/agent.json").get_json()
        skill_ids = [s["id"] for s in data["skills"]]
        assert "coworker.launch" in skill_ids

    def test_task_endpoint_rejects_missing_problem_text(self):
        server = _make_server()
        client = server.app.test_client()
        resp = client.post("/task", json={})
        assert resp.status_code == 400

    def test_task_endpoint_launches_and_returns_task_id(self, monkeypatch):
        # Stub out icdev.tools.ace.controller so the real ACE DB is not touched
        fake_instance_id = "ace-testabcdef012345"

        class _FakeController:
            @staticmethod
            def get_instance():
                return _FakeController()

            def launch(self, problem_text, trigger_source, trigger_ref):  # noqa: ARG002
                return fake_instance_id

        fake_mod = types.ModuleType("icdev.tools.ace.controller")
        fake_mod.ACEController = _FakeController
        monkeypatch.setitem(sys.modules, "icdev.tools.ace.controller", fake_mod)

        server = _make_server()
        client = server.app.test_client()
        resp = client.post("/task", json={"problem_text": "build a data pipeline"})
        assert resp.status_code == 200
        assert resp.get_json()["task_id"] == fake_instance_id
