# CUI // SP-CTI
"""FORGE Academy tracks per Community of Practice, with xAPI out to ICDEV[OS] (icx-cop-01).

What the card promises, each clause a test here:

  * four tracks, one per CoP (engineering/AIDLC, AI and data, application, cloud), whose ids are the learning path
    ids ICDEV[OS]'s charters link; every mission is about this platform's own patterns and cites files that exist;
    no cloud-vendor training or certification content;
  * a mission is graded on the server against the declared key, which never leaves it; only a person attempts, and
    every attempt is recorded on the learner's own append-only, hash-chained record;
  * the xAPI statements are derived from that ledger alone: a pseudonymous account actor, ADL verb ids, the path's
    activity id, result.completion/success and the timestamp; progressed is never read as complete;
  * the icos-cop-academy/v1 batch meets ICDEV[OS]'s pinned schema, its digest recomputes, deterministic given the
    ledger and the time;
  * publishing is a person's declared step, never the preparer's, bound to the digest; an agent only drafts; the
    export carries its publication beside the batch and verifies against the schemas and the ledger;
  * the page and routes, the twin (threads that resolve, replay) and the MCP read and draft.

People here are labelled test persons (synthetic). All on the conftest SQLite platform DB.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
LEARNER = {"id": "u-cop01-learner", "role": "developer"}
OTHER = {"id": "u-cop01-learner-2", "role": "developer"}
PREPARER = {"id": "u-cop01-preparer", "role": "reviewer"}
PUBLISHER = {"id": "u-cop01-publisher", "role": "reviewer"}
SERVICE = {"id": "svc-cop01", "role": "service"}
AGENT = {"id": "agent:cop01-drafter", "role": "service"}
#: The learning path ids ICDEV[OS]'s CoP charters link (its args/cops.yaml learning_paths, osx-cop-01).
ICOS_PATHS = ["cop-engineering-foundations", "cop-ai-data-foundations", "cop-application-foundations",
              "cop-cloud-foundations"]
AT = "2026-11-18T12:00:00Z"


@pytest.fixture()
def cop_db(wf_db):
    """The platform DB (conftest SQLite) with the Academy ledger, the learners' pseudonyms and the wf_ tables."""
    return wf_db


def _t():
    from tools.idp import cop_tracks

    return cop_tracks


def _key(mission_id: str) -> dict:
    """The right answers to a mission, read from the declared config (as a learner who studied would give)."""
    t = _t()
    _, m = t.find_mission(t.load_config(), mission_id)
    return {q["id"]: q["answer"] for q in m["questions"]}


def _missions(path_id: str) -> list[str]:
    t = _t()
    return [m["id"] for tr in t.load_config()["tracks"] if tr["id"] == path_id for m in tr["missions"]]


def _complete(path_id: str, who: dict) -> None:
    for mid in _missions(path_id):
        assert _t().attempt(mid, _key(mid), who)["passed"]


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── the tracks ───────────────────────────────────────────────────────────────


def test_four_tracks_one_per_cop_on_our_own_patterns():
    t = _t()
    cfg = t.load_config()
    assert [tr["cop"] for tr in cfg["tracks"]] == ["engineering", "ai-data", "application", "cloud"]
    assert [tr["id"] for tr in cfg["tracks"]] == ICOS_PATHS, "the ids ICDEV[OS]'s charters link"
    assert cfg["xapi"]["path_prefix"] == "urn:icdev:academy:path:", "ICDEV[OS]'s academy.path_prefix"
    assert all(len(tr["missions"]) >= 3 for tr in cfg["tracks"])
    patterns = {m["pattern"] for tr in cfg["tracks"] for m in tr["missions"]}
    for want in ("red-first", "standards-register", "golden-paths", "twin", "signed-workflows", "marking",
                 "five-rules", "llm-router"):
        assert want in patterns, want
    # Owner: no cloud-vendor training or certification content.
    text = (ROOT / t.CONFIG_RELPATH).read_text(encoding="utf-8")
    body = text.split("tracks:", 1)[1]
    for banned in (r"\bAWS\b", r"\bAmazon\b", r"\bAzure\b", r"\bGCP\b", r"\bGoogle Cloud\b", r"[Cc]ertification exam",
                   r"Solutions Architect", r"Cloud Practitioner"):
        assert not re.search(banned, body), banned


def test_every_reading_exists_and_every_question_has_one_right_answer():
    t = _t()
    for tr in t.load_config()["tracks"]:
        for m in tr["missions"]:
            assert m["reading"], m["id"]
            for rel in m["reading"]:
                assert (ROOT / rel).exists(), f"{m['id']} cites {rel}, which does not exist"
            for q in m["questions"]:
                assert len(q["options"]) >= 2 and 0 <= q["answer"] < len(q["options"])
                assert len(set(q["options"])) == len(q["options"]), f"{m['id']}/{q['id']} repeats an option"


def test_a_bad_config_is_refused_never_half_read(tmp_path):
    t = _t()
    good = yaml.safe_load((ROOT / t.CONFIG_RELPATH).read_text(encoding="utf-8"))
    assert t.config_problems(good) == []
    two = json.loads(json.dumps(good))
    two["tracks"][3]["cop"] = "engineering"
    assert any("one track per CoP" in p for p in t.config_problems(two))
    bad = json.loads(json.dumps(good))
    bad["tracks"][0]["missions"][0]["questions"][0]["answer"] = 9
    bad["xapi"]["verbs"]["completed"] = "https://example.invalid/did"
    bad["tracks"][1]["missions"][0]["reading"] = []
    probs = " ".join(t.config_problems(bad))
    assert "an answer that is one of them" in probs and "ADL verb" in probs and "cites what it teaches" in probs
    p = tmp_path / "tracks.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(t.CopRefused) as exc:
        t.load_config(p)
    assert exc.value.code == "config"


# ── grading on the server ────────────────────────────────────────────────────


def test_answers_never_leave_the_server():
    t = _t()
    pub = t.public_tracks(t.load_config())
    assert "answer" not in json.dumps(pub)
    q = pub[0]["missions"][0]["questions"][0]
    assert set(q) == {"id", "prompt", "options"}


def test_grading_is_on_the_server_and_a_wrong_or_missing_answer_fails():
    t = _t()
    _, m = t.find_mission(t.load_config(), "eng-red-first")
    right = {q["id"]: q["answer"] for q in m["questions"]}
    assert t.grade(m, right)["passed"] is True and t.grade(m, right)["score"] == 3
    one_wrong = dict(right, q2=(right["q2"] + 1) % 3)
    g1 = t.grade(m, one_wrong)
    assert g1["passed"] is False and g1["wrong"] == ["q2"] and g1["score"] == 2
    assert t.grade(m, {k: v for k, v in right.items() if k != "q3"})["wrong"] == ["q3"], "missing is wrong"
    assert t.grade(m, {k: str(v) for k, v in right.items()})["passed"] is False, "a string is not an index"
    assert right["q1"] == 1 and t.grade(m, {"q1": True, "q2": right["q2"], "q3": right["q3"]})["passed"] is False, \
        "True == 1 in Python, and still not an answer"
    assert t.grade(m, None)["score"] == 0
    with pytest.raises(t.CopRefused) as exc:
        t.find_mission(t.load_config(), "no-such-mission")
    assert exc.value.status == 404


def test_an_attempt_is_a_persons_and_recorded_on_their_own_chain(cop_db):
    t = _t()
    for who in (SERVICE, AGENT, None, {"role": "developer"}):
        with pytest.raises(t.CopRefused) as exc:
            t.attempt("eng-red-first", _key("eng-red-first"), who)
        assert exc.value.code == "not_a_person"
    wrong = dict(_key("eng-red-first"), q1=2)
    first = t.attempt("eng-red-first", wrong, LEARNER)
    assert first["passed"] is False and first["event"] == "attempted" and first["statement"] is None
    second = t.attempt("eng-red-first", _key("eng-red-first"), LEARNER)
    assert second["passed"] is True and second["event"] == "completed"
    assert second["learner"] == first["learner"], "one pseudonym per learner, kept"
    assert re.fullmatch(r"lrn-[0-9a-f]{20}", second["learner"]) and LEARNER["id"] not in second["learner"]
    other = t.attempt("eng-red-first", _key("eng-red-first"), OTHER)
    assert other["learner"] != second["learner"]
    ch = t.chain(t.read_events())
    mine = ch[f"learner:{second['learner']}"]
    assert [r["event"] for r in mine] == ["attempted", "completed"] and ch["_problems"] == []
    assert mine[0]["prev_hash"] == t.GENESIS and mine[1]["prev_hash"] == mine[0]["row_hash"]
    assert json.loads(mine[0]["detail_json"])["wrong"] == ["q1"]
    assert "answers" not in mine[1]["detail_json"], "the ledger keeps the answers' digest, not the answers"


# ── xAPI statements ──────────────────────────────────────────────────────────


def test_statements_are_standard_xapi_derived_from_the_ledger_alone(cop_db):
    t = _t()
    cfg = t.load_config()
    out = t.attempt("cloud-marking", _key("cloud-marking"), LEARNER)
    s = out["statement"]
    assert s["verb"]["id"] == "http://adlnet.gov/expapi/verbs/progressed"
    assert s["result"] == {"completion": False}, "progressed is never read as complete"
    assert s["object"] == {"objectType": "Activity", "id": "urn:icdev:academy:path:cop-cloud-foundations"}
    assert s["actor"] == {"objectType": "Agent", "account": {"homePage": cfg["xapi"]["homepage"],
                                                             "name": out["learner"]}}
    assert "mbox" not in json.dumps(s) and LEARNER["id"] not in json.dumps(s), "pseudonymous: no name, no mailbox"
    uuid.UUID(s["id"])
    for mid in _missions("cop-cloud-foundations")[1:]:
        last = t.attempt(mid, _key(mid), LEARNER)
    done = last["statement"]
    assert done["verb"] == {"id": "http://adlnet.gov/expapi/verbs/completed", "display": {"en-US": "completed"}}
    assert done["result"] == {"completion": True, "success": True}
    completed = [r for r in t.read_events() if r["event"] == "completed" and r["learner"] == out["learner"]]
    assert done["timestamp"] == max(r["created_at"] for r in completed), "the time of the pass that made it so"
    again = t.statements(cfg, t.read_events())
    assert again == t.statements(cfg, t.read_events()) and done in again, "the same ledger, the same statements"
    # A pass against a mission the path no longer declares counts for nothing.
    shrunk = json.loads(json.dumps(cfg))
    shrunk["tracks"][3]["missions"] = [m for m in shrunk["tracks"][3]["missions"] if m["id"] != "cloud-marking"] + [
        {"id": "cloud-new", "title": "x", "reading": ["README.md"], "questions": []}]
    got = [x for x in t.statements(shrunk, t.read_events()) if x["object"]["id"].endswith("cop-cloud-foundations")]
    assert [x["verb"]["id"].rsplit("/", 1)[1] for x in got] == ["progressed"]


def test_a_learner_whose_record_does_not_verify_reports_nothing(cop_db):
    t = _t()
    _complete("cop-cloud-foundations", LEARNER)
    rows = t.read_events()
    assert len(t.statements(t.load_config(), rows)) == 1
    forged = [dict(r) for r in rows]
    forged[0]["mission_id"] = "cloud-append-only"
    probs = t.chain(forged)["_problems"]
    assert any(p["problem"] == "row hash does not match its content" for p in probs)
    assert t.statements(t.load_config(), forged) == [], "a chain that does not verify reports nothing"


# ── the batch against ICDEV[OS]'s schema ─────────────────────────────────────


def test_the_batch_meets_the_pinned_icos_schema_and_its_digest(cop_db):
    t = _t()
    _complete("cop-engineering-foundations", LEARNER)
    t.attempt("ai-governed-router", _key("ai-governed-router"), OTHER)
    b = t.build(at=AT)
    assert b["schema"] == "icos-cop-academy/v1" and b["paths"] == ICOS_PATHS
    assert b["producer"]["system"] == "icdev-it" and b["classification"] == "CUI // SP-CTI"
    assert t.validate(b) == [] and len(b["statements"]) == 2
    assert b["content_digest"] == t.content_digest(b), "SHA-256 over the batch without its digest, as ICDEV[OS] does it"
    tampered = json.loads(json.dumps(b))
    tampered["statements"][0]["result"]["success"] = False
    assert t.validate(tampered) == ["content_digest is not the digest of the batch's content"]
    extra = json.loads(json.dumps(b))
    extra["publication"] = {"signed_by": "x"}
    assert any("Additional properties" in e for e in t.validate(extra)), "the batch admits no extra field"
    ctx = json.loads(json.dumps(b))
    ctx["statements"][0]["context"] = {"registration": str(uuid.uuid4())}
    assert t.validate(ctx), "the statement shape ICDEV[OS] pins, nothing more"


def test_the_schemas_are_pinned(monkeypatch, tmp_path):
    t = _t()
    t.schema()
    t.publication_schema()
    readme = (ROOT / "context/icos/README.md").read_text(encoding="utf-8")
    assert t.SCHEMA_SHA256 in readme and t.PUBLICATION_SHA256 in readme
    assert "PROPOSED" in readme and '"x-icos-contract": "proposed"' in (ROOT / t.PUBLICATION_RELPATH).read_text()
    (tmp_path / "context/icos").mkdir(parents=True)
    for rel in (t.SCHEMA_RELPATH, t.PUBLICATION_RELPATH):
        (tmp_path / rel).write_text((ROOT / rel).read_text(encoding="utf-8").replace("Apache", "Apache "),
                                    encoding="utf-8")
    monkeypatch.setattr(t, "BASE_DIR", tmp_path)
    for fn in (t.schema, t.publication_schema):
        with pytest.raises(t.CopRefused) as exc:
            fn()
        assert exc.value.code == "schema", "a changed schema is refused, never silently used"


def test_the_batch_is_deterministic_given_the_ledger_and_the_time(cop_db, tmp_path, capsys):
    from tools.idp import cop_xapi_export

    t = _t()
    _complete("cop-application-foundations", LEARNER)
    a, b = t.build(at=AT), t.build(at=AT)
    assert a == b and t.build(at="2026-11-19T00:00:00Z")["content_digest"] != a["content_digest"]
    assert cop_xapi_export.main(["--preview", "--at", AT, "--out", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["content_digest"] == a["content_digest"] and out["published"] is False
    assert json.loads((tmp_path / "icos-cop-academy.preview.json").read_text()) == a


# ── publishing: a person, never the preparer ─────────────────────────────────


def test_publish_is_a_persons_step_never_the_preparers_bound_to_the_digest(cop_db):
    t = _t()
    _complete("cop-cloud-foundations", LEARNER)
    with pytest.raises(t.CopRefused) as exc:
        t.publish(decision="publish", reason="r", read_digest="x", actor=PUBLISHER)
    assert exc.value.code == "nothing_prepared"
    p = t.prepare(PREPARER, note="cop01 test")
    d = p["content_digest"]
    assert p["actor_kind"] == "person" and p["statements"] == 1
    body = {"decision": "publish", "reason": "Read it (synthetic).", "read_digest": d}
    for who, code in ((PREPARER, "separation"), (LEARNER, "role"), (SERVICE, "not_a_person"), (AGENT, "not_a_person")):
        with pytest.raises(t.CopRefused) as exc:
            t.publish(actor=who, **body)
        assert exc.value.code == code, who
    with pytest.raises(t.CopRefused) as exc:
        t.publish(actor=PUBLISHER, **dict(body, read_digest="0" * 64))
    assert exc.value.code == "digest"
    with pytest.raises(t.CopRefused) as exc:
        t.publish(actor=PUBLISHER, **dict(body, reason=""))
    assert exc.value.code == "reason"
    assert t.published() is None, "nothing leaves before a person publishes it"
    out = t.publish(actor=PUBLISHER, **body)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"] and out["prepared_by"] == PREPARER["id"]
    pub = out["published"]
    assert pub["batch"]["content_digest"] == d and pub["publication"]["content_digest"] == d
    assert pub["publication"]["signed_by"] == PUBLISHER["id"] and pub["publication"]["template"] == t.TEMPLATE
    steps = _rows(cop_db, "SELECT template_id, actor, subject FROM wf_decision_steps WHERE actor = ?",
                  (PUBLISHER["id"],))
    assert (t.TEMPLATE, PUBLISHER["id"], "cop_academy:xapi") in [tuple(r) for r in steps]
    with pytest.raises(t.CopRefused) as exc:
        t.publish(actor=PUBLISHER, **body)
    assert exc.value.code == "already_decided"
    # a refusal never leaves
    t.attempt("eng-red-first", _key("eng-red-first"), OTHER)
    p2 = t.prepare(PREPARER)
    r = t.publish(decision="refuse", reason="Not yet (synthetic).", read_digest=p2["content_digest"], actor=PUBLISHER)
    assert r["event"] == "refused" and "published" not in r
    assert t.published()["batch"]["content_digest"] == d, "the newest PUBLISHED batch is still the one a person published"


def test_an_agent_only_drafts(cop_db):
    t = _t()
    out = t.prepare(AGENT)
    assert out["actor_kind"] == "agent" and out["prepared_by"] == AGENT["id"]
    svc = t.prepare(SERVICE)
    assert svc["actor_kind"] == "agent", "a service key is recorded as an agent"
    with pytest.raises(t.CopRefused) as exc:
        t.prepare(None)
    assert exc.value.code == "no_actor"
    with pytest.raises(t.CopRefused) as exc:
        t.publish(decision="publish", reason="r", read_digest=svc["content_digest"], actor=AGENT)
    assert exc.value.code == "not_a_person"
    led = t.bundle_view()
    assert led["next"] == ["publish"] and SERVICE["id"] in led["builders"]


def test_the_ledger_is_append_only_and_hash_chained(cop_db):
    t = _t()
    t.attempt("eng-red-first", _key("eng-red-first"), LEARNER)
    p = t.prepare(PREPARER)
    t.publish(decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    rows = t.read_events()
    ch = t.chain(rows)
    assert ch["_problems"] == [] and [r["event"] for r in ch["bundle"]] == ["prepared", "published"]
    assert ch["bundle"][1]["prev_hash"] == ch["bundle"][0]["row_hash"]
    for sql in ("UPDATE cop_academy_events SET actor = 'x'", "DELETE FROM cop_academy_events",
                "UPDATE cop_academy_learners SET pseudonym = 'x'", "DELETE FROM cop_academy_learners"):
        conn = sqlite3.connect(str(cop_db))
        try:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        finally:
            conn.close()
    forged = [dict(r) for r in rows]
    prep = next(r for r in forged if r["event"] == "prepared")
    prep["bundle_json"] = prep["bundle_json"].replace('"completion":true', '"completion":false', 1).replace(
        "progressed", "completed", 1)
    prep["row_hash"] = t._row_hash(prep)
    probs = t.chain(forged)["_problems"]
    assert any(x["problem"] == "the bundle does not match its digest" for x in probs)
    assert any(x["problem"] == "not on its hash chain" for x in probs), "the next row no longer links"


# ── the export, and verifying it ─────────────────────────────────────────────


def test_the_export_carries_its_publication_and_verifies(cop_db, tmp_path, capsys):
    from tools.idp import cop_xapi_export as x

    t = _t()
    assert x.main(["--published", "--out", str(tmp_path / "none")]) == 1, "nothing published: nothing leaves"
    capsys.readouterr()
    _complete("cop-ai-data-foundations", LEARNER)
    p = t.prepare(PREPARER)
    t.publish(decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    out = tmp_path / "export"
    assert x.main(["--published", "--out", str(out)]) == 0
    capsys.readouterr()
    batch = json.loads((out / x.BATCH_FILE).read_text())
    publication = json.loads((out / x.PUBLICATION_FILE).read_text())
    assert batch["content_digest"] == p["content_digest"] == publication["content_digest"]
    assert set(batch) == {"schema", "producer", "produced_at", "classification", "paths", "statements",
                          "content_digest"}, "exactly what ICDEV[OS] imports"
    assert x.main(["--verify", str(out), "--ledger"]) == 0
    v = json.loads(capsys.readouterr().out)
    assert v["ok"] and v["ledger"] == {"chain_intact": True, "published_row": True, "newest_published": True}
    assert t.verify_published()["ok"]
    # each way an export can be wrong is named
    self_pub = dict(publication, signed_by=publication["prepared_by"])
    assert "the batch was published by the person who prepared it" in t.verify(batch, self_pub)["errors"]
    assert any("missing" in e for e in t.verify(batch, None)["errors"])
    other = json.loads(json.dumps(batch))
    other["produced_at"] = "2026-01-01T00:00:00Z"
    other["content_digest"] = t.content_digest(other)
    errs = t.verify(other, publication, t.read_events())["errors"]
    assert "the publication was taken at another digest" in errs
    forged = dict(publication, row_hash="f" * 64)
    assert "ledger: no published event with that id and hash" in t.verify(batch, forged, t.read_events())["errors"]
    (out / x.BATCH_FILE).write_text(json.dumps(dict(batch, paths=["cop-engineering-foundations"])))
    assert x.main(["--verify", str(out)]) == 1


# ── the page, the twin, the MCP tools ────────────────────────────────────────


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


def test_the_routes_and_the_page(cop_db):
    learner, prep, pub = _page_app(LEARNER), _page_app(PREPARER), _page_app(PUBLISHER)
    html = learner.get("/idp/cop-tracks").get_data(as_text=True)
    for want in ("Community of Practice learning paths", "cop-engineering-foundations", "cop-cloud-foundations",
                 "decl-wft-cop-academy-xapi-publish", "idp.idp_api_cop_tracks", "idp.idp_api_cop_mission_attempt",
                 "idp.idp_api_cop_xapi_prepare", "idp.idp_api_cop_xapi_publish", "idp.idp_api_cop_xapi_published",
                 "idp.idp_api_standards_twin"):
        assert want in html, want
    tmpl = (ROOT / "tools/dashboard/templates/idp/cop_tracks.html").read_text(encoding="utf-8")
    assert "/api/" not in tmpl, "routes are named by endpoint id"
    v = learner.get("/idp/api/cop-tracks").get_json()
    assert [tr["id"] for tr in v["tracks"]] == ICOS_PATHS and v["learner"] is None
    assert '"answer"' not in json.dumps(v), "no route returns an answer"
    r = learner.post("/idp/api/cop-tracks/missions/cloud-marking/attempt", json={"answers": _key("cloud-marking")})
    assert r.status_code == 201 and r.get_json()["passed"] is True
    assert learner.post("/idp/api/cop-tracks/missions/nope/attempt", json={"answers": {}}).status_code == 404
    svc = _page_app(SERVICE).post("/idp/api/cop-tracks/missions/cloud-marking/attempt", json={"answers": {}})
    assert svc.status_code == 403
    v = learner.get("/idp/api/cop-tracks").get_json()
    assert v["progress"]["cop-cloud-foundations"] == {"passed": ["cloud-marking"], "of": 3, "state": "progressed"}
    assert [s["actor"]["account"]["name"] for s in v["statements"]] == [v["learner"]]
    assert prep.get("/idp/api/cop-tracks/xapi/published").get_json()["code"] == "unpublished"
    d = prep.post("/idp/api/cop-tracks/xapi/prepare", json={"note": "t"}).get_json()["content_digest"]
    body = {"decision": "publish", "reason": "Read it (synthetic).", "digest": d}
    refused = prep.post("/idp/api/cop-tracks/xapi/publish", json=body)
    assert refused.status_code == 403 and refused.get_json()["code"] == "separation"
    out = pub.post("/idp/api/cop-tracks/xapi/publish", json=body).get_json()
    assert out["event"] == "published" and out["published"]["publication"]["signed_by"] == PUBLISHER["id"]
    got = pub.get("/idp/api/cop-tracks/xapi/published").get_json()
    assert got["batch"]["content_digest"] == d and got["verify"]["ok"] is True
    # reachable: in the nav (registry), from the portal and from the Academy
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    idp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "idp")
    assert "/idp/cop-tracks" in [lk["href"] for lk in idp["nav"]["links"]]
    for page in ("tools/dashboard/templates/idp/page.html", "tools/dashboard/templates/forge_academy/index.html"):
        assert "/idp/cop-tracks" in (ROOT / page).read_text(encoding="utf-8") or \
            "url_for('idp.idp_cop_tracks_page')" in (ROOT / page).read_text(encoding="utf-8"), page


def test_the_twin_observes_with_threads_and_replays(cop_db):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    t = _t()
    adapter = governance.adapter_for("idp")
    assert "cop.academy" in adapter.observes
    env = governance.observe(adapter, record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["cop.academy.ledger"]["status"] == "verified"
    assert chk["cop.academy.published"]["status"] == "unverified", "nothing published is never a pass"
    assert env["verdict"] != "pass"
    _complete("cop-cloud-foundations", LEARNER)
    env = governance.observe(adapter, record=False)
    un = [v for v in env["violations"] if v["rule_id"] == "cop.academy.unreported"]
    assert len(un) == 1 and un[0]["title"].startswith("1 statement(s)")
    th = governance.resolve_thread(un[0]["thread_id"])
    assert th["resolved"] and th["source"] == "cop_academy_events" and th["record"]["event"] == "completed"
    assert th["record"]["path_id"] == "cop-cloud-foundations"
    p = t.prepare(PREPARER)
    t.publish(decision="publish", reason="ok (synthetic)", read_digest=p["content_digest"], actor=PUBLISHER)
    env = governance.observe(adapter)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["cop.academy.published"]["status"] == "verified"
    assert not [v for v in env["violations"] if v["rule_id"] == "cop.academy.unreported"]
    assert env["extra"]["summary"]["cop_academy"]["statements_published"] == 1
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")
    # the judge is pure: a self-published, stale, off-contract batch is judged from evidence alone
    ev = {"config": {"paths": ICOS_PATHS}, "problems": [], "unreported": {"statements": 0},
          "bundle": {"prepared": None, "published": {"event_id": "copa-x", "actor": "u-a", "prepared_by": "u-a",
                                                     "created_at": "2026-01-01T00:00:00Z", "content_digest": "d",
                                                     "produced_at": "2026-01-01T00:00:00Z", "statements": 1,
                                                     "off_contract": True}}}
    _, findings, _ = t.twin_judge(ev, {}, "2026-11-18T00:00:00Z")
    assert {f["rule_id"] for f in findings} == {"cop.academy.self_published", "cop.academy.stale",
                                                "cop.academy.off_contract"}
    assert all(f["thread_id"] == "cop_academy:copa-x" for f in findings)


def test_the_twin_says_unverified_when_the_ledger_cannot_be_read(monkeypatch):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    t = _t()

    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(t, "read_events", boom)
    env = governance.observe(governance.adapter_for("idp"), record=False)
    chk = {x["id"]: x for x in env["extra"]["checks"]}
    assert chk["cop.academy.ledger"]["status"] == "unverified" and env["verdict"] != "pass"


def test_the_mcp_tools_read_and_only_prepare(cop_db):
    from tools.conformance.feature_audit import approval_policy
    from tools.mcp.gap_handlers import handle_cop_academy, handle_cop_academy_xapi_prepare
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert {"cop_academy", "cop_academy_xapi_prepare"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["cop_academy"] is True and tool_authorization("cop_academy")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["cop_academy_xapi_prepare"] is False
    policy = approval_policy(ROOT, "args/agent_approval_policy.yaml")
    assert policy["tiers"].get("cop_academy_xapi_prepare", policy["default"]) in policy["halts"]
    read = handle_cop_academy({})
    assert [tr["id"] for tr in read["tracks"]] == ICOS_PATHS and '"answer"' not in json.dumps(read)
    assert handle_cop_academy({"published": True})["code"] == "unpublished"
    out = handle_cop_academy_xapi_prepare({"agent_id": "cop01-drafter"})
    assert out["draft"]["prepared_by"] == "agent:cop01-drafter" and out["draft"]["actor_kind"] == "agent"
    assert "publishes it" in out["next"]
    assert handle_cop_academy({"published": True})["code"] == "unpublished", "a draft never leaves"
