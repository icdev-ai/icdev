# CUI // SP-CTI
"""Compiled artifacts -> source_extractor IR, basis decompiled (rea-xlate-01, card ``rea``).

What is pinned here, per compiled fixture:

* a .NET DLL (``Fixture.dll``), a stripped C binary (``fixture.exe``) and a JS
  bundle (``tests/fixtures/rea/xlate/js_bundle``) each yield IR units carrying
  ``basis: decompiled``, a confidence and the REA ``evidence_id`` they came from;
* ``code_translator.translate_units`` / the ``translate_unit`` CLI accept those
  units and keep the basis on every entry they emit;
* ``legacy_analyzer.register_binary_application`` writes component and
  ``method_call`` rows that ``architecture_extractor.extract_call_graph`` reads,
  and the basis is still there at the end;
* the rails: no authorization basis -> nothing normalized; a JS target whose
  bytes changed is refused; anything not measured is ``None``, never ``[]``.

CI needs neither Node nor Ghidra: REA's answers are replayed by the
rea-wire-01 stub server from ``tests/fixtures/rea/`` (recorded, except the two
call-graph answers the fixture marks ``recorded: false``).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

from icdev.tools.analyzers import rea_adapter as ra
from icdev.tools.mcp_client import registry as reg
from icdev.tools.mcp_client import target_auth
from icdev.tools.translation import code_translator as ct
from icdev.tools.translation import decompiled_extractor as dx

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "rea"
WIRE = json.loads((FIXTURES / "rea_6.4.0_adapter_responses.json").read_text(encoding="utf-8"))
XLATE = json.loads((FIXTURES / "rea_6.4.0_xlate_responses.json").read_text(encoding="utf-8"))
RECORDED = json.loads((FIXTURES / "rea_6.4.0_recorded.json").read_text(encoding="utf-8"))
JS_BUNDLE = FIXTURES / "xlate" / "js_bundle" / "shipped"


def _wire(key: str) -> dict:
    return WIRE["responses"][key]["result"]


def _xlate(key: str) -> dict:
    return XLATE["responses"][key]["result"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reg.reset_external_registry()
    monkeypatch.delenv(ra.ENV_AUTHORIZATION, raising=False)
    yield
    reg.reset_external_registry()


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """The shipped rea entry, switched on, against the replaying stub server."""
    root = tmp_path / "node_modules" / "rea-agents"
    (root / "scripts").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": "6.4.0"}), encoding="utf-8")
    for name in ("stub_rea_server.py", "rea_6.4.0_recorded.json"):
        shutil.copy(FIXTURES / name, root / "scripts" / name)
    call_log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("ICDEV_REA_ENTRY", str(root / "scripts" / "stub_rea_server.py"))
    monkeypatch.setenv("STUB_REA_CALL_LOG", str(call_log))
    config = yaml.safe_load((REPO / "args/external_mcp_servers.yaml").read_text(encoding="utf-8"))
    spec = next(s for s in config["servers"] if s["name"] == "rea")
    spec = {**spec, "command": [sys.executable, "env:ICDEV_REA_ENTRY"], "enabled": True, "timeout_seconds": 30.0}
    monkeypatch.setattr(reg, "enabled_servers", lambda: [spec])
    monkeypatch.setattr(target_auth, "audit_recorder", lambda *a, **k: 1)

    class Stub:
        pass

    s = Stub()
    s.tmp = tmp_path
    s.calls = lambda: (
        [json.loads(x) for x in call_log.read_text(encoding="utf-8").splitlines()] if call_log.exists() else []
    )

    def scenario(**responses):
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps(responses), encoding="utf-8")
        monkeypatch.setenv("STUB_REA_RESPONSES", str(path))

    s.scenario = scenario
    s.pe = tmp_path / "Fixture.dll"
    s.pe.write_bytes(b"MZ icdev-owned rea-xlate-01 stand-in for the recorded Fixture.dll")
    return s


def _all_decompiled(ir: dict) -> None:
    assert ir["basis"] == "decompiled"
    assert ir["units"], ir.get("reason")
    for unit in ir["units"]:
        assert unit["basis"] == "decompiled", unit["name"]
        assert unit["confidence"] in ("low", "medium"), unit["name"]
        assert unit["evidence_id"], unit["name"]
        for key in ("name", "kind", "source_code", "idioms", "dependencies"):
            assert key in unit, (unit["name"], key)


# ---------------------------------------------------------------------------
# .NET DLL
# ---------------------------------------------------------------------------
def _dotnet_ir(stub) -> dict:
    stub.scenario(
        inspect_managed_artifact=RECORDED["responses"]["inspect_managed_artifact"],
        inspect_managed_members=_xlate("dotnet.inspect_managed_members"),
    )
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned", depth="xlate")
    assert report["status"] == "ok", report
    return dx.extract_from_report(report)


def test_dotnet_dll_yields_method_and_type_units(stub):
    ir = _dotnet_ir(stub)
    _all_decompiled(ir)
    assert ir["language"] == "csharp" and ir["recovery"] == ["cil_metadata"]
    by_name = {u["name"]: u for u in ir["units"]}
    assert {"Classifier", "Route"} <= set(by_name)
    classify = by_name["Classifier.Classify"]
    assert classify["parameters"] == [{"name": None, "type": "i4"}], "a parameter NAME was not measured"
    assert classify["return_type"] == "i4"
    assert classify["complexity"] == 4, "1 + the three switch targets"
    assert "switch_dispatch" in classify["idioms"]
    assert "did NOT decompile C#" in classify["source_code"]
    extern = by_name["Classifier.GetTickCount"]
    assert extern["complexity"] is None and "no_cil_body" in extern["idioms"]
    assert by_name["Route.ToString"]["calls"] == ["Route.get_Path"]
    edges = {(e["source"], e["target"]) for e in ir["call_graph"]["edges"]}
    assert ("Route..ctor", "Route.set_Path") in edges


def test_dotnet_at_summary_depth_has_nothing_to_translate(stub):
    stub.scenario(inspect_managed_artifact=RECORDED["responses"]["inspect_managed_artifact"])
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned")
    ir = dx.extract_from_report(report)
    assert ir["status"] == "unavailable" and ir["reason"] == "members_not_inspected"
    assert ir["units"] is None, "not measured is None, never []"


# ---------------------------------------------------------------------------
# Stripped C binary (Ghidra pseudocode)
# ---------------------------------------------------------------------------
def _native_ir(stub) -> dict:
    stub.scenario(
        open_binary=_wire("native_pe.open_binary"),
        binary_overview=_wire("native_pe.binary_overview"),
        get_call_graph=_xlate("native_pe.get_call_graph"),
        batch_decompile=_xlate("native_pe.batch_decompile"),
        list_unknowns=_wire("native_pe.list_unknowns"),
    )
    report = ra.analyze(
        str(stub.pe), family="binary", authorization_basis="owned", procedure="0x140001060", depth="xlate"
    )
    assert report["status"] == "ok", report
    return dx.extract_from_report(report)


def test_c_binary_yields_pseudocode_units_and_a_call_graph(stub):
    ir = _native_ir(stub)
    _all_decompiled(ir)
    assert ir["language"] == "c" and ir["dialect"] == "ghidra_pseudo_c"
    by_name = {u["name"]: u for u in ir["units"]}
    assert set(by_name) == {"FUN_140001060", "FUN_140001700", "FUN_140001f50"}
    main = by_name["FUN_140001060"]
    assert main["parameters"] == [{"name": "param_1", "type": "int"}, {"name": "param_2", "type": "longlong"}]
    assert main["return_type"] == "undefined8"
    assert {"unrecovered_symbol_names", "unrecovered_types"} <= set(main["idioms"])
    assert {"FUN_140001700", "FUN_140001f50", "strlen"} <= set(main["dependencies"])
    assert {"FUN_140001700", "FUN_140001f50"} <= set(main["calls"])
    assert ir["dependency_order"].index("FUN_140001700") < ir["dependency_order"].index("FUN_140001060")
    assert ir["call_graph"]["undecompiled_procedures"] == ["0x140001500", "0x140021e20"]
    batch = [c for c in stub.calls() if c["name"] == "batch_decompile"]
    assert batch and batch[0]["arguments"]["addresses"][0] == "0x140001060"
    assert len(batch[0]["arguments"]["addresses"]) == 5, "every call-graph node is asked for"


def test_native_xlate_without_an_entry_runs_nothing(stub):
    report = ra.analyze(str(stub.pe), family="binary", authorization_basis="owned", depth="xlate")
    assert report["status"] == "error" and report["reason"] == "procedure_required"
    assert stub.calls() == []


# ---------------------------------------------------------------------------
# JS bundle: source map first, bundle text second
# ---------------------------------------------------------------------------
def _js_report(stub, target: Path) -> dict:
    stub.scenario(analyze_javascript_application=_xlate("js_bundle.analyze_javascript_application"))
    report = ra.analyze(str(target), family="js_bundle", authorization_basis="owned", depth="xlate")
    assert report["status"] in ("ok", "partial"), report
    return report


def test_js_bundle_recovers_modules_from_the_source_map_first(stub):
    target = stub.tmp / "dist"
    shutil.copytree(JS_BUNDLE, target)
    ir = dx.extract_from_report(_js_report(stub, target))
    _all_decompiled(ir)
    assert ir["recovery"] == ["source_map"]
    by_name = {u["name"]: u for u in ir["units"]}
    assert {"checksum", "classify", "describeRoute", "entry"} <= set(by_name)
    assert by_name["describeRoute"]["confidence"] == "medium", "map content is claimed, not verified"
    assert by_name["describeRoute"]["calls"] == ["checksum", "classify"]
    assert ir["dependency_order"].index("checksum") < ir["dependency_order"].index("describeRoute")
    assert "./checksum.js" in ir["imports"]


def test_js_bundle_without_a_map_falls_back_to_bundle_text(stub):
    target = stub.tmp / "dist"
    shutil.copytree(JS_BUNDLE, target)
    (target / "app.js.map").unlink()
    ir = dx.extract_from_report(_js_report(stub, target))
    _all_decompiled(ir)
    assert ir["recovery"] == ["bundle_text"]
    functions = {u["name"] for u in ir["units"] if u["kind"] == "function"}
    assert functions == {"c", "n", "u"}, "minified names: the bundle is what was shipped"
    assert all(u["confidence"] == "low" for u in ir["units"])


def test_js_target_changed_after_analysis_is_refused(stub):
    target = stub.tmp / "dist"
    shutil.copytree(JS_BUNDLE, target)
    report = _js_report(stub, target)
    (target / "app.js").write_text("function swapped(){}", encoding="utf-8")
    ir = dx.extract_from_report(report)
    assert ir["status"] == "error" and ir["reason"] == "target_changed" and ir["units"] is None


# ---------------------------------------------------------------------------
# Rails
# ---------------------------------------------------------------------------
def test_a_report_without_authorization_is_refused(stub):
    report = ra.analyze(str(stub.pe), family="dotnet_assembly")
    ir = dx.extract_from_report(report)
    assert ir["reason"] == "authorization_missing" and ir["units"] is None


def test_unmeasured_lists_are_none_never_empty():
    report = {
        "family": "binary",
        "status": "unavailable",
        "reason": "engine_unavailable",
        "target": "x",
        "provenance": {"target_sha256": "ab" * 32, "authorization_basis": "owned"},
        "body": [],
        "limitations": None,
        "residual_unknowns": None,
    }
    ir = dx.extract_from_report(report)
    for key in ("units", "imports", "call_graph", "total_units", "residual_unknowns"):
        assert ir[key] is None, key


# ---------------------------------------------------------------------------
# Downstream: translate_unit, legacy_analyzer, architecture_extractor
# ---------------------------------------------------------------------------
def test_translate_units_accepts_decompiled_units_and_keeps_the_basis(stub, monkeypatch):
    ir = _native_ir(stub)
    prompts = []
    monkeypatch.setattr(ct, "_invoke_llm", lambda prompt, config, fn="": prompts.append(prompt) or "def f():\n    pass")
    result = ct.translate_units(ir, "c", "python", config={"translation": {"candidates": 1}})
    assert result["stats"]["translated_count"] == 3 and result["stats"]["decompiled_count"] == 3
    for entry in result["translated_units"]:
        assert entry["basis"] == "decompiled" and entry["evidence_id"] and entry["confidence"] == "low"
    assert all(p.startswith("NOTE: this unit was RECONSTRUCTED") for p in prompts)


def test_translate_unit_cli_writes_the_basis_into_the_output(stub, monkeypatch, tmp_path):
    ir_file = tmp_path / "ir.json"
    ir_file.write_text(json.dumps(_dotnet_ir(stub)), encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(ct, "_invoke_llm", lambda *a, **k: None)  # LLM down -> mock-and-continue
    monkeypatch.setattr(ct, "DB_PATH", tmp_path / "absent.db")
    from icdev.tools.audit import audit_logger

    monkeypatch.setattr(audit_logger, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "code_translator",
            "--ir-file",
            str(ir_file),
            "--source-language",
            "csharp",
            "--target-language",
            "python",
            "--output-dir",
            str(out),
            "--candidates",
            "1",
            "--json",
        ],
    )
    ct.main()
    written = sorted(out.glob("*.py"))
    assert written, "the CLI wrote nothing"
    for path in written:
        assert path.read_text(encoding="utf-8").startswith("# basis: decompiled")


_LEGACY_DDL = """
CREATE TABLE projects (id TEXT PRIMARY KEY);
CREATE TABLE legacy_applications (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
    description TEXT, source_path TEXT NOT NULL, primary_language TEXT NOT NULL,
    language_version TEXT, framework TEXT, framework_version TEXT, app_type TEXT DEFAULT 'monolith',
    analysis_status TEXT DEFAULT 'registered', loc_total INTEGER DEFAULT 0, loc_code INTEGER DEFAULT 0,
    loc_comment INTEGER DEFAULT 0, loc_blank INTEGER DEFAULT 0, file_count INTEGER DEFAULT 0,
    complexity_score REAL DEFAULT 0.0, tech_debt_hours REAL DEFAULT 0.0,
    maintainability_index REAL DEFAULT 0.0, source_hash TEXT, registered_at TEXT, analyzed_at TEXT,
    UNIQUE(project_id, name));
CREATE TABLE legacy_components (
    id TEXT PRIMARY KEY, legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    name TEXT NOT NULL, component_type TEXT NOT NULL CHECK(component_type IN (
        'class','module','package','service','controller','model','view','repository','util',
        'config','test','migration','interface','abstract_class','enum','servlet','ejb','entity',
        'stored_procedure','trigger','function','api_endpoint')),
    file_path TEXT NOT NULL, qualified_name TEXT, parent_component_id TEXT REFERENCES legacy_components(id),
    loc INTEGER DEFAULT 0, cyclomatic_complexity REAL DEFAULT 0.0, coupling_score REAL DEFAULT 0.0,
    cohesion_score REAL DEFAULT 0.0, dependencies_in INTEGER DEFAULT 0, dependencies_out INTEGER DEFAULT 0,
    properties TEXT, discovered_at TEXT, UNIQUE(legacy_app_id, qualified_name));
CREATE TABLE legacy_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, legacy_app_id TEXT NOT NULL REFERENCES legacy_applications(id),
    source_component_id TEXT NOT NULL REFERENCES legacy_components(id),
    target_component_id TEXT REFERENCES legacy_components(id), dependency_type TEXT NOT NULL,
    weight REAL DEFAULT 1.0, is_bidirectional INTEGER DEFAULT 0, evidence TEXT,
    UNIQUE(legacy_app_id, source_component_id, target_component_id, dependency_type));
INSERT INTO projects (id) VALUES ('proj-rea-xlate');
"""


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    from icdev.tools.modernization import architecture_extractor, legacy_analyzer

    db = tmp_path / "icdev.db"
    conn = sqlite3.connect(db)
    conn.executescript(_LEGACY_DDL)
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.setattr(legacy_analyzer, "DB_PATH", db)
    monkeypatch.setattr(architecture_extractor, "DB_PATH", db)
    return legacy_analyzer, architecture_extractor, db


@pytest.mark.parametrize("build", ["native", "dotnet"])
def test_a_binary_only_app_registers_and_its_call_graph_keeps_the_basis(stub, legacy_db, build):
    legacy_analyzer, architecture_extractor, db = legacy_db
    ir = _native_ir(stub) if build == "native" else _dotnet_ir(stub)
    app = legacy_analyzer.register_binary_application("proj-rea-xlate", f"fixture-{build}", ir)
    assert app["basis"] == "decompiled" and app["description"].startswith("[basis: decompiled")
    assert app["components"] == len(ir["units"]) and app["dependencies"] >= 1
    with sqlite3.connect(db) as conn:
        props = [
            json.loads(r[0])
            for r in conn.execute("SELECT properties FROM legacy_components WHERE legacy_app_id=?", (app["id"],))
        ]
        source_hash = conn.execute("SELECT source_hash FROM legacy_applications WHERE id=?", (app["id"],)).fetchone()[0]
    assert all(p["basis"] == "decompiled" and p["evidence_id"] for p in props)
    assert source_hash == ir["provenance"]["target_sha256"]

    graph = architecture_extractor.extract_call_graph(app["id"])
    assert graph["edges"] and graph["basis"] == "decompiled"
    assert all(n["basis"] == "decompiled" and n["confidence"] == "low" for n in graph["nodes"])


def test_register_binary_application_refuses_source_ir(legacy_db):
    legacy_analyzer, _, _ = legacy_db
    with pytest.raises(ValueError):
        legacy_analyzer.register_binary_application("proj-rea-xlate", "x", {"units": [{"name": "f"}]})
