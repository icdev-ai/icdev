# CUI // SP-CTI
"""Engineering standards register with a lifecycle, on the IDP (icx-coe-01).

Covers:
  * the register: one entry per standard (id, version, owner role, sources by reference, scope, check-as-code); every seed
    validates, a seed is ``adopted`` only when its check resolves, ICDEV[OS] references are pinned at a commit with a
    digest and enter as proposals; the WG 22 citation catalog (args/standards_registry.yaml) is untouched and its reader
    still validates citations;
  * a standard without a check is a wish: adoption REFUSES an entry with no check-as-code and no named manual evidence
    step (and one whose scorecard rule does not exist), before any step is taken (planted);
  * the lifecycle propose -> review -> adopt -> deprecate through the three declared templates, each a person's step in
    wf_decision_steps, bound to the entry digest read; the author is never the reviewer or the signer; nothing advances
    on its own (order enforced); an agent only drafts and can take no step;
  * seeds are recorded by the labelled bootstrap identity, never a person, idempotently;
  * the ledger is append-only and hash-chained; a forged row breaks the chain and the register refuses to change it;
  * the page, the routes, the twin (findings with threads that resolve, replay) and the MCP tools.

Run: pytest tests/test_icx_coe_01_standards.py -v
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
#: Labelled test persons (synthetic): the author of a draft, and a steward who is not.
AUTHOR = {"id": "u-coe01-author", "role": "developer"}
STEWARD = {"id": "u-coe01-steward", "role": "reviewer"}
#: An author who also holds the steward's role: only separation of duties stops them.
AUTHOR_STEWARD = {"id": "u-coe01-author-steward", "role": "isso"}
AGENT = {"id": "agent:coe01-drafter", "role": "service"}

DRAFT = {
    "id": "ENG-COE01-TEST", "title": "A test standard (synthetic)", "version": "1.0", "owner": "developer",
    "scope": "Synthetic: the coe01 test suite only.", "sources": [{"path": "tools/idp/scorecard.py"}],
    "check": {"scorecard_rule": {"scorecard": "component-readiness", "rule": "e2e-spec"}},
}
WISH = {**DRAFT, "id": "ENG-COE01-WISH", "check": None}


@pytest.fixture()
def coe_db(wf_db, monkeypatch):
    """The platform DB (conftest SQLite) with coe_standard_events and the wf_ tables."""
    return wf_db


def _sql(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _std():
    from tools.idp import standards

    return standards


def _view(sid):
    return _std().standard_view(sid)


def _to_review(entry=DRAFT, author=AUTHOR):
    s = _std()
    s.propose(entry, author)
    v = _view(entry["id"])
    return v


# ── the register ─────────────────────────────────────────────────────────────


def test_the_register_validates_and_seeds_by_reference():
    s = _std()
    reg = s.load_register()
    assert s.validate_register(reg) == []
    assert reg["seed_identity"].startswith("bootstrap:")
    ids = [s.normalise(e)["id"] for e in reg["standards"]]
    assert len(ids) == len(set(ids))
    for want in ("ENG-SEC-GATES", "ENG-RED-FIRST", "ENG-COHERENCE", "ENG-FIVE-RULES", "ENG-GUARD-HOOKS",
                 "ICOS-INTERFACES", "ICOS-CONTROLS"):
        assert want in ids, want
    for e in reg["standards"]:
        n = s.normalise(e)
        assert n["owner"] in reg["owner_roles"] and n["sources"] and n["scope"]
        if e["seed"] == "adopted":
            assert s.resolve_check(e)["resolved"], n["id"]
        for src in n["sources"]:
            if "repo" in src:   # another repo's file: pinned at a commit with its digest, never copied here
                assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]) and re.fullmatch(r"[0-9a-f]{64}", src["sha256"])
                assert not (ROOT / src["path"]).exists() or src["repo"] != "icdev-os" or "interfaces" not in src["path"]
    icos = [e for e in reg["standards"] if s.normalise(e)["id"].startswith("ICOS-")]
    assert icos and all(e["seed"] == "proposed" for e in icos), "ICDEV[OS] registers are proposed, never adopted here"


def test_every_owner_role_is_a_dashboard_role():
    from tools.dashboard.auth import VALID_DASHBOARD_ROLES

    assert set(_std().load_register()["owner_roles"]) <= set(VALID_DASHBOARD_ROLES)


def test_the_citation_catalog_is_untouched_and_its_reader_still_validates():
    from tools.writing import standards_validator

    data = yaml.safe_load((ROOT / "args/standards_registry.yaml").read_text(encoding="utf-8"))
    assert set(data) == {"standards"} and "NIST_SP_800-53" in data["standards"]
    out = standards_validator.validate_citations("Per NIST SP 800-53 AC-2 and OWASP A01 we limit access.")
    assert out and isinstance(out, (dict, list))
    # An engineering standard cites a catalog key; an unknown key is refused.
    s = _std()
    assert "NIST_SP_800-53" in s.catalog_keys()
    bad = {**DRAFT, "sources": [{"catalog": "NOT_A_KEY"}]}
    assert any("not a key" in e for e in s.validate_entry(bad))


@pytest.mark.parametrize("check, resolved", [
    ({"scorecard_rule": {"scorecard": "component-readiness", "rule": "completeness-gate"}}, True),
    ({"scorecard_rule": {"scorecard": "component-readiness", "rule": "no-such-rule"}}, False),
    ({"scorecard_rule": {"scorecard": "no-such-card", "rule": "e2e-spec"}}, False),
    ({"manual_evidence": {"name": "red-first proof", "procedure": "read the artifact",
                          "produced_by": "tools/ci/red_first_gate.py"}}, True),
    ({"manual_evidence": {"name": "x", "procedure": "y", "produced_by": "tools/no/such/file.py"}}, False),
    ({"manual_evidence": {"name": "", "procedure": "read it"}}, False),
    (None, False),
])
def test_a_check_resolves_only_when_it_exists(check, resolved):
    out = _std().resolve_check({**DRAFT, "check": check})
    assert out["resolved"] is resolved, out
    if not resolved and check is None:
        assert "wish" in out["why"]


# ── a standard without a check is a wish (planted) ───────────────────────────


@pytest.mark.parametrize("check", [
    None,
    {"manual_evidence": {"name": "", "procedure": ""}},
    {"scorecard_rule": {"scorecard": "component-readiness", "rule": "a-rule-nobody-wrote"}},
])
def test_adoption_refuses_a_standard_without_a_check(coe_db, check):
    s = _std()
    entry = {**WISH, "check": check}
    s.propose(entry, AUTHOR)
    v = _view(WISH["id"])
    assert v["check"]["resolved"] is False
    s.review(WISH["id"], "1.0", decision="endorse", reason="reviewed (synthetic)", read_digest=v["entry_digest"],
             actor=STEWARD)
    steps_before = _sql(coe_db, "SELECT COUNT(*) FROM wf_decision_steps WHERE template_id = ?",
                        ("decl-wft-coe-standard-adopt",))[0][0]
    with pytest.raises(s.StandardRefused) as exc:
        s.adopt(WISH["id"], "1.0", decision="sign", reason="sign it (synthetic)", read_digest=v["entry_digest"],
                actor=STEWARD)
    assert exc.value.code == "no_check" and exc.value.status == 409 and "wish" in exc.value.message
    assert _sql(coe_db, "SELECT COUNT(*) FROM wf_decision_steps WHERE template_id = ?",
                ("decl-wft-coe-standard-adopt",))[0][0] == steps_before, "refused before any step is taken"
    assert _view(WISH["id"])["state"] == "reviewed" and _view(WISH["id"])["in_force"] is None
    assert not _sql(coe_db, "SELECT 1 FROM coe_standard_events WHERE event = 'adopted' AND standard_id = ?",
                    (WISH["id"],))


# ── the lifecycle ────────────────────────────────────────────────────────────


def test_propose_review_adopt_deprecate_with_history(coe_db):
    s = _std()
    p = s.propose(DRAFT, AUTHOR, note="please review (synthetic)")
    assert p["state"] == "proposed" and p["author_kind"] == "person" and p["check"]["resolved"]
    v = _view(DRAFT["id"])
    assert v["next"] == ["review"] and v["builders"] == {AUTHOR["id"]: "authored version 1.0 of this standard"}
    r = s.review(DRAFT["id"], "1.0", decision="endorse", reason="sound (synthetic)", read_digest=v["entry_digest"],
                 actor=STEWARD)
    assert r["state"] == "reviewed" and r["decision_step"]["template_id"] == "decl-wft-coe-standard-review"
    assert _view(DRAFT["id"])["next"] == ["adopt"]
    a = s.adopt(DRAFT["id"], "1.0", decision="sign", reason="adopted (synthetic)", read_digest=v["entry_digest"],
                actor=STEWARD)
    assert a["state"] == "adopted" and a["decision_step"]["decided_by"] == STEWARD["id"]
    v = _view(DRAFT["id"])
    assert v["in_force"] == "1.0" and v["adopted_by"] == STEWARD["id"] and v["adopted_kind"] == "person"
    assert [h["event"] for h in v["history"]] == ["proposed", "reviewed", "adopted"]
    assert v["history"][-1]["detail"]["check"]["rule"] == "e2e-spec"
    assert v["history"][-1]["detail"]["sources"][0]["sha256"]
    # the steps are a person's, in the declared ledger, bound to the digest read
    rows = _sql(coe_db, "SELECT template_id, actor, decision, payload_json FROM wf_decision_steps "
                        "WHERE subject = ? ORDER BY created_at", ("standard:ENG-COE01-TEST@1.0",))
    assert [(t, a, d) for t, a, d, _ in rows] == [
        ("decl-wft-coe-standard-review", STEWARD["id"], "endorse"),
        ("decl-wft-coe-standard-adopt", STEWARD["id"], "sign")]
    assert all(json.loads(pj)["entry_digest"] == v["entry_digest"] for *_, pj in rows)
    d = s.deprecate(DRAFT["id"], "1.0", reason="retired (synthetic)", read_digest=v["entry_digest"],
                    superseded_by="ENG-COE01-NEXT", actor=STEWARD)
    assert d["state"] == "deprecated"
    v = _view(DRAFT["id"])
    assert v["state"] == "deprecated" and v["in_force"] is None and v["next"] == []
    assert [h["event"] for h in v["history"]] == ["proposed", "reviewed", "adopted", "deprecated"]
    assert v["history"][-1]["detail"]["superseded_by"] == "ENG-COE01-NEXT" and v["chain"]["intact"]


def test_a_new_version_supersedes_the_one_in_force(coe_db):
    s = _std()
    v = _to_review()
    s.review(DRAFT["id"], "1.0", decision="endorse", reason="ok", read_digest=v["entry_digest"], actor=STEWARD)
    s.adopt(DRAFT["id"], "1.0", decision="sign", reason="ok", read_digest=v["entry_digest"], actor=STEWARD)
    with pytest.raises(s.StandardRefused, match="exists"):
        s.propose(DRAFT, AUTHOR)
    s.propose({**DRAFT, "version": "1.1", "scope": "Synthetic, revised."}, AUTHOR)
    v2 = _view(DRAFT["id"])
    assert v2["latest_version"] == "1.1" and v2["in_force"] == "1.0", "1.0 stays in force while 1.1 is a draft"
    with pytest.raises(s.StandardRefused, match="still proposed"):
        s.propose({**DRAFT, "version": "1.2"}, AUTHOR)
    s.review(DRAFT["id"], "1.1", decision="endorse", reason="ok", read_digest=v2["entry_digest"], actor=STEWARD)
    s.adopt(DRAFT["id"], "1.1", decision="sign", reason="ok", read_digest=v2["entry_digest"], actor=STEWARD)
    v3 = _view(DRAFT["id"])
    assert v3["in_force"] == "1.1"
    assert {x["version"]: x["state"] for x in v3["versions"]} == {"1.0": "superseded", "1.1": "adopted"}


def test_the_author_is_never_the_reviewer_or_the_signer(coe_db):
    s = _std()
    v = _to_review(author=AUTHOR_STEWARD)     # holds a steward's role, and authored it
    with pytest.raises(s.StandardRefused) as exc:
        s.review(DRAFT["id"], "1.0", decision="endorse", reason="my own", read_digest=v["entry_digest"],
                 actor=AUTHOR_STEWARD)
    assert exc.value.status == 403 and exc.value.code == "separation"
    s.review(DRAFT["id"], "1.0", decision="endorse", reason="ok", read_digest=v["entry_digest"], actor=STEWARD)
    with pytest.raises(s.StandardRefused) as exc:
        s.adopt(DRAFT["id"], "1.0", decision="sign", reason="my own", read_digest=v["entry_digest"],
                actor=AUTHOR_STEWARD)
    assert exc.value.status == 403 and exc.value.code == "separation"
    s.adopt(DRAFT["id"], "1.0", decision="sign", reason="ok", read_digest=v["entry_digest"], actor=STEWARD)
    with pytest.raises(s.StandardRefused) as exc:
        s.deprecate(DRAFT["id"], "1.0", reason="my own", read_digest=v["entry_digest"], actor=AUTHOR_STEWARD)
    assert exc.value.code == "separation"


def test_a_steward_role_is_needed_and_a_reason(coe_db):
    s = _std()
    v = _to_review()
    other_dev = {"id": "u-coe01-dev2", "role": "developer"}
    with pytest.raises(s.StandardRefused) as exc:
        s.review(DRAFT["id"], "1.0", decision="endorse", reason="ok", read_digest=v["entry_digest"], actor=other_dev)
    assert exc.value.status == 403 and exc.value.code == "role"
    with pytest.raises(s.StandardRefused) as exc:
        s.review(DRAFT["id"], "1.0", decision="endorse", reason="", read_digest=v["entry_digest"], actor=STEWARD)
    assert exc.value.code == "reason"
    with pytest.raises(s.StandardRefused) as exc:
        s.review(DRAFT["id"], "1.0", decision="endorse", reason="ok", read_digest=v["entry_digest"], actor=None)
    assert exc.value.status == 401


def test_nothing_advances_out_of_order_and_a_step_is_bound_to_the_digest(coe_db):
    s = _std()
    v = _to_review()
    with pytest.raises(s.StandardRefused, match="adopt needs it reviewed") as exc:
        s.adopt(DRAFT["id"], "1.0", decision="sign", reason="skip review", read_digest=v["entry_digest"],
                actor=STEWARD)
    assert exc.value.code == "out_of_order"
    with pytest.raises(s.StandardRefused) as exc:
        s.deprecate(DRAFT["id"], "1.0", reason="not in force", read_digest=v["entry_digest"], actor=STEWARD)
    assert exc.value.code == "out_of_order"
    with pytest.raises(s.StandardRefused) as exc:
        s.review(DRAFT["id"], "1.0", decision="endorse", reason="ok", read_digest="0" * 64, actor=STEWARD)
    assert exc.value.code == "digest"
    s.review(DRAFT["id"], "1.0", decision="return", reason="needs a sharper scope", read_digest=v["entry_digest"],
             actor=STEWARD)
    assert _view(DRAFT["id"])["state"] == "returned" and _view(DRAFT["id"])["next"] == []
    with pytest.raises(s.StandardRefused, match="adopt needs it reviewed"):
        s.adopt(DRAFT["id"], "1.0", decision="sign", reason="x", read_digest=v["entry_digest"], actor=STEWARD)
    # the author revises: a new version, proposed again
    s.propose({**DRAFT, "version": "1.1"}, AUTHOR)
    assert _view(DRAFT["id"])["state"] == "proposed"


def test_an_agent_only_drafts(coe_db):
    from tools.mcp.gap_handlers import handle_coe_standard_propose

    s = _std()
    out = handle_coe_standard_propose({"agent_id": "coe01-drafter", **{k: DRAFT[k] for k in
                                       ("id", "title", "version", "owner", "scope")},
                                       "sources": ["tools/idp/scorecard.py", "catalog:OWASP_TOP_10"],
                                       "check_kind": "scorecard_rule", "scorecard": "component-readiness",
                                       "rule": "e2e-spec"})
    assert out["draft"]["state"] == "proposed" and out["draft"]["author_kind"] == "agent"
    v = _view(DRAFT["id"])
    assert v["author"] == "agent:coe01-drafter" and v["author_kind"] == "agent"
    for fn, kw in ((s.review, {"decision": "endorse"}), ):
        with pytest.raises(s.StandardRefused) as exc:
            fn(DRAFT["id"], "1.0", reason="an agent deciding", read_digest=v["entry_digest"], actor=AGENT, **kw)
        assert exc.value.code in ("not_a_person", "separation")
    s.review(DRAFT["id"], "1.0", decision="endorse", reason="a person reviewed the agent's draft",
             read_digest=v["entry_digest"], actor=STEWARD)
    with pytest.raises(s.StandardRefused) as exc:
        s.adopt(DRAFT["id"], "1.0", decision="sign", reason="an agent signing", read_digest=v["entry_digest"],
                actor={"id": "agent:other", "role": "service"})
    assert exc.value.code == "not_a_person"


def test_a_malformed_draft_is_refused(coe_db):
    s = _std()
    for bad in ({**DRAFT, "id": "lowercase"}, {**DRAFT, "owner": "nobody"}, {**DRAFT, "sources": []},
                {**DRAFT, "sources": [{"path": "../etc/passwd"}]}, {**DRAFT, "sources": [{"url": "http://x"}]}):
        with pytest.raises(s.StandardRefused) as exc:
            s.propose(bad, AUTHOR)
        assert exc.value.code == "invalid", bad
    with pytest.raises(s.StandardRefused) as exc:
        s.propose(DRAFT, None)
    assert exc.value.status == 401


# ── seeds ────────────────────────────────────────────────────────────────────


def test_seeds_are_recorded_by_the_bootstrap_identity_never_a_person(coe_db):
    s = _std()
    first = s.seed()
    second = s.seed()
    assert first["seeded"] and second["seeded"] == [] and sorted(second["skipped"]) == sorted(first["seeded"])
    rows = _sql(coe_db, "SELECT standard_id, actor, actor_kind, step_id, detail_json FROM coe_standard_events")
    assert {r[1] for r in rows} == {first["seed_identity"]} and {r[2] for r in rows} == {"bootstrap"}
    assert all(r[3] == "" for r in rows), "a seed takes no person's step"
    reg = {s.normalise(e)["id"]: e["seed"] for e in s.load_register()["standards"]}
    view = {x["id"]: x for x in s.register_view()["standards"]}
    for sid, seed in reg.items():
        assert view[sid]["state"] == seed
        if seed == "adopted":
            assert view[sid]["in_force"] and view[sid]["adopted_kind"] == "bootstrap" and view[sid]["check"]["resolved"]
    assert not _sql(coe_db, "SELECT 1 FROM wf_decision_steps WHERE template_id LIKE 'decl-wft-coe-%'")
    # an ICOS reference waits for a person, like any draft
    icos = _view("ICOS-INTERFACES")
    assert icos["state"] == "proposed" and icos["next"] == ["review"]
    assert icos["sources"][0]["kind"] == "pinned"


def test_a_seed_without_a_check_cannot_enter_adopted():
    s = _std()
    reg = s.load_register()
    planted = {**reg, "standards": [{**WISH, "sources": [{"path": "tools/idp/scorecard.py"}], "seed": "adopted"}]}
    errors = s.validate_register(planted)
    assert any("seeded as adopted but its check does not resolve" in e for e in errors)
    with pytest.raises(s.StandardRefused) as exc:
        s.seed(register=planted)
    assert exc.value.code == "invalid_register"
    assert any("bootstrap" in e for e in s.validate_register({**reg, "seed_identity": "u-a-real-person"}))


# ── the ledger ───────────────────────────────────────────────────────────────


def test_the_ledger_is_append_only_and_a_forged_row_breaks_the_chain(coe_db):
    s = _std()
    v = _to_review()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        _sql(coe_db, "UPDATE coe_standard_events SET actor = 'someone-else'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        _sql(coe_db, "DELETE FROM coe_standard_events")
    # a forged 'adopted' row appended after the proposal, with a hash that does not match its content
    (head,) = _sql(coe_db, "SELECT row_hash FROM coe_standard_events WHERE standard_id = ?", (DRAFT["id"],))[0]
    _sql(coe_db, "INSERT INTO coe_standard_events (event_id, standard_id, version, event, actor, actor_kind, "
                 "entry_digest, entry_json, detail_json, prev_hash, row_hash, created_at) "
                 "VALUES ('coe-forged', ?, '1.0', 'adopted', 'mallory', 'person', ?, ?, '{\"seed\": null}', ?, ?, "
                 "'2026-11-17T00:00:00.000000Z')",
         (DRAFT["id"], v["entry_digest"], json.dumps(v["entry"]), head, "f" * 64))
    v2 = _view(DRAFT["id"])
    assert v2["chain"]["intact"] is False
    assert {"event_id": "coe-forged", "standard_id": DRAFT["id"],
            "problem": "row hash does not match its content"} in v2["chain"]["problems"]
    assert s.register_view()["chain"]["intact"] is False
    with pytest.raises(s.StandardRefused) as exc:
        s.review(DRAFT["id"], "1.0", decision="endorse", reason="x", read_digest=v["entry_digest"], actor=STEWARD)
    assert exc.value.code == "chain_broken"


def test_two_events_cannot_follow_the_same_one(coe_db):
    s = _std()
    s.propose(DRAFT, AUTHOR)
    st = s._state(DRAFT["id"], s._connection())[0]
    conn = s._connection()
    try:
        with pytest.raises(s.StandardRefused) as exc:
            s._append(conn, standard_id=DRAFT["id"], version="9.9", event="proposed", actor="x",
                      actor_kind="person", entry=DRAFT, prev_hash=s.GENESIS)
        assert exc.value.code == "concurrent"
    finally:
        conn.close()
    assert st["history"][0]["prev_hash"] == s.GENESIS


# ── the console and the routes ───────────────────────────────────────────────


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


def test_the_routes_take_the_lifecycle_and_refuse_what_they_must(coe_db):
    author, steward = _app(AUTHOR), _app(STEWARD)
    body = {"id": "ENG-COE01-ROUTE", "title": "Route test (synthetic)", "version": "1.0", "owner": "developer",
            "scope": "Synthetic.", "sources": "tools/idp/scorecard.py\ncatalog:OWASP_TOP_10",
            "check_kind": "manual_evidence", "evidence_name": "red-first proof",
            "evidence_procedure": "read the artifact", "produced_by": "tools/ci/red_first_gate.py"}
    resp = author.post("/idp/api/standards/propose", json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    d = steward.get("/idp/api/standards/ENG-COE01-ROUTE").get_json()
    assert d["check"]["resolved"] and [x["kind"] for x in d["sources"]] == ["path", "catalog"]
    step = {"version": "1.0", "reason": "ok (synthetic)", "digest": d["entry_digest"]}
    assert author.post("/idp/api/standards/ENG-COE01-ROUTE/review",
                       json={**step, "decision": "endorse"}).status_code == 403
    assert steward.post("/idp/api/standards/ENG-COE01-ROUTE/adopt",
                        json={**step, "decision": "sign"}).status_code == 409
    assert steward.post("/idp/api/standards/ENG-COE01-ROUTE/review",
                        json={**step, "decision": "endorse"}).status_code == 200
    out = steward.post("/idp/api/standards/ENG-COE01-ROUTE/adopt", json={**step, "decision": "sign"}).get_json()
    assert out["state"] == "adopted" and out["decision_step"]["template_id"] == "decl-wft-coe-standard-adopt"
    assert steward.post("/idp/api/standards/ENG-COE01-ROUTE/deprecate", json=step).status_code == 200
    reg = steward.get("/idp/api/standards").get_json()
    assert {s["id"]: s["state"] for s in reg["standards"]}["ENG-COE01-ROUTE"] == "deprecated"
    # a check-less proposal is accepted as a draft and refused at adoption
    wish = {**body, "id": "ENG-COE01-ROUTE-WISH", "check_kind": ""}
    assert author.post("/idp/api/standards/propose", json=wish).get_json()["check"]["resolved"] is False
    w = steward.get("/idp/api/standards/ENG-COE01-ROUTE-WISH").get_json()
    wstep = {"version": "1.0", "reason": "ok", "digest": w["entry_digest"]}
    assert steward.post("/idp/api/standards/ENG-COE01-ROUTE-WISH/review",
                        json={**wstep, "decision": "endorse"}).status_code == 200
    resp = steward.post("/idp/api/standards/ENG-COE01-ROUTE-WISH/adopt", json={**wstep, "decision": "sign"})
    assert resp.status_code == 409 and resp.get_json()["code"] == "no_check"
    assert steward.get("/idp/api/standards/NOPE-NOPE").status_code == 404


def _page_app(user):
    """A fresh app carrying the dashboard's rendering context (base.html reads ``nav_tree``), with the IDP on it.

    Not the shared ``tools.dashboard.app`` singleton: nothing other tests observe is mutated (the same shape as
    tests/test_ace_instance_page_render.py::_isolated_app).
    """
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


def test_the_page_renders_the_register_and_its_controls(coe_db):
    _std().seed()
    resp = _page_app(STEWARD).get("/idp/standards")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for want in ("Engineering standards", "A standard without a check is a wish", "decl-wft-coe-standard-adopt",
                 "idp.idp_api_standard_adopt", "idp.idp_api_standard_review", "idp.idp_api_standard_deprecate",
                 "idp.idp_api_standard_propose", "idp.idp_api_standards_twin", "ENG-RED-FIRST"):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/idp/standards.html").read_text(encoding="utf-8"), \
        "routes are named by endpoint id"
    portal = (ROOT / "tools/dashboard/templates/idp/page.html").read_text(encoding="utf-8")
    assert 'href="/idp/standards"' in portal, "linked from the Developer Portal (a nav page)"
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    idp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "idp")
    assert "/idp/standards" in [lk["href"] for lk in idp["nav"]["links"]], "and from the Canvases nav"


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_observes_with_threads_that_resolve_and_replays(coe_db, monkeypatch):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    s = _std()
    s.seed()
    v = _to_review(WISH)
    adapter = governance.adapter_for("idp")
    # icx-coe-02: the same canvas observes the CoE's exemptions and ADRs too
    # icx-coe-03: and its conformance bundles
    assert adapter.observes == ("coe.standards", "coe.exemptions", "coe.adrs", "coe.conformance", "coe.golden_paths",
                                "finops.focus")
    env = governance.observe(adapter)
    assert env["extra"]["recorded"] is True
    rules = {x["rule_id"] for x in env["violations"]}
    assert "coe.in_force.bootstrap_only" in rules, "a seed is not a person's signature, and the twin says so"
    assert all(x["thread_id"] for x in env["violations"])
    boot = next(x for x in env["violations"] if x["rule_id"] == "coe.in_force.bootstrap_only")
    t = governance.resolve_thread(boot["thread_id"])
    assert t["resolved"] and t["record"]["actor_kind"] == "bootstrap" and t["record"]["event"] == "seeded"
    assert env["extra"]["summary"]["chain_intact"] is True and env["extra"]["summary"]["drafts"] >= 1
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep["reason"]
    # a standard in force whose check stops resolving is a finding, threaded to its adoption
    s.review(WISH["id"], "1.0", decision="return", reason="no check", read_digest=v["entry_digest"], actor=STEWARD)
    s.propose({**DRAFT}, AUTHOR)
    d = _view(DRAFT["id"])
    s.review(DRAFT["id"], "1.0", decision="endorse", reason="ok", read_digest=d["entry_digest"], actor=STEWARD)
    s.adopt(DRAFT["id"], "1.0", decision="sign", reason="ok", read_digest=d["entry_digest"], actor=STEWARD)
    real = s._scorecards
    monkeypatch.setattr(s, "_scorecards", lambda root: {k: v - {"e2e-spec"} for k, v in real(root).items()})
    env2 = governance.observe(adapter, record=False)
    gone = [x for x in env2["violations"] if x["rule_id"] == "coe.in_force.check_unresolved"]
    assert gone and any(DRAFT["id"] in x["title"] for x in gone)
    adopted_event = _view(DRAFT["id"])["history"][-1]["event_id"]
    assert any(x["thread_id"] == f"coe_standard:{adopted_event}" for x in gone)


def test_the_twin_says_unverified_when_the_ledger_cannot_be_read(monkeypatch):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    s = _std()

    def boom(*_a, **_k):
        raise RuntimeError("no database")

    monkeypatch.setattr(s, "read_events", boom)
    env = governance.observe(governance.adapter_for("idp"), record=False)
    assert env["verdict"] != "pass" and env["coverage_score"] < 1


# ── AI-native: read, and propose only as a draft ─────────────────────────────


def test_the_mcp_tools_read_and_only_draft(coe_db):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_coe_standards
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    _std().seed()
    assert {"coe_standards", "coe_standard_propose"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["coe_standards"] is True and tool_authorization("coe_standards")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["coe_standard_propose"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    tier = policy["tiers"].get("coe_standard_propose", policy["default"])
    assert tier in policy["halts"], "an agent's draft halts at the approval gate"
    out = handle_coe_standards({})
    assert {x["id"] for x in out["register"]["standards"]} >= {"ENG-RED-FIRST", "ICOS-CONTROLS"}
    one = handle_coe_standards({"standard_id": "ENG-RED-FIRST"})
    assert one["standard"]["history"][0]["actor_kind"] == "bootstrap"
    assert handle_coe_standards({"standard_id": "NOPE-NOPE"})["code"] == "unknown_standard"


def test_the_cli_validates_and_lists(coe_db, capsys):
    s = _std()
    assert s.main(["--validate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert s.main(["--seed", "--json"]) == 0
    capsys.readouterr()
    assert s.main(["--list"]) == 0
    assert "ENG-RED-FIRST" in capsys.readouterr().out
