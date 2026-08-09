# CUI // SP-CTI
"""The pending-approval store (agov-inbox-01).

Four things have to be true, and each has a test class here:

  1. An item transitions ``pending`` -> ``resolved``, exactly once.
  2. Resolving writes the corresponding ``agent_approval_log`` row — the
     permanent decision record, not a second copy of it.
  3. No raw argument VALUE from the originating tool call reaches EITHER table.
     ``approval_items`` rows get mirrored into Slack, so this is the strictest
     place the convention has to hold.
  4. ``approval_items`` is NOT in ``APPEND_ONLY_TABLES``, on purpose.

Both tables are built from their own migrations' DDL rather than a hand-written
schema, so a column added to one and not the other fails here instead of at
runtime inside a swallowed exception (CLAUDE.md: "every column in an INSERT must
exist in the LIVE schema").
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime.approval_gate import Classification, classify
from tools.agent_runtime.approval_inbox import (
    COLUMNS,
    ORIGIN_ACE,
    ORIGIN_SAG,
    RESOLUTION_APPROVED,
    RESOLUTION_DENIED,
    STATE_CANCELLED,
    STATE_EXPIRED,
    STATE_PENDING,
    STATE_RESOLVED,
    TABLE,
    ApprovalInboxUnavailable,
    cancel,
    enqueue,
    expire_due,
    get,
    list_items,
    list_pending,
    render_summary,
    resolve,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A value that could only have come from the raw tool arguments. If this string
# turns up anywhere in either table, an argument value leaked.
SECRET = "s3cr3t-token-DO-NOT-PERSIST"
TOOL_INPUT = {
    "command": f"curl -H 'Authorization: Bearer {SECRET}' https://example.invalid",
    "cwd": "/opt/icdev",
}


# ---------------------------------------------------------------------------
# Schema — from the migrations themselves
# ---------------------------------------------------------------------------
def _approval_items_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260809203855_agov_approval_items" / "up.sql"
    )
    return path.read_text(encoding="utf-8")


def _approval_log_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _storage_module():
    """The module ``approval_inbox`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    instead — two different objects. Both the store and ``record_decision``
    import the shim from inside their functions, so patching the canonical
    module (which is what monkeypatch's string form resolves to) would silently
    patch nothing and every test below would assert its own no-op.
    """
    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    ``unclosable``: the store closes its connection in a ``finally`` block, and
    the fixture's connection has to outlive that so the assertions can still
    read the rows.

    A named factory rather than an inline ``translating(raw, ...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS — a name bound directly from the imported
    ``_sql_compat`` helper is not propagated, so the correctly-wrapped fixture
    reads to that gate as a raw sqlite3 handle.
    """
    return translating(raw, unclosable=True)


@pytest.fixture
def inbox_db(monkeypatch, tmp_path):
    """Both real tables, in one DB, behind the production %s translation."""
    raw = sqlite3.connect(str(tmp_path / "inbox.db"))
    raw.executescript(_approval_items_ddl())
    raw.executescript(_approval_log_ddl())
    conn = _translating_conn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    yield raw
    raw.close()


def _rows(raw: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _queue(**overrides) -> Any:
    """Enqueue one irreversible ask, rendered the sanctioned way."""
    cls = classify("run_command", TOOL_INPUT)
    title, body = render_summary(cls, TOOL_INPUT, actor="agent")
    kwargs = dict(
        tool_name=cls.tool_name,
        tier=cls.tier,
        title=title,
        body=body,
        origin=ORIGIN_SAG,
        session_id="sess-1",
        inbox="ops",
        tool_input=TOOL_INPUT,
        rule=cls.rule,
    )
    kwargs.update(overrides)
    return enqueue(**kwargs)


class TestSchema:
    def test_columns_match_the_migration(self, inbox_db):
        live = [r[1] for r in inbox_db.execute(f"PRAGMA table_info({TABLE})").fetchall()]
        assert list(COLUMNS) == live

    def test_insert_matches_the_live_schema(self, inbox_db):
        item = _queue()
        rows = _rows(inbox_db, TABLE)
        assert len(rows) == 1
        row = rows[0]
        # Every column the store names is a column the table has, and every
        # value round-trips — an INSERT naming a phantom column would have
        # raised out of enqueue rather than reaching here.
        assert set(row) == set(COLUMNS)
        assert row["item_id"] == item.item_id
        assert row["state"] == STATE_PENDING
        assert row["origin"] == ORIGIN_SAG
        assert row["inbox"] == "ops"
        assert row["classification"] == "CUI"
        assert row["created_at"] and row["updated_at"]

    def test_unknown_origin_is_refused_before_the_insert(self, inbox_db):
        with pytest.raises(ValueError, match="unknown origin"):
            _queue(origin="carrier-pigeon")
        assert _rows(inbox_db, TABLE) == []

    def test_a_missing_table_raises_rather_than_dropping_the_ask(self, monkeypatch, inbox_db):
        monkeypatch.setattr(_storage_module(), "table_exists", lambda c, t: False)
        with pytest.raises(ApprovalInboxUnavailable):
            _queue()


# ---------------------------------------------------------------------------
# 1. pending -> resolved
# ---------------------------------------------------------------------------
class TestLifecycle:
    def test_pending_to_resolved(self, inbox_db):
        item = _queue()
        assert item.state == STATE_PENDING
        assert item.is_pending is True
        assert get(item.item_id).state == STATE_PENDING

        settled = resolve(item.item_id, approved=True, resolved_by="alice", reason="ok")
        assert settled is not None
        assert settled.state == STATE_RESOLVED
        assert settled.resolution == RESOLUTION_APPROVED
        assert settled.resolved_by == "alice"
        assert settled.resolved_at
        assert settled.is_approved is True

        # Persisted, not just returned.
        reread = get(item.item_id)
        assert reread.state == STATE_RESOLVED
        assert reread.resolution == RESOLUTION_APPROVED
        assert _rows(inbox_db, TABLE)[0]["state"] == STATE_RESOLVED

    def test_pending_is_listable_then_is_not(self, inbox_db):
        item = _queue()
        assert [x.item_id for x in list_pending(inbox="ops")] == [item.item_id]
        resolve(item.item_id, approved=False, resolved_by="alice")
        assert list_pending(inbox="ops") == []
        assert [x.item_id for x in list_items(state=STATE_RESOLVED)] == [item.item_id]

    def test_a_second_resolution_is_an_idempotent_no_op(self, inbox_db):
        item = _queue()
        assert resolve(item.item_id, approved=True, resolved_by="alice") is not None
        # The Slack reply arrives twice, or the sweep races the reply.
        assert resolve(item.item_id, approved=False, resolved_by="mallory") is None
        assert get(item.item_id).resolution == RESOLUTION_APPROVED
        # And it did not produce a second, contradictory decision record.
        assert len(_rows(inbox_db, "agent_approval_log")) == 1

    def test_resolving_an_unknown_item_is_a_no_op(self, inbox_db):
        assert resolve("ai-nope", approved=True, resolved_by="alice") is None
        assert _rows(inbox_db, "agent_approval_log") == []

    def test_expiry_denies_and_never_approves(self, inbox_db):
        item = _queue(expires_in_seconds=-1)
        swept = expire_due()
        assert [x.item_id for x in swept] == [item.item_id]
        expired = get(item.item_id)
        assert expired.state == STATE_EXPIRED
        assert expired.resolution == RESOLUTION_DENIED
        # A timeout that reads as approval is how this feature would silently
        # become an auto-approver.
        assert expired.is_approved is False

    def test_an_item_with_no_expiry_is_never_swept(self, inbox_db):
        item = _queue()
        assert expire_due() == []
        assert get(item.item_id).state == STATE_PENDING

    def test_cancel_denies(self, inbox_db):
        item = _queue()
        settled = cancel(item.item_id, resolved_by="alice")
        assert settled.state == STATE_CANCELLED
        assert settled.resolution == RESOLUTION_DENIED
        assert settled.is_approved is False


# ---------------------------------------------------------------------------
# 2. Resolving writes the corresponding agent_approval_log row
# ---------------------------------------------------------------------------
class TestDecisionIsRecorded:
    def test_resolving_writes_an_agent_approval_log_row(self, inbox_db):
        item = _queue()
        assert _rows(inbox_db, "agent_approval_log") == [], "queuing is not a decision"

        resolve(item.item_id, approved=True, resolved_by="alice", reason="hotfix ok")

        logged = _rows(inbox_db, "agent_approval_log")
        assert len(logged) == 1
        row = logged[0]
        assert row["decision"] == "approved"
        assert row["actor"] == "alice"
        assert row["reason"] == "hotfix ok"
        assert row["tool_name"] == item.tool_name
        assert row["tier"] == item.tier
        assert row["session_id"] == "sess-1"
        assert row["decided_at"]
        # The correlator: the digest the item carried since enqueue, not one
        # recomputed from arguments the resolver never saw.
        assert row["input_sha256"] == item.input_sha256
        assert row["arg_keys"] == item.arg_keys

    def test_a_denial_is_recorded_too(self, inbox_db):
        item = _queue()
        resolve(item.item_id, approved=False, resolved_by="bob", reason="no")
        row = _rows(inbox_db, "agent_approval_log")[0]
        assert row["decision"] == "denied"
        assert row["actor"] == "bob"

    def test_expiry_is_recorded_as_a_denial(self, inbox_db):
        item = _queue(expires_in_seconds=-1)
        expire_due()
        row = _rows(inbox_db, "agent_approval_log")[0]
        assert row["decision"] == "denied"
        assert "expired" in row["reason"]
        assert row["input_sha256"] == item.input_sha256


# ---------------------------------------------------------------------------
# 3. No raw argument VALUE in EITHER table
# ---------------------------------------------------------------------------
class TestNoArgumentValuesArePersisted:
    def test_neither_table_contains_a_raw_argument_value(self, inbox_db):
        item = _queue()
        resolve(item.item_id, approved=True, resolved_by="alice", reason="ok")

        for table in (TABLE, "agent_approval_log"):
            for row in _rows(inbox_db, table):
                blob = " ".join("" if v is None else str(v) for v in row.values())
                assert SECRET not in blob, f"argument value leaked into {table}"
                assert "example.invalid" not in blob, f"argument value leaked into {table}"
                assert "/opt/icdev" not in blob, f"argument value leaked into {table}"

    def test_the_key_names_and_the_digest_are_kept(self, inbox_db):
        """Dropping the values must not mean dropping the ability to correlate."""
        item = _queue()
        assert item.arg_keys == "command,cwd"
        assert len(item.input_sha256) == 64
        row = _rows(inbox_db, TABLE)[0]
        assert row["arg_keys"] == "command,cwd"
        assert row["input_sha256"] == item.input_sha256

    def test_render_summary_carries_no_values(self):
        cls = classify("run_command", TOOL_INPUT)
        title, body = render_summary(cls, TOOL_INPUT, actor="agent")
        assert SECRET not in title and SECRET not in body
        assert "example.invalid" not in body
        assert "command,cwd" in body      # keys, yes
        assert cls.tier in body

    def test_approval_request_summary_is_the_trap_this_avoids(self):
        """Why render_summary exists rather than reusing the gate's own summary.

        ``ApprovalRequest.summary()`` previews the ``command`` VALUE. It is fine
        on a local console and unacceptable in a Slack message, which is the
        whole difference between the gate and the inbox. If this ever stops
        being true the guard above is redundant and can go — but silently
        reusing summary() for a delivered body would leak.
        """
        from tools.agent_runtime.approval_gate import ApprovalRequest

        request = ApprovalRequest(
            tool_name="run_command",
            tool_input=TOOL_INPUT,
            classification=classify("run_command", TOOL_INPUT),
            actor="agent",
        )
        assert SECRET in request.summary()

    def test_a_caller_supplied_body_is_stored_verbatim(self, inbox_db):
        """The module never derives a value; it cannot police what it is handed.

        Documented so the guarantee is not read as wider than it is: everything
        this module renders is value-free, and agov-inbox-03 must render through
        render_summary rather than hand-rolling a body.
        """
        item = _queue(body="operator wrote this")
        assert _rows(inbox_db, TABLE)[0]["body"] == "operator wrote this"
        assert item.arg_keys == "command,cwd"


# ---------------------------------------------------------------------------
# 4. approval_items is NOT append-only
# ---------------------------------------------------------------------------
def _append_only_tables() -> set[str]:
    """Parse ``APPEND_ONLY_TABLES`` out of the pre_tool_use hook.

    Read from source rather than imported: the hook is a Claude Code entry
    point, not an importable module, and it is the file CLAUDE.md names as the
    canonical list.
    """
    src = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "APPEND_ONLY_TABLES"
            for t in node.targets
        ):
            return {
                e.value
                for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    raise AssertionError("APPEND_ONLY_TABLES not found in pre_tool_use.py")


class TestAppendOnlyRegistration:
    def test_approval_items_is_not_append_only(self):
        """Deliberate. It is mutable state; the decision record is elsewhere.

        Adding it would make the ``pending`` -> ``resolved`` UPDATE — the one
        transition this table exists for — a hook violation.
        """
        assert TABLE not in _append_only_tables()

    def test_the_decision_log_still_is(self):
        """Guards the test above against being vacuously true."""
        assert "agent_approval_log" in _append_only_tables()


class TestReadsDegradeRatherThanRaise:
    def test_reads_return_empty_without_a_table(self, monkeypatch, inbox_db):
        monkeypatch.setattr(_storage_module(), "table_exists", lambda c, t: False)
        assert get("ai-anything") is None
        assert list_items() == []
        assert list_pending() == []

    def test_origin_filter(self, inbox_db):
        sag = _queue()
        ace = _queue(origin=ORIGIN_ACE)
        assert {x.item_id for x in list_items(origin=ORIGIN_ACE)} == {ace.item_id}
        assert {x.item_id for x in list_items(origin=ORIGIN_SAG)} == {sag.item_id}


class TestRenderSummaryShape:
    def test_title_names_the_tier_and_tool(self):
        cls = Classification(
            tool_name="git_push",
            tier="irreversible",
            rule="tool_list",
            detail="publishes to a remote",
            requires_approval=True,
        )
        title, body = render_summary(cls, {"remote": "origin"})
        assert title == "[IRREVERSIBLE] git_push"
        assert "publishes to a remote" in body
        assert "Argument keys: remote" in body

    def test_no_arguments_is_stated_not_blank(self):
        cls = classify("git_push", {})
        _, body = render_summary(cls, {})
        assert "Argument keys: (none)" in body
