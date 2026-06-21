# CUI // SP-CTI
"""Tests for the AI-ified executive summary in the report service (aiify-opp-5539, aiify-opp-5596, aiify-opp-5840).

The db -> render -> notify chains in ``tools.notification_service.report_service``
gained an opt-in LLM executive summary. These tests pin the two load-bearing
guarantees:

1. The summary is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so an assessment or
   posture report never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import report_service


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
    captured = _fake_router(monkeypatch, content="  Posture improved; sustain remediation.  ")
    facts = {"canvas_name": "OHC", "score": 82.0, "delta": 3.0}

    out = report_service._ai_report_narrative("canvas assessment report", facts)

    assert out == "Posture improved; sustain remediation."  # stripped
    assert captured["function"] == "narrative_generation"
    # Facts are grounded into the user prompt, sorted for cache stability.
    user_msg = captured["request"].messages[0]["content"]
    assert "OHC" in user_msg
    assert "score: 82.0" in user_msg
    # Trusted first-party facts skip the injection scan.
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = report_service._ai_report_narrative("fedramp compliance assessment summary", {"controls_total": 325})

    assert out is None  # graceful degradation, never raises


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = report_service._ai_report_narrative("daily compliance posture digest", {"overall_score": 80})

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    # Simulate an environment where the LLM stack is absent entirely.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = report_service._ai_report_narrative("canvas assessment report", {"x": 1})

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_score": 90.0, "a_canvas": "ZIG", "m_delta": 3.5}

    report_service._ai_report_narrative("posture digest", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_canvas")
    pos_m = user_msg.index("m_delta")
    pos_z = user_msg.index("z_score")
    assert pos_a < pos_m < pos_z


def test_report_kind_appears_in_prompt(monkeypatch):
    """The report_kind label must appear in the user message for model framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    report_service._ai_report_narrative(
        "AI-ify roadmap readiness report",
        {"roadmap_id": "rm-abc123", "readiness": 72.5, "opportunity_count": 14},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "AI-ify roadmap readiness report" in user_msg
    assert "roadmap_id" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for report narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    report_service._ai_report_narrative(
        "canvas assessment report", {"canvas_name": "OHC", "score": 88.0}
    )

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    report_service._ai_report_narrative(
        "fedramp compliance assessment summary",
        {"controls_total": 325, "controls_pass": 310},
    )

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


# ---------------------------------------------------------------------------
# Shared factory for deliver_* integration tests (aiify-opp-5840)
# ---------------------------------------------------------------------------

def _seq_conn(monkeypatch, results: list):
    """Patch get_connection() to return canned query results in call order.

    Each entry in ``results`` governs one execute() call:
    - dict  → fetchone() returns it, fetchall() returns []
    - list  → fetchone() returns None, fetchall() returns list
    - None  → both return empty
    """
    call_counter = {"n": 0}

    class _Cur:
        def __init__(self, r):
            self._r = r

        def fetchone(self):
            return self._r if not isinstance(self._r, (list, tuple)) else None

        def fetchall(self):
            return list(self._r) if isinstance(self._r, (list, tuple)) else []

    class _Conn:
        def execute(self, sql, params=()):
            r = results[call_counter["n"]] if call_counter["n"] < len(results) else None
            call_counter["n"] += 1
            return _Cur(r)

        def commit(self): pass
        def close(self): pass

    import tools.notification_service.report_service as rs
    monkeypatch.setattr(rs, "get_connection", lambda: _Conn())
    monkeypatch.setattr(rs, "sendmail", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "send", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "render_to_string", lambda *a, **kw: "plain")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "dispatch", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "emit", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# deliver_canvas_report integration tests
# ---------------------------------------------------------------------------

_CANVAS_RESULTS = [
    {
        "id": "assess-01",
        "design_id": "d-01",
        "score": 82.0,
        "cat1_findings": 0,
        "cat2_findings": 3,
        "cat3_findings": 7,
        "findings_json": None,
        "assessment_type": "full",
        "created_at": "2026-06-01T00:00:00",
    },
    {"score": 79.0},
    [],
]


def test_canvas_report_no_narrative_by_default(monkeypatch):
    """deliver_canvas_report returns narrative=None when ai_narrative=False."""
    _seq_conn(monkeypatch, _CANVAS_RESULTS)

    result = report_service.deliver_canvas_report(
        "OHC", "assess-01", "full", ["ops@example.com"], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_canvas_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _seq_conn(monkeypatch, _CANVAS_RESULTS)
    _fake_router(monkeypatch, content="OHC improved 3 pts; maintain remediation cadence.")

    result = report_service.deliver_canvas_report(
        "OHC", "assess-01", "full", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "OHC improved 3 pts; maintain remediation cadence."
    assert result["score"] == 82.0


def test_canvas_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_canvas_report degrades to narrative=None when LLM raises."""
    _seq_conn(monkeypatch, _CANVAS_RESULTS)
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = report_service.deliver_canvas_report(
        "OHC", "assess-01", "full", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


# ---------------------------------------------------------------------------
# deliver_assessment_summary integration tests
# ---------------------------------------------------------------------------

_POAM_RESULTS = [
    {"open_cnt": 5, "closed_cnt": 20, "due_soon": 2},
]


def test_assessment_summary_no_narrative_by_default(monkeypatch):
    """deliver_assessment_summary returns narrative=None when ai_narrative=False."""
    _seq_conn(monkeypatch, _POAM_RESULTS)

    result = report_service.deliver_assessment_summary("poam", "proj-01", [], [])
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_assessment_summary_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _seq_conn(monkeypatch, _POAM_RESULTS)
    _fake_router(monkeypatch, content="5 open POA&M items; address 2 due within 30 days first.")

    result = report_service.deliver_assessment_summary(
        "poam", "proj-01", [], [], ai_narrative=True
    )
    assert result["narrative"] == "5 open POA&M items; address 2 due within 30 days first."
    assert result["framework"] == "poam"


def test_assessment_summary_narrative_none_on_llm_failure(monkeypatch):
    """deliver_assessment_summary degrades to narrative=None when LLM raises."""
    _seq_conn(monkeypatch, _POAM_RESULTS)
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = report_service.deliver_assessment_summary(
        "poam", "proj-01", [], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


# ---------------------------------------------------------------------------
# deliver_posture_digest integration tests
# ---------------------------------------------------------------------------

_DIGEST_RESULTS = [
    [{"canvas_name": "OHC", "avg_score": 82.0, "latest": "2026-06-01"}],
    {"overall": 82.0},
    {"overall": 79.0},
]


def test_posture_digest_no_narrative_by_default(monkeypatch):
    """deliver_posture_digest returns narrative=None when ai_narrative=False."""
    _seq_conn(monkeypatch, _DIGEST_RESULTS)

    result = report_service.deliver_posture_digest(
        "daily", {"date": "2026-06-02"}, [], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_posture_digest_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _seq_conn(monkeypatch, _DIGEST_RESULTS)
    _fake_router(monkeypatch, content="Overall posture at 82/100; 3-pt weekly gain sustained.")

    result = report_service.deliver_posture_digest(
        "daily", {"date": "2026-06-02"}, [], [], ai_narrative=True
    )
    assert result["narrative"] == "Overall posture at 82/100; 3-pt weekly gain sustained."
    assert result["overall_score"] == 82.0


def test_posture_digest_narrative_none_on_llm_failure(monkeypatch):
    """deliver_posture_digest degrades to narrative=None when LLM raises."""
    _seq_conn(monkeypatch, _DIGEST_RESULTS)
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = report_service.deliver_posture_digest(
        "daily", {"date": "2026-06-02"}, [], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


# ---------------------------------------------------------------------------
# deliver_aiify_roadmap_report integration tests
# ---------------------------------------------------------------------------

_ROADMAP_RESULTS = [
    {
        "roadmap_id": "rm-test01",
        "scan_id": 5,
        "phases_json": "{}",
        "created_at": "2026-06-01T00:00:00",
    },
    [
        {
            "function_name": "send_alert",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "composite_score": 0.82,
            "value_score": 0.90,
            "feasibility_score": 0.85,
        },
    ],
    {"overall_ai_readiness": 74.5, "status": "complete", "input_ref": "tools/"},
]


def test_roadmap_report_no_narrative_by_default(monkeypatch):
    """deliver_aiify_roadmap_report returns narrative=None when ai_narrative=False."""
    _seq_conn(monkeypatch, _ROADMAP_RESULTS)

    result = report_service.deliver_aiify_roadmap_report("rm-test01", [], [])
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_roadmap_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _seq_conn(monkeypatch, _ROADMAP_RESULTS)
    _fake_router(
        monkeypatch,
        content="Roadmap rm-test01 shows strong AI readiness; start with db_render_notify_chain.",
    )

    result = report_service.deliver_aiify_roadmap_report(
        "rm-test01", [], [], ai_narrative=True
    )
    assert result["narrative"] == (
        "Roadmap rm-test01 shows strong AI readiness; start with db_render_notify_chain."
    )
    assert result["readiness"] == 74.5
    assert result["opportunity_count"] == 1


def test_roadmap_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_aiify_roadmap_report degrades to narrative=None when LLM raises."""
    _seq_conn(monkeypatch, _ROADMAP_RESULTS)
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = report_service.deliver_aiify_roadmap_report(
        "rm-test01", [], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


# ---------------------------------------------------------------------------
# deliver_aiify_scan_report integration tests
# ---------------------------------------------------------------------------

_SCAN_RESULTS = [
    {
        "scan_id": 38,
        "overall_ai_readiness": 78.5,
        "status": "complete",
        "input_ref": "tools/",
        "created_at": "2026-06-02T00:00:00",
    },
    [
        {"pattern_type": "db_render_notify_chain", "opp_count": 12, "avg_score": 0.77},
        {"pattern_type": "llm_classification", "opp_count": 5, "avg_score": 0.65},
    ],
    [],  # module_rows
    [],  # top_opps
]


def test_scan_report_no_narrative_by_default(monkeypatch):
    """deliver_aiify_scan_report returns narrative=None when ai_narrative=False."""
    _seq_conn(monkeypatch, _SCAN_RESULTS)

    result = report_service.deliver_aiify_scan_report(38, [], [])
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_scan_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _seq_conn(monkeypatch, _SCAN_RESULTS)
    _fake_router(monkeypatch, content="Scan complete; prioritize db_render_notify_chain.")

    result = report_service.deliver_aiify_scan_report(38, [], [], ai_narrative=True)
    assert result["narrative"] == "Scan complete; prioritize db_render_notify_chain."
    assert result["readiness"] == 78.5
    assert result["total_opportunities"] == 17  # 12 + 5


def test_scan_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_aiify_scan_report degrades to narrative=None when LLM raises."""
    _seq_conn(monkeypatch, _SCAN_RESULTS)
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = report_service.deliver_aiify_scan_report(38, [], [], ai_narrative=True)
    assert result["narrative"] is None
    assert result["status"] == "delivered"
