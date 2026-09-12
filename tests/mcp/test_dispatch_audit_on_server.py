# CUI // SP-CTI
"""xrv-cost-05 — every ``tools/call`` through an MCP server leaves ONE row.

The defect these pin: ``studio_mcp_dispatch_audit`` had exactly two writers,
both Studio-internal, so ``capability_consumption --class mcp_dispatch_tool``
read 468 of 472 tools ``inert`` while a Claude Code session used a dozen of
them the same afternoon — a measurement of ONE caller wearing the name of all
callers.

Every test here drives the REAL ``MCPServer._handle_tools_call`` against a
``tmp_path`` database, never the ambient one, and reads the row back out of
the table rather than trusting the writer's return value.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from tools.db.storage import get_connection
from tools.mcp import dispatch_audit
from tools.mcp.base_server import MCPServer
from tools.studio.executors import mcp_executor

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MCP_DIR = REPO_ROOT / "tools" / "mcp"
AUDIT_DDL = REPO_ROOT / "tools" / "db" / "migrations" / "307_studio_mcp_dispatch_audit.sql"

SECRET_ARG = "s3cret-value-that-must-never-be-stored"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    """A throwaway database carrying the REAL migration-307 DDL.

    The DDL is read from the migration rather than retyped here: the
    ``decision`` CHECK is derived from ``mcp_executor.DECISIONS`` and a copy in
    a test fixture is exactly how the two come to disagree.
    """
    db = tmp_path / "dispatch_audit.db"
    ddl = "\n".join(
        line
        for line in AUDIT_DDL.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    conn = get_connection(db_path=str(db))
    try:
        for statement in ddl.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        mcp_executor, "_gate_connection", lambda: get_connection(db_path=str(db))
    )
    monkeypatch.setenv("ICDEV_MCP_DISPATCH_AUDIT", "1")
    monkeypatch.setenv("ICDEV_SESSION_ID", "sess-xrv-cost-05")
    monkeypatch.setenv("ICDEV_AGENT", "kanban")
    monkeypatch.delenv("ICDEV_TENANT_ID", raising=False)
    monkeypatch.delenv("ICDEV_CALLER_IL", raising=False)
    dispatch_audit.reset_stats()
    yield db
    dispatch_audit.flush(10.0)
    dispatch_audit.reset_stats()


def _rows(db) -> list[dict]:
    conn = get_connection(db_path=str(db))
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM studio_mcp_dispatch_audit ORDER BY recorded_at"
            ).fetchall()
        ]
    finally:
        conn.close()


def _server(name: str = "icdev-unified") -> MCPServer:
    server = MCPServer(name=name, version="1.0.0")
    server.register_tool(
        "echo", "Echo the args", {"type": "object"}, lambda args: {"echo": args}
    )

    def _boom(_args):
        raise RuntimeError("handler exploded")

    server.register_tool("boom", "Always raises", {"type": "object"}, _boom)
    return server


# ---------------------------------------------------------------------------
# The happy path: one row, the right caller, and NO argument values
# ---------------------------------------------------------------------------


def test_dispatched_call_writes_exactly_one_allowed_row(audit_db):
    server = _server()
    args = {"msg": "hello", "token": SECRET_ARG}

    result = server._handle_tools_call({"name": "echo", "arguments": args})

    assert result["isError"] is False
    assert dispatch_audit.flush(10.0), "audit queue did not drain"

    rows = _rows(audit_db)
    assert len(rows) == 1, f"expected exactly one audit row, got {len(rows)}"
    row = rows[0]
    assert row["tool"] == "echo"
    assert row["decision"] == mcp_executor.DECISION_ALLOWED
    assert row["reason"] == mcp_executor.REASON_DISPATCHED
    assert row["caller_source"] == "mcp_server:icdev-unified"
    assert row["principal_id"] == "sess-xrv-cost-05"
    assert row["caller_roles"] == "kanban"
    # classification is NOT NULL and is derived from the caller IL, never a
    # hardcoded banner — an unset IL still yields the platform baseline.
    assert row["classification"]


def test_params_are_digested_and_no_argument_value_is_stored(audit_db):
    server = _server()
    args = {"msg": "hello", "token": SECRET_ARG}

    server._handle_tools_call({"name": "echo", "arguments": args})
    assert dispatch_audit.flush(10.0)

    row = _rows(audit_db)[0]
    assert row["params_sha256"] == mcp_executor.params_digest(args)
    # The whole row, every column, must not carry the argument VALUE anywhere.
    blob = json.dumps(row, default=str)
    assert SECRET_ARG not in blob
    assert "hello" not in blob


def test_caller_source_names_the_server_that_served_it(audit_db):
    _server(name="icdev-compliance")._handle_tools_call(
        {"name": "echo", "arguments": {}}
    )
    assert dispatch_audit.flush(10.0)
    assert _rows(audit_db)[0]["caller_source"] == "mcp_server:icdev-compliance"


# ---------------------------------------------------------------------------
# Refusals: the row is written and the client's answer is UNCHANGED
# ---------------------------------------------------------------------------


def test_raising_handler_is_refused_and_the_error_result_is_unchanged(audit_db):
    server = _server()

    result = server._handle_tools_call({"name": "boom", "arguments": {"a": 1}})

    # The JSON-RPC-level answer is exactly what it was before this card: the
    # audit is an observer, never a gate.
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"] == "handler exploded"
    assert payload["tool"] == "boom"

    assert dispatch_audit.flush(10.0)
    row = _rows(audit_db)[0]
    assert row["decision"] == mcp_executor.DECISION_REFUSED
    assert row["reason"] == "RuntimeError"
    assert row["detail"] == "handler exploded"


def test_unknown_tool_is_refused_and_still_raises_method_not_found(audit_db):
    server = _server()

    with pytest.raises(Exception) as exc_info:
        server._handle_tools_call({"name": "no_such_tool", "arguments": {}})
    assert "no_such_tool" in str(exc_info.value)

    assert dispatch_audit.flush(10.0)
    row = _rows(audit_db)[0]
    assert row["tool"] == "no_such_tool"
    assert row["decision"] == mcp_executor.DECISION_REFUSED
    assert row["reason"] == dispatch_audit.REASON_UNKNOWN_TOOL


# ---------------------------------------------------------------------------
# The switch, and it SAYS it is off
# ---------------------------------------------------------------------------


def test_toggle_off_writes_no_row_and_initialize_says_so(audit_db, monkeypatch):
    monkeypatch.setenv("ICDEV_MCP_DISPATCH_AUDIT", "0")
    server = _server()

    result = server._handle_tools_call({"name": "echo", "arguments": {"a": 1}})
    assert result["isError"] is False
    assert dispatch_audit.flush(10.0)
    assert _rows(audit_db) == []

    handshake = server._handle_initialize({})
    audit = handshake["icdev"]["dispatchAudit"]
    assert audit["enabled"] is False
    # Never a silent no-op: the reason names the switch that closed it.
    assert dispatch_audit.ENV_TOGGLE in audit["reason"]


def test_toggle_on_is_reported_on_the_handshake(audit_db):
    audit = _server()._handle_initialize({})["icdev"]["dispatchAudit"]
    assert audit["enabled"] is True
    assert audit["table"] == "studio_mcp_dispatch_audit"


# ---------------------------------------------------------------------------
# An unreachable database costs the caller NOTHING
# ---------------------------------------------------------------------------


def test_unreachable_db_leaves_the_tool_result_unchanged(audit_db, monkeypatch):
    def _refuse():
        raise OSError("database is gone")

    monkeypatch.setattr(mcp_executor, "_gate_connection", _refuse)
    server = _server()

    result = server._handle_tools_call({"name": "echo", "arguments": {"a": 1}})

    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"echo": {"a": 1}}
    assert dispatch_audit.flush(10.0)
    # The failure is COUNTED, never swallowed into silence — a warning log
    # alone cannot be asserted here (get_logger output does not reach caplog),
    # and a counter is the stronger claim anyway.
    stats = dispatch_audit.stats()
    assert stats["failed"] == 1
    assert stats["written"] == 0
    assert "database is gone" in stats["last_error"]
    assert _rows(audit_db) == []


# ---------------------------------------------------------------------------
# Structural: ONE writer, ONE call site, NO second INSERT
# ---------------------------------------------------------------------------


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _call_sites(tree: ast.AST, func_name: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == func_name:
            out.append(node)
    return out


def test_exactly_one_module_under_tools_mcp_reaches_the_studio_writer():
    """``record_dispatch_audit`` is reached from ONE place under tools/mcp/.

    An AST test rather than a behavioural one because the failure mode is a
    FUTURE edit adding a second writer — every behavioural test over today's
    call graph would still pass the day it happens.
    """
    hits = {
        path.relative_to(REPO_ROOT).as_posix(): len(
            _call_sites(ast.parse(path.read_text(encoding="utf-8")), "record_dispatch_audit")
        )
        for path in _py_files(MCP_DIR)
    }
    callers = {path: n for path, n in hits.items() if n}
    assert callers == {"tools/mcp/dispatch_audit.py": 1}, (
        "the Studio audit writer must be reached from exactly one place under "
        f"tools/mcp/ (dispatch_audit._write); found {callers}"
    )


def test_the_wrapper_is_called_only_from_base_server_handle_tools_call():
    """One choke point: ``_audit_dispatch`` lives in and is called from ONE method."""
    for path in _py_files(MCP_DIR):
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        wrapper_calls = _call_sites(tree, "record_tool_call")
        if rel == "tools/mcp/base_server.py":
            assert len(wrapper_calls) == 1, (
                "base_server must reach dispatch_audit.record_tool_call exactly "
                f"once (via _audit_dispatch); found {len(wrapper_calls)}"
            )
        else:
            assert not wrapper_calls, f"{rel} must not call record_tool_call"

    source = (MCP_DIR / "base_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    enclosing = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in _call_sites(node, "_audit_dispatch"):
                enclosing.setdefault(node.name, []).append(call)
    # The helper is invoked only from the dispatch path, and on all three of
    # its exits (unknown tool / raising handler / dispatched).
    assert set(enclosing) == {"_handle_tools_call"}, (
        f"_audit_dispatch may only be called from _handle_tools_call; found {set(enclosing)}"
    )
    assert len(enclosing["_handle_tools_call"]) == 3


def _docstring_nodes(tree: ast.AST) -> set:
    """Every node that IS a docstring, by identity.

    PROSE IS NOT A LITERAL (the model_id_gate precedent): dispatch_audit.py's
    own module docstring NAMES the forbidden statement in order to say it does
    not use one, and a naive text scan flags the explanation of the rule as a
    breach of it.
    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def test_no_second_insert_into_the_append_only_table_under_tools_mcp():
    needle = "insert into studio_mcp_dispatch_audit"
    for path in _py_files(MCP_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                assert needle not in node.value.lower(), (
                    f"{path.relative_to(REPO_ROOT).as_posix()} writes the "
                    "append-only audit table directly; go through "
                    "mcp_executor.record_dispatch_audit"
                )
    # Positive control: the scanner CAN see the literal when it is real code.
    planted = ast.parse('SQL = "INSERT INTO studio_mcp_dispatch_audit (tool) VALUES (1)"')
    found = [
        n
        for n in ast.walk(planted)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and needle in n.value.lower()
        and id(n) not in _docstring_nodes(planted)
    ]
    assert found, "the scanner would not detect a real INSERT literal"


def test_decision_vocabulary_matches_the_studio_constants():
    """The spellings base_server uses must be the ones the CHECK admits."""
    assert dispatch_audit.DECISION_ALLOWED == mcp_executor.DECISION_ALLOWED
    assert dispatch_audit.DECISION_REFUSED == mcp_executor.DECISION_REFUSED
    assert dispatch_audit.REASON_DISPATCHED == mcp_executor.REASON_DISPATCHED
    for value in (dispatch_audit.DECISION_ALLOWED, dispatch_audit.DECISION_REFUSED):
        assert value in mcp_executor.DECISIONS


# ---------------------------------------------------------------------------
# The reader: by_caller_source keeps the two callers apart
# ---------------------------------------------------------------------------


def test_by_caller_source_never_merges_studio_and_server_counts(audit_db):
    from tools.awareness.capability_consumption import _dispatch_by_caller_source

    server = _server()
    server._handle_tools_call({"name": "echo", "arguments": {"a": 1}})
    assert dispatch_audit.flush(10.0)
    # A Studio-side row for the same tool, written through the same one writer.
    conn = get_connection(db_path=str(audit_db))
    try:
        assert mcp_executor.record_dispatch_audit(
            "echo", {"a": 1}, mcp_executor.DECISION_ALLOWED,
            mcp_executor.REASON_DISPATCHED,
            caller={"source": "workflow", "impact_level": "IL4"},
        )[0]
    finally:
        conn.close()

    conn = get_connection(db_path=str(audit_db))
    try:
        from datetime import datetime, timedelta, timezone

        since = datetime.now(timezone.utc) - timedelta(days=1)
        split = _dispatch_by_caller_source(conn, since, {"echo"}, 20)
    finally:
        conn.close()

    assert split["state"] == "measured"
    sources = split["sources"]
    assert sources["mcp_server:icdev-unified"]["kind"] == "mcp_server"
    assert sources["mcp_server:icdev-unified"]["events"] == 1
    assert sources["workflow"]["kind"] == "studio_gate"
    assert sources["workflow"]["events"] == 1
    # Two callers, two entries: there is no field anywhere carrying their sum.
    assert 2 not in [
        v for entry in sources.values() for v in entry.values() if isinstance(v, int)
    ]
