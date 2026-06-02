# CUI // SP-CTI
"""Tests for the AI-ified render narrative in render_handler_service (aiify-opp-5875).

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
# aiify-opp-5878: DIC document summary, modernization status, security scan
# ---------------------------------------------------------------------------

def test_dic_document_summary_narrative_attached(monkeypatch):
    """render_and_send_dic_document_summary attaches narrative when LLM is available."""
    doc_row = {"id": "doc-1", "title": "ACOIC Architecture Spec", "source_type": "pdf",
               "ingested_at": "2026-06-01", "classification": "CUI"}
    chunk_rows = [{"id": "c-1", "chunk_index": 0, "relevance_score": 0.92}]
    entity_rows = [{"entity_text": "ACOIC", "entity_type": "ORG", "confidence": 0.95}]
    _fake_conn(monkeypatch, rows_by_call=[doc_row, chunk_rows, entity_rows])
    _fake_router(monkeypatch, content="ACOIC Architecture Spec ingested as CUI PDF; review extracted entities before sharing.")

    result = rhs.render_and_send_dic_document_summary("doc-1", "dic@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["doc_id"] == "doc-1"
    assert result["narrative"] == "ACOIC Architecture Spec ingested as CUI PDF; review extracted entities before sharing."


def test_dic_document_summary_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_send_dic_document_summary("doc-99", "dic@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_dic_document_summary_narrative_none_on_llm_failure(monkeypatch):
    """DIC notification ships even when the LLM raises; narrative degrades to None."""
    doc_row = {"id": "doc-2", "title": "STIG Guide", "source_type": "html",
               "ingested_at": "2026-06-01", "classification": "CUI"}
    _fake_conn(monkeypatch, rows_by_call=[doc_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_send_dic_document_summary("doc-2", "dic@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_modernization_status_narrative_attached(monkeypatch):
    """render_and_deliver_modernization_status attaches narrative when LLM is available."""
    project_row = {"slug": "acoic-mod", "name": "ACOIC Modernization", "status": "in_progress",
                   "completion_pct": 62.0, "target_il": "IL5"}
    milestone_rows = [{"title": "API refactor", "status": "done", "due_date": "2026-05-15"}]
    risk_rows = [{"title": "Dependency gap", "severity": "high", "mitigation_status": "open"}]
    _fake_conn(monkeypatch, rows_by_call=[project_row, milestone_rows, risk_rows])
    _fake_router(monkeypatch, content="ACOIC Modernization is 62% complete targeting IL5; address high-severity dependency gap this sprint.")

    result = rhs.render_and_deliver_modernization_status("acoic-mod", "lead@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["project_slug"] == "acoic-mod"
    assert result["narrative"] == "ACOIC Modernization is 62% complete targeting IL5; address high-severity dependency gap this sprint."


def test_modernization_status_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_deliver_modernization_status("proj-x", "lead@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_modernization_status_narrative_none_on_llm_failure(monkeypatch):
    """Modernization notification ships even when the LLM raises; narrative degrades to None."""
    project_row = {"slug": "mod-2", "name": "Legacy Migration", "status": "planning",
                   "completion_pct": 10.0, "target_il": "IL4"}
    _fake_conn(monkeypatch, rows_by_call=[project_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("llm timeout"))

    result = rhs.render_and_deliver_modernization_status("mod-2", "lead@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_security_scan_narrative_attached(monkeypatch):
    """render_and_notify_security_scan attaches narrative when LLM is available."""
    scan_row = {"id": "sr-1", "scan_type": "SAST", "target": "tools/", "status": "complete", "created_at": "2026-06-01"}
    finding_rows = [{"title": "SQL injection risk", "severity": "critical", "tool": "bandit", "status": "open"}]
    summary_rows = [{"severity": "critical", "cnt": 1}]
    _fake_conn(monkeypatch, rows_by_call=[scan_row, finding_rows, summary_rows])
    _fake_router(monkeypatch, content="SAST scan found 1 critical SQL injection risk in tools/; remediate before next deploy gate.")

    result = rhs.render_and_notify_security_scan("sr-1", "sec@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["scan_run_id"] == "sr-1"
    assert result["narrative"] == "SAST scan found 1 critical SQL injection risk in tools/; remediate before next deploy gate."


def test_security_scan_no_narrative_by_default(monkeypatch):
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_notify_security_scan("sr-99", "sec@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_security_scan_narrative_none_on_llm_failure(monkeypatch):
    """Security scan notification ships even when the LLM raises; narrative degrades to None."""
    scan_row = {"id": "sr-2", "scan_type": "dependency", "target": "requirements.txt",
                "status": "complete", "created_at": "2026-06-01"}
    _fake_conn(monkeypatch, rows_by_call=[scan_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = rhs.render_and_notify_security_scan("sr-2", "sec@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# aiify-opp-5915: D-AWARE drift detection render chain
# ---------------------------------------------------------------------------

def test_awareness_drift_report_narrative_attached(monkeypatch):
    """render_and_notify_awareness_drift_report attaches narrative when LLM is available."""
    run_row = {"id": "run-1", "probe_run_id": "pr-1", "status": "complete", "created_at": "2026-06-02"}
    regression_rows = [
        {"title": "route_regression: /agents", "severity": "high", "confidence": 0.85,
         "outcome": "http_head fail", "created_at": "2026-06-02"},
    ]
    health_rows = [
        {"node_id": "agents-blueprint", "probe_type": "http_head", "status": "fail"},
    ]
    _fake_conn(monkeypatch, rows_by_call=[run_row, regression_rows, health_rows])
    _fake_router(monkeypatch, content="1 high-severity route regression detected on /agents; investigate blueprint startup and restart the agent.")

    result = rhs.render_and_notify_awareness_drift_report("run-1", "ops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["run_id"] == "run-1"
    assert result["regression_count"] == 1
    assert result["narrative"] == "1 high-severity route regression detected on /agents; investigate blueprint startup and restart the agent."


def test_awareness_drift_report_no_narrative_by_default(monkeypatch):
    """render_and_notify_awareness_drift_report returns narrative=None when ai_narrative=False."""
    _fake_conn(monkeypatch, rows_by_call=[None, [], []])

    result = rhs.render_and_notify_awareness_drift_report("run-99", "ops@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"
    assert result["regression_count"] == 0


def test_awareness_drift_report_narrative_none_on_llm_failure(monkeypatch):
    """Drift report ships even when the LLM raises; narrative degrades to None."""
    run_row = {"id": "run-2", "probe_run_id": "pr-2", "status": "error", "created_at": "2026-06-02"}
    _fake_conn(monkeypatch, rows_by_call=[run_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = rhs.render_and_notify_awareness_drift_report("run-2", "ops@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_awareness_drift_report_narrative_facts_grounded(monkeypatch):
    """Narrative prompt must include run_id, regression_count, and critical_high_count in facts."""
    run_row = {"id": "run-3", "probe_run_id": "pr-3", "status": "complete", "created_at": "2026-06-02"}
    regression_rows = [
        {"title": "module_import_broken: tools.aiify.scanner", "severity": "critical",
         "confidence": 0.95, "outcome": "ImportError", "created_at": "2026-06-02"},
        {"title": "coherence_new_fail: tools.manifest check", "severity": "high",
         "confidence": 0.80, "outcome": "coherence fail", "created_at": "2026-06-02"},
    ]
    _fake_conn(monkeypatch, rows_by_call=[run_row, regression_rows, []])
    captured = _fake_router(monkeypatch, content="2 regressions detected.")

    rhs.render_and_notify_awareness_drift_report("run-3", "ops@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "run_id" in user_msg
    assert "regression_count" in user_msg
    assert "critical_high_count" in user_msg
    assert "D-AWARE drift detection" in user_msg


# ---------------------------------------------------------------------------
# aiify-opp-5913: weekly AI-ify digest render chain
# ---------------------------------------------------------------------------

def test_aiify_weekly_digest_narrative_attached(monkeypatch):
    """render_and_send_aiify_weekly_digest attaches narrative when LLM is available."""
    scan_rows = [
        {"scan_id": "sc-1", "status": "complete", "overall_ai_readiness": 72.5, "created_at": "2026-06-01"},
        {"scan_id": "sc-2", "status": "complete", "overall_ai_readiness": 75.0, "created_at": "2026-05-31"},
    ]
    opp_rows = [
        {"function_name": "handle_foo", "pattern_type": "db_render_notify_chain", "composite_score": 0.76},
    ]
    roadmap_rows = [{"roadmap_id": "rm-abc", "scan_id": "sc-1"}]
    _fake_conn(monkeypatch, rows_by_call=[scan_rows, opp_rows, roadmap_rows])
    _fake_router(monkeypatch, content="AI readiness averaged 73.75% this week with 1 top opportunity; prioritize db_render_notify_chain implementation.")

    result = rhs.render_and_send_aiify_weekly_digest("2026-W22", "aiops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["week_label"] == "2026-W22"
    assert result["scan_count"] == 2
    assert result["narrative"] == "AI readiness averaged 73.75% this week with 1 top opportunity; prioritize db_render_notify_chain implementation."


def test_aiify_weekly_digest_no_narrative_by_default(monkeypatch):
    """render_and_send_aiify_weekly_digest returns narrative=None when ai_narrative=False."""
    _fake_conn(monkeypatch, rows_by_call=[[], [], []])

    result = rhs.render_and_send_aiify_weekly_digest("2026-W21", "aiops@example.com")

    assert result["narrative"] is None
    assert result["status"] == "sent"
    assert result["scan_count"] == 0


def test_aiify_weekly_digest_narrative_none_on_llm_failure(monkeypatch):
    """Weekly digest ships even when the LLM raises; narrative degrades to None."""
    scan_rows = [
        {"scan_id": "sc-3", "status": "complete", "overall_ai_readiness": 68.0, "created_at": "2026-06-02"},
    ]
    _fake_conn(monkeypatch, rows_by_call=[scan_rows, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_send_aiify_weekly_digest("2026-W23", "aiops@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"
    assert result["scan_count"] == 1


def test_aiify_weekly_digest_narrative_facts_grounded(monkeypatch):
    """Narrative prompt must include week_label, scan_count, and top_pattern_type in facts."""
    scan_rows = [
        {"scan_id": "sc-4", "status": "complete", "overall_ai_readiness": 80.0, "created_at": "2026-06-01"},
    ]
    opp_rows = [
        {"function_name": "process_report", "pattern_type": "llm_generation", "composite_score": 0.91},
    ]
    _fake_conn(monkeypatch, rows_by_call=[scan_rows, opp_rows, []])
    captured = _fake_router(monkeypatch, content="Strong AI week.")

    rhs.render_and_send_aiify_weekly_digest("2026-W24", "aiops@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "week_label" in user_msg
    assert "scan_count" in user_msg
    assert "top_pattern_type" in user_msg
    assert "llm_generation" in user_msg


# ---------------------------------------------------------------------------
# aiify-opp-5916: supply chain alert, FedRAMP ATO status, innovation cycle
# ---------------------------------------------------------------------------

def test_supply_chain_alert_no_narrative_by_default(monkeypatch):
    """render_and_send_supply_chain_alert returns narrative=None when ai_narrative=False."""
    assessment_row = {
        "id": "ASS-1", "vendor_name": "Acme Corp", "risk_level": "high",
        "assessed_at": "2026-06-01", "overall_score": 42.5,
    }
    finding_rows = [
        {"title": "Open source dependency risk", "severity": "critical", "category": "software", "status": "open"},
    ]
    vendor_rows = [{"vendor_name": "Acme Corp", "risk_tier": 1}]
    _fake_conn(monkeypatch, rows_by_call=[assessment_row, finding_rows, vendor_rows])

    result = rhs.render_and_send_supply_chain_alert("ASS-1", "sc@example.com")

    assert result["status"] == "sent"
    assert result["assessment_id"] == "ASS-1"
    assert result["narrative"] is None


def test_supply_chain_alert_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    assessment_row = {
        "id": "ASS-2", "vendor_name": "Beta Inc", "risk_level": "critical",
        "assessed_at": "2026-06-01", "overall_score": 28.0,
    }
    finding_rows = [
        {"title": "Backdoor in firmware", "severity": "critical", "category": "hardware", "status": "open"},
        {"title": "No SLA for patches", "severity": "high", "category": "process", "status": "open"},
    ]
    vendor_rows = []
    _fake_conn(monkeypatch, rows_by_call=[assessment_row, finding_rows, vendor_rows])
    _fake_router(monkeypatch, content="Critical supply chain risk detected at Beta Inc; suspend procurement pending review.")

    result = rhs.render_and_send_supply_chain_alert("ASS-2", "sc@example.com", ai_narrative=True)

    assert result["narrative"] == "Critical supply chain risk detected at Beta Inc; suspend procurement pending review."
    assert result["assessment_id"] == "ASS-2"
    assert result["status"] == "sent"


def test_supply_chain_alert_narrative_none_on_llm_failure(monkeypatch):
    """Notification ships even when the LLM raises; narrative degrades to None."""
    assessment_row = {"id": "ASS-3", "vendor_name": "Gamma LLC", "risk_level": "medium", "overall_score": 60.0}
    _fake_conn(monkeypatch, rows_by_call=[assessment_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_send_supply_chain_alert("ASS-3", "sc@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_fedramp_ato_no_narrative_by_default(monkeypatch):
    """render_and_deliver_fedramp_ato_status returns narrative=None when ai_narrative=False."""
    pkg_row = {
        "id": "PKG-10", "system_name": "ICDEV Platform", "ato_status": "authorized",
        "authorization_date": "2026-01-15", "expiry_date": "2029-01-15",
    }
    controls_rows = [{"implementation_status": "implemented", "cnt": 120}]
    poam_rows = [{"id": "P-1", "title": "Patch TLS", "severity": "medium", "due_date": "2026-09-01"}]
    _fake_conn(monkeypatch, rows_by_call=[pkg_row, controls_rows, poam_rows])

    result = rhs.render_and_deliver_fedramp_ato_status("PKG-10", "isso@example.com")

    assert result["status"] == "sent"
    assert result["package_id"] == "PKG-10"
    assert result["narrative"] is None


def test_fedramp_ato_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    pkg_row = {
        "id": "PKG-11", "system_name": "Classified Portal", "ato_status": "in_process",
        "authorization_date": None, "expiry_date": None,
    }
    controls_rows = [
        {"implementation_status": "implemented", "cnt": 80},
        {"implementation_status": "planned", "cnt": 40},
    ]
    poam_rows = []
    _fake_conn(monkeypatch, rows_by_call=[pkg_row, controls_rows, poam_rows])
    _fake_router(monkeypatch, content="FedRAMP authorization in progress; 80 controls implemented, 40 remaining before ATO issuance.")

    result = rhs.render_and_deliver_fedramp_ato_status("PKG-11", "isso@example.com", ai_narrative=True)

    assert result["narrative"] == "FedRAMP authorization in progress; 80 controls implemented, 40 remaining before ATO issuance."
    assert result["package_id"] == "PKG-11"
    assert result["status"] == "sent"


def test_fedramp_ato_narrative_none_on_llm_failure(monkeypatch):
    """FedRAMP notification ships even when the LLM raises; narrative degrades to None."""
    pkg_row = {"id": "PKG-12", "system_name": "Test System", "ato_status": "expired"}
    _fake_conn(monkeypatch, rows_by_call=[pkg_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("llm unavailable"))

    result = rhs.render_and_deliver_fedramp_ato_status("PKG-12", "isso@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_innovation_cycle_no_narrative_by_default(monkeypatch):
    """render_and_notify_innovation_cycle returns narrative=None when ai_narrative=False."""
    cycle_row = {
        "id": "CYC-5", "cycle_type": "weekly", "status": "completed",
        "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    finding_rows = [
        {"title": "New RAG pattern detected", "category": "ai_rag", "priority": 1, "status": "new"},
    ]
    action_rows = [{"action_type": "suggest", "target": "rag_server.py", "outcome": "card_created"}]
    _fake_conn(monkeypatch, rows_by_call=[cycle_row, finding_rows, action_rows])

    result = rhs.render_and_notify_innovation_cycle("CYC-5", "ops@example.com")

    assert result["status"] == "sent"
    assert result["cycle_id"] == "CYC-5"
    assert result["narrative"] is None


def test_innovation_cycle_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    cycle_row = {
        "id": "CYC-6", "cycle_type": "monthly", "status": "completed",
        "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    finding_rows = [
        {"title": "LLM routing optimization", "category": "performance", "priority": 1, "status": "new"},
        {"title": "Vector index fragmentation", "category": "database", "priority": 2, "status": "new"},
    ]
    action_rows = [{"action_type": "optimize", "target": "llm_router.py", "outcome": "card_created"}]
    _fake_conn(monkeypatch, rows_by_call=[cycle_row, finding_rows, action_rows])
    _fake_router(monkeypatch, content="Monthly innovation cycle surfaced 2 findings; LLM routing optimization is the top priority.")

    result = rhs.render_and_notify_innovation_cycle("CYC-6", "ops@example.com", ai_narrative=True)

    assert result["narrative"] == "Monthly innovation cycle surfaced 2 findings; LLM routing optimization is the top priority."
    assert result["cycle_id"] == "CYC-6"
    assert result["status"] == "sent"


def test_innovation_cycle_narrative_none_on_llm_failure(monkeypatch):
    """Notification ships even when the LLM raises for innovation cycle; narrative degrades to None."""
    cycle_row = {"id": "CYC-7", "cycle_type": "daily", "status": "completed"}
    _fake_conn(monkeypatch, rows_by_call=[cycle_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_notify_innovation_cycle("CYC-7", "ops@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_innovation_cycle_no_findings_still_returns(monkeypatch):
    """When no findings exist, top_category defaults to 'none' and function still returns."""
    cycle_row = {"id": "CYC-8", "cycle_type": "weekly", "status": "no_findings"}
    _fake_conn(monkeypatch, rows_by_call=[cycle_row, [], []])

    result = rhs.render_and_notify_innovation_cycle("CYC-8", "ops@example.com")

    assert result["status"] == "sent"
    assert result["cycle_id"] == "CYC-8"
    assert result["narrative"] is None


# ---------------------------------------------------------------------------
# aiify-opp-5917: fine-tuning job result render chain
# ---------------------------------------------------------------------------

def test_finetune_job_result_no_narrative_by_default(monkeypatch):
    """render_and_notify_finetune_job_result returns narrative=None when ai_narrative=False."""
    job_row = {
        "id": "JOB-1", "model_base": "claude-haiku-4-5", "status": "completed",
        "epochs": 3, "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    metric_rows = [{"metric_name": "train_loss", "value": 0.21, "epoch": 3}]
    eval_rows = [{"benchmark_name": "hellaswag", "score": 0.87, "delta": 0.04}]
    _fake_conn(monkeypatch, rows_by_call=[job_row, metric_rows, eval_rows])

    result = rhs.render_and_notify_finetune_job_result("JOB-1", "aiops@example.com")

    assert result["status"] == "sent"
    assert result["job_id"] == "JOB-1"
    assert result["narrative"] is None


def test_finetune_job_result_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    job_row = {
        "id": "JOB-2", "model_base": "claude-sonnet-4-6", "status": "completed",
        "epochs": 5, "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    metric_rows = [
        {"metric_name": "train_loss", "value": 0.18, "epoch": 5},
        {"metric_name": "val_loss", "value": 0.22, "epoch": 5},
    ]
    eval_rows = [
        {"benchmark_name": "mmlu", "score": 0.91, "delta": 0.07},
        {"benchmark_name": "hellaswag", "score": 0.88, "delta": 0.03},
    ]
    _fake_conn(monkeypatch, rows_by_call=[job_row, metric_rows, eval_rows])
    _fake_router(monkeypatch, content="Fine-tuning JOB-2 completed on claude-sonnet-4-6 over 5 epochs with top MMLU score 0.91; deploy to staging for evaluation.")

    result = rhs.render_and_notify_finetune_job_result("JOB-2", "aiops@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["job_id"] == "JOB-2"
    assert result["narrative"] == "Fine-tuning JOB-2 completed on claude-sonnet-4-6 over 5 epochs with top MMLU score 0.91; deploy to staging for evaluation."


def test_finetune_job_result_narrative_none_on_llm_failure(monkeypatch):
    """Fine-tuning notification ships even when the LLM raises; narrative degrades to None."""
    job_row = {
        "id": "JOB-3", "model_base": "claude-haiku-4-5", "status": "failed",
        "epochs": 0, "started_at": "2026-06-02", "completed_at": "2026-06-02",
    }
    _fake_conn(monkeypatch, rows_by_call=[job_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_notify_finetune_job_result("JOB-3", "aiops@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_finetune_job_result_narrative_facts_grounded(monkeypatch):
    """Narrative prompt must include job_id, model_base, job_status, and best_eval_score."""
    job_row = {
        "id": "JOB-4", "model_base": "claude-opus-4-8", "status": "completed",
        "epochs": 10, "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    eval_rows = [{"benchmark_name": "mmlu", "score": 0.93, "delta": 0.09}]
    _fake_conn(monkeypatch, rows_by_call=[job_row, [], eval_rows])
    captured = _fake_router(monkeypatch, content="Strong fine-tune result.")

    rhs.render_and_notify_finetune_job_result("JOB-4", "aiops@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "job_id" in user_msg
    assert "model_base" in user_msg
    assert "job_status" in user_msg
    assert "best_eval_score" in user_msg
    assert "fine-tuning job result" in user_msg


def test_finetune_job_result_no_eval_results_returns(monkeypatch):
    """When no eval results exist, best_eval_score defaults to 0.0 and function still returns."""
    job_row = {"id": "JOB-5", "model_base": "claude-haiku-4-5", "status": "completed", "epochs": 2}
    _fake_conn(monkeypatch, rows_by_call=[job_row, [], []])

    result = rhs.render_and_notify_finetune_job_result("JOB-5", "aiops@example.com")

    assert result["status"] == "sent"
    assert result["job_id"] == "JOB-5"
    assert result["narrative"] is None


# ---------------------------------------------------------------------------
# aiify-opp-5955: research engine findings render chain
# ---------------------------------------------------------------------------

def test_research_findings_no_narrative_by_default(monkeypatch):
    """render_and_notify_research_findings returns narrative=None when ai_narrative=False."""
    report_row = {
        "id": "rpt-1", "topic": "Zero Trust Architecture Gaps", "status": "complete",
        "confidence": 0.88, "created_at": "2026-06-02",
    }
    finding_rows = [
        {"title": "Micro-segmentation gaps", "category": "network", "confidence": 0.91, "summary": "..."},
        {"title": "Identity governance gaps", "category": "identity", "confidence": 0.85, "summary": "..."},
    ]
    source_rows = [{"source_url": "https://example.mil/zt.pdf", "source_type": "pdf", "relevance_score": 0.93}]
    _fake_conn(monkeypatch, rows_by_call=[report_row, finding_rows, source_rows])

    result = rhs.render_and_notify_research_findings("rpt-1", "research@example.com")

    assert result["status"] == "sent"
    assert result["report_id"] == "rpt-1"
    assert result["finding_count"] == 2
    assert result["narrative"] is None


def test_research_findings_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    report_row = {
        "id": "rpt-2", "topic": "FedRAMP High Control Gaps", "status": "complete",
        "confidence": 0.92, "created_at": "2026-06-02",
    }
    finding_rows = [
        {"title": "AC-2 gap", "category": "access_control", "confidence": 0.93, "summary": "Account management not enforced."},
    ]
    source_rows = [
        {"source_url": "https://example.com/nist.pdf", "source_type": "pdf", "relevance_score": 0.97},
    ]
    _fake_conn(monkeypatch, rows_by_call=[report_row, finding_rows, source_rows])
    _fake_router(monkeypatch, content="Research engine found 1 FedRAMP control gap (AC-2); prioritize account management remediation before the next ATO review cycle.")

    result = rhs.render_and_notify_research_findings("rpt-2", "research@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["report_id"] == "rpt-2"
    assert result["finding_count"] == 1
    assert result["narrative"] == "Research engine found 1 FedRAMP control gap (AC-2); prioritize account management remediation before the next ATO review cycle."


def test_research_findings_narrative_none_on_llm_failure(monkeypatch):
    """Research notification ships even when the LLM raises; narrative degrades to None."""
    report_row = {"id": "rpt-3", "topic": "Supply Chain Risk", "status": "complete", "confidence": 0.75}
    _fake_conn(monkeypatch, rows_by_call=[report_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_notify_research_findings("rpt-3", "research@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_research_findings_narrative_facts_grounded(monkeypatch):
    """Narrative prompt must include report_id, topic, finding_count, and source_count."""
    report_row = {
        "id": "rpt-4", "topic": "CMMC Level 3 Readiness", "status": "complete",
        "confidence": 0.80, "created_at": "2026-06-02",
    }
    finding_rows = [
        {"title": "IR-1 gap", "category": "incident_response", "confidence": 0.82, "summary": "..."},
        {"title": "MA-2 gap", "category": "maintenance", "confidence": 0.78, "summary": "..."},
        {"title": "CA-3 gap", "category": "assessment", "confidence": 0.76, "summary": "..."},
    ]
    source_rows = [
        {"source_url": "https://example.com/cmmc.pdf", "source_type": "pdf", "relevance_score": 0.90},
        {"source_url": "https://example.com/nist.pdf", "source_type": "pdf", "relevance_score": 0.85},
    ]
    _fake_conn(monkeypatch, rows_by_call=[report_row, finding_rows, source_rows])
    captured = _fake_router(monkeypatch, content="3 CMMC Level 3 gaps found.")

    rhs.render_and_notify_research_findings("rpt-4", "research@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "report_id" in user_msg
    assert "topic" in user_msg
    assert "finding_count" in user_msg
    assert "source_count" in user_msg
    assert "research engine findings" in user_msg


def test_research_findings_no_findings_still_returns(monkeypatch):
    """When no findings exist, avg_finding_confidence is 0.0 and function still returns."""
    report_row = {"id": "rpt-5", "topic": "Empty Scan", "status": "no_findings", "confidence": 0.0}
    _fake_conn(monkeypatch, rows_by_call=[report_row, [], []])

    result = rhs.render_and_notify_research_findings("rpt-5", "research@example.com")

    assert result["status"] == "sent"
    assert result["report_id"] == "rpt-5"
    assert result["finding_count"] == 0
    assert result["narrative"] is None


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


# ---------------------------------------------------------------------------
# aiify-opp-5953: simulation COA result render chain
# ---------------------------------------------------------------------------

def test_simulation_coa_result_no_narrative_by_default(monkeypatch):
    """render_and_notify_simulation_coa_result returns narrative=None when ai_narrative=False."""
    run_row = {
        "id": "run-1", "scenario_name": "Budget Crunch Scenario",
        "status": "complete", "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    coa_rows = [
        {"coa_id": "coa-1", "title": "Reduce scope", "feasibility_score": 0.82, "risk_score": 0.35, "recommended": True},
        {"coa_id": "coa-2", "title": "Delay phase 2", "feasibility_score": 0.74, "risk_score": 0.50, "recommended": False},
    ]
    metric_rows = [{"metric_name": "cost_variance", "value": -120000, "unit": "USD"}]
    _fake_conn(monkeypatch, rows_by_call=[run_row, coa_rows, metric_rows])

    result = rhs.render_and_notify_simulation_coa_result("run-1", "lead@example.com")

    assert result["status"] == "sent"
    assert result["run_id"] == "run-1"
    assert result["coa_count"] == 2
    assert result["narrative"] is None


def test_simulation_coa_result_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    run_row = {
        "id": "run-2", "scenario_name": "Staffing Constraint COA",
        "status": "complete", "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    coa_rows = [
        {"coa_id": "coa-3", "title": "Hire contractors", "feasibility_score": 0.91, "risk_score": 0.28, "recommended": True},
    ]
    metric_rows = [
        {"metric_name": "schedule_variance_days", "value": -14, "unit": "days"},
        {"metric_name": "cost_overrun_pct", "value": 8.5, "unit": "percent"},
    ]
    _fake_conn(monkeypatch, rows_by_call=[run_row, coa_rows, metric_rows])
    _fake_router(monkeypatch, content="Staffing Constraint COA simulation complete; COA 'Hire contractors' is recommended with 0.91 feasibility — approve contract action immediately.")

    result = rhs.render_and_notify_simulation_coa_result("run-2", "lead@example.com", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["run_id"] == "run-2"
    assert result["coa_count"] == 1
    assert result["narrative"] == "Staffing Constraint COA simulation complete; COA 'Hire contractors' is recommended with 0.91 feasibility — approve contract action immediately."


def test_simulation_coa_result_narrative_none_on_llm_failure(monkeypatch):
    """COA notification ships even when the LLM raises; narrative degrades to None."""
    run_row = {"id": "run-3", "scenario_name": "Risk Scenario A", "status": "complete"}
    _fake_conn(monkeypatch, rows_by_call=[run_row, [], []])
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = rhs.render_and_notify_simulation_coa_result("run-3", "lead@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_simulation_coa_result_narrative_facts_grounded(monkeypatch):
    """Narrative prompt must include run_id, scenario_name, coa_count, and recommended_count."""
    run_row = {
        "id": "run-4", "scenario_name": "IL5 Migration COA",
        "status": "complete", "started_at": "2026-06-01", "completed_at": "2026-06-02",
    }
    coa_rows = [
        {"coa_id": "coa-5", "title": "Phased migration", "feasibility_score": 0.88, "risk_score": 0.30, "recommended": True},
        {"coa_id": "coa-6", "title": "Big bang cutover", "feasibility_score": 0.65, "risk_score": 0.70, "recommended": False},
    ]
    metric_rows = [{"metric_name": "estimated_downtime_hrs", "value": 2.5, "unit": "hours"}]
    _fake_conn(monkeypatch, rows_by_call=[run_row, coa_rows, metric_rows])
    captured = _fake_router(monkeypatch, content="2 COAs evaluated for IL5 Migration.")

    rhs.render_and_notify_simulation_coa_result("run-4", "lead@example.com", ai_narrative=True)

    user_msg = captured["request"].messages[0]["content"]
    assert "run_id" in user_msg
    assert "scenario_name" in user_msg
    assert "coa_count" in user_msg
    assert "recommended_count" in user_msg
    assert "simulation COA result" in user_msg
