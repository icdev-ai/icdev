# CUI // SP-CTI
"""The bundle carries what ICDEV[OS] now accepts (icx-mdz-04; ICDEV[OS] contract 1.189.0, osx-mdz-01..04).

Covers:
  * the vendored contract is ICDEV[OS]'s 1.189.0, pinned by SHA-256;
  * inline, not beside the bundle: a feature read from source AND seen running is ``provenance: both`` (basis source),
    with its capability hints; each process carries its source, feature, order, derivation, step signature and review,
    each step its verb, object, signature, evidence and APIs; ``sections`` is the bundle's own map and agrees with what
    it carries; the 7R carries each strategy's confidence, unmeasured criteria and whether it is assessable;
  * waves derived before any plan are ``derived_waves`` (never approved); a planned wave carries the sum of its tasks'
    estimates, and none -- never 0 -- where no task is on it; no date is made up;
  * the application's environment comes only from the declared file, with its source; an entry with no source or an
    impact level off the ladder is refused; an undeclared application has none, and the run says so;
  * every bundle the exporter writes is held to the pinned schema AND ICDEV[OS]'s agreement rules (a lying sections map,
    a ``both`` feature with an observed basis, a signature that does not digest its steps are refused);
  * the Modernisation page shows confidence, unmeasured criteria, the security domain and the derived waves; the
    modernisation twin flags a bundle that fails the contract or reports no security domain, threaded to the
    application, and replays; the MCP bundle tool carries the new fields;
  * the three real applications' regenerated bundles validate against the pinned contract, carry everything inline,
    need no side file, and report no security domain (none is declared), which their gap reports say.

Run: pytest tests/test_icx_mdz_04_bundle_fields.py -v
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import tests.test_icx_mdz_03_bundle_features as mdz03
from tests.test_icx_mdz_03_bundle_features import _bundle_evidence, _client, _conn, _report

shop, canvas = mdz03.shop, mdz03.canvas      # icx-mdz-03's fixtures (a synthetic shop, the migration canvas), reused

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "tests" / "fixtures" / "icos" / "modsamples"
REAL = ("daytrader7", "cycle-store", "jpetstore6")
AT = "2026-11-16T00:00:00Z"
OBSERVED = {"basis": "observed", "features_basis": "observed_by_rea", "features": [
    {"kind": "http_endpoint", "label": "POST /cart/checkout", "evidence_ids": ["ev-1"]},
    {"kind": "http_endpoint", "label": "GET /wishlist/{id}", "evidence_ids": ["ev-2"]}]}


def _build(**kw):
    from tools.modernization import apptwin_export as ax

    conn = _conn()
    try:
        return ax.build("app-1", conn, at=AT, **kw)
    finally:
        conn.close()


# ── the contract ─────────────────────────────────────────────────────────────


def test_the_vendored_contract_is_icos_1_192_0_pinned_by_digest():
    from tools.modernization import apptwin_export as ax

    raw = (ROOT / "context" / "icos" / "apptwin.schema.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ax.SCHEMA_SHA256
    doc = json.loads(raw)
    assert doc["x-icos-contract"] == ax.CONTRACT_VERSION == "1.192.0"     # osx-mdz-09 re-pinned it (1.189.0 before)
    readme = (ROOT / "context" / "icos" / "README.md").read_text(encoding="utf-8")
    assert ax.SCHEMA_SHA256 in readme and "1.192.0" in readme
    d = doc["$defs"]
    assert "both" in d["Feature"]["properties"]["provenance"]["anyOf"][0]["enum"]
    assert {"derived_waves", "sections"} <= set(doc["properties"])
    assert "security_domain" in d["Environment"]["properties"] and "estimated_hours" in d["Wave"]["properties"]
    assert set(ax.SECTIONS) | {"schema", "application", "producer", "produced_at", "derived_waves", "sections"} \
        == set(doc["properties"]), "the exporter's sections are the contract's"


# ── features, processes, sections ────────────────────────────────────────────


def test_a_feature_seen_both_ways_is_both_with_its_hints_inline(shop):
    b = _build(extract=_report(observed=OBSERVED))
    feats = {f["id"]: f for f in b["features"]}
    cart, wish, catalog = feats["cart"], feats["wishlist"], feats["catalog"]
    assert (cart["provenance"], cart["basis"]) == ("both", "source"), "read from source AND seen running"
    assert (wish["provenance"], wish["basis"]) == ("observed", "observed")
    assert (catalog["provenance"], catalog["basis"]) == ("static", "source")
    hints = {h["capability"]: h for h in cart["capability_hints"]}
    assert {"cart", "catalogue", "order"} <= set(hints) and hints["catalogue"]["terms"] and "calls" in hints["catalogue"]["from"]
    from tools.modernization import apptwin_export as ax
    assert ax.validate(b) == []


def test_processes_carry_source_signature_review_and_step_fields(shop):
    b = _build(extract=_report())
    procs = {p["id"]: p for p in b["processes"]}
    cart = procs["cart-actions"]
    assert cart["source"] == "static" and cart["feature"] == "cart" and "not an observed sequence" in cart["order"]
    assert cart["derived_from"] == "handler methods" and "truncated" not in cart
    sig = cart["signature"]
    assert sig["steps"] == ["view:cart", "create:cart+item", "submit:cart"]
    assert sig["digest"] == hashlib.sha256("\n".join(sig["steps"]).encode()).hexdigest() and len(sig["rules_digest"]) == 64
    assert cart["review"] == {"state": "unreviewed"}
    s2 = cart["steps"][1]
    assert (s2["verb"], s2["object"], s2["signature"]) == ("create", "cart+item", "create:cart+item")
    assert s2["evidence"] == "web/CartController.java:13" and s2["apis"] == ["GET /cart/addItem"]
    trade = procs["trade-actions"]
    assert [s["signature"] for s in trade["steps"]] == ["authenticate", "buy", "sell"]
    assert all("object" not in s for s in trade["steps"]), "a verb with no object names none"


def test_a_reviewed_process_is_exported_reviewed_at_its_digest(canvas, shop):
    feats = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/features").get_json()
    proc = next(p for p in feats["processes"] if p["id"] == "cart-actions")
    resp = _client(canvas).post("/migration-canvas/api/modernisation/apps/app-1/processes/cart-actions/review",
                                json={"decision": "confirm", "reason": "the cart flow is real",
                                      "signature_digest": proc["signature"]["digest"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    b = _build(extract=_report())
    rv = next(p for p in b["processes"] if p["id"] == "cart-actions")["review"]
    assert rv["state"] == "reviewed" and rv["by"] == "u-pm" and rv["signature_digest"] == proc["signature"]["digest"]
    assert rv["step_id"] and rv["at"] and rv["decision"] == "confirm"
    from tools.modernization import apptwin_export as ax
    assert ax.validate(b) == []


def test_the_sections_map_is_inline_and_agrees_with_what_is_carried(shop):
    from tools.modernization import apptwin_export as ax

    b = _build(extract=_report(), waves={"tool": "icdev-it/app_wave_planner", "basis": "1 dependency",
                                         "waves": [{"id": "w1", "name": "Wave 1", "wave_number": 1, "status": "planned",
                                                    "components": ["shop.web.CatalogService"], "backout": "route back"}]},
               reasons={"migration": "refused, no ICDEV[OS] may-plan (missing): no export"})
    s = b["sections"]
    assert set(s) == set(ax.SECTIONS) | set(ax.PARTS), "only the contract's sections and parts"
    assert s["features"] == {"state": "present", "count": 3} and s["processes"]["state"] == "present"
    assert s["migration"] == {"state": "missing", "reason": "refused, no ICDEV[OS] may-plan (missing): no export"}
    assert s["migration.waves"]["state"] == "missing" and s["migration.waves"]["derived"] == 1
    assert "derived_waves" in s["migration.waves"]["reason"] and "migration-plan gate will block" in s["migration.waves"]["reason"]
    assert ax.agreement(b) == []
    lying = json.loads(json.dumps(b))
    lying["sections"]["features"] = {"state": "missing", "reason": "none"}
    lying["sections"]["refactor.jobs"] = {"state": "present", "count": 2}
    errs = ax.validate(lying)
    assert any("features is missing, but it is carried" in e for e in errs)
    assert any("refactor.jobs is present, but it is not carried" in e for e in errs)


def test_icos_agreement_rules_refuse_what_the_importer_would(shop):
    from tools.modernization import apptwin_export as ax

    b = _build(extract=_report(observed=OBSERVED))
    bad = json.loads(json.dumps(b))
    next(f for f in bad["features"] if f["id"] == "cart")["basis"] = "observed"         # both, but not from source
    p = next(x for x in bad["processes"] if x["id"] == "cart-actions")
    p["signature"]["steps"][0] = "delete:cart"                                          # no longer its digest
    p["steps"][1]["evidence"] = "/home/analyst/src/web/CartController.java:13"          # where the analysis ran
    p["review"] = {"state": "reviewed"}                                                 # reviewed by nobody
    errs = ax.validate(bad)
    assert any("a both feature is read from source" in e for e in errs)
    assert any("signature's digest is not the digest of its steps" in e for e in errs)
    assert any("is a local path" in e for e in errs)
    assert any("names its step, who took it" in e for e in errs)
    conn = _conn()
    try:
        with pytest.raises(ax.BundleError, match="icos-apptwin/v1 contract"):
            ax.build("app-1", conn, at=AT, extract=_report(), reasons=None, sections={"rtm": {"tool": "x"}})
    finally:
        conn.close()


# ── 7R, waves ────────────────────────────────────────────────────────────────


def test_the_7r_carries_confidence_unmeasured_and_assessable_as_fields():
    from tools.modernization import apptwin_export as ax

    a = {"assessed_at": "2026-11-15T00:00:00+00:00", "recommended_strategy": "rehost", "evidence": json.dumps({
        "ranking": [{"strategy_id": "rehost", "score": 0.81}, {"strategy_id": "retire", "score": 0.0},
                    {"strategy_id": "rearchitect", "score": None}],
        "strategies": {"rehost": {"score": 0.81, "confidence": 0.64, "assessable": True, "unmeasured": ["team_skills"]},
                       "retire": {"score": 0.0, "confidence": 0.12, "assessable": True,
                                  "unmeasured": ["cost_savings", "functional_redundancy"]},
                       "rearchitect": {"score": None, "confidence": 0.0, "assessable": False, "unmeasured": ["all"]}},
        "recommendation": {"status": "recommended", "strategy": "rehost", "confidence": 0.64, "reason": "rehost scores"}})}
    s = ax.seven_r_from_assessment(a, at=AT)
    ranked = {r["strategy"]: r for r in s["ranked"]}
    assert ranked["rehost"] == {"strategy": "rehost", "score": 0.81, "confidence": 0.64, "unmeasured": ["team_skills"],
                                "assessable": True}
    assert ranked["retire"]["unmeasured"] == ["cost_savings", "functional_redundancy"]
    assert "rearchitect" not in ranked, "not assessable: no score, so not ranked (said in evidence)"
    assert "rearchitect: not assessable (nothing measured)" in s["evidence"]
    assert s["confidence"] == 0.64
    thin = json.loads(a["evidence"])
    thin["recommendation"] = {"status": "insufficient_evidence", "strategy": None, "confidence": None}
    assert "confidence" not in ax.seven_r_from_assessment({**a, "evidence": thin}, at=AT), "no recommendation, none"


def test_waves_before_a_plan_are_derived_and_carry_no_estimate(shop):
    b = _build(extract=_report(), waves={"tool": "icdev-it/app_wave_planner", "basis": "no dependency resolved",
                                         "waves": [{"id": "w1", "name": "Wave 1: all", "wave_number": 1,
                                                    "status": "planned", "components": ["shop.web.CartController"],
                                                    "backout": "route back"}]})
    assert b["migration"] is None
    dw = b["derived_waves"]
    assert dw["tool"] == "icdev-it/app_wave_planner" and dw["basis"] == "no dependency resolved"
    [w] = dw["waves"]
    assert w["id"] == "w1" and "estimated_hours" not in w and "starts" not in w and "ends" not in w


MIGRATION_DDL = ("migration_plans", "migration_tasks")


def _plan(db: Path, tasks: list[tuple]) -> None:
    import re

    from tools.modernization import apptwin_export as ax
    text = (Path(ax._BASE) / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    c = sqlite3.connect(str(db))
    try:
        for t in MIGRATION_DDL:   # the definitions of record, never a hand-written copy
            c.execute(re.search(rf"CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);", text, re.S).group(0))
        c.execute("INSERT INTO migration_plans (id, legacy_app_id, plan_name, strategy, status, created_at) "
                  "VALUES ('mplan-1', 'app-1', 'rehost_shop', 'rehost', 'draft', '2026-11-16T00:00:00Z')")
        for i, (comp, hours) in enumerate(tasks):
            c.execute("INSERT INTO migration_tasks (id, plan_id, legacy_component_id, task_type, title, estimated_hours) "
                      "VALUES (?, 'mplan-1', ?, 'deploy', ?, ?)", (f"t{i}", comp, f"task {i}", hours))
        c.commit()
    finally:
        c.close()


def test_a_planned_wave_carries_the_sum_of_its_tasks_estimates_and_none_is_never_zero(shop):
    _plan(shop, [("c-cart", 6.0), ("c-cart", 2.5), ("c-catalog", 4.0), ("c-trade", None), (None, 8.0)])
    waves = {"waves": [
        {"id": "w1", "name": "Wave 1", "wave_number": 1, "status": "planned", "components": ["shop.web.CartController"]},
        {"id": "w2", "name": "Wave 2", "wave_number": 2, "status": "planned", "components": ["shop.web.CatalogController"]},
        {"id": "w3", "name": "Wave 3", "wave_number": 3, "status": "planned", "components": ["shop.web.TradeServlet"]},
        {"id": "w4", "name": "Wave 4", "wave_number": 4, "status": "planned", "components": ["shop.web.CatalogService"]}]}
    b = _build(extract=_report(), waves=waves)
    assert "derived_waves" not in b, "a plan carries the waves"
    got = {w["id"]: w.get("estimated_hours") for w in b["migration"]["waves"]}
    assert got == {"w1": 8.5, "w2": 4.0, "w3": None, "w4": None}, \
        "a task with no estimate leaves its wave unknown; a wave no task is on has none; the plan-level task is no wave's"
    assert all("starts" not in w and "ends" not in w for w in b["migration"]["waves"]), "never scheduled here"
    assert b["sections"]["migration.waves"] == {"state": "present", "count": 4}


# ── the environment ──────────────────────────────────────────────────────────


def test_the_environment_comes_only_from_the_declared_file_with_its_source(shop, tmp_path):
    from tools.modernization import apptwin_export as ax

    env_file = tmp_path / "envs.yaml"
    env_file.write_text("applications:\n  Shop:\n    security_domain: nipr\n    impact_level: il4\n"
                        "    source: \"SSP SHOP-1 section 9.1\"\n", encoding="utf-8")
    env, rep = ax.environment("app-1", "Shop", path=env_file)
    assert env == {"security_domain": "nipr", "impact_level": "il4"}
    assert rep["declared"] is True and rep["source"] == "SSP SHOP-1 section 9.1"
    assert rep["unreported"] == ["ato_boundary", "hosting"]
    b = _build(extract=_report(), environment=env)
    assert b["application"]["environment"] == {"security_domain": "nipr", "impact_level": "il4"}
    none, why = ax.environment("app-1", "Shop")
    assert none is None and why["declared"] is False and "never guessed" in why["reason"]
    assert "environment" not in _build(extract=_report())["application"], "absent stays absent"
    for body, msg in (("applications:\n  Shop:\n    security_domain: nipr\n", "names no source"),
                      ("applications:\n  Shop:\n    impact_level: IL4\n    source: x\n", "is not one of"),
                      ("applications:\n  Shop:\n    enclave: x\n    source: x\n", "names ['enclave']"),
                      ("applications:\n  Shop:\n    security_domain: no\n    source: x\n", "must be words")):
        env_file.write_text(body, encoding="utf-8")
        with pytest.raises(ax.BundleError, match=msg.replace("[", r"\[").replace("]", r"\]")):
            ax.environment("app-1", "Shop", path=env_file)
    shipped = ax.declared_environments()
    assert shipped == {}, "the shipped file declares no application (the three samples are deployed nowhere)"


# ── the page, the twin, MCP ──────────────────────────────────────────────────


def test_the_page_shows_confidence_unmeasured_security_domain_and_derived_waves():
    rel = "tools/dashboard/templates/migration_canvas/modernisation.html"
    text = (ROOT / rel).read_text(encoding="utf-8")
    for needle in ("mdz-bundle-7r", "mdz-7r-confidence", "mdz-7r-unmeasured", "mdz-security-domain",
                   "mdz-derived-waves", "mdz-bundle-contract", "environment_report", "derived_waves",
                   "args/modernisation_environments.yaml"):
        assert needle in text, needle
    assert "/api/" not in text
    assert (ROOT / "icdev" / rel).read_text(encoding="utf-8") == text, "not mirrored to icdev/"


def test_the_bundle_preview_and_mcp_tool_carry_the_new_fields(canvas):
    from tools.mcp import gap_handlers
    from tools.modernization import apptwin_export as ax

    d = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/bundle").get_json()
    assert d["valid"] is True and d["schema_errors"] == [] and d["contract"] == ax.CONTRACT_VERSION
    assert d["security_domain"] is None and d["environment_report"]["declared"] is False
    assert d["derived_waves"]["waves"] and "never approved" in d["derived_waves"]["means"]
    assert d["section_states"]["migration.waves"]["derived"] == len(d["derived_waves"]["waves"])
    assert d["seven_r"] is None, "no assessment in this database: nothing to carry"
    m = gap_handlers.handle_modernisation_bundle({"app_id": "app-1"})
    for k in ("schema_errors", "contract", "security_domain", "environment_report", "derived_waves", "seven_r",
              "section_states", "planned_waves"):
        assert k in m, k


def test_the_twin_flags_a_bundle_failing_the_contract_or_without_a_security_domain():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    ev = _bundle_evidence(bundles=[
        {"app": "app-1", "name": "Shop", "valid": False, "error": None, "features": 3, "processes": [],
         "missing": {}, "schema_errors": ["features/0/provenance: 'seen' is not one of ['static', 'observed', 'both']"],
         "security_domain": None, "environment_reason": "no environment is declared for Shop"},
        {"app": "app-2", "name": "Depot", "valid": True, "error": None, "features": 1, "processes": [],
         "missing": {}, "schema_errors": [], "security_domain": "nipr", "environment_reason": None}])
    out = ModernisationGovernanceTwin().judge(ev)
    found = {(f["rule_id"], f["thread_id"]) for f in out["findings"]}
    assert ("mdz.exporter.schema_invalid", "legacy_app:app-1") in found
    assert ("mdz.exporter.no_security_domain", "legacy_app:app-1") in found
    assert not {r for r, t in found if t == "legacy_app:app-2"}, "valid, with a declared domain: nothing to say"
    no_dom = next(f for f in out["findings"] if f["rule_id"] == "mdz.exporter.no_security_domain")
    assert "no environment is declared for Shop" in no_dom["detail"]
    assert out["summary"]["bundles_failing_contract"] == 1 and out["summary"]["bundles_without_security_domain"] == 1


def test_the_twin_observes_no_security_domain_with_a_thread_and_replays(canvas):
    from tools.twin_core import governance

    adapter = governance.adapter_for("mdc")
    env = governance.observe(adapter)
    threads = {(v["rule_id"], v["thread_id"]) for v in env["violations"]}
    assert ("mdz.exporter.no_security_domain", "legacy_app:app-1") in threads
    assert not any(r == "mdz.exporter.schema_invalid" for r, _ in threads), "what the exporter writes meets the contract"
    replayed = governance.replay(adapter, env["extra"]["observation_id"])
    assert replayed["match"] is True, replayed["reason"]


# ── the three real applications ──────────────────────────────────────────────


def test_the_real_bundles_meet_the_pinned_contract_inline_with_no_side_file():
    from tools.modernization import apptwin_export as ax

    for name in REAL:
        b = json.loads((BUNDLES / f"{name}.apptwin.json").read_text(encoding="utf-8"))
        gaps = json.loads((BUNDLES / f"{name}.gaps.json").read_text(encoding="utf-8"))
        assert ax.validate(b) == [], name
        assert not (BUNDLES / f"{name}.features.json").exists(), f"{name}: everything is inline; no side file"
        assert gaps["contract"] == "1.189.0" and b["sections"] == gaps["sections"], name   # made at 1.189.0; still valid
        assert any(f.get("capability_hints") for f in b["features"]), f"{name}: hints inline"
        assert {f["provenance"] for f in b["features"]} == {"static"}, f"{name}: the source is never run"
        assert all(p["signature"]["digest"] and p["review"]["state"] == "unreviewed" and p["source"] == "static"
                   for p in b["processes"]), name
        assert all(s.get("verb") and s.get("signature") and s.get("evidence") for p in b["processes"] for s in p["steps"])
        assert len({p["signature"]["rules_digest"] for p in b["processes"]}) == 1, f"{name}: one rule set"
        ranked = b["seven_r"]["ranked"]
        assert all("confidence" in r and "assessable" in r for r in ranked) and b["seven_r"].get("confidence"), name
        retire = next(r for r in ranked if r["strategy"] == "retire")
        assert "functional_redundancy" in retire["unmeasured"], f"{name}: no ICDEV[OS] export yet, so unmeasured"
        assert b["migration"] is None and b["derived_waves"]["waves"], f"{name}: derived before any plan"
        assert all("estimated_hours" not in w for w in b["derived_waves"]["waves"]), "no task yet: no estimate"
        assert "environment" not in b["application"], f"{name}: no environment declared"
        assert gaps["environment"]["declared"] is False and "never guessed" in gaps["environment"]["reason"]
        red = next(s for s in gaps["steps"] if s["step"] == "seven-r")["redundancy"]
        assert red["share"] is None and red["state"] == "missing"
    hints = {n: {h["capability"] for f in json.loads((BUNDLES / f"{n}.apptwin.json").read_text())["features"]
                 for h in f.get("capability_hints") or []} for n in REAL}
    assert "catalogue" in hints["jpetstore6"] & hints["cycle-store"]
