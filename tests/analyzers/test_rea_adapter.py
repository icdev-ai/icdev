# CUI // SP-CTI
"""REA analyzers on the analyzer contract (rea-wire-02, project card ``rea``).

What is pinned here:

* seven declarations, one per family, all ``sandboxed`` and all reporting their
  own outcome through ``status_key``; the six new observable types are in the
  CLOSED vocabulary and ``analyzer_capabilities`` lists them;
* REA's evidence record maps the house way: ``normalized_result`` is the body,
  ``evidence_id`` is kept, REA's ``[]`` is a MEASURED empty list and anything
  not measured is ``None`` with a ``*_basis`` -- never ``[]``;
* ``truncated`` and an over-budget answer are ``partial``; a missing engine is
  ``unavailable``; none of them is folded into ``ok``;
* the native provider is chosen by the rea-eng-01 license gate, never by REA;
* no authorization basis -> nothing reaches REA;
* ``analyzer_dispatch`` runs the adapter through ``run_sandboxed`` and refuses
  when the sandbox cannot run.

CI needs neither Node nor Ghidra: the stub stdio server replays responses REA
6.4.0 returned to rea-spike-01 (``tests/fixtures/rea/``).
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

from icdev.tools.analyzers import rea_adapter as ra
from icdev.tools.mcp_client import client as transport_mod
from icdev.tools.mcp_client import registry as reg
from icdev.tools.mcp_client import target_auth

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "rea"
RECORDED = json.loads((FIXTURES / "rea_6.4.0_adapter_responses.json").read_text(encoding="utf-8"))
NEW_TYPES = ("dotnet_assembly", "js_bundle", "apk", "wasm", "har", "website")
REA_KEYS = ("rea_native", "rea_dotnet", "rea_javascript", "rea_har", "rea_apk", "rea_wasm",
            "rea_website")


def _response(key: str) -> dict:
    return RECORDED["responses"][key]["result"]


def _rea_spec() -> dict:
    config = yaml.safe_load((REPO / "args/external_mcp_servers.yaml").read_text(encoding="utf-8"))
    return next(s for s in config["servers"] if s["name"] == "rea")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reg.reset_external_registry()
    monkeypatch.delenv(ra.ENV_AUTHORIZATION, raising=False)
    yield
    reg.reset_external_registry()


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """The SHIPPED rea entry, switched on, against the stub; audit rows captured."""
    root = tmp_path / "node_modules" / "rea-agents"
    (root / "scripts").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": "6.4.0"}),
                                       encoding="utf-8")
    for name in ("stub_rea_server.py", "rea_6.4.0_recorded.json"):
        shutil.copy(FIXTURES / name, root / "scripts" / name)
    entry = root / "scripts" / "stub_rea_server.py"
    call_log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("ICDEV_REA_ENTRY", str(entry))
    monkeypatch.setenv("STUB_REA_CALL_LOG", str(call_log))
    spec = {**_rea_spec(), "command": [sys.executable, "env:ICDEV_REA_ENTRY"], "enabled": True,
            "timeout_seconds": 30.0}
    monkeypatch.setattr(reg, "enabled_servers", lambda: [spec])

    rows: list = []
    monkeypatch.setattr(target_auth, "audit_recorder",
                        lambda event, action, details, classification: rows.append((event, details)) or 1)

    class Stub:
        pass

    s = Stub()
    s.tmp, s.rows = tmp_path, rows
    s.calls = lambda: ([json.loads(x) for x in call_log.read_text(encoding="utf-8").splitlines()]
                       if call_log.exists() else [])

    def scenario(**responses):
        path = tmp_path / "scenario.json"
        path.write_text(json.dumps(responses), encoding="utf-8")
        monkeypatch.setenv("STUB_REA_RESPONSES", str(path))

    s.scenario = scenario
    s.pe = tmp_path / "fixture.exe"
    s.pe.write_bytes(b"MZ icdev-owned rea-wire-02 fixture")
    return s


def _assert_never_empty_unmeasured(report: dict) -> None:
    """The house rule: a list nobody measured is None, never []."""
    for key in ("limitations", "residual_unknowns"):
        if report[key] is not None and report[key] == []:
            assert report[f"{key}_basis"] == ra.BASIS_REPORTED, (key, report[f"{key}_basis"])
        if report[f"{key}_basis"] != ra.BASIS_REPORTED:
            assert report[key] is None, (key, report[key])


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_new_observable_types_are_in_the_closed_vocabulary():
    from icdev.tools.analyzers.contract import load_contract

    contract = load_contract()
    for name in NEW_TYPES:
        assert name in contract.observable_types
        assert "tools/analyzers/rea_adapter.py" in contract.observable_types[name].consumers


def test_every_rea_declaration_is_sandboxed_and_reports_its_own_status():
    from icdev.tools.analyzers.contract import load_contract

    contract = load_contract()
    accepted = set()
    for key in REA_KEYS:
        decl = contract.get(key)
        assert decl is not None, key
        assert decl.module == "tools.analyzers.rea_adapter" and decl.entrypoint == "analyze"
        assert decl.sandbox == "sandboxed"
        assert decl.status_key == "status"
        assert decl.taxonomy.levels == ("info",), "an RE structure report derives no verdict"
        assert dict(decl.binding.static_args)["family"] in ra.FAMILIES
        accepted.update(decl.accepts)
    assert accepted == set(NEW_TYPES) | {"binary"}


def test_contract_cli_validates():
    from icdev.tools.analyzers import contract

    assert contract.main(["--validate"]) == 0


def test_analyzer_capabilities_lists_them():
    from icdev.tools.analyzers.dispatch import capabilities

    caps = capabilities()
    assert "partial" in caps["statuses"]
    for name in NEW_TYPES:
        keys = {a["key"] for a in caps["observable_types"][name]["analyzers"]}
        assert keys & set(REA_KEYS), name
    native = {a["key"]: a for a in caps["observable_types"]["binary"]["analyzers"]}["rea_native"]
    assert native["execution_mode"] == "sandboxed"


def test_a_status_key_value_outside_the_lifted_set_is_misdeclared():
    from icdev.tools.analyzers.contract import load_contract
    from icdev.tools.analyzers.dispatch import _lifted_status

    decl = load_contract().get("rea_wasm")
    assert _lifted_status(decl, {"status": "ok"}) == ("ok", None)
    assert _lifted_status(decl, {"status": "truncated", "reason": "r"})[0] == "partial"
    assert _lifted_status(decl, {"status": "refused"})[0] == "misdeclared"
    assert _lifted_status(decl, "not a mapping")[0] == "misdeclared"


# ---------------------------------------------------------------------------
# Families that never run
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("family,reason", [("wasm", "rea_no_wasm_analysis"),
                                           ("apk", "not_allowlisted"),
                                           ("website", "not_allowlisted")])
def test_an_unmeasured_family_is_unavailable_and_runs_nothing(stub, family, reason):
    report = ra.analyze(str(stub.pe), family=family, authorization_basis="owned")
    assert report["status"] == "unavailable"
    assert report["reason"] == reason
    assert report["body"] == [] and report["evidence_ids"] is None
    assert report["limitations"] is None and report["limitations_basis"] == ra.BASIS_NOT_RUN
    assert report["residual_unknowns"] is None
    assert stub.calls() == []


# ---------------------------------------------------------------------------
# Rails: authorization, license gate, availability
# ---------------------------------------------------------------------------
def test_no_authorization_basis_refuses_before_rea(stub):
    report = ra.analyze(str(stub.pe), family="dotnet_assembly")
    assert report["status"] == "error"
    assert report["reason"] == "authorization_missing"
    assert report["provenance"]["target_sha256"] == target_auth.target_digest(stub.pe)
    assert report["provenance"]["authorization_basis"] is None
    assert stub.calls() == [] and stub.rows == []


def test_the_env_basis_is_honoured(stub, monkeypatch):
    monkeypatch.setenv(ra.ENV_AUTHORIZATION, "licensed")
    report = ra.analyze(str(stub.pe), family="dotnet_assembly")
    assert report["status"] == "ok"
    assert report["provenance"]["authorization_basis"] == "licensed"


def test_an_unreadable_target_is_an_error_not_a_finding(stub):
    report = ra.analyze(str(stub.tmp / "absent.exe"), family="binary", authorization_basis="owned")
    assert report["status"] == "error" and report["reason"] == "source_unreadable"
    assert stub.calls() == []


def test_the_license_gate_chooses_the_provider(stub, monkeypatch):
    asked = []
    real = ra._license_gate
    monkeypatch.setattr(ra, "_license_gate", lambda engine: asked.append(engine) or real(engine))
    stub.scenario(open_binary=_response("native_pe.open_binary"),
                  binary_overview=_response("native_pe.binary_overview"),
                  list_unknowns=_response("native_pe.list_unknowns"))
    report = ra.analyze(str(stub.pe), family="binary", authorization_basis="owned")
    assert asked == ["ghidra"]
    opened = next(c for c in stub.calls() if c["name"] == "open_binary")
    assert opened["arguments"]["provider_id"] == "ghidra", "never `auto`: REA must not pick"
    assert report["engine"]["allowed"] is True
    assert report["provenance"]["engine"]["engine"] == "ghidra"


def test_a_refused_engine_is_unavailable_and_never_reaches_rea(stub, monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_RE_LICENSES", str(tmp_path / "no-declaration.yaml"))
    report = ra.analyze(str(stub.pe), family="binary", engine="ida", authorization_basis="owned")
    assert report["status"] == "unavailable"
    assert report["reason"] in ("undeclared", "platform_unsupported")
    assert report["engine"]["allowed"] is False
    assert stub.calls() == []


def test_shipped_config_is_unavailable_not_ok(tmp_path):
    target = tmp_path / "a.dll"
    target.write_bytes(b"MZ")
    report = ra.analyze(str(target), family="dotnet_assembly", authorization_basis="owned")
    assert report["status"] == "unavailable" and report["reason"] == "rea_not_enabled"
    assert report["containment"]["write_sandbox"]["reason"]


def test_enabled_but_not_installed_is_unavailable(stub, monkeypatch):
    monkeypatch.delenv("ICDEV_REA_ENTRY")
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned")
    assert report["status"] == "unavailable" and report["reason"] == "rea_not_connected"


def test_an_unwritable_provenance_row_is_an_error(stub, monkeypatch):
    def broken(*_args):
        raise RuntimeError("audit_trail unreachable")

    monkeypatch.setattr(target_auth, "audit_recorder", broken)
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned")
    assert report["status"] == "error" and report["reason"] == "provenance_unwritable"
    assert stub.calls() == []


# ---------------------------------------------------------------------------
# Evidence mapping, over REA 6.4.0's own responses
# ---------------------------------------------------------------------------
def test_native_pe_maps_evidence_and_measured_unknowns(stub):
    stub.scenario(open_binary=_response("native_pe.open_binary"),
                  binary_overview=_response("native_pe.binary_overview"),
                  list_unknowns=_response("native_pe.list_unknowns"))
    report = ra.analyze(str(stub.pe), family="binary", authorization_basis="owned", actor="op1")
    assert report["status"] == "ok", report["reason_detail"]
    [entry] = report["body"]
    assert entry["tool"] == "binary_overview" and entry["basis"] == "observed"
    assert entry["evidence_id"].startswith("ev_") and report["evidence_ids"] == [entry["evidence_id"]]
    assert entry["normalized_result"]["procedure_count"] == 580
    assert report["limitations"] == ["Derived by an REA composed workflow."]
    # list_unknowns answered {"items": [], "total": 0}: MEASURED, none.
    assert report["residual_unknowns"] == []
    assert report["residual_unknowns_basis"] == ra.BASIS_REPORTED
    assert report["truncated"] is False
    assert {"predicate": "format", "level": "info", "value": "pe"} in report["taxonomy"]
    assert report["provenance"]["authorization_basis"] == "owned"
    assert stub.rows and all(e == target_auth.EVENT_AUTHORIZED for e, _ in stub.rows)
    assert stub.rows[0][1]["targets"][0]["operator"] == "op1"
    _assert_never_empty_unmeasured(report)


def test_pseudocode_is_decompiled_never_recovered_source(stub):
    stub.scenario(open_binary=_response("native_pe.open_binary"),
                  binary_overview=_response("native_pe.binary_overview"),
                  procedure_pseudo_code=_response("native_pe.procedure_pseudo_code"),
                  list_unknowns=_response("native_pe.list_unknowns"))
    report = ra.analyze(str(stub.pe), family="binary", procedure="FUN_140001060",
                        authorization_basis="owned")
    assert report["status"] == "ok"
    code = next(e for e in report["body"] if e["tool"] == "procedure_pseudo_code")
    assert code["basis"] == "decompiled"
    assert code["confidence"] == ra.DECOMPILED_CONFIDENCE
    assert code["rea_confidence"] == "observed", "REA's word is kept, but only as rea_confidence"
    assert "FUN_140001060" in code["normalized_result"]
    assert any(t["predicate"] == "decompiled" for t in report["taxonomy"])


def test_a_missing_engine_is_unavailable_never_ok(stub):
    # Windows ELF: REA opens it, then every provider rejects it (rea-spike-01).
    stub.scenario(open_binary=_response("native_elf_windows.open_binary"),
                  binary_overview=_response("native_elf_windows.binary_overview"))
    report = ra.analyze(str(stub.pe), family="binary", authorization_basis="owned")
    assert report["status"] == "unavailable"
    assert report["reason"] == "engine_unavailable"
    assert report["rea_errors"][0]["code"] == "provider_unavailable"
    assert report["body"] == [] and report["evidence_ids"] is None
    assert report["limitations"] is None and report["limitations_basis"] == "run_failed"
    assert report["residual_unknowns"] is None
    _assert_never_empty_unmeasured(report)


def test_a_format_rea_cannot_open_is_unavailable(stub):
    stub.scenario(open_binary=_response("wasm.open_binary"))
    report = ra.analyze(str(stub.pe), family="binary", authorization_basis="owned")
    assert report["status"] == "unavailable"
    assert report["reason"] == "rea_target_unavailable"


def test_dotnet_unknowns_rea_did_not_report_are_none(stub):
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned")
    assert report["status"] == "ok"
    assert report["body"][0]["normalized_result"]["artifact"]["format"] == "pe"
    assert report["limitations"]
    assert report["residual_unknowns"] is None
    assert report["residual_unknowns_basis"] == ra.BASIS_NOT_REPORTED
    _assert_never_empty_unmeasured(report)


def test_truncated_is_partial(stub):
    # No recorded REA answer was truncated, so the recorded .NET record gets ONE
    # stated mutation: the flag REA sets when it stops early.
    recorded = json.loads((FIXTURES / "rea_6.4.0_recorded.json").read_text(encoding="utf-8"))
    body = json.loads(recorded["responses"]["inspect_managed_artifact"]["content"][0]["text"])
    body["normalized_result"]["coverage"]["truncated"] = True
    stub.scenario(inspect_managed_artifact={"content": [{"type": "text", "text": json.dumps(body)}]})
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned")
    assert report["status"] == "partial" and report["reason"] == "truncated"
    assert report["truncated"] is True and report["evidence_ids"]


def test_js_bundle_directory_reports_reas_unknowns(stub):
    dist = stub.tmp / "dist"
    dist.mkdir()
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    stub.scenario(analyze_javascript_application=_response(
        "js_bundle.analyze_javascript_application"))
    report = ra.analyze(str(dist), family="js_bundle", authorization_basis="owned")
    assert report["status"] == "ok"
    call = stub.calls()[0]
    assert call["arguments"]["detail"] == "summary" and call["arguments"]["format"] == "directory"
    assert report["residual_unknowns_basis"] == ra.BASIS_REPORTED
    assert report["residual_unknowns"][0].startswith("1654 semantic graph unknowns")


def test_har_needs_sensitive_values(stub):
    har = stub.tmp / "s.har"
    har.write_text("{}", encoding="utf-8")
    report = ra.analyze(str(har), family="har", authorization_basis="owned")
    assert report["status"] == "error" and report["reason"] == "sensitive_values_required"
    assert stub.calls() == []


def test_har_over_budget_is_partial_and_keeps_the_evidence_id(stub):
    har = stub.tmp / "s.har"
    har.write_text("{}", encoding="utf-8")
    stub.scenario(inspect_web_network_capture=_response("har.inspect_web_network_capture"))
    report = ra.analyze(str(har), family="har", sensitive_values=["csrf-123"],
                        authorization_basis="owned")
    assert report["status"] == "partial" and report["reason"] == "response_over_budget"
    assert "ev_7a4fa24a9dd7" in report["reason_detail"]
    assert stub.calls()[0]["arguments"]["sensitive_values"] == ["csrf-123"]
    assert report["limitations"] is None


# ---------------------------------------------------------------------------
# analyzer_dispatch, through the sandbox path
# ---------------------------------------------------------------------------
class _ExecInProcess:
    """A SandboxExecutor that runs the real driver here, as an image carrying
    ICDEV + Node + REA would. Only the container is faked; the marshalling is
    run_sandboxed's own."""

    calls: list = []

    def execute(self, *, code, language, **kwargs):
        from icdev.tools.security.sandbox_executor import SandboxResult

        _ExecInProcess.calls.append(language)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(code, "<sandbox-driver>", "exec"), {"__name__": "__sandbox__"})  # noqa: S102
        return SandboxResult(stdout=out.getvalue(), exit_code=0, status="completed")


class _NoRuntime:
    def execute(self, **kwargs):
        from icdev.tools.security.sandbox_executor import SandboxResult

        return SandboxResult(status="unavailable", error="no container runtime")


def _limiter():
    from icdev.tools.analyzers.rate_limit import AnalyzerRateLimiter

    return AnalyzerRateLimiter()


def test_dispatch_runs_rea_through_the_sandbox(stub, monkeypatch):
    from icdev.tools.analyzers.dispatch import dispatch
    from icdev.tools.security import sandbox_executor

    _ExecInProcess.calls = []
    monkeypatch.setattr(sandbox_executor, "SandboxExecutor", _ExecInProcess)
    result = dispatch("dotnet_assembly", str(stub.pe),
                      context={"authorization_basis": "owned", "actor": "op1"},
                      limiter=_limiter())
    [report] = result.reports
    assert report.analyzer == "rea_dotnet"
    assert report.status == "ok", report.detail
    assert report.execution_mode == "sandboxed" and _ExecInProcess.calls == ["python"]
    assert report.data["evidence_ids"][0].startswith("ev_")
    assert report.taxonomy_defects == ()
    assert {t["predicate"] for t in report.taxonomy} >= {"evidence", "format"}
    assert result.partial is False


def test_dispatch_reports_unavailable_not_ok(monkeypatch, tmp_path):
    from icdev.tools.analyzers.dispatch import dispatch
    from icdev.tools.security import sandbox_executor

    monkeypatch.setattr(sandbox_executor, "SandboxExecutor", _ExecInProcess)
    target = tmp_path / "m.wasm"
    target.write_bytes(b"\0asm")
    result = dispatch("wasm", str(target), limiter=_limiter())
    [report] = result.reports
    assert report.status == "unavailable"
    assert "rea_no_wasm_analysis" in report.detail
    assert result.partial is True and result.partial_reasons == {"unavailable": ["rea_wasm"]}


def test_dispatch_fails_closed_without_a_sandbox(stub, monkeypatch):
    from icdev.tools.analyzers.dispatch import dispatch
    from icdev.tools.security import sandbox_executor

    monkeypatch.setattr(sandbox_executor, "SandboxExecutor", _NoRuntime)
    result = dispatch("binary", str(stub.pe), analyzers=["rea_native"],
                      context={"authorization_basis": "owned"}, limiter=_limiter())
    [report] = result.reports
    assert report.status == "sandbox_unavailable"
    assert stub.calls() == [], "never downgraded to an in-process REA run"


# ---------------------------------------------------------------------------
# Containment plumbing and the coverage decision
# ---------------------------------------------------------------------------
def test_the_l3_prefix_wraps_the_stdio_command(monkeypatch):
    monkeypatch.setenv("ICDEV_REA_ENTRY", "/opt/rea/scripts/rea.mjs")
    transport = transport_mod.build_transport(_rea_spec(), command_prefix=["bwrap", "--"])
    assert transport._command == ["bwrap", "--", "node", "/opt/rea/scripts/rea.mjs", "mcp"]
    assert reg.ExternalToolRegistry(command_prefix=["x"]).command_prefix == ["x"]


def test_containment_is_reported_on_every_run(stub):
    report = ra.analyze(str(stub.pe), family="dotnet_assembly", authorization_basis="owned")
    l3 = report["containment"]["write_sandbox"]
    assert set(l3) == {"applied", "backend", "reason"} and l3["reason"]
    assert report["containment"]["sandbox_posture"] == "sandboxed"


def test_sandbox_coverage_records_rea_as_sandboxed():
    doc = (REPO / "docs/security/sandbox-coverage.md").read_text(encoding="utf-8")
    gap = doc.split("### Gap 73", 1)[1].split("\n### ", 1)[0]
    assert "tools/analyzers/rea_adapter.py" in gap
    assert "**Decision:** **sandboxed**" in gap
