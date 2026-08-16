# CUI // SP-CTI
"""The append-only agent event log (hcx-evt-01).

Six things have to be true, and each has a test class here:

  1. ``payload_hash`` is written for EVERY event, including one whose payload the
     classification policy withheld. A hash-only row is still evidence; a row
     with neither is not.
  2. ``payload_json`` is written only when the policy allows, the policy fails
     CLOSED on a config it cannot parse, and a NULL payload reports as WITHHELD
     rather than as an empty one.
  3. ``seq`` is monotonic per session, UNIQUE over (session_id, seq), and a
     writer that loses the allocation race RETRIES rather than writing a
     duplicate position.
  4. The module is append-only in the only sense that matters: it emits no
     UPDATE and no DELETE, and it exposes no verb that could.
  5. A failed INSERT RAISES. The gap this whole card exists to close was a write
     path that reported success while persisting nothing.
  6. ``agent_session_events`` is registered in ``APPEND_ONLY_TABLES``, so the
     PreToolUse hook refuses a mutation against it.

The schema comes from the migration's own up.sql, not from a hand-written copy —
a column added to one and not the other has to fail here rather than at runtime
inside somebody's ``except`` (CLAUDE.md: "every column in an INSERT must exist in
the LIVE schema").
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime.event_log import (
    COLUMNS,
    EVENT_TYPES,
    MAX_SEQ_ATTEMPTS,
    MIGRATION,
    TABLE,
    Event,
    EventLogUnavailable,
    RetentionPolicy,
    append,
    load_policy,
    next_seq,
    read_session,
)
from tools.audit.row_hash import (
    AUDIT_HASH_FIELDS,
    canonical_payload,
    compute_payload_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SESSION = "sess-evt-1"


# ---------------------------------------------------------------------------
# Schema — from the migration itself
# ---------------------------------------------------------------------------
def _events_ddl() -> str:
    path = REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    return path.read_text(encoding="utf-8")


def _storage_module():
    """The module ``event_log`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` and ``icdev.tools.db.storage`` are
    two distinct module objects, and patching the wrong one installs a fake
    nothing ever calls — the test then asserts against its own no-op while the
    code under test writes to the LIVE board. ``event_log._connect`` imports
    ``from tools.db.storage import ...`` inside the function, so that is the
    binding to replace.
    """
    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    ``unclosable``: ``event_log`` closes its connection in a ``finally``, and the
    fixture's has to outlive that so the assertions can still read the rows.

    A named factory rather than an inline ``translating(...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS.
    """
    return translating(raw, unclosable=True)


def _sabotage_inserts(conn):
    """A translating connection whose INSERT raises. Everything else still works.

    Stands in for a disk-full / constraint / permissions failure at exactly the
    statement this module must not swallow.
    """
    real = conn.execute

    def execute(sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            raise RuntimeError("disk is full")
        return real(sql, params)

    conn.execute = execute
    return conn


@pytest.fixture
def event_db(monkeypatch, tmp_path):
    """The real table, behind the production ``%s`` → ``?`` translation."""
    raw = sqlite3.connect(str(tmp_path / "events.db"))
    raw.executescript(_events_ddl())
    conn = _translating_conn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.delenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", raising=False)
    monkeypatch.setenv("ICDEV_TENANT_ID", "acme")
    yield raw
    raw.close()


def _rows(raw: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {TABLE} ORDER BY seq")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


RETAIN = RetentionPolicy(enabled=True, max_classification="CUI")
HASH_ONLY = RetentionPolicy(enabled=False)


# ---------------------------------------------------------------------------
# 1. payload_hash is always written
# ---------------------------------------------------------------------------
class TestPayloadHashAlwaysWritten:
    def test_every_event_type_gets_a_hash(self, event_db):
        for etype in EVENT_TYPES:
            append(SESSION, etype, {"t": etype}, policy=RETAIN)
        rows = _rows(event_db)
        assert len(rows) == len(EVENT_TYPES)
        assert all(len(r["payload_hash"]) == 64 for r in rows)
        assert [r["event_type"] for r in rows] == list(EVENT_TYPES)

    def test_a_withheld_payload_still_has_its_hash(self, event_db):
        event = append(SESSION, "tool_call", {"cmd": "rm -rf /"}, policy=HASH_ONLY)
        row = _rows(event_db)[0]
        assert row["payload_json"] is None
        assert row["payload_hash"] == compute_payload_hash({"cmd": "rm -rf /"})
        assert event.payload_withheld is True

    def test_a_none_payload_still_has_its_hash(self, event_db):
        append(SESSION, "turn_start", None, policy=RETAIN)
        row = _rows(event_db)[0]
        assert row["payload_hash"] == compute_payload_hash(None)
        assert len(row["payload_hash"]) == 64

    def test_hash_is_key_order_independent(self):
        # Dict order is a property of how the payload was BUILT, never of what it
        # means. Without sort_keys the same tool call hashes two ways.
        assert compute_payload_hash({"a": 1, "b": 2}) == compute_payload_hash(
            {"b": 2, "a": 1}
        )

    def test_a_string_payload_hashes_as_itself_not_as_json(self):
        # assistant_message carries raw text; "hello" and hello must not be two
        # different events.
        assert canonical_payload("hello") == "hello"
        assert compute_payload_hash("hello") != compute_payload_hash(json.dumps("hello"))

    def test_stored_payload_rehashes_to_the_recorded_hash(self, event_db):
        payload = {"tool": "Bash", "args": {"command": "ls", "timeout": 30}}
        append(SESSION, "tool_call", payload, policy=RETAIN)
        row = _rows(event_db)[0]
        assert compute_payload_hash(json.loads(row["payload_json"])) == row["payload_hash"]

    def test_the_audit_row_recipe_is_untouched(self):
        # compute_payload_hash lives beside compute_audit_row_hash and must not
        # have perturbed it — every hash already in audit_trail depends on this
        # tuple.
        assert AUDIT_HASH_FIELDS == (
            "id", "project_id", "event_type", "actor", "action", "details",
            "classification", "ip_address", "session_id",
        )


# ---------------------------------------------------------------------------
# 2. payload_json obeys the classification policy
# ---------------------------------------------------------------------------
class TestRetentionPolicy:
    def test_payload_is_stored_at_or_below_the_ceiling(self, event_db):
        append(SESSION, "assistant_message", "ordinary text",
               classification="CUI", policy=RETAIN)
        assert _rows(event_db)[0]["payload_json"] is not None
        assert read_session(SESSION)[0].payload == "ordinary text"

    def test_a_string_payload_round_trips_as_a_string(self, event_db):
        # Stored as a JSON document, not as raw text: a bare 123 would read back
        # as an integer.
        append(SESSION, "assistant_message", "123", policy=RETAIN)
        assert read_session(SESSION)[0].payload == "123"

    def test_a_retained_none_payload_is_stored_as_json_null_not_sql_null(
        self, event_db
    ):
        # This is what makes `payload_json IS NULL` mean WITHHELD and nothing
        # else. Collapsing the two would make a replay silently wrong.
        append(SESSION, "turn_start", None, policy=RETAIN)
        assert _rows(event_db)[0]["payload_json"] == "null"

    def test_payload_is_withheld_above_the_ceiling(self, event_db):
        append(SESSION, "assistant_message", "sensitive text",
               classification="SECRET", policy=RETAIN)
        row = _rows(event_db)[0]
        assert row["payload_json"] is None
        assert row["classification"] == "SECRET"
        assert row["payload_hash"] == compute_payload_hash("sensitive text")

    def test_the_ceiling_compares_by_ORDER_not_string_equality(self):
        policy = RetentionPolicy(enabled=True, max_classification="SECRET")
        assert policy.stores("tool_call", "CUI") is True          # below
        assert policy.stores("tool_call", "SECRET") is True       # at
        assert policy.stores("tool_call", "TOP SECRET") is False  # above

    def test_never_store_suppresses_one_type_only(self):
        policy = RetentionPolicy(enabled=True, never_store=("request_context",))
        assert policy.stores("request_context", "CUI") is False
        assert policy.stores("tool_call", "CUI") is True

    def test_an_absent_config_file_uses_the_documented_defaults(self, tmp_path):
        policy = load_policy(tmp_path / "nope.yaml")
        assert policy.enabled is True
        assert policy.max_classification == "CUI"
        assert policy.source == "default_no_config_file"

    def test_an_unparseable_config_file_fails_CLOSED(self, tmp_path):
        # Present-but-broken is NOT the same as absent. Guessing a retention rule
        # in the permissive direction is how transcripts end up somewhere they
        # are not allowed to be.
        bad = tmp_path / "agent_event_log.yaml"
        bad.write_text("payload_retention: [this is a list, not a mapping]\n",
                       encoding="utf-8")
        policy = load_policy(bad)
        assert policy.enabled is False
        assert policy.source.startswith("fail_closed")
        assert policy.stores("tool_call", "CUI") is False

    def test_the_env_override_beats_the_file(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_event_log.yaml"
        cfg.write_text(
            "payload_retention:\n  enabled: true\n  max_classification: CUI\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", "0")
        policy = load_policy(cfg)
        assert policy.enabled is False
        assert "env:" in policy.source

    def test_the_shipped_policy_file_parses(self):
        policy = load_policy(REPO_ROOT / "args" / "agent_event_log.yaml")
        assert not policy.source.startswith("fail_closed")
        assert policy.unknown_event_types == ()

    def test_a_withheld_payload_is_not_reported_as_an_empty_one(self, event_db):
        append(SESSION, "tool_result", {"stdout": "secret"}, policy=HASH_ONLY)
        append(SESSION, "turn_start", None, policy=RETAIN)
        withheld, genuinely_none = read_session(SESSION)
        assert withheld.payload_withheld is True
        assert genuinely_none.payload_withheld is False
        assert genuinely_none.payload is None


# ---------------------------------------------------------------------------
# 3. seq is the ordering, and it is enforced
# ---------------------------------------------------------------------------
class TestSequence:
    def test_seq_starts_at_one_and_is_monotonic_per_session(self, event_db):
        assert next_seq(SESSION) == 1
        for _ in range(3):
            append(SESSION, "turn_start", None, policy=RETAIN)
        append("sess-other", "turn_start", None, policy=RETAIN)
        assert [r["seq"] for r in _rows(event_db) if r["session_id"] == SESSION] == [1, 2, 3]
        assert next_seq(SESSION) == 4
        assert next_seq("sess-other") == 2

    def test_a_duplicate_position_is_refused_by_the_database(self, event_db):
        append(SESSION, "turn_start", None, policy=RETAIN)
        with pytest.raises(sqlite3.IntegrityError):
            event_db.execute(
                f"INSERT INTO {TABLE} (event_id, session_id, seq, event_type, "
                "occurred_at, payload_hash) VALUES (?, ?, ?, ?, ?, ?)",
                ("dup", SESSION, 1, "turn_start", "now", "x" * 64),
            )

    def test_a_lost_race_retries_instead_of_writing_a_duplicate(
        self, event_db, monkeypatch
    ):
        # Simulate the interleaving: the first next_seq() hands back a number a
        # concurrent writer has already taken.
        import tools.agent_runtime.event_log as mod

        real = mod.next_seq
        stolen = {"done": False}

        def racy(session_id, conn=None):
            n = real(session_id, conn)
            if not stolen["done"]:
                stolen["done"] = True
                event_db.execute(
                    f"INSERT INTO {TABLE} (event_id, session_id, seq, event_type, "
                    "occurred_at, payload_hash) VALUES (?, ?, ?, ?, ?, ?)",
                    ("rival", session_id, n, "turn_start", "now", "y" * 64),
                )
                event_db.commit()
            return n

        monkeypatch.setattr(mod, "next_seq", racy)
        event = append(SESSION, "tool_call", {"a": 1}, policy=RETAIN)

        assert event.seq == 2
        seqs = [r["seq"] for r in _rows(event_db)]
        assert seqs == [1, 2]
        assert len(set(seqs)) == len(seqs)

    def test_an_unrelated_integrity_error_is_not_retried_into_a_loop(
        self, event_db, monkeypatch
    ):
        import tools.agent_runtime.event_log as mod

        calls = {"n": 0}
        original = mod.next_seq

        def counting(session_id, conn=None):
            calls["n"] += 1
            return original(session_id, conn)

        monkeypatch.setattr(mod, "next_seq", counting)
        # A NOT NULL violation is an IntegrityError that has nothing to do with
        # the seq index; retrying it would spin MAX_SEQ_ATTEMPTS times and then
        # report the wrong cause.
        monkeypatch.setattr(mod, "compute_payload_hash", lambda p: None)
        with pytest.raises(EventLogUnavailable):
            append(SESSION, "turn_start", None, policy=RETAIN)
        assert calls["n"] == 1
        assert calls["n"] < MAX_SEQ_ATTEMPTS

    def test_read_session_returns_events_in_seq_order(self, event_db):
        for etype in ("turn_start", "tool_call", "tool_result", "turn_end"):
            append(SESSION, etype, {"t": etype}, policy=RETAIN)
        assert [e.seq for e in read_session(SESSION)] == [1, 2, 3, 4]
        assert [e.event_type for e in read_session(SESSION)] == [
            "turn_start", "tool_call", "tool_result", "turn_end",
        ]


# ---------------------------------------------------------------------------
# 4. Append-only
# ---------------------------------------------------------------------------
class TestAppendOnly:
    def _module_source(self) -> str:
        return (REPO_ROOT / "tools" / "agent_runtime" / "event_log.py").read_text(
            encoding="utf-8"
        )

    def test_the_module_emits_no_update_or_delete_against_the_table(self):
        # Every SQL string literal in the module, checked. A verb that reaches
        # this table would make the log editable, and an editable audit log is
        # not one.
        tree = ast.parse(self._module_source())
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.upper()
                if ("UPDATE " in text or "DELETE " in text) and TABLE.upper() in text:
                    offenders.append(node.value)
            elif isinstance(node, ast.JoinedStr):
                text = "".join(
                    v.value for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ).upper()
                if "UPDATE " in text or "DELETE " in text:
                    offenders.append(text)
        assert offenders == []

    def test_the_module_exposes_no_mutating_verb(self):
        import tools.agent_runtime.event_log as mod

        public = {n for n in dir(mod) if not n.startswith("_") and callable(getattr(mod, n))}
        assert not {n for n in public if n in {"update", "delete", "upsert", "purge"}}
        assert {"append", "read_session", "next_seq"} <= public

    def test_appending_never_rewrites_an_earlier_row(self, event_db):
        append(SESSION, "turn_start", {"n": 1}, policy=RETAIN)
        first = _rows(event_db)[0]
        for etype in ("tool_call", "tool_result", "turn_end"):
            append(SESSION, etype, {"t": etype}, policy=RETAIN)
        assert _rows(event_db)[0] == first

    def test_the_table_is_registered_append_only(self):
        hook = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(
            encoding="utf-8"
        )
        assert f'"{TABLE}"' in hook


# ---------------------------------------------------------------------------
# 5. A failed write raises
# ---------------------------------------------------------------------------
class TestFailuresSurface:
    def test_a_missing_table_raises_and_names_the_migration(
        self, event_db, monkeypatch
    ):
        monkeypatch.setattr(_storage_module(), "table_exists", lambda c, t: False)
        with pytest.raises(EventLogUnavailable) as exc:
            append(SESSION, "turn_start", None, policy=RETAIN)
        assert MIGRATION in str(exc.value)

    def test_a_failed_insert_raises_rather_than_reporting_success(
        self, event_db, monkeypatch
    ):
        # The REAL translating connection with one verb sabotaged, rather than a
        # hand-rolled stub: everything else on the write path — the %s → ?
        # translation, next_seq's SELECT, the rollback — stays production shaped,
        # so this exercises the failure the code actually meets.
        monkeypatch.setattr(
            _storage_module(),
            "get_connection",
            lambda *a, **k: _sabotage_inserts(_translating_conn(event_db)),
        )
        with pytest.raises(EventLogUnavailable):
            append(SESSION, "turn_start", None, policy=RETAIN)
        assert _rows(event_db) == []

    def test_an_unknown_event_type_is_refused(self, event_db):
        with pytest.raises(ValueError):
            append(SESSION, "chunk", {"delta": "x"}, policy=RETAIN)
        assert _rows(event_db) == []

    def test_there_is_no_per_chunk_event_type(self):
        # Deliberately smaller than DSH's vocabulary: ICDEV's loop does not
        # stream into the log, so a chunk row would record the transport's
        # framing at one row per token.
        assert EVENT_TYPES == (
            "turn_start", "request_context", "assistant_message",
            "tool_call", "tool_result", "turn_end",
        )
        assert not any("chunk" in t for t in EVENT_TYPES)


# ---------------------------------------------------------------------------
# 6. Schema parity + identity
# ---------------------------------------------------------------------------
class TestSchema:
    def test_every_inserted_column_exists_in_the_live_table(self, event_db):
        live = {r[1] for r in event_db.execute(f"PRAGMA table_info({TABLE})")}
        assert set(COLUMNS) <= live, f"INSERT names columns the table lacks: {set(COLUMNS) - live}"

    def test_the_task_mandated_columns_are_all_present(self, event_db):
        live = {r[1] for r in event_db.execute(f"PRAGMA table_info({TABLE})")}
        assert {
            "event_id", "session_id", "seq", "event_type", "occurred_at",
            "payload_hash", "payload_json", "tenant_id", "classification",
            "correlation_id",
        } <= live

    def test_the_row_carries_tenant_and_classification_for_RLS(self, event_db):
        append(SESSION, "turn_start", None, policy=RETAIN)
        row = _rows(event_db)[0]
        assert row["tenant_id"] == "acme"       # from ICDEV_TENANT_ID
        assert row["classification"] == "CUI"

    def test_correlation_id_round_trips(self, event_db):
        append(SESSION, "tool_call", {"a": 1},
               correlation_id="corr-42", policy=RETAIN)
        assert _rows(event_db)[0]["correlation_id"] == "corr-42"
        assert read_session(SESSION)[0].correlation_id == "corr-42"

    def test_read_session_can_filter_by_type(self, event_db):
        for etype in ("turn_start", "tool_call", "tool_call", "turn_end"):
            append(SESSION, etype, None, policy=RETAIN)
        assert len(read_session(SESSION, event_types=["tool_call"])) == 2
        with pytest.raises(ValueError):
            read_session(SESSION, event_types=["chunk"])

    def test_include_payload_false_still_reports_withholding_truthfully(self, event_db):
        append(SESSION, "tool_call", {"a": 1}, policy=RETAIN)
        append(SESSION, "tool_result", {"b": 2}, policy=HASH_ONLY)
        stored, withheld = read_session(SESSION, include_payload=False)
        assert stored.payload is None and stored.payload_withheld is False
        assert withheld.payload is None and withheld.payload_withheld is True

    def test_read_session_of_an_unknown_session_is_empty_not_an_error(self, event_db):
        assert read_session("no-such-session") == []

    def test_event_to_dict_can_omit_the_payload(self):
        event = Event(
            event_id="e", session_id="s", seq=1, event_type="tool_call",
            occurred_at="now", payload_hash="h", payload={"secret": 1},
        )
        assert "payload" not in event.to_dict(include_payload=False)
        assert event.to_dict()["payload"] == {"secret": 1}
