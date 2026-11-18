# CUI // SP-CTI
"""ADRs as signed, versioned records (icx-coe-02).

  * the flat log is migrated losslessly: the records render the flat doc as it stood, byte for byte;
  * the flat doc is generated from the records and a drift fails the check (CI runs it);
  * every record validates and links only to real platform-model requirements and standards;
  * propose -> accept -> supersede through declared steps, the author never the signer;
  * the records are seeded into an append-only, hash-chained ledger by a bootstrap identity;
  * a ledger decision reaches the doc through --export and --write;
  * the page, the routes, the twin (unverified, never passed) and the MCP read and draft.

People here are labelled test persons (synthetic). The ledger tests run on the conftest SQLite platform DB.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/adrs_flat_20261117.md"
ARCHITECT = {"id": "u-coe02-architect", "role": "reviewer"}
APPROVER = {"id": "u-coe02-approver", "role": "reviewer"}
OTHER = {"id": "u-coe02-other", "role": "isso"}

DRAFT = {"id": "D-COE02-TEST-1", "title": "A test decision (synthetic)", "version": "1",
         "section": "Phase 83 — COE (Engineering Centre of Excellence: the standards register)",
         "context": "Synthetic: the coe02 test suite only.", "decision": "Do the synthetic thing.",
         "consequences": "Nothing outside this test.",
         "links": {"requirements": ["PM-COE-08"], "standards": ["ENG-E2E-SPEC"]}}


@pytest.fixture()
def coe_db(wf_db):
    return wf_db


def _a():
    from tools.idp import adrs

    return adrs


def _copy_root(tmp_path: Path) -> Path:
    """A throwaway repo root with the ADR records, the doc, the config and the platform model."""
    root = tmp_path / "repo"
    for rel in ("docs/reference/adrs", "args/platform_model"):
        shutil.copytree(ROOT / rel, root / rel)
    for rel in ("docs/reference/adrs.md", "args/coe_records.yaml"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
    return root


# ── the records and the generated doc ────────────────────────────────────────


def test_the_migration_is_lossless():
    a = _a()
    text = FIXTURE.read_text(encoding="utf-8")
    layout, parsed = a.parse_flat(text)
    assert len(parsed) == 676, "every ADR of the flat log"
    ids = [p["id"] for p in parsed]
    assert len(ids) == len(set(ids))
    # 1. the parser round-trips the flat log
    as_records = {p["id"]: {"id": p["id"], "format": "legacy", "status": "accepted", "decision": p["decision"]}
                  for p in parsed}
    assert a.render(as_records, layout, banner=False) == text
    # 2. the COMMITTED records render the flat log as it stood, byte for byte
    committed = a.load_records()
    legacy = {k: committed[k] for k in ids}
    for p in parsed:
        rec = legacy[p["id"]]
        assert rec["format"] == "legacy" and rec["version"] == "1" and rec["decision"] == p["decision"], p["id"]
        assert rec["section"] == p["section"] and rec["title"] == a.derive_title(p["decision"])
        assert rec["recorded"]["kind"] == "bootstrap" and rec["recorded"]["by"].startswith("bootstrap:")
    assert a.render(legacy, layout, banner=False) == text
    # 3. the committed layout is the flat log's, plus a line (and its blank) per ADR added since
    later = set(committed) - set(ids)
    repo_layout = a.load_layout()
    kept: list[str] = []
    for line in repo_layout:
        m = a._PLACEHOLDER.match(line)
        if m and m.group(1) in later:
            if kept and kept[-1] == "":
                kept.pop()
            continue
        kept.append(line)
    assert kept == layout
    # the one reviewed normalisation: the generated doc opens with a banner line
    doc = (ROOT / "docs/reference/adrs.md").read_text(encoding="utf-8")
    assert doc.split("\n", 1)[0] == a.BANNER and doc.split("\n", 1)[1].startswith(text.split("\n\n", 1)[0])


def test_the_flat_doc_is_generated_and_drift_fails_the_check(tmp_path):
    a = _a()
    assert a.check_doc() == {"ok": True, "doc": "docs/reference/adrs.md",
                             "sha256": a.check_doc()["sha256"]}
    assert a.generate() == (ROOT / "docs/reference/adrs.md").read_text(encoding="utf-8")
    root = _copy_root(tmp_path)
    doc = root / "docs/reference/adrs.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n- **D999:** hand-appended\n", encoding="utf-8")
    out = a.check_doc(root)
    assert out["ok"] is False and "drifted" in out["error"] and "--write" in out["error"]
    a.write_doc(root)
    assert a.check_doc(root)["ok"] is True
    rec = root / "docs/reference/adrs/D1.md"
    rec.write_text(rec.read_text(encoding="utf-8").replace("zero-config", "zero config"), encoding="utf-8")
    assert a.check_doc(root)["ok"] is False, "a record changed and the doc not regenerated is drift"
    ci = (ROOT / ".github/workflows/icdev-ci.yml").read_text(encoding="utf-8")
    assert "python tools/idp/adrs.py --check" in ci, "CI runs the drift check"
    assert a.main(["--check"]) == 0


def test_every_record_validates_and_links_to_real_requirements_and_standards():
    a = _a()
    assert a.validate_records() == []
    recs = a.load_records()
    assert len(recs) >= 677 and recs["D-COE-2"]["format"] == "structured"
    assert set(recs["D-COE-2"]["links"]["requirements"]) <= a.requirement_ids()
    assert a.validate_entry({**DRAFT, "links": {"requirements": ["PM-NOPE-1"], "standards": []}}) != []
    assert any("standards register" in e for e in
               a.validate_entry({**DRAFT, "links": {"requirements": [], "standards": ["ENG-NOPE"]}}))
    assert any("context" in e for e in a.validate_entry({**DRAFT, "context": ""}))
    assert any("supersedes" in e for e in a.validate_entry({**DRAFT, "supersedes": ["D-NOPE-9"]},
                                                           known_adrs=recs))


# ── the lifecycle ────────────────────────────────────────────────────────────


def _accept(aid, version="1", actor=APPROVER):
    a = _a()
    v = a.adr_view(aid)
    return a.accept(aid, version, decision="accept", reason="Agreed (synthetic).", read_digest=v["entry_digest"],
                    actor=actor)


def test_propose_accept_supersede_with_history(coe_db):
    a = _a()
    out = a.propose(DRAFT, ARCHITECT, note="please review (synthetic)")
    assert out["state"] == "proposed" and out["author_kind"] == "person"
    assert a.adr_view(DRAFT["id"])["next"] == ["accept"]
    acc = _accept(DRAFT["id"])
    assert acc["state"] == "accepted" and acc["decision_step"]["template_id"] == "decl-wft-coe-adr-accept"
    succ = {**DRAFT, "id": "D-COE02-TEST-2", "title": "Its successor (synthetic)", "supersedes": [DRAFT["id"]]}
    a.propose(succ, ARCHITECT)
    _accept(succ["id"])
    v = a.adr_view(DRAFT["id"])
    assert v["next"] == ["supersede"]
    out = a.supersede(DRAFT["id"], "1", decision="supersede", superseded_by=succ["id"],
                      reason="Replaced (synthetic).", read_digest=v["entry_digest"], actor=OTHER)
    assert out["state"] == "superseded"
    v = a.adr_view(DRAFT["id"])
    assert [h["event"] for h in v["history"]] == ["proposed", "accepted", "superseded"]
    assert v["status"] == "superseded" and v["superseded_by"] == succ["id"] and v["next"] == []
    assert v["chain"]["intact"]
    # a new version of an accepted ADR supersedes the version before it
    a.propose({**succ, "version": "2", "decision": "Do it better (synthetic)."}, ARCHITECT)
    _accept(succ["id"], "2")
    v2 = a.adr_view(succ["id"])
    assert v2["in_force"] == "2" and [x["state"] for x in v2["versions"]] == ["superseded", "accepted"]
    with pytest.raises(a.AdrRefused) as exc:
        a.propose({**succ, "version": "2"}, ARCHITECT)
    assert exc.value.code == "version_exists"


def test_the_author_is_never_the_signer(coe_db):
    a = _a()
    a.propose(DRAFT, ARCHITECT)
    v = a.adr_view(DRAFT["id"])
    with pytest.raises(a.AdrRefused) as exc:
        a.accept(DRAFT["id"], "1", decision="accept", reason="mine", read_digest=v["entry_digest"], actor=ARCHITECT)
    assert exc.value.code == "separation"
    for bad, code in (({"read_digest": "0" * 64}, "digest"), ({"reason": ""}, "reason"),
                      ({"actor": {"id": "agent:x", "role": "service"}}, "not_a_person"),
                      ({"actor": {"id": "u-dev", "role": "developer"}}, "role")):
        kw = {"decision": "accept", "reason": "ok", "read_digest": v["entry_digest"], "actor": APPROVER, **bad}
        with pytest.raises(a.AdrRefused) as exc:
            a.accept(DRAFT["id"], "1", **kw)
        assert exc.value.code == code, bad
    with pytest.raises(a.AdrRefused) as exc:
        a.supersede(DRAFT["id"], "1", decision="deprecate", reason="x", read_digest=v["entry_digest"], actor=OTHER)
    assert exc.value.code == "out_of_order", "nothing advances out of order"
    out = a.accept(DRAFT["id"], "1", decision="return", reason="Needs work (synthetic).",
                   read_digest=v["entry_digest"], actor=APPROVER)
    assert out["state"] == "returned"


def test_supersede_needs_an_accepted_successor_and_neither_author(coe_db):
    a = _a()
    a.propose(DRAFT, ARCHITECT)
    _accept(DRAFT["id"])
    v = a.adr_view(DRAFT["id"])
    step = {"reason": "x (synthetic)", "read_digest": v["entry_digest"]}
    with pytest.raises(a.AdrRefused) as exc:
        a.supersede(DRAFT["id"], "1", decision="supersede", superseded_by="", actor=OTHER, **step)
    assert exc.value.code == "successor"
    succ = {**DRAFT, "id": "D-COE02-TEST-3", "title": "Proposed only (synthetic)"}
    a.propose(succ, OTHER)
    with pytest.raises(a.AdrRefused) as exc:
        a.supersede(DRAFT["id"], "1", decision="supersede", superseded_by=succ["id"], actor=APPROVER, **step)
    assert exc.value.code == "successor", "a successor is accepted first"
    _accept(succ["id"], actor=APPROVER)
    for who in (ARCHITECT, OTHER):          # the ADR's author, and the successor's author
        with pytest.raises(a.AdrRefused) as exc:
            a.supersede(DRAFT["id"], "1", decision="supersede", superseded_by=succ["id"], actor=who, **step)
        assert exc.value.code == "separation", who
    assert a.supersede(DRAFT["id"], "1", decision="supersede", superseded_by=succ["id"], actor=APPROVER,
                       **step)["state"] == "superseded"


def test_records_are_seeded_by_the_bootstrap_identity_never_a_person(coe_db):
    a = _a()
    with pytest.raises(a.AdrRefused) as exc:
        a.accept("D1", "1", decision="accept", reason="x", read_digest="", actor=APPROVER)
    assert exc.value.code == "unknown_adr" and "not yet seeded" in exc.value.message
    out = a.seed()
    n = len(a.load_records())
    assert out == {"seeded": n, "skipped": 0, "seed_identity": a.load_config()["seed_identity"]}
    assert a.seed()["seeded"] == 0, "idempotent"
    d1 = a.adr_view("D1")
    assert d1["status"] == "accepted" and d1["history"][0]["actor_kind"] == "bootstrap"
    assert d1["history"][0]["step_id"] == "" and d1["next"] == ["supersede"]
    assert a.adr_view("D-COE-2")["status"] == "proposed" and a.adr_view("D-COE-2")["next"] == ["accept"]
    rows = sqlite3.connect(str(coe_db)).execute(
        "SELECT COUNT(*) FROM coe_adr_events WHERE actor_kind != 'bootstrap'").fetchone()
    assert rows[0] == 0, "no seed is a person's signature"
    with pytest.raises(a.AdrRefused) as exc:
        a.propose({**DRAFT, "id": "D1"}, ARCHITECT)
    assert exc.value.code == "version_exists", "a migrated ADR is amended by a new version"
    a.propose({**DRAFT, "id": "D1", "version": "2"}, ARCHITECT)
    _accept("D1", "2")
    d1 = a.adr_view("D1")
    assert d1["in_force"] == "2" and [v["state"] for v in d1["versions"]] == ["superseded", "accepted"]


def test_export_writes_the_record_and_the_doc_follows(coe_db, tmp_path):
    a = _a()
    root = _copy_root(tmp_path)
    a.propose(DRAFT, ARCHITECT, root=root)
    _accept(DRAFT["id"])
    out = a.export(DRAFT["id"], root=root)
    assert out["status"] == "accepted"
    rec = a.read_record(root / "docs/reference/adrs" / f"{DRAFT['id']}.md")
    assert rec["status"] == "accepted" and rec["recorded"]["kind"] == "person"
    assert rec["recorded"]["by"] == APPROVER["id"] and rec["recorded"]["step_id"].startswith("step-")
    assert "{{adr " + DRAFT["id"] + "}}" in a.load_layout(root)
    assert a.check_doc(root)["ok"] is False, "the doc follows only when it is regenerated"
    a.write_doc(root)
    assert a.check_doc(root)["ok"] is True and a.validate_records(root) == []
    doc = (root / "docs/reference/adrs.md").read_text(encoding="utf-8")
    assert f"- **{DRAFT['id']}:** **A test decision (synthetic).** Do the synthetic thing." in doc
    assert "*Status:* accepted, version 1" in doc and "requirements PM-COE-08" in doc


def test_the_ledger_is_append_only_and_hash_chained(coe_db):
    a = _a()
    a.propose(DRAFT, ARCHITECT)
    _accept(DRAFT["id"])
    rows = a.read_events(adr_id=DRAFT["id"])
    ch = a.chain(rows)
    assert ch["_problems"] == [] and [r["event"] for r in ch[DRAFT["id"]]] == ["proposed", "accepted"]
    for sql in ("UPDATE coe_adr_events SET reason = 'x'", "DELETE FROM coe_adr_events"):
        conn = sqlite3.connect(str(coe_db))
        try:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        finally:
            conn.close()
    forged = dict(rows[1], reason="forged")
    assert a.chain([rows[0], forged])["_problems"][0]["problem"] == "row hash does not match its content"
    conn = a._connection()
    try:
        with pytest.raises(a.AdrRefused) as exc:
            a._append(conn, adr_id=DRAFT["id"], version="9", event="proposed", actor="x", actor_kind="person",
                      entry=DRAFT, prev_hash=a.GENESIS)
        assert exc.value.code == "concurrent"
    finally:
        conn.close()


# ── the twin ─────────────────────────────────────────────────────────────────


def test_the_twin_says_unverified_for_records_not_in_the_ledger(coe_db):
    import tools.twin_core.adapters.coe_standards  # noqa: F401
    from tools.twin_core import governance

    a = _a()
    adapter = governance.adapter_for("idp")
    env = governance.observe(adapter, record=False)
    chk = {c["id"]: c for c in env["extra"]["checks"]}
    assert chk["coe.adrs.recorded"]["status"] == "unverified", "a record the ledger never saw is never passed"
    assert env["extra"]["summary"]["adr_doc_in_sync"] is True
    a.seed()
    a.propose(DRAFT, ARCHITECT)
    _accept(DRAFT["id"])
    env = governance.observe(adapter)
    chk = {c["id"]: c for c in env["extra"]["checks"]}
    assert chk["coe.adrs.recorded"]["status"] == "verified"
    une = [x for x in env["violations"] if x["rule_id"] == "coe.adr.unexported"]
    assert une and DRAFT["id"] in une[0]["title"]
    t = governance.resolve_thread(une[0]["thread_id"])
    assert t["resolved"] and t["source"] == "coe_adr_events" and t["record"]["event"] == "accepted"
    assert env["extra"]["summary"]["adrs_accepted_by_a_person"] == 1
    rep = governance.replay(adapter, env["extra"]["observation_id"])
    assert rep["match"] is True, rep.get("reason")


# ── the console, the routes, MCP ─────────────────────────────────────────────


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


def _page_app(user):
    """The dashboard's rendering context on a fresh app (as tests/test_icx_coe_01_standards.py)."""
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


def test_the_adr_routes_and_page(coe_db):
    from tools.mcp.gap_handlers import handle_coe_adr_propose, handle_coe_adrs
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    arch, appr = _app(ARCHITECT), _app(APPROVER)
    body = {**{k: v for k, v in DRAFT.items() if k != "links"}, "requirements": "PM-COE-08",
            "standards": "ENG-E2E-SPEC"}
    resp = arch.post("/idp/api/adrs/propose", json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    d = appr.get(f"/idp/api/adrs/{DRAFT['id']}").get_json()
    assert d["status"] == "proposed" and d["entry"]["links"]["standards"] == ["ENG-E2E-SPEC"]
    step = {"version": "1", "reason": "ok (synthetic)", "digest": d["entry_digest"], "decision": "accept"}
    r = arch.post(f"/idp/api/adrs/{DRAFT['id']}/accept", json=step)
    assert r.status_code == 403 and r.get_json()["code"] == "separation"
    assert appr.post(f"/idp/api/adrs/{DRAFT['id']}/accept", json=step).get_json()["state"] == "accepted"
    lst = appr.get("/idp/api/adrs?q=COE02").get_json()
    assert [x["id"] for x in lst["adrs"]] == [DRAFT["id"]] and lst["doc"]["ok"] is True
    r = appr.post(f"/idp/api/adrs/{DRAFT['id']}/supersede",
                  json={**step, "decision": "supersede", "superseded_by": "D-NOPE-1"})
    assert r.status_code == 409 and r.get_json()["code"] == "successor"
    assert appr.post(f"/idp/api/adrs/{DRAFT['id']}/supersede",
                     json={**step, "decision": "deprecate"}).get_json()["state"] == "deprecated"
    assert appr.get("/idp/api/adrs/D-NOPE-9").status_code == 404
    html = _page_app(APPROVER).get("/idp/adrs").get_data(as_text=True)
    for want in ("Architecture decision records", "decl-wft-coe-adr-accept", "decl-wft-coe-adr-supersede",
                 "idp.idp_api_adr_accept", "idp.idp_api_adr_supersede", "idp.idp_api_adr_propose",
                 "idp.idp_api_adrs", "D-COE-2"):
        assert want in html, want
    # AI-native: read, and draft only
    assert {"coe_adrs", "coe_adr_propose"} <= set(list_tools())
    assert READ_ONLY_DECLARATIONS["coe_adrs"] is True and tool_authorization("coe_adrs")["tier"] == "read"
    assert READ_ONLY_DECLARATIONS["coe_adr_propose"] is False
    out = handle_coe_adr_propose({**body, "id": "D-COE02-MCP-1", "agent_id": "coe02-drafter"})
    assert out["draft"]["state"] == "proposed" and out["draft"]["author_kind"] == "agent"
    one = handle_coe_adrs({"adr_id": "D-COE02-MCP-1"})["adr"]
    assert one["history"][0]["actor"] == "agent:coe02-drafter" and one["next"] == ["accept"]
    assert len(handle_coe_adrs({"limit": 5})["register"]["adrs"]) == 5
    a = _a()
    with pytest.raises(a.AdrRefused) as exc:
        a.accept("D-COE02-MCP-1", "1", decision="accept", reason="x", read_digest=one["entry_digest"],
                 actor={"id": "agent:coe02-drafter", "role": "service"})
    assert exc.value.code == "not_a_person"
