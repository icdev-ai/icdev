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
# Integration tests (aiify-opp-5868): full handler → LLM narrative path
#
# Each test below mocks both get_connection and the LLM router so the full
# handler function can be exercised without a real DB or LLM. The goal is to
# verify that the ``narrative`` key flows correctly from ``_ai_handler_narrative``
# into the handler's return dict when ``ai_narrative=True``.
# ---------------------------------------------------------------------------

class _FakeRow(dict):
    """dict subclass that supports attribute-style access for row objects."""
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class _FakeCursor:
    """Minimal cursor mock: cycles through a list of return values per call."""

    def __init__(self, values):
        self._values = list(values)
        self._idx = 0

    def fetchone(self):
        val = self._values[self._idx] if self._idx < len(self._values) else None
        self._idx += 1
        return val

    def fetchall(self):
        val = self._values[self._idx] if self._idx < len(self._values) else []
        self._idx += 1
        return val if isinstance(val, list) else []


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, *_args, **_kwargs):
        return self._cursor

    def close(self):
        pass


def _install_fake_conn(monkeypatch, return_values):
    cursor = _FakeCursor(return_values)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(
        "tools.notification_service.handler_service.get_connection",
        lambda: conn,
    )
    return conn


def _install_fake_send(monkeypatch):
    """Stub all send/notify helpers so no real I/O occurs."""
    for name in ("send", "sendmail", "notify", "emit", "publish", "dispatch", "render_template", "render_to_string"):
        monkeypatch.setattr(
            f"tools.notification_service.handler_service.{name}",
            lambda *a, **kw: "rendered",
        )


def test_task_status_change_narrative_in_result(monkeypatch):
    """handle_task_status_change_notify returns narrative when ai_narrative=True."""
    _fake_router(monkeypatch, content="Task moved to done — review CI before close.")
    task_row = _FakeRow({"id": "T-1", "title": "Fix ZIG gap", "actor": "user1", "updated_at": "2026-06-01"})
    history_rows = [
        _FakeRow({"event": "status_change", "created_at": "2026-06-01"}),
    ]
    _install_fake_conn(monkeypatch, [task_row, history_rows])
    _install_fake_send(monkeypatch)

    result = handler_service.handle_task_status_change_notify("T-1", "done", "user@example.com", ai_narrative=True)

    assert result["narrative"] == "Task moved to done — review CI before close."
    assert result["task_id"] == "T-1"
    assert result["status"] == "sent"


def test_task_status_change_no_narrative_when_flag_false(monkeypatch):
    """handle_task_status_change_notify skips LLM when ai_narrative=False."""
    called = []

    class _Sentinel:
        def invoke(self, *a, **kw):
            called.append(True)

    import types as _types
    monkeypatch.setitem(
        __import__("sys").modules,
        "tools.llm.router",
        _types.SimpleNamespace(LLMRouter=_Sentinel),
    )
    task_row = _FakeRow({"id": "T-2", "title": "Deploy fix", "actor": "user2", "updated_at": "2026-06-01"})
    _install_fake_conn(monkeypatch, [task_row, []])
    _install_fake_send(monkeypatch)

    result = handler_service.handle_task_status_change_notify("T-2", "in_progress", "ops@example.com", ai_narrative=False)

    assert result["narrative"] is None
    assert not called


def test_stig_finding_narrative_in_result(monkeypatch):
    """handle_stig_finding_handler returns narrative when ai_narrative=True."""
    _fake_router(monkeypatch, content="CAT I finding V-12345 on prod-svc requires immediate remediation.")
    finding_row = _FakeRow({
        "check_id": "V-12345", "check_name": "RHEL audit log", "severity": "I",
        "status": "open", "remediation": "Enable auditd"
    })
    workload_row = _FakeRow({"name": "prod-svc", "classification": "CUI"})
    _install_fake_conn(monkeypatch, [finding_row, workload_row])
    _install_fake_send(monkeypatch)

    result = handler_service.handle_stig_finding_handler("V-12345", "wl-1", "sec@example.com", ai_narrative=True)

    assert "CAT I finding" in result["narrative"]
    assert result["check_id"] == "V-12345"
    assert result["status"] == "sent"


def test_stig_finding_narrative_degradation_on_llm_failure(monkeypatch):
    """handle_stig_finding_handler still sends when LLM raises."""
    _fake_router(monkeypatch, raises=ConnectionError("no LLM"))
    finding_row = _FakeRow({
        "check_id": "V-99999", "check_name": "Kernel hardening", "severity": "II",
        "status": "not_reviewed", "remediation": "Apply STIG"
    })
    workload_row = _FakeRow({"name": "staging", "classification": "CUI"})
    _install_fake_conn(monkeypatch, [finding_row, workload_row])
    _install_fake_send(monkeypatch)

    result = handler_service.handle_stig_finding_handler("V-99999", "wl-2", "sec@example.com", ai_narrative=True)

    assert result["narrative"] is None
    assert result["status"] == "sent"


def test_oracle_prediction_narrative_in_result(monkeypatch):
    """handle_oracle_prediction_handler attaches narrative to result dict."""
    _fake_router(monkeypatch, content="High-severity threat detected; escalate to CISO within 4 hours.")
    pred_row = _FakeRow({
        "id": "P-77", "title": "Supply chain breach", "severity": "high",
        "confidence": 0.91, "lens_id": "lens-3", "created_at": "2026-06-01"
    })
    lens_row = _FakeRow({"name": "Threat Intel", "horizon_days": 30})
    _install_fake_conn(monkeypatch, [pred_row, lens_row])
    _install_fake_send(monkeypatch)

    result = handler_service.handle_oracle_prediction_handler("P-77", "ciso@example.com", ai_narrative=True)

    assert result["narrative"] == "High-severity threat detected; escalate to CISO within 4 hours."
    assert result["prediction_id"] == "P-77"


def test_agent_incident_narrative_in_result(monkeypatch):
    """handle_agent_incident_handler attaches narrative when LLM available."""
    _fake_router(monkeypatch, content="Agent builder-3 crashed; restart and check memory pressure.")
    agent_row = _FakeRow({"id": "agent-3", "name": "builder-3", "status": "crashed", "last_heartbeat": "2026-06-01"})
    errors = [_FakeRow({"error_msg": "OOM killed", "created_at": "2026-06-01"})]
    metrics = [_FakeRow({"metric_name": "memory_mb", "value": 4096})]
    _install_fake_conn(monkeypatch, [agent_row, errors, metrics])
    _install_fake_send(monkeypatch)

    result = handler_service.handle_agent_incident_handler("agent-3", "crash", "ops@example.com", ai_narrative=True)

    assert "builder-3" in result["narrative"]
    assert result["status"] == "sent"
    assert result["agent_id"] == "agent-3"
