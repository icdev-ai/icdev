# CUI // SP-CTI
"""Scorecard exemptions that expire (icx-coe-02).

What the card promises, each clause a test here:

  * an exemption has a declared scope (rule, service, standard, repo) and links to the engineering
    standards it waives, each one checked against icx-coe-01's register;
  * an exemption with no expiry is refused, and so is one not after today or beyond the horizon;
  * an expired exemption no longer exempts: it FAILS the IDP scorecard and the CI check (planted);
  * granting and renewing are a person's declared steps, never the requester's (or the renewal's
    asker's), with a reason, bound to the exemption's digest; nothing renews by itself;
  * the log is append-only (triggers) and hash-chained; legacy approved exemptions with no expiry
    are given one by a labelled bootstrap identity, never granted again, and the twin says so;
  * the pages, the routes, the twin (threads that resolve, replay) and the MCP read and draft.

People here are labelled test persons (synthetic). All on the conftest SQLite platform DB.
"""
from __future__ import annotations

import json
import sqlite3
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
REQUESTER = {"id": "u-coe02-requester", "role": "reviewer"}
APPROVER = {"id": "u-coe02-approver", "role": "reviewer"}
OTHER = {"id": "u-coe02-other", "role": "isso"}
DEVELOPER = {"id": "u-coe02-dev", "role": "developer"}
CARD = "component-readiness"

#: The exemption log keeps UTC dates.
TODAY = datetime.now(timezone.utc).date()


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _clock(days_ago: int):
    """An injected clock ``days_ago`` days in the past (how a test reaches an expired exemption)."""
    at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return lambda: at


@pytest.fixture()
def coe_db(wf_db):
    """The platform DB (conftest SQLite) with idp_rule_exemptions (both migrations) and the wf_ tables."""
    return wf_db


def _ex():
    from tools.idp import exemptions

    return exemptions


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _request(component="coe02-svc", rule="e2e-spec", expires=None, **kw):
    return _ex().request_exemption(CARD, rule, component, reason=kw.pop("reason", "Headless (synthetic)."),
                                   requested_by=kw.pop("requested_by", REQUESTER["id"]),
                                   expires=expires or _d(60), **kw)


def _grant(row, actor=APPROVER, **kw):
    e = _ex()
    current = e._latest(e._open_connection(), row["exemption_key"])
    return e.decide_exemption(row["exemption_key"], decision="grant", reason=kw.pop("reason", "Confirmed (synthetic)."),
                              read_digest=e.exemption_digest(current), actor=actor, **kw)


# ── expiry is mandatory ──────────────────────────────────────────────────────


def test_a_request_without_an_expiry_is_refused(coe_db):
    e = _ex()
    for missing in (None, ""):
        with pytest.raises(e.ExemptionError) as exc:
            e.request_exemption(CARD, "e2e-spec", "coe02-svc", reason="r", requested_by="u", expires=missing)
        assert exc.value.code == "no_expiry"
    assert _rows(coe_db, "SELECT COUNT(*) FROM idp_rule_exemptions")[0][0] == 0
    # read back: an approved row with no expiry does not apply
    assert "no expiry" in e.attribution_defect(status="approved", approved_by="b", reason="r", expires="")


def test_an_expiry_must_be_after_today_and_within_the_horizon(coe_db):
    e = _ex()
    cfg = e.load_config()
    for bad in (_d(0), _d(-1), _d(int(cfg["max_expiry_days"]) + 1), "2026-13-01", "soon"):
        with pytest.raises(e.ExemptionError) as exc:
            _request(expires=bad)
        assert exc.value.code == "bad_expiry", bad
    row = _request(expires=_d(int(cfg["max_expiry_days"])))
    assert row["status"] == "pending" and row["expires_on"] == _d(int(cfg["max_expiry_days"]))


def test_scopes_resolve_and_link_the_standards_they_waive(coe_db):
    e = _ex()
    rule = _request(component="coe02-a")
    assert rule["scope_kind"] == "rule" and json.loads(rule["standard_ids"]) == ["ENG-E2E-SPEC"], \
        "the rule's standard is linked from the register"
    std = _request(component="coe02-b", rule="", scope="standard", scope_ref="ENG-PAGE-COMPLETENESS")
    assert std["rule_identifier"] == "completeness-gate" and std["scope_ref"] == "ENG-PAGE-COMPLETENESS"
    assert "ENG-PAGE-COMPLETENESS" in json.loads(std["standard_ids"])
    svc = _request(component="coe02-c", rule="", scope="service")
    assert svc["rule_identifier"] == e.ANY and {"ENG-E2E-SPEC", "ENG-PAGE-COMPLETENESS"} <= set(
        json.loads(svc["standard_ids"]))
    repo = _request(component="", rule="has-owner", scope="repo", scope_ref="icdev")
    assert repo["component_key"] == e.ANY and repo["scope_ref"] == "icdev"
    for kw, code in (({"scope": "galaxy"}, "scope"),
                     ({"scope": "repo", "scope_ref": "nowhere", "component": "", "rule": "has-owner"}, "scope"),
                     ({"scope": "standard", "scope_ref": "ENG-RED-FIRST", "rule": ""}, "standard"),
                     ({"scope": "standard", "scope_ref": "ENG-NOPE", "rule": ""}, "standard"),
                     ({"rule": "no-such-rule"}, "scope"),
                     ({"standards": ["ENG-NOT-REAL"]}, "standard")):
        with pytest.raises(e.ExemptionError) as exc:
            _request(**{"component": "coe02-z", **kw})
        assert exc.value.code == code, kw


# ── an expired exemption FAILS (planted) ─────────────────────────────────────

_CARD_YAML = """
key: test-exempt-coe02
name: Test exemptions (coe02, synthetic)
collection: test.exempt.coe02
adapter_module: tools.iqe
ladder:
  levels:
  - {name: Bronze, rank: 1}
  - {name: Gold, rank: 2}
rules:
- identifier: rls-clean
  level: Bronze
  weight: 10
  expression: foreach c in test.exempt.coe02 where c.rls_clean == true select c.key
- identifier: has-owner
  level: Gold
  weight: 30
  expression: foreach c in test.exempt.coe02 where c.has_owner == true select c.key
"""


@pytest.fixture()
def synthetic_card():
    from tools.idp.scorecard import parse_scorecard
    from tools.iqe.executor import register_collection

    rows = [{"key": "alpha", "rls_clean": True, "has_owner": True},
            {"key": "bravo", "rls_clean": True, "has_owner": False}]
    register_collection("test.exempt.coe02", lambda conn=None: [dict(r) for r in rows])
    return parse_scorecard(yaml.safe_load(textwrap.dedent(_CARD_YAML)), source_path="<test>")


def test_an_expired_exemption_fails_the_scorecard_and_the_ci_check(coe_db, synthetic_card):
    """PLANTED. Granted 40 days ago for 30 days (an injected clock): it has expired, and it fails."""
    from tools.db.storage import get_connection
    from tools.idp.scorecard import evaluate

    e = _ex()
    policy = e.ExemptionPolicy()
    then = _clock(40)
    row = e.request_exemption(synthetic_card.key, "has-owner", "bravo", reason="Vendor-managed (synthetic).",
                              requested_by=REQUESTER["id"], expires=(TODAY - timedelta(days=10)).isoformat(),
                              policy=policy, clock=then)
    current = e._latest(e._open_connection(), row["exemption_key"])
    e.decide_exemption(row["exemption_key"], decision="grant", reason="ok (synthetic)",
                       read_digest=e.exemption_digest(current), actor=APPROVER, policy=policy, clock=then)
    conn = get_connection()
    try:
        report = evaluate(synthetic_card, exemptions_conn=conn)
        before = evaluate(synthetic_card, exemptions_conn=conn, today=(TODAY - timedelta(days=20)).isoformat())
    finally:
        conn.close()
    # 20 days ago it applied; today it has expired and fails
    assert before["exemptions"]["gate"] == "pass" and before["exemptions"]["active_count"] == 1
    bravo = next(r for r in before["results"] if r["entity"] == "bravo")
    assert next(o for o in bravo["rules"] if o["identifier"] == "has-owner")["status"] == "exempt"
    ex = report["exemptions"]
    assert ex["gate"] == "fail" and ex["expired_count"] == 1 and ex["active_count"] == 0
    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    out = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert out["status"] == "fail" and out["message"].startswith("exemption expired on ")
    assert out["exemption"]["expired"] is True and bravo["level"] == "Bronze"
    # the CI check fails on it, and says which
    chk = e.check()
    assert chk["ok"] is False and [x["exemption_key"] for x in chk["expired"]] == [row["exemption_key"]]
    assert e.main(["--check", "--gate", "--json"]) == 1
    assert e.main(["--check", "--json"]) == 0, "without --gate it only reports"
    # the page view and the classification say expired; expiry decided nothing, it only removed the exemption
    view = e.exemptions_view()
    assert view["gate"] == "fail" and view["exemptions"][0]["state"] == "expired"
    assert [h["event"] for h in e.history(conn=None)] == ["requested", "approved"]


def test_a_config_grant_or_auto_approve_fails_the_check(coe_db, tmp_path):
    e = _ex()
    assert e.check()["ok"] is True, "the shipped scorecards carry no grant and no autoApprove"
    card = yaml.safe_load((ROOT / "args/scorecards/component-readiness.yaml").read_text(encoding="utf-8"))
    card["exemptions"]["grants"] = [{"identifier": "e2e-spec", "entity": "x", "reason": "r",
                                     "approvedBy": "someone", "expires": _d(30)}]
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(card), encoding="utf-8")
    chk = e.check(directory=tmp_path)
    assert chk["ok"] is False and chk["config_grants"][0]["rule"] == "e2e-spec"
    from tools.idp.scorecard import load_scorecard

    parsed = load_scorecard(CARD, tmp_path)
    assert "not granted by a person's declared step" in parsed.exemptions[0].defect(_d(0))
    card["exemptions"]["grants"] = []
    card["exemptions"]["autoApprove"] = True
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(card), encoding="utf-8")
    assert e.check(directory=tmp_path)["auto_approve"] == [CARD]
    with pytest.raises(e.ExemptionError) as exc:
        _request(policy=e.ExemptionPolicy(auto_approve=True))
    assert exc.value.code == "auto_approve"


# ── the declared steps ───────────────────────────────────────────────────────


def test_the_requester_is_never_the_approver_and_a_step_is_bound_to_the_digest(coe_db):
    e = _ex()
    row = _request()
    key = row["exemption_key"]
    current = e._latest(e._open_connection(), key)
    dg = e.exemption_digest(current)
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(key, decision="grant", reason="mine", read_digest=dg, actor=REQUESTER)
    assert exc.value.code == "separation"
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(key, decision="grant", reason="ok", read_digest="0" * 64, actor=APPROVER)
    assert exc.value.code == "digest"
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(key, decision="grant", reason="", read_digest=dg, actor=APPROVER)
    assert exc.value.code == "reason"
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(key, decision="grant", reason="ok", read_digest=dg, actor=DEVELOPER)
    assert exc.value.code == "role"
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(key, decision="grant", reason="ok", read_digest=dg,
                           actor={"id": "agent:x", "role": "service"})
    assert exc.value.code == "not_a_person"
    with pytest.raises(e.ExemptionError) as exc:
        e.approve_exemption(CARD, "e2e-spec", "coe02-svc", actor="bob", decision_reason="a string is no person")
    assert exc.value.code == "no_person"
    out = e.decide_exemption(key, decision="grant", reason="Confirmed (synthetic).", read_digest=dg, actor=APPROVER)
    assert out["status"] == "approved" and out["decided_by"] == APPROVER["id"] and out["self_approved"] == 0
    step = _rows(coe_db, "SELECT template_id, actor, subject, payload_digest FROM wf_decision_steps "
                         "WHERE step_id = ?", (out["step_id"],))[0]
    assert step[:3] == ("decl-wft-coe-exemption-grant", APPROVER["id"], f"exemption:{key}")
    payload = {"exemption_key": key, "digest": dg, "expires_on": row["expires_on"], "scope": "rule",
               "standard_ids": ["ENG-E2E-SPEC"]}
    from tools.workflow_hitl.declared import payload_digest

    assert step[3] == payload_digest(payload), "the step is bound to the exemption's digest"
    # a request whose expiry has passed is never granted, even by someone else
    late = _request(component="coe02-late", expires=_d(5), clock=_clock(0))
    cur = e._latest(e._open_connection(), late["exemption_key"])
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(late["exemption_key"], decision="grant", reason="ok", read_digest=e.exemption_digest(cur),
                           actor=APPROVER, clock=lambda: datetime.now(timezone.utc) + timedelta(days=6))
    assert exc.value.code == "bad_expiry"


def test_renewal_is_a_persons_step_and_nothing_renews_by_itself(coe_db):
    e = _ex()
    row = _request()
    key = row["exemption_key"]
    _grant(row)
    with pytest.raises(e.ExemptionError) as exc:
        e.request_renewal(key, expires=_d(30), reason="shorter", requested_by=OTHER)
    assert exc.value.code == "bad_expiry", "a renewal runs past the current expiry"
    asked = e.request_renewal(key, expires=_d(120), reason="Still headless (synthetic).", requested_by=OTHER)
    assert asked["event"] == "renewal_requested" and asked["expires_on"] == _d(60), "asking changes nothing"
    assert e.classify(asked, TODAY.isoformat()) == "renewal_pending"
    with pytest.raises(e.ExemptionError) as exc:
        e.request_renewal(key, expires=_d(150), reason="again", requested_by=OTHER)
    assert exc.value.code == "exists"
    cur = e._latest(e._open_connection(), key)
    dg = e.exemption_digest(cur)
    for who in (REQUESTER, OTHER):         # the requester, and whoever asked for the renewal
        with pytest.raises(e.ExemptionError) as exc:
            e.renew_exemption(key, decision="renew", reason="mine", read_digest=dg, actor=who)
        assert exc.value.code == "separation", who
    out = e.renew_exemption(key, decision="renew", reason="Confirmed again (synthetic).", read_digest=dg,
                            actor=APPROVER)
    assert out["event"] == "renewed" and out["expires_on"] == _d(120)
    assert _rows(coe_db, "SELECT template_id FROM wf_decision_steps WHERE step_id = ?",
                 (out["step_id"],))[0][0] == "decl-wft-coe-exemption-renew"
    # a refused renewal leaves the expiry where it was: it lapses on it
    e.request_renewal(key, expires=_d(200), reason="longer (synthetic)", requested_by=OTHER)
    cur = e._latest(e._open_connection(), key)
    ref = e.renew_exemption(key, decision="refuse", reason="No (synthetic).", read_digest=e.exemption_digest(cur),
                            actor=APPROVER)
    assert ref["event"] == "renewal_refused" and ref["expires_on"] == _d(120)
    with pytest.raises(e.ExemptionError) as exc:
        e.renew_exemption(key, decision="renew", reason="x", read_digest="", actor=APPROVER)
    assert exc.value.code == "out_of_order", "no renewal waits: nothing to renew"
    # no code path appends a renewal on its own: only these functions write 'renewed', each through a step
    src = (ROOT / "tools/idp/exemptions.py").read_text(encoding="utf-8")
    assert src.count("event=EVENT_RENEWED if renewed") == 1 and "auto_renew" not in src


def test_revoking_is_a_stop_with_a_reason(coe_db):
    e = _ex()
    row = _request()
    _grant(row)
    with pytest.raises(e.ExemptionError) as exc:
        e.revoke_by_key(row["exemption_key"], revoked_by=OTHER, reason="")
    assert exc.value.code == "reason"
    out = e.revoke_by_key(row["exemption_key"], revoked_by=OTHER, reason="Fixed (synthetic).")
    assert out["status"] == "revoked" and out["step_id"] == ""
    with pytest.raises(e.ExemptionError) as exc:
        e.revoke_by_key(row["exemption_key"], revoked_by=OTHER, reason="again")
    assert exc.value.code == "out_of_order"
    # a new request may follow a revoked one, and starts pending
    assert _request()["status"] == "pending"


# ── the log ──────────────────────────────────────────────────────────────────


def test_the_log_is_append_only_and_hash_chained(coe_db):
    e = _ex()
    row = _request()
    _grant(row)
    events = e.history(conn=None)
    chk = e.verify_chain(events)
    assert chk == {"legacy": 0, "chained": 2, "problems": []}
    assert events[0]["prev_hash"] == e.GENESIS and events[1]["prev_hash"] == events[0]["row_hash"]
    for sql in ("UPDATE idp_rule_exemptions SET reason = 'x'", "DELETE FROM idp_rule_exemptions"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn = sqlite3.connect(str(coe_db))
            try:
                conn.execute(sql)
            finally:
                conn.close()
    forged = dict(events[1], reason="forged")
    assert e.verify_chain([events[0], forged])["problems"][0]["problem"] == "row hash does not match its content"
    # two rows cannot follow the same one
    conn = e._open_connection()
    try:
        with pytest.raises(e.ExemptionError) as exc:
            e._append_event(conn, current=events[0], **e._carry(events[0]), event="revoked", status="revoked",
                            decided_by="x", decision_reason="x", expires_on=None, notified=[])
        assert exc.value.code == "concurrent"
    finally:
        conn.close()
    # the INSERT names only columns the migrations create (the swallowed-INSERT bug class)
    from importlib import util as iu

    path = ROOT / "tools/db/migrations/20261118010237_coe_exemptions_adrs/up.py"
    spec = iu.spec_from_file_location("_mig_coe02", path)
    mig = iu.module_from_spec(spec)
    spec.loader.exec_module(mig)
    old = (ROOT / "tools/db/migrations/20260803030514_idp_rule_exemptions/up.py").read_text(encoding="utf-8")
    declared = {c for c, _ in mig.EXEMPTION_COLUMNS} | {
        c for c in e.INSERT_COLUMNS if f"\n    {c} " in old}
    assert set(e.INSERT_COLUMNS) <= declared


def test_legacy_exemptions_are_migrated_by_the_bootstrap_identity_never_granted(coe_db):
    """A row as the pre-icx-coe-02 code wrote it: approved, no expiry, no chain."""
    e = _ex()
    conn = sqlite3.connect(str(coe_db))
    try:
        for i, (event, status, by) in enumerate((("requested", "pending", None), ("approved", "approved", "bob")), 1):
            conn.execute("INSERT INTO idp_rule_exemptions (id, exemption_key, scorecard_key, rule_identifier, "
                         "component_key, event, status, event_index, reason, requested_by, decided_by, "
                         "auto_approved, self_approved, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,?)",
                         (f"legacy-{i}", f"{CARD}|has-owner|old-svc", CARD, "has-owner", "old-svc", event, status,
                          i, "Old waiver (synthetic).", "alice", by, "2026-08-01T00:00:00+00:00"))
        conn.commit()
    finally:
        conn.close()
    assert e.check()["no_expiry"] == [f"{CARD}|has-owner|old-svc"], "before the migration it fails the check"
    out = e.migrate_legacy()
    cfg = e.load_config()
    assert out["identity"] == cfg["migration_identity"] and out["identity"].startswith("bootstrap:")
    assert [m["exemption_key"] for m in out["migrated"]] == [f"{CARD}|has-owner|old-svc"]
    assert out["migrated"][0]["expires_on"] == _d(int(cfg["migration_grace_days"]))
    events = [x for x in e.history(conn=None) if x["component_key"] == "old-svc"]
    last = events[-1]
    assert last["event"] == "migrated" and last["actor"] == cfg["migration_identity"]
    assert last["actor_kind"] == "bootstrap" and last["decided_by"] == "bob", "still bob's grant, never re-granted"
    assert last["step_id"] == "" and last["status"] == "approved"
    assert e.verify_chain(events) == {"legacy": 2, "chained": 1, "problems": []}
    assert e.migrate_legacy()["migrated"] == [], "idempotent"
    assert e.check()["ok"] is True
    # the twin reports it for the owner, threaded to the migrated row
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    env = governance.observe(governance.adapter_for("idp"), record=False)
    mig = [x for x in env["violations"] if x["rule_id"] == "coe.exemption.migrated"]
    assert mig and mig[0]["thread_id"] == f"idp_exemption:{last['id']}"
    t = governance.resolve_thread(mig[0]["thread_id"])
    assert t["resolved"] and t["record"]["actor_kind"] == "bootstrap"


def test_the_migration_adds_columns_and_triggers(tmp_path, monkeypatch):
    """On a database the old migration made (no new columns, no triggers): the new one converges."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from importlib import util as iu

    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(tmp_path / "m.db"))
    try:
        def load(rel):
            spec = iu.spec_from_file_location(rel.replace("/", "_"), ROOT / rel)
            mod = iu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        load("tools/db/migrations/20260803030514_idp_rule_exemptions/up.py").up(conn)
        mig = load("tools/db/migrations/20261118010237_coe_exemptions_adrs/up.py")
        first = mig.up(conn)
        assert set(first["columns_added"]) == {c for c, _ in mig.EXEMPTION_COLUMNS}
        assert mig.up(conn)["columns_added"] == [], "a re-run converges"
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()}
        assert {"idp_rule_exemptions_no_update", "idp_rule_exemptions_no_delete",
                "coe_adr_events_no_update", "coe_adr_events_no_delete"} <= names
    finally:
        conn.close()


# ── the console, the routes ──────────────────────────────────────────────────


def _app(user):
    from tools.idp.blueprint import bp

    app = Flask(__name__, template_folder=str(ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""), "classification": "CUI"}

    app.register_blueprint(bp, url_prefix="/idp")
    return app.test_client()


def test_the_routes_take_the_steps_and_refuse_what_they_must(coe_db):
    req, appr = _app(REQUESTER), _app(APPROVER)
    body = {"scorecard": CARD, "scope": "rule", "rule": "e2e-spec", "component": "coe02-route",
            "reason": "Headless (synthetic).", "expires": _d(45)}
    assert req.post("/idp/api/exemptions/request", json={**body, "expires": ""}).get_json()["code"] == "no_expiry"
    resp = req.post("/idp/api/exemptions/request", json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    ref = resp.get_json()["ref"]
    d = appr.get(f"/idp/api/exemptions/{ref}").get_json()
    assert d["state"] == "pending" and d["next"] == ["grant", "revoke"] and REQUESTER["id"] in d["builders"]
    step = {"decision": "grant", "reason": "ok (synthetic)", "digest": d["digest"]}
    r = req.post(f"/idp/api/exemptions/{ref}/decide", json=step)
    assert r.status_code == 403 and r.get_json()["code"] == "separation"
    out = appr.post(f"/idp/api/exemptions/{ref}/decide", json=step).get_json()
    assert out["status"] == "approved" and out["decision_step"]["template_id"] == "decl-wft-coe-exemption-grant"
    lst = appr.get("/idp/api/exemptions").get_json()
    assert lst["gate"] == "pass" and lst["exemptions"][0]["state"] == "active"
    ahead = appr.get(f"/idp/api/exemptions?as_of={_d(46)}").get_json()
    assert ahead["look_ahead"] and ahead["gate"] == "fail" and ahead["exemptions"][0]["state"] == "expired"
    assert req.post(f"/idp/api/exemptions/{ref}/renewal",
                    json={"expires": _d(90), "reason": "longer (synthetic)"}).status_code == 201
    d = appr.get(f"/idp/api/exemptions/{ref}").get_json()
    assert d["state"] == "renewal_pending" and d["next"] == ["renew", "revoke"]
    assert req.post(f"/idp/api/exemptions/{ref}/renew", json={"decision": "renew", "reason": "x",
                                                               "digest": d["digest"]}).status_code == 403
    out = appr.post(f"/idp/api/exemptions/{ref}/renew",
                    json={"decision": "renew", "reason": "ok (synthetic)", "digest": d["digest"]}).get_json()
    assert out["expires_on"] == _d(90)
    assert req.post(f"/idp/api/exemptions/{ref}/revoke", json={"reason": ""}).status_code == 400
    assert req.post(f"/idp/api/exemptions/{ref}/revoke", json={"reason": "fixed (synthetic)"}).status_code == 200
    assert appr.get("/idp/api/exemptions/0000000000000000").status_code == 404


def test_rows_carry_the_writers_label_and_a_person_reads_back_their_request(coe_db):
    """The console reads the log through the row-security predicate at the session's level, so a
    request is labelled at its writer's level (Bell-LaPadula: write at your level) and read back."""
    from tools.security.security_context import SecurityContext
    from tools.idp.blueprint import bp

    e = _ex()
    with pytest.raises(e.ExemptionError) as exc:
        _request(component="coe02-label", classification="NOT-A-LABEL")
    assert exc.value.code == "label"
    cli = _request(component="coe02-cli")
    assert cli["classification"] == e.CLASSIFICATION, "no session: the module's label"
    app = Flask(__name__, template_folder=str(ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(REQUESTER)
        g.security_context = SecurityContext(user_id=REQUESTER["id"], role="reviewer", clearance_level=1,
                                             classification="CUI")

    app.register_blueprint(bp, url_prefix="/idp")
    client = app.test_client()
    out = client.post("/idp/api/exemptions/request", json={
        "scorecard": CARD, "rule": "e2e-spec", "component": "coe02-session", "reason": "r (synthetic)",
        "expires": _d(30)}).get_json()
    assert out["classification"] == "CUI"
    keys = [x["exemption_key"] for x in client.get("/idp/api/exemptions").get_json()["exemptions"]]
    assert out["exemption_key"] in keys, "read back through the predicate"
    assert cli["exemption_key"] not in keys, "a CUI session does not read the default CUI // SP-CTI label"
    assert client.get(f"/idp/api/exemptions/{out['ref']}").status_code == 200
    _grant(out)
    assert {r["classification"] for r in e.history(conn=None) if r["exemption_key"] == out["exemption_key"]} \
        == {"CUI"}, "later rows carry the first row's label"


def _page_app(user):
    from tools.dashboard.app import app as dash
    from tools.idp.blueprint import bp

    app = Flask(__name__, template_folder=dash.template_folder, static_folder=dash.static_folder)
    app.config.update(dash.config)
    app.jinja_env.filters.update(dash.jinja_env.filters)
    app.jinja_env.globals.update(dash.jinja_env.globals)
    app.template_context_processors[None].extend(dash.template_context_processors.get(None, []))
    app.secret_key = dash.secret_key or "test-secret"
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user)
        g.security_context = {"tenant_id": "default", "user_id": user["id"], "classification": "CUI"}

    app.register_blueprint(bp, url_prefix="/idp")
    return app.test_client()


def test_the_pages_render_and_link_from_the_standards_page(coe_db):
    _request()
    client = _page_app(APPROVER)
    html = client.get("/idp/exemptions").get_data(as_text=True)
    for want in ("Scorecard exemptions", "Every exemption expires", "decl-wft-coe-exemption-grant",
                 "decl-wft-coe-exemption-renew", "idp.idp_api_exemption_decide", "idp.idp_api_exemption_renew",
                 "idp.idp_api_exemption_renewal", "idp.idp_api_exemption_revoke", "idp.idp_api_exemption_request",
                 "idp.idp_api_standards_twin", "coe02-svc"):
        assert want in html, want
    for page in ("exemptions.html", "adrs.html"):
        assert "/api/" not in (ROOT / "tools/dashboard/templates/idp" / page).read_text(encoding="utf-8"), \
            "routes are named by endpoint id"
    std = client.get("/idp/standards").get_data(as_text=True)
    assert 'id="std-link-exemptions"' in std and 'href="/idp/exemptions"' in std and 'href="/idp/adrs"' in std
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    idp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "idp")
    hrefs = [lk["href"] for lk in idp["nav"]["links"]]
    assert "/idp/exemptions" in hrefs and "/idp/adrs" in hrefs
    portal = (ROOT / "tools/dashboard/templates/idp/page.html").read_text(encoding="utf-8")
    assert 'id="idp-exemption-gate"' in portal and 'href="/idp/exemptions"' in portal, \
        "the scorecard shows the exemption gate"


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_reports_expired_and_migrated_exemptions_with_threads(coe_db):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    e = _ex()
    then = _clock(40)
    row = _request(component="coe02-twin", expires=(TODAY - timedelta(days=10)).isoformat(), clock=then)
    cur = e._latest(e._open_connection(), row["exemption_key"])
    e.decide_exemption(row["exemption_key"], decision="grant", reason="ok", read_digest=e.exemption_digest(cur),
                       actor=APPROVER, clock=then)
    soon = _request(component="coe02-soon", expires=_d(3))
    _grant(soon)
    adapter = governance.adapter_for("idp")
    # icx-coe-03: and its conformance bundles
    assert adapter.observes == ("coe.standards", "coe.exemptions", "coe.adrs", "coe.conformance", "coe.golden_paths")
    env = governance.observe(adapter)
    assert env["extra"]["recorded"] is True
    found = {x["rule_id"]: x for x in env["violations"]}
    exp = found["coe.exemption.expired"]
    assert exp["severity"] == "high" and row["exemption_key"] in exp["title"]
    t = governance.resolve_thread(exp["thread_id"])
    assert t["resolved"] and t["source"] == "idp_rule_exemptions" and t["record"]["event"] == "approved"
    assert "coe.exemption.expiring" in found
    assert env["extra"]["summary"]["exemption_gate"] == "fail"
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")


def test_the_twin_says_unverified_when_the_log_cannot_be_read(monkeypatch):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    e = _ex()

    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(e, "_open_connection", boom)
    env = governance.observe(governance.adapter_for("idp"), record=False)
    chk = {c["id"]: c for c in env["extra"]["checks"]}
    assert chk["coe.exemptions.log"]["status"] == "unverified"
    assert env["verdict"] != "pass" and env["coverage_score"] < 1
    assert env["extra"]["summary"]["exemption_gate"] == "unknown", "nothing read is never a pass"


# ── AI-native: read, and draft only ──────────────────────────────────────────


def test_the_mcp_tools_read_and_only_draft(coe_db):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_coe_exemption_request, handle_coe_exemptions
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"coe_exemptions", "coe_exemption_request"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["coe_exemptions"] is True and tool_authorization("coe_exemptions")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["coe_exemption_request"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    tier = policy["tiers"].get("coe_exemption_request", policy["default"])
    assert tier in policy["halts"], "an agent's draft halts at the approval gate"
    assert handle_coe_exemption_request({"agent_id": "coe02-drafter", "rule": "e2e-spec", "component": "x",
                                         "reason": "r"})["code"] == "no_expiry"
    out = handle_coe_exemption_request({"agent_id": "coe02-drafter", "rule": "e2e-spec", "component": "coe02-mcp",
                                        "reason": "Drafted (synthetic).", "expires": _d(30)})
    assert out["draft"]["status"] == "pending" and out["draft"]["requested_by"] == "agent:coe02-drafter"
    assert out["draft"]["actor_kind"] == "agent"
    lst = handle_coe_exemptions({})["exemptions"]
    assert lst["exemptions"][0]["state"] == "pending"
    one = handle_coe_exemptions({"ref": out["draft"]["ref"]})["exemption"]
    assert one["next"] == ["grant", "revoke"]
    # an agent cannot take the step
    e = _ex()
    with pytest.raises(e.ExemptionError) as exc:
        e.decide_exemption(out["draft"]["exemption_key"], decision="grant", reason="x", read_digest=one["digest"],
                           actor={"id": "agent:coe02-drafter", "role": "service"})
    assert exc.value.code == "not_a_person"
