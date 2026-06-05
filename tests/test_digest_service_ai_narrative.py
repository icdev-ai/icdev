# CUI // SP-CTI
"""Tests for the AI-ified digest narrative in the digest service (aiify-opp-5550).

The db -> render -> notify chains in ``tools.notification_service.digest_service``
gained an opt-in LLM digest narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so a scheduled digest
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import digest_service


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
    captured = _fake_router(monkeypatch, content="  Three tasks completed today; prioritize the two overdue items.  ")
    facts = {
        "date": "2026-06-01",
        "status_breakdown": "done: 3; in_progress: 2; backlog: 10",
        "status_group_count": 3,
        "recently_completed": "Add ZIG maturity report; Fix RLS subquery bug",
    }

    out = digest_service._ai_digest_narrative("daily kanban digest", facts)

    assert out == "Three tasks completed today; prioritize the two overdue items."  # stripped
    assert captured["function"] == "narrative_generation"
    # Facts are grounded into the user prompt, sorted for cache stability.
    user_msg = captured["request"].messages[0]["content"]
    assert "daily kanban digest" in user_msg
    assert "status_breakdown" in user_msg
    assert "recently_completed" in user_msg
    # Trusted first-party facts skip the injection scan.
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = digest_service._ai_digest_narrative("oracle weekly prediction digest", {"prediction_groups": 4})

    assert out is None  # graceful degradation, never raises


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = digest_service._ai_digest_narrative("compliance posture report", {"overall_score": 85.0})

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    # Simulate an environment where the LLM stack is absent entirely.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = digest_service._ai_digest_narrative("ZIG zero-trust maturity report", {"overall_pct": 72.5})

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last", "a_first": "first", "m_middle": "middle"}

    digest_service._ai_digest_narrative("agent health alert", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z


def test_digest_kind_appears_in_prompt(monkeypatch):
    """The digest_kind label must appear in the user message for prompt framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    digest_service._ai_digest_narrative(
        "ZIG zero-trust maturity report",
        {"overall_pct": 72.5, "pillar_count": 7},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "ZIG zero-trust maturity report" in user_msg
    assert "overall_pct" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for digest narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    digest_service._ai_digest_narrative("audit trail activity summary", {"total_events": 42})

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    digest_service._ai_digest_narrative("POA&M due-soon digest", {"due_count": 5})

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


# ---------------------------------------------------------------------------
# Integration tests for digest send_* pipeline functions (aiify-opp-5895)
# ---------------------------------------------------------------------------

def _fake_digest_conn(monkeypatch, rows_by_call=()):
    """Patch get_connection() and all event_service helpers in digest_service."""
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
            result = rows_by_call[idx] if idx < len(rows_by_call) else []
            call_counter["n"] += 1
            return _FakeCursor(result)

        def close(self):
            pass

    import tools.notification_service.digest_service as ds
    monkeypatch.setattr(ds, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(ds, "render_template", lambda *a, **kw: "<p>rendered</p>")
    monkeypatch.setattr(ds, "render_to_string", lambda *a, **kw: "<p>rendered</p>")
    monkeypatch.setattr(ds, "send", lambda *a, **kw: None)
    monkeypatch.setattr(ds, "sendmail", lambda *a, **kw: None)
    monkeypatch.setattr(ds, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(ds, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(ds, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(ds, "dispatch", lambda *a, **kw: None)


def test_kanban_digest_no_narrative_by_default(monkeypatch):
    """send_daily_kanban_digest returns narrative=None when ai_narrative=False."""
    rows = [{"status": "done", "cnt": 3}, {"status": "in_progress", "cnt": 2}]
    done = [{"id": "t-1", "title": "Add ZIG report"}]
    _fake_digest_conn(monkeypatch, rows_by_call=[rows, done])

    result = digest_service.send_daily_kanban_digest("ops@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["task_count"] == 2


def test_kanban_digest_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    rows = [{"status": "done", "cnt": 5}, {"status": "backlog", "cnt": 10}]
    done = [
        {"id": "t-1", "title": "Add ZIG report"},
        {"id": "t-2", "title": "Fix RLS subquery bug"},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[rows, done])
    _fake_router(monkeypatch, content="Five tasks completed today; backlog of 10 items warrants sprint planning review.")

    result = digest_service.send_daily_kanban_digest("ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["narrative"] == "Five tasks completed today; backlog of 10 items warrants sprint planning review."
    assert result["task_count"] == 2


def test_kanban_digest_narrative_none_on_llm_failure(monkeypatch):
    """Digest ships even when LLM raises; narrative degrades to None."""
    rows = [{"status": "done", "cnt": 1}]
    done = []
    _fake_digest_conn(monkeypatch, rows_by_call=[rows, done])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = digest_service.send_daily_kanban_digest("ops@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_aiify_weekly_digest_narrative_attached(monkeypatch):
    """send_aiify_weekly_digest attaches narrative when LLM is available."""
    scans = [
        {"scan_id": 40, "status": "complete", "overall_ai_readiness": 0.76, "created_at": "2026-06-02"},
        {"scan_id": 39, "status": "complete", "overall_ai_readiness": 0.72, "created_at": "2026-05-26"},
    ]
    top_opps = [
        {"function_name": "send_digest", "pattern_type": "db_render_notify_chain", "composite_score": 0.91},
        {"function_name": "handle_alert", "pattern_type": "event_driven_ml", "composite_score": 0.85},
    ]
    pattern_summary = [
        {"pattern_type": "db_render_notify_chain", "cnt": 12},
        {"pattern_type": "event_driven_ml", "cnt": 8},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[scans, top_opps, pattern_summary])
    _fake_router(monkeypatch, content="Two scans averaged 74% AI readiness; prioritize db_render_notify_chain opportunities for quick wins.")

    result = digest_service.send_aiify_weekly_digest("ai@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["scan_count"] == 2
    assert result["narrative"] == "Two scans averaged 74% AI readiness; prioritize db_render_notify_chain opportunities for quick wins."


def test_aiify_weekly_digest_no_narrative_by_default(monkeypatch):
    """send_aiify_weekly_digest returns narrative=None when ai_narrative=False."""
    scans = [{"scan_id": 40, "status": "complete", "overall_ai_readiness": 0.8, "created_at": "2026-06-02"}]
    top_opps = []
    pattern_summary = [{"pattern_type": "db_render_notify_chain", "cnt": 5}]
    _fake_digest_conn(monkeypatch, rows_by_call=[scans, top_opps, pattern_summary])

    result = digest_service.send_aiify_weekly_digest("ai@example.com")

    assert result["narrative"] is None
    assert result["scan_count"] == 1


def test_audit_trail_summary_narrative_attached(monkeypatch):
    """send_audit_trail_summary attaches narrative when LLM is available."""
    events = [
        {"resource_type": "kanban_task", "event": "status_change", "actor": "sovanna", "cnt": 14},
        {"resource_type": "stig_finding", "event": "create", "actor": "scanner", "cnt": 6},
    ]
    actors = [
        {"actor": "sovanna", "cnt": 20},
        {"actor": "scanner", "cnt": 6},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[events, actors])
    _fake_router(monkeypatch, content="20 events in the last 24 hours led by sovanna; review STIG finding creation spike.")

    result = digest_service.send_audit_trail_summary("audit@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["event_count"] == 20
    assert result["narrative"] == "20 events in the last 24 hours led by sovanna; review STIG finding creation spike."


def test_audit_trail_summary_no_narrative_by_default(monkeypatch):
    """send_audit_trail_summary returns narrative=None when ai_narrative=False."""
    events = [{"resource_type": "kanban_task", "event": "create", "actor": "alice", "cnt": 3}]
    actors = [{"actor": "alice", "cnt": 3}]
    _fake_digest_conn(monkeypatch, rows_by_call=[events, actors])

    result = digest_service.send_audit_trail_summary("audit@example.com")

    assert result["narrative"] is None
    assert result["event_count"] == 3


def test_audit_trail_summary_narrative_none_on_llm_failure(monkeypatch):
    """Audit summary ships even when the LLM raises; narrative degrades to None."""
    events = [{"resource_type": "agent_error", "event": "create", "actor": "monitor", "cnt": 7}]
    actors = [{"actor": "monitor", "cnt": 7}]
    _fake_digest_conn(monkeypatch, rows_by_call=[events, actors])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = digest_service.send_audit_trail_summary("audit@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# Oracle weekly digest (aiify-opp-5932)
# ---------------------------------------------------------------------------

def test_oracle_weekly_digest_no_narrative_by_default(monkeypatch):
    """send_oracle_weekly_digest returns narrative=None when ai_narrative=False."""
    predictions = [
        {"severity": "high", "cnt": 3, "avg_conf": 0.85},
        {"severity": "medium", "cnt": 7, "avg_conf": 0.72},
    ]
    top_preds = [
        {"title": "Security breach imminent", "severity": "high", "confidence": 0.92},
        {"title": "Config drift detected", "severity": "medium", "confidence": 0.78},
    ]
    lens_stats = [{"lens_id": "risk", "cnt": 4}, {"lens_id": "compliance", "cnt": 6}]
    _fake_digest_conn(monkeypatch, rows_by_call=[predictions, top_preds, lens_stats])

    result = digest_service.send_oracle_weekly_digest("ops@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["prediction_groups"] == 2


def test_oracle_weekly_digest_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    predictions = [
        {"severity": "critical", "cnt": 1, "avg_conf": 0.95},
        {"severity": "high", "cnt": 4, "avg_conf": 0.88},
    ]
    top_preds = [
        {"title": "Privilege escalation path", "severity": "critical", "confidence": 0.95},
    ]
    lens_stats = [{"lens_id": "risk", "cnt": 5}]
    _fake_digest_conn(monkeypatch, rows_by_call=[predictions, top_preds, lens_stats])
    _fake_router(monkeypatch, content="One critical and four high-severity predictions this week; address the privilege escalation path immediately.")

    result = digest_service.send_oracle_weekly_digest("ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["prediction_groups"] == 2
    assert result["narrative"] == "One critical and four high-severity predictions this week; address the privilege escalation path immediately."


def test_oracle_weekly_digest_narrative_none_on_llm_failure(monkeypatch):
    """Oracle digest ships even when the LLM raises; narrative degrades to None."""
    predictions = [{"severity": "high", "cnt": 2, "avg_conf": 0.80}]
    top_preds = [{"title": "Alert X", "severity": "high", "confidence": 0.80}]
    lens_stats = [{"lens_id": "risk", "cnt": 2}]
    _fake_digest_conn(monkeypatch, rows_by_call=[predictions, top_preds, lens_stats])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = digest_service.send_oracle_weekly_digest("ops@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# Compliance posture report (aiify-opp-5932)
# ---------------------------------------------------------------------------

def test_compliance_posture_report_no_narrative_by_default(monkeypatch):
    """send_compliance_posture_report returns narrative=None when ai_narrative=False."""
    canvases = [
        {"canvas_name": "security", "score": 72.0, "latest": "2026-06-01"},
        {"canvas_name": "compliance", "score": 85.5, "latest": "2026-06-01"},
    ]
    overall = {"overall": 81.5}
    open_findings = [{"severity": "CAT1", "cnt": 2}, {"severity": "CAT2", "cnt": 5}]
    _fake_digest_conn(monkeypatch, rows_by_call=[canvases, overall, open_findings])

    result = digest_service.send_compliance_posture_report("compliance@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["overall_score"] == 81.5


def test_compliance_posture_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    canvases = [
        {"canvas_name": "security", "score": 65.0, "latest": "2026-06-01"},
        {"canvas_name": "audit", "score": 78.0, "latest": "2026-06-01"},
        {"canvas_name": "operations", "score": 91.0, "latest": "2026-06-01"},
    ]
    overall = {"overall": 78.0}
    open_findings = [{"severity": "CAT1", "cnt": 1}, {"severity": "CAT2", "cnt": 3}]
    _fake_digest_conn(monkeypatch, rows_by_call=[canvases, overall, open_findings])
    _fake_router(monkeypatch, content="Overall posture at 78%; security canvas is the weakest link — remediate the one open CAT1 finding first.")

    result = digest_service.send_compliance_posture_report("compliance@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["overall_score"] == 78.0
    assert result["narrative"] == "Overall posture at 78%; security canvas is the weakest link — remediate the one open CAT1 finding first."


def test_compliance_posture_report_narrative_none_on_llm_failure(monkeypatch):
    """Posture report ships even when the LLM raises; narrative degrades to None."""
    canvases = [{"canvas_name": "security", "score": 80.0, "latest": "2026-06-01"}]
    overall = {"overall": 80.0}
    open_findings = []
    _fake_digest_conn(monkeypatch, rows_by_call=[canvases, overall, open_findings])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = digest_service.send_compliance_posture_report("compliance@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# Agent health alert (aiify-opp-5932)
# ---------------------------------------------------------------------------

def test_agent_health_alert_no_narrative_by_default(monkeypatch):
    """send_agent_health_alert returns narrative=None when ai_narrative=False."""
    agents = [
        {"id": "a-1", "name": "Orchestrator", "status": "active", "last_heartbeat": "2026-06-02T10:00:00"},
        {"id": "a-2", "name": "Builder", "status": "inactive", "last_heartbeat": "2026-06-01T08:00:00"},
    ]
    inactive = {"cnt": 1}
    recent_errors = [{"agent_id": "a-2", "error_msg": "heartbeat timeout", "created_at": "2026-06-02T09:55:00"}]
    _fake_digest_conn(monkeypatch, rows_by_call=[agents, inactive, recent_errors])

    result = digest_service.send_agent_health_alert("ops@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["inactive_agents"] == 1


def test_agent_health_alert_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    agents = [
        {"id": "a-1", "name": "Orchestrator", "status": "active", "last_heartbeat": "2026-06-02T10:00:00"},
        {"id": "a-2", "name": "Builder", "status": "inactive", "last_heartbeat": "2026-06-01T08:00:00"},
        {"id": "a-3", "name": "Monitor", "status": "inactive", "last_heartbeat": "2026-06-01T07:00:00"},
    ]
    inactive = {"cnt": 2}
    recent_errors = [
        {"agent_id": "a-2", "error_msg": "OOM killed", "created_at": "2026-06-02T09:00:00"},
        {"agent_id": "a-3", "error_msg": "port conflict", "created_at": "2026-06-02T08:30:00"},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[agents, inactive, recent_errors])
    _fake_router(monkeypatch, content="Two agents are inactive with OOM and port conflict errors; restart Builder and Monitor immediately.")

    result = digest_service.send_agent_health_alert("ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["inactive_agents"] == 2
    assert result["narrative"] == "Two agents are inactive with OOM and port conflict errors; restart Builder and Monitor immediately."


# ---------------------------------------------------------------------------
# Genesis phase summary (aiify-opp-5932)
# ---------------------------------------------------------------------------

def test_genesis_phase_summary_no_narrative_by_default(monkeypatch):
    """send_genesis_phase_summary returns narrative=None when ai_narrative=False."""
    phases = [
        {"phase": "design", "status": "done", "started_at": "2026-05-30", "completed_at": "2026-05-31"},
        {"phase": "build", "status": "in_progress", "started_at": "2026-06-01", "completed_at": None},
    ]
    design = {"name": "Project Alpha", "status": "in_progress", "current_phase": "build"}
    reflexes = [{"name": "auto_review", "confidence": 0.88, "fired_at": "2026-06-02T09:00:00"}]
    _fake_digest_conn(monkeypatch, rows_by_call=[phases, design, reflexes])

    result = digest_service.send_genesis_phase_summary("design-abc123", "team@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["phases"] == 2


def test_genesis_phase_summary_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    phases = [
        {"phase": "design", "status": "done", "started_at": "2026-05-28", "completed_at": "2026-05-30"},
        {"phase": "build", "status": "done", "started_at": "2026-05-30", "completed_at": "2026-06-01"},
        {"phase": "review", "status": "in_progress", "started_at": "2026-06-01", "completed_at": None},
    ]
    design = {"name": "Modernization Engine", "status": "in_progress", "current_phase": "review"}
    reflexes = [
        {"name": "auto_fix", "confidence": 0.92, "fired_at": "2026-06-02T08:00:00"},
        {"name": "code_gen", "confidence": 0.85, "fired_at": "2026-06-01T15:00:00"},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[phases, design, reflexes])
    _fake_router(monkeypatch, content="Modernization Engine has completed design and build phases; review is underway with two high-confidence reflexes fired.")

    result = digest_service.send_genesis_phase_summary("design-abc123", "team@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["phases"] == 3
    assert result["narrative"] == "Modernization Engine has completed design and build phases; review is underway with two high-confidence reflexes fired."


def test_genesis_phase_summary_narrative_none_on_llm_failure(monkeypatch):
    """Genesis summary ships even when the LLM raises; narrative degrades to None."""
    phases = [{"phase": "design", "status": "done", "started_at": "2026-06-01", "completed_at": "2026-06-01"}]
    design = {"name": "Test Project", "status": "done", "current_phase": "design"}
    reflexes = []
    _fake_digest_conn(monkeypatch, rows_by_call=[phases, design, reflexes])
    _fake_router(monkeypatch, raises=RuntimeError("provider down"))

    result = digest_service.send_genesis_phase_summary("design-xyz", "team@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# ZIG maturity report (aiify-opp-5932)
# ---------------------------------------------------------------------------

def test_zig_maturity_report_no_narrative_by_default(monkeypatch):
    """send_zig_maturity_report returns narrative=None when ai_narrative=False."""
    pillars = [
        {"pillar_slug": "identity", "score": 0.72, "maturity_level": "managed", "complete_activities": 8, "activity_count": 10},
        {"pillar_slug": "devices", "score": 0.55, "maturity_level": "initial", "complete_activities": 4, "activity_count": 8},
        {"pillar_slug": "network", "score": 0.88, "maturity_level": "optimized", "complete_activities": 9, "activity_count": 10},
    ]
    gaps = [
        {"pillar_slug": "devices", "title": "Device attestation", "phase": "1"},
        {"pillar_slug": "devices", "title": "Patch compliance", "phase": "2"},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[pillars, gaps])

    result = digest_service.send_zig_maturity_report("security@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["pillars"] == 3


def test_zig_maturity_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    pillars = [
        {"pillar_slug": "identity", "score": 0.82, "maturity_level": "managed", "complete_activities": 9, "activity_count": 11},
        {"pillar_slug": "apps", "score": 0.61, "maturity_level": "initial", "complete_activities": 5, "activity_count": 9},
        {"pillar_slug": "data", "score": 0.77, "maturity_level": "defined", "complete_activities": 7, "activity_count": 10},
    ]
    gaps = [
        {"pillar_slug": "apps", "title": "SBOM integration", "phase": "1"},
        {"pillar_slug": "apps", "title": "Runtime scanning", "phase": "2"},
    ]
    _fake_digest_conn(monkeypatch, rows_by_call=[pillars, gaps])
    _fake_router(monkeypatch, content="ZIG maturity averages 73.3%; apps pillar is the laggard — prioritize SBOM integration in Phase 1.")

    result = digest_service.send_zig_maturity_report("security@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["pillars"] == 3
    assert result["narrative"] == "ZIG maturity averages 73.3%; apps pillar is the laggard — prioritize SBOM integration in Phase 1."


# ---------------------------------------------------------------------------
# POA&M due-soon digest (aiify-opp-5932)
# ---------------------------------------------------------------------------

def test_poam_due_soon_digest_no_narrative_by_default(monkeypatch):
    """send_poam_due_soon_digest returns narrative=None when ai_narrative=False."""
    due_items = [
        {"id": "poam-1", "title": "Patch CVE-2024-1234", "severity": "CAT1",
         "due_date": "2026-06-10", "owner": "alice", "status": "open", "days_left": 8},
        {"id": "poam-2", "title": "Rotate API keys", "severity": "CAT2",
         "due_date": "2026-06-15", "owner": "bob", "status": "open", "days_left": 13},
    ]
    overdue = {"cnt": 1}
    _fake_digest_conn(monkeypatch, rows_by_call=[due_items, overdue])

    result = digest_service.send_poam_due_soon_digest("poam@example.com")

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert result["due_count"] == 2
    assert result["overdue"] == 1


def test_poam_due_soon_digest_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    due_items = [
        {"id": "poam-1", "title": "Patch CVE-2024-5678", "severity": "CAT1",
         "due_date": "2026-06-05", "owner": "alice", "status": "open", "days_left": 3},
        {"id": "poam-2", "title": "Enable MFA everywhere", "severity": "CAT2",
         "due_date": "2026-06-20", "owner": "bob", "status": "open", "days_left": 18},
    ]
    overdue = {"cnt": 0}
    _fake_digest_conn(monkeypatch, rows_by_call=[due_items, overdue])
    _fake_router(monkeypatch, content="Two POA&M items due within 30 days; the CAT1 CVE patch is critical with only 3 days remaining — escalate to alice immediately.")

    result = digest_service.send_poam_due_soon_digest("poam@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["due_count"] == 2
    assert result["overdue"] == 0
    assert result["narrative"] == "Two POA&M items due within 30 days; the CAT1 CVE patch is critical with only 3 days remaining — escalate to alice immediately."


def test_poam_due_soon_digest_narrative_none_on_llm_failure(monkeypatch):
    """POA&M digest ships even when the LLM raises; narrative degrades to None."""
    due_items = [
        {"id": "poam-3", "title": "Update TLS certs", "severity": "CAT2",
         "due_date": "2026-06-25", "owner": "carol", "status": "open", "days_left": 23},
    ]
    overdue = {"cnt": 2}
    _fake_digest_conn(monkeypatch, rows_by_call=[due_items, overdue])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = digest_service.send_poam_due_soon_digest("poam@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"
    assert result["overdue"] == 2
