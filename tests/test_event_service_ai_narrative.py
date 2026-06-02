# CUI // SP-CTI
"""Tests for the AI-ified event narrative in the event service (aiify-opp-5716).

The db -> render -> notify chains in ``tools.notification_service.event_service``
gained an opt-in LLM event narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so an event notification
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import event_service


def _fake_router(monkeypatch, *, content=None, raises=None):
    """Install a fake LLMRouter whose ``invoke`` returns/raises as directed."""
    captured = {}

    class _Resp:
        def __init__(self, text):
            self.content = text

    class _FakeRouter:
        def invoke(self, function, request):
            captured["function"] = function
            captured["request"] = request
            if raises is not None:
                raise raises
            return _Resp(content)

    fake_module = types.SimpleNamespace(LLMRouter=_FakeRouter)
    monkeypatch.setitem(__import__("sys").modules, "tools.llm.router", fake_module)
    return captured


def test_narrative_returns_content_when_llm_available(monkeypatch):
    captured = _fake_router(monkeypatch, content="  Task TASK-42 completed; sprint velocity is on track.  ")
    facts = {
        "task_id": "TASK-42",
        "title": "Add ZIG maturity report",
        "actor": "sovanna",
        "event_type": "task_completed",
        "duration": "2h 15m",
    }

    out = event_service._ai_event_narrative("kanban task_completed notification", facts)

    assert out == "Task TASK-42 completed; sprint velocity is on track."
    assert captured["function"] == "narrative_generation"
    user_msg = captured["request"].messages[0]["content"]
    assert "kanban task_completed notification" in user_msg
    assert "task_id" in user_msg
    assert "event_type" in user_msg
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = event_service._ai_event_narrative(
        "oracle cat1_new alert notification", {"lens_id": "L-1", "count": "3"}
    )

    assert out is None


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = event_service._ai_event_narrative(
        "genesis phase_complete milestone notification", {"design_id": "D-1", "phase": "validate"}
    )

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = event_service._ai_event_narrative(
        "kanban sprint_closed notification", {"sprint": "S-12", "done_count": "8"}
    )

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last", "a_first": "first", "m_middle": "middle"}

    event_service._ai_event_narrative("genesis reflex_fired milestone notification", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z


def test_event_kind_appears_in_prompt(monkeypatch):
    """The event_kind label must appear in the user message for framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    event_service._ai_event_narrative(
        "oracle cat1_escalate alert notification",
        {"lens_id": "L-2", "count": "5", "horizon": "24"},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "oracle cat1_escalate alert notification" in user_msg
    assert "lens_id" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for event narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    event_service._ai_event_narrative("kanban epic_complete notification", {"epic_key": "E-1"})

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    event_service._ai_event_narrative(
        "genesis drift_detected milestone notification", {"component": "rag", "delta": "0.12"}
    )

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


# ---------------------------------------------------------------------------
# Integration tests: send_platform_event_digest (aiify-opp-5754)
# ---------------------------------------------------------------------------

def _stub_db(monkeypatch):
    """Monkeypatch get_connection to return an in-memory SQLite DB with the
    tables send_platform_event_digest queries (kanban_tasks, genesis_phase_log,
    oracle_predictions). All three tables are empty so counts return 0."""
    import sqlite3

    class _FakeConn:
        def __init__(self):
            self._db = sqlite3.connect(":memory:")
            self._db.row_factory = sqlite3.Row
            self._db.execute(
                "CREATE TABLE kanban_tasks "
                "(id TEXT, title TEXT, status TEXT, updated_at TEXT)"
            )
            self._db.execute(
                "CREATE TABLE genesis_phase_log "
                "(design_id TEXT, phase TEXT, status TEXT, completed_at TEXT)"
            )
            self._db.execute(
                "CREATE TABLE oracle_predictions "
                "(id TEXT, title TEXT, severity TEXT, confidence REAL, "
                "created_at TEXT, outcome TEXT)"
            )
            self._db.commit()

        def execute(self, sql, params=()):
            return self._db.execute(sql, params)

        def close(self):
            self._db.close()

    monkeypatch.setattr(event_service, "get_connection", _FakeConn)
    monkeypatch.setattr(event_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(event_service, "sendmail", lambda **kw: None)
    monkeypatch.setattr(event_service, "emit", lambda *a, **kw: None)


def test_send_platform_event_digest_no_narrative_by_default(monkeypatch):
    """When ai_narrative is omitted, narrative must be None."""
    _stub_db(monkeypatch)

    result = event_service.send_platform_event_digest("ops@icdev.local")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["kanban_event_count"] == 0
    assert result["genesis_milestone_count"] == 0
    assert result["oracle_alert_count"] == 0


def test_send_platform_event_digest_attaches_narrative_when_enabled(monkeypatch):
    """With ai_narrative=True and a functioning LLM, narrative is attached."""
    _stub_db(monkeypatch)
    _fake_router(monkeypatch, content="No critical events detected in the last 24 hours.")

    result = event_service.send_platform_event_digest(
        "ops@icdev.local", hours=24, ai_narrative=True
    )

    assert result["status"] == "sent"
    assert result["narrative"] == "No critical events detected in the last 24 hours."


def test_send_platform_event_digest_graceful_degradation_on_llm_failure(monkeypatch):
    """If the LLM raises, narrative is None but the digest is still sent."""
    _stub_db(monkeypatch)
    _fake_router(monkeypatch, raises=ConnectionError("LLM unreachable"))

    result = event_service.send_platform_event_digest(
        "ops@icdev.local", ai_narrative=True
    )

    assert result["status"] == "sent"
    assert result["narrative"] is None


# ---------------------------------------------------------------------------
# Integration tests: notify_aiify_opportunity_event (aiify-opp-5792)
# ---------------------------------------------------------------------------

def _stub_aiify_db(monkeypatch):
    """Monkeypatch get_connection to return an in-memory SQLite DB with the
    tables notify_aiify_opportunity_event queries (kanban_tasks, audit_trail)."""
    import sqlite3

    class _FakeConn:
        def __init__(self):
            self._db = sqlite3.connect(":memory:")
            self._db.row_factory = sqlite3.Row
            self._db.execute(
                "CREATE TABLE kanban_tasks "
                "(id TEXT, title TEXT, status TEXT, actor TEXT, attempts INTEGER, "
                "created_at TEXT, updated_at TEXT)"
            )
            self._db.execute(
                "INSERT INTO kanban_tasks VALUES "
                "('aiify-opp-5792', 'LLM narrative for event_service', 'in_progress', "
                "'kanban-scheduler', 1, '2026-06-02T00:00:00', '2026-06-02T01:00:00')"
            )
            self._db.execute(
                "CREATE TABLE audit_trail "
                "(resource_type TEXT, resource_id TEXT, event TEXT, actor TEXT, "
                "detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
            self._db.commit()

        def execute(self, sql, params=()):
            return self._db.execute(sql, params)

        def commit(self):
            self._db.commit()

        def close(self):
            self._db.close()

    monkeypatch.setattr(event_service, "get_connection", _FakeConn)
    monkeypatch.setattr(event_service, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "notify", lambda *a, **kw: None)


def test_notify_aiify_opportunity_event_no_narrative_by_default(monkeypatch):
    """When ai_narrative is omitted, narrative must be None and delivery succeeds."""
    _stub_aiify_db(monkeypatch)

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=5792,
        event_type="opportunity_dispatched",
        channels=["slack"],
        extra={
            "module_path": "tools/notification_service/event_service.py",
            "function_name": "<unknown>",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "composite_score": 0.7769,
        },
    )

    assert result["status"] == "delivered"
    assert result["opportunity_id"] == 5792
    assert result["event_type"] == "opportunity_dispatched"
    assert result["narrative"] is None
    assert "slack" in result["receipts"]


def test_notify_aiify_opportunity_event_attaches_narrative_when_enabled(monkeypatch):
    """With ai_narrative=True and a functioning LLM, narrative is attached."""
    _stub_aiify_db(monkeypatch)
    _fake_router(
        monkeypatch,
        content="Opportunity 5792 detected a db→render→notify chain in event_service; apply LLM narrative immediately.",
    )

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=5792,
        event_type="opportunity_detected",
        channels=["console"],
        extra={
            "module_path": "tools/notification_service/event_service.py",
            "function_name": "<unknown>",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "composite_score": 0.7769,
        },
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] == (
        "Opportunity 5792 detected a db→render→notify chain in event_service; "
        "apply LLM narrative immediately."
    )


def test_notify_aiify_opportunity_event_graceful_degradation_on_llm_failure(monkeypatch):
    """If the LLM raises, narrative is None but the notification is still delivered."""
    _stub_aiify_db(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("LLM unavailable"))

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=5792,
        event_type="opportunity_completed",
        channels=["webhook"],
        extra={
            "module_path": "tools/notification_service/event_service.py",
            "function_name": "<unknown>",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "composite_score": 0.7769,
        },
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "webhook" in result["receipts"]
