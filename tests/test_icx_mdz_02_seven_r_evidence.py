# CUI // SP-CTI
"""7R on evidence, one engine, may-plan enforced (icx-mdz-02).

The defects, each pinned by a test that fails on the old code:

  * ``seven_r_assessor._score_strategy`` paired catalogue criteria with check results BY LIST POSITION when their
    names differed (six of seven strategies), and scored inputs it could not measure as 0.5, so every real
    application came out ``rehost``. Now every criterion is the catalogue's, by name; a name mismatch is refused;
    an unmeasured criterion is None, left out of the score, lowers the strategy's confidence and never counts 0.5;
    a strategy with nothing measured is not assessable; only a strategy at the catalogue's minimum confidence is
    recommended;
  * three 7R generators: ``migration_intelligence/strategy_generator`` recommended whichever option came first and
    ``migration_canvas/coa_engine`` a technology's mapped strategy. Both now consume the assessor's result: the same
    application gets the same strategy from all three, or none from any;
  * ICDEV[OS]'s signed ``may-plan`` was verified but not enforced: ``monolith_decomposer`` wrote plans and
    ``refactor_jobs`` ran migrators without it. Now a missing, untrusted or refusing may-plan writes nothing, and
    the refusal is audited, shown on /migration and observed by the twin with its thread.

Run: pytest tests/test_icx_mdz_02_seven_r_evidence.py -v
"""
from __future__ import annotations

import base64
import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask, g

from tests._schema_compat import ensure_tables

ROOT = Path(__file__).resolve().parents[1]
PM = {"id": "u-pm", "role": "pm"}

# The tables the assessor, the planner and the routes read and write, with the schema of record's NOT NULL columns
# and keys (tools/db/schema/pg_consolidated.sql), and only the other columns these tests use.
DDL = (
    "CREATE TABLE IF NOT EXISTS legacy_applications (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, source_path TEXT NOT NULL, primary_language TEXT NOT NULL, language_version TEXT, framework TEXT, framework_version TEXT, app_type TEXT DEFAULT 'monolith', analysis_status TEXT DEFAULT 'registered', loc_total INTEGER DEFAULT 0, loc_code INTEGER DEFAULT 0, file_count INTEGER DEFAULT 0, complexity_score REAL DEFAULT 0.0, tech_debt_hours REAL DEFAULT 0.0, maintainability_index REAL DEFAULT 0.0)",
    "CREATE TABLE IF NOT EXISTS legacy_components (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, name TEXT NOT NULL, component_type TEXT NOT NULL, file_path TEXT NOT NULL, qualified_name TEXT, loc INTEGER DEFAULT 0, cyclomatic_complexity REAL DEFAULT 0.0, coupling_score REAL DEFAULT 0.0, cohesion_score REAL DEFAULT 0.0, dependencies_in INTEGER DEFAULT 0, dependencies_out INTEGER DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS legacy_dependencies (id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_app_id TEXT NOT NULL, source_component_id TEXT NOT NULL, target_component_id TEXT, dependency_type TEXT NOT NULL, evidence TEXT)",
    "CREATE TABLE IF NOT EXISTS legacy_apis (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, component_id TEXT, method TEXT, path TEXT NOT NULL, handler_function TEXT)",
    "CREATE TABLE IF NOT EXISTS legacy_db_schemas (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, db_type TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL, data_type TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS migration_assessments (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, component_id TEXT, assessment_scope TEXT DEFAULT 'application', rehost_score REAL DEFAULT 0.0, replatform_score REAL DEFAULT 0.0, refactor_score REAL DEFAULT 0.0, rearchitect_score REAL DEFAULT 0.0, repurchase_score REAL DEFAULT 0.0, retire_score REAL DEFAULT 0.0, retain_score REAL DEFAULT 0.0, recommended_strategy TEXT, cost_estimate_hours REAL, risk_score REAL DEFAULT 0.0, timeline_weeks INTEGER, ato_impact TEXT, tech_debt_reduction REAL DEFAULT 0.0, scoring_weights TEXT, evidence TEXT, assessed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS migration_plans (id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL, plan_name TEXT NOT NULL, strategy TEXT NOT NULL, target_language TEXT, target_framework TEXT, target_database TEXT, target_architecture TEXT, migration_approach TEXT, total_tasks INTEGER DEFAULT 0, status TEXT DEFAULT 'draft', estimated_hours REAL DEFAULT 0, created_at TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS migration_tasks (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, legacy_component_id TEXT, task_type TEXT NOT NULL, title TEXT NOT NULL, description TEXT, priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'pending', estimated_hours REAL DEFAULT 0, dependencies TEXT, created_at TEXT)",
)


def _sql(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _rich(conn, app_id="app-rich"):
    """An analysed Java 8 / Struts application with components, a test component, imports and a PostgreSQL schema."""
    conn.execute("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language, language_version, "
                 "framework, analysis_status, loc_total, loc_code, tech_debt_hours, maintainability_index) "
                 "VALUES (?, 'proj-1', 'Shop', '/src/shop', 'java', '8', 'struts', 'analyzed', 4000, 3500, 40, 62)",
                 (app_id,))
    for cid, name, kind in (("c1", "Cart", "class"), ("c2", "Catalog", "class"), ("c3", "Order", "class"),
                            ("c4", "CartTest", "test")):
        conn.execute("INSERT INTO legacy_components (id, legacy_app_id, name, component_type, file_path, qualified_name) "
                     "VALUES (?, ?, ?, ?, 'x.java', ?)", (f"{app_id}-{cid}", app_id, name, kind, f"shop.{name}"))
    for evidence in ("import java.util.List", "import java.io.File"):
        conn.execute("INSERT INTO legacy_dependencies (legacy_app_id, source_component_id, dependency_type, evidence) "
                     "VALUES (?, ?, 'import', ?)", (app_id, f"{app_id}-c1", evidence))
    for i, table in enumerate(("cart", "item", "orders")):
        conn.execute("INSERT INTO legacy_db_schemas (id, legacy_app_id, db_type, table_name, column_name, data_type) "
                     "VALUES (?, ?, 'postgresql', ?, 'id', 'int')", (f"{app_id}-s{i}", app_id, table))


def _thin(conn, app_id="app-thin"):
    """A registered application nobody analysed: no figure of it is a measurement."""
    conn.execute("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language) "
                 "VALUES (?, 'proj-1', 'Unknown', '/src/x', 'unknown')", (app_id,))


@pytest.fixture()
def db(wf_db, monkeypatch):
    conn = sqlite3.connect(str(wf_db))
    try:
        ensure_tables(conn, DDL)
        _rich(conn)
        _thin(conn)
        conn.commit()
    finally:
        conn.close()
    for mod in ("tools.modernization.seven_r_assessor", "tools.modernization.monolith_decomposer",
                "tools.dashboard.api.migration"):
        monkeypatch.setattr(importlib.import_module(mod), "DB_PATH", Path(wf_db))
    monkeypatch.delenv("ICDEV_ICOS_DECISIONS", raising=False)
    monkeypatch.delenv("ICDEV_ICOS_JWKS", raising=False)
    return wf_db


def _sr():
    return importlib.import_module("tools.modernization.seven_r_assessor")


def _criteria(sid):
    return next(s for s in _sr().load_seven_rs_catalog()["strategies"] if s["id"] == sid)["scoring_criteria"]


# ── 7R by name ────────────────────────────────────────────────────────────────


def test_every_check_returns_exactly_its_catalogue_criteria_by_name():
    sr = _sr()
    catalog = sr.load_seven_rs_catalog()
    assert set(sr.FITNESS_CHECKS) == {s["id"] for s in catalog["strategies"]}
    profile = {"analysis_status": "analyzed", "component_count": 3, "framework": "spring", "primary_language": "java",
               "language_version": "17"}
    for strat in catalog["strategies"]:
        got = sr.FITNESS_CHECKS[strat["id"]](profile)
        assert set(got) == set(strat["scoring_criteria"]), f"{strat['id']}: criteria not keyed by the catalogue's names"
        sr._strategy_evidence(strat["id"], got, strat["scoring_criteria"])        # never raises on the real pairs


def test_a_renamed_criterion_is_refused_never_paired_by_position(db, monkeypatch):
    """Planted: rename ONE criterion in a check (or in the catalogue) and the assessment fails, writing nothing."""
    sr = _sr()
    checks = sr._check_rehost_fitness({})
    renamed = {("containerizable" if k == "infrastructure_compatibility" else k): v for k, v in checks.items()}
    with pytest.raises(sr.CriteriaMismatch, match="containerizable"):
        sr._score_strategy("rehost", renamed, _criteria("rehost"))

    real = sr.FITNESS_CHECKS["rehost"]
    monkeypatch.setitem(sr.FITNESS_CHECKS, "rehost", lambda p: {
        ("containerizable" if k == "infrastructure_compatibility" else k): v for k, v in real(p).items()})
    with pytest.raises(sr.CriteriaMismatch):
        sr.run_seven_r_assessment("proj-1", "app-rich", db_path=db)
    assert _sql(db, "SELECT COUNT(*) FROM migration_assessments")[0][0] == 0, "a refused assessment wrote a row"
    monkeypatch.setitem(sr.FITNESS_CHECKS, "rehost", real)

    catalog = sr.load_seven_rs_catalog()
    for s in catalog["strategies"]:
        if s["id"] == "retain":
            s["scoring_criteria"]["stable_operation"] = s["scoring_criteria"].pop("current_environment_viability")
    monkeypatch.setattr(sr, "load_seven_rs_catalog", lambda *a, **k: catalog)
    with pytest.raises(sr.CriteriaMismatch, match="stable_operation"):
        sr.run_seven_r_assessment("proj-1", "app-rich", db_path=db)


def test_custom_weights_must_name_the_catalogues_criteria():
    sr = _sr()
    checks = sr._check_replatform_fitness({})
    with pytest.raises(sr.CriteriaMismatch, match="managed_db_candidate"):
        sr._strategy_evidence("replatform", checks, _criteria("replatform"),
                              weights={"replatform": {"managed_db_candidate": 0.9}})


def test_an_unmeasured_input_lowers_confidence_and_never_counts_half():
    """Planted: one measured criterion and the rest unmeasured. The score is the measured one alone (never pulled
    towards 0.5), and the confidence is exactly the measured weight's share."""
    sr = _sr()
    crit = _criteria("repurchase")
    only = {k: None for k in crit} | {"data_migration_feasibility": 1.0}
    ev = sr._strategy_evidence("repurchase", only, crit)
    w = crit["data_migration_feasibility"]["weight"]
    assert ev["score"] == 1.0 and ev["assessable"] is True
    assert abs(ev["confidence"] - w / sum(c["weight"] for c in crit.values())) < 1e-4
    assert set(ev["unmeasured"]) == set(crit) - {"data_migration_feasibility"}
    half = {k: 0.5 for k in crit} | {"data_migration_feasibility": 1.0}
    assert sr._strategy_evidence("repurchase", half, crit)["score"] < ev["score"], "0.5 would have pulled it down"
    # More measured weight raises the confidence; an unmeasured criterion never moves the score.
    two = only | {"vendor_viability": 1.0}
    assert sr._strategy_evidence("repurchase", two, crit)["confidence"] > ev["confidence"]
    assert sr._strategy_evidence("repurchase", two, crit)["score"] == 1.0
    # With nothing measured the checks return None for every criterion -- not one 0.5 stands in for a measurement.
    for sid, check in sr.FITNESS_CHECKS.items():
        assert all(v is None for v in check({}).values()), f"{sid} scored an input it did not measure"


def test_a_strategy_with_nothing_measured_is_not_assessable(db):
    sr = _sr()
    ev = sr._strategy_evidence("rearchitect", sr._check_rearchitect_fitness({}), _criteria("rearchitect"))
    assert ev["score"] is None and ev["assessable"] is False and ev["confidence"] == 0.0
    a = sr.run_seven_r_assessment("proj-1", "app-rich", db_path=db)
    assert a["strategies"]["rearchitect"]["assessable"] is False and a["rearchitect_score"] is None
    assert a["ranking"][-1]["strategy_id"] == "rearchitect" and a["ranking"][-1]["score"] is None
    assert _sql(db, "SELECT rearchitect_score FROM migration_assessments WHERE id = ?", (a["id"],))[0][0] is None


def test_only_a_strategy_at_the_minimum_confidence_is_recommended(db):
    sr = _sr()
    rich = sr.run_seven_r_assessment("proj-1", "app-rich", db_path=db)
    floor = sr.min_confidence()
    assert rich["recommended_strategy"] and rich["confidence"] >= floor
    top = next(r for r in rich["ranking"] if r["strategy_id"] == rich["recommended_strategy"])
    assert top["eligible"] and all(not r["eligible"] for r in rich["ranking"] if r["confidence"] < floor)
    # A strategy scoring higher on less evidence is ranked, but never recommended.
    assert all(r["rank"] > top["rank"] for r in rich["ranking"] if not r["eligible"])

    thin = sr.run_seven_r_assessment("proj-1", "app-thin", db_path=db)
    assert thin["recommended_strategy"] is None and thin["recommendation"]["status"] == "insufficient_evidence"
    assert "no criterion of any strategy was measured" in thin["recommendation"]["reason"]
    assert thin["cost_estimate_hours"] is None and thin["ato_impact"] is None
    assert _sql(db, "SELECT recommended_strategy FROM migration_assessments WHERE id = ?", (thin["id"],))[0][0] is None
    summary = sr.strategy_for_app("app-thin", db_path=db)
    assert summary["recommended_strategy"] is None and len(summary["not_assessable"]) == 7


# ── one engine ───────────────────────────────────────────────────────────────


def _mi(tmp_path, monkeypatch, app_id):
    from tools.migration_intelligence.db import init_db as mi

    path = str(tmp_path / "mi.db")
    monkeypatch.setenv("MI_DB_PATH", path)
    mi.init_db(path)
    conn = mi.get_connection(path)
    try:
        conn.execute("INSERT INTO mi_opportunities (id, title, opportunity_type, source_canvas, source_entity_id, "
                     "status) VALUES (%s, %s, 'application_migration', 'mdc', %s, 'scored')",
                     (f"opp-{app_id}", f"migrate {app_id}", app_id))
        conn.commit()
    finally:
        conn.close()
    return path


def test_the_three_generators_give_the_same_strategy_for_the_same_app(db, tmp_path, monkeypatch):
    from tools.migration_canvas import coa_engine
    from tools.migration_intelligence import strategy_generator as sg

    sr = _sr()
    assessed = sr.run_seven_r_assessment("proj-1", "app-rich", db_path=db)["recommended_strategy"]
    assert assessed

    path = _mi(tmp_path, monkeypatch, "app-rich")
    out = sg.generate_strategies_for_opportunity("opp-app-rich", db_path=path)
    assert out["seven_r"]["status"] == "recommended" and out["recommended"] == sg.SEVEN_R_TO_MI[assessed]
    rows = _sql(path, "SELECT strategy_type FROM mi_strategies WHERE opportunity_id = 'opp-app-rich' AND recommended = 1")
    assert rows == [(sg.SEVEN_R_TO_MI[assessed],)], "exactly the assessor's strategy, never the first option"

    coas = coa_engine.get_coas_for_app("app-rich", "struts")
    rec = [c for c in coas if c["recommended"]]
    assert len(rec) == 1 and rec[0]["strategy"] == assessed and rec[0]["recommended_by"] == "seven_r_assessor"
    assert coas[0] is rec[0] and rec[0]["confidence"] == sr.strategy_for_app("app-rich", db_path=db)["confidence"]


def test_no_assessment_or_no_evidence_means_no_generator_recommends(db, tmp_path, monkeypatch):
    from tools.migration_canvas import coa_engine
    from tools.migration_intelligence import strategy_generator as sg

    path = _mi(tmp_path, monkeypatch, "app-thin")
    out = sg.generate_strategies_for_opportunity("opp-app-thin", db_path=path)
    assert out["seven_r"]["status"] == "not_assessed" and out["recommended"] is None
    assert not _sql(path, "SELECT 1 FROM mi_strategies WHERE recommended = 1")
    assert not any(c["recommended"] for c in coa_engine.get_coas("oracle")), "a technology alone recommends nothing"
    assert any(c.get("typical_for_technology") for c in coa_engine.get_coas("oracle"))

    _sr().run_seven_r_assessment("proj-1", "app-thin", db_path=db)
    again = sg.seven_r_for_opportunity({"source_canvas": "mdc", "source_entity_id": "app-thin"})
    assert again["status"] == "insufficient_evidence" and again["strategy"] is None
    assert not any(c["recommended"] for c in coa_engine.get_coas_for_app("app-thin", "oracle"))


def test_the_coa_chat_says_a_technology_alone_recommends_nothing():
    from tools.migration_canvas import migration_chat_advisor as advisor

    out = advisor.handle_coa("oracle")
    assert out["coa_cards"] and not any(c["recommended"] for c in out["coa_cards"])
    assert "None is recommended here" in out["reply"] and "*** RECOMMENDED ***" not in out["reply"]


# ── may-plan, enforced ───────────────────────────────────────────────────────


def _sign(doc: dict, key, kid: str) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from tools.modernization import icos_decisions as icd

    protected = icd._b64u(json.dumps({"alg": icd.ALG, "typ": icd.TYP, "kid": kid}).encode())
    der = key.sign(f"{protected}.{icd._b64u(icd.jcs(doc).encode())}".encode(), ec.ECDSA(hashes.SHA384()))
    r, s = decode_dss_signature(der)
    return {"document": doc, "signature": {"protected": protected,
                                           "signature": icd._b64u(r.to_bytes(48, "big") + s.to_bytes(48, "big"))}}


def _jwks(key, kid):
    nums = key.public_key().public_numbers()
    b = lambda n: base64.urlsafe_b64encode(n.to_bytes(48, "big")).rstrip(b"=").decode()  # noqa: E731
    return {"keys": [{"kty": "EC", "crv": "P-384", "kid": kid, "x": b(nums.x), "y": b(nums.y)}]}


@pytest.fixture()
def icos(tmp_path):
    """A pinned test key, and a signer of decisions exports accepting one strategy per application."""
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP384R1())
    jwks = tmp_path / "icos_jwks.json"
    jwks.write_text(json.dumps(_jwks(key, "k1")), encoding="utf-8")

    def export(accepted: dict, *, signer=None) -> Path:
        doc = {"schema": "icos-portfolio-decisions/v1", "issuer": "icos-test", "chain": {"verifies": True},
               "strategies": [{"application": a, "strategy": s, "at": 1, "item": f"i-{a}", "accepted_by": "mo",
                               "decision": f"d-{a}"} for a, s in accepted.items()]}
        p = tmp_path / f"decisions-{len(list(tmp_path.glob('decisions-*')))}.json"
        p.write_text(json.dumps(_sign(doc, signer or key, "k1")), encoding="utf-8")
        return p
    return {"jwks": jwks, "export": export}


def _plans(db):
    return (_sql(db, "SELECT COUNT(*) FROM migration_plans")[0][0], _sql(db, "SELECT COUNT(*) FROM migration_tasks")[0][0])


def _audit(db, prefix):
    return _sql(db, "SELECT id, event_type, actor, action, details FROM audit_trail WHERE action LIKE ? ORDER BY id",
                (f"{prefix}%",))


def test_may_plan_refused_or_missing_writes_no_plan(db, icos):
    """Planted: may-plan missing, untrusted, naming another application, or accepting another strategy -> no plan."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from tools.modernization import monolith_decomposer as md
    from tools.modernization.plan_gate import PlanRefused

    cases = [
        ({}, "missing"),
        ({"icos_decisions": str(icos["export"]({"app-rich": "rehost"})), "icos_jwks": None}, "missing"),
        ({"icos_decisions": str(icos["export"]({"app-rich": "rehost"}, signer=ec.generate_private_key(ec.SECP384R1()))),
          "icos_jwks": str(icos["jwks"])}, "untrusted"),
        ({"icos_decisions": str(icos["export"]({"app-other": "rehost"})), "icos_jwks": str(icos["jwks"])}, "refused"),
        ({"icos_decisions": str(icos["export"]({"app-rich": "replatform"})), "icos_jwks": str(icos["jwks"])}, "refused"),
    ]
    for kw, status in cases:
        with pytest.raises(PlanRefused) as exc:
            md.create_migration_plan("proj-1", "app-rich", "rehost", db_path=db, **kw)
        assert exc.value.answer["status"] == status and exc.value.answer["allowed"] is False
        assert _plans(db) == (0, 0), f"{status}: a plan was written without ICDEV[OS]'s may-plan"
    refused = _audit(db, "mdz.may_plan.refused:")
    assert len(refused) == len(cases) and all(r[1] == "approval_denied" for r in refused)
    assert "accepted replatform" in json.loads(refused[-1][4])["reason"]

    ok = md.create_migration_plan("proj-1", "app-rich", "rehost", db_path=db,
                                  icos_decisions=str(icos["export"]({"app-rich": "rehost"})),
                                  icos_jwks=str(icos["jwks"]))
    assert _plans(db)[0] == 1 and ok["may_plan"]["status"] == "allowed" and ok["may_plan"]["role"] == "strategy accepted"
    granted = _audit(db, "mdz.may_plan.allowed:")
    assert len(granted) == 1 and granted[0][1] == "approval_granted" and ok["may_plan"]["audit_id"] == granted[0][0]


def test_the_planner_cli_and_the_mcp_tool_say_refused(db, tmp_path):
    from tools.mcp import modernization_server as ms

    env = {"ICDEV_STORAGE_BACKEND": "sqlite", "ICDEV_DB_PATH": str(db), "PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    p = subprocess.run([sys.executable, str(ROOT / "tools/modernization/monolith_decomposer.py"), "--app-id", "app-rich",
                        "--create-plan", "--project-id", "proj-1", "--strategy", "rehost", "--json",
                        "--db-path", str(db)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 3, p.stdout + p.stderr
    said = json.loads(p.stdout.strip().splitlines()[-1])
    assert said["outcome"] == "refused" and said["status"] == "missing" and "[REFUSED]" in p.stderr
    out = ms.handle_create_migration_plan({"project_id": "proj-1", "app_id": "app-rich", "strategy": "rehost"})
    assert out["outcome"] == "refused" and out["allowed"] is False and "decisions export" in out["reason"]
    assert _plans(db) == (0, 0)


def test_refactor_jobs_write_nothing_without_may_plan(db, tmp_path, monkeypatch):
    """Planted: no may-plan -> no CAM project, inventory row or job, and the CLI writes no --out file."""
    from tools.modernization import refactor_jobs as rj
    from tools.modernization.plan_gate import PlanRefused

    mc = tmp_path / "mc.db"
    monkeypatch.setenv("MC_DB_PATH", str(mc))
    with pytest.raises(PlanRefused) as exc:
        rj.run("app-rich", strategy="rehost")
    assert exc.value.answer["status"] == "missing"
    assert not mc.exists() or not _sql(mc, "SELECT name FROM sqlite_master WHERE name = 'mc_projects'") or \
        _sql(mc, "SELECT COUNT(*) FROM mc_projects")[0][0] == 0
    out = tmp_path / "refactor.json"
    assert rj.main(["--app-id", "app-rich", "--strategy", "rehost", "--out", str(out)]) == 3 and not out.exists()
    assert {json.loads(r[4])["tool"] for r in _audit(db, "mdz.may_plan.refused:app-rich")} == {rj.TOOL}


# ── console, twin, MCP, bundle ───────────────────────────────────────────────


def _client(user=PM):
    from tools.dashboard.api.migration import migration_api

    app = Flask(__name__, template_folder=str(ROOT / "tools" / "dashboard" / "templates"))
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = dict(user) if user else None
        g.security_context = {"tenant_id": "default", "user_id": (user or {}).get("id", ""), "classification": "CUI"}

    app.register_blueprint(migration_api, name="migration_api_legacy")
    return app.test_client()


def test_the_7r_page_shows_confidence_unmeasured_and_the_refusal(db):
    from tools.modernization import monolith_decomposer as md
    from tools.modernization.plan_gate import PlanRefused

    sr = _sr()
    a = sr.run_seven_r_assessment("proj-1", "app-rich", db_path=db)
    sr.run_seven_r_assessment("proj-1", "app-thin", db_path=db)
    with pytest.raises(PlanRefused):
        md.create_migration_plan("proj-1", "app-rich", "rehost", db_path=db)
    body = _client().get("/api/migration/assessments").get_json()
    rows = {x["legacy_app_id"]: x for x in body["assessments"]}
    rich = rows["app-rich"]["seven_r"]
    assert rich["confidence"] == a["confidence"] and rich["strategies"]["rearchitect"]["assessable"] is False
    assert "licensing_transferability" in rich["strategies"]["rehost"]["unmeasured"]
    assert rows["app-thin"]["recommended_strategy"] is None
    assert rows["app-thin"]["seven_r"]["recommendation"]["status"] == "insufficient_evidence"
    assert body["plan_refusals_error"] is None
    assert [r["application"] for r in body["plan_refusals"]] == ["app-rich"]
    assert body["plan_refusals"][0]["status"] == "missing"

    # The person's record binds the confidence they saw.
    step = _client().post(f"/api/migration/assessments/{a['id']}/strategy",
                          json={"decision": "record", "strategy": a["recommended_strategy"], "reason": "on evidence"})
    assert step.status_code == 200, step.get_data(as_text=True)
    assert step.get_json()["strategy_record"]["confidence"] == a["confidence"]

    for rel in ("tools/dashboard/templates/migration.html",):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in ("renderEvidence(a)", "plan-refusals-panel", "renderRefusals(d.plan_refusals",
                       "not assessable", "none recommended"):
            assert needle in text, needle
        assert (ROOT / "icdev" / rel).read_text(encoding="utf-8") == text, f"{rel} is not mirrored"


def _twin_evidence(**over):
    base = {"canvas": "mdc", "target_id": "fleet", "observed_at": "2026-11-15T00:00:00+00:00",
            "policy": {"strategy_record_warn_days": 14, "job_review_warn_days": 7, "draft_review_warn_days": 7,
                       "seven_r_confidence_warn": 0.7},
            "assessments": [
                {"id": "ma-1", "app": "app-1", "recommended": "rehost", "at": "2026-11-14T00:00:00+00:00",
                 "confidence": 0.55, "min_confidence": 0.5, "not_assessable": ["rearchitect"], "method": "by name",
                 "reason": "rehost scores 0.80"},
                {"id": "ma-2", "app": "app-2", "recommended": "", "at": "2026-11-14T00:00:00+00:00",
                 "confidence": None, "min_confidence": 0.5, "not_assessable": ["rearchitect", "retain"],
                 "method": "by name", "reason": "no strategy reaches confidence 0.50"},
                {"id": "ma-3", "app": "app-3", "recommended": "rehost", "at": "2026-11-14T00:00:00+00:00",
                 "confidence": None, "min_confidence": None, "not_assessable": [],
                 "method": "positional (before icx-mdz-02)", "reason": None},
                {"id": "ma-4", "app": "app-4", "recommended": "refactor", "at": "2026-11-14T00:00:00+00:00",
                 "confidence": 0.9, "min_confidence": 0.5, "not_assessable": [], "method": "by name", "reason": ""}],
            "strategy_records": [], "plans": [{"id": "mp-9", "app": "app-9", "strategy": "rehost", "status": "draft",
                                               "at": "2026-11-15T00:00:00+00:00"}],
            "refusals": [{"id": 11, "application": "app-1", "tool": "icdev-it/monolith_decomposer", "status": "missing",
                          "reason": "no export", "strategy_asked": "rehost", "at": "2026-11-14T01:00:00+00:00"},
                         {"id": 12, "application": "app-9", "tool": "icdev-it/monolith_decomposer",
                          "status": "refused", "reason": "no", "strategy_asked": "rehost",
                          "at": "2026-11-14T01:00:00+00:00"}],
            "aiify_decisions": [], "aiify_steps": [], "drafts": [], "jobs": [], "job_reviews": [],
            "schema": {"path": "context/icos/apptwin.schema.json", "sha256": "a" * 64, "pinned": "a" * 64},
            "errors": {}}
    base.update(over)
    return base


def test_the_twin_finds_thin_evidence_and_plans_attempted_without_may_plan():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    twin = ModernisationGovernanceTwin()
    found = {(f["rule_id"], f["thread_id"]) for f in twin.judge(_twin_evidence())["findings"]
             if f["rule_id"].startswith(("mdz.seven_r.low", "mdz.seven_r.not", "mdz.seven_r.plan_refused"))}
    assert found == {
        ("mdz.seven_r.low_confidence", "mdz_assessment:ma-1"),       # recommended on thin evidence
        ("mdz.seven_r.not_assessable", "mdz_assessment:ma-2"),       # nothing recommended
        ("mdz.seven_r.low_confidence", "mdz_assessment:ma-3"),       # paired by position, before icx-mdz-02
        ("mdz.seven_r.plan_refused", "audit_trail:11"),              # app-9 was planned after its refusal
    }
    unread = twin.envelope(_twin_evidence(refusals=None, errors={"refusals": "audit_trail unreadable"}))
    assert unread["verdict"] != "pass" and all(v.get("thread_id") for v in unread["violations"])
    assert twin.judge(_twin_evidence()) == twin.judge(_twin_evidence())


def test_the_twin_observes_a_refusal_resolves_its_thread_and_replays(db):
    from tools.modernization import monolith_decomposer as md
    from tools.modernization.plan_gate import PlanRefused
    from tools.twin_core import governance

    _sr().run_seven_r_assessment("proj-1", "app-thin", db_path=db)
    with pytest.raises(PlanRefused) as exc:
        md.create_migration_plan("proj-1", "app-rich", "rehost", db_path=db)
    audit_id = exc.value.answer["audit_id"]
    env = governance.observe(governance.adapter_for("mdc"))
    threads = {v["thread_id"] for v in env["violations"]}
    assert f"audit_trail:{audit_id}" in threads
    assert any(t.startswith("mdz_assessment:") for t in threads), "the not-assessable application is a finding"
    replayed = governance.replay(governance.adapter_for("mdc"), env["extra"]["observation_id"])
    assert replayed["match"] is True, replayed["reason"]
    thread = governance.resolve_thread(f"audit_trail:{audit_id}")
    assert thread["resolved"] is True and thread["record"]["action"] == "mdz.may_plan.refused:app-rich"


def test_the_mcp_read_tool_carries_confidence_unmeasured_and_refusals(db):
    from tools.mcp import gap_handlers
    from tools.modernization import monolith_decomposer as md
    from tools.modernization.plan_gate import PlanRefused

    _sr().run_seven_r_assessment("proj-1", "app-rich", db_path=db)
    with pytest.raises(PlanRefused):
        md.create_migration_plan("proj-1", "app-rich", "rehost", db_path=db)
    out = gap_handlers.handle_modernisation_seven_r({"app_id": "app-rich"})
    [a] = out["assessments"]
    assert a["confidence"] is not None and a["not_assessable"] == ["rearchitect"]
    assert "operational_readiness" in a["strategies"]["rehost"]["unmeasured"]
    assert out["plan_refusals"][0]["application"] == "app-rich" and out["plan_refusals_error"] is None


def test_the_bundle_ranks_only_assessable_strategies(db):
    from tools.modernization import apptwin_export as ax

    a = _sr().run_seven_r_assessment("proj-1", "app-rich", db_path=db)
    section = ax.seven_r_from_assessment(a, at="2026-11-15T00:00:00Z")
    assert "rearchitect" not in {r["strategy"] for r in section["ranked"]}
    assert section["recommended"] == a["recommended_strategy"]
    assert "rearchitect: not assessable (nothing measured)" in section["evidence"]
    assert any(e.startswith("rehost: confidence ") for e in section["evidence"])
    thin = _sr().run_seven_r_assessment("proj-1", "app-thin", db_path=db)
    assert ax.seven_r_from_assessment(thin, at="2026-11-15T00:00:00Z") is None, "nothing ranked: unverified"
