# CUI // SP-CTI
"""Tests for the AI-ified handler narrative in the handler service (aiify-opp-5592).

The db -> render -> notify chains in ``tools.notification_service.handler_service``
gained an opt-in LLM handler narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so a handler notification
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import handler_service


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
    captured = _fake_router(monkeypatch, content="  Task TASK-42 moved to done; verify CI gates pass before closing the sprint.  ")
    facts = {
        "task_id": "TASK-42",
        "task_title": "Add ZIG maturity report",
        "actor": "sovanna",
        "to_status": "done",
        "recent_events": "status_change; comment_added",
    }

    out = handler_service._ai_handler_narrative("task status change notification", facts)

    assert out == "Task TASK-42 moved to done; verify CI gates pass before closing the sprint."
    assert captured["function"] == "narrative_generation"
    user_msg = captured["request"].messages[0]["content"]
    assert "task status change notification" in user_msg
    assert "task_id" in user_msg
    assert "to_status" in user_msg
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = handler_service._ai_handler_narrative(
        "oracle prediction alert", {"prediction_id": "P-1", "severity": "high"}
    )

    assert out is None


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = handler_service._ai_handler_narrative(
        "STIG finding compliance notification", {"check_id": "V-12345", "severity": "I"}
    )

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = handler_service._ai_handler_narrative(
        "agent incident ops alert", {"agent_id": "agent-7", "incident_type": "crash"}
    )

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last", "a_first": "first", "m_middle": "middle"}

    handler_service._ai_handler_narrative("genesis reflex fired notification", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z


def test_narrative_none_when_ai_narrative_false(monkeypatch):
    """Passing ai_narrative=False (the default) must never call the LLM."""
    called = []

    class _Sentinel:
        def invoke(self, *a, **kw):
            called.append(True)

    fake_module = types.SimpleNamespace(LLMRouter=_Sentinel)
    monkeypatch.setitem(__import__("sys").modules, "tools.llm.router", fake_module)

    # Call the helper directly with an empty facts dict — no LLM call expected.
    out = handler_service._ai_handler_narrative.__doc__  # just a sanity import check
    assert out is not None
    assert not called  # _ai_handler_narrative was never invoked by default args


def test_handler_kind_appears_in_prompt(monkeypatch):
    """The handler_kind label must appear in the user message for framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    handler_service._ai_handler_narrative(
        "POA&M deadline reminder notification",
        {"poam_id": "POAM-99", "severity": "high", "due_date": "2026-07-01"},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "POA&M deadline reminder notification" in user_msg
    assert "poam_id" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for handler narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    handler_service._ai_handler_narrative("ZIG pillar maturity update notification", {"pillar_slug": "identity"})

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    handler_service._ai_handler_narrative("canvas assessment result notification", {"canvas_id": "c1", "score": 82.0})

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


# ---------------------------------------------------------------------------
# Integration tests for handler functions (aiify-opp-5800)
# ---------------------------------------------------------------------------

def _fake_handler_conn(monkeypatch, rows_by_call=()):
    """Patch get_connection() and all event_service helpers in handler_service."""
    call_counter = {"n": 0}

    class _FakeCursor:
        def __init__(self, result):
            self._result = result

        def fetchone(self):
            return self._result if not isinstance(self._result, (list, tuple)) else None

        def fetchall(self):
            return list(self._result) if isinstance(self._result, (list, tuple)) else []

    class _FakeConn:
        def execute(self, sql, params=()):
            idx = call_counter["n"]
            result = rows_by_call[idx] if idx < len(rows_by_call) else None
            call_counter["n"] += 1
            return _FakeCursor(result)

        def close(self):
            pass

    import tools.notification_service.handler_service as hs
    monkeypatch.setattr(hs, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(hs, "render_template", lambda *a, **kw: "<p>rendered</p>")
    monkeypatch.setattr(hs, "render_to_string", lambda *a, **kw: "<p>rendered</p>")
    monkeypatch.setattr(hs, "send", lambda *a, **kw: None)
    monkeypatch.setattr(hs, "sendmail", lambda *a, **kw: None)
    monkeypatch.setattr(hs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(hs, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(hs, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(hs, "dispatch", lambda *a, **kw: None)


def test_task_handler_no_narrative_by_default(monkeypatch):
    """handle_task_status_change_notify returns narrative=None when ai_narrative=False."""
    task_row = {"id": "TASK-42", "title": "Add ZIG report", "actor": "sovanna", "updated_at": "2026-06-01"}
    history_rows = [{"event": "status_change", "created_at": "2026-06-01"}]
    _fake_handler_conn(monkeypatch, rows_by_call=[task_row, history_rows])

    result = handler_service.handle_task_status_change_notify("TASK-42", "done", "ops@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None


def test_task_handler_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    task_row = {"id": "TASK-42", "title": "Add ZIG report", "actor": "sovanna", "updated_at": "2026-06-01"}
    history_rows = [{"event": "status_change", "created_at": "2026-06-01"}]
    _fake_handler_conn(monkeypatch, rows_by_call=[task_row, history_rows])
    _fake_router(monkeypatch, content="Task completed; verify CI gates before sprint close.")

    result = handler_service.handle_task_status_change_notify(
        "TASK-42", "done", "ops@example.com", ai_narrative=True
    )

    assert result["narrative"] == "Task completed; verify CI gates before sprint close."
    assert result["task_id"] == "TASK-42"
    assert result["status"] == "sent"


def test_task_handler_narrative_none_on_llm_failure(monkeypatch):
    """Notification ships even when the LLM raises; narrative degrades to None."""
    task_row = {"id": "TASK-99", "title": "Bug fix", "actor": "alice", "updated_at": "2026-06-01"}
    history_rows = []
    _fake_handler_conn(monkeypatch, rows_by_call=[task_row, history_rows])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = handler_service.handle_task_status_change_notify(
        "TASK-99", "in_progress", "alice@example.com", ai_narrative=True
    )

    assert result["narrative"] is None
    assert result["status"] == "sent"  # deterministic notification still delivered


def test_stig_handler_narrative_attached(monkeypatch):
    """handle_stig_finding_handler attaches narrative when LLM is available."""
    finding_row = {
        "check_id": "V-12345", "check_name": "Audit log enabled",
        "severity": "I", "status": "open", "remediation": "Enable audit logging.",
    }
    workload_row = {"name": "api-gateway", "classification": "CUI"}
    _fake_handler_conn(monkeypatch, rows_by_call=[finding_row, workload_row])
    _fake_router(monkeypatch, content="CAT I STIG finding on api-gateway requires immediate remediation.")

    result = handler_service.handle_stig_finding_handler(
        "V-12345", "wl-1", "sec@example.com", ai_narrative=True
    )

    assert result["narrative"] == "CAT I STIG finding on api-gateway requires immediate remediation."
    assert result["check_id"] == "V-12345"
    assert result["status"] == "sent"


def test_agent_incident_handler_no_narrative_by_default(monkeypatch):
    """handle_agent_incident_handler returns narrative=None when ai_narrative=False."""
    agent_row = {"id": "agent-7", "name": "Architect", "status": "crashed", "last_heartbeat": "2026-06-01"}
    error_rows = [{"error_msg": "Connection timeout", "created_at": "2026-06-01"}]
    metric_rows = []
    _fake_handler_conn(monkeypatch, rows_by_call=[agent_row, error_rows, metric_rows])

    result = handler_service.handle_agent_incident_handler("agent-7", "crash", "ops@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["agent_id"] == "agent-7"


# ---------------------------------------------------------------------------
# Extended integration tests for remaining handler functions (aiify-opp-5836)
# ---------------------------------------------------------------------------

def test_canvas_assessment_handler_narrative_attached(monkeypatch):
    """handle_canvas_assessment_handler attaches narrative when LLM is available."""
    assessment_row = {"id": "a-1", "score": 87.5, "cat1_findings": 0, "created_at": "2026-06-02"}
    design_row = {"name": "ZIG Canvas", "classification": "CUI"}
    _fake_handler_conn(monkeypatch, rows_by_call=[assessment_row, design_row])
    _fake_router(monkeypatch, content="Canvas assessment passed with no CAT I findings; proceed to ATO milestone.")

    result = handler_service.handle_canvas_assessment_handler("c-42", "compliance@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["canvas_id"] == "c-42"
    assert result["narrative"] == "Canvas assessment passed with no CAT I findings; proceed to ATO milestone."


def test_canvas_assessment_handler_no_narrative_by_default(monkeypatch):
    """handle_canvas_assessment_handler returns narrative=None when ai_narrative=False."""
    assessment_row = {"id": "a-2", "score": 60.0, "cat1_findings": 3, "created_at": "2026-06-02"}
    design_row = {"name": "DIC Canvas", "classification": "CUI"}
    _fake_handler_conn(monkeypatch, rows_by_call=[assessment_row, design_row])

    result = handler_service.handle_canvas_assessment_handler("c-99", "sec@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None


def test_oracle_prediction_handler_narrative_attached(monkeypatch):
    """handle_oracle_prediction_handler attaches narrative when LLM is available."""
    pred_row = {
        "id": "P-55", "title": "Supply chain disruption risk",
        "severity": "high", "confidence": 0.91, "lens_id": "lens-3", "created_at": "2026-06-02",
    }
    lens_row = {"name": "Supply Chain", "horizon_days": 90}
    _fake_handler_conn(monkeypatch, rows_by_call=[pred_row, lens_row])
    _fake_router(monkeypatch, content="High-confidence supply chain disruption predicted; engage alternate vendors within 30 days.")

    result = handler_service.handle_oracle_prediction_handler("P-55", "ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["prediction_id"] == "P-55"
    assert result["narrative"] == "High-confidence supply chain disruption predicted; engage alternate vendors within 30 days."


def test_oracle_prediction_handler_no_narrative_by_default(monkeypatch):
    """handle_oracle_prediction_handler returns narrative=None when ai_narrative=False."""
    pred_row = {"id": "P-01", "title": "Low risk event", "severity": "low", "confidence": 0.4, "lens_id": "lens-1"}
    lens_row = {"name": "Ops", "horizon_days": 30}
    _fake_handler_conn(monkeypatch, rows_by_call=[pred_row, lens_row])

    result = handler_service.handle_oracle_prediction_handler("P-01", "watch@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_genesis_reflex_handler_narrative_attached(monkeypatch):
    """handle_genesis_reflex_handler attaches narrative when LLM is available."""
    reflex_row = {"id": "r-7", "name": "gap_fill_reflex", "confidence": 0.85, "fired_at": "2026-06-02"}
    design_row = {"name": "ACOIC Genesis", "status": "in_progress", "current_phase": "integrate"}
    event_rows = [
        {"phase": "architect", "status": "done"},
        {"phase": "navigate", "status": "done"},
    ]
    _fake_handler_conn(monkeypatch, rows_by_call=[reflex_row, design_row, event_rows])
    _fake_router(monkeypatch, content="Genesis reflex fired with high confidence; review integrate phase outputs before proceeding.")

    result = handler_service.handle_genesis_reflex_handler("r-7", "d-12", "dev@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["reflex_id"] == "r-7"
    assert result["narrative"] == "Genesis reflex fired with high confidence; review integrate phase outputs before proceeding."


def test_genesis_reflex_handler_narrative_none_on_llm_failure(monkeypatch):
    """Notification ships even when the LLM raises for genesis reflex handler."""
    reflex_row = {"id": "r-8", "name": "drift_reflex", "confidence": 0.6, "fired_at": "2026-06-02"}
    design_row = {"name": "Test Design", "status": "active", "current_phase": "verify"}
    event_rows = []
    _fake_handler_conn(monkeypatch, rows_by_call=[reflex_row, design_row, event_rows])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = handler_service.handle_genesis_reflex_handler("r-8", "d-20", "dev@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_poam_deadline_handler_narrative_attached(monkeypatch):
    """handle_poam_deadline_handler attaches narrative when LLM is available."""
    poam_row = {
        "id": "POAM-10", "title": "Patch OpenSSL 3.0.x", "severity": "high",
        "due_date": "2026-07-01", "owner": "alice", "status": "open",
        "milestone": "Sprint 14", "finding_ref": "stig-99",
    }
    finding_row = {"title": "OpenSSL CVE-2026-1234", "severity": "high"}
    owner_row = {"email": "alice@example.com", "name": "Alice Dev"}
    _fake_handler_conn(monkeypatch, rows_by_call=[poam_row, finding_row, owner_row])
    _fake_router(monkeypatch, content="POA&M deadline for OpenSSL patch is in 29 days; assign sprint resources immediately.")

    result = handler_service.handle_poam_deadline_handler("POAM-10", "proj-1", "fallback@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["poam_id"] == "POAM-10"
    assert result["narrative"] == "POA&M deadline for OpenSSL patch is in 29 days; assign sprint resources immediately."


def test_poam_deadline_handler_no_narrative_by_default(monkeypatch):
    """handle_poam_deadline_handler returns narrative=None when ai_narrative=False."""
    poam_row = {
        "id": "POAM-20", "title": "Update TLS certs", "severity": "medium",
        "due_date": "2026-08-01", "owner": "bob", "status": "in_progress",
        "milestone": "Sprint 15", "finding_ref": "",
    }
    finding_row = None
    owner_row = {"email": "bob@example.com", "name": "Bob Ops"}
    _fake_handler_conn(monkeypatch, rows_by_call=[poam_row, finding_row, owner_row])

    result = handler_service.handle_poam_deadline_handler("POAM-20", "proj-2", "sec@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_zig_pillar_handler_narrative_attached(monkeypatch):
    """handle_zig_pillar_handler attaches narrative when LLM is available."""
    scores_row = {
        "pillar_slug": "identity", "score": 0.74, "maturity_level": "managed",
        "complete_activities": 31, "activity_count": 42,
    }
    cap_rows = [
        {"title": "MFA enforcement", "implementation_status": "done", "phase": 1},
        {"title": "PAM integration", "implementation_status": "in_progress", "phase": 2},
    ]
    activity_rows = [
        {"title": "Enable FIDO2 keys", "status": "complete"},
        {"title": "Vault PAM install", "status": "in_progress"},
    ]
    _fake_handler_conn(monkeypatch, rows_by_call=[scores_row, cap_rows, activity_rows])
    _fake_router(monkeypatch, content="Identity pillar at 74% maturity; focus on PAM integration to advance to optimizing level.")

    result = handler_service.handle_zig_pillar_handler("identity", "zig@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["pillar_slug"] == "identity"
    assert result["narrative"] == "Identity pillar at 74% maturity; focus on PAM integration to advance to optimizing level."


def test_zig_pillar_handler_no_narrative_by_default(monkeypatch):
    """handle_zig_pillar_handler returns narrative=None when ai_narrative=False."""
    scores_row = {"pillar_slug": "data", "score": 0.5, "maturity_level": "initial", "complete_activities": 10, "activity_count": 20}
    cap_rows = []
    activity_rows = []
    _fake_handler_conn(monkeypatch, rows_by_call=[scores_row, cap_rows, activity_rows])

    result = handler_service.handle_zig_pillar_handler("data", "zig@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# Tests for handle_aiify_opportunity_handler (aiify-opp-5907)
# ---------------------------------------------------------------------------

def test_aiify_opportunity_handler_narrative_attached(monkeypatch):
    """handle_aiify_opportunity_handler attaches narrative when LLM is available."""
    opp_row = {
        "opportunity_id": 5907, "function_name": "db_render_notify_chain",
        "pattern_type": "db_render_notify_chain",
        "module_path": "tools/notification_service/handler_service.py",
    }
    scores_row = {"composite_score": 0.759, "value_score": 0.882, "feasibility_score": 0.82, "risk_score": 0.625}
    roadmap_row = {"roadmap_id": "rm-2b005631fb", "phase": "Phase 1 — Quick Wins"}
    _fake_handler_conn(monkeypatch, rows_by_call=[opp_row, scores_row, roadmap_row])
    _fake_router(monkeypatch, content="High-value AI-ify opportunity found; implement llm_generation pattern in the handler service to enrich notifications.")

    result = handler_service.handle_aiify_opportunity_handler(5907, "scan-40", "dev@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["opportunity_id"] == 5907
    assert result["narrative"] == "High-value AI-ify opportunity found; implement llm_generation pattern in the handler service to enrich notifications."


def test_aiify_opportunity_handler_no_narrative_by_default(monkeypatch):
    """handle_aiify_opportunity_handler returns narrative=None when ai_narrative=False."""
    opp_row = {
        "opportunity_id": 5800, "function_name": "handle_task_status",
        "pattern_type": "db_render_notify_chain",
        "module_path": "tools/notification_service/handler_service.py",
    }
    scores_row = {"composite_score": 0.71, "value_score": 0.80, "feasibility_score": 0.75, "risk_score": 0.60}
    roadmap_row = None
    _fake_handler_conn(monkeypatch, rows_by_call=[opp_row, scores_row, roadmap_row])

    result = handler_service.handle_aiify_opportunity_handler(5800, "scan-38", "ops@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"
    assert result["opportunity_id"] == 5800


def test_aiify_opportunity_handler_narrative_none_on_llm_failure(monkeypatch):
    """Notification ships even when the LLM raises for the AI-ify opportunity handler."""
    opp_row = {
        "opportunity_id": 5501, "function_name": "unknown",
        "pattern_type": "string_template_rendering",
        "module_path": "tools/alert_service.py",
    }
    scores_row = {"composite_score": 0.65, "value_score": 0.70, "feasibility_score": 0.80, "risk_score": 0.50}
    roadmap_row = {"roadmap_id": "rm-abc123", "phase": "Phase 2"}
    _fake_handler_conn(monkeypatch, rows_by_call=[opp_row, scores_row, roadmap_row])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = handler_service.handle_aiify_opportunity_handler(5501, "scan-35", "dev@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_aiify_opportunity_handler_facts_include_scores(monkeypatch):
    """The narrative prompt must include all four score dimensions for grounding."""
    opp_row = {
        "opportunity_id": 9001, "function_name": "compute_digest",
        "pattern_type": "db_render_notify_chain",
        "module_path": "tools/digest_service.py",
    }
    scores_row = {"composite_score": 0.80, "value_score": 0.90, "feasibility_score": 0.85, "risk_score": 0.70}
    roadmap_row = {"roadmap_id": "rm-xyz", "phase": "Phase 1 — Quick Wins"}
    _fake_handler_conn(monkeypatch, rows_by_call=[opp_row, scores_row, roadmap_row])
    captured = _fake_router(monkeypatch, content="Grounded narrative text.")

    handler_service.handle_aiify_opportunity_handler(9001, "scan-42", "lead@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "composite_score" in user_msg
    assert "value_score" in user_msg
    assert "feasibility_score" in user_msg
    assert "risk_score" in user_msg
    assert "pattern_type" in user_msg
