# CUI // SP-CTI
"""Dispatch integration tests for alert_service LLM narrative (aiify-opp-5669).

Verifies that the ``ai_narrative`` flag correctly threads through each
db → render → notify chain:

- ``escalate_cat1_finding``
- ``dispatch_stig_alert``
- ``send_poam_reminder``

Key guarantees under test:
1. ``narrative`` key is present in every result dict, ``None`` when LLM
   is unavailable or the flag is False.
2. Slack/Teams payloads include ``narrative`` when one is generated.
3. LLM failure never blocks the deterministic alert from shipping.
"""

from __future__ import annotations

import types


from tools.notification_service import alert_service


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    """dict that also supports attribute access (mimics sqlite3.Row)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)


def _make_conn(rows: list):
    """Return a fake connection that yields ``rows`` from successive fetchone calls."""
    state = {"idx": 0, "last_id": 99}

    class _Cursor:
        def fetchone(self_):
            i = state["idx"]
            state["idx"] += 1
            if i < len(rows):
                return rows[i]
            return None

        @property
        def lastrowid(self_):
            return state["last_id"]

    class _Conn:
        def execute(self_, *_args, **_kwargs):
            return _Cursor()

        def commit(self_):
            pass

        def close(self_):
            pass

    return _Conn()


def _patch_conn(monkeypatch, rows):
    conn = _make_conn(rows)
    monkeypatch.setattr(
        "tools.notification_service.alert_service.get_connection", lambda: conn
    )
    return conn


def _patch_render(monkeypatch):
    """Stub all render helpers to return trivial strings."""
    for fn in ("render_template", "render_to_string", "render_string"):
        monkeypatch.setattr(
            alert_service,
            fn,
            lambda *a, **kw: "<stub>",
        )


def _patch_notify(monkeypatch):
    """Stub all notification dispatch helpers (no-op)."""
    published = []
    for fn in ("send", "sendmail", "notify", "emit", "dispatch"):
        monkeypatch.setattr(alert_service, fn, lambda *a, **kw: None)

    def _publish(channel, payload):
        published.append((channel, payload))

    monkeypatch.setattr(alert_service, "publish", _publish)
    return published


def _fake_router(monkeypatch, *, content=None, raises=None):
    captured = {}

    class _Resp:
        def __init__(self, text):
            self.content = text

    class _FakeRouter:
        def invoke(self, function, request):
            captured["function"] = function
            if raises is not None:
                raise raises
            return _Resp(content)

    fake_mod = types.SimpleNamespace(LLMRouter=_FakeRouter)
    monkeypatch.setitem(__import__("sys").modules, "tools.llm.router", fake_mod)
    return captured


# ---------------------------------------------------------------------------
# escalate_cat1_finding
# ---------------------------------------------------------------------------

_CAT1_ROWS = [
    _FakeRow(
        id="f-1", title="Unpatched OpenSSL", severity="I", vid="V-12345",
        component="web-tier", check_id="C-1", detected_at="2026-06-01T00:00:00Z",
        remediation="Apply patch",
    ),
    _FakeRow(id="poam-1", status="open", due_date="2026-07-01", milestone="M1", owner="alice"),
    _FakeRow(name="Alpha System", classification="CUI", system_owner="alice"),
    _FakeRow(cnt=3),
]


def test_cat1_narrative_in_result_when_ai_on(monkeypatch):
    _patch_conn(monkeypatch, list(_CAT1_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)
    _fake_router(monkeypatch, content="Escalate this CAT-I finding immediately.")

    result = alert_service.escalate_cat1_finding(
        "f-1", "proj-1", ["slack"], ai_narrative=True
    )

    assert result["narrative"] == "Escalate this CAT-I finding immediately."
    assert result["status"] == "escalated"


def test_cat1_narrative_none_when_ai_off(monkeypatch):
    _patch_conn(monkeypatch, list(_CAT1_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)

    result = alert_service.escalate_cat1_finding(
        "f-1", "proj-1", ["slack"], ai_narrative=False
    )

    assert result["narrative"] is None


def test_cat1_narrative_none_on_llm_failure(monkeypatch):
    _patch_conn(monkeypatch, list(_CAT1_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("provider offline"))

    result = alert_service.escalate_cat1_finding(
        "f-1", "proj-1", ["slack"], ai_narrative=True
    )

    assert result["narrative"] is None
    assert result["status"] == "escalated"  # deterministic alert still delivered


def test_cat1_narrative_attached_to_slack_payload(monkeypatch):
    _patch_conn(monkeypatch, list(_CAT1_ROWS))
    _patch_render(monkeypatch)
    published = _patch_notify(monkeypatch)
    _fake_router(monkeypatch, content="Patch OpenSSL on web-tier now.")

    alert_service.escalate_cat1_finding(
        "f-1", "proj-1", ["slack"], ai_narrative=True
    )

    assert published, "publish() was not called for slack"
    _, payload = published[0]
    assert payload.get("narrative") == "Patch OpenSSL on web-tier now."


def test_cat1_narrative_absent_from_slack_when_llm_fails(monkeypatch):
    _patch_conn(monkeypatch, list(_CAT1_ROWS))
    _patch_render(monkeypatch)
    published = _patch_notify(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("offline"))

    alert_service.escalate_cat1_finding(
        "f-1", "proj-1", ["slack"], ai_narrative=True
    )

    _, payload = published[0]
    assert "narrative" not in payload


# ---------------------------------------------------------------------------
# dispatch_stig_alert
# ---------------------------------------------------------------------------

_STIG_ROWS = [
    _FakeRow(
        check_id="V-12345", check_name="Audit log rotation", severity="II",
        status="open", remediation="Enable log rotation", reference_url="",
        workload_id="wl-1",
    ),
    _FakeRow(name="Prod Workload", classification="CUI", owner="bob"),
    None,  # existing_poam → None (no existing POAM, triggers creation)
]


def test_stig_narrative_in_result_when_ai_on(monkeypatch):
    _patch_conn(monkeypatch, list(_STIG_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)
    _fake_router(monkeypatch, content="Enable audit log rotation immediately.")

    result = alert_service.dispatch_stig_alert(
        "V-12345", "wl-1", ["slack"], auto_create_poam=False, ai_narrative=True
    )

    assert result["narrative"] == "Enable audit log rotation immediately."
    assert result["status"] == "dispatched"


def test_stig_narrative_none_when_ai_off(monkeypatch):
    _patch_conn(monkeypatch, list(_STIG_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)

    result = alert_service.dispatch_stig_alert(
        "V-12345", "wl-1", ["slack"], auto_create_poam=False, ai_narrative=False
    )

    assert result["narrative"] is None


def test_stig_narrative_none_on_llm_failure(monkeypatch):
    _patch_conn(monkeypatch, list(_STIG_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("provider offline"))

    result = alert_service.dispatch_stig_alert(
        "V-12345", "wl-1", ["slack"], auto_create_poam=False, ai_narrative=True
    )

    assert result["narrative"] is None
    assert result["status"] == "dispatched"


def test_stig_narrative_attached_to_teams_payload(monkeypatch):
    _patch_conn(monkeypatch, list(_STIG_ROWS))
    _patch_render(monkeypatch)
    published = _patch_notify(monkeypatch)
    _fake_router(monkeypatch, content="Audit log retention non-compliant; apply fix now.")

    alert_service.dispatch_stig_alert(
        "V-12345", "wl-1", ["teams"], auto_create_poam=False, ai_narrative=True
    )

    assert published
    _, payload = published[0]
    assert payload.get("narrative") == "Audit log retention non-compliant; apply fix now."


# ---------------------------------------------------------------------------
# send_poam_reminder
# ---------------------------------------------------------------------------

_POAM_ROWS = [
    _FakeRow(
        id="POAM-99", title="Patch OpenSSL", severity="high", status="open",
        due_date="2026-07-01", milestone="Sprint-12", owner="charlie",
        finding_ref="f-1", framework="FedRAMP",
    ),
    _FakeRow(days_remaining=7),
    _FakeRow(email="charlie@example.com", name="Charlie", phone="555-1234"),
    _FakeRow(title="Unpatched OpenSSL", severity="I"),
]


def test_poam_narrative_in_result_when_ai_on(monkeypatch):
    _patch_conn(monkeypatch, list(_POAM_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)
    _fake_router(monkeypatch, content="Seven days remain; escalate to ISSO immediately.")

    result = alert_service.send_poam_reminder(
        "POAM-99", "proj-1", ["slack"], ai_narrative=True
    )

    assert result["narrative"] == "Seven days remain; escalate to ISSO immediately."
    assert result["status"] == "delivered"


def test_poam_narrative_none_when_ai_off(monkeypatch):
    _patch_conn(monkeypatch, list(_POAM_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)

    result = alert_service.send_poam_reminder(
        "POAM-99", "proj-1", ["slack"], ai_narrative=False
    )

    assert result["narrative"] is None


def test_poam_narrative_none_on_llm_failure(monkeypatch):
    _patch_conn(monkeypatch, list(_POAM_ROWS))
    _patch_render(monkeypatch)
    _patch_notify(monkeypatch)
    _fake_router(monkeypatch, raises=RuntimeError("no provider"))

    result = alert_service.send_poam_reminder(
        "POAM-99", "proj-1", ["slack"], ai_narrative=True
    )

    assert result["narrative"] is None
    assert result["status"] == "delivered"


def test_poam_narrative_attached_to_slack_payload(monkeypatch):
    # send_poam_reminder uses dispatch() (not publish()) for slack/teams.
    _patch_conn(monkeypatch, list(_POAM_ROWS))
    _patch_render(monkeypatch)
    dispatched = []

    def _dispatch(channel, payload):
        dispatched.append((channel, payload))

    _patch_notify(monkeypatch)
    monkeypatch.setattr(alert_service, "dispatch", _dispatch)
    _fake_router(monkeypatch, content="Deadline in 7 days; owner must act now.")

    alert_service.send_poam_reminder(
        "POAM-99", "proj-1", ["slack"], ai_narrative=True
    )

    assert dispatched
    _, payload = dispatched[0]
    assert payload.get("narrative") == "Deadline in 7 days; owner must act now."
