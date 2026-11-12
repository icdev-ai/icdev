# CUI // SP-CTI
"""icx-bun-01 -- one legacy application exported as an ICDEV[OS] icos-apptwin/v1 bundle.

* the inventory comes from the legacy_* tables, in a stable order, and the bundle validates against the vendored
  contract (pinned by digest: a changed schema file is refused);
* every other section is carried from its own tool's JSON, and a section with no input stays null (ICDEV[OS]
  reads null as unverified, never as a pass); nothing is invented (no feature guessed, no posture grade that is
  database-wide, no consumers);
* the same inputs and ``--at`` give the same bytes.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from tools.modernization import apptwin_export as ex

AT = "2026-11-07T00:00:00Z"


_TABLES = ("projects", "legacy_applications", "legacy_components", "legacy_dependencies", "legacy_apis", "legacy_db_schemas")


def _canonical_ddl() -> list[str]:
    """The legacy_* definitions of record, read from tools/db/init_icdev_db.py (never a hand-written copy)."""
    import re
    from pathlib import Path
    text = (Path(ex._BASE) / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    out = []
    for t in _TABLES:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);", text, re.S)
        assert m, f"no canonical DDL for {t}"
        out.append(m.group(0))
    return out


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    from tools.db.storage import get_connection
    c = get_connection()
    for ddl in _canonical_ddl():
        c.execute(ddl)
    rows = [
        ("INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
         ("proj-1", "depot", "webapp", "/src/depot")),
        ("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language, framework) "
         "VALUES (%s, %s, %s, %s, %s, %s)", ("app-a", "proj-1", "Depot stock", "/src/depot", "java", "spring")),
        ("INSERT INTO legacy_components (id, legacy_app_id, name, qualified_name, component_type, file_path) "
         "VALUES (%s, %s, %s, %s, %s, %s)", ("c2", "app-a", "StockRepo", "depot.StockRepo", "repository", "src/StockRepo.java")),
        ("INSERT INTO legacy_components (id, legacy_app_id, name, qualified_name, component_type, file_path) "
         "VALUES (%s, %s, %s, %s, %s, %s)", ("c1", "app-a", "StockController", "depot.StockController", "controller",
                                             "src/StockController.java")),
        ("INSERT INTO legacy_apis (id, legacy_app_id, method, path, handler_function) VALUES (%s, %s, %s, %s, %s)",
         ("a1", "app-a", "POST", "/stock/receive", "receive")),
        ("INSERT INTO legacy_apis (id, legacy_app_id, method, path, handler_function) VALUES (%s, %s, %s, %s, %s)",
         ("a2", "app-a", "GET", "/stock", None)),
    ]
    for i, (tbl, col) in enumerate((("stock", "qty"), ("stock", "depot"), ("depot", "id"))):
        rows.append(("INSERT INTO legacy_db_schemas (id, legacy_app_id, db_type, table_name, column_name, data_type) "
                     "VALUES (%s, %s, %s, %s, %s, %s)", (f"s{i}", "app-a", "postgresql", tbl, col, "text")))
    rows += [("INSERT INTO legacy_dependencies (legacy_app_id, source_component_id, target_component_id, dependency_type) "
              "VALUES (%s, %s, %s, %s)", ("app-a", "c1", "c2", "injection")),
             ("INSERT INTO legacy_dependencies (legacy_app_id, source_component_id, target_component_id, dependency_type) "
              "VALUES (%s, %s, %s, %s)", ("app-a", "c1", None, "external_service"))]
    for sql, params in rows:
        c.execute(sql, params)
    c.commit()
    yield c
    c.close()


SCAN = {"scan_id": 7, "status": "completed", "overall_verdict": "partial", "overall_readiness_score": 0.42,
        "pillar_scores": {"audit": {"passed": 1, "total": 4, "percentage": 0.25}},
        "opportunities": [{"module_path": "src/Rules.java", "pattern_type": "hardcoded_threshold",
                           "score": {"ai_readiness": "ai_ready", "composite_score": 0.8}, "disqualifiers": []},
                          {"module_path": "src/X.java", "pattern_type": "nested_conditionals", "score": None}]}
PROC = {"status": "ok", "workflow": {"workflow_name": "Receive stock", "basis": "document", "label": "modelled",
                                     "steps": [{"id": "step-1", "title": "Inspect", "assignee_role": "Clerk"},
                                               {"title": "Approve"}]}}
OBSERVED_NONE = {"status": "ok", "workflow": {"workflow_name": "Observed", "basis": "observed", "steps": None}}


def test_inventory_only_gives_a_valid_bundle_with_every_other_section_unverified(conn):
    b = ex.build("app-a", conn, at=AT)
    assert b["schema"] == "icos-apptwin/v1" and b["application"] == {"id": "app-a", "title": "Depot stock", "languages": ["java"]}
    assert [c["id"] for c in b["components"]] == ["depot.StockController", "depot.StockRepo"]
    assert b["apis"] == [{"method": "GET", "path": "/stock"}, {"method": "POST", "path": "/stock/receive", "handler": "receive"}]
    assert b["data"] == [{"name": "depot", "columns": ["id"]}, {"name": "stock", "columns": ["depot", "qty"]}]
    assert b["dependencies"] == [{"source": "depot.StockController", "target": "depot.StockRepo", "kind": "injection"}]
    unverified = {"features", "processes", "consumers", "aiify", "integrity", "seven_r", "ivv", "sbd", "rtm",
                  "coherence", "readiness", "migration", "refactor"}
    assert {k for k, v in b.items() if v is None} == unverified
    assert ex.validate(b) == []


def test_each_section_is_carried_from_its_own_tool_and_nothing_is_invented(conn):
    inv = {"features": [{"label": "Receive stock", "evidence_ids": ["ev-2", "ev-1"]}, {"label": "Receive stock"}]}
    b = ex.build("app-a", conn, at=AT, features=inv, processes=[PROC, OBSERVED_NONE], aiify=SCAN,
                 integrity={"verdict": "review", "risk_score": 3, "capabilities": [{"capability_type": "network_egress"}]},
                 seven_r={"evidence": {"ranking": [{"strategy_id": "refactor", "score": 0.7}, {"strategy_id": "retire", "score": 0.2}]}})
    assert b["features"] == [{"id": "receive-stock", "title": "Receive stock", "basis": "observed", "evidence": ["ev-1", "ev-2"]}]
    assert b["processes"] == [{"id": "receive-stock", "title": "Receive stock", "basis": "document",
                               "steps": [{"id": "step-1", "title": "Inspect", "role": "Clerk"}, {"id": "step-2", "title": "Approve"}]}]
    a = b["aiify"]
    assert a["posture_grade"] is None, "AI-Ify's posture grade is database-wide, never one application's"
    assert a["pillars"] == {"audit": {"passed": 1, "total": 4, "percentage": 25.0}} and a["readiness_score"] == 0.42
    assert [o["module_path"] for o in a["opportunities"]] == ["src/Rules.java"], "an unscored opportunity is not carried"
    assert b["integrity"]["verdict"] == "REVIEW" and b["integrity"]["capabilities"] == ["network_egress"]
    assert b["seven_r"]["ranked"][0] == {"strategy": "refactor", "score": 0.7}
    assert b["consumers"] is None and ex.validate(b) == []


def test_a_draft_with_no_measured_steps_leaves_processes_unverified(conn):
    assert ex.build("app-a", conn, at=AT, processes=[OBSERVED_NONE])["processes"] is None


def test_a_bundle_that_breaks_the_contract_is_refused(conn):
    with pytest.raises(ex.BundleError, match="contract"):
        ex.build("app-a", conn, at=AT, sections={"ivv": {"tool": "x", "recommendation": "MAYBE"}})
    with pytest.raises(ex.BundleError, match="no legacy application"):
        ex.build("nope", conn, at=AT)


def test_the_vendored_contract_is_pinned(monkeypatch, tmp_path):
    assert hashlib.sha256(ex.SCHEMA_PATH.read_bytes()).hexdigest() == ex.SCHEMA_SHA256
    changed = tmp_path / "apptwin.schema.json"
    changed.write_text(ex.SCHEMA_PATH.read_text(encoding="utf-8").replace("icos-apptwin/v1", "icos-apptwin/v9"), encoding="utf-8")
    monkeypatch.setattr(ex, "SCHEMA_PATH", changed)
    with pytest.raises(ex.BundleError, match="not the pinned"):
        ex.schema()


def test_the_same_inputs_give_the_same_bytes(conn, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tools.db.storage.get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(conn, "close", lambda: None)
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps(SCAN), encoding="utf-8")
    outs = []
    for i in range(2):
        out = tmp_path / f"b{i}.json"
        assert ex.main(["--app-id", "app-a", "--aiify", str(scan), "--at", AT, "--out", str(out)]) == 0
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    said = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert said["status"] == "ok" and "integrity" in said["unverified"] and "aiify" not in said["unverified"]


def test_the_7r_detail_waves_and_refactor_jobs_are_carried_as_given(conn):
    """ICDEV[OS] Phase 247: the 7R estimates, the canvas's waves and CAM's jobs; an artefact with no file has no digest."""
    seven = {"created_at": "2026-11-07T00:00:00Z", "recommended_strategy": "refactor", "cost_estimate_hours": 1840,
             "risk_score": 0.42, "timeline_weeks": 26, "ato_impact": "medium", "tech_debt_reduction": 0.55,
             "scoring_weights": json.dumps({"technical": 0.3, "cost": 0.2}),
             "evidence": json.dumps({"ranking": [{"strategy_id": "refactor", "score": 0.71}, {"strategy_id": "retain", "score": 0.55}],
                                     "rationale": ["two bounded contexts"]})}
    b = ex.build("app-a", conn, at=AT, seven_r=seven, refactor=[{"id": "j1", "refactor_type": "code_scaffold",
                                                                  "component_name": "StockRepo", "status": "completed",
                                                                  "artifacts_json": json.dumps(["data/no-such-file"])}])
    s = b["seven_r"]
    assert (s["recommended"], s["cost_estimate_hours"], s["ato_impact"], s["evidence"]) == ("refactor", 1840.0, "medium", ["two bounded contexts"])
    assert b["refactor"]["jobs"] == [{"id": "j1", "kind": "code_scaffold", "component": "StockRepo", "status": "completed"}]
    assert b["migration"] is None, "no plan recorded: unverified, never an empty plan"
    assert ex.validate(b) == []
