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


# ---------------------------------------------------------------------------
# deliver_aiify_phase_report integration tests (aiify-opp-5840)
# ---------------------------------------------------------------------------

def _fake_phase_conn(monkeypatch, *, opp_rows=None, pattern_rows=()):
    """Patch get_connection() to return a stub for phase report queries."""
    rows_by_call = [opp_rows if opp_rows is not None else [], pattern_rows]
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
            result = rows_by_call[call_counter["n"]] if call_counter["n"] < len(rows_by_call) else []
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


def _make_phase_opp_rows():
    return [
        {
            "opportunity_id": 5840,
            "function_name": "<unknown>",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "module_path": "tools/notification_service/report_service.py",
            "composite_score": 0.7769,
            "value_score": 0.922,
            "feasibility_score": 0.82,
        },
        {
            "opportunity_id": 5841,
            "function_name": "deliver_canvas_report",
            "pattern_type": "db_render_notify_chain",
            "ai_paradigm": "llm_generation",
            "module_path": "tools/notification_service/report_service.py",
            "composite_score": 0.72,
            "value_score": 0.88,
            "feasibility_score": 0.80,
        },
    ]


def _make_phase_pattern_rows():
    return [
        {"pattern_type": "db_render_notify_chain", "opp_count": 2, "avg_score": 0.748},
    ]


def test_phase_report_no_narrative_by_default(monkeypatch):
    """deliver_aiify_phase_report returns narrative=None when ai_narrative=False."""
    _fake_phase_conn(
        monkeypatch,
        opp_rows=_make_phase_opp_rows(),
        pattern_rows=_make_phase_pattern_rows(),
    )

    result = report_service.deliver_aiify_phase_report(
        "rm-843f22be0d", "Phase 1 — Quick Wins", ["ops@example.com"], []
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"
    assert result["opportunity_count"] == 2


def test_phase_report_narrative_attached_when_llm_available(monkeypatch):
    """narrative contains LLM text when ai_narrative=True and LLM succeeds."""
    _fake_phase_conn(
        monkeypatch,
        opp_rows=_make_phase_opp_rows(),
        pattern_rows=_make_phase_pattern_rows(),
    )
    _fake_router(monkeypatch, content="Phase 1 shows strong db_render_notify_chain potential.")

    result = report_service.deliver_aiify_phase_report(
        "rm-843f22be0d", "Phase 1 — Quick Wins", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] == "Phase 1 shows strong db_render_notify_chain potential."
    assert result["opportunity_count"] == 2
    assert result["phase"] == "Phase 1 — Quick Wins"


def test_phase_report_narrative_none_on_llm_failure(monkeypatch):
    """deliver_aiify_phase_report degrades to narrative=None when LLM raises."""
    _fake_phase_conn(
        monkeypatch,
        opp_rows=_make_phase_opp_rows(),
        pattern_rows=_make_phase_pattern_rows(),
    )
    _fake_router(monkeypatch, raises=RuntimeError("provider unavailable"))

    result = report_service.deliver_aiify_phase_report(
        "rm-843f22be0d", "Phase 1 — Quick Wins", ["ops@example.com"], [], ai_narrative=True
    )
    assert result["narrative"] is None
    assert result["status"] == "delivered"
