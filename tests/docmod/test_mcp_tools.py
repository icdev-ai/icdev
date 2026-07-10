# CUI // SP-CTI
"""docmod-ops-02: MCP tool registration + handler dispatch."""
from __future__ import annotations

import pytest

_DOCMOD_DDL_KEYS = ("docmod_findings", "docmod_scan_runs", "docmod_doc_scan_state")


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DOCMOD_DDL_KEYS) and "CREATE TABLE" in stmt:
            conn.execute(stmt)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dic_documents (doc_id TEXT PRIMARY KEY, "
        "collection_id TEXT, title TEXT, tenant_id TEXT, classification TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dic_versions (version_id TEXT PRIMARY KEY, "
        "doc_id TEXT, version_no INTEGER, origin TEXT, status TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()
    yield


def test_registry_declares_three_docmod_tools():
    from tools.mcp.tool_registry import TOOL_REGISTRY

    for name in ("docmod_scan", "docmod_findings", "docmod_redline"):
        entry = TOOL_REGISTRY.get(name)
        assert entry, f"{name} missing from TOOL_REGISTRY"
        assert entry["category"] == "docmod"
        assert entry["module"] == "tools.mcp.gap_handlers"
        assert entry["handler"].startswith("handle_docmod_")


def test_registry_handlers_resolve():
    """Every declared docmod handler exists — no dangling stubs
    (the exact defect kg_stale_entities had)."""
    import importlib

    from tools.mcp.tool_registry import TOOL_REGISTRY

    for name in ("docmod_scan", "docmod_findings", "docmod_redline"):
        entry = TOOL_REGISTRY[name]
        module = importlib.import_module(entry["module"])
        assert callable(getattr(module, entry["handler"], None)), (
            f"{entry['handler']} missing from {entry['module']}"
        )


def test_docmod_findings_handler_dispatch(db):
    from tools.mcp.gap_handlers import handle_docmod_findings

    result = handle_docmod_findings({"state": "open"})
    assert "error" not in result
    assert result["count"] == len(result["findings"])


def test_docmod_scan_handler_dispatch(db):
    from tools.mcp.gap_handlers import handle_docmod_scan

    result = handle_docmod_scan({})
    # Whole-corpus sweep over the (empty) test corpus
    assert "error" not in result, result
    assert result.get("docs_total") == 0 or "docs_scanned" in result


def test_docmod_redline_handler_validates_input():
    from tools.mcp.gap_handlers import handle_docmod_redline

    assert handle_docmod_redline({})["error"] == "finding_id required"
    result = handle_docmod_redline({"finding_id": "fnd-nope"})
    # graceful: unknown finding => error status from the drafter, not a crash
    assert result.get("status") == "error" or "error" in result
