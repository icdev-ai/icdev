# CUI // SP-CTI
"""Tests for ACE chat trigger, coworker_instance_id context storage, and HITL confidence gate."""
from __future__ import annotations

import json
import sqlite3
import types
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ace_db(tmp_path, monkeypatch):
    """Fresh temp SQLite DB with ACE schema pre-applied, pointed to by env var."""
    db_path = tmp_path / "ace_trigger_test.db"
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(db_path))
    from icdev.tools.ace.db.init_db import init as init_ace_db
    init_ace_db()
    return db_path


def _ace_conn():
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("ICDEV_ACE_DB_URL")


# ---------------------------------------------------------------------------
# 1. detect_ace_trigger — explicit / implicit / None
# ---------------------------------------------------------------------------


def test_detect_ace_trigger_explicit():
    from icdev.tools.ace.chat_trigger import detect_ace_trigger
    assert detect_ace_trigger("@team build a compliance dashboard") == "explicit"


def test_detect_ace_trigger_explicit_case_insensitive():
    from icdev.tools.ace.chat_trigger import detect_ace_trigger
    assert detect_ace_trigger("@TEAM build something") == "explicit"


def test_detect_ace_trigger_implicit():
    from icdev.tools.ace.chat_trigger import detect_ace_trigger
    # 200+ chars with 4+ RICOAS signals
    msg = (
        "The system shall provide a monitoring dashboard that must track all user activity. "
        "The system should alert on anomalies and needs to generate daily reports. "
        "Requirements include role-based access control and user story management. "
        "Please build this as a REST API with acceptance criteria defined per endpoint."
    )
    assert len(msg) >= 200
    result = detect_ace_trigger(msg)
    assert result == "implicit"


def test_detect_ace_trigger_none_too_short():
    from icdev.tools.ace.chat_trigger import detect_ace_trigger
    assert detect_ace_trigger("hello world") is None


def test_detect_ace_trigger_none_long_but_no_signals():
    from icdev.tools.ace.chat_trigger import detect_ace_trigger
    # 200+ chars but no RICOAS keywords
    msg = "a" * 210
    assert detect_ace_trigger(msg) is None


# ---------------------------------------------------------------------------
# 2. count_ricoas_signals
# ---------------------------------------------------------------------------


def test_count_ricoas_signals_basic():
    from icdev.tools.ace.chat_trigger import count_ricoas_signals
    text = "The system shall alert. It must comply. It should track. It needs to process."
    count = count_ricoas_signals(text)
    assert count >= 4


def test_count_ricoas_signals_zero():
    from icdev.tools.ace.chat_trigger import count_ricoas_signals
    assert count_ricoas_signals("hello world") == 0


# ---------------------------------------------------------------------------
# 3. maybe_launch_ace — non-trigger returns None
# ---------------------------------------------------------------------------


def test_maybe_launch_ace_no_trigger_returns_none():
    from icdev.tools.ace.chat_trigger import maybe_launch_ace
    result = maybe_launch_ace("ctx-1", "short message")
    assert result is None


def test_maybe_launch_ace_explicit_trigger(ace_db, monkeypatch):
    """@team message calls ACEController.launch and returns an instance_id."""
    from icdev.tools.ace import chat_trigger as _ct

    captured = {}

    class _FakeCtrl:
        @staticmethod
        def get_instance():
            return _FakeCtrl()

        def launch(self, problem_text, trigger_source, trigger_ref,
                   user_id="system", project_id=""):
            captured["launched"] = True
            captured["trigger_source"] = trigger_source
            captured["problem_text"] = problem_text
            return "ace-test-001"

    monkeypatch.setattr(_ct, "ACEController", _FakeCtrl)

    result = _ct.maybe_launch_ace("ctx-explicit", "@team build a REST API")
    assert result == "ace-test-001"
    assert captured.get("launched") is True
    assert captured.get("trigger_source") == "chat"
    assert "build a REST API" in captured.get("problem_text", "")


# ---------------------------------------------------------------------------
# 4. coworker_instance_id stored in chat context metadata
# ---------------------------------------------------------------------------


def test_check_coworker_trigger_stores_instance_id(tmp_path, monkeypatch):
    """_check_coworker_trigger persists coworker_instance_id to chat_contexts.context_config."""
    db_path = tmp_path / "chat_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE chat_contexts (
            id TEXT PRIMARY KEY,
            context_config TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("INSERT INTO chat_contexts (id) VALUES ('ctx-ace-001')")
    conn.commit()
    conn.close()

    import tools.dashboard.chat_manager as _cm
    # DB_PATH only. get_connection stays real so its StorageConnection rewrites
    # the %s placeholders this repo authors for PostgreSQL into ? for SQLite;
    # a raw sqlite3.connect skips that, and _check_coworker_trigger's
    # best-effort `except Exception` then swallows the syntax error, so the
    # test measured a no-op it had caused itself.
    monkeypatch.setattr(_cm, "DB_PATH", db_path)

    _cm._check_coworker_trigger(
        "ctx-ace-001",
        "@team build a pipeline",
        {"coworker_instance_id": "ace-inst-xyz"},
    )

    conn2 = sqlite3.connect(str(db_path))
    row = conn2.execute(
        "SELECT context_config FROM chat_contexts WHERE id = 'ctx-ace-001'"
    ).fetchone()
    conn2.close()

    assert row is not None, "context_config row must exist"
    cfg = json.loads(row[0])
    assert cfg.get("coworker_instance_id") == "ace-inst-xyz"


# ---------------------------------------------------------------------------
# 5. HITL confidence gate at trust_score < 0.6
# ---------------------------------------------------------------------------


def _fake_loader_cls(steps):
    class _Loader:
        def __init__(self, *args, **kwargs):
            pass
        def get_role(self, role_id):
            return types.SimpleNamespace(steps=list(steps), communication={}, tool_permissions=[])
    return _Loader


def _spec(coworker_id: str = "cw-hitl", role_id: str = "ai_developer"):
    from icdev.tools.ace.team_assembler import CoWorkerSpec
    return CoWorkerSpec(
        coworker_id=coworker_id,
        role_id=role_id,
        role_slot=role_id,
        mailbox_id=f"mailbox:{coworker_id}",
        llm_function="code_generation",
        tool_permissions=[],
        trust_tier="yellow",
    )


def test_hitl_gate_fires_when_trust_below_0_6(ace_db, monkeypatch):
    """CoWorkerThread enters hitl_pending when trust_score < 0.6."""
    import icdev.tools.ace.coworker_thread as _cwt

    # Patch trust_calibrator to return a low score
    monkeypatch.setattr(
        _cwt,
        "get_trust_score" if hasattr(_cwt, "get_trust_score") else "_TRUST_SUPERVISED_PLACEHOLDER",
        lambda role_id: 0.45,
        raising=False,
    )

    # Patch trust_calibrator module-level get_trust_score
    import icdev.tools.ace.trust_calibrator as _tc
    monkeypatch.setattr(_tc, "get_trust_score", lambda role_id: 0.45)

    # Stub step executor and role loader
    record: list[str] = []

    class _RecordingExecutor:
        def run(self, step, context, spec, trust_kernel):
            record.append(step.get("id"))
            return {"step": step.get("id")}

    monkeypatch.setattr(_cwt, "StepExecutor", _RecordingExecutor)
    monkeypatch.setattr(_cwt, "RoleLoader", _fake_loader_cls(["s1"]))

    from icdev.tools.ace.coworker_thread import CoWorkerThread, HITLGate

    bus = MagicMock()
    bus.poll_inbox.return_value = []

    thread = CoWorkerThread(
        spec=_spec("cw-low-trust"),
        instance_id="ace-inst-low-trust",
        message_bus=bus,
        trust_kernel=MagicMock(),
    )

    # Simulate HITL being immediately resolved (no pending items)
    monkeypatch.setattr(HITLGate, "get_pending", staticmethod(lambda cw_id: []))

    states_set: list[str] = []
    _orig_set_state = thread._set_state

    def _capture_state(state):
        states_set.append(state)
        return _orig_set_state(state)

    thread._set_state = _capture_state

    thread._run_inner()

    assert "hitl_pending" in states_set, (
        "CoWorkerThread must enter hitl_pending state when trust_score < 0.6"
    )
    assert record == ["s1"], "After HITL resolves, steps should still execute"


def test_hitl_gate_skipped_when_trust_above_0_6(ace_db, monkeypatch):
    """CoWorkerThread skips the confidence gate when trust_score >= 0.6."""
    import icdev.tools.ace.coworker_thread as _cwt
    import icdev.tools.ace.trust_calibrator as _tc

    monkeypatch.setattr(_tc, "get_trust_score", lambda role_id: 0.75)

    record: list[str] = []

    class _RecordingExecutor:
        def run(self, step, context, spec, trust_kernel):
            record.append(step.get("id"))
            return {"step": step.get("id")}

    monkeypatch.setattr(_cwt, "StepExecutor", _RecordingExecutor)
    monkeypatch.setattr(_cwt, "RoleLoader", _fake_loader_cls(["s1", "s2"]))

    from icdev.tools.ace.coworker_thread import CoWorkerThread

    bus = MagicMock()
    bus.poll_inbox.return_value = []

    thread = CoWorkerThread(
        spec=_spec("cw-high-trust"),
        instance_id="ace-inst-high-trust",
        message_bus=bus,
        trust_kernel=MagicMock(),
    )

    states_set: list[str] = []
    _orig_set_state = thread._set_state

    def _capture_state(state):
        states_set.append(state)
        return _orig_set_state(state)

    thread._set_state = _capture_state

    thread._run_inner()

    assert "hitl_pending" not in states_set, (
        "CoWorkerThread must NOT enter hitl_pending when trust_score >= 0.6"
    )
    assert record == ["s1", "s2"]
