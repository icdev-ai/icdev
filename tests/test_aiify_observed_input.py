# CUI // SP-CTI
"""AI-ify from observed behaviour: the no-source input mode (rea-aiify-01, card ``rea``).

What is pinned here:

* ``engine._resolve_input`` accepts har / electron_app / website / apk, and the
  rails refuse BEFORE a scan row exists: no authorization basis, a missing
  local artifact, a URL where a local artifact is required, a HAR with no
  ``sensitive_values``, a live website without ``allow_execute``;
* REA evidence -> a feature inventory: ``METHOD /path`` with ids collapsed and
  the query string never kept, static assets dropped, traced seeds become
  features, a seed that reaches nothing does not;
* an inventory nobody measured is ``None`` with a basis -- never ``[]`` -- and
  the scan then says "Not measured", not "no AI-augmentable patterns";
* each MAPPED feature is scored by the existing ``score_and_assess`` and placed
  by the existing ``generate_roadmap``, tagged ``basis: observed`` on the
  opportunity row and the roadmap entry; unmapped features are never scored;
* end to end, REA is reached through ``analyzer_dispatch`` on the sandbox path
  (the stub stdio server; CI needs neither Node nor Ghidra).

The ``analyze_javascript_application`` response is REA 6.4.0's own recording
(rea-spike-01). The ``trace_application_feature`` and named-entry
``inspect_web_network_capture`` bodies are SYNTHETIC: REA's evidence envelope
around a hand-written ``normalized_result``, because rea-spike-01 recorded
neither (the HAR answer was over the stdio budget). They pin this module's
reading of named entries, not REA's exact shape.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from tools.aiify import observed_inventory as oi

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "rea"
RECORDED = json.loads((FIXTURES / "rea_6.4.0_adapter_responses.json").read_text(encoding="utf-8"))
JS_SUMMARY = RECORDED["responses"]["js_bundle.analyze_javascript_application"]["result"]
HAR_OVER_BUDGET = RECORDED["responses"]["har.inspect_web_network_capture"]["result"]


def _evidence(operation: str, normalized: dict, tag: str) -> dict:
    """REA's evidence envelope (synthetic body; see the module docstring)."""
    body = {"evidence_id": "ev_" + (tag * 64)[:64], "subject": {"name": "fixture"},
            "provider": {"id": "rea-test"}, "operation": operation, "confidence": "observed",
            "authority": "static", "limitations": ["synthetic fixture"],
            "normalized_result": normalized}
    return {"content": [{"type": "text", "text": json.dumps(body)}]}


HAR_RECORDS = {"records": [
    {"request": {"method": "POST", "url": "https://app.example/api/documents/upload?token=S3CRET"},
     "response": {"status": 200}},
    {"request": {"method": "GET", "url": "https://app.example/api/search?q=alpha"}},
    {"request": {"method": "GET", "url": "https://app.example/api/search?q=beta"}},
    {"request": {"method": "GET", "url": "https://app.example/api/search?q=gamma"}},
    {"request": {"method": "GET", "url": "https://app.example/api/orders/48213"}},
    {"request": {"method": "GET", "url": "https://app.example/static/app.9f3c.js"}},
]}
TRACE = {"seed": {"kind": "string", "value": "export report"},
         "nodes": [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
         "relations": [{"from": "n1", "to": "n2"}]}


@pytest.fixture(autouse=True)
def _no_env_basis(monkeypatch):
    monkeypatch.delenv(oi.ENV_AUTHORIZATION, raising=False)


# ---------------------------------------------------------------------------
# Rails -- refused before anything runs
# ---------------------------------------------------------------------------
def test_every_observed_type_is_accepted_by_resolve_input(tmp_path):
    from tools.aiify import engine

    har = tmp_path / "s.har"
    har.write_text("{}", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"PK")
    ctx = {"authorization_basis": "owned", "sensitive_values": []}
    for input_type, ref in (("har", har), ("electron_app", app), ("website", app), ("apk", apk)):
        path, cleanup = engine._resolve_input(input_type, str(ref), ctx)
        assert Path(path) == ref.resolve() and cleanup is None


@pytest.mark.parametrize("input_type,ref,ctx,reason", [
    ("electron_app", "APP", {}, "authorization_missing"),
    ("har", "HAR", {"authorization_basis": "owned"}, "sensitive_values_required"),
    ("apk", "MISSING", {"authorization_basis": "owned"}, "source_unreadable"),
    ("har", "https://app.example/s.har", {"authorization_basis": "owned"}, "local_artifact_required"),
    ("website", "https://app.example/", {"authorization_basis": "owned"}, "allow_execute_required"),
    ("electron_app", "APP", {"authorization_basis": "borrowed"}, "authorization_missing"),
])
def test_a_rail_refuses_before_a_scan_row(tmp_path, monkeypatch, input_type, ref, ctx, reason):
    from tools.aiify import engine

    (tmp_path / "app").mkdir()
    (tmp_path / "s.har").write_text("{}", encoding="utf-8")
    ref = {"APP": str(tmp_path / "app"), "HAR": str(tmp_path / "s.har"),
           "MISSING": str(tmp_path / "absent.apk")}.get(ref, ref)
    monkeypatch.setattr(engine, "init_db", lambda: None)
    monkeypatch.setattr(engine, "get_connection",
                        lambda: pytest.fail("a refused input must never open the scan DB"))
    with pytest.raises(oi.ObservedInputRefused) as refused:
        engine.run_scan(input_type, ref, ctx)
    assert refused.value.reason == reason
    assert isinstance(refused.value, RuntimeError), "the dashboard's error path still catches it"


def test_the_env_basis_is_honoured_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv(oi.ENV_AUTHORIZATION, "customer-authorized")
    (tmp_path / "app").mkdir()
    plan = oi.check_observed_input("electron_app", str(tmp_path / "app"), {})
    assert plan["provenance"]["authorization_basis"] == "customer-authorized"
    assert plan["provenance"]["analysis"] == "local"
    assert plan["provenance"]["allow_execute"] is False


def test_a_live_website_with_allow_execute_goes_to_the_website_family():
    plan = oi.check_observed_input("website", "https://app.example/",
                                   {"authorization_basis": "owned", "allow_execute": True})
    assert plan["family"] == "website" and plan["provenance"]["target_sha256"] is None
    # allow_execute must be the literal True -- a truthy string is not consent.
    with pytest.raises(oi.ObservedInputRefused):
        oi.check_observed_input("website", "https://app.example/",
                                {"authorization_basis": "owned", "allow_execute": "yes"})


# ---------------------------------------------------------------------------
# Evidence -> inventory -> patterns
# ---------------------------------------------------------------------------
def _entry(tool: str, normalized: dict, ev: str = "ev_1") -> dict:
    return {"tool": tool, "evidence_id": ev, "normalized_result": normalized,
            "rea_confidence": "observed", "basis": "observed"}


def test_har_records_become_routes_without_query_or_assets():
    features = oi.extract_features([_entry("inspect_web_network_capture", HAR_RECORDS)])
    by_label = {f["label"]: f for f in features}
    assert set(by_label) == {"POST /api/documents/upload", "GET /api/search", "GET /api/orders/{id}"}
    assert by_label["GET /api/search"]["occurrences"] == 3
    assert by_label["POST /api/documents/upload"]["pattern_type"] == "document_ingestion_pipeline"
    assert by_label["GET /api/search"]["pattern_type"] == "fulltext_search_engine"
    assert by_label["GET /api/orders/{id}"]["pattern_type"] is None
    assert "S3CRET" not in json.dumps(features) and "app.example" not in json.dumps(features)


def test_ipc_channels_and_traced_seeds_are_features_and_a_dead_seed_is_not():
    features = oi.extract_features([
        _entry("analyze_javascript_application", {"ipc": [{"channel": "invoice:import"}]}),
        _entry("trace_application_feature", TRACE, "ev_2"),
        _entry("trace_application_feature", {"seed": {"value": "ghost"}, "nodes": []}, "ev_3"),
    ])
    labels = {(f["kind"], f["label"]) for f in features}
    assert labels == {("ipc_channel", "invoice:import"), ("traced_feature", "export report")}
    traced = next(f for f in features if f["kind"] == "traced_feature")
    assert traced["occurrences"] == 4 and traced["evidence_ids"] == ["ev_2"]
    assert traced["pattern_type"] == "string_template_rendering"


def test_patterns_are_observed_tagged_and_unmapped_features_are_not_scored():
    inventory = {"input_type": "har", "family": "har",
                 "provenance": {"target_sha256": "ab" * 32, "authorization_basis": "owned"},
                 "features": oi.extract_features([_entry("inspect_web_network_capture", HAR_RECORDS)])}
    patterns = oi.features_to_patterns(inventory)
    assert {p["function_name"] for p in patterns} == {"POST /api/documents/upload", "GET /api/search"}
    for p in patterns:
        assert p["basis"] == "observed" and p["pattern_detail"]["basis"] == "observed"
        assert p["module_path"].startswith("observed:har:") and p["line_start"] == 0
        assert p["pattern_detail"]["target_sha256"] == "ab" * 32
        assert p["pattern_detail"]["language_basis"].startswith("not_measured")
    search = next(p for p in patterns if p["function_name"] == "GET /api/search")
    upload = next(p for p in patterns if p["function_name"] != "GET /api/search")
    assert search["usage_freq"] == 1.0 and upload["usage_freq"] < search["usage_freq"]


class _Report:
    def __init__(self, status, data, detail=None):
        self.status, self.data, self.detail, self.execution_mode = status, data, detail, "sandboxed"


class _Result:
    def __init__(self, *reports):
        self.reports = reports


def _dispatcher(status, data, seen=None):
    def run(observable_type, value, *, context, analyzers):
        if seen is not None:
            seen.append((observable_type, value, context, analyzers))
        return _Result(_Report(status, data, detail=data.get("reason_detail")))
    return run


def test_an_unavailable_family_is_not_measured_never_empty(tmp_path):
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"PK")
    seen: list = []
    inventory = oi.build_inventory(
        "apk", str(apk), {"authorization_basis": "owned"},
        dispatcher=_dispatcher("unavailable", {"reason": "not_allowlisted", "reason_detail": "JADX",
                                               "body": [], "evidence_ids": None}, seen))
    assert seen[0][0] == "apk" and seen[0][3] == ["rea_apk"]
    assert inventory["features"] is None and inventory["unmapped"] is None
    assert inventory["features_basis"] == "not_run: not_allowlisted"
    assert oi.features_to_patterns(inventory) == []


def test_a_js_summary_alone_names_nothing_so_it_is_not_measured(tmp_path):
    from tools.analyzers import rea_adapter as ra

    (tmp_path / "app").mkdir()
    step = ra.map_response("analyze_javascript_application", {"ok": True, "result": JS_SUMMARY})
    body = [{k: step["record"][k] for k in ("tool", "evidence_id", "normalized_result", "confidence",
                                             "rea_confidence")}]
    inventory = oi.build_inventory(
        "electron_app", str(tmp_path / "app"), {"authorization_basis": "owned"},
        dispatcher=_dispatcher("ok", {"body": body, "evidence_ids": [body[0]["evidence_id"]]}))
    assert inventory["features"] is None
    assert inventory["features_basis"].startswith("not_enumerated")
    assert inventory["surface_counts"]["electron"]["ipc"]["main_handlers"] == 0


def test_an_over_budget_har_is_not_measured_and_says_so(tmp_path):
    har = tmp_path / "s.har"
    har.write_text("{}", encoding="utf-8")
    inventory = oi.build_inventory(
        "har", str(har), {"authorization_basis": "owned", "sensitive_values": []},
        dispatcher=_dispatcher("partial", {"reason": "response_over_budget", "body": [],
                                           "evidence_ids": None}))
    assert inventory["features"] is None
    assert inventory["features_basis"] == "no_evidence_inline: response_over_budget"


# ---------------------------------------------------------------------------
# End to end: run_scan -> dispatch (sandbox path) -> stub REA -> scorer -> roadmap
# ---------------------------------------------------------------------------
@pytest.fixture
def aiify_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))
    import tools.aiify.db.init_db as init_db
    import tools.aiify.engine as engine

    monkeypatch.setattr(init_db, "DB_PATH", tmp_path / "aiify_canvas.db")
    # Board promotion and innovation signals are not this card's subject.
    monkeypatch.setattr(engine, "_promote_top_opportunities", lambda *a, **k: 0)
    monkeypatch.setattr(engine, "_promote_phase_opportunities", lambda *a, **k: 0)
    monkeypatch.setattr(engine, "_register_innovation_signals", lambda *a, **k: 0)
    monkeypatch.setattr(engine, "detect_patterns",
                        lambda path: pytest.fail("an observed input must never reach Semgrep"))
    return engine


class _ExecInProcess:
    """A SandboxExecutor that runs the real driver here (as test_rea_adapter does)."""

    calls: list = []

    def execute(self, *, code, language, **kwargs):
        from icdev.tools.security.sandbox_executor import SandboxResult

        _ExecInProcess.calls.append(language)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(code, "<sandbox-driver>", "exec"), {"__name__": "__sandbox__"})  # noqa: S102
        return SandboxResult(stdout=out.getvalue(), exit_code=0, status="completed")


@pytest.fixture
def stub_rea(tmp_path, monkeypatch):
    """The SHIPPED rea entry, switched on, against the stub stdio server."""
    from icdev.tools.mcp_client import registry as reg
    from icdev.tools.mcp_client import target_auth
    from icdev.tools.security import sandbox_executor

    reg.reset_external_registry()
    root = tmp_path / "node_modules" / "rea-agents"
    (root / "scripts").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": "6.4.0"}),
                                       encoding="utf-8")
    for name in ("stub_rea_server.py", "rea_6.4.0_recorded.json"):
        shutil.copy(FIXTURES / name, root / "scripts" / name)
    monkeypatch.setenv("ICDEV_REA_ENTRY", str(root / "scripts" / "stub_rea_server.py"))
    call_log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("STUB_REA_CALL_LOG", str(call_log))
    config = yaml.safe_load((REPO / "args/external_mcp_servers.yaml").read_text(encoding="utf-8"))
    spec = next(s for s in config["servers"] if s["name"] == "rea")
    spec = {**spec, "command": [sys.executable, "env:ICDEV_REA_ENTRY"], "enabled": True,
            "timeout_seconds": 30.0}
    monkeypatch.setattr(reg, "enabled_servers", lambda: [spec])
    monkeypatch.setattr(target_auth, "audit_recorder", lambda *a: 1)
    _ExecInProcess.calls = []
    monkeypatch.setattr(sandbox_executor, "SandboxExecutor", _ExecInProcess)

    def scenario(**responses):
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps(responses), encoding="utf-8")
        monkeypatch.setenv("STUB_REA_RESPONSES", str(path))

    def calls():
        if not call_log.exists():
            return []
        return [json.loads(x) for x in call_log.read_text(encoding="utf-8").splitlines()]

    yield scenario, calls
    reg.reset_external_registry()


def _rows(scan_id):
    from tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("AIIFY_DB_PATH")
    try:
        scan = dict(conn.execute("SELECT * FROM aiify_scans WHERE scan_id = %s", (scan_id,)).fetchone())
        opps = [dict(r) for r in conn.execute(
            "SELECT o.*, s.composite_score, s.verdict FROM aiify_opportunities o "
            "JOIN aiify_scores s ON s.opportunity_id = o.opportunity_id WHERE o.scan_id = %s",
            (scan_id,)).fetchall()]
        roadmap = dict(conn.execute("SELECT * FROM aiify_roadmaps WHERE scan_id = %s",
                                    (scan_id,)).fetchone())
    finally:
        conn.close()
    return scan, opps, roadmap


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def test_an_electron_app_yields_observed_opportunities_end_to_end(aiify_env, stub_rea, tmp_path):
    scenario, calls = stub_rea
    scenario(analyze_javascript_application=JS_SUMMARY,
             trace_application_feature=_evidence("trace_application_feature", TRACE, "c"))
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.js").write_text("// fixture\n", encoding="utf-8")

    result = aiify_env.run_scan("electron_app", str(app), {
        "il_level": "il4", "authorization_basis": "owned", "feature_seeds": ["export report"]})

    assert result["status"] == "completed" and result["basis"] == "observed"
    assert _ExecInProcess.calls == ["python"], "REA ran on the analyzer sandbox path"
    trace = next(c for c in calls() if c["name"] == "trace_application_feature")
    graph = json.loads(JS_SUMMARY["content"][0]["text"])["normalized_result"]["parent_evidence_id"]
    assert trace["arguments"]["application"] == {"kind": "retained-evidence", "evidence_id": graph}
    assert result["opportunities_count"] == 1

    scan, opps, roadmap = _rows(result["scan_id"])
    assert scan["total_loc"] is None, "no source: LOC is not measured, not zero"
    inventory = _load(scan["language_profile"])["observed_inventory"]
    assert inventory["provenance"]["authorization_basis"] == "owned"
    assert inventory["provenance"]["target_sha256"]
    [opp] = opps
    assert opp["pattern_type"] == "string_template_rendering"
    assert opp["module_path"] == "observed:electron_app:export report"
    assert _load(opp["pattern_detail"])["basis"] == "observed"
    assert opp["composite_score"] is not None and opp["verdict"]
    placed = [o for phase in _load(roadmap["phases"]) for o in phase["opportunities"]]
    assert [(o["basis"], o["function_name"]) for o in placed] == [("observed", "export report")]


def test_a_har_yields_observed_opportunities_end_to_end(aiify_env, stub_rea, tmp_path):
    scenario, calls = stub_rea
    scenario(inspect_web_network_capture=_evidence("inspect_web_network_capture", HAR_RECORDS, "d"))
    har = tmp_path / "session.har"
    har.write_text(json.dumps({"log": {"entries": []}}), encoding="utf-8")

    result = aiify_env.run_scan("har", str(har), {
        "il_level": "il4", "authorization_basis": "licensed", "sensitive_values": ["S3CRET"]})

    assert result["status"] == "completed"
    assert result["opportunities_count"] == 2
    assert result["observed_inventory"]["unmapped"] == ["GET /api/orders/{id}"]
    [har_call] = [c for c in calls() if c["name"] == "inspect_web_network_capture"]
    assert har_call["arguments"]["sensitive_values"] == ["S3CRET"]
    scan, opps, roadmap = _rows(result["scan_id"])
    assert {o["pattern_type"] for o in opps} == {"document_ingestion_pipeline", "fulltext_search_engine"}
    assert all(o["module_path"].startswith("observed:har:") for o in opps)
    placed = [o for phase in _load(roadmap["phases"]) for o in phase["opportunities"]]
    assert placed and {o["basis"] for o in placed} == {"observed"}
    assert "S3CRET" not in json.dumps([scan, opps, roadmap], default=str)


def test_a_live_website_with_allow_execute_is_not_measured(aiify_env, stub_rea):
    _, calls = stub_rea
    result = aiify_env.run_scan("website", "https://app.example/", {
        "il_level": "il4", "authorization_basis": "owned", "allow_execute": True})
    assert result["status"] == "completed" and result["opportunities_count"] == 0
    assert result["observed_inventory"]["features"] is None
    assert result["observed_inventory"]["reason"] == "not_allowlisted"
    assert result["overall_verdict"] == "Not measured" and result["overall_ai_readiness"] is None
    assert calls() == [], "nothing is captured: REA's website family never runs"
