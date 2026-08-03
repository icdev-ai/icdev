# CUI // SP-CTI
"""Tests for rule-level scorecard exemptions with approval and audit (idp-score-04).

The acceptance contract is one sentence and every clause of it is a test here:

  1. an exempt rule excludes the component from **that rule only**
  2. an exempt rule does **not** block ladder progression
  3. the exemption writes an **append-only** record naming approver and reason

Plus the two invariants that make those worth anything:

  * ``autoApprove`` is OFF by default, so a request waives nothing until
    somebody approves it
  * an exemption with no approver or no reason is INERT — it does not waive

The store is exercised through the **real migration** rather than a
hand-written CREATE TABLE, because the bug class this is most exposed to is an
INSERT naming a column the live schema lacks: it raises, is swallowed by the
caller, and reports success while persisting nothing. Connections come from
``tools.db.storage.get_connection`` rather than raw ``sqlite3``, because the
module writes ``%s`` placeholders and only the storage wrapper translates them
— a raw connection here would make the tests assert their own no-op.
"""
from __future__ import annotations

import importlib
import importlib.util
import textwrap
from pathlib import Path

import pytest

from tools.idp.exemptions import (
    INSERT_COLUMNS,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING,
    STATUS_REVOKED,
    ExemptionError,
    ExemptionPolicy,
    active_grants,
    approve_exemption,
    attribution_defect,
    current_states,
    deny_exemption,
    history,
    request_exemption,
    revoke_exemption,
)
from tools.idp.scorecard import evaluate, load_scorecard, parse_scorecard
from tools.iqe.executor import register_collection

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_DIR = REPO_ROOT / "tools" / "db" / "migrations" / "20260803030514_idp_rule_exemptions"

SCORECARD = "test-exempt"

# `bravo` fails the Gold rule only, so one waiver is exactly the difference
# between Bronze and Gold for it. `charlie` fails both, which is the shape that
# exercises "waived out of every gating rule".
FIXTURE_ROWS = [
    {"key": "alpha", "rls_clean": True, "has_owner": True},
    {"key": "bravo", "rls_clean": True, "has_owner": False},
    {"key": "charlie", "rls_clean": False, "has_owner": False},
]

CARD_YAML = f"""
key: {SCORECARD}
name: Test Exemptions
collection: test.exempt
adapter_module: tools.iqe
ladder:
  levels:
  - name: Bronze
    rank: 1
  - name: Gold
    rank: 2
rules:
- identifier: rls-clean
  level: Bronze
  weight: 10
  expression: foreach c in test.exempt where c.rls_clean == true select c.key
- identifier: has-owner
  level: Gold
  weight: 30
  expression: foreach c in test.exempt where c.has_owner == true select c.key
"""


def _load_migration(name: str):
    """Import the shipped migration by path (its dir name is not an identifier)."""
    spec = importlib.util.spec_from_file_location(
        f"_mig_exempt_{name}", MIGRATION_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card(extra: str = ""):
    import yaml

    return parse_scorecard(yaml.safe_load(textwrap.dedent(CARD_YAML + extra)), source_path="<test>")


@pytest.fixture(autouse=True)
def _register_fixture_collection():
    register_collection("test.exempt", lambda conn=None: [dict(r) for r in FIXTURE_ROWS])
    yield


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A storage connection over a temp SQLite DB with the migration applied."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "exemptions.db"))
    _load_migration("up").up(connection)
    yield connection
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


def _grant(conn, rule="has-owner", component="bravo", **kwargs):
    """Request + approve in one step, the way an operator would."""
    request_exemption(
        SCORECARD,
        rule,
        component,
        reason=kwargs.pop("reason", "Vendor-managed; ICDEV cannot assign an owner."),
        requested_by=kwargs.pop("requested_by", "alice"),
        expires=kwargs.pop("expires", None),
        policy=kwargs.pop("policy", ExemptionPolicy()),
        conn=conn,
    )
    return approve_exemption(
        SCORECARD,
        rule,
        component,
        approved_by=kwargs.pop("approved_by", "bob"),
        decision_reason=kwargs.pop("decision_reason", "Confirmed with the vendor."),
        policy=ExemptionPolicy(),
        conn=conn,
    )


# ---------------------------------------------------------------------------
# Acceptance 1 — an exemption excludes the component from THAT RULE ONLY
# ---------------------------------------------------------------------------


def test_exempt_rule_leaves_the_denominator_rather_than_paying_out(conn):
    """The rule stops applying. It is not scored as a pass."""
    _grant(conn)
    report = evaluate(_card(), conn=conn, today="2026-08-03")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")

    assert outcome["status"] == "exempt"
    assert outcome["counted"] is False, "an exempt rule must leave the denominator"
    # Only the 10-point rule is left, and bravo passes it. If the exemption had
    # been *credited* the totals would read 40/40 — same 100%, but a waiver
    # would then be worth 30 points, which is the incentive to avoid.
    assert (bravo["earned_weight"], bravo["total_weight"]) == (10, 10)
    assert bravo["score"] == 100.0


def test_the_exemption_touches_no_other_rule_and_no_other_component(conn):
    _grant(conn)
    report = evaluate(_card(), conn=conn, today="2026-08-03")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    other = next(o for o in bravo["rules"] if o["identifier"] == "rls-clean")
    assert other["status"] == "pass", "waiving one rule must not touch the others"

    # alpha is graded on the full rule set — an exemption is per (rule, entity).
    alpha = next(r for r in report["results"] if r["entity"] == "alpha")
    assert alpha["total_weight"] == 40
    assert all(o["status"] == "pass" for o in alpha["rules"])

    # charlie still fails the same rule bravo was waived from.
    charlie = next(r for r in report["results"] if r["entity"] == "charlie")
    assert next(o for o in charlie["rules"] if o["identifier"] == "has-owner")["status"] == "fail"

    rule = next(r for r in report["rules"] if r["identifier"] == "has-owner")
    assert rule["exempt"] == 1
    assert rule["passing"] == 1, "the waiver must not be counted as a pass"


# ---------------------------------------------------------------------------
# Acceptance 2 — an exempt rule does not block ladder progression
# ---------------------------------------------------------------------------


def test_exempt_rule_does_not_block_the_ladder(conn):
    before = evaluate(_card(), conn=conn, today="2026-08-03")
    assert next(r for r in before["results"] if r["entity"] == "bravo")["level"] == "Bronze"

    _grant(conn)

    after = evaluate(_card(), conn=conn, today="2026-08-03")
    assert next(r for r in after["results"] if r["entity"] == "bravo")["level"] == "Gold"


def test_a_component_exempt_from_everything_is_not_promoted_to_the_top(conn):
    """The vacuous-truth guard still holds with exemptions in play.

    A rung met because every rule on it was waived is not evidence of anything.
    When NOTHING gating remains, the entity is unranked rather than crowned —
    otherwise waiving every rule would be the fastest route to Platinum.
    """
    _grant(conn, rule="has-owner", component="charlie")
    _grant(
        conn,
        rule="rls-clean",
        component="charlie",
        reason="Third-party store; RLS is enforced upstream.",
    )

    report = evaluate(_card(), conn=conn, today="2026-08-03")
    charlie = next(r for r in report["results"] if r["entity"] == "charlie")
    assert charlie["level"] is None
    assert charlie["score"] is None, "nothing was measured, so there is no score"


# ---------------------------------------------------------------------------
# Acceptance 3 — an append-only record naming approver and reason
# ---------------------------------------------------------------------------


def test_the_grant_writes_an_append_only_record_naming_approver_and_reason(conn):
    _grant(conn)

    events = history(SCORECARD, conn=conn)
    assert [e["event"] for e in events] == ["requested", "approved"]
    assert [e["event_index"] for e in events] == [1, 2]

    approval = events[-1]
    assert approval["decided_by"] == "bob", "the record must name WHO approved it"
    assert approval["reason"] == "Vendor-managed; ICDEV cannot assign an owner."
    assert approval["decision_reason"] == "Confirmed with the vendor."
    assert approval["requested_by"] == "alice"
    assert approval["status"] == STATUS_APPROVED

    # Append-only: revoking adds a third row, it does not remove the first two.
    revoke_exemption(
        SCORECARD, "has-owner", "bravo",
        revoked_by="carol", decision_reason="Owner assigned.",
        policy=ExemptionPolicy(), conn=conn,
    )
    after = history(SCORECARD, conn=conn)
    assert [e["event"] for e in after] == ["requested", "approved", "revoked"]
    assert after[1]["decided_by"] == "bob", "the original approver is still on the record"


def test_the_evaluated_outcome_carries_the_approver(conn):
    """The waiver is attributed where it is *used*, not only where it is stored."""
    _grant(conn)
    report = evaluate(_card(), conn=conn, today="2026-08-03")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert outcome["exemption"]["approved_by"] == "bob"
    assert outcome["exemption"]["source"] == "approval-log"
    assert outcome["message"] == "Vendor-managed; ICDEV cannot assign an owner."

    active = report["exemptions"]["active"]
    assert len(active) == 1 and active[0]["approved_by"] == "bob"


def test_every_inserted_column_exists_in_the_live_schema(conn):
    """The swallowed-INSERT bug class, pinned."""
    live = {
        str(dict(row)["name"])
        for row in conn.execute("PRAGMA table_info(idp_rule_exemptions)").fetchall()
    }
    missing = set(INSERT_COLUMNS) - live
    assert not missing, f"INSERT names columns absent from the migration: {sorted(missing)}"

    _grant(conn)
    total = dict(conn.execute("SELECT COUNT(*) AS n FROM idp_rule_exemptions").fetchone())
    assert total["n"] == 2, "the write must really land, not merely not raise"


def test_revoking_stops_the_waiver_and_the_rule_applies_again(conn):
    _grant(conn)
    revoke_exemption(
        SCORECARD, "has-owner", "bravo",
        revoked_by="carol", decision_reason="Owner assigned.",
        policy=ExemptionPolicy(), conn=conn,
    )

    report = evaluate(_card(), conn=conn, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert outcome["status"] == "fail"
    assert bravo["level"] == "Bronze"
    assert report["exemptions"]["inert_count"] == 1


# ---------------------------------------------------------------------------
# The approval step — autoApprove is OFF by default
# ---------------------------------------------------------------------------


def test_a_pending_request_waives_nothing(conn):
    request_exemption(
        SCORECARD, "has-owner", "bravo",
        reason="Vendor-managed.", requested_by="alice",
        policy=ExemptionPolicy(), conn=conn,
    )

    state = current_states(SCORECARD, conn=conn)
    assert len(state) == 1 and state[0]["status"] == STATUS_PENDING
    assert active_grants(SCORECARD, conn=conn) == []

    report = evaluate(_card(), conn=conn, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert outcome["status"] == "fail", "an unapproved request must not waive the rule"
    assert bravo["level"] == "Bronze"


def test_auto_approve_defaults_off_on_a_shipped_scorecard():
    card = load_scorecard("component-readiness")
    assert card.exemption_policy.auto_approve is False
    assert card.exemption_policy.enabled is True


def test_auto_approve_when_enabled_records_both_events(conn):
    """Auto-approval still leaves a record — including that nobody reviewed it."""
    row = request_exemption(
        SCORECARD, "has-owner", "bravo",
        reason="Vendor-managed.", requested_by="alice",
        policy=ExemptionPolicy(auto_approve=True), conn=conn,
    )
    assert row["status"] == STATUS_APPROVED
    assert row["auto_approved"] == 1
    assert row["decided_by"] == "alice"

    events = history(SCORECARD, conn=conn)
    assert [e["event"] for e in events] == ["requested", "approved"], (
        "the request is a separate event, so 'nobody reviewed this' stays visible"
    )
    assert events[-1]["self_approved"] == 1


def test_a_denied_request_waives_nothing(conn):
    request_exemption(
        SCORECARD, "has-owner", "bravo",
        reason="Vendor-managed.", requested_by="alice",
        policy=ExemptionPolicy(), conn=conn,
    )
    deny_exemption(
        SCORECARD, "has-owner", "bravo",
        denied_by="bob", decision_reason="An owner can be assigned.",
        policy=ExemptionPolicy(), conn=conn,
    )

    assert current_states(SCORECARD, conn=conn)[0]["status"] == STATUS_DENIED
    report = evaluate(_card(), conn=conn, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "fail"


def test_policy_disabled_makes_every_grant_inert(conn):
    _grant(conn)
    disabled = _card(
        """
exemptions:
  enabled: false
  grants: []
"""
    )
    report = evaluate(disabled, conn=conn, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "fail"
    assert report["exemptions"]["active_count"] == 0
    assert report["exemptions"]["inert_count"] == 1


# ---------------------------------------------------------------------------
# Attribution — an unattributed exemption does not apply
# ---------------------------------------------------------------------------


def test_a_yaml_grant_without_an_approver_is_inert():
    unattributed = _card(
        """
exemptions:
  grants:
  - identifier: has-owner
    entity: bravo
    reason: Vendor-managed.
"""
    )
    report = evaluate(unattributed, conn=None, today="2026-08-03")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "fail"
    inert = report["exemptions"]["inert"]
    assert len(inert) == 1
    assert "no approver" in inert[0]["defect"]


def test_a_yaml_grant_with_an_approver_and_reason_applies():
    attributed = _card(
        """
exemptions:
  grants:
  - identifier: has-owner
    entity: bravo
    reason: Vendor-managed; ICDEV cannot assign an owner.
    approvedBy: platform-lead@example.mil
    approvedAt: '2026-08-03'
    expires: '2099-01-01'
"""
    )
    report = evaluate(attributed, conn=None, today="2026-08-03")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert outcome["status"] == "exempt"
    assert outcome["exemption"]["approved_by"] == "platform-lead@example.mil"
    assert outcome["exemption"]["source"] == "scorecard"
    assert bravo["level"] == "Gold"


def test_a_placeholder_approver_is_not_an_approver():
    """`approvedBy: TBD` is the same failure as `owner: TBD` — nobody."""
    stubbed = _card(
        """
exemptions:
  grants:
  - identifier: has-owner
    entity: bravo
    reason: Vendor-managed.
    approvedBy: TBD
"""
    )
    report = evaluate(stubbed, conn=None, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "fail"


def test_an_expired_grant_stops_applying():
    expired = _card(
        """
exemptions:
  grants:
  - identifier: has-owner
    entity: bravo
    reason: Temporary waiver.
    approvedBy: platform-lead@example.mil
    expires: '2026-01-01'
"""
    )
    report = evaluate(expired, conn=None, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "fail"
    assert "expired" in report["exemptions"]["inert"][0]["defect"]


def test_a_revocation_in_the_log_overrides_a_grant_still_in_the_yaml(conn):
    """Otherwise revoking a waiver would require a code change to take effect."""
    request_exemption(
        SCORECARD, "has-owner", "bravo",
        reason="Vendor-managed.", requested_by="alice",
        policy=ExemptionPolicy(), conn=conn,
    )
    approve_exemption(
        SCORECARD, "has-owner", "bravo",
        approved_by="bob", policy=ExemptionPolicy(), conn=conn,
    )
    revoke_exemption(
        SCORECARD, "has-owner", "bravo",
        revoked_by="carol", decision_reason="Owner assigned.",
        policy=ExemptionPolicy(), conn=conn,
    )

    still_in_yaml = _card(
        """
exemptions:
  grants:
  - identifier: has-owner
    entity: bravo
    reason: Vendor-managed.
    approvedBy: platform-lead@example.mil
"""
    )
    report = evaluate(still_in_yaml, conn=conn, today="2026-08-03")
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "fail"


def test_attribution_defect_reports_why(conn):
    assert attribution_defect(status=STATUS_APPROVED, approved_by="bob", reason="ok") == ""
    assert "not approved" in attribution_defect(
        status=STATUS_PENDING, approved_by="", reason="ok"
    )
    assert "no reason" in attribution_defect(
        status=STATUS_APPROVED, approved_by="bob", reason=""
    )
    assert "disabled" in attribution_defect(
        status=STATUS_APPROVED, approved_by="bob", reason="ok",
        policy=ExemptionPolicy(enabled=False),
    )


# ---------------------------------------------------------------------------
# Write-time validation
# ---------------------------------------------------------------------------


def test_a_request_without_a_reason_is_refused(conn):
    with pytest.raises(ExemptionError, match="reason"):
        request_exemption(
            SCORECARD, "has-owner", "bravo",
            reason="  ", requested_by="alice",
            policy=ExemptionPolicy(), conn=conn,
        )
    assert history(SCORECARD, conn=conn) == []


def test_an_approval_without_an_approver_is_refused(conn):
    request_exemption(
        SCORECARD, "has-owner", "bravo",
        reason="Vendor-managed.", requested_by="alice",
        policy=ExemptionPolicy(), conn=conn,
    )
    with pytest.raises(ExemptionError, match="name who"):
        approve_exemption(
            SCORECARD, "has-owner", "bravo",
            approved_by="TBD", policy=ExemptionPolicy(), conn=conn,
        )
    assert current_states(SCORECARD, conn=conn)[0]["status"] == STATUS_PENDING


def test_approving_something_that_was_never_requested_is_refused(conn):
    with pytest.raises(ExemptionError, match="no exemption"):
        approve_exemption(
            SCORECARD, "has-owner", "bravo",
            approved_by="bob", policy=ExemptionPolicy(), conn=conn,
        )


def test_a_second_request_for_a_live_exemption_is_refused(conn):
    _grant(conn)
    with pytest.raises(ExemptionError, match="already has"):
        request_exemption(
            SCORECARD, "has-owner", "bravo",
            reason="Again.", requested_by="alice",
            policy=ExemptionPolicy(), conn=conn,
        )
    # …but it can be re-requested once revoked, and the log shows the full arc.
    revoke_exemption(
        SCORECARD, "has-owner", "bravo",
        revoked_by="carol", policy=ExemptionPolicy(), conn=conn,
    )
    row = request_exemption(
        SCORECARD, "has-owner", "bravo",
        reason="The vendor situation is back.", requested_by="alice",
        policy=ExemptionPolicy(), conn=conn,
    )
    assert row["event_index"] == 4
    assert current_states(SCORECARD, conn=conn)[0]["status"] == STATUS_PENDING


def test_notifications_are_off_by_default_and_name_who_was_told(conn, monkeypatch):
    """`userSpecificNotifications` addresses people, and records which people."""
    sent: list[dict] = []

    class _FakeGateway:
        def send(self, **kwargs):
            sent.append(kwargs)
            return {"deliveries": {}}

    # Patched via import_module + setattr, not the dotted-string form: the root
    # `tools` package is a shim onto `icdev.tools`, and the string form resolves
    # to a different module object than the lazy import inside `_notify` uses.
    gateway_module = importlib.import_module("tools.notifications.gateway")
    monkeypatch.setattr(
        gateway_module, "NotificationGateway", lambda *a, **k: _FakeGateway()
    )

    row = _grant(conn)
    assert row["notified"] == [], "off by default — no notification is attempted"
    assert sent == []

    request_exemption(
        SCORECARD, "rls-clean", "charlie",
        reason="Upstream store enforces RLS.", requested_by="alice",
        policy=ExemptionPolicy(user_specific_notifications=True), conn=conn,
    )
    assert len(sent) == 1
    assert sent[0]["metadata"]["recipients"] == ["alice"]

    state = next(
        s for s in current_states(SCORECARD, conn=conn)
        if s["rule_identifier"] == "rls-clean"
    )
    # Persisted as JSON on the event row: the log says WHO was told, not merely
    # that something was sent.
    assert "alice" in str(state["notified"])


# ---------------------------------------------------------------------------
# Degradation — no table must not take the scorecard down
# ---------------------------------------------------------------------------


def test_evaluation_survives_a_missing_exemption_table(conn):
    conn.execute("DROP TABLE idp_rule_exemptions")
    conn.commit()

    report = evaluate(_card(), conn=conn, today="2026-08-03")
    assert report["entity_count"] == len(FIXTURE_ROWS)
    assert report["exemptions"]["active_count"] == 0


def test_the_shipped_scorecard_still_parses_and_declares_a_policy():
    card = load_scorecard("component-readiness")
    assert card.exemption_policy.to_dict() == {
        "enabled": True,
        "auto_approve": False,
        "user_specific_notifications": False,
    }
    assert card.exemptions == ()


def test_revoked_status_constant_is_what_the_store_writes(conn):
    _grant(conn)
    revoke_exemption(
        SCORECARD, "has-owner", "bravo",
        revoked_by="carol", policy=ExemptionPolicy(), conn=conn,
    )
    assert current_states(SCORECARD, conn=conn)[0]["status"] == STATUS_REVOKED
