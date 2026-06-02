# CUI // SP-CTI
"""Integration tests: narrative key propagation through alert_service dispatch functions (aiify-opp-5634).

Verifies that all three db -> render -> notify chain functions correctly:
1. Pass ``narrative=None`` in their return dict when ``ai_narrative=False`` (default).
2. Pass the LLM-synthesized string under ``narrative`` when ``ai_narrative=True``
   and the LLM is available.
3. Pass ``narrative=None`` even with ``ai_narrative=True`` when LLM fails —
   the deterministic alert ships unchanged.

The DB, renderer, and channel helpers are all mocked to keep these tests
unit-speed; correctness of the DB queries and dispatch channels is tested
elsewhere.
"""

from __future__ import annotations

import sqlite3
import types
from unittest.mock import MagicMock, patch

import pytest

from tools.notification_service import alert_service


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_row(**kwargs):
    """Return a sqlite3.Row-compatible dict-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: kwargs.get(k)
    row.get = lambda k, default=None: kwargs.get(k, default)
    return row


def _fake_router(monkeypatch, *, content=None, raises=None):
    """Patch LLMRouter so invoke returns/raises as directed."""
    class _Resp:
        def __init__(self, text):
            self.content = text

    class _FakeRouter:
        def invoke(self, function, request):
            if raises is not None:
                raise raises
            return _Resp(content)

    fake_module = types.SimpleNamespace(LLMRouter=_FakeRouter)
    monkeypatch.setitem(__import__("sys").modules, "tools.llm.router", fake_module)


def _noop(*a, **kw):
    return None


def _patch_dispatchers(monkeypatch):
    """Silence all outbound notification helpers."""
    for name in ("render_template", "render_to_string", "render_string",
                 "send", "sendmail", "notify", "emit", "publish", "dispatch"):
        monkeypatch.setattr(alert_service, name, _noop)


# ---------------------------------------------------------------------------
# escalate_cat1_finding
# ---------------------------------------------------------------------------

class TestEscalateCat1Narrative:
    def _mock_conn(self):
        conn = MagicMock()
        finding = _make_row(
            id="F-1", title="Unpatched OpenSSL", severity="I", vid="V-220700",
            component="web-tier", check_id="V-220700", detected_at="2026-06-01T00:00:00",
            remediation="Apply patch",
        )
        poam = _make_row(id="POAM-001", status="open", due_date="2026-07-01",
                         milestone="patch-sprint", owner="alice")
        project = _make_row(name="ICDEV-PROD", classification="CUI", system_owner="bob")
        count = _make_row(cnt=3)

        def _execute(sql, params=()):
            cur = MagicMock()
            if "stig_findings" in sql and "COUNT" not in sql:
                cur.fetchone.return_value = finding
            elif "poam_items" in sql and "finding_ref" in sql:
                cur.fetchone.return_value = poam
            elif "projects" in sql:
                cur.fetchone.return_value = project
            elif "COUNT" in sql:
                cur.fetchone.return_value = count
            else:
                cur.fetchone.return_value = None
            return cur

        conn.execute = _execute
        conn.commit = MagicMock()
        conn.close = MagicMock()
        return conn

    def test_narrative_none_by_default(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)

        result = alert_service.escalate_cat1_finding("F-1", "proj-1", ["slack"])

        assert result["narrative"] is None

    def test_narrative_populated_when_opted_in(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, content="Apply the OpenSSL patch within 24 hours.")

        result = alert_service.escalate_cat1_finding(
            "F-1", "proj-1", ["slack"], ai_narrative=True
        )

        assert result["narrative"] == "Apply the OpenSSL patch within 24 hours."

    def test_narrative_none_on_llm_failure(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, raises=RuntimeError("LLM unavailable"))

        result = alert_service.escalate_cat1_finding(
            "F-1", "proj-1", ["slack"], ai_narrative=True
        )

        assert result["narrative"] is None

    def test_narrative_attached_to_slack_payload(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, content="Critical OpenSSL exposure. Patch now.")
        published = []
        monkeypatch.setattr(alert_service, "publish", lambda ch, payload: published.append(payload))

        alert_service.escalate_cat1_finding("F-1", "proj-1", ["slack"], ai_narrative=True)

        assert published, "publish() was never called for slack channel"
        assert published[0].get("narrative") == "Critical OpenSSL exposure. Patch now."


# ---------------------------------------------------------------------------
# dispatch_stig_alert
# ---------------------------------------------------------------------------

class TestDispatchStigNarrative:
    def _mock_conn(self):
        conn = MagicMock()
        check = _make_row(
            check_id="V-220700", check_name="OpenSSL version check", severity="I",
            status="open", remediation="Upgrade to patched version",
            reference_url="https://stig.example/V-220700", workload_id="wl-1",
        )
        workload = _make_row(name="Web Tier", classification="CUI", owner="alice")
        no_poam = MagicMock()
        no_poam.fetchone.return_value = None

        def _execute(sql, params=()):
            cur = MagicMock()
            if "govlift_stig_checks" in sql:
                cur.fetchone.return_value = check
            elif "govlift_workloads" in sql:
                cur.fetchone.return_value = workload
            elif "poam_items" in sql and "finding_ref" in sql:
                cur.fetchone.return_value = None
                cur.lastrowid = 42
            else:
                cur.fetchone.return_value = None
                cur.lastrowid = 42
            return cur

        conn.execute = _execute
        conn.commit = MagicMock()
        conn.close = MagicMock()
        return conn

    def test_narrative_none_by_default(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)

        result = alert_service.dispatch_stig_alert("V-220700", "wl-1", ["slack"],
                                                    auto_create_poam=False)

        assert result["narrative"] is None

    def test_narrative_populated_when_opted_in(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, content="Upgrade OpenSSL on all web-tier nodes.")

        result = alert_service.dispatch_stig_alert(
            "V-220700", "wl-1", ["slack"],
            auto_create_poam=False, ai_narrative=True,
        )

        assert result["narrative"] == "Upgrade OpenSSL on all web-tier nodes."

    def test_narrative_none_on_llm_failure(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, raises=RuntimeError("LLM unavailable"))

        result = alert_service.dispatch_stig_alert(
            "V-220700", "wl-1", ["slack"],
            auto_create_poam=False, ai_narrative=True,
        )

        assert result["narrative"] is None

    def test_narrative_attached_to_slack_payload(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, content="STIG CAT-I finding detected; upgrade immediately.")
        published = []
        monkeypatch.setattr(alert_service, "publish", lambda ch, payload: published.append(payload))

        alert_service.dispatch_stig_alert(
            "V-220700", "wl-1", ["slack"],
            auto_create_poam=False, ai_narrative=True,
        )

        assert published, "publish() was never called for slack channel"
        assert published[0].get("narrative") == "STIG CAT-I finding detected; upgrade immediately."


# ---------------------------------------------------------------------------
# send_poam_reminder
# ---------------------------------------------------------------------------

class TestPoamReminderNarrative:
    def _mock_conn(self):
        conn = MagicMock()
        poam = _make_row(
            id="POAM-1", title="OpenSSL patch", severity="high", status="open",
            due_date="2026-07-01", milestone="patch-sprint", owner="alice",
            finding_ref="V-220700", framework="fedramp",
        )
        days = _make_row(days_remaining=10)
        owner = _make_row(email="alice@icdev.local", name="Alice", phone="555-0100")
        finding = _make_row(title="OpenSSL version check", severity="I")

        def _execute(sql, params=()):
            cur = MagicMock()
            if "poam_items" in sql and "julianday" not in sql:
                cur.fetchone.return_value = poam
            elif "julianday" in sql:
                cur.fetchone.return_value = days
            elif "users" in sql:
                cur.fetchone.return_value = owner
            elif "stig_findings" in sql:
                cur.fetchone.return_value = finding
            else:
                cur.fetchone.return_value = None
            return cur

        conn.execute = _execute
        conn.commit = MagicMock()
        conn.close = MagicMock()
        return conn

    def test_narrative_none_by_default(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)

        result = alert_service.send_poam_reminder("POAM-1", "proj-1", ["slack"])

        assert result["narrative"] is None

    def test_narrative_populated_when_opted_in(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, content="Only 10 days left on this POA&M. Escalate to system owner.")

        result = alert_service.send_poam_reminder("POAM-1", "proj-1", ["slack"],
                                                   ai_narrative=True)

        assert result["narrative"] == "Only 10 days left on this POA&M. Escalate to system owner."

    def test_narrative_none_on_llm_failure(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, raises=RuntimeError("LLM unavailable"))

        result = alert_service.send_poam_reminder("POAM-1", "proj-1", ["slack"],
                                                   ai_narrative=True)

        assert result["narrative"] is None

    def test_narrative_attached_to_slack_payload(self, monkeypatch):
        monkeypatch.setattr(alert_service, "get_connection", lambda: self._mock_conn())
        _patch_dispatchers(monkeypatch)
        _fake_router(monkeypatch, content="POA&M deadline imminent; assign owner immediately.")
        dispatched = []
        monkeypatch.setattr(alert_service, "dispatch",
                            lambda ch, payload: dispatched.append(payload))

        alert_service.send_poam_reminder("POAM-1", "proj-1", ["slack"], ai_narrative=True)

        assert dispatched, "dispatch() was never called for slack channel"
        assert dispatched[0].get("narrative") == "POA&M deadline imminent; assign owner immediately."
