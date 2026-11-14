# CUI // SP-CTI
"""ICDEV platform model, traced into the MBSE digital thread (icx-cnf-04).

What is held:
  * the shipped args/platform_model validates against the repository: every
    cited test exists by file AND function, every code path or symbol exists, no
    element is an orphan, every feature key is real;
  * a test that does not exist is an ERROR, never a trace, and seeding refuses it;
  * a requirement with no test is a GAP, never a pass, in the model and the thread;
  * seeded into a scratch database, find_orphans and find_gaps name nothing for a
    fully traced feature, and name the untested code of a feature that has a gap;
  * the MCP trace_forward / trace_backward tools resolve the seeded rows;
  * /api/lineage/platform-model and the /lineage page show the trace, and
    platform_model_trace serves it read-only.

Every database here is a throwaway SQLite file under tmp_path.
"""
from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest
import yaml

pm = importlib.import_module("tools.mbse.platform_model")

REPO = Path(__file__).resolve().parent.parent


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def mini(tmp_path):
    """A tiny repository with one traced requirement and one code file."""
    root = tmp_path
    _write(root, "tools/mbse/digital_thread.py",
           'VALID_TYPES = ("doors_requirement", "sysml_element", "code_module", "test_file")\n')
    _write(root, "tools/demo/core.py", "def run():\n    return 1\n\n\nclass Box:\n    def open(self):\n        pass\n")
    _write(root, "tests/test_demo.py", textwrap.dedent('''\
        def test_runs():
            assert True


        class TestBox:
            def test_opens(self):
                assert True
        '''))
    return root


def _model(root: Path, tests: list, code: list) -> None:
    _write(root, "args/platform_model/demo.yaml", yaml.safe_dump({
        "component": "demo",
        "elements": [{"id": "demo.core", "type": "sysml_element", "kind": "block",
                      "features": ["demo.feature"], "code": code}],
        "requirements": [{"id": "REQ-1", "features": ["demo.feature"], "elements": ["demo.core"],
                          "controls": ["SA-11"], "tests": tests}],
    }))


def _seeded(tmp_path, monkeypatch) -> str:
    """The shipped model seeded into a fresh, full-schema scratch database."""
    from tools.db.init_icdev_db import init_db

    db = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    init_db(str(db))
    result = pm.seed(db_path=str(db))
    assert result["seeded"], result
    return str(db)


# ── the model, no database ───────────────────────────────────────────────────


def test_the_shipped_model_validates():
    report = pm.validate(REPO)
    assert report["ok"], report["errors"]
    keys = pm._feature_keys(REPO)
    assert sorted(report["features"]) == sorted(keys)
    for key, row in report["features"].items():
        assert row["requirements"], f"{key} has no requirement"
        assert row["status"] in ("traced", "gap")
    declared_untested = sorted(
        str(r["id"]) for r in pm.load_model(REPO)["requirements"] if not r.get("tests"))
    assert sorted(g["requirement"] for g in report["gaps"]) == declared_untested


def test_an_invented_test_name_is_refused(mini):
    _model(mini, ["tests/test_demo.py::test_runs", "tests/test_demo.py::TestBox::test_opens"],
           ["tools/demo/core.py::run", "tools/demo/core.py::Box.open"])
    assert pm.validate(mini, feature_keys=["demo.feature"])["errors"] == []

    _model(mini, ["tests/test_demo.py::test_made_up"], ["tools/demo/core.py"])
    errors = pm.validate(mini, feature_keys=["demo.feature"])["errors"]
    assert errors == ["requirement REQ-1: test tests/test_demo.py::test_made_up: "
                      "tests/test_demo.py has no test function 'test_made_up'"]

    _model(mini, ["tests/test_demo.py::TestBox::test_closes"], ["tools/demo/core.py::Box.shut"])
    errors = pm.validate(mini, feature_keys=["demo.feature"])["errors"]
    assert any("Box has no method 'shut'" in e for e in errors)
    assert any("no test TestBox::test_closes" in e for e in errors)

    _model(mini, ["tests/test_gone.py"], ["tools/demo/core.py"])
    assert pm.validate(mini, feature_keys=["demo.feature"])["errors"] == [
        "requirement REQ-1: test tests/test_gone.py: no test file tests/test_gone.py"]
    # A model that cites a missing test is never written into the thread.
    refused = pm.seed(db_path=str(mini / "never.db"), root=mini)
    assert refused["seeded"] is False and refused["errors"]
    assert not (mini / "never.db").exists()


def test_an_untested_requirement_is_a_gap_not_a_pass(mini):
    _model(mini, [], ["tools/demo/core.py"])
    report = pm.validate(mini, feature_keys=["demo.feature"])
    assert report["errors"] == []
    assert report["gaps"] == [{"requirement": "REQ-1", "features": "demo.feature",
                               "detail": "REQ-1 traces to no test"}]
    row = report["features"]["demo.feature"]
    assert row["status"] == "gap" and row["traced"] == [] and row["untested"] == ["REQ-1"]
    # An element no requirement names is an orphan: a structural error.
    _write(mini, "args/platform_model/extra.yaml", yaml.safe_dump({"elements": [
        {"id": "demo.loose", "type": "sysml_element", "features": ["demo.feature"],
         "code": ["tools/demo/core.py"]}]}))
    assert "element demo.loose: orphan, no requirement of demo.feature names it" in \
        pm.validate(mini, feature_keys=["demo.feature"])["errors"]


# ── the digital thread (scratch database) ────────────────────────────────────


def test_seeded_thread_has_no_orphans_or_gaps_for_traced_features(tmp_path, monkeypatch):
    db = _seeded(tmp_path, monkeypatch)
    model_view = pm.validate(REPO)["features"]
    thread = pm.trace(db_path=db)
    assert thread["seeded"] and thread["current"]
    for key, row in model_view.items():
        got = thread["features"][key]
        if row["status"] == "traced":
            assert got["status"] == "traced", key
            assert got["orphans"] == [] and got["gaps"] == [] and got["untested"] == [], key
        else:
            assert got["status"] == "gap", key
            assert got["untested"] == row["untested"], key

    # The untested BOM route is code with no test in the existing orphan analysis.
    from tools.mbse.digital_thread import find_gaps, find_orphans

    orphan_code = {o["id"] for o in find_orphans(pm.PROJECT_ID, db_path=db)["code_without_tests"]["items"]}
    assert "tools/cortex/rest_v1.py::api_v1_bom" in orphan_code
    assert "tools/cortex/rest_v1.py::api_v1_award" not in orphan_code
    assert {g["gap_type"] for g in find_gaps(pm.PROJECT_ID, db_path=db)["gaps"]} <= {"code_without_test"}

    # A requirement row says whether a test verifies it; a re-seed replaces, never duplicates.
    from tools.db.storage import get_connection

    conn = get_connection(db_path=db)
    status = dict(conn.execute(
        "SELECT id, status FROM doors_requirements WHERE project_id = %s", (pm.PROJECT_ID,)).fetchall())
    before = conn.execute("SELECT COUNT(*) FROM digital_thread_links WHERE project_id = %s",
                          (pm.PROJECT_ID,)).fetchone()[0]
    conn.close()
    assert status["PM-CTX-17"] == "active" and status["PM-CTX-01"] == "verified"
    pm.seed(db_path=db)
    conn = get_connection(db_path=db)
    after = conn.execute("SELECT COUNT(*) FROM digital_thread_links WHERE project_id = %s",
                         (pm.PROJECT_ID,)).fetchone()[0]
    conn.close()
    assert after == before == len(pm.thread_links(pm.load_model(REPO)))


def test_mcp_trace_forward_and_backward_resolve_a_requirement(tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    from tools.mcp.mbse_server import handle_trace_backward, handle_trace_forward

    fwd = handle_trace_forward({"project_id": pm.PROJECT_ID, "source_type": "doors_requirement",
                                "source_id": "PM-CTX-01"})
    assert "error" not in fwd, fwd
    assert fwd["source"]["name"] == "Search returns normalized rows through the REST surface"
    elements = {n["target"]["id"]: n for n in fwd["links"] if n["target"]["type"] == "sysml_element"}
    assert set(elements) == {"cortex.search_ask", "if.cortex.rest_v1"}
    code = {c["target"]["id"]: c for c in elements["cortex.search_ask"]["children"]}
    assert "tools/cortex/search_service.py::search" in code
    tests = {t["target"]["id"] for t in code["tools/cortex/search_service.py::search"]["children"]
             if t["target"]["type"] == "test_file"}
    assert "tests/cortex/test_rest_api.py::test_search_returns_normalized_rows" in tests

    test_id = "tests/cortex/test_rest_api.py::test_search_returns_normalized_rows"
    back = handle_trace_backward({"project_id": pm.PROJECT_ID, "target_type": "test_file",
                                  "target_id": test_id})
    assert "error" not in back, back

    def walk(nodes):
        for n in nodes:
            yield n["source"]
            yield from walk(n.get("children") or [])

    sources = {(s["type"], s["id"]) for s in walk(back["links"])}
    assert ("code_module", "tools/cortex/search_service.py::search") in sources
    assert ("doors_requirement", "PM-CTX-01") in sources


# ── the console and the MCP read ─────────────────────────────────────────────


def test_lineage_route_and_page_show_the_trace(tmp_path, monkeypatch):
    from flask import Flask

    db = _seeded(tmp_path, monkeypatch)
    lineage = importlib.import_module("tools.dashboard.api.lineage")
    monkeypatch.setattr(lineage, "DB_PATH", Path(db))
    app = Flask(__name__)
    app.register_blueprint(lineage.lineage_api)
    client = app.test_client()

    body = client.get("/api/lineage/platform-model").get_json()
    assert body["project_id"] == "icdev-platform"
    assert body["model"]["ok"] and body["thread"]["seeded"]
    assert body["model"]["features"]["cortex.pmo"]["untested"] == ["PM-CTX-17"]
    assert body["thread"]["features"]["cortex.search_ask"]["status"] == "traced"

    one = client.get("/api/lineage/platform-model?feature=cwk.canvas").get_json()
    assert list(one["model"]["features"]) == ["cwk.canvas"]
    assert [g["requirement"] for g in one["model"]["gaps"]] == ["PM-CWK-02"]
    assert client.get("/api/lineage/platform-model?feature=nope").status_code == 404

    # Unseeded, the route says so rather than reading as clean.
    monkeypatch.setattr(lineage, "DB_PATH", tmp_path / "empty.db")
    from tools.db.init_icdev_db import init_db

    init_db(str(tmp_path / "empty.db"))
    empty = client.get("/api/lineage/platform-model").get_json()
    assert empty["thread"]["seeded"] is False and "--seed" in empty["thread"]["reason"]

    page = (REPO / "tools/dashboard/templates/lineage.html").read_text(encoding="utf-8")
    assert "loadPlatformModel()" in page and "/api/lineage/platform-model" in page
    assert "never a pass" in page


def test_mcp_platform_model_trace_is_read_only(tmp_path, monkeypatch):
    from tools.mcp.gap_handlers import handle_platform_model_trace
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert "platform_model_trace" in list_tools()
    assert READ_ONLY_DECLARATIONS["platform_model_trace"] is True
    assert tool_authorization("platform_model_trace")["tier"] == "read"

    out = handle_platform_model_trace({"feature": "ace.abort"})
    assert out["ok"] and out["features"]["ace.abort"]["status"] == "gap"
    assert [g["requirement"] for g in out["gaps"]] == ["PM-ACE-07"]
    assert "error" in handle_platform_model_trace({"feature": "nope"})

    _seeded(tmp_path, monkeypatch)
    seeded = handle_platform_model_trace({"seeded": True, "feature": "cortex.govern"})
    assert seeded["seeded"] and seeded["features"]["cortex.govern"]["status"] == "traced"


def test_coherence_fails_on_an_invented_test_and_warns_on_a_gap(mini, monkeypatch):
    """The RTM gate for these features: a lie fails, a gap warns by name."""
    cc = importlib.import_module("tools.workflow.coherence_checker")
    monkeypatch.setattr(cc, "PROJECT_ROOT", mini)
    _write(mini, "args/feature_conformance.yaml", yaml.safe_dump({"features": [{"key": "demo.feature"}]}))

    _model(mini, ["tests/test_demo.py::test_runs"], ["tools/demo/core.py"])
    assert cc.check_platform_model_trace().status == "pass"

    _model(mini, [], ["tools/demo/core.py"])
    gap = cc.check_platform_model_trace()
    assert gap.status == "warn" and gap.missing == ["REQ-1 (demo.feature): no test"]

    _model(mini, ["tests/test_demo.py::test_made_up"], ["tools/demo/core.py"])
    lie = cc.check_platform_model_trace()
    assert lie.status == "fail" and "test_made_up" in lie.missing[0]
    assert "platform_model_trace" in cc.CHECK_REGISTRY
