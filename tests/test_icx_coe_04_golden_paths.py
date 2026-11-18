# CUI // SP-CTI
"""Golden paths: a new service that is conformant by construction (icx-coe-04).

What the card promises, each clause a test here:

  * `icdev scaffold service` renders a service on a golden path, with every piece a service needs (contract,
    SBOM and an AI-BOM where it uses a model, control map, OTel, a PROPOSED ADR record, the five-rules wiring,
    tests, a scorecard declaration) and splices its registrations as text;
  * each path declares the standards it satisfies by id and version; the standards that apply are DERIVED from
    the register (in force, and the path's repo declaration), and a path that does not declare one is behind:
    CI fails ("the golden path is behind the standards in force");
  * the drift check scaffolds into a temporary tree and runs the IDP scorecard and feature_audit on the result:
    a machine-checkable standard passes, a manual one needs evidence, anything else is drift;
  * a version is proposed (by anyone, or an agent) and published or retired only by a person who did not author
    it, and published only when its gate holds; the ledger is append-only and hash-chained, seeds labelled;
  * the page and routes, the twin (threads that resolve, unverified when unread) and the MCP read and draft.

People here are labelled test persons (synthetic). All on the conftest SQLite platform DB.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from flask import Flask, g

ROOT = Path(__file__).resolve().parents[1]
AUTHOR = {"id": "u-coe04-author", "role": "developer"}
PUBLISHER = {"id": "u-coe04-publisher", "role": "reviewer"}
STEWARD = {"id": "u-coe04-steward", "role": "reviewer"}
AGENT = {"id": "agent:coe04-drafter", "role": "service"}
SEEDED = ["ENG-COHERENCE", "ENG-E2E-SPEC", "ENG-FIVE-RULES", "ENG-GUARD-HOOKS", "ENG-PAGE-COMPLETENESS",
          "ENG-RED-FIRST", "ENG-SEC-GATES"]

NEW_STD = {
    "id": "ENG-COE04-PLANTED", "title": "A planted standard every service answers to (synthetic)", "version": "1.0",
    "owner": "developer", "scope": "Every service (synthetic, icx-coe-04 tests).",
    "sources": [{"path": "args/golden_paths.yaml"}],
    "check": {"manual_evidence": {"name": "A person reads the planted evidence",
                                  "procedure": "Read the synthetic evidence and confirm it holds."}},
}


@pytest.fixture()
def gp():
    from tools.idp import golden_paths

    return golden_paths


@pytest.fixture()
def seeded(wf_db):
    from tools.idp import golden_paths, standards

    standards.seed()
    golden_paths.seed()
    return wf_db


@pytest.fixture(scope="module")
def drift_report():
    """ONE real drift check of the `service` path against the register (the slow part, run once)."""
    from tools.idp import golden_paths

    return golden_paths.drift(golden_paths.path_entry("service"), source="register")


def _adopt(std: dict) -> None:
    from tools.idp import standards

    standards.propose(std, AUTHOR)
    d = standards.standard_view(std["id"])["entry_digest"]
    standards.review(std["id"], std["version"], decision="endorse", reason="synthetic", read_digest=d, actor=STEWARD)
    standards.adopt(std["id"], std["version"], decision="sign", reason="synthetic", read_digest=d, actor=STEWARD)


def _pass_drift(monkeypatch, gp, result="pass"):
    """The drift function, replaced for ledger tests: the real one is exercised by ``drift_report``."""
    def fake(entry, *, forced=None, source="register", cfg=None, keep=None):
        b = gp.behind(entry, forced or [])
        return {"result": result if not b["behind"] else "drift", "why": [] if result == "pass" else ["planted"],
                "summary": {"standards": {}}, "template_digest": gp.template_digest(entry), "standards": [],
                "feature_audit": {}, "tests": {"red_first": True}, "pieces": [], "behind": b, "seconds": 0.0,
                "key": "golden_drift"}
    monkeypatch.setattr(gp, "drift", fake)


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ── the declaration, derived from the register ───────────────────────────────


def test_the_paths_declare_every_standard_in_force_derived_from_the_register(gp):
    out = gp.check("register")
    assert out["ok"], out
    assert [s["id"] for s in out["in_force"]] == SEEDED, "in force = the register's seeds that enter adopted"
    for p in out["paths"]:
        assert [r["id"] for r in p["required"]] == SEEDED and not p["missing"] and not p["not_in_force"]
    assert {p["id"] for p in out["paths"]} == {"service", "model-service"}
    cfg = gp.load_config()
    for e in cfg["paths"]:
        assert gp.validate_entry(e) == [], e["id"]


def test_adopting_a_standard_that_applies_puts_the_path_behind_and_ci_fails(gp, seeded, tmp_path, capsys):
    assert gp.check("ledger")["ok"]
    _adopt(NEW_STD)
    out = gp.check("ledger")
    assert not out["ok"]
    svc = next(p for p in out["paths"] if p["id"] == "service")
    assert svc["missing"] == [{"id": "ENG-COE04-PLANTED", "version": "1.0", "title": NEW_STD["title"]}]
    assert svc["why"].startswith("the golden path is behind the standards in force: service 1.0")
    # the CLI CI runs fails, and says why, when the register itself carries a new adopted standard
    reg = yaml.safe_load((ROOT / "args/engineering_standards.yaml").read_text(encoding="utf-8"))
    reg["standards"].append({**NEW_STD, "seed": "adopted"})
    alt = tmp_path / "engineering_standards.yaml"
    alt.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    from tools.idp import standards

    real = standards.load_register
    try:
        standards.load_register = lambda path=None: real(alt)
        assert gp.main(["--check"]) == 1
    finally:
        standards.load_register = real
    assert "the golden path is behind the standards in force" in capsys.readouterr().out


def test_a_standard_that_does_not_apply_or_a_version_change_is_judged_exactly(gp):
    entry = gp.path_entry("service")
    forced = gp.in_force("register")
    assert not gp.behind(entry, forced + [{"id": "ICOS-CONTROLS", "version": "1.0", "title": "x", "check": {}}])["behind"], \
        "a standard the path's repo does not answer to never puts it behind"
    bumped = [dict(s, version="2.0") if s["id"] == "ENG-RED-FIRST" else s for s in forced]
    b = gp.behind(entry, bumped)
    assert b["behind"] and b["stale_version"] == [{"id": "ENG-RED-FIRST", "version": "2.0", "declared": "1.0"}]
    dropped = [s for s in forced if s["id"] != "ENG-GUARD-HOOKS"]
    assert gp.behind(entry, dropped)["not_in_force"] == ["ENG-GUARD-HOOKS"]


# ── the scaffold ─────────────────────────────────────────────────────────────


def test_scaffold_service_renders_every_piece_and_registers_it_as_text(gp, tmp_path):
    from tools.cli import scaffold
    from tools.idp import adrs

    for rel in gp.SHARED.values():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((ROOT / rel).read_bytes())
    res = scaffold.scaffold_service("coe04_probe", "Coe04 Probe", tmp_path, path_id="model-service")
    assert res["success"], res
    files = set(res["rendered_files"]) | set(res["artifacts"]["files"])
    for want in ("tools/coe04_probe/contract.openapi.yaml", "tools/coe04_probe/control_map.yaml",
                 "tools/coe04_probe/telemetry.py", "tools/coe04_probe/service.yaml",
                 "tools/coe04_probe/bom/sbom.cdx.json", "tools/coe04_probe/bom/ai-bom.json",
                 "docs/reference/adrs/D-COE04-PROBE-1.md", "tools/dashboard/templates/coe04_probe/page.html",
                 "icdev/tools/dashboard/templates/coe04_probe/page.html", "tools/twin_core/adapters/coe04_probe.py",
                 "args/workflow_hitl_templates/coe04-probe-decide.yaml", "args/platform_model/coe04_probe.yaml",
                 "tools/iqe/adapters/coe04_probe.py", "tests/test_coe04_probe.py", "tests/e2e/coe04_probe.spec.ts"):
        assert want in files, want
    rec = adrs.read_record(tmp_path / "docs/reference/adrs/D-COE04-PROBE-1.md")
    assert rec["status"] == "proposed" and rec["recorded"]["kind"] == "agent", "the generator never accepts an ADR"
    sbom = json.loads((tmp_path / "tools/coe04_probe/bom/sbom.cdx.json").read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX" and {c["name"] for c in sbom["components"]} == {"flask", "pyyaml"}
    decl = yaml.safe_load((tmp_path / "tools/coe04_probe/service.yaml").read_text(encoding="utf-8"))
    assert decl["golden_path"] == {"id": "model-service", "version": "1.0"} and len(decl["standards"]) == 7
    # registered as text in the shared registers, every reviewed comment kept
    reg = (tmp_path / "args/component_registry.yaml").read_text(encoding="utf-8")
    assert reg.startswith((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8")[:2000])
    comp = next(c for c in yaml.safe_load(reg)["components"] if c.get("key") == "coe04_probe")
    assert comp["kind"] == "canvas" and comp["owner"] == "developer" and comp["nav"]["section"]
    feats = yaml.safe_load((tmp_path / "args/feature_conformance.yaml").read_text(encoding="utf-8"))["features"]
    assert feats[-1]["key"] == "coe04_probe.service" and feats[-1]["mcp_tools"] == ["coe04_probe_items", "coe04_probe_propose"]
    reg_py = (tmp_path / "tools/mcp/tool_registry.py").read_text(encoding="utf-8")
    compile(reg_py, "tool_registry.py", "exec")
    assert '"coe04_probe_items": True,' in reg_py and '"coe04_probe_propose": False,' in reg_py
    # nothing is ever overwritten, and a second registration is a no-op
    again = scaffold.scaffold_service("coe04_probe", "Coe04 Probe", tmp_path, path_id="model-service")
    assert not again["success"] and "never overwritten" in again["errors"][0]
    assert all(not r["spliced"] for r in gp.register(tmp_path, tmp_path / gp.SNIPPETS_DIR))
    plain = scaffold.scaffold_service("coe04_plain", "Coe04 Plain", tmp_path, path_id="service", register=False)
    assert not (tmp_path / "tools/coe04_plain/bom/ai-bom.json").exists() and plain["success"]


def test_the_drift_check_passes_the_scaffold_machine_pass_manual_needs_evidence(drift_report):
    r = drift_report
    assert r["result"] == "pass", r.get("why")
    status = {s["id"]: s["status"] for s in r["standards"]}
    assert status == {"ENG-COHERENCE": "needs_evidence", "ENG-E2E-SPEC": "pass", "ENG-FIVE-RULES": "needs_evidence",
                      "ENG-GUARD-HOOKS": "needs_evidence", "ENG-PAGE-COMPLETENESS": "pass",
                      "ENG-RED-FIRST": "needs_evidence", "ENG-SEC-GATES": "needs_evidence"}
    assert {c: v["status"] for c, v in r["feature_audit"]["cells"].items()} == dict.fromkeys(
        ("gui", "workflow", "twin", "architecture", "ai_native"), "pass")
    assert r["feature_audit"]["config_errors"] == [] and r["feature_audit"]["hand_written_routes"] == []
    t = r["tests"]
    assert t["red_first"] and t["green"]["rc"] == 0 and t["red"]["rc"] != 0
    assert all(p["ok"] for p in r["pieces"]) and {p["piece"] for p in r["pieces"]} == {
        "contract", "sbom", "ai_bom", "control_map", "otel", "adr", "scorecard_declaration"}
    assert r["seconds"] < 120, "the drift check stays fast enough for CI"


def test_a_machine_standard_the_scaffold_does_not_meet_is_drift_never_pass(gp):
    forced = gp.in_force("register") + [{
        "id": "ENG-COE04-PROBED", "version": "1.0", "title": "probed (synthetic)",
        "check": {"scorecard_rule": {"scorecard": "component-readiness", "rule": "probes-healthy"}}}]
    entry = gp.normalise({**gp.path_entry("service"), "satisfies": gp.path_entry("service")["satisfies"]
                          + [{"id": "ENG-COE04-PROBED", "version": "1.0", "by": "synthetic"}]})
    r = gp.drift(entry, forced=forced)
    row = next(s for s in r["standards"] if s["id"] == "ENG-COE04-PROBED")
    assert row["status"] == "unverified" and "nothing was measured" in row["why"], \
        "a rule that does not apply to the scaffold measured nothing: unverified, never pass"
    assert r["result"] == "drift" and any("ENG-COE04-PROBED" in w for w in r["why"])


# ── the ledger: propose, drift check, a person publishes or retires ──────────


def test_propose_drift_and_publish_by_another_person_when_the_gate_holds(gp, seeded, monkeypatch):
    _adopt(NEW_STD)
    v = gp.path_view("service")
    assert v["behind"]["behind"] and v["in_use"] == "1.0" and v["published_kind"] == "bootstrap"
    entry = gp.entry_from_form({"id": "service", "version": "1.1",
                                "satisfies": [f"{s}@1.0" for s in SEEDED + ["ENG-COE04-PLANTED"]]},
                               v["versions"][-1]["entry"])
    p = gp.propose(entry, AUTHOR, note="declares the planted standard (synthetic)")
    assert p["state"] == "proposed" and p["author_kind"] == "person"
    with pytest.raises(gp.GoldenPathRefused) as no_drift:
        gp.publish("service", "1.1", decision="publish", reason="r", read_digest=p["entry_digest"], actor=PUBLISHER)
    assert no_drift.value.code == "gate" and "no drift check" in no_drift.value.message
    _pass_drift(monkeypatch, gp)
    d = gp.record_drift("service", "1.1", AUTHOR)
    assert d["result"] == "pass" and d["version"] == "1.1"
    with pytest.raises(gp.GoldenPathRefused) as own:
        gp.publish("service", "1.1", decision="publish", reason="mine", read_digest=p["entry_digest"],
                   actor={**AUTHOR, "role": "reviewer"})
    assert own.value.code == "separation", "the author never publishes their own version"
    with pytest.raises(gp.GoldenPathRefused) as stale:
        gp.publish("service", "1.1", decision="publish", reason="r", read_digest="0" * 64, actor=PUBLISHER)
    assert stale.value.code == "digest"
    out = gp.publish("service", "1.1", decision="publish", reason="Read it (synthetic).",
                     read_digest=p["entry_digest"], actor=PUBLISHER)
    assert out["event"] == "published" and out["decided_by"] == PUBLISHER["id"]
    v = gp.path_view("service")
    assert v["in_use"] == "1.1" and not v["behind"]["behind"]
    assert [x["state"] for x in v["versions"]] == ["superseded", "published"]
    rows = _rows(seeded, "SELECT template_id, actor, decision FROM wf_decision_steps WHERE subject = ?",
                 ("golden_path:service@1.1",))
    assert rows == [("decl-wft-coe-golden-path-publish", PUBLISHER["id"], "publish")]


def test_the_gate_blocks_a_failed_or_stale_drift_check_and_a_version_behind(gp, seeded, monkeypatch, tmp_path):
    base = gp.path_view("service")["versions"][-1]["entry"]
    p = gp.propose({**base, "version": "1.2"}, AUTHOR)
    _pass_drift(monkeypatch, gp, result="drift")
    gp.record_drift("service", "1.2", AGENT)
    with pytest.raises(gp.GoldenPathRefused) as failed:
        gp.publish("service", "1.2", decision="publish", reason="r", read_digest=p["entry_digest"], actor=PUBLISHER)
    assert "did not pass" in failed.value.message
    _pass_drift(monkeypatch, gp)
    gp.record_drift("service", "1.2", AGENT)
    real = gp.template_digest
    monkeypatch.setattr(gp, "template_digest", lambda entry, root=None: "changed")
    with pytest.raises(gp.GoldenPathRefused) as changed:
        gp.publish("service", "1.2", decision="publish", reason="r", read_digest=p["entry_digest"], actor=PUBLISHER)
    assert "template changed" in changed.value.message
    monkeypatch.setattr(gp, "template_digest", real)
    gp.record_drift("service", "1.2", AGENT)
    _adopt(NEW_STD)
    with pytest.raises(gp.GoldenPathRefused) as behind:
        gp.publish("service", "1.2", decision="publish", reason="r", read_digest=p["entry_digest"], actor=PUBLISHER)
    assert behind.value.code == "gate" and "behind the standards in force" in behind.value.message
    refused = gp.publish("service", "1.2", decision="refuse", reason="Behind (synthetic).",
                         read_digest=p["entry_digest"], actor=PUBLISHER)
    assert refused["event"] == "refused", "refusing is always open to a person; only publishing is gated"


def test_an_agent_only_drafts_and_retiring_is_a_persons_step_never_the_authors(gp, seeded, monkeypatch):
    base = gp.path_view("model-service")["versions"][-1]["entry"]
    p = gp.propose({**base, "version": "1.1"}, AGENT)
    assert p["author_kind"] == "agent"
    _pass_drift(monkeypatch, gp)
    gp.record_drift("model-service", "1.1", AGENT)
    with pytest.raises(gp.GoldenPathRefused) as agent:
        gp.publish("model-service", "1.1", decision="publish", reason="r", read_digest=p["entry_digest"], actor=AGENT)
    assert agent.value.status == 403
    gp.publish("model-service", "1.1", decision="publish", reason="ok (synthetic)", read_digest=p["entry_digest"],
               actor=PUBLISHER)
    with pytest.raises(gp.GoldenPathRefused) as author:
        gp.retire("model-service", "1.1", decision="retire", reason="r", read_digest=p["entry_digest"],
                  actor={"id": AGENT["id"], "role": "reviewer"})
    assert author.value.code == "separation"
    kept = gp.retire("model-service", "1.1", decision="keep", reason="still right (synthetic)",
                     read_digest=p["entry_digest"], actor=STEWARD)
    assert kept["event"] == "kept"
    out = gp.retire("model-service", "1.1", decision="retire", reason="replaced (synthetic)",
                    read_digest=p["entry_digest"], actor=STEWARD)
    assert out["event"] == "retired" and gp.path_view("model-service")["in_use"] is None
    with pytest.raises(gp.GoldenPathRefused):
        gp.propose({**base, "version": "x"}, PUBLISHER)
    with pytest.raises(gp.GoldenPathRefused):
        gp.propose({**base, "version": "1.1"}, PUBLISHER)


def test_the_ledger_is_append_only_and_hash_chained(gp, seeded):
    rows = gp.read_events()
    assert rows and gp.chain(rows)["_problems"] == []
    conn = sqlite3.connect(str(seeded))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE coe_golden_path_events SET reason = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM coe_golden_path_events")
        with pytest.raises(sqlite3.IntegrityError):
            r = rows[0]
            conn.execute("INSERT INTO coe_golden_path_events (event_id, path_id, version, event, actor, actor_kind, "
                         "entry_digest, entry_json, prev_hash, row_hash, created_at) VALUES "
                         "('fork', ?, '9', 'proposed', 'x', 'person', 'd', '{}', ?, 'h', 'now')",
                         (r["path_id"], r["prev_hash"]))
    finally:
        conn.close()
    forged = [dict(r) for r in rows]
    forged[0]["reason"] = "forged"
    assert gp.chain(forged)["_problems"][0]["problem"] == "row hash does not match its content"


def test_seeds_are_recorded_by_the_bootstrap_identity_never_a_person(gp, wf_db):
    from tools.idp import standards

    standards.seed()
    out = gp.seed()
    assert {s["id"]: s["state"] for s in out["seeded"]} == {"service": "published", "model-service": "published"}
    assert gp.seed()["seeded"] == [], "idempotent"
    for r in gp.read_events():
        assert r["actor"] == "bootstrap:icx-coe-04-seed" and r["actor_kind"] == "bootstrap" and r["step_id"] == ""


def test_the_scorecard_grades_another_tree_only_while_rooted(tmp_path):
    from tools.iqe.adapters import idp as adapter

    reg = tmp_path / "args/component_registry.yaml"
    reg.parent.mkdir(parents=True)
    reg.write_text(yaml.safe_dump({"components": [{"key": "coe04_only", "kind": "feature", "display_name": "x"}]}))
    with adapter.rooted(tmp_path, reg):
        assert [r["key"] for r in adapter.components_adapter()] == ["coe04_only"]
    assert "coe04_only" not in [r["key"] for r in adapter.components_adapter()]


# ── page, routes, twin, MCP ──────────────────────────────────────────────────


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


def test_the_routes_and_the_page(gp, seeded, monkeypatch):
    author, pub = _page_app({**AUTHOR, "role": "reviewer"}), _page_app(PUBLISHER)
    html = author.get("/idp/golden-paths").get_data(as_text=True)
    for want in ("Golden paths", "conformant by construction", "needs evidence", "decl-wft-coe-golden-path-publish",
                 "decl-wft-coe-golden-path-retire", "idp.idp_api_golden_path", "idp.idp_api_golden_path_drift",
                 "idp.idp_api_golden_path_propose", "idp.idp_api_golden_path_publish", "idp.idp_api_golden_path_retire",
                 "idp.idp_api_standards_twin", 'data-path="service"', 'data-path="model-service"'):
        assert want in html, want
    assert "/api/" not in (ROOT / "tools/dashboard/templates/idp/golden_paths.html").read_text(encoding="utf-8")
    lst = author.get("/idp/api/golden-paths").get_json()
    assert [p["id"] for p in lst["paths"]] == ["model-service", "service"]
    assert author.get("/idp/api/golden-paths/zz").status_code == 404
    r = author.post("/idp/api/golden-paths/propose", json={"id": "service", "version": "1.1",
                                                            "satisfies": [f"{s}@1.0" for s in SEEDED]})
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()["entry_digest"]
    _pass_drift(monkeypatch, gp)
    assert author.post("/idp/api/golden-paths/service/drift", json={"version": "1.1"}).get_json()["result"] == "pass"
    body = {"version": "1.1", "decision": "publish", "reason": "Read it (synthetic).", "digest": d}
    refused = author.post("/idp/api/golden-paths/service/publish", json=body)
    assert refused.status_code == 403 and refused.get_json()["code"] == "separation"
    assert pub.post("/idp/api/golden-paths/service/publish", json=body).get_json()["event"] == "published"
    one = pub.get("/idp/api/golden-paths/service").get_json()
    assert one["in_use"] == "1.1" and one["versions"][-1]["entry"]["satisfies"][0]["by"]
    keep = pub.post("/idp/api/golden-paths/service/retire", json={**body, "decision": "keep"})
    assert keep.status_code == 200 and keep.get_json()["event"] == "kept"
    # linked from the standards register and in the nav
    text = (ROOT / "tools/dashboard/templates/idp/standards.html").read_text(encoding="utf-8")
    assert "url_for('idp.idp_golden_paths_page')" in text
    reg = yaml.safe_load((ROOT / "args/component_registry.yaml").read_text(encoding="utf-8-sig"))
    idp = next(c for c in reg["components"] if isinstance(c, dict) and c.get("key") == "idp")
    assert "/idp/golden-paths" in [lk["href"] for lk in idp["nav"]["links"]]


def test_the_twin_observes_paths_with_threads_and_replays(gp, seeded):
    from tools.twin_core import governance
    from tools.twin_core.adapters.coe_standards import EngineeringStandardsTwin

    twin = EngineeringStandardsTwin()
    assert "coe.golden_paths" in twin.observes
    _adopt(NEW_STD)
    ev = twin.collect()
    env = twin.envelope(ev)
    found = {v["rule_id"]: v for v in env["violations"] if v["rule_id"].startswith("coe.golden_path")}
    assert "coe.golden_path.behind" in found and "coe.golden_path.bootstrap_only" in found
    behind = [v for v in env["violations"] if v["rule_id"] == "coe.golden_path.behind"]
    assert {v["title"].split(" ")[0] for v in behind} == {"service", "model-service"}
    res = governance.resolve_thread(behind[0]["thread_id"])
    assert res["resolved"] and res["source"] == "coe_golden_path_events" and res["record"]["event"] == "seeded"
    checks = {c["id"]: c["status"] for c in env["extra"]["checks"]}
    assert checks["coe.golden_path.service"] == "verified"
    assert checks["coe.golden_path.service.drift"] == "unverified", "never drift-checked: unverified, never passed"
    again = twin.envelope(json.loads(json.dumps(ev)))
    assert again["extra"]["verdict_digest"] == env["extra"]["verdict_digest"], "a replay judges the same"


def test_the_twin_says_unverified_when_the_ledger_cannot_be_read(gp):
    checks, findings, summary = gp.twin_judge({"paths": None, "errors": {"golden_paths": "down"}}, {}, "2026-11-18")
    assert checks[0]["status"] == "unverified" and not findings and summary["golden_paths"] == 0


def test_the_mcp_tools_read_and_only_propose(gp, seeded):
    from tools.mcp import gap_handlers as h
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, tool_authorization

    read = h.handle_coe_golden_paths({})
    assert {p["id"] for p in read["paths"]["paths"]} == {"service", "model-service"}
    assert h.handle_coe_golden_paths({"check": True})["check"]["ok"]
    out = h.handle_coe_golden_path_propose({"agent_id": "drafter", "path_id": "service", "version": "1.1",
                                            "satisfies": [f"{s}@1.0" for s in SEEDED]})
    assert out["draft"]["author_kind"] == "agent" and out["draft"]["state"] == "proposed"
    assert READ_ONLY_DECLARATIONS["coe_golden_paths"] is True and READ_ONLY_DECLARATIONS["coe_golden_path_propose"] is False
    assert tool_authorization("coe_golden_paths")["tier"] == "read"
    assert tool_authorization("coe_golden_path_propose")["tier"] != "read"


def test_the_scaffold_finds_its_repo_in_a_checkout_named_icdev_and_from_the_mirror(tmp_path):
    """CI checks out into /home/runner/work/icdev/icdev: a checkout NAMED icdev is the repo, not the mirror."""
    from tools.cli.scaffold import repo_root_of

    ci = tmp_path / "work" / "icdev" / "icdev"
    (ci / "tools" / "cli").mkdir(parents=True)
    (ci / "tools" / "cli" / "scaffold.py").write_text("", encoding="utf-8")
    assert repo_root_of(ci / "tools" / "cli" / "scaffold.py") == ci.resolve()
    mirror = ci / "icdev" / "tools" / "cli"
    mirror.mkdir(parents=True)
    (mirror / "scaffold.py").write_text("", encoding="utf-8")
    assert repo_root_of(mirror / "scaffold.py") == ci.resolve(), "the icdev/ mirror climbs to its repo"
    plain = tmp_path / "wt" / "tools" / "cli"
    plain.mkdir(parents=True)
    assert repo_root_of(plain / "scaffold.py") == (tmp_path / "wt").resolve()
