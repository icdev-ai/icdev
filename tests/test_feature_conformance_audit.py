# CUI // SP-CTI
"""Feature conformance audit — the five rules, per feature (icx-cnf-01).

The audit is only worth anything if it has teeth, so most of this file builds a
small synthetic repository in which ONE demo feature meets all five rules, then
breaks one rule at a time and asserts that exactly that gap appears. The url_map
and the MCP declarations are injected, so no test here starts the dashboard; the
real probe is exercised by ``--check`` and the coherence sweep.

What is held:
  * determinism: two runs, and a shuffled url_map, give byte-identical JSON;
  * a planted gap for each rule shows up under that rule, by id;
  * a stated reason excuses only its own rule, and a reason that excuses nothing
    is stale;
  * stops and non-decisions go stale when the surface is not a decision;
  * an unowned endpoint and an unverifiable quotation are config errors;
  * the census, doc and snapshot make ``check()`` fail when stale;
  * the MCP tool and the console page serve the committed snapshot read-only;
  * the coherence check warns on gaps and fails once enforcement is ``fail``.
"""
from __future__ import annotations

import copy
import importlib
import random
import textwrap
from pathlib import Path

import pytest
import yaml

fa = importlib.import_module("tools.conformance.feature_audit")

REPO = Path(__file__).resolve().parent.parent

BLUEPRINT = textwrap.dedent('''\
    from flask import render_template


    def index():
        return render_template("demo/index.html")


    def api_launch():
        return {"ok": True}


    def api_tweak():
        return {"ok": True}


    def api_store():
        from tools.workflow_hitl.gate import HITLGate
        return HITLGate


    def details():
        return render_template("demo/details.html")
    ''')

ADAPTER = textwrap.dedent('''\
    from tools.twin_core.registry import TwinAdapter, register_twin


    @register_twin
    class DemoTwin(TwinAdapter):
        canvas_key = "demo"

        def take_snapshot(self, target_id, label=None, **kwargs):
            return {"violations": [{"thread_id": "thr-1"}]}

        def replay(self, snapshot_id):
            return {}
    ''')


def _lines(text: str, name: str) -> tuple[int, int]:
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines, 1) if ln.startswith(f"def {name}("))
    end = start
    while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
        end += 1
    return start, end


def _rule(path: str, endpoint: str, methods: list[str]) -> dict:
    start, end = _lines(BLUEPRINT, endpoint.split(".", 1)[1])
    return {"rule": path, "endpoint": endpoint, "methods": sorted(methods + ["OPTIONS"]),
            "file": "tools/demo/blueprint.py", "start": start, "end": end}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def demo(tmp_path: Path) -> dict:
    """A repository in which feature ``demo.core`` meets all five rules."""
    root = tmp_path
    _write(root, "args/component_registry.yaml", yaml.safe_dump({"components": [
        {"key": "demo", "kind": "canvas", "url_prefix": "/demo",
         "nav": {"section": "Canvases", "label": "Demo",
                 "links": [{"label": "Demo", "href": "/demo/"}]}},
    ]}))
    _write(root, "tools/dashboard/templates/base.html", '<a href="/home">Home</a>\n')
    _write(root, "tools/dashboard/templates/demo/index.html", textwrap.dedent('''\
        <a href="/demo/details">details</a>
        <script>
          fetch('/demo/api/launch', {method: 'POST'});
          fetch('/demo/api/tweak', {method: 'POST'});
        </script>
        '''))
    _write(root, "tools/dashboard/templates/demo/details.html", "<p>details</p>\n")
    _write(root, "tools/demo/blueprint.py", BLUEPRINT)
    _write(root, "tools/demo/store.py", "# a person decides: A HUMAN signs this off\n")
    _write(root, "tools/twin_core/adapters/demo.py", ADAPTER)
    _write(root, "tools/mbse/digital_thread.py",
           'VALID_TYPES = ("doors_requirement", "sysml_element", "code_module", "test_file")\n')
    _write(root, "tests/test_demo.py", "def test_demo():\n    assert True\n")
    _write(root, "args/agent_approval_policy.yaml", yaml.safe_dump({
        "default_tier": "unknown", "require_approval_tiers": ["irreversible", "unknown"],
        "tools": {"reversible": ["demo_read"], "recoverable": [], "irreversible": []},
    }))
    _write(root, "args/workflow_hitl_templates/demo-launch.yaml", yaml.safe_dump({
        "id": "wft-demo-launch", "name": "Demo launch",
        "decides": ["demo.api_launch"],
        "stages": [{"name": "Approve", "step_type": "manual", "role": "pm"}],
    }))
    _write(root, "args/platform_model/demo.yaml", yaml.safe_dump({
        "requirements": [{"id": "REQ-DEMO-1", "features": ["demo.core"],
                          "elements": ["demo.blueprint"], "tests": ["tests/test_demo.py"]}],
        "elements": [{"id": "demo.blueprint", "type": "sysml_element", "features": ["demo.core"],
                      "code": ["tools/demo/blueprint.py"]}],
    }))
    config = {
        "enforcement": "warn",
        "census_max": 50,
        "doc": "docs/audit.md",
        "snapshot": "docs/audit.json",
        "census": "args/census.txt",
        "components": {"demo": {"blueprints": ["demo"], "mcp_prefixes": ["demo_"]}},
        "features": [{
            "key": "demo.core", "component": "demo", "label": "Demo",
            "endpoints": ["demo.index", "demo.details", "demo.api_launch", "demo.api_tweak"],
            "mcp_tools": ["demo_read", "demo_launch"],
        }],
    }
    rules = [
        _rule("/demo/", "demo.index", ["GET", "HEAD"]),
        _rule("/demo/details", "demo.details", ["GET", "HEAD"]),
        _rule("/demo/api/launch", "demo.api_launch", ["POST"]),
        _rule("/demo/api/tweak", "demo.api_tweak", ["POST"]),
    ]
    mcp = {
        "demo_read": {"registered": True, "read_only": True, "tier": "read"},
        "demo_launch": {"registered": True, "read_only": False, "tier": "write"},
    }
    return {"root": root, "config": config, "rules": rules, "mcp": mcp}


def run(demo: dict) -> dict:
    return fa.audit(demo["root"], rules=copy.deepcopy(demo["rules"]),
                    mcp=copy.deepcopy(demo["mcp"]), config=copy.deepcopy(demo["config"]))


def gaps(report: dict, rule: str, feature: str = "demo.core") -> list[str]:
    cell = next(f for f in report["features"] if f["key"] == feature)["cells"][rule]
    return [g["id"] for g in cell["gaps"]]


def cell(report: dict, rule: str, feature: str = "demo.core") -> dict:
    return next(f for f in report["features"] if f["key"] == feature)["cells"][rule]


# ── baseline and determinism ─────────────────────────────────────────────────


def test_the_baseline_meets_all_five_rules(demo):
    report = run(demo)
    assert report["config_errors"] == []
    assert report["stale_reasons"] == []
    assert report["summary"]["gaps_total"] == 0, report["features"]
    statuses = {r: cell(report, r)["status"] for r in fa.RULES}
    assert statuses == {r: "pass" for r in fa.RULES}
    # Every verdict names what it rests on.
    assert all(cell(report, r)["evidence"] for r in fa.RULES)
    assert any("wft-demo-launch" in e for e in cell(report, "workflow")["evidence"])


def test_two_runs_and_a_shuffled_url_map_are_byte_identical(demo):
    first = fa.snapshot_text(run(demo))
    assert fa.snapshot_text(run(demo)) == first
    shuffled = dict(demo, rules=list(reversed(demo["rules"])))
    random.Random(7).shuffle(shuffled["rules"])
    assert fa.snapshot_text(run(shuffled)) == first
    assert fa.render_markdown(run(demo)) == fa.render_markdown(run(shuffled))


# ── one planted gap per rule ─────────────────────────────────────────────────


def test_gui_an_uncalled_mutating_route_is_a_gap(demo):
    _write(demo["root"], "tools/dashboard/templates/demo/index.html",
           "<a href=\"/demo/details\">d</a><script>fetch('/demo/api/launch')</script>\n")
    report = run(demo)
    assert gaps(report, "gui") == ["gui:uncalled:demo.api_tweak"]
    assert gaps(report, "workflow") == []


def test_gui_a_page_outside_the_nav_is_a_gap(demo):
    demo["config"]["features"][0]["endpoints"].remove("demo.index")
    demo["config"]["components"] = {}
    _write(demo["root"], "tools/dashboard/templates/demo/index.html", "<p>no links</p>\n")
    report = run(demo)
    assert "gui:orphan-page:demo.details" in gaps(report, "gui")
    assert "gui:not-in-nav" in gaps(report, "gui")


def test_workflow_a_decision_with_no_template_is_a_gap(demo):
    (demo["root"] / "args/workflow_hitl_templates/demo-launch.yaml").unlink()
    report = run(demo)
    assert gaps(report, "workflow") == ["workflow:undeclared:demo.api_launch"]
    assert gaps(report, "gui") == []


def test_workflow_a_hitl_gate_in_the_source_makes_a_decision(demo):
    demo["config"]["features"][0]["endpoints"].append("demo.api_store")
    demo["rules"].append(_rule("/demo/api/store", "demo.api_store", ["POST"]))
    _write(demo["root"], "tools/dashboard/templates/demo/store.html",
           "<script>fetch('/demo/api/store')</script>\n")
    assert gaps(run(demo), "workflow") == ["workflow:undeclared:demo.api_store"]


def test_workflow_a_template_with_no_manual_role_stage_does_not_count(demo):
    _write(demo["root"], "args/workflow_hitl_templates/demo-launch.yaml", yaml.safe_dump({
        "id": "wft-auto", "decides": ["demo.api_launch"],
        "stages": [{"name": "Auto", "step_type": "automated"}],
    }))
    assert gaps(run(demo), "workflow") == ["workflow:auto-template:demo.api_launch"]


def test_workflow_a_declared_decision_with_no_endpoint_is_a_gap(demo):
    demo["config"]["features"][0]["declared_decisions"] = [
        {"id": "demo.signoff", "evidence": {"file": "tools/demo/store.py",
                                            "text": "A HUMAN signs this off"}}]
    report = run(demo)
    assert gaps(report, "workflow") == ["workflow:no-endpoint:demo.signoff"]
    assert report["config_errors"] == []


def test_workflow_a_declared_decision_must_quote_real_text(demo):
    demo["config"]["features"][0]["declared_decisions"] = [
        {"id": "demo.signoff", "evidence": {"file": "tools/demo/store.py",
                                            "text": "words that are not there"}}]
    assert any("quotes text not found" in e for e in run(demo)["config_errors"])


def test_workflow_an_mcp_decision_passes_only_through_a_halting_tier(demo):
    # demo_launch is not enumerated: default tier 'unknown' halts.
    assert any("mcp:demo_launch" in e and "halts" in e
               for e in cell(run(demo), "workflow")["evidence"])
    _write(demo["root"], "args/agent_approval_policy.yaml", yaml.safe_dump({
        "default_tier": "unknown", "require_approval_tiers": ["irreversible", "unknown"],
        "tools": {"recoverable": ["demo_launch"]},
    }))
    report = run(demo)
    assert gaps(report, "workflow") == ["workflow:undeclared:mcp:demo_launch"]
    assert "ai:ungated-mutation:demo_launch" in gaps(report, "ai_native")


def test_twin_no_adapter_and_no_thread_ids_are_gaps(demo):
    _write(demo["root"], "tools/twin_core/adapters/demo.py",
           ADAPTER.replace('"thread_id"', '"trace"'))
    assert gaps(run(demo), "twin") == [
        "twin:no-thread-ids:tools/twin_core/adapters/demo.py::DemoTwin"]
    (demo["root"] / "tools/twin_core/adapters/demo.py").unlink()
    assert gaps(run(demo), "twin") == ["twin:no-adapter"]


def test_twin_an_adapter_with_no_replay_is_a_gap(demo):
    _write(demo["root"], "tools/twin_core/adapters/demo.py",
           ADAPTER.replace("def replay(", "def rerun("))
    assert gaps(run(demo), "twin") == ["twin:no-replay:tools/twin_core/adapters/demo.py::DemoTwin"]


def test_architecture_an_orphan_element_and_a_missing_test_are_gaps(demo):
    _write(demo["root"], "args/platform_model/demo.yaml", yaml.safe_dump({
        "requirements": [{"id": "REQ-DEMO-1", "features": ["demo.core"],
                          "elements": ["demo.blueprint"], "tests": ["tests/test_gone.py"]}],
        "elements": [
            {"id": "demo.blueprint", "type": "sysml_element", "features": ["demo.core"],
             "code": ["tools/demo/blueprint.py"]},
            {"id": "demo.orphan", "type": "sysml_element", "features": ["demo.core"],
             "code": ["tools/demo/store.py"]},
        ],
    }))
    assert gaps(run(demo), "architecture") == [
        "architecture:orphan-element:demo.orphan",
        "architecture:untested:REQ-DEMO-1",
    ]
    (demo["root"] / "args/platform_model/demo.yaml").unlink()
    assert gaps(run(demo), "architecture") == ["architecture:no-requirement"]


def test_architecture_a_cited_test_or_symbol_must_exist_and_interfaces_count(demo):
    """icx-cnf-04: a test is a pytest node and code may name a symbol; both are checked.

    An interface is an element, so one no requirement names is an orphan.
    """
    def model(test: str, code: str, iface_used: bool) -> None:
        _write(demo["root"], "args/platform_model/demo.yaml", yaml.safe_dump({
            "requirements": [{"id": "REQ-DEMO-1", "features": ["demo.core"],
                              "elements": ["demo.blueprint"] + (["if.demo"] if iface_used else []),
                              "tests": [test]}],
            "elements": [{"id": "demo.blueprint", "type": "sysml_element",
                          "features": ["demo.core"], "code": [code]}],
            "interfaces": [{"id": "if.demo", "type": "sysml_element", "features": ["demo.core"],
                            "code": ["tools/demo/blueprint.py"]}],
        }))

    model("tests/test_demo.py::test_demo", "tools/demo/blueprint.py::index", True)
    assert gaps(run(demo), "architecture") == []
    model("tests/test_demo.py::test_invented", "tools/demo/blueprint.py::index", True)
    assert gaps(run(demo), "architecture") == ["architecture:untested:REQ-DEMO-1"]
    model("tests/test_demo.py::test_demo", "tools/demo/blueprint.py::no_such_view", True)
    assert gaps(run(demo), "architecture") == ["architecture:no-code:demo.blueprint"]
    model("tests/test_demo.py::test_demo", "tools/demo/blueprint.py::index", False)
    assert gaps(run(demo), "architecture") == ["architecture:orphan-element:if.demo"]


def test_ai_native_no_read_tool_is_a_gap(demo):
    demo["mcp"]["demo_read"]["read_only"] = False
    demo["mcp"]["demo_read"]["tier"] = "write"
    report = run(demo)
    assert "ai:no-read-tool" in gaps(report, "ai_native")
    # demo_read is enumerated 'reversible' (auto), so as a mutating tool it is ungated too.
    assert "ai:ungated-mutation:demo_read" in gaps(report, "ai_native")


# ── reasons, stops and ownership ─────────────────────────────────────────────


def test_a_stated_reason_excuses_only_its_own_rule(demo):
    (demo["root"] / "tools/twin_core/adapters/demo.py").unlink()
    (demo["root"] / "args/platform_model/demo.yaml").unlink()
    demo["config"]["features"][0]["reasons"] = {"twin": "Nothing here has state to observe."}
    report = run(demo)
    assert cell(report, "twin")["status"] == "excused"
    assert cell(report, "twin")["gaps"] == []
    assert [g["id"] for g in cell(report, "twin")["excused"]] == ["twin:no-adapter"]
    # The architecture gap is untouched by the twin reason.
    assert gaps(report, "architecture") == ["architecture:no-requirement"]
    assert report["summary"]["gaps_total"] == 1
    assert "demo.core|twin:no-adapter" not in fa.gap_keys(report)


def test_a_reason_that_excuses_nothing_is_stale(demo):
    demo["config"]["features"][0]["reasons"] = {"gui": "No page needed."}
    report = run(demo)
    assert cell(report, "gui")["status"] == "pass"
    assert any("reason for 'gui' excuses nothing" in s for s in report["stale_reasons"])
    problems = fa.check(report, demo["root"], demo["config"])
    assert any(p.startswith("stale reason:") for p in problems)


def test_a_stop_is_immediate_and_goes_stale_when_not_a_decision(demo):
    feature = demo["config"]["features"][0]
    (demo["root"] / "args/workflow_hitl_templates/demo-launch.yaml").unlink()
    feature["stops"] = [{"surface": "demo.api_launch", "reason": "a stop"}]
    report = run(demo)
    assert gaps(report, "workflow") == []
    assert any("stop, immediate" in e for e in cell(report, "workflow")["evidence"])
    feature["stops"] = [{"surface": "demo.api_tweak", "reason": "not a decision at all"}]
    assert any("stops demo.api_tweak" in s for s in run(demo)["stale_reasons"])


def test_a_machine_endpoint_reason_goes_stale_once_a_page_calls_it(demo):
    demo["config"]["features"][0]["machine_endpoints"] = [
        {"endpoint": "demo.api_tweak", "reason": "external consumers only"}]
    assert any("machine_endpoints demo.api_tweak" in s for s in run(demo)["stale_reasons"])


def test_every_endpoint_of_an_owned_blueprint_needs_a_feature(demo):
    demo["rules"].append(_rule("/demo/api/store", "demo.api_store", ["POST"]))
    errors = run(demo)["config_errors"]
    assert "demo: endpoint demo.api_store is owned by no feature" in errors


def test_an_mcp_tool_under_an_owned_prefix_needs_a_feature(demo):
    demo["mcp"]["demo_extra"] = {"registered": True, "read_only": True, "tier": "read"}
    assert "demo: MCP tool demo_extra is owned by no feature" in run(demo)["config_errors"]


# ── census, doc and snapshot ─────────────────────────────────────────────────


def test_check_fails_on_a_stale_doc_snapshot_or_census(demo):
    root, cfg = demo["root"], demo["config"]
    (root / "tools/twin_core/adapters/demo.py").unlink()
    report = run(demo)
    problems = fa.check(report, root, cfg)
    assert any("docs/audit.md is stale" in p for p in problems)
    assert any("docs/audit.json is stale" in p for p in problems)
    assert "new gap not in the census: demo.core|twin:no-adapter" in problems

    _write(root, "docs/audit.md", "# Audit\n\n<!-- BEGIN GENERATED feature-conformance -->\n"
           "<!-- END GENERATED feature-conformance -->\n\n## Notes\nkept\n")
    fa.write_doc(report, root, cfg)
    _write(root, "args/census.txt", fa.render_census(report))
    assert fa.check(report, root, cfg) == []
    assert (root / "docs/audit.md").read_text(encoding="utf-8").endswith("## Notes\nkept\n")

    # Closing the gap leaves a census line that must be pruned.
    _write(root, "tools/twin_core/adapters/demo.py", ADAPTER)
    problems = fa.check(run(demo), root, cfg)
    assert ("census lists a closed gap (prune it, lower census_max): "
            "demo.core|twin:no-adapter") in problems


def test_the_census_ceiling_may_not_be_exceeded(demo):
    root, cfg = demo["root"], dict(demo["config"], census_max=0)
    (root / "tools/twin_core/adapters/demo.py").unlink()
    report = run(demo)
    _write(root, "args/census.txt", fa.render_census(report))
    assert any("over census_max" in p for p in fa.check(report, root, cfg))


# ── the shipped record ───────────────────────────────────────────────────────


def test_the_shipped_config_census_and_snapshot_agree():
    cfg = fa.load_config(REPO)
    snapshot = fa.load_snapshot(REPO)
    census = fa.read_census(REPO / cfg["census"])
    assert census == fa.gap_keys(snapshot)
    assert len(census) <= int(cfg["census_max"])
    assert snapshot["config_errors"] == []
    assert snapshot["stale_reasons"] == []
    keys = {f["key"] for f in cfg["features"]}
    assert {"cortex.search_ask", "ace.runs", "cwk.canvas"} <= keys
    assert {f["key"] for f in snapshot["features"]} == keys


def test_the_mcp_tool_is_declared_read_only_and_serves_the_snapshot():
    registry = importlib.import_module("tools.mcp.tool_registry")
    handlers = importlib.import_module("tools.mcp.gap_handlers")
    assert "feature_conformance" in registry.list_tools()
    assert registry.READ_ONLY_DECLARATIONS["feature_conformance"] is True
    assert registry.tool_authorization("feature_conformance")["tier"] == "read"

    out = handlers.handle_feature_conformance({"feature": "ace.abort", "rule": "workflow"})
    assert [f["key"] for f in out["features"]] == ["ace.abort"]
    assert list(out["features"][0]["cells"]) == ["workflow"]
    assert out["source"]["snapshot"].endswith(".json")
    assert "error" in handlers.handle_feature_conformance({"rule": "bogus"})
    assert "error" in handlers.handle_feature_conformance({"feature": "no.such"})


def test_the_console_page_and_api_render_the_snapshot(tmp_path):
    from flask import Flask

    from tools.twin_observatory.blueprint import twin_observatory_bp

    # A stub base.html first in the search path: the real one needs the whole
    # dashboard's context. The page's own template is the real one.
    _write(tmp_path, "base.html", "{% block content %}{% endblock %}")
    app = Flask(__name__, template_folder=str(tmp_path))
    app.register_blueprint(twin_observatory_bp)
    client = app.test_client()
    page = client.get("/twin-observatory/conformance")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'id="conformance-matrix"' in html
    assert "ACE: abort" in html
    api = client.get("/twin-observatory/api/conformance")
    assert api.status_code == 200
    assert api.get_json()["report"]["summary"]["features"] >= 17


# ── the coherence check ──────────────────────────────────────────────────────


_REAL_AUDIT = fa.audit


def _checker(monkeypatch, demo, enforcement: str):
    checker = importlib.import_module("tools.workflow.coherence_checker")
    cfg = dict(demo["config"], enforcement=enforcement)
    report = _REAL_AUDIT(demo["root"], rules=demo["rules"], mcp=demo["mcp"], config=cfg)
    monkeypatch.setattr(fa, "load_config", lambda root=None: cfg)
    monkeypatch.setattr(fa, "audit", lambda root=None, config=None: report)
    monkeypatch.setattr(fa, "check", lambda rep, root, c: [])
    return checker.check_feature_conformance()


def test_coherence_warns_on_open_gaps_and_fails_once_enforced(monkeypatch, demo):
    (demo["root"] / "tools/twin_core/adapters/demo.py").unlink()
    warned = _checker(monkeypatch, demo, "warn")
    assert warned.status == "warn"
    assert "twin: 1 open gap(s)" in warned.missing
    assert _checker(monkeypatch, demo, "fail").status == "fail"


def test_coherence_passes_when_every_feature_conforms(monkeypatch, demo):
    assert _checker(monkeypatch, demo, "fail").status == "pass"


def test_coherence_registers_the_check_in_the_heavy_tier():
    checker = importlib.import_module("tools.workflow.coherence_checker")
    assert checker.CHECK_REGISTRY["feature_conformance"] is checker.check_feature_conformance
    assert "feature_conformance" not in checker.select_checks("fast", [Path("README.md")])
    assert "feature_conformance" in checker.select_checks("fast", [Path("tools/cortex/api.py")])
