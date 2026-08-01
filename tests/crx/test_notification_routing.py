# CUI // SP-CTI
"""Tests for the crx-not-01 notification routing / escalation / preferences engine.

Covers:
  * routing_rules.resolve_channels — severity x component x tenant -> channels
  * escalation — register / acknowledge / escalate-after-timeout (injected now)
  * preferences — quiet hours (incl. midnight wrap) + channel narrowing + digest

All deterministic; no real network sends. DB-backed tables self-create via the
modules' _ensure_schema(), so no conftest schema edits are required.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.notifications import routing_rules
from tools.notifications import escalation
from tools.notifications import preferences


# --- Shared test config (no disk read) --------------------------------------

ROUTING_CFG = {
    "rules": [
        {"name": "critical-any", "severity": ["critical"], "component": "*",
         "tenant": "*", "channels": ["slack", "telegram", "email"]},
        {"name": "high-security", "severity": ["high"],
         "component": ["security", "compliance"], "tenant": "*",
         "channels": ["slack", "email"]},
        {"name": "high-default", "severity": ["high"], "component": "*",
         "tenant": "*", "channels": ["slack"]},
        {"name": "tenant-scoped", "severity": ["medium"], "component": "*",
         "tenant": ["acme"], "channels": ["webhook"]},
    ],
    "default_channels": ["telegram"],
    "escalation": {"critical_severities": ["critical"],
                   "default_escalation_channels": ["slack", "telegram", "email"],
                   "timeout_minutes": 30},
}


# ============================ routing_rules =================================

def test_critical_routes_to_all_channels():
    ch = routing_rules.resolve_channels("critical", "kanban", "t1", config=ROUTING_CFG)
    assert ch == ["slack", "telegram", "email"]


def test_high_security_component_specific():
    assert routing_rules.resolve_channels("high", "security", None, config=ROUTING_CFG) \
        == ["slack", "email"]


def test_high_other_component_falls_to_high_default():
    # high + non-security: only the wildcard high rule matches.
    assert routing_rules.resolve_channels("high", "kanban", None, config=ROUTING_CFG) \
        == ["slack"]


def test_union_and_dedupe_preserves_order():
    # high + security matches BOTH high-security and high-default; slack is deduped.
    ch = routing_rules.resolve_channels("high", "compliance", None, config=ROUTING_CFG)
    assert ch == ["slack", "email"]


def test_tenant_scoped_rule_only_for_matching_tenant():
    assert routing_rules.resolve_channels("medium", "billing", "acme", config=ROUTING_CFG) \
        == ["webhook"]
    # Different tenant -> no rule matches -> default.
    assert routing_rules.resolve_channels("medium", "billing", "other", config=ROUTING_CFG) \
        == ["telegram"]


def test_no_match_uses_default():
    assert routing_rules.resolve_channels("info", "anything", None, config=ROUTING_CFG) \
        == ["telegram"]


def test_explicit_default_arg_overrides_config_default():
    assert routing_rules.resolve_channels("info", None, None, default=["console"],
                                          config=ROUTING_CFG) == ["console"]


def test_shipped_yaml_loads_and_resolves():
    # The real args/notification_routing.yaml must parse and route critical.
    ch = routing_rules.resolve_channels("critical", "security", "t1")
    assert "slack" in ch and "email" in ch


# ============================ escalation ====================================

def _uid() -> str:
    return "alert-" + uuid.uuid4().hex[:10]


def test_non_critical_not_tracked(monkeypatch):
    monkeypatch.setattr(escalation, "_escalation_cfg",
                        lambda: ROUTING_CFG["escalation"])
    res = escalation.register_alert(_uid(), "high", "t1", "CUI", ["slack"])
    assert res["tracked"] is False


def test_register_and_acknowledge(monkeypatch):
    monkeypatch.setattr(escalation, "_escalation_cfg",
                        lambda: ROUTING_CFG["escalation"])
    alert_id = _uid()
    reg = escalation.register_alert(alert_id, "critical", "t1", "CUI",
                                    ["slack"], component="security")
    assert reg["tracked"] is True
    assert reg["status"] == "pending"
    assert reg["ack_link"].endswith(reg["ack_token"])

    ack = escalation.acknowledge(reg["ack_token"], actor="analyst")
    assert ack["status"] == "acknowledged"

    row = escalation.get_escalation(reg["ack_token"])
    assert row["status"] == "acknowledged"
    assert row["acknowledged_by"] == "analyst"


def test_acknowledge_is_idempotent(monkeypatch):
    monkeypatch.setattr(escalation, "_escalation_cfg",
                        lambda: ROUTING_CFG["escalation"])
    reg = escalation.register_alert(_uid(), "critical", "t1", "CUI", ["slack"])
    escalation.acknowledge(reg["ack_token"])
    second = escalation.acknowledge(reg["ack_token"])
    assert second["status"] == "acknowledged"
    assert second.get("already") is True


def test_escalation_fires_after_timeout(monkeypatch):
    monkeypatch.setattr(escalation, "_escalation_cfg",
                        lambda: ROUTING_CFG["escalation"])
    # Force routing to resolve deterministically for escalation channels.
    monkeypatch.setattr(escalation, "resolve_channels",
                        lambda **kw: ["slack", "telegram", "email"])

    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    alert_id = _uid()
    reg = escalation.register_alert(alert_id, "critical", "esc-t1", "CUI",
                                    ["slack"], component="security",
                                    timeout_minutes=30, now=t0)

    # Before the deadline: nothing escalates.
    before = escalation.process_escalations(now=t0 + timedelta(minutes=10),
                                            tenant_id="esc-t1")
    assert all(e["alert_id"] != alert_id for e in before)

    # After the deadline: this alert escalates exactly once.
    after = escalation.process_escalations(now=t0 + timedelta(minutes=31),
                                           tenant_id="esc-t1")
    mine = [e for e in after if e["alert_id"] == alert_id]
    assert len(mine) == 1
    assert mine[0]["channels"] == ["slack", "telegram", "email"]

    # Re-running does not re-escalate (status now 'escalated').
    again = escalation.process_escalations(now=t0 + timedelta(minutes=60),
                                           tenant_id="esc-t1")
    assert all(e["alert_id"] != alert_id for e in again)

    assert escalation.get_escalation(reg["ack_token"])["status"] == "escalated"


def test_acknowledged_alert_does_not_escalate(monkeypatch):
    monkeypatch.setattr(escalation, "_escalation_cfg",
                        lambda: ROUTING_CFG["escalation"])
    t0 = datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)
    alert_id = _uid()
    reg = escalation.register_alert(alert_id, "critical", "esc-t2", "CUI",
                                    ["slack"], timeout_minutes=15, now=t0)
    escalation.acknowledge(reg["ack_token"], now=t0 + timedelta(minutes=5))

    escalated = escalation.process_escalations(now=t0 + timedelta(minutes=30),
                                               tenant_id="esc-t2")
    assert all(e["alert_id"] != alert_id for e in escalated)
    assert escalation.get_escalation(reg["ack_token"])["status"] == "acknowledged"


# ============================ preferences ===================================

def test_quiet_hours_normal_window():
    prefs = {"quiet_hours_start": 22, "quiet_hours_end": 23, "timezone": "UTC"}
    inside = datetime(2026, 1, 1, 22, 30, tzinfo=timezone.utc)
    outside = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert preferences.in_quiet_hours(prefs, now=inside) is True
    assert preferences.in_quiet_hours(prefs, now=outside) is False


def test_quiet_hours_wrapping_midnight():
    prefs = {"quiet_hours_start": 22, "quiet_hours_end": 6, "timezone": "UTC"}
    assert preferences.in_quiet_hours(
        prefs, now=datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)) is True
    assert preferences.in_quiet_hours(
        prefs, now=datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)) is True
    assert preferences.in_quiet_hours(
        prefs, now=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)) is False


def test_quiet_hours_disabled_when_unset():
    assert preferences.in_quiet_hours(
        {"quiet_hours_start": None, "quiet_hours_end": None}) is False


def test_set_get_preferences_roundtrip():
    uid = "user-" + uuid.uuid4().hex[:8]
    preferences.set_preferences(uid, tenant_id="t1", channels=["email", "slack"],
                                quiet_hours_start=22, quiet_hours_end=6,
                                digest_opt_in=True, digest_frequency="weekly")
    got = preferences.get_preferences(uid, tenant_id="t1")
    assert got["exists"] is True
    assert got["channels"] == ["email", "slack"]
    assert got["digest_opt_in"] is True
    assert got["digest_frequency"] == "weekly"
    assert preferences.wants_digest(uid, "t1") is True


def test_resolve_user_channels_intersects_preferences():
    uid = "user-" + uuid.uuid4().hex[:8]
    preferences.set_preferences(uid, tenant_id="t1", channels=["email"])
    # Routing offered slack+email+telegram; user only wants email.
    got = preferences.resolve_user_channels(
        uid, ["slack", "email", "telegram"], tenant_id="t1")
    assert got == ["email"]


def test_quiet_hours_suppresses_non_critical_but_critical_bypasses(monkeypatch):
    # dispatcher_paused is pinned False: quiet-hours suppression now also
    # depends on live scheduler state, and without this the assertion below
    # would flip whenever the kanban runner happened to be paused while the
    # suite ran. Patch the module attribute, not the underlying function.
    monkeypatch.setattr(preferences, "dispatcher_paused", lambda: False)

    uid = "user-" + uuid.uuid4().hex[:8]
    preferences.set_preferences(uid, tenant_id="t1", channels=["email"],
                                quiet_hours_start=0, quiet_hours_end=23,
                                timezone="UTC")
    midday = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    # Non-critical in quiet hours -> suppressed.
    assert preferences.resolve_user_channels(
        uid, ["email"], tenant_id="t1", severity="high", now=midday) == []
    # Critical bypasses quiet hours (default config).
    assert preferences.resolve_user_channels(
        uid, ["email"], tenant_id="t1", severity="critical", now=midday) == ["email"]


def test_paused_dispatcher_bypasses_quiet_hours(monkeypatch):
    """A paused dispatcher has no autonomous responder, so alerts must land."""
    uid = "user-" + uuid.uuid4().hex[:8]
    preferences.set_preferences(uid, tenant_id="t1", channels=["email"],
                                quiet_hours_start=0, quiet_hours_end=23,
                                timezone="UTC")
    midday = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(preferences, "dispatcher_paused", lambda: False)
    assert preferences.resolve_user_channels(
        uid, ["email"], tenant_id="t1", severity="high", now=midday) == []

    # Same instant, same non-critical severity — only the pause differs.
    monkeypatch.setattr(preferences, "dispatcher_paused", lambda: True)
    assert preferences.resolve_user_channels(
        uid, ["email"], tenant_id="t1", severity="high", now=midday) == ["email"]


def test_paused_dispatcher_still_respects_channel_preferences(monkeypatch):
    """The bypass lifts quiet hours only — it must not widen channel choice."""
    monkeypatch.setattr(preferences, "dispatcher_paused", lambda: True)
    uid = "user-" + uuid.uuid4().hex[:8]
    preferences.set_preferences(uid, tenant_id="t1", channels=["email"],
                                quiet_hours_start=0, quiet_hours_end=23,
                                timezone="UTC")
    midday = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    got = preferences.resolve_user_channels(
        uid, ["slack", "email", "telegram"], tenant_id="t1",
        severity="high", now=midday)
    assert got == ["email"]


def test_dispatcher_paused_fails_closed_when_kanban_unavailable(monkeypatch):
    """An unimportable/broken scheduler must not break notifications.

    Guards the fail-closed contract: an error means "not paused", so quiet
    hours keep suppressing exactly as before rather than the probe raising out
    of resolve_user_channels.
    """
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name.startswith("tools.kanban"):
            raise ImportError("simulated: scheduler module unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert preferences.dispatcher_paused() is False


def test_default_prefs_for_unknown_user():
    got = preferences.get_preferences("nobody-" + uuid.uuid4().hex[:6], "t1")
    assert got["exists"] is False
    assert isinstance(got["channels"], list)


# ============================ digest gate ===================================

def test_should_deliver_digest_respects_optin():
    from tools.notification_service.digest_service import should_deliver_digest

    uid = "user-" + uuid.uuid4().hex[:8]
    preferences.set_preferences(uid, tenant_id="t1", digest_opt_in=False)
    assert should_deliver_digest(uid, "t1") is False

    preferences.set_preferences(uid, tenant_id="t1", digest_opt_in=True)
    assert should_deliver_digest(uid, "t1") is True
