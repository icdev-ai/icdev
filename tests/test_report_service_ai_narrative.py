# CUI // SP-CTI
"""Tests for the AI-ified executive summary in the report service (aiify-opp-5539, aiify-opp-5596).

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
# deliver_aiify_scan_report integration tests (aiify-opp-5804)
# ---------------------------------------------------------------------------

def _fake_conn(monkeypatch, *, scan_row=None, pattern_rows=(), module_rows=(), top_opps=()):
    """Patch get_connection() to return a stub with canned query results."""
    import types

    rows_by_call = [scan_row, pattern_rows, module_rows, top_opps]
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
            result = rows_by_call[call_counter["n"]] if call_counter["n"] < len(rows_by_call) else None
            call_counter["n"] += 1
            return _FakeCursor(result)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = _FakeConn()

    import tools.notification_service.report_service as rs

    monkeypatch.setattr(rs, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(rs, "sendmail", lambda **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "publish", lambda *a, **kw: None)
    return fake_conn


def _make_scan_row(scan_id=37, readiness=78.5, status="complete", input_ref="tools/"):
    return {
        "scan_id": scan_id,
        "overall_ai_readiness": readiness,
        "status": status,
        "input_ref": input_ref,
        "created_at": "2026-06-02T00:00:00",
    }


def _make_pattern_rows():
    return [
        {"pattern_type": "db_render_notify_chain", "opp_count": 12, "avg_score": 0.77},
        {"pattern_type": "llm_classification", "opp_count": 5, "avg_score": 0.65},
    ]


def test_scan_report_no_narrative_by_default(monkeypatch):
    """deliver_aiify_scan_report must return narrative=None when ai_narrative=False."""
    _fake_conn(
        monkeypatch,
        scan_row=_make_scan_row(),
        pattern_rows=_make_pattern_rows(),
        module_rows=[],
        top_opps=[],
    )

    result = report_service.deliver_aiify_scan_report(37, ["ops@example.com"], [])
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_scan_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_conn(
        monkeypatch,
        scan_row=_make_scan_row(),
        pattern_rows=_make_pattern_rows(),
        module_rows=[],
        top_opps=[],
    )
    _fake_router(monkeypatch, content="Scan complete; prioritize db_render_notify_chain.")

    result = report_service.deliver_aiify_scan_report(
        37, ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "Scan complete; prioritize db_render_notify_chain."
    assert result["readiness"] == 78.5
    assert result["total_opportunities"] == 17  # 12 + 5


def test_scan_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_aiify_scan_report degrades to narrative=None when LLM raises."""
    _fake_conn(
        monkeypatch,
        scan_row=_make_scan_row(),
        pattern_rows=_make_pattern_rows(),
        module_rows=[],
        top_opps=[],
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_aiify_scan_report(
        37, ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"  # deterministic report still delivered


# ---------------------------------------------------------------------------
# deliver_aiify_opportunity_report integration tests (aiify-opp-5844)
# ---------------------------------------------------------------------------

def _fake_opp_conn(monkeypatch, *, opp_row=None, score_row=None, scan_row=None):
    """Patch get_connection() for opportunity-level report tests."""
    rows_by_call = [opp_row, score_row, scan_row]
    call_counter = {"n": 0}

    class _FakeCursor:
        def __init__(self, result):
            self._result = result

        def fetchone(self):
            return self._result

        def fetchall(self):
            return []

        def execute(self, *a):
            return self

    class _FakeConn:
        def execute(self, sql, params=()):
            result = rows_by_call[call_counter["n"]] if call_counter["n"] < len(rows_by_call) else None
            call_counter["n"] += 1
            return _FakeCursor(result)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = _FakeConn()
    import tools.notification_service.report_service as rs
    monkeypatch.setattr(rs, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(rs, "sendmail", lambda **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "publish", lambda *a, **kw: None)
    return fake_conn


def _make_opp_row(opportunity_id=5844, scan_id=38, roadmap_id="rm-843f22be0d"):
    return {
        "opportunity_id": opportunity_id,
        "scan_id": scan_id,
        "roadmap_id": roadmap_id,
        "function_name": "<unknown>",
        "module_path": "tools/notification_service/report_service.py",
        "pattern_type": "db_render_notify_chain",
        "ai_paradigm": "llm_generation",
        "phase": "Phase 1 — Quick Wins",
        "status": "open",
        "created_at": "2026-06-02T00:00:00",
    }


def _make_score_row():
    return {
        "composite_score": 0.7769,
        "value_score": 0.922,
        "feasibility_score": 0.82,
        "risk_score": 0.625,
    }


def _make_opp_scan_row():
    return {"overall_ai_readiness": 78.5, "status": "complete"}


def test_opportunity_report_no_narrative_by_default(monkeypatch):
    """deliver_aiify_opportunity_report returns narrative=None when ai_narrative=False."""
    _fake_opp_conn(
        monkeypatch,
        opp_row=_make_opp_row(),
        score_row=_make_score_row(),
        scan_row=_make_opp_scan_row(),
    )

    result = report_service.deliver_aiify_opportunity_report(5844, ["ops@example.com"], [])
    assert result["narrative"] is None
    assert result["status"] == "delivered"
    assert result["opportunity_id"] == 5844
    assert result["pattern_type"] == "db_render_notify_chain"


def test_opportunity_report_composite_score_in_result(monkeypatch):
    """composite_score must be surfaced in the return dict."""
    _fake_opp_conn(
        monkeypatch,
        opp_row=_make_opp_row(),
        score_row=_make_score_row(),
        scan_row=_make_opp_scan_row(),
    )

    result = report_service.deliver_aiify_opportunity_report(5844, [], [])
    assert result["composite_score"] == 0.7769


def test_opportunity_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_opp_conn(
        monkeypatch,
        opp_row=_make_opp_row(),
        score_row=_make_score_row(),
        scan_row=_make_opp_scan_row(),
    )
    _fake_router(monkeypatch, content="High-value db_render_notify_chain; implement LLM narrative first.")

    result = report_service.deliver_aiify_opportunity_report(
        5844, ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "High-value db_render_notify_chain; implement LLM narrative first."
    assert result["status"] == "delivered"


def test_opportunity_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_aiify_opportunity_report degrades to narrative=None when LLM raises."""
    _fake_opp_conn(
        monkeypatch,
        opp_row=_make_opp_row(),
        score_row=_make_score_row(),
        scan_row=_make_opp_scan_row(),
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_aiify_opportunity_report(
        5844, ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_opportunity_report_no_data_when_missing(monkeypatch):
    """Returns status=no_data when the opportunity row is absent."""
    _fake_opp_conn(monkeypatch, opp_row=None)

    result = report_service.deliver_aiify_opportunity_report(9999, [], [])
    assert result["status"] == "no_data"
    assert "9999" in result["rendered"]


def test_opportunity_report_rendered_contains_key_fields(monkeypatch):
    """Rendered text must include pattern_type, ai_paradigm, phase, and scores."""
    _fake_opp_conn(
        monkeypatch,
        opp_row=_make_opp_row(),
        score_row=_make_score_row(),
        scan_row=_make_opp_scan_row(),
    )

    result = report_service.deliver_aiify_opportunity_report(5844, [], [])
    rendered = result["rendered"]
    assert "db_render_notify_chain" in rendered
    assert "llm_generation" in rendered
    assert "Phase 1" in rendered
    assert "0.7769" in rendered


# ---------------------------------------------------------------------------
# deliver_canvas_report integration tests (aiify-opp-5842)
# ---------------------------------------------------------------------------

def _fake_conn_canvas(monkeypatch, *, assessment_row=None, prev_row=None, gate_rows=()):
    """Patch get_connection() for deliver_canvas_report with canned query results.

    Query call order mirrors the function body:
      0 — assessment_row (fetchone)
      1 — prev_row       (fetchone)
      2 — gate_rows      (fetchall)
    """
    rows_by_call = [assessment_row, prev_row, gate_rows]
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
            result = rows_by_call[call_counter["n"]] if call_counter["n"] < len(rows_by_call) else None
            call_counter["n"] += 1
            return _FakeCursor(result)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = _FakeConn()

    import tools.notification_service.report_service as rs

    monkeypatch.setattr(rs, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(rs, "sendmail", lambda **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "render_to_string", lambda *a, **kw: "plain text")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "publish", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "dispatch", lambda *a, **kw: None)
    return fake_conn


def _make_canvas_assessment_row(assessment_id="a-001", design_id="d-001", score=82.5):
    return {
        "id": assessment_id,
        "design_id": design_id,
        "score": score,
        "cat1_findings": 0,
        "cat2_findings": 2,
        "cat3_findings": 5,
        "findings_json": None,
        "assessment_type": "automated",
        "created_at": "2026-06-02T00:00:00",
    }


def test_canvas_report_no_narrative_by_default(monkeypatch):
    """deliver_canvas_report must return narrative=None when ai_narrative=False."""
    _fake_conn_canvas(
        monkeypatch,
        assessment_row=_make_canvas_assessment_row(),
        prev_row={"score": 80.0},
        gate_rows=[],
    )

    result = report_service.deliver_canvas_report(
        "OHC", "a-001", "assessment_complete", ["ops@example.com"], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_canvas_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_conn_canvas(
        monkeypatch,
        assessment_row=_make_canvas_assessment_row(score=82.5),
        prev_row={"score": 80.0},
        gate_rows=[],
    )
    _fake_router(monkeypatch, content="OHC posture improved; sustain remediation cadence.")

    result = report_service.deliver_canvas_report(
        "OHC", "a-001", "assessment_complete", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "OHC posture improved; sustain remediation cadence."
    assert result["score"] == 82.5
    assert result["delta"] == 2.5


def test_canvas_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_canvas_report degrades to narrative=None when LLM raises."""
    _fake_conn_canvas(
        monkeypatch,
        assessment_row=_make_canvas_assessment_row(),
        prev_row={"score": 80.0},
        gate_rows=[],
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_canvas_report(
        "OHC", "a-001", "assessment_complete", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


# ---------------------------------------------------------------------------
# deliver_posture_digest integration tests (aiify-opp-5879)
# ---------------------------------------------------------------------------

def _fake_conn_digest(monkeypatch, *, canvas_rows=(), overall_row=None, prev_overall_row=None):
    """Patch get_connection() for deliver_posture_digest.

    Query call order mirrors the function body:
      0 — canvas_rows      (fetchall)
      1 — overall_row      (fetchone)
      2 — prev_overall_row (fetchone)
    """
    rows_by_call = [canvas_rows, overall_row, prev_overall_row]
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
            result = rows_by_call[call_counter["n"]] if call_counter["n"] < len(rows_by_call) else None
            call_counter["n"] += 1
            return _FakeCursor(result)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = _FakeConn()
    import tools.notification_service.report_service as rs
    monkeypatch.setattr(rs, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(rs, "sendmail", lambda **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "emit", lambda *a, **kw: None)
    return fake_conn


def _make_canvas_rows():
    return [
        {"canvas_name": "OHC", "avg_score": 82.0, "latest": "2026-06-02T00:00:00"},
        {"canvas_name": "ZIG", "avg_score": 75.5, "latest": "2026-06-01T00:00:00"},
    ]


def test_posture_digest_no_narrative_by_default(monkeypatch):
    """deliver_posture_digest returns narrative=None when ai_narrative=False."""
    _fake_conn_digest(
        monkeypatch,
        canvas_rows=_make_canvas_rows(),
        overall_row={"overall": 79.0},
        prev_overall_row={"overall": 76.5},
    )

    result = report_service.deliver_posture_digest(
        "daily", {"date": "2026-06-02"}, ["ops@example.com"], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"
    assert result["digest_type"] == "daily"


def test_posture_digest_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_conn_digest(
        monkeypatch,
        canvas_rows=_make_canvas_rows(),
        overall_row={"overall": 79.0},
        prev_overall_row={"overall": 76.5},
    )
    _fake_router(monkeypatch, content="Overall posture improved 2.5 pts; OHC leads. Sustain remediation.")

    result = report_service.deliver_posture_digest(
        "daily", {"date": "2026-06-02"}, ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "Overall posture improved 2.5 pts; OHC leads. Sustain remediation."
    assert result["overall_score"] == 79.0
    assert result["delta_7d"] == 2.5
    assert result["canvas_count"] == 2


def test_posture_digest_narrative_none_on_llm_failure(monkeypatch):
    """deliver_posture_digest degrades to narrative=None when LLM raises."""
    _fake_conn_digest(
        monkeypatch,
        canvas_rows=_make_canvas_rows(),
        overall_row={"overall": 79.0},
        prev_overall_row={"overall": 76.5},
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_posture_digest(
        "weekly", {"week_start": "2026-05-26"}, ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_posture_digest_rendered_contains_scores(monkeypatch):
    """Rendered digest text must include overall_score and canvas names."""
    _fake_conn_digest(
        monkeypatch,
        canvas_rows=_make_canvas_rows(),
        overall_row={"overall": 79.0},
        prev_overall_row={"overall": 76.5},
    )

    result = report_service.deliver_posture_digest(
        "daily", {"date": "2026-06-02"}, [], []
    )
    rendered = result["rendered"]
    assert "79.0" in rendered
    assert "2026-06-02" in rendered


# ---------------------------------------------------------------------------
# deliver_aiify_roadmap_report integration tests (aiify-opp-5879)
# ---------------------------------------------------------------------------

def _fake_conn_roadmap(monkeypatch, *, roadmap_row=None, top_opps=(), scan_row=None):
    """Patch get_connection() for deliver_aiify_roadmap_report.

    Query call order mirrors the function body:
      0 — roadmap_row (fetchone)
      1 — top_opps    (fetchall)
      2 — scan_row    (fetchone)
    """
    rows_by_call = [roadmap_row, top_opps, scan_row]
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
            result = rows_by_call[call_counter["n"]] if call_counter["n"] < len(rows_by_call) else None
            call_counter["n"] += 1
            return _FakeCursor(result)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = _FakeConn()
    import tools.notification_service.report_service as rs
    monkeypatch.setattr(rs, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(rs, "sendmail", lambda **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "publish", lambda *a, **kw: None)
    return fake_conn


def _make_roadmap_row(roadmap_id="rm-55b580f97b", scan_id=39):
    return {
        "roadmap_id": roadmap_id,
        "scan_id": scan_id,
        "phases_json": '{"phases": []}',
        "created_at": "2026-06-02T00:00:00",
    }


def _make_roadmap_top_opps():
    return [
        {
            "function_name": "<unknown>",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "composite_score": 0.7769,
            "value_score": 0.922,
            "feasibility_score": 0.82,
        },
        {
            "function_name": "deliver_canvas_report",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "composite_score": 0.75,
            "value_score": 0.88,
            "feasibility_score": 0.80,
        },
    ]


def _make_roadmap_scan_row():
    return {"overall_ai_readiness": 82.0, "status": "complete", "input_ref": "tools/"}


def test_roadmap_report_no_narrative_by_default(monkeypatch):
    """deliver_aiify_roadmap_report returns narrative=None when ai_narrative=False."""
    _fake_conn_roadmap(
        monkeypatch,
        roadmap_row=_make_roadmap_row(),
        top_opps=_make_roadmap_top_opps(),
        scan_row=_make_roadmap_scan_row(),
    )

    result = report_service.deliver_aiify_roadmap_report(
        "rm-55b580f97b", ["ops@example.com"], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"
    assert result["roadmap_id"] == "rm-55b580f97b"


def test_roadmap_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_conn_roadmap(
        monkeypatch,
        roadmap_row=_make_roadmap_row(),
        top_opps=_make_roadmap_top_opps(),
        scan_row=_make_roadmap_scan_row(),
    )
    _fake_router(monkeypatch, content="Readiness at 82/100; prioritize db_render_notify_chain quick wins.")

    result = report_service.deliver_aiify_roadmap_report(
        "rm-55b580f97b", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "Readiness at 82/100; prioritize db_render_notify_chain quick wins."
    assert result["readiness"] == 82.0
    assert result["opportunity_count"] == 2


def test_roadmap_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_aiify_roadmap_report degrades to narrative=None when LLM raises."""
    _fake_conn_roadmap(
        monkeypatch,
        roadmap_row=_make_roadmap_row(),
        top_opps=_make_roadmap_top_opps(),
        scan_row=_make_roadmap_scan_row(),
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_aiify_roadmap_report(
        "rm-55b580f97b", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_roadmap_report_no_data_when_missing(monkeypatch):
    """Returns status=no_data when the roadmap row is absent."""
    _fake_conn_roadmap(monkeypatch, roadmap_row=None)

    result = report_service.deliver_aiify_roadmap_report("rm-missing", [], [])
    assert result["status"] == "no_data"
    assert "rm-missing" in result["rendered"]


def test_roadmap_report_rendered_contains_key_fields(monkeypatch):
    """Rendered text must include roadmap_id, readiness, opportunity count."""
    _fake_conn_roadmap(
        monkeypatch,
        roadmap_row=_make_roadmap_row(),
        top_opps=_make_roadmap_top_opps(),
        scan_row=_make_roadmap_scan_row(),
    )

    result = report_service.deliver_aiify_roadmap_report("rm-55b580f97b", [], [])
    rendered = result["rendered"]
    assert "rm-55b580f97b" in rendered
    assert "82.0" in rendered
    assert "db_render_notify_chain" in rendered


# ---------------------------------------------------------------------------
# deliver_assessment_summary integration tests (aiify-opp-5879)
# ---------------------------------------------------------------------------

def _fake_conn_summary(monkeypatch, *, data_row=None):
    """Patch get_connection() for deliver_assessment_summary (single DB query)."""
    call_counter = {"n": 0}

    class _FakeCursor:
        def __init__(self, result):
            self._result = result

        def fetchone(self):
            return self._result

        def fetchall(self):
            return []

        def execute(self, *a):
            return self

    class _FakeConn:
        def execute(self, sql, params=()):
            call_counter["n"] += 1
            return _FakeCursor(data_row)

        def commit(self):
            pass

        def close(self):
            pass

    fake_conn = _FakeConn()
    import tools.notification_service.report_service as rs
    monkeypatch.setattr(rs, "get_connection", lambda: fake_conn)
    monkeypatch.setattr(rs, "send", lambda **kw: None)
    monkeypatch.setattr(rs, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(rs, "notify", lambda *a, **kw: None)
    monkeypatch.setattr(rs, "publish", lambda *a, **kw: None)
    return fake_conn


def test_assessment_summary_no_narrative_by_default(monkeypatch):
    """deliver_assessment_summary returns narrative=None when ai_narrative=False."""
    _fake_conn_summary(
        monkeypatch,
        data_row={"total": 120, "naf": 100, "open_cnt": 5},
    )

    result = report_service.deliver_assessment_summary(
        "stig", "proj-001", ["ops@example.com"], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"
    assert result["framework"] == "stig"


def test_assessment_summary_narrative_attached_when_llm_available(monkeypatch):
    """narrative key contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_conn_summary(
        monkeypatch,
        data_row={"total": 325, "passed": 310},
    )
    _fake_router(monkeypatch, content="FedRAMP controls at 95% pass rate; close 15 open gaps to achieve full auth.")

    result = report_service.deliver_assessment_summary(
        "fedramp", "proj-001", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "FedRAMP controls at 95% pass rate; close 15 open gaps to achieve full auth."
    assert result["status"] == "delivered"


def test_assessment_summary_narrative_none_on_llm_failure(monkeypatch):
    """deliver_assessment_summary degrades to narrative=None when LLM raises."""
    _fake_conn_summary(
        monkeypatch,
        data_row={"open_cnt": 3, "closed_cnt": 12, "due_soon": 1},
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_assessment_summary(
        "poam", "proj-001", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_assessment_summary_rendered_contains_framework(monkeypatch):
    """Rendered text must include framework name and project_id."""
    _fake_conn_summary(
        monkeypatch,
        data_row={"total": 80, "naf": 70, "open_cnt": 2},
    )

    result = report_service.deliver_assessment_summary(
        "stig", "proj-stig-001", [], []
    )
    assert result["framework"] == "stig"
    assert result["project_id"] == "proj-stig-001"
    assert "STIG" in result["rendered"]
