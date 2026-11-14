"""ICDEV[OS] Phase 254: the modernisation tooling over real applications, and the fixes running it found.

The applications themselves (DayTrader 7, the AWS legacy Cycle Store, JPetStore 6) are never vendored here; these
tests use small trees written in the shape those apps take, so each fix is pinned to what broke:

* ``@WebServlet(name=..., urlPatterns={...})`` endpoints were missed (only ``@WebServlet("/x")`` matched);
* a component ``INSERT OR IGNORE`` dropped (the same class in two modules) left a dangling id: the API and
  dependency inserts after it failed the foreign key and the whole analysis aborted;
* no language level was recorded, so the 7R never saw an upgrade path;
* no dependency ever named its target, so the bundle carried none;
* ASP.NET MVC on the .NET Framework was undetected, and its conventional ``{controller}/{action}`` routes unseen;
* a T-SQL script's ``[Schema].[Table]`` names collapsed into the schema, and its ``GO`` batches into one statement;
* AI-Ify's verdict sentence was exported where the contract wants its enum; module paths leaked the analyst's
  checkout; disqualifiers were dropped;
* most modernisation tools ignored ``ICDEV_DB_PATH`` and wrote ``data/icdev.db``;
* (osx-fth-12) features were never reported: they are now, statically, from the entry points.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.modernization import apptwin_export as ax
from tools.modernization import legacy_analyzer as la


def test_webservlet_url_patterns_in_every_form():
    assert la._webservlet_paths('@WebServlet(name = "TradeAppServlet", urlPatterns = { "/app" })') == ["/app"]
    assert la._webservlet_paths('@WebServlet("/x")') == ["/x"]
    assert la._webservlet_paths('@WebServlet(value = {"/a", "/b"}, name = "n")') == ["/a", "/b"]
    assert la._webservlet_paths('@WebServlet(name = "only-a-name")') == []
    assert la._webservlet_paths("class A {}") == []


def test_mvc_actions_follow_the_conventional_route():
    src = ("public ActionResult Index() {} public async Task<ActionResult> Detail(int id) {} "
           "private ActionResult Hidden() {} public string NotAnAction() {}")
    assert la._mvc_actions("HomeController", "Controller", src) == ["Detail", "Index"]
    assert la._mvc_actions("Home", "Controller", src) == []
    assert la._mvc_actions("HomeController", "ApiBase", src) == []


def test_language_level_from_the_build(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/pom.xml").write_text("<maven.compiler.source>1.8</maven.compiler.source>")
    (tmp_path / "pom.xml").write_text("<properties><java.version>11</java.version></properties>")
    assert la._detect_language_version(tmp_path, "java") == "8", "the lowest module holds the upgrade back"
    cs = tmp_path / "cs"
    cs.mkdir()
    (cs / "Web.csproj").write_text("<TargetFrameworkVersion>v4.5</TargetFrameworkVersion>")
    assert la._detect_language_version(cs, "csharp") == "framework-4"
    assert la._detect_language_version(tmp_path / "cs", "python") is None


def _java_app(root: Path) -> Path:
    for mod in ("ejb", "web"):
        d = root / mod / "src/main/java/com/acme"
        d.mkdir(parents=True)
        # the same class in two modules: the second insert is ignored, and nothing may point at its fresh id
        (d / "Util.java").write_text("package com.acme;\npublic class Util {}\n")
    web = root / "web/src/main/java/com/acme"
    (web / "TradeServlet.java").write_text(
        "package com.acme;\nimport com.acme.Util;\nimport javax.servlet.http.HttpServlet;\n"
        '@WebServlet(name = "Trade", urlPatterns = { "/app", "/trade" })\n'
        "public class TradeServlet extends HttpServlet { Util u; }\n")
    (root / "pom.xml").write_text("<project><properties><maven.compiler.source>1.8</maven.compiler.source>"
                                  "</properties></project>")
    (root / "schema.sql").write_text(
        "CREATE TABLE [Trade].[Account](\n  [id] int NOT NULL,\n  [owner] nvarchar(50)\n)\nGO\n"
        "CREATE TABLE [Trade].[Holding](\n  [id] int NOT NULL,\n  [account_id] int\n)\nGO\n")
    (root / "README.md").write_text("A trading sample.\n")
    return root


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    base = tmp_path_factory.mktemp("modsamples")
    src = _java_app(base / "src")
    out = base / "out"
    p = subprocess.run([sys.executable, str(ROOT / "tools/modernization/sample_pipeline.py"), "--source", str(src),
                        "--name", "acme-trade", "--scratch-db", str(base / "db/icdev.db"), "--out", str(out),
                        "--at", "2026-11-12T00:00:00Z", "--no-model"],
                       cwd=ROOT, capture_output=True, text=True, timeout=900, check=False)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    return {"base": base, "out": out, "report": json.loads((out / "gaps.json").read_text()),
            "bundle": json.loads((out / "acme-trade.apptwin.json").read_text())}


def test_the_pipeline_exports_a_valid_bundle_from_a_scratch_database(run):
    assert ax.validate(run["bundle"]) == []
    with sqlite3.connect(run["base"] / "db/icdev.db") as c:
        assert c.execute("SELECT COUNT(*) FROM legacy_applications").fetchone()[0] == 1
    steps = {s["step"]: s for s in run["report"]["steps"]}
    assert steps["analyze"]["language_version"] == "8" and steps["analyze"]["legacy_apis"] == 2


def test_servlets_tables_and_dependencies_reach_the_bundle(run):
    b = run["bundle"]
    assert {a["path"] for a in b["apis"]} >= {"/app", "/trade"}
    assert {t["name"] if isinstance(t, dict) else t for t in b["data"]} >= {"account", "holding"}, \
        "the table, not its [Trade] schema"
    assert any(d["target"] == "com.acme.Util" for d in b["dependencies"] or []), "an import resolved to its component"


def test_every_section_not_produced_is_a_gap_with_its_reason(run):
    gaps, b = run["report"]["gaps"], run["bundle"]
    for k, v in b.items():
        if v in (None, [], {}) and k != "schema":
            assert gaps.get(k), f"{k} is empty and the report does not say why"
    assert "--no-model" in gaps["processes"]
    assert "features" not in gaps, "reported statically, from the entry points (osx-fth-12)"


def test_features_are_reported_statically_never_observed(run):
    """osx-fth-12: the source is never run, so features come from its entry points, labelled static."""
    [f] = run["bundle"]["features"]
    assert f["id"] == "trade" and f["provenance"] == "static" and f["basis"] == "source"
    assert f["signatures"]["apis"] == ["ALL /app", "ALL /trade"] and 0 < f["confidence"] <= 1
    step = next(s for s in run["report"]["steps"] if s["step"] == "features")
    assert step["ok"] and step["provenance"] == "static" and step["features"] == 1


def test_the_scratch_database_must_be_new(tmp_path):
    db = tmp_path / "icdev.db"
    db.write_bytes(b"")
    p = subprocess.run([sys.executable, str(ROOT / "tools/modernization/sample_pipeline.py"), "--source", str(tmp_path),
                        "--name", "x", "--scratch-db", str(db), "--out", str(tmp_path / "o")],
                       cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode == 1 and "must be a new file" in p.stdout


def test_aiify_export_carries_the_enum_relative_paths_and_disqualifiers(tmp_path):
    scan = {"overall_verdict": "Partially AI-ready", "overall_ai_readiness": "partial", "opportunities": [
        {"opportunity_id": 1, "pattern_type": "nested_conditionals", "module_path": str(tmp_path / "src/A.java"),
         "score": {"ai_readiness": "candidate", "composite_score": 0.6}, "disqualifiers": '["safety-critical"]'}]}
    sec = ax.aiify_from_scan(scan, at="2026-11-12T00:00:00Z", root=str(tmp_path))
    o = sec["opportunities"][0]
    assert sec["overall_verdict"] == "partial" and o["module_path"] == "src/A.java"
    assert o["disqualifiers"] == ["safety-critical"]
    with pytest.raises(ax.BundleError):
        ax.aiify_from_scan({"overall_verdict": "Sort of ready"}, at="2026-11-12T00:00:00Z")


def test_every_timestamp_in_the_bundle_parses(run):
    """monolith_decomposer wrote "...+00:00Z" (an offset AND a Z); ICDEV[OS] refused the whole bundle."""
    from datetime import datetime

    def walk(v, path=""):
        if isinstance(v, dict):
            for k, x in v.items():
                yield from walk(x, f"{path}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                yield from walk(x, f"{path}[{i}]")
        elif isinstance(v, str) and path.endswith(("_at", ".at")):
            yield path, v

    stamps = list(walk(run["bundle"]))
    assert stamps
    for path, v in stamps:
        datetime.fromisoformat(v.replace("Z", "+00:00")), path
    assert ax._iso("2026-11-12T04:38:13.113247+00:00Z") == "2026-11-12T04:38:13.113247+00:00"
    assert "/" not in json.dumps(run["report"]["source"])
