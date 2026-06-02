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
