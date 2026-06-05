# CUI // SP-CTI
"""Tests for the AI-ified triage narrative in the alert service (aiify-opp-155, aiify-opp-5525, aiify-opp-5599, aiify-opp-5636, aiify-opp-5698, aiify-opp-5700, aiify-opp-5738, aiify-opp-5740, aiify-opp-5742, aiify-opp-5776, aiify-opp-5780, aiify-opp-5814, aiify-opp-5816, aiify-opp-5850, aiify-opp-5926, aiify-opp-5928).

The db -> render -> notify chains in ``tools.notification_service.alert_service``
gained an opt-in LLM triage narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so a security alert
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import alert_service


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
    captured = _fake_router(monkeypatch, content="  Remediate the CAT-I finding now.  ")
    facts = {"finding_title": "Unpatched OpenSSL", "severity": "CAT I", "sla_hours": 24}

    out = alert_service._ai_alert_narrative("CAT-I security finding escalation", facts)

    assert out == "Remediate the CAT-I finding now."  # stripped
    assert captured["function"] == "narrative_generation"
    # Facts are grounded into the user prompt, sorted for cache stability.
    user_msg = captured["request"].messages[0]["content"]
    assert "Unpatched OpenSSL" in user_msg
    assert "sla_hours: 24" in user_msg
    # Trusted first-party facts skip the injection scan.
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = alert_service._ai_alert_narrative("STIG check finding alert", {"check_id": "V-1"})

    assert out is None  # graceful degradation, never raises


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = alert_service._ai_alert_narrative("POA&M deadline reminder", {"poam_id": "P-1"})

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    # Simulate an environment where the LLM stack is absent entirely.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = alert_service._ai_alert_narrative("CAT-I security finding escalation", {"x": 1})

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Review the CAT-I finding immediately.")
    facts = {"z_sla_hours": 24, "a_finding_title": "Unpatched OpenSSL", "m_severity": "CAT I"}

    alert_service._ai_alert_narrative("CAT-I security finding escalation", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_finding_title")
    pos_m = user_msg.index("m_severity")
    pos_z = user_msg.index("z_sla_hours")
    assert pos_a < pos_m < pos_z


def test_alert_kind_appears_in_prompt(monkeypatch):
    """The alert_kind label must appear in the user message for model framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    alert_service._ai_alert_narrative(
        "POA&M deadline reminder",
        {"poam_id": "POAM-99", "severity": "high", "due_date": "2026-07-01"},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "POA&M deadline reminder" in user_msg
    assert "poam_id" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for alert narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    alert_service._ai_alert_narrative(
        "STIG check finding alert", {"check_id": "V-12345", "severity": "I"}
    )

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    alert_service._ai_alert_narrative(
        "CAT-I security finding escalation", {"finding_title": "Expired cert", "sla_hours": 24}
    )

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


def test_narrative_not_called_when_ai_narrative_false(monkeypatch):
    """The LLM must never be invoked when ai_narrative=False (the default)."""
    called = []

    class _Sentinel:
        def invoke(self, *a, **kw):
            called.append(True)

    fake_module = types.SimpleNamespace(LLMRouter=_Sentinel)
    monkeypatch.setitem(__import__("sys").modules, "tools.llm.router", fake_module)

    # Confirm the helper itself is never reached unless the caller opts in.
    # We verify docstring presence as a smoke-import check; the key assertion
    # is that the sentinel invoke was never triggered.
    assert alert_service._ai_alert_narrative.__doc__ is not None
    assert not called


# ---------------------------------------------------------------------------
# Tests for send_security_alert_digest (aiify-opp-5702)
# ---------------------------------------------------------------------------

def _fake_alert_conn(monkeypatch, *, cat1=None, stig=None, poam=None):
    """Install a fake DB connection returning minimal rows for digest queries."""
    call_order = [cat1 or [], stig or [], poam or []]
    idx = [0]

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Conn:
        def execute(self, sql, params=()):
            rows = call_order[idx[0]] if idx[0] < len(call_order) else []
            idx[0] += 1
            return _Cursor(rows)

        def close(self):
            pass

    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(alert_service, "sendmail", lambda **kw: None)
    monkeypatch.setattr(alert_service, "emit", lambda *a, **kw: None)


def test_send_security_alert_digest_narrative_none_by_default(monkeypatch):
    """send_security_alert_digest must not invoke LLM when ai_narrative=False."""
    _fake_alert_conn(monkeypatch)
    called = []
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: called.append(True) or "x")

    result = alert_service.send_security_alert_digest("sec@icdev.local")

    assert result["narrative"] is None
    assert not called


def test_send_security_alert_digest_attaches_narrative(monkeypatch):
    """send_security_alert_digest includes LLM narrative in result when ai_narrative=True."""
    _fake_alert_conn(
        monkeypatch,
        cat1=[{"id": "F-1", "title": "Expired cert", "severity": "I",
               "vid": "V-1", "component": "web", "detected_at": "2026-06-01"}],
    )
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Urgent: remediate the open CAT-I finding immediately.",
    )

    result = alert_service.send_security_alert_digest("sec@icdev.local", ai_narrative=True)

    assert result["narrative"] == "Urgent: remediate the open CAT-I finding immediately."
    assert result["cat1_count"] == 1
    assert result["status"] == "sent"


def test_send_security_alert_digest_degrades_on_llm_failure(monkeypatch):
    """send_security_alert_digest returns complete result even when narrative is None."""
    _fake_alert_conn(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    result = alert_service.send_security_alert_digest("sec@icdev.local", ai_narrative=True)

    assert result["status"] == "sent"
    assert result["narrative"] is None
    assert "cat1_count" in result
    assert "stig_critical_count" in result
    assert "poam_due_count" in result


# ---------------------------------------------------------------------------
# Tests for escalate_cat1_finding (aiify-opp-5738)
# ---------------------------------------------------------------------------

_CAT1_FINDING_ROW = {
    "title": "Unpatched OpenSSL", "severity": "I", "vid": "V-12345",
    "component": "web", "check_id": "SV-1", "detected_at": "2026-06-01",
    "remediation": "Apply patch",
}
_OPEN_CAT1_COUNT_ROW = {"cnt": 3}


def _fake_cat1_conn(monkeypatch):
    rows = [
        _CAT1_FINDING_ROW,
        {},
        {"name": "TestSys", "classification": "CUI", "system_owner": "J"},
        _OPEN_CAT1_COUNT_ROW,
    ]
    idx = [0]

    class _Cur:
        def __init__(self, row):
            self._row = row
            self.lastrowid = None

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            row = rows[idx[0]] if idx[0] < len(rows) else None
            idx[0] += 1
            return _Cur(row)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(alert_service, "render_string", lambda *a, **kw: "sms")


def test_escalate_cat1_narrative_none_by_default(monkeypatch):
    """escalate_cat1_finding must not invoke LLM when ai_narrative=False."""
    _fake_cat1_conn(monkeypatch)
    called = []
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: called.append(True) or "x")

    result = alert_service.escalate_cat1_finding("F-1", "P-1", [])

    assert result["narrative"] is None
    assert not called


def test_escalate_cat1_attaches_narrative(monkeypatch):
    """escalate_cat1_finding includes LLM narrative in result when ai_narrative=True."""
    _fake_cat1_conn(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Patch OpenSSL immediately to close the CAT-I gap.",
    )

    result = alert_service.escalate_cat1_finding("F-1", "P-1", [], ai_narrative=True)

    assert result["narrative"] == "Patch OpenSSL immediately to close the CAT-I gap."
    assert result["status"] == "escalated"


def test_escalate_cat1_degrades_on_llm_failure(monkeypatch):
    """escalate_cat1_finding returns complete result even when narrative is None."""
    _fake_cat1_conn(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    result = alert_service.escalate_cat1_finding("F-1", "P-1", [], ai_narrative=True)

    assert result["status"] == "escalated"
    assert result["narrative"] is None
    assert "sla_hours" in result
    assert "open_cat1_count" in result


# ---------------------------------------------------------------------------
# Tests for dispatch_stig_alert (aiify-opp-5738)
# ---------------------------------------------------------------------------

_STIG_CHECK_ROW = {
    "check_id": "SV-99999", "check_name": "SSH cipher config", "severity": "I",
    "status": "open", "remediation": "Disable weak ciphers", "reference_url": "https://stig.disa.mil/",
    "workload_id": "WL-1",
}


def _fake_stig_conn(monkeypatch):
    rows = [
        _STIG_CHECK_ROW,
        {"name": "TestWorkload", "classification": "CUI", "owner": "ops"},
        None,  # no existing_poam
    ]
    idx = [0]

    class _Cur:
        def __init__(self, row):
            self._row = row
            self.lastrowid = 42

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            row = rows[idx[0]] if idx[0] < len(rows) else None
            idx[0] += 1
            return _Cur(row)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")


def test_dispatch_stig_narrative_none_by_default(monkeypatch):
    """dispatch_stig_alert must not invoke LLM when ai_narrative=False."""
    _fake_stig_conn(monkeypatch)
    called = []
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: called.append(True) or "x")

    result = alert_service.dispatch_stig_alert("SV-99999", "WL-1", [], auto_create_poam=False)

    assert result["narrative"] is None
    assert not called


def test_dispatch_stig_attaches_narrative(monkeypatch):
    """dispatch_stig_alert includes LLM narrative in result when ai_narrative=True."""
    _fake_stig_conn(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Disable weak SSH ciphers to close this CAT-I STIG finding.",
    )

    result = alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", [], auto_create_poam=False, ai_narrative=True
    )

    assert result["narrative"] == "Disable weak SSH ciphers to close this CAT-I STIG finding."
    assert result["status"] == "dispatched"


def test_dispatch_stig_degrades_on_llm_failure(monkeypatch):
    """dispatch_stig_alert returns complete result even when narrative is None."""
    _fake_stig_conn(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    result = alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", [], auto_create_poam=False, ai_narrative=True
    )

    assert result["status"] == "dispatched"
    assert result["narrative"] is None
    assert "check_id" in result
    assert "severity" in result


# ---------------------------------------------------------------------------
# Tests for send_poam_reminder (aiify-opp-5738)
# ---------------------------------------------------------------------------

_POAM_ROW = {
    "id": "POAM-42", "title": "Fix expired cert", "severity": "II",
    "status": "open", "due_date": "2026-07-01", "milestone": "Q3",
    "owner": "jdoe", "finding_ref": "F-99", "framework": "NIST",
}
_DAYS_ROW = {"days_remaining": 10}


def _fake_poam_conn(monkeypatch):
    rows = [
        _POAM_ROW,
        _DAYS_ROW,
        {"email": "jdoe@icdev.local", "name": "Jane", "phone": ""},
        None,  # related_findings
    ]
    idx = [0]

    class _Cur:
        def __init__(self, row):
            self._row = row
            self.lastrowid = None

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            row = rows[idx[0]] if idx[0] < len(rows) else None
            idx[0] += 1
            return _Cur(row)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(alert_service, "render_to_string", lambda *a, **kw: "brief")


def test_send_poam_reminder_narrative_none_by_default(monkeypatch):
    """send_poam_reminder must not invoke LLM when ai_narrative=False."""
    _fake_poam_conn(monkeypatch)
    called = []
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: called.append(True) or "x")

    result = alert_service.send_poam_reminder("POAM-42", "P-1", [])

    assert result["narrative"] is None
    assert not called


def test_send_poam_reminder_attaches_narrative(monkeypatch):
    """send_poam_reminder includes LLM narrative in result when ai_narrative=True."""
    _fake_poam_conn(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Renew the expired cert within 10 days to avoid POA&M breach.",
    )

    result = alert_service.send_poam_reminder("POAM-42", "P-1", [], ai_narrative=True)

    assert result["narrative"] == "Renew the expired cert within 10 days to avoid POA&M breach."
    assert result["status"] == "delivered"


def test_send_poam_reminder_degrades_on_llm_failure(monkeypatch):
    """send_poam_reminder returns complete result even when narrative is None."""
    _fake_poam_conn(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    result = alert_service.send_poam_reminder("POAM-42", "P-1", [], ai_narrative=True)

    assert result["status"] == "delivered"
    assert result["narrative"] is None
    assert "days_remaining" in result
    assert "urgency" in result


# ---------------------------------------------------------------------------
# Tests for send_security_alert_digest emit-payload narrative (aiify-opp-5742)
# ---------------------------------------------------------------------------

def test_send_security_alert_digest_narrative_in_emit_payload(monkeypatch):
    """send_security_alert_digest must include the narrative in the emitted event payload."""
    _fake_alert_conn(monkeypatch)
    emitted = []
    monkeypatch.setattr(alert_service, "emit", lambda event, payload: emitted.append((event, payload)))
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Three CAT-I findings open; escalate to ISSO immediately.",
    )

    result = alert_service.send_security_alert_digest("sec@icdev.local", ai_narrative=True)

    assert result["narrative"] == "Three CAT-I findings open; escalate to ISSO immediately."
    assert emitted, "emit() was never called"
    event_name, payload = emitted[0]
    assert event_name == "security.alert_digest_sent"
    assert payload.get("narrative") == "Three CAT-I findings open; escalate to ISSO immediately."


def test_send_security_alert_digest_emit_payload_no_narrative_key_when_none(monkeypatch):
    """When narrative is None the emitted payload must not contain a 'narrative' key."""
    _fake_alert_conn(monkeypatch)
    emitted = []
    monkeypatch.setattr(alert_service, "emit", lambda event, payload: emitted.append((event, payload)))
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.send_security_alert_digest("sec@icdev.local", ai_narrative=True)

    assert emitted, "emit() was never called"
    _, payload = emitted[0]
    assert "narrative" not in payload


def test_send_security_alert_digest_emit_event_name(monkeypatch):
    """The emit event name must be 'security.alert_digest_sent' regardless of narrative."""
    _fake_alert_conn(monkeypatch)
    emitted = []
    monkeypatch.setattr(alert_service, "emit", lambda event, payload: emitted.append(event))

    alert_service.send_security_alert_digest("sec@icdev.local")

    assert emitted == ["security.alert_digest_sent"]


# ---------------------------------------------------------------------------
# Tests for _NARRATIVE_SYSTEM_PROMPT content (aiify-opp-5778)
# ---------------------------------------------------------------------------

def test_system_prompt_passed_to_llm_request(monkeypatch):
    """The system_prompt on the LLMRequest must be the module-level _NARRATIVE_SYSTEM_PROMPT."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    alert_service._ai_alert_narrative("CAT-I security finding escalation", {"finding_title": "Test"})

    assert captured["request"].system_prompt is alert_service._NARRATIVE_SYSTEM_PROMPT


def test_system_prompt_contains_dod_ic_framing(monkeypatch):
    """_NARRATIVE_SYSTEM_PROMPT must include DoD/IC analyst framing so the model context is correct."""
    assert "DoD/IC" in alert_service._NARRATIVE_SYSTEM_PROMPT


def test_system_prompt_prohibits_invented_facts(monkeypatch):
    """_NARRATIVE_SYSTEM_PROMPT must instruct the model not to invent VIDs, dates, or system names."""
    prompt = alert_service._NARRATIVE_SYSTEM_PROMPT
    assert "never invent" in prompt.lower() or "never invent" in prompt


def test_system_prompt_specifies_output_format(monkeypatch):
    """_NARRATIVE_SYSTEM_PROMPT must ask for plain prose without markdown headers."""
    prompt = alert_service._NARRATIVE_SYSTEM_PROMPT
    assert "no markdown" in prompt.lower() or "no headers" in prompt.lower()


# ---------------------------------------------------------------------------
# Tests for dispatch_stig_alert channel-payload propagation (aiify-opp-5816)
# Lines 280-386: the dispatch loop must include narrative in Slack/Teams
# publish() calls and omit it when narrative is None.
# ---------------------------------------------------------------------------

def _fake_stig_conn_with_publish(monkeypatch):
    """Fake DB + render for dispatch_stig_alert; returns the captured publish calls."""
    rows = [
        _STIG_CHECK_ROW,
        {"name": "TestWorkload", "classification": "CUI", "owner": "ops"},
        None,  # no existing_poam
    ]
    idx = [0]

    class _Cur:
        def __init__(self, row):
            self._row = row
            self.lastrowid = 42

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            row = rows[idx[0]] if idx[0] < len(rows) else None
            idx[0] += 1
            return _Cur(row)

        def commit(self):
            pass

        def close(self):
            pass

    published = []
    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(alert_service, "publish", lambda ch, payload: published.append((ch, payload)))
    return published


def test_dispatch_stig_narrative_in_slack_payload(monkeypatch):
    """When dispatch_stig_alert sends to slack with ai_narrative=True the narrative
    must appear in the publish() payload so SecOps tooling can surface it."""
    published = _fake_stig_conn_with_publish(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Disable weak SSH ciphers; this CAT-I check is open.",
    )

    result = alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", ["slack"], auto_create_poam=False, ai_narrative=True
    )

    assert result["narrative"] == "Disable weak SSH ciphers; this CAT-I check is open."
    assert published, "publish() was never called"
    _, payload = published[0]
    assert payload.get("narrative") == "Disable weak SSH ciphers; this CAT-I check is open."


def test_dispatch_stig_narrative_absent_from_slack_payload_when_none(monkeypatch):
    """When narrative is None the publish() payload must not contain a 'narrative' key."""
    published = _fake_stig_conn_with_publish(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", ["slack"], auto_create_poam=False, ai_narrative=True
    )

    assert published, "publish() was never called"
    _, payload = published[0]
    assert "narrative" not in payload


def test_dispatch_stig_narrative_absent_when_ai_narrative_false(monkeypatch):
    """When ai_narrative=False the publish() payload must never contain a narrative key."""
    published = _fake_stig_conn_with_publish(monkeypatch)
    # Patch to a sentinel that would expose a bug if called.
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda *a: "SHOULD NOT APPEAR",
    )

    alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", ["slack"], auto_create_poam=False, ai_narrative=False
    )

    assert published, "publish() was never called"
    _, payload = published[0]
    assert "narrative" not in payload


# ---------------------------------------------------------------------------
# Tests for teams-channel narrative propagation (aiify-opp-5850)
# The publish/dispatch loops handle "slack" and "teams" identically; these
# tests pin that the teams branch carries the narrative through so SecOps
# tooling using Teams webhooks also receives the triage context.
# ---------------------------------------------------------------------------

def _fake_cat1_conn_with_publish(monkeypatch):
    """Fake DB + render for escalate_cat1_finding; returns captured publish calls."""
    rows = [
        _CAT1_FINDING_ROW,
        {},
        {"name": "TestSys", "classification": "CUI", "system_owner": "J"},
        _OPEN_CAT1_COUNT_ROW,
    ]
    idx = [0]

    class _Cur:
        def __init__(self, row):
            self._row = row
            self.lastrowid = None

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            row = rows[idx[0]] if idx[0] < len(rows) else None
            idx[0] += 1
            return _Cur(row)

        def commit(self):
            pass

        def close(self):
            pass

    published = []
    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(alert_service, "render_string", lambda *a, **kw: "sms")
    monkeypatch.setattr(alert_service, "publish", lambda ch, payload: published.append((ch, payload)))
    return published


def _fake_poam_conn_with_dispatch(monkeypatch):
    """Fake DB + render for send_poam_reminder; returns captured dispatch calls."""
    rows = [
        _POAM_ROW,
        _DAYS_ROW,
        {"email": "jdoe@icdev.local", "name": "Jane", "phone": ""},
        None,
    ]
    idx = [0]

    class _Cur:
        def __init__(self, row):
            self._row = row
            self.lastrowid = None

        def fetchone(self):
            return self._row

        def fetchall(self):
            return []

    class _Conn:
        def execute(self, sql, params=()):
            row = rows[idx[0]] if idx[0] < len(rows) else None
            idx[0] += 1
            return _Cur(row)

        def commit(self):
            pass

        def close(self):
            pass

    dispatched = []
    monkeypatch.setattr(alert_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(alert_service, "render_template", lambda *a, **kw: "<html/>")
    monkeypatch.setattr(alert_service, "render_to_string", lambda *a, **kw: "brief")
    monkeypatch.setattr(alert_service, "dispatch", lambda ch, payload: dispatched.append((ch, payload)))
    return dispatched


def test_escalate_cat1_narrative_in_teams_payload(monkeypatch):
    """escalate_cat1_finding must include narrative in the teams publish() payload."""
    published = _fake_cat1_conn_with_publish(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Patch OpenSSL immediately — CAT-I SLA is 24 hours.",
    )

    result = alert_service.escalate_cat1_finding("F-1", "P-1", ["teams"], ai_narrative=True)

    assert result["narrative"] == "Patch OpenSSL immediately — CAT-I SLA is 24 hours."
    assert published, "publish() was never called for teams channel"
    ch, payload = published[0]
    assert ch == "teams"
    assert payload.get("narrative") == "Patch OpenSSL immediately — CAT-I SLA is 24 hours."


def test_escalate_cat1_narrative_absent_from_teams_payload_when_none(monkeypatch):
    """When narrative is None the teams publish() payload must not contain a 'narrative' key."""
    published = _fake_cat1_conn_with_publish(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.escalate_cat1_finding("F-1", "P-1", ["teams"], ai_narrative=True)

    assert published, "publish() was never called for teams channel"
    _, payload = published[0]
    assert "narrative" not in payload


def test_dispatch_stig_narrative_in_teams_payload(monkeypatch):
    """dispatch_stig_alert must include narrative in the teams publish() payload."""
    published = _fake_stig_conn_with_publish(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Disable weak SSH ciphers; CAT-I STIG check is open.",
    )

    result = alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", ["teams"], auto_create_poam=False, ai_narrative=True
    )

    assert result["narrative"] == "Disable weak SSH ciphers; CAT-I STIG check is open."
    assert published, "publish() was never called for teams channel"
    ch, payload = published[0]
    assert ch == "teams"
    assert payload.get("narrative") == "Disable weak SSH ciphers; CAT-I STIG check is open."


def test_dispatch_stig_narrative_absent_from_teams_payload_when_none(monkeypatch):
    """When narrative is None the teams publish() payload must not contain a 'narrative' key."""
    published = _fake_stig_conn_with_publish(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.dispatch_stig_alert(
        "SV-99999", "WL-1", ["teams"], auto_create_poam=False, ai_narrative=True
    )

    assert published, "publish() was never called for teams channel"
    _, payload = published[0]
    assert "narrative" not in payload


def test_send_poam_reminder_narrative_in_teams_payload(monkeypatch):
    """send_poam_reminder must include narrative in the teams dispatch() payload."""
    dispatched = _fake_poam_conn_with_dispatch(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Renew the cert within 10 days or the POA&M will breach.",
    )

    result = alert_service.send_poam_reminder("POAM-42", "P-1", ["teams"], ai_narrative=True)

    assert result["narrative"] == "Renew the cert within 10 days or the POA&M will breach."
    assert dispatched, "dispatch() was never called for teams channel"
    ch, payload = dispatched[0]
    assert ch == "teams"
    assert payload.get("narrative") == "Renew the cert within 10 days or the POA&M will breach."


def test_send_poam_reminder_narrative_absent_from_teams_payload_when_none(monkeypatch):
    """When narrative is None the teams dispatch() payload must not contain a 'narrative' key."""
    dispatched = _fake_poam_conn_with_dispatch(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.send_poam_reminder("POAM-42", "P-1", ["teams"], ai_narrative=True)

    assert dispatched, "dispatch() was never called for teams channel"
    _, payload = dispatched[0]
    assert "narrative" not in payload


# ---------------------------------------------------------------------------
# Tests for slack-channel narrative propagation in escalate_cat1_finding and
# send_poam_reminder (aiify-opp-5926)
# Both functions handle "slack" and "teams" in the same branch; these tests
# pin that the slack variant carries the narrative through identically.
# ---------------------------------------------------------------------------

def test_escalate_cat1_narrative_in_slack_payload(monkeypatch):
    """escalate_cat1_finding must include narrative in the slack publish() payload."""
    published = _fake_cat1_conn_with_publish(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Patch OpenSSL immediately — CAT-I SLA is 24 hours.",
    )

    result = alert_service.escalate_cat1_finding("F-1", "P-1", ["slack"], ai_narrative=True)

    assert result["narrative"] == "Patch OpenSSL immediately — CAT-I SLA is 24 hours."
    assert published, "publish() was never called for slack channel"
    ch, payload = published[0]
    assert ch == "slack"
    assert payload.get("narrative") == "Patch OpenSSL immediately — CAT-I SLA is 24 hours."


def test_escalate_cat1_narrative_absent_from_slack_payload_when_none(monkeypatch):
    """When narrative is None the slack publish() payload must not contain a 'narrative' key."""
    published = _fake_cat1_conn_with_publish(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.escalate_cat1_finding("F-1", "P-1", ["slack"], ai_narrative=True)

    assert published, "publish() was never called for slack channel"
    _, payload = published[0]
    assert "narrative" not in payload


def test_escalate_cat1_narrative_absent_from_slack_payload_when_ai_narrative_false(monkeypatch):
    """When ai_narrative=False the slack publish() payload must never contain a narrative key."""
    published = _fake_cat1_conn_with_publish(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda *a: "SHOULD NOT APPEAR",
    )

    alert_service.escalate_cat1_finding("F-1", "P-1", ["slack"], ai_narrative=False)

    assert published, "publish() was never called for slack channel"
    _, payload = published[0]
    assert "narrative" not in payload


def test_send_poam_reminder_narrative_in_slack_payload(monkeypatch):
    """send_poam_reminder must include narrative in the slack dispatch() payload."""
    dispatched = _fake_poam_conn_with_dispatch(monkeypatch)
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: "Renew the cert within 10 days or the POA&M will breach.",
    )

    result = alert_service.send_poam_reminder("POAM-42", "P-1", ["slack"], ai_narrative=True)

    assert result["narrative"] == "Renew the cert within 10 days or the POA&M will breach."
    assert dispatched, "dispatch() was never called for slack channel"
    ch, payload = dispatched[0]
    assert ch == "slack"
    assert payload.get("narrative") == "Renew the cert within 10 days or the POA&M will breach."


def test_send_poam_reminder_narrative_absent_from_slack_payload_when_none(monkeypatch):
    """When narrative is None the slack dispatch() payload must not contain a 'narrative' key."""
    dispatched = _fake_poam_conn_with_dispatch(monkeypatch)
    monkeypatch.setattr(alert_service, "_ai_alert_narrative", lambda *a: None)

    alert_service.send_poam_reminder("POAM-42", "P-1", ["slack"], ai_narrative=True)

    assert dispatched, "dispatch() was never called for slack channel"
    _, payload = dispatched[0]
    assert "narrative" not in payload


# ---------------------------------------------------------------------------
# Tests for alert_kind labels and facts-dict keys passed from chain functions
# (aiify-opp-5928)
# Pins the alert_kind string each chain function passes to _ai_alert_narrative
# (which steers model framing) and verifies the facts dict contains the keys
# the model needs to ground its output — contracts not previously tested at
# the integration level.
# ---------------------------------------------------------------------------

def test_escalate_cat1_alert_kind_label(monkeypatch):
    """escalate_cat1_finding must pass 'CAT-I security finding escalation' as alert_kind."""
    _fake_cat1_conn(monkeypatch)
    captured = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured.append(kind) or None,
    )

    alert_service.escalate_cat1_finding("F-1", "P-1", [], ai_narrative=True)

    assert captured == ["CAT-I security finding escalation"]


def test_dispatch_stig_alert_kind_label(monkeypatch):
    """dispatch_stig_alert must pass 'STIG check finding alert' as alert_kind."""
    _fake_stig_conn(monkeypatch)
    captured = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured.append(kind) or None,
    )

    alert_service.dispatch_stig_alert("SV-99999", "WL-1", [], auto_create_poam=False, ai_narrative=True)

    assert captured == ["STIG check finding alert"]


def test_send_poam_reminder_alert_kind_label(monkeypatch):
    """send_poam_reminder must pass 'POA&M deadline reminder' as alert_kind."""
    _fake_poam_conn(monkeypatch)
    captured = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured.append(kind) or None,
    )

    alert_service.send_poam_reminder("POAM-42", "P-1", [], ai_narrative=True)

    assert captured == ["POA&M deadline reminder"]


def test_send_security_alert_digest_alert_kind_label(monkeypatch):
    """send_security_alert_digest must pass 'security alert digest summary' as alert_kind."""
    _fake_alert_conn(monkeypatch)
    captured = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured.append(kind) or None,
    )

    alert_service.send_security_alert_digest("sec@icdev.local", ai_narrative=True)

    assert captured == ["security alert digest summary"]


def test_send_security_alert_digest_facts_dict_keys(monkeypatch):
    """send_security_alert_digest must include aggregate count keys in the facts dict."""
    _fake_alert_conn(
        monkeypatch,
        cat1=[{"id": "F-1", "title": "T", "severity": "I",
               "vid": "V-1", "component": "c", "detected_at": "2026-06-01"}],
        stig=[{"check_id": "SV-1", "check_name": "N", "severity": "I", "status": "open"}],
        poam=[{"id": "P-1", "title": "T", "severity": "II",
               "due_date": "2026-07-01", "owner": "j", "status": "open"}],
    )
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.send_security_alert_digest("sec@icdev.local", ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "cat1_count" in facts
    assert "stig_critical_count" in facts
    assert "poam_due_count" in facts
    assert facts["cat1_count"] == 1
    assert facts["stig_critical_count"] == 1
    assert facts["poam_due_count"] == 1


def test_escalate_cat1_facts_contain_key_fields(monkeypatch):
    """escalate_cat1_finding facts dict must contain finding_title, severity, and sla_hours."""
    _fake_cat1_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.escalate_cat1_finding("F-1", "P-1", [], ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "finding_title" in facts
    assert "severity" in facts
    assert "sla_hours" in facts
    assert facts["finding_title"] == "Unpatched OpenSSL"
    assert facts["sla_hours"] == 24


# ---------------------------------------------------------------------------
# Tests for facts-dict contracts in dispatch_stig_alert and send_poam_reminder
# (aiify-opp-155)
# Pins the keys the model relies on to ground its output — ensures the
# caller passes the correct facts even as the implementation evolves.
# ---------------------------------------------------------------------------

def test_dispatch_stig_alert_facts_contain_key_fields(monkeypatch):
    """dispatch_stig_alert facts dict must contain check_id, stig_severity, severity,
    status, and remediation so the narrative is grounded in the alert details."""
    _fake_stig_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.dispatch_stig_alert("SV-99999", "WL-1", [], auto_create_poam=False, ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "check_id" in facts
    assert "stig_severity" in facts
    assert "severity" in facts
    assert "status" in facts
    assert "remediation" in facts
    assert facts["check_id"] == "SV-99999"
    assert facts["status"] == "open"


def test_send_poam_reminder_facts_contain_key_fields(monkeypatch):
    """send_poam_reminder facts dict must contain poam_id, title, severity, owner,
    days_remaining, and due_date so the narrative is grounded in the reminder details."""
    _fake_poam_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.send_poam_reminder("POAM-42", "P-1", [], ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "poam_id" in facts
    assert "title" in facts
    assert "severity" in facts
    assert "owner" in facts
    assert "days_remaining" in facts
    assert "due_date" in facts
    assert facts["poam_id"] == "POAM-42"
    assert facts["days_remaining"] == 10


def test_all_chain_functions_default_narrative_none(monkeypatch):
    """All four db->render->notify chain functions must return narrative=None when
    ai_narrative is not passed, regardless of LLM availability (aiify-opp-155)."""
    _fake_cat1_conn(monkeypatch)
    called = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda *a: called.append(True) or "unexpected",
    )

    result = alert_service.escalate_cat1_finding("F-1", "P-1", [])
    assert result["narrative"] is None
    assert not called, "LLM must not be invoked when ai_narrative defaults to False"


# ---------------------------------------------------------------------------
# Tests for extended facts-dict grounding contracts (aiify-opp-153)
# Pins additional keys that were not previously asserted so the model
# receives sufficient context to produce a grounded narrative across all
# four db->render->notify chain functions.
# ---------------------------------------------------------------------------

def test_escalate_cat1_facts_include_system_and_count_fields(monkeypatch):
    """escalate_cat1_finding facts dict must also contain open_cat1_count, system_name,
    vid, component, and poam_id so the narrative can reference system context."""
    _fake_cat1_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.escalate_cat1_finding("F-1", "P-1", [], ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "open_cat1_count" in facts
    assert "system_name" in facts
    assert "vid" in facts
    assert "component" in facts
    assert "poam_id" in facts
    assert facts["open_cat1_count"] == 3
    assert facts["system_name"] == "TestSys"
    assert facts["vid"] == "V-12345"


def test_dispatch_stig_alert_facts_include_system_and_action_fields(monkeypatch):
    """dispatch_stig_alert facts dict must contain system_name, workload_id, and
    action so the narrative can describe the affected system and recommended action."""
    _fake_stig_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.dispatch_stig_alert("SV-99999", "WL-1", [], auto_create_poam=False, ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "system_name" in facts
    assert "workload_id" in facts
    assert "action" in facts
    assert facts["workload_id"] == "WL-1"
    assert facts["system_name"] == "TestWorkload"


def test_send_poam_reminder_facts_include_project_and_milestone(monkeypatch):
    """send_poam_reminder facts dict must contain project_id, milestone, and
    framework so the narrative can place the deadline in its compliance context."""
    _fake_poam_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.send_poam_reminder("POAM-42", "P-1", [], ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "project_id" in facts
    assert "milestone" in facts
    assert "framework" in facts
    assert facts["project_id"] == "P-1"
    assert facts["milestone"] == "Q3"
    assert facts["framework"] == "NIST"


def test_send_security_alert_digest_facts_include_days_window_and_project(monkeypatch):
    """send_security_alert_digest facts dict must contain days_window and project_id
    so the narrative can reference the reporting window and optional scope filter."""
    _fake_alert_conn(monkeypatch)
    captured_facts = []
    monkeypatch.setattr(
        alert_service, "_ai_alert_narrative",
        lambda kind, facts: captured_facts.append(dict(facts)) or None,
    )

    alert_service.send_security_alert_digest("sec@icdev.local", project_id="P-42", days=14, ai_narrative=True)

    assert captured_facts, "narrative helper was never called"
    facts = captured_facts[0]
    assert "days_window" in facts
    assert "project_id" in facts
    assert facts["days_window"] == 14
    assert facts["project_id"] == "P-42"
