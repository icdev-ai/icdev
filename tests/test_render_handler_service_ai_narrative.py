# CUI // SP-CTI
"""Tests for the AI-ified render narrative in render_handler_service (aiify-opp-5952).

The db -> render -> notify chains in
``tools.notification_service.render_handler_service`` gained an opt-in LLM
render narrative. These tests pin the two load-bearing guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so a rendered
   notification never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import render_handler_service as rhs


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


def _fake_conn(monkeypatch, rows_by_call=()):
    """Patch get_connection() and all event_service helpers in render_handler_service."""
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

    monkeypatch.setattr(rhs, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(rhs, "render", lambda *a, **kw: "<p>rendered</p>")
    monkeypatch.setattr(rhs, "send", lambda *a, **kw: None)
    monkeypatch.setattr(rhs, "sendmail", lambda *a, **kw: None)
    monkeypatch.setattr(rhs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rhs, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(rhs, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(rhs, "dispatch", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# _ai_render_narrative unit tests
# ---------------------------------------------------------------------------

def test_narrative_returns_content_when_llm_available(monkeypatch):
    captured = _fake_router(monkeypatch, content="  Task TASK-42 summary rendered; review and close the sprint.  ")
    facts = {
        "task_id": "TASK-42",
        "task_title": "Add ZIG maturity report",
        "task_status": "done",
        "actor": "sovanna",
        "event_count": 5,
        "subtask_count": 2,
    }

    out = rhs._ai_render_narrative("task summary render notification", facts)

    assert out == "Task TASK-42 summary rendered; review and close the sprint."
    assert captured["function"] == "narrative_generation"
    user_msg = captured["request"].messages[0]["content"]
    assert "task summary render notification" in user_msg
    assert "task_id" in user_msg
    assert "task_status" in user_msg
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = rhs._ai_render_narrative(
        "canvas status render notification", {"canvas_name": "ZIG", "score": 87.5}
    )

    assert out is None


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = rhs._ai_render_narrative(
        "STIG compliance report render notification", {"workload_id": "wl-1", "check_count": 10}
    )

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = rhs._ai_render_narrative(
        "agent performance report render notification",
        {"agent_id": "agent-7", "agent_status": "crashed"},
    )

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last", "a_first": "first", "m_middle": "middle"}

    rhs._ai_render_narrative("oracle lens digest render notification", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z


def test_render_kind_appears_in_prompt(monkeypatch):
    """The render_kind label must appear in the user message for framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    rhs._ai_render_narrative(
        "POA&M update render notification",
        {"poam_id": "POAM-99", "severity": "high", "due_date": "2026-07-01"},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "POA&M update render notification" in user_msg
    assert "poam_id" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for render narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    rhs._ai_render_narrative("ZIG pillar update render notification", {"pillar_slug": "identity"})

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    rhs._ai_render_narrative("genesis progress render notification", {"design_id": "d-1"})

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


# ---------------------------------------------------------------------------
# Integration tests for render_and_* functions
# ---------------------------------------------------------------------------

def test_task_summary_no_narrative_by_default(monkeypatch):
    """render_and_send_task_summary returns narrative=None when ai_narrative=False."""
    task_row = {"id": "TASK-42", "title": "Add ZIG report", "status": "done", "actor": "sovanna", "created_at": "2026-06-01"}
    event_rows = [{"event": "status_change", "actor": "sovanna", "created_at": "2026-06-01"}]
    subtask_rows = []
    _fake_conn(monkeypatch, rows_by_call=[task_row, event_rows, subtask_rows])

    result = rhs.render_and_send_task_summary("TASK-42", "ops@example.com")

    assert result["status"] == "sent"
    assert result["task_id"] == "TASK-42"
    assert result["narrative"] is None


def test_task_summary_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    task_row = {"id": "TASK-42", "title": "Add ZIG report", "status": "done", "actor": "sovanna", "created_at": "2026-06-01"}
    event_rows = [{"event": "status_change", "actor": "sovanna", "created_at": "2026-06-01"}]
    subtask_rows = [{"id": "TASK-43", "title": "Sub task", "status": "done"}]
    _fake_conn(monkeypatch, rows_by_call=[task_row, event_rows, subtask_rows])
    _fake_router(monkeypatch, content="Task TASK-42 completed with 1 subtask; verify and close sprint.")

    result = rhs.render_and_send_task_summary("TASK-42", "ops@example.com", ai_narrative=True)

    assert result["narrative"] == "Task TASK-42 completed with 1 subtask; verify and close sprint."
    assert result["status"] == "sent"


def test_task_summary_narrative_none_on_llm_failure(monkeypatch):
    """Notification ships even when the LLM raises; narrative degrades to None."""
    task_row = {"id": "TASK-99", "title": "Bug fix", "status": "in_progress", "actor": "alice", "created_at": "2026-06-01"}
    _fake_conn(monkeypatch, rows_by_call=[task_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_send_task_summary("TASK-99", "alice@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_canvas_status_no_narrative_by_default(monkeypatch):
    """render_and_deliver_canvas_status returns narrative=None when ai_narrative=False."""
    assessment_row = {"id": "a-1", "score": 87.5, "created_at": "2026-06-01"}
    finding_rows = [{"id": "f-1", "title": "CAT I open", "severity": "I"}]
    trend_rows = [{"score": 85.0, "created_at": "2026-05-25"}]
    _fake_conn(monkeypatch, rows_by_call=[assessment_row, finding_rows, trend_rows])

    result = rhs.render_and_deliver_canvas_status("ZIG Canvas", "ops@example.com")

    assert result["status"] == "sent"
    assert result["canvas_name"] == "ZIG Canvas"
    assert result["narrative"] is None


def test_canvas_status_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    assessment_row = {"id": "a-2", "score": 91.0, "created_at": "2026-06-01"}
    finding_rows = []
    trend_rows = [{"score": 88.0, "created_at": "2026-05-25"}]
    _fake_conn(monkeypatch, rows_by_call=[assessment_row, finding_rows, trend_rows])
    _fake_router(monkeypatch, content="ZIG Canvas is at 91% with no open findings; maintain current controls.")

    result = rhs.render_and_deliver_canvas_status("ZIG Canvas", "ops@example.com", ai_narrative=True)

    assert result["narrative"] == "ZIG Canvas is at 91% with no open findings; maintain current controls."
    assert result["status"] == "sent"


def test_oracle_digest_narrative_attached(monkeypatch):
    """render_and_send_oracle_digest attaches narrative when LLM is available."""
    prediction_rows = [
        {"id": "P-1", "title": "Supply chain risk", "severity": "high", "confidence": 0.91, "outcome": "delayed"},
    ]
    lens_row = {"name": "Supply Chain", "horizon_days": 90}
    stat_rows = [{"severity": "high", "cnt": 1}]
    _fake_conn(monkeypatch, rows_by_call=[prediction_rows, lens_row, stat_rows])
    _fake_router(monkeypatch, content="Supply chain lens shows high risk within 90 days; engage alternate vendors.")

    result = rhs.render_and_send_oracle_digest("lens-7", "ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["lens_id"] == "lens-7"
    assert result["narrative"] == "Supply chain lens shows high risk within 90 days; engage alternate vendors."


def test_oracle_digest_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[[], None, []])

    result = rhs.render_and_send_oracle_digest("lens-1", "ops@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_genesis_progress_narrative_attached(monkeypatch):
    """render_and_notify_genesis_progress attaches narrative when LLM is available."""
    design_row = {"id": "d-1", "name": "ACOIC Genesis", "status": "active", "current_phase": "integrate"}
    phase_rows = [{"phase": "architect", "status": "done", "started_at": "2026-05-01", "completed_at": "2026-05-02"}]
    reflex_rows = [{"name": "gap_fill", "confidence": 0.85, "fired_at": "2026-06-01"}]
    _fake_conn(monkeypatch, rows_by_call=[design_row, phase_rows, reflex_rows])
    _fake_router(monkeypatch, content="ACOIC Genesis is in integrate phase with 1 reflex fired; review outputs before launch.")

    result = rhs.render_and_notify_genesis_progress("d-1", "dev@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["design_id"] == "d-1"
    assert result["narrative"] == "ACOIC Genesis is in integrate phase with 1 reflex fired; review outputs before launch."


def test_genesis_progress_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_notify_genesis_progress("d-99", "dev@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_stig_report_narrative_attached(monkeypatch):
    """render_and_dispatch_stig_report attaches narrative when LLM is available."""
    check_rows = [{"check_id": "V-1", "check_name": "Audit logging", "severity": "I", "status": "open"}]
    workload_row = {"name": "api-gateway", "classification": "CUI"}
    summary_rows = [{"status": "open", "cnt": 1}]
    _fake_conn(monkeypatch, rows_by_call=[check_rows, workload_row, summary_rows])
    _fake_router(monkeypatch, content="1 CAT I STIG finding open on api-gateway; remediate before next ATO cycle.")

    result = rhs.render_and_dispatch_stig_report("wl-1", "sec@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["workload_id"] == "wl-1"
    assert result["narrative"] == "1 CAT I STIG finding open on api-gateway; remediate before next ATO cycle."


def test_stig_report_narrative_none_on_llm_failure(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[[], None, []])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = rhs.render_and_dispatch_stig_report("wl-99", "sec@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_poam_update_narrative_attached(monkeypatch):
    """render_and_publish_poam_update attaches narrative when LLM is available."""
    poam_row = {
        "id": "POAM-10", "title": "Patch OpenSSL", "severity": "high",
        "status": "open", "due_date": "2026-07-01", "owner": "alice",
    }
    milestone_rows = [{"milestone_text": "Patch applied", "target_date": "2026-06-15", "status": "open"}]
    evidence_rows = [{"filename": "screenshot.png", "uploaded_at": "2026-06-01"}]
    _fake_conn(monkeypatch, rows_by_call=[poam_row, milestone_rows, evidence_rows])
    _fake_router(monkeypatch, content="POA&M POAM-10 due 2026-07-01; assign sprint resources to close before deadline.")

    result = rhs.render_and_publish_poam_update("POAM-10", "sec@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["poam_id"] == "POAM-10"
    assert result["narrative"] == "POA&M POAM-10 due 2026-07-01; assign sprint resources to close before deadline."


def test_poam_update_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_publish_poam_update("POAM-99", "sec@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_agent_report_narrative_attached(monkeypatch):
    """render_and_emit_agent_report attaches narrative when LLM is available."""
    agent_row = {"id": "agent-5", "name": "Architect", "status": "healthy", "last_heartbeat": "2026-06-01", "tier": "core"}
    metric_rows = [{"metric_name": "latency_ms", "value": 120, "recorded_at": "2026-06-01"}]
    error_rows = []
    _fake_conn(monkeypatch, rows_by_call=[agent_row, metric_rows, error_rows])
    _fake_router(monkeypatch, content="Architect agent is healthy with low latency; no action required.")

    result = rhs.render_and_emit_agent_report("agent-5", "ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["agent_id"] == "agent-5"
    assert result["narrative"] == "Architect agent is healthy with low latency; no action required."


def test_agent_report_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_emit_agent_report("agent-9", "ops@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_zig_pillar_update_narrative_attached(monkeypatch):
    """render_and_send_zig_pillar_update attaches narrative when LLM is available."""
    scores_row = {
        "pillar_slug": "identity", "score": 0.74, "maturity_level": "managed",
        "complete_activities": 31, "activity_count": 42,
    }
    cap_rows = [{"id": "c-1", "title": "MFA enforcement", "implementation_status": "done", "phase": 1}]
    completion_rows = [{"title": "FIDO2 keys", "status": "complete"}]
    _fake_conn(monkeypatch, rows_by_call=[scores_row, cap_rows, completion_rows])
    _fake_router(monkeypatch, content="Identity pillar at 74% maturity; prioritize PAM integration for next level.")

    result = rhs.render_and_send_zig_pillar_update("identity", "zig@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["pillar_slug"] == "identity"
    assert result["narrative"] == "Identity pillar at 74% maturity; prioritize PAM integration for next level."


def test_zig_pillar_update_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_send_zig_pillar_update("data", "zig@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_aiify_scan_results_narrative_attached(monkeypatch):
    """render_and_deliver_aiify_scan_results attaches narrative when LLM is available."""
    scan_row = {"scan_id": "sc-1", "input_ref": "tools/", "status": "complete", "overall_ai_readiness": 72.5, "created_at": "2026-06-01"}
    opp_rows = [{"function_name": "handle_foo", "pattern_type": "db_render_notify_chain", "composite_score": 0.76, "value_score": 0.88}]
    roadmap_row = {"roadmap_id": "rm-abc", "phases_json": "{}"}
    _fake_conn(monkeypatch, rows_by_call=[scan_row, opp_rows, roadmap_row])
    _fake_router(monkeypatch, content="AI readiness at 72.5%; 1 opportunity identified; implement db_render_notify_chain first.")

    result = rhs.render_and_deliver_aiify_scan_results("sc-1", "aiops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["scan_id"] == "sc-1"
    assert result["narrative"] == "AI readiness at 72.5%; 1 opportunity identified; implement db_render_notify_chain first."


def test_aiify_scan_results_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], None])

    result = rhs.render_and_deliver_aiify_scan_results("sc-99", "aiops@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_zig_gaps_report_narrative_attached(monkeypatch):
    """render_and_deliver_zig_gaps_report attaches narrative when LLM is available."""
    gap_rows = [
        {"pillar_slug": "identity", "title": "PAM integration", "phase": 2, "implementation_status": "not_started"},
    ]
    pillar_rows = [{"slug": "identity", "name": "Identity", "pillar_weight": 0.15}]
    activity_rows = [{"title": "Vault install", "phase": 2, "pillar_slug": "identity"}]
    _fake_conn(monkeypatch, rows_by_call=[gap_rows, pillar_rows, activity_rows])
    _fake_router(monkeypatch, content="1 ZIG capability gap across 1 pillar; prioritize PAM integration in Sprint 15.")

    result = rhs.render_and_deliver_zig_gaps_report("zig@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["gap_count"] == 1
    assert result["narrative"] == "1 ZIG capability gap across 1 pillar; prioritize PAM integration in Sprint 15."


def test_zig_gaps_report_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[[], [], []])

    result = rhs.render_and_deliver_zig_gaps_report("zig@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"
    assert result["gap_count"] == 0


def test_sprint_summary_narrative_attached(monkeypatch):
    """render_and_send_kanban_sprint_summary attaches narrative when LLM is available."""
    task_rows = [{"id": "T-1", "title": "Fix bug", "status": "done", "actor": "alice", "updated_at": "2026-06-01"}]
    metric_rows = [{"status": "done", "cnt": 1}]
    event_rows = []
    _fake_conn(monkeypatch, rows_by_call=[task_rows, metric_rows, event_rows])
    _fake_router(monkeypatch, content="Sprint SP-1 complete with 1 task done; prepare retrospective.")

    result = rhs.render_and_send_kanban_sprint_summary("SP-1", "lead@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["sprint_key"] == "SP-1"
    assert result["narrative"] == "Sprint SP-1 complete with 1 task done; prepare retrospective."


def test_sprint_summary_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[[], [], []])

    result = rhs.render_and_send_kanban_sprint_summary("SP-99", "lead@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_compliance_gate_narrative_attached(monkeypatch):
    """render_and_notify_compliance_gate attaches narrative when LLM is available."""
    gate_row = {"id": "g-1", "gate_name": "FedRAMP Moderate Gate", "status": "blocked", "triggered_at": "2026-06-01"}
    failure_rows = [{"criterion": "CAT I STIG open", "detail": "V-12345 open", "severity": "high"}]
    project_row = {"name": "ACOIC Platform", "classification": "CUI"}
    _fake_conn(monkeypatch, rows_by_call=[gate_row, failure_rows, project_row])
    _fake_router(monkeypatch, content="FedRAMP gate blocked for ACOIC Platform due to 1 failure; remediate V-12345 immediately.")

    result = rhs.render_and_notify_compliance_gate("g-1", "proj-1", "lead@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["gate_id"] == "g-1"
    assert result["narrative"] == "FedRAMP gate blocked for ACOIC Platform due to 1 failure; remediate V-12345 immediately."


def test_compliance_gate_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], None])

    result = rhs.render_and_notify_compliance_gate("g-99", "proj-99", "lead@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_compliance_gate_narrative_none_on_llm_failure(monkeypatch):
    """Gate notification ships even when the LLM raises; narrative degrades to None."""
    gate_row = {"id": "g-2", "gate_name": "CMMC Gate", "status": "passed", "triggered_at": "2026-06-01"}
    failure_rows = []
    project_row = {"name": "Portal", "classification": "CUI"}
    _fake_conn(monkeypatch, rows_by_call=[gate_row, failure_rows, project_row])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = rhs.render_and_notify_compliance_gate("g-2", "proj-2", "lead@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# aiify-opp-5952: RAG knowledge search result render chain
# ---------------------------------------------------------------------------

def test_rag_query_result_no_narrative_by_default(monkeypatch):
    """render_and_notify_rag_query_result returns narrative=None when ai_narrative=False."""
    query_row = {
        "id": "q-1", "query_text": "What are the ZIG identity pillar gaps?",
        "lens": "zig", "status": "complete", "created_at": "2026-06-02",
    }
    chunk_rows = [
        {"id": "ck-1", "source_doc": "zig_guide.pdf", "relevance_score": 0.91, "excerpt": "Identity gaps..."},
    ]
    citation_rows = [{"source_doc": "zig_guide.pdf", "citation_text": "p.14", "confidence": 0.88}]
    _fake_conn(monkeypatch, rows_by_call=[query_row, chunk_rows, citation_rows])

    result = rhs.render_and_notify_rag_query_result("q-1", "user@example.com")

    assert result["status"] == "sent"
    assert result["query_ref"] == "q-1"
    assert result["chunk_count"] == 1
    assert result["narrative"] is None


def test_rag_query_result_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    query_row = {
        "id": "q-2", "query_text": "Summarize the FedRAMP Moderate control gaps.",
        "lens": "compliance", "status": "complete", "created_at": "2026-06-02",
    }
    chunk_rows = [
        {"id": "ck-2", "source_doc": "fedramp_ssp.docx", "relevance_score": 0.95, "excerpt": "AC-2 gap..."},
        {"id": "ck-3", "source_doc": "fedramp_ssp.docx", "relevance_score": 0.87, "excerpt": "SC-8 gap..."},
    ]
    citation_rows = [
        {"source_doc": "fedramp_ssp.docx", "citation_text": "Section 3.2", "confidence": 0.92},
    ]
    _fake_conn(monkeypatch, rows_by_call=[query_row, chunk_rows, citation_rows])
    _fake_router(monkeypatch, content="2 FedRAMP Moderate control gaps identified (AC-2, SC-8); prioritize AC-2 account management remediation before next ATO review.")

    result = rhs.render_and_notify_rag_query_result("q-2", "user@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["query_ref"] == "q-2"
    assert result["chunk_count"] == 2
    assert result["narrative"] == "2 FedRAMP Moderate control gaps identified (AC-2, SC-8); prioritize AC-2 account management remediation before next ATO review."


def test_rag_query_result_narrative_none_on_llm_failure(monkeypatch):
    """RAG result notification ships even when the LLM raises; narrative degrades to None."""
    query_row = {"id": "q-3", "query_text": "STIG CAT I open items", "lens": "stig", "status": "complete"}
    _fake_conn(monkeypatch, rows_by_call=[query_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_notify_rag_query_result("q-3", "user@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_rag_query_result_narrative_facts_grounded(monkeypatch):
    """Narrative prompt must include query_ref, lens, chunk_count, and citation_count in facts."""
    query_row = {
        "id": "q-4", "query_text": "Innovation engine recent findings",
        "lens": "innovation", "status": "complete", "created_at": "2026-06-02",
    }
    chunk_rows = [
        {"id": "ck-4", "source_doc": "innovation_log.md", "relevance_score": 0.80, "excerpt": "RAG pattern..."},
        {"id": "ck-5", "source_doc": "innovation_log.md", "relevance_score": 0.75, "excerpt": "LLM routing..."},
        {"id": "ck-6", "source_doc": "innovation_log.md", "relevance_score": 0.70, "excerpt": "Vector index..."},
    ]
    citation_rows = [
        {"source_doc": "innovation_log.md", "citation_text": "2026-06-01 entry", "confidence": 0.82},
        {"source_doc": "innovation_log.md", "citation_text": "2026-05-31 entry", "confidence": 0.79},
    ]
    _fake_conn(monkeypatch, rows_by_call=[query_row, chunk_rows, citation_rows])
    captured = _fake_router(monkeypatch, content="3 innovation findings retrieved.")

    rhs.render_and_notify_rag_query_result("q-4", "user@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "query_ref" in user_msg
    assert "lens" in user_msg
    assert "chunk_count" in user_msg
    assert "citation_count" in user_msg
    assert "RAG knowledge search" in user_msg
