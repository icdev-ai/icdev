# CUI // SP-CTI
"""Every context injection is recorded as a ``request_context`` event (hcx-evt-03).

Seven things have to be true, and each has a test class here:

  1. All three injectors record. Before this card nothing did, and a
     ``request_context`` event type that is declared and never emitted is this
     platform's signature defect wearing the audit log's clothes.
  2. The event NAMES the source. A row that cannot say which injector produced it
     does not close the gap, whatever else it holds.
  3. The envelope survives a retention policy that suppresses the body — and the
     body does not. Two withheld flags, two different facts.
  4. Recording never breaks injection. A dead event log, a raising ``append``, a
     missing table: the block still reaches the model, unchanged.
  5. Swallowed is not unmeasured. Every outcome lands in exactly one counter, and
     a failure is separated from a skip.
  6. Nothing is recorded when nothing was injected. Fabricated coverage is worse
     than measured absence.
  7. The runtime files events under the chat ``context_id``, which exists on turn
     one, and not under the agent-loop session id, which does not.

The schema comes from the migration's own up.sql, as ``test_agent_event_log.py``
does — a column added to one and not the other has to fail here rather than at
runtime inside somebody's ``except``.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import context_events
from tools.agent_runtime.context_events import (
    EVENT_TYPE,
    SOURCES,
    build_envelope,
    coverage,
    injections_for_session,
    record_injection,
    reset_stats,
    stats,
)
from tools.agent_runtime.event_log import (
    EVENT_TYPES,
    MIGRATION,
    TABLE,
    RetentionPolicy,
)
from tools.audit.row_hash import compute_payload_hash

REPO_ROOT = Path(__file__).resolve().parents[2]

SESSION = "ctx-evt-3"


# ---------------------------------------------------------------------------
# Fixtures — the real table, behind the production %s → ? translation
# ---------------------------------------------------------------------------
def _events_ddl() -> str:
    path = REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    return path.read_text(encoding="utf-8")


def _storage_module():
    """The module ``event_log`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` and ``icdev.tools.db.storage`` are two distinct module
    objects and patching the wrong one installs a fake nothing calls — the test
    then asserts against its own no-op while the code under test writes to the
    LIVE board. ``event_log._connect`` imports ``from tools.db.storage import``
    inside the function, so that is the binding to replace.
    """
    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    ``unclosable``: ``event_log`` closes its connection in a ``finally`` and the
    fixture's has to outlive that. A named factory rather than an inline
    ``translating(...)`` because ``coherence_checker.check_test_db_isolation``
    seeds its safe-name set from local factory FUNCTIONS.
    """
    return translating(raw, unclosable=True)


@pytest.fixture
def event_db(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "events.db"))
    raw.executescript(_events_ddl())
    conn = _translating_conn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.delenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", raising=False)
    monkeypatch.setenv("ICDEV_TENANT_ID", "acme")
    reset_stats()
    yield raw
    raw.close()
    reset_stats()


@pytest.fixture
def dead_log(monkeypatch):
    """An event log whose ``append`` raises, as a dead database would."""
    from tools.agent_runtime import event_log

    def boom(*a, **k):
        raise event_log.EventLogUnavailable("agent_session_events is missing")

    monkeypatch.setattr(context_events, "append", boom)
    reset_stats()
    yield
    reset_stats()


def _rows(raw: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {TABLE} ORDER BY seq")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


RETAIN = RetentionPolicy(enabled=True, max_classification="CUI")
HASH_ONLY = RetentionPolicy(enabled=False)
#: The setting args/agent_event_log.yaml names as this flag's intended use.
NEVER_REQUEST_CONTEXT = RetentionPolicy(
    enabled=True, max_classification="CUI", never_store=(EVENT_TYPE,)
)


# ---------------------------------------------------------------------------
# 1. All three injectors record
# ---------------------------------------------------------------------------
class TestEveryInjectorRecords:
    """The gap: three modules injected into the prompt and none left a trace."""

    def test_project_context_records(self, event_db, monkeypatch, tmp_path):
        from tools.agent_runtime import project_context

        (tmp_path / "CLAUDE.md").write_text("# Rules\nBe direct.", encoding="utf-8")
        monkeypatch.setattr(project_context, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(project_context, "context_enabled", lambda: True)
        monkeypatch.setattr(project_context, "project_state_enabled", lambda: False)

        text = project_context.build_for_runtime(
            "code_generation", session_id=SESSION
        )
        assert "Be direct." in text

        entries = injections_for_session(SESSION)
        assert [e["source"] for e in entries] == ["project_context"]
        assert entries[0]["size_chars"] == len(text)

    def test_goal_context_records(self, event_db, monkeypatch):
        from tools.agent_runtime import goal_context

        goal = type("G", (), {"title": "Ship hcx-evt-03", "detail": "", "progress": 0})()
        monkeypatch.setattr(goal_context, "goals_enabled", lambda: True)
        monkeypatch.setattr(goal_context, "active_goals", lambda **k: [goal])

        text = goal_context.build_for_runtime("code_generation", session_id=SESSION)
        assert "Ship hcx-evt-03" in text

        entries = injections_for_session(SESSION)
        assert [e["source"] for e in entries] == ["goal_context"]
        assert entries[0]["detail"]["shown"] == 1

    def test_profile_memory_records(self, event_db, monkeypatch):
        from tools.agent_runtime import profile_memory

        monkeypatch.setattr(
            profile_memory,
            "load_profile",
            lambda *a, **k: {
                "preferences": {"tone": "direct"},
                "facts": [{"text": "Prefers worktrees", "confidence": 0.9}],
            },
        )
        text = profile_memory.build_profile_context("op", session_id=SESSION)
        assert "Prefers worktrees" in text

        entries = injections_for_session(SESSION)
        assert [e["source"] for e in entries] == ["profile_memory"]
        assert entries[0]["detail"]["facts_shown"] == 1
        assert entries[0]["detail"]["memory_searched"] is False

    def test_all_three_land_in_one_ordered_session(self, event_db):
        for source in SOURCES:
            record_injection(SESSION, source, f"block from {source}", policy=RETAIN)
        rows = _rows(event_db)
        assert [r["seq"] for r in rows] == [1, 2, 3]
        assert all(r["event_type"] == EVENT_TYPE for r in rows)
        assert coverage(SESSION)["total"] == 3
        assert all(s["recorded"] for s in coverage(SESSION)["sources"].values())

    def test_request_context_is_a_real_event_type(self):
        # The vocabulary is event_log's; this module must not invent a seventh.
        assert EVENT_TYPE in EVENT_TYPES


# ---------------------------------------------------------------------------
# 2. The event names the source
# ---------------------------------------------------------------------------
class TestTheEventNamesItsSource:
    def test_source_is_persisted_and_read_back(self, event_db):
        record_injection(SESSION, "goal_context", "goals", policy=RETAIN)
        assert injections_for_session(SESSION)[0]["source"] == "goal_context"

    def test_an_unregistered_source_is_recorded_and_flagged(self, event_db):
        # Dropping an injection because its name is unfamiliar would be the exact
        # failure this card closes. Record it; flag it.
        event = record_injection(SESSION, "some_new_injector", "text", policy=RETAIN)
        assert event is not None
        entry = injections_for_session(SESSION)[0]
        assert entry["source"] == "some_new_injector"
        assert entry["source_registered"] is False
        assert coverage(SESSION)["unregistered"] == ["some_new_injector"]

    def test_registered_sources_are_flagged_registered(self, event_db):
        record_injection(SESSION, "project_context", "text", policy=RETAIN)
        assert injections_for_session(SESSION)[0]["source_registered"] is True


# ---------------------------------------------------------------------------
# 3. Envelope always; body only when the policy allows
# ---------------------------------------------------------------------------
class TestBodyRetentionIsPolicyGated:
    def test_hash_only_mode_keeps_the_envelope_and_drops_the_body(self, event_db):
        record_injection(SESSION, "project_context", "SECRET RULES", policy=HASH_ONLY)
        entry = injections_for_session(SESSION, include_body=True)[0]
        # The row can still say what it was and how big — the whole point.
        assert entry["source"] == "project_context"
        assert entry["size_chars"] == len("SECRET RULES")
        assert entry["body_stored"] is False
        assert entry.get("body") is None
        # ...and the content digest survives, so a holder of the block can prove
        # it is the one that was injected.
        assert entry["body_sha256"] == compute_payload_hash("SECRET RULES")

    def test_never_store_request_context_drops_only_the_body(self, event_db):
        # args/agent_event_log.yaml names this event type as never_store's
        # intended use. Honour it for the body; keep the envelope.
        record_injection(
            SESSION, "goal_context", "my goals", policy=NEVER_REQUEST_CONTEXT
        )
        entry = injections_for_session(SESSION, include_body=True)[0]
        assert entry["body_stored"] is False
        assert entry.get("body") is None
        assert entry["source"] == "goal_context"

    def test_retention_on_stores_the_body_verbatim(self, event_db):
        record_injection(SESSION, "project_context", "# Rules", policy=RETAIN)
        entry = injections_for_session(SESSION, include_body=True)[0]
        assert entry["body_stored"] is True
        assert entry["body"] == "# Rules"

    def test_env_override_forces_hash_only(self, event_db, monkeypatch):
        monkeypatch.setenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", "0")
        record_injection(SESSION, "project_context", "# Rules")
        assert injections_for_session(SESSION)[0]["body_stored"] is False

    def test_envelope_withheld_and_body_stored_are_different_facts(self, event_db):
        # payload_json (the envelope) is always kept; body_stored describes the
        # text. Merging them would make "retention is off" read identically to
        # "no context was injected".
        record_injection(SESSION, "project_context", "x", policy=HASH_ONLY)
        entry = injections_for_session(SESSION)[0]
        assert entry["envelope_withheld"] is False
        assert entry["body_stored"] is False
        assert _rows(event_db)[0]["payload_json"] is not None

    def test_row_hash_is_always_written(self, event_db):
        record_injection(SESSION, "project_context", "x", policy=HASH_ONLY)
        assert len(_rows(event_db)[0]["payload_hash"]) == 64

    def test_body_sha256_is_identical_under_both_policies(self, event_db):
        record_injection(SESSION, "project_context", "same text", policy=RETAIN)
        record_injection(SESSION, "goal_context", "same text", policy=HASH_ONLY)
        digests = {e["body_sha256"] for e in injections_for_session(SESSION)}
        assert len(digests) == 1

    def test_envelope_shape_is_pinned(self):
        env = build_envelope("goal_context", "abc", detail={"shown": 2})
        assert env["source"] == "goal_context"
        assert env["size_chars"] == 3
        assert env["body_stored"] is True
        assert env["body"] == "abc"
        assert env["detail"] == {"shown": 2}
        assert env["body_sha256"] == compute_payload_hash("abc")

    def test_withheld_envelope_has_no_body_key_at_all(self):
        env = build_envelope("goal_context", "abc", store_body=False)
        assert "body" not in env
        assert env["body_sha256"] == compute_payload_hash("abc")


# ---------------------------------------------------------------------------
# 4. Recording never breaks injection
# ---------------------------------------------------------------------------
class TestRecordingNeverBreaksInjection:
    """"Recording must not become a new way for context injection to fail.\""""

    def test_record_injection_returns_none_instead_of_raising(self, dead_log):
        assert record_injection(SESSION, "project_context", "text") is None

    def test_project_context_still_returns_its_block(self, dead_log, monkeypatch, tmp_path):
        from tools.agent_runtime import project_context

        (tmp_path / "CLAUDE.md").write_text("# Rules\nBe direct.", encoding="utf-8")
        monkeypatch.setattr(project_context, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(project_context, "context_enabled", lambda: True)
        monkeypatch.setattr(project_context, "project_state_enabled", lambda: False)
        text = project_context.build_for_runtime("code_generation", session_id=SESSION)
        assert "Be direct." in text

    def test_goal_context_still_returns_its_block(self, dead_log, monkeypatch):
        from tools.agent_runtime import goal_context

        goal = type("G", (), {"title": "Ship it", "detail": "", "progress": 0})()
        monkeypatch.setattr(goal_context, "goals_enabled", lambda: True)
        monkeypatch.setattr(goal_context, "active_goals", lambda **k: [goal])
        assert "Ship it" in goal_context.build_for_runtime(
            "code_generation", session_id=SESSION
        )

    def test_profile_memory_still_returns_its_block(self, dead_log, monkeypatch):
        from tools.agent_runtime import profile_memory

        monkeypatch.setattr(
            profile_memory, "load_profile",
            lambda *a, **k: {"preferences": {}, "facts": [
                {"text": "Prefers worktrees", "confidence": 0.9}]},
        )
        assert "Prefers worktrees" in profile_memory.build_profile_context(
            "op", session_id=SESSION
        )

    def test_a_missing_context_events_module_is_survivable(self, monkeypatch, tmp_path):
        # The import guard at each call site, not the function's own except: a
        # stripped runtime may ship no event log at all.
        from tools.agent_runtime import project_context

        (tmp_path / "CLAUDE.md").write_text("# Rules\nBe direct.", encoding="utf-8")
        monkeypatch.setattr(project_context, "repo_root", lambda: tmp_path)
        monkeypatch.setattr(project_context, "context_enabled", lambda: True)
        monkeypatch.setattr(project_context, "project_state_enabled", lambda: False)
        monkeypatch.setitem(sys.modules, "tools.agent_runtime.context_events", None)
        assert "Be direct." in project_context.build_for_runtime(
            "code_generation", session_id=SESSION
        )

    def test_a_broken_retention_policy_does_not_break_injection(self, event_db, monkeypatch):
        def boom():
            raise RuntimeError("yaml is corrupt")

        monkeypatch.setattr(context_events, "load_policy", boom)
        assert record_injection(SESSION, "project_context", "text") is None
        assert stats()["failed"] == 1


# ---------------------------------------------------------------------------
# 5. Swallowed is not unmeasured
# ---------------------------------------------------------------------------
class TestFailuresAreCounted:
    def test_a_success_increments_recorded_only(self, event_db):
        record_injection(SESSION, "project_context", "text", policy=RETAIN)
        assert stats()["recorded"] == 1
        assert stats()["failed"] == 0

    def test_a_failure_increments_failed_and_records_the_error(self, dead_log):
        record_injection(SESSION, "project_context", "text")
        assert stats()["failed"] == 1
        assert "EventLogUnavailable" in stats()["last_error"]

    def test_a_skip_is_not_a_failure(self, event_db):
        record_injection(SESSION, "project_context", "")      # nothing injected
        record_injection("", "project_context", "text")        # no session
        assert stats()["failed"] == 0
        assert stats()["skipped_empty"] == 1
        assert stats()["skipped_no_session"] == 1

    def test_every_call_lands_in_exactly_one_bucket(self, dead_log):
        record_injection(SESSION, "project_context", "text")
        record_injection(SESSION, "project_context", "")
        record_injection("", "project_context", "text")
        s = stats()
        assert (
            s["recorded"] + s["skipped_empty"] + s["skipped_no_session"] + s["failed"]
        ) == 3


# ---------------------------------------------------------------------------
# 6. Nothing injected, nothing recorded
# ---------------------------------------------------------------------------
class TestNoFabricatedCoverage:
    def test_an_empty_block_writes_no_row(self, event_db):
        assert record_injection(SESSION, "goal_context", "") is None
        assert _rows(event_db) == []

    def test_no_session_writes_no_row(self, event_db):
        assert record_injection("", "goal_context", "text") is None
        assert _rows(event_db) == []

    def test_a_disabled_injector_writes_no_row(self, event_db, monkeypatch):
        from tools.agent_runtime import goal_context

        monkeypatch.setattr(goal_context, "goals_enabled", lambda: False)
        assert goal_context.build_for_runtime("code_generation", session_id=SESSION) == ""
        assert _rows(event_db) == []

    def test_coverage_reports_absence_without_calling_it_a_defect(self, event_db):
        record_injection(SESSION, "project_context", "text", policy=RETAIN)
        report = coverage(SESSION)
        assert report["sources"]["project_context"]["recorded"] is True
        assert report["sources"]["goal_context"]["recorded"] is False
        assert report["unregistered"] == []

    def test_injections_for_an_unreadable_log_is_empty_not_an_exception(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no such table")

        monkeypatch.setattr(context_events, "read_session", boom)
        assert injections_for_session(SESSION) == []


# ---------------------------------------------------------------------------
# 7. The runtime files events under an id that exists on turn one
# ---------------------------------------------------------------------------
class TestRuntimeSuppliesTheRightIds:
    def test_session_id_is_the_chat_context_id(self):
        from tools.agent_runtime.runtime import AgentRuntime

        rt = AgentRuntime.__new__(AgentRuntime)
        rt.session = type("S", (), {"context_id": "ctx-42", "resume_session_id": ""})()
        assert rt._event_session_id() == "ctx-42"

    def test_correlation_id_is_the_agent_loop_session_id(self):
        from tools.agent_runtime.runtime import AgentRuntime

        rt = AgentRuntime.__new__(AgentRuntime)
        rt.session = type(
            "S", (), {"context_id": "ctx-42", "resume_session_id": "loop-7"}
        )()
        assert rt._event_correlation_id() == "loop-7"

    def test_turn_one_has_a_session_id_but_no_correlation_id(self):
        # The whole reason context_id is the key: the loop session id does not
        # exist until a turn COMPLETES, and injection happens before one starts.
        from tools.agent_runtime.runtime import AgentRuntime

        rt = AgentRuntime.__new__(AgentRuntime)
        rt.session = type("S", (), {"context_id": "ctx-42", "resume_session_id": ""})()
        assert rt._event_session_id() == "ctx-42"
        assert rt._event_correlation_id() == ""

    def test_correlation_id_is_persisted_on_the_row(self, event_db):
        record_injection(
            SESSION, "goal_context", "goals", correlation_id="loop-7", policy=RETAIN
        )
        assert _rows(event_db)[0]["correlation_id"] == "loop-7"
