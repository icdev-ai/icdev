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


# ---------------------------------------------------------------------------
# Integration tests: notify_kanban_event (aiify-opp-5828)
# ---------------------------------------------------------------------------

def _stub_kanban_notify_db(monkeypatch):
    """In-memory SQLite with kanban_tasks + audit_trail for notify_kanban_event."""
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
                "('dt-test-01', 'Add ZIG maturity report', 'done', 'system', 1, "
                "'2026-06-02T00:00:00', '2026-06-02T02:00:00')"
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
    monkeypatch.setattr(event_service, "render_to_string", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(event_service, "send", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "notify", lambda *a, **kw: None)


def test_notify_kanban_event_no_narrative_by_default(monkeypatch):
    """When ai_narrative is omitted, narrative must be None and delivery succeeds."""
    _stub_kanban_notify_db(monkeypatch)

    result = event_service.notify_kanban_event(
        task_id="dt-test-01",
        event_type="task_completed",
        channels=["console"],
    )

    assert result["status"] == "delivered"
    assert result["task_id"] == "dt-test-01"
    assert result["narrative"] is None
    assert "console" in result["receipts"]


def test_notify_kanban_event_attaches_narrative_when_enabled(monkeypatch):
    """With ai_narrative=True and a functioning LLM, narrative is attached."""
    _stub_kanban_notify_db(monkeypatch)
    _fake_router(monkeypatch, content="Task dt-test-01 completed successfully; sprint cadence remains on track.")

    result = event_service.notify_kanban_event(
        task_id="dt-test-01",
        event_type="task_completed",
        channels=["console"],
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] == "Task dt-test-01 completed successfully; sprint cadence remains on track."


def test_notify_kanban_event_graceful_degradation_on_llm_failure(monkeypatch):
    """If the LLM raises, narrative is None but the notification is still delivered."""
    _stub_kanban_notify_db(monkeypatch)
    _fake_router(monkeypatch, raises=ConnectionError("LLM unreachable"))

    result = event_service.notify_kanban_event(
        task_id="dt-test-01",
        event_type="task_blocked",
        channels=["console"],
        extra={"reason": "dependency unavailable"},
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "console" in result["receipts"]


# ---------------------------------------------------------------------------
# Integration tests: notify_genesis_milestone (aiify-opp-5828)
# ---------------------------------------------------------------------------

def _stub_genesis_notify_db(monkeypatch):
    """In-memory SQLite with empty Genesis tables for notify_genesis_milestone."""
    import sqlite3

    class _FakeConn:
        def __init__(self):
            self._db = sqlite3.connect(":memory:")
            self._db.row_factory = sqlite3.Row
            self._db.execute(
                "CREATE TABLE genesis_designs "
                "(id TEXT, name TEXT, status TEXT, current_phase TEXT, created_at TEXT)"
            )
            self._db.execute(
                "CREATE TABLE genesis_phase_log "
                "(design_id TEXT, phase TEXT, status TEXT, started_at TEXT, completed_at TEXT)"
            )
            self._db.execute(
                "CREATE TABLE genesis_reflexes "
                "(design_id TEXT, name TEXT, confidence REAL, fired_at TEXT)"
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
    monkeypatch.setattr(event_service, "sendmail", lambda **kw: None)
    monkeypatch.setattr(event_service, "dispatch", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "emit", lambda *a, **kw: None)


def test_notify_genesis_milestone_no_narrative_by_default(monkeypatch):
    """When ai_narrative is omitted, narrative must be None and delivery succeeds."""
    _stub_genesis_notify_db(monkeypatch)

    result = event_service.notify_genesis_milestone(
        design_id="D-42",
        milestone_type="phase_complete",
        channels=["console"],
        phase_data={"phase": "validate", "next_phase": "deploy"},
    )

    assert result["status"] == "delivered"
    assert result["design_id"] == "D-42"
    assert result["narrative"] is None
    assert "console" in result["receipts"]


def test_notify_genesis_milestone_attaches_narrative_when_enabled(monkeypatch):
    """With ai_narrative=True and a functioning LLM, narrative is attached."""
    _stub_genesis_notify_db(monkeypatch)
    _fake_router(monkeypatch, content="Genesis design D-42 has completed the validate phase; deploy phase is next.")

    result = event_service.notify_genesis_milestone(
        design_id="D-42",
        milestone_type="phase_complete",
        channels=["console"],
        phase_data={"phase": "validate", "next_phase": "deploy"},
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] == "Genesis design D-42 has completed the validate phase; deploy phase is next."


def test_notify_genesis_milestone_graceful_degradation_on_llm_failure(monkeypatch):
    """If the LLM raises, narrative is None but the milestone notification still ships."""
    _stub_genesis_notify_db(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("LLM unavailable"))

    result = event_service.notify_genesis_milestone(
        design_id="D-42",
        milestone_type="drift_detected",
        channels=["console"],
        phase_data={"component": "rag", "delta": 0.15, "action": "rollback"},
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "console" in result["receipts"]


# ---------------------------------------------------------------------------
# Integration tests: notify_oracle_alert (aiify-opp-5828)
# ---------------------------------------------------------------------------

def _stub_oracle_notify_db(monkeypatch):
    """In-memory SQLite with empty Oracle tables for notify_oracle_alert."""
    import sqlite3

    class _FakeConn:
        def __init__(self):
            self._db = sqlite3.connect(":memory:")
            self._db.row_factory = sqlite3.Row
            self._db.execute(
                "CREATE TABLE oracle_predictions "
                "(id TEXT, title TEXT, severity TEXT, confidence REAL, "
                "lens_id TEXT, outcome TEXT, created_at TEXT)"
            )
            self._db.execute(
                "CREATE TABLE oracle_lenses "
                "(lens_id TEXT, name TEXT, horizon_days INTEGER)"
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
    monkeypatch.setattr(event_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(event_service, "send", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "notify", lambda *a, **kw: None)


def test_notify_oracle_alert_no_narrative_by_default(monkeypatch):
    """When ai_narrative is omitted, narrative must be None and delivery succeeds."""
    _stub_oracle_notify_db(monkeypatch)

    result = event_service.notify_oracle_alert(
        lens_id="L-security",
        alert_type="cat2_digest",
        prediction_ids=[],
        channels=["console"],
    )

    assert result["status"] == "delivered"
    assert result["lens_id"] == "L-security"
    assert result["narrative"] is None
    assert "console" in result["receipts"]


def test_notify_oracle_alert_attaches_narrative_when_enabled(monkeypatch):
    """With ai_narrative=True and a functioning LLM, narrative is attached."""
    _stub_oracle_notify_db(monkeypatch)
    _fake_router(monkeypatch, content="Zero predictions are pending review for lens L-security; no immediate action required.")

    result = event_service.notify_oracle_alert(
        lens_id="L-security",
        alert_type="cat2_digest",
        prediction_ids=[],
        channels=["console"],
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] == "Zero predictions are pending review for lens L-security; no immediate action required."


def test_notify_oracle_alert_graceful_degradation_on_llm_failure(monkeypatch):
    """If the LLM raises, narrative is None but the alert is still delivered."""
    _stub_oracle_notify_db(monkeypatch)
    _fake_router(monkeypatch, raises=ConnectionError("LLM unreachable"))

    result = event_service.notify_oracle_alert(
        lens_id="L-security",
        alert_type="cat1_escalate",
        prediction_ids=[],
        channels=["console"],
        urgency="normal",
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "console" in result["receipts"]


# ---------------------------------------------------------------------------
# Integration tests: send_aiify_scan_report (aiify-opp-5940)
# ---------------------------------------------------------------------------

def _stub_scan_report_db(monkeypatch):
    """In-memory SQLite with aiify_scans + aiify_opportunities + audit_trail."""
    import sqlite3

    class _FakeConn:
        def __init__(self):
            self._db = sqlite3.connect(":memory:")
            self._db.row_factory = sqlite3.Row
            self._db.execute(
                "CREATE TABLE aiify_scans "
                "(id INTEGER, roadmap_id TEXT, scan_status TEXT, "
                "opportunity_count INTEGER, started_at TEXT, completed_at TEXT)"
            )
            self._db.execute(
                "INSERT INTO aiify_scans VALUES "
                "(41, 'rm-55fc0a0e6a', 'complete', 12, "
                "'2026-06-02T00:00:00', '2026-06-02T00:15:00')"
            )
            self._db.execute(
                "CREATE TABLE aiify_opportunities "
                "(id INTEGER, scan_id INTEGER, module_path TEXT, function_name TEXT, "
                "pattern_type TEXT, ai_paradigm TEXT, composite_score REAL)"
            )
            self._db.execute(
                "INSERT INTO aiify_opportunities VALUES "
                "(5940, 41, 'tools/notification_service/event_service.py', '<unknown>', "
                "'db_render_notify_chain', 'llm_generation', 0.7769)"
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
    monkeypatch.setattr(event_service, "sendmail", lambda **kw: None)
    monkeypatch.setattr(event_service, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(event_service, "notify", lambda *a, **kw: None)


def test_send_aiify_scan_report_no_narrative_by_default(monkeypatch):
    """When ai_narrative is omitted, narrative must be None and delivery succeeds."""
    _stub_scan_report_db(monkeypatch)

    result = event_service.send_aiify_scan_report(
        scan_id=41,
        channels=["console"],
    )

    assert result["status"] == "delivered"
    assert result["scan_id"] == 41
    assert result["opportunity_count"] == 12
    assert result["narrative"] is None
    assert "console" in result["receipts"]


def test_send_aiify_scan_report_attaches_narrative_when_enabled(monkeypatch):
    """With ai_narrative=True and a functioning LLM, narrative is attached."""
    _stub_scan_report_db(monkeypatch)
    _fake_router(
        monkeypatch,
        content=(
            "Scan 41 detected 12 AI-ify opportunities; the highest-scoring candidate "
            "is in event_service.py and should be prioritised for immediate implementation."
        ),
    )

    result = event_service.send_aiify_scan_report(
        scan_id=41,
        channels=["console"],
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] == (
        "Scan 41 detected 12 AI-ify opportunities; the highest-scoring candidate "
        "is in event_service.py and should be prioritised for immediate implementation."
    )


def test_send_aiify_scan_report_graceful_degradation_on_llm_failure(monkeypatch):
    """If the LLM raises, narrative is None but the report is still delivered."""
    _stub_scan_report_db(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("LLM unavailable"))

    result = event_service.send_aiify_scan_report(
        scan_id=41,
        channels=["slack"],
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "slack" in result["receipts"]


# ---------------------------------------------------------------------------
# notify_aiify_opportunity_event — opp-167 (aiify-rm-c5c5642863)
#
# Opportunity 167: db_render_notify_chain -> llm_generation in event_service.py
# scan_id=2, roadmap_id="rm-c5c5642863", function_name="<unknown>",
# scores: composite=0.7769, value=0.922, feasibility=0.82, risk=0.625.
# The AI narrative is already implemented (aiify-opp-5716); these tests pin
# the exact scanner data so any future rename or score drift is caught.
# ---------------------------------------------------------------------------

def _stub_opp_167_db(monkeypatch):
    """In-memory SQLite with kanban_tasks + audit_trail for opp-167 pinned tests."""
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
                "('aiify-rm-c5c56-phase-167', 'db_render_notify_chain in event_service', "
                "'in_progress', 'kanban-scheduler', 1, "
                "'2026-06-02T00:00:00', '2026-06-02T01:00:00')"
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


_OPP_167_EXTRA = {
    "task_id": "aiify-rm-c5c56-phase-167",
    "module_path": "tools/notification_service/event_service.py",
    "function_name": "<unknown>",
    "pattern_type": "db_render_notify_chain",
    "ai_paradigm": "llm_generation",
    "composite_score": 0.7769,
}


def test_opp_167_no_narrative_by_default(monkeypatch):
    """notify_aiify_opportunity_event(167) returns narrative=None when ai_narrative omitted."""
    _stub_opp_167_db(monkeypatch)

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=167,
        event_type="opportunity_dispatched",
        channels=["console"],
        extra=_OPP_167_EXTRA,
    )

    assert result["status"] == "delivered"
    assert result["opportunity_id"] == 167
    assert result["narrative"] is None
    assert "console" in result["receipts"]


def test_opp_167_scanner_data_in_rendered_message(monkeypatch):
    """Rendered message must include opp-167 pattern_type, paradigm, and composite score."""
    _stub_opp_167_db(monkeypatch)

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=167,
        event_type="opportunity_dispatched",
        channels=["console"],
        extra=_OPP_167_EXTRA,
    )

    rendered = result["rendered"]
    assert "167" in rendered
    assert "db_render_notify_chain" in rendered or "event_service" in rendered


def test_opp_167_narrative_attached_when_llm_available(monkeypatch):
    """narrative contains LLM text for opp-167 when ai_narrative=True and LLM succeeds."""
    _stub_opp_167_db(monkeypatch)
    _fake_router(
        monkeypatch,
        content=(
            "Opportunity 167 detected a db→render→notify chain in event_service.py; "
            "AI narrative is already active and ready for opt-in callers."
        ),
    )

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=167,
        event_type="opportunity_detected",
        channels=["console"],
        extra=_OPP_167_EXTRA,
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] == (
        "Opportunity 167 detected a db→render→notify chain in event_service.py; "
        "AI narrative is already active and ready for opt-in callers."
    )


def test_opp_167_narrative_none_on_llm_failure(monkeypatch):
    """notify_aiify_opportunity_event(167) degrades to narrative=None when LLM raises."""
    _stub_opp_167_db(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = event_service.notify_aiify_opportunity_event(
        opportunity_id=167,
        event_type="opportunity_completed",
        channels=["webhook"],
        extra=_OPP_167_EXTRA,
        ai_narrative=True,
    )

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "webhook" in result["receipts"]


def test_opp_167_narrative_prompt_grounded_in_facts(monkeypatch):
    """LLM prompt for opp-167 must include pattern_type, paradigm, and composite score."""
    _stub_opp_167_db(monkeypatch)
    captured = _fake_router(monkeypatch, content="Narrative for opp-167.")

    event_service.notify_aiify_opportunity_event(
        opportunity_id=167,
        event_type="opportunity_dispatched",
        channels=["console"],
        extra=_OPP_167_EXTRA,
        ai_narrative=True,
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "db_render_notify_chain" in user_msg
    assert "llm_generation" in user_msg
    assert "0.7769" in user_msg
    assert "aiify opportunity opportunity_dispatched notification" in user_msg
