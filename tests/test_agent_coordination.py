# CUI // SP-CTI
"""Tests for the sibling-agent coordination bus (post_result / read_result tools)."""
from __future__ import annotations

import json
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Helpers — in-memory DB fixture
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_coordination (
    id          TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL DEFAULT '',
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL DEFAULT 'null',
    posted_by   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agcoord_ns_key
    ON agent_coordination (namespace, key);
"""


def _make_conn():
    # agent_coordination authors %s placeholders for PostgreSQL; the translating
    # wrapper stands in for StorageConnection's rewrite, and unclosable keeps the
    # in-memory DB alive when the code under test closes its connection.
    from _sql_compat import translating

    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.commit()
    return translating(conn, unclosable=True)


# ---------------------------------------------------------------------------
# agent_coordination module — post_result
# ---------------------------------------------------------------------------

class TestPostResult:
    def test_post_returns_confirmation(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        result = _m.post_result("ns-a", "key1", {"data": 42}, posted_by="inst-1")
        assert "key1" in result
        assert "ns-a" in result

    def test_post_persists_value(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-b", "mykey", [1, 2, 3])
        row = conn.execute(
            "SELECT value_json FROM agent_coordination WHERE namespace='ns-b' AND key='mykey'"
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == [1, 2, 3]

    def test_post_upserts_existing(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-c", "k", "first")
        _m.post_result("ns-c", "k", "second")
        rows = conn.execute(
            "SELECT value_json FROM agent_coordination WHERE namespace='ns-c' AND key='k'"
        ).fetchall()
        assert len(rows) == 1
        assert json.loads(rows[0][0]) == "second"

    def test_post_db_unavailable_raises(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        monkeypatch.setattr(_m, "_get_conn", lambda: None)
        with pytest.raises(RuntimeError, match="unavailable"):
            _m.post_result("ns", "k", "v")

    def test_post_stores_posted_by(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-d", "item", "val", posted_by="inst-xyz")
        row = conn.execute(
            "SELECT posted_by FROM agent_coordination WHERE namespace='ns-d' AND key='item'"
        ).fetchone()
        assert row[0] == "inst-xyz"


# ---------------------------------------------------------------------------
# agent_coordination module — read_result
# ---------------------------------------------------------------------------

class TestReadResult:
    def test_read_returns_value(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-r", "item", {"score": 0.9})
        val = _m.read_result("ns-r", "item")
        assert val == {"score": 0.9}

    def test_read_missing_key_returns_sentinel(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        val = _m.read_result("ns-x", "nonexistent")
        assert val is _m._NOT_FOUND

    def test_read_different_namespace_isolated(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-1", "shared", "value-1")
        val = _m.read_result("ns-2", "shared")
        assert val is _m._NOT_FOUND

    def test_read_db_unavailable_raises(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        monkeypatch.setattr(_m, "_get_conn", lambda: None)
        with pytest.raises(RuntimeError, match="unavailable"):
            _m.read_result("ns", "k")

    def test_read_various_types(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        for value in [42, 3.14, True, None, "string", [1, 2], {"a": "b"}]:
            _m.post_result("ns-types", f"key-{type(value).__name__}", value)
            got = _m.read_result("ns-types", f"key-{type(value).__name__}")
            assert got == value


# ---------------------------------------------------------------------------
# agent_coordination module — list_results
# ---------------------------------------------------------------------------

class TestListResults:
    def test_list_returns_entries(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-l", "a", 1)
        _m.post_result("ns-l", "b", 2)
        results = _m.list_results("ns-l")
        assert len(results) == 2
        keys = {r["key"] for r in results}
        assert keys == {"a", "b"}

    def test_list_empty_namespace(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        assert _m.list_results("ns-empty") == []

    def test_list_db_unavailable_returns_empty(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        monkeypatch.setattr(_m, "_get_conn", lambda: None)
        assert _m.list_results("ns") == []

    def test_list_other_namespace_not_included(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        _m.post_result("ns-visible", "v", 1)
        _m.post_result("ns-hidden", "h", 2)
        results = _m.list_results("ns-visible")
        assert all(r["key"] == "v" for r in results)


# ---------------------------------------------------------------------------
# AgentToolRegistry handler tests
# ---------------------------------------------------------------------------

def _make_registry(monkeypatch, namespace="test-ns"):
    import icdev.tools.ace.agent_coordination as _m
    conn = _make_conn()
    monkeypatch.setattr(_m, "_get_conn", lambda: conn)

    from icdev.tools.ace.agent_tools import AgentToolRegistry

    class FakeSpec:
        coworker_id = "cw-test"
        trust_tier = "green"
        folder_access: list = []
        icdev_tools: list = []
        coordination_namespace = namespace

    return AgentToolRegistry(FakeSpec(), instance_id="inst-test"), conn


class TestPostResultTool:
    def test_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        assert "post_result" in _SCHEMAS

    def test_schema_not_read_only(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        s = _SCHEMAS["post_result"]
        assert not s.get("is_read_only")

    def test_post_result_handler_stores_value(self, monkeypatch):
        registry, conn = _make_registry(monkeypatch)
        _, handlers = registry.build(["post_result"])
        result = handlers["post_result"]({"key": "analysis", "value": {"score": 0.95}}, None)
        assert "analysis" in result
        row = conn.execute(
            "SELECT value_json FROM agent_coordination WHERE key='analysis'"
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == {"score": 0.95}

    def test_missing_key_returns_error(self, monkeypatch):
        registry, _ = _make_registry(monkeypatch)
        _, handlers = registry.build(["post_result"])
        result = handlers["post_result"]({"value": "x"}, None)
        assert "error" in result.lower()


class TestReadResultTool:
    def test_schema_registered(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        assert "read_result" in _SCHEMAS

    def test_schema_is_read_only(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        s = _SCHEMAS["read_result"]
        assert s.get("is_read_only") is True

    def test_read_result_returns_json(self, monkeypatch):
        registry, conn = _make_registry(monkeypatch)
        _, handlers = registry.build(["post_result", "read_result"])
        handlers["post_result"]({"key": "data", "value": [1, 2, 3]}, None)
        result = handlers["read_result"]({"key": "data"}, None)
        parsed = json.loads(result)
        assert parsed == [1, 2, 3]

    def test_missing_key_returns_not_found(self, monkeypatch):
        registry, _ = _make_registry(monkeypatch)
        _, handlers = registry.build(["read_result"])
        result = handlers["read_result"]({"key": "nonexistent"}, None)
        assert "not found" in result.lower()

    def test_missing_key_arg_returns_error(self, monkeypatch):
        registry, _ = _make_registry(monkeypatch)
        _, handlers = registry.build(["read_result"])
        result = handlers["read_result"]({}, None)
        assert "error" in result.lower()


# ---------------------------------------------------------------------------
# Sibling coordination end-to-end
# ---------------------------------------------------------------------------

class TestSiblingCoordination:
    def test_sibling_agents_share_namespace(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class SpecWriter:
            coworker_id = "cw-writer"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []
            coordination_namespace = "shared-job-42"

        class SpecReader:
            coworker_id = "cw-reader"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []
            coordination_namespace = "shared-job-42"

        writer = AgentToolRegistry(SpecWriter(), instance_id="inst-w")
        reader = AgentToolRegistry(SpecReader(), instance_id="inst-r")

        _, w_handlers = writer.build(["post_result"])
        _, r_handlers = reader.build(["read_result"])

        w_handlers["post_result"]({"key": "result", "value": {"answer": 42}}, None)
        out = r_handlers["read_result"]({"key": "result"}, None)
        assert json.loads(out) == {"answer": 42}

    def test_different_namespaces_isolated(self, monkeypatch):
        import icdev.tools.ace.agent_coordination as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class SpecA:
            coworker_id = "cw-a"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []
            coordination_namespace = "ns-alpha"

        class SpecB:
            coworker_id = "cw-b"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []
            coordination_namespace = "ns-beta"

        reg_a = AgentToolRegistry(SpecA(), instance_id="inst-a")
        reg_b = AgentToolRegistry(SpecB(), instance_id="inst-b")

        _, a_handlers = reg_a.build(["post_result"])
        _, b_handlers = reg_b.build(["read_result"])

        a_handlers["post_result"]({"key": "secret", "value": "alpha-data"}, None)
        out = b_handlers["read_result"]({"key": "secret"}, None)
        assert "not found" in out.lower()

    def test_default_namespace_is_instance_id(self):
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class SpecNoNs:
            coworker_id = "cw-x"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []

        reg = AgentToolRegistry(SpecNoNs(), instance_id="my-instance-id")
        assert reg._coordination_namespace == "my-instance-id"

    def test_read_result_in_read_only_set(self):
        from icdev.tools.llm.agent_loop import _build_read_only_set
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class FakeSpec:
            coworker_id = "cw"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []
            coordination_namespace = "ns"

        registry = AgentToolRegistry(FakeSpec(), instance_id="inst")
        tools, _ = registry.build(["read_result", "post_result", "write_file"])
        ro_set = _build_read_only_set(tools)
        assert "read_result" in ro_set
        assert "post_result" not in ro_set


# ---------------------------------------------------------------------------
# Namespace inheritance in spawn_agent
# ---------------------------------------------------------------------------


class TestNamespaceInheritanceInSpawnAgent:
    """spawn_agent inherits the parent namespace by default; explicit override wins."""

    def _make_reg(self, ns: str):
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class FakeSpec:
            coworker_id = "cw-ns"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []
            coordination_namespace = ns

        return AgentToolRegistry(FakeSpec(), instance_id="inst-ns")

    def test_registry_stores_spec_namespace(self):
        reg = self._make_reg("parent-ns")
        assert reg._coordination_namespace == "parent-ns"

    def test_registry_falls_back_to_instance_id_when_no_spec_ns(self):
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class SpecNoNs:
            coworker_id = "cw-y"
            trust_tier = "green"
            folder_access: list = []
            icdev_tools: list = []

        reg = AgentToolRegistry(SpecNoNs(), instance_id="fallback-inst")
        assert reg._coordination_namespace == "fallback-inst"

    def test_spawn_agent_schema_has_coordination_namespace_param(self):
        from icdev.tools.ace.agent_tools import _SCHEMAS
        props = _SCHEMAS["spawn_agent"]["function"]["parameters"]["properties"]
        assert "coordination_namespace" in props
