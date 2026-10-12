# CUI // SP-CTI
"""REA registered as an external MCP server (rea-wire-01, project card `rea`).

What is pinned here, and why each matters:

* the shipped entry is OFF, exactly pinned to the rea-spike-01 version, and
  launched from an installed path (never ``npx`` / ``@latest``);
* its allowlist is read-only by REA's OWN declared effects, recorded from 6.4.0;
* it is absent when either switch is off, under air-gap and on a shared
  deployment;
* a call naming a target with no operator authorization basis is refused BEFORE
  the server is reached, and every call leaves a provenance row;
* ``pin_census`` sees the pin.

CI needs neither Node nor Ghidra: ``tests/fixtures/rea/stub_rea_server.py``
replays responses recorded from the real rea-agents 6.4.0.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from icdev.tools.mcp_client import client as transport_mod
from icdev.tools.mcp_client import registry as reg
from icdev.tools.mcp_client import target_auth

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "rea"
RECORDED = json.loads((FIXTURES / "rea_6.4.0_recorded.json").read_text(encoding="utf-8"))

#: Effects that make a tool something other than read-only analysis, whatever
#: else it declares.
FORBIDDEN_EFFECTS = {"mutatesTarget", "accessesNetwork", "changesUiState"}
#: Effects allowed only for a tool named in the entry's `write_exceptions`.
WRITE_EFFECTS = {"writesFilesystem", "mayDiscardData"}
#: Families excluded by NAME, whatever their declared effects.
FORBIDDEN_NAME = re.compile(
    r"adb|evm|jeb|capture_|observe_|install|setup|update|^set_|annotate|import_|close_"
)


def _shipped() -> dict:
    return yaml.safe_load((REPO / "args/external_mcp_servers.yaml").read_text(encoding="utf-8"))


def _rea(config: dict | None = None) -> dict:
    servers = (config or _shipped())["servers"]
    return next(s for s in servers if s["name"] == "rea")


@pytest.fixture(autouse=True)
def _clean_registry():
    reg.reset_external_registry()
    yield
    reg.reset_external_registry()


# ---------------------------------------------------------------------------
# The shipped entry
# ---------------------------------------------------------------------------
def test_rea_is_registered_off_by_default():
    config = _shipped()
    assert config["enabled"] is False, "the master switch must stay off"
    assert _rea(config)["enabled"] is False
    assert all(s.get("enabled") is False for s in config["servers"])


def test_rea_is_pinned_to_the_spiked_version():
    package = _rea()["package"]
    assert package["name"] == "rea-agents"
    assert package["version"] == "6.4.0"
    assert reg._EXACT_VERSION.match(package["version"])
    # The integrity is the one rea-spike-01 measured, not a second guess.
    spike = (REPO / "docs/cards/rea-spike-01.md").read_text(encoding="utf-8")
    assert package["integrity"] in spike


def test_rea_launches_from_an_installed_path():
    command = _rea()["command"]
    assert command == ["node", "env:ICDEV_REA_ENTRY", "mcp"]
    assert not any("npx" in c or "@latest" in c or "@" in c for c in command)


def test_rea_carries_every_rail():
    entry = _rea()
    assert entry["transport"] == "stdio"
    assert entry["single_user_only"] is True
    auth = entry["authorization"]
    assert auth["required"] is True
    assert set(auth["bases"]) == set(target_auth.DEFAULT_BASES)
    assert "export_evidence_bundle" in auth["non_target_tools"]
    assert entry["classification_ceiling"] in reg._CLASSIFICATION_ORDER


def test_allowlist_is_read_only_by_reas_own_declared_effects():
    entry = _rea()
    effects = RECORDED["effects"]
    assert len(effects) == 170, "the recorded effect audit covers REA 6.4.0's whole catalog"
    exceptions = set(entry["write_exceptions"])
    assert exceptions <= set(entry["tools"])
    for tool in entry["tools"]:
        assert tool in effects, f"{tool} is not a REA 6.4.0 tool"
        declared = set(effects[tool])
        assert not declared & FORBIDDEN_EFFECTS, (tool, declared & FORBIDDEN_EFFECTS)
        if tool not in exceptions:
            assert not declared & WRITE_EFFECTS, (tool, declared & WRITE_EFFECTS)
        assert not FORBIDDEN_NAME.search(tool), tool


def test_allowlist_excludes_wasm_and_the_capture_tools():
    tools = set(_rea()["tools"])
    for never in ("install_adb_package", "push_adb_file", "set_comment", "inspect_evm_interface",
                  "capture_process_scenario", "capture_browser_scenario", "observe_native_calls",
                  "recover_javascript_sources", "extract_firmware"):
        assert never not in tools


def test_every_bundle_tool_is_on_the_server_allowlist():
    bundles = yaml.safe_load((REPO / "args/agent_toolsets.yaml").read_text(encoding="utf-8"))["bundles"]
    bundle = bundles["reverse_engineering"]
    assert bundle["mutating"] is True
    allow = set(_rea()["tools"])
    names = bundle["tools"]
    assert names and all(n.startswith("ext__rea__") for n in names)
    assert {n[len("ext__rea__"):] for n in names} <= allow


def test_packaged_copies_match():
    for rel in ("external_mcp_servers.yaml", "agent_toolsets.yaml", "pin_gate.yaml"):
        assert (REPO / "args" / rel).read_bytes() == (REPO / "icdev/data/args" / rel).read_bytes(), rel


# ---------------------------------------------------------------------------
# Absent when off, under air-gap, on a shared deployment
# ---------------------------------------------------------------------------
def _config_with(monkeypatch, *, master: bool, entry: bool) -> None:
    config = _shipped()
    config["enabled"] = master
    _rea(config)["enabled"] = entry
    monkeypatch.setattr(reg, "load_config", lambda: config)
    monkeypatch.setattr(transport_mod, "_airgap_blocks", lambda: False)
    monkeypatch.setattr(reg, "_shared_deployment", lambda: False)


def _names() -> list[str]:
    return [s["name"] for s in reg.enabled_servers()]


def test_absent_with_the_shipped_config():
    assert "rea" not in _names()


@pytest.mark.parametrize("master,entry", [(False, False), (False, True), (True, False)])
def test_absent_unless_both_switches_are_on(monkeypatch, master, entry):
    _config_with(monkeypatch, master=master, entry=entry)
    assert "rea" not in _names()


def test_present_when_both_switches_are_on(monkeypatch):
    _config_with(monkeypatch, master=True, entry=True)
    assert _names() == ["rea"]


def test_absent_under_air_gap(monkeypatch):
    _config_with(monkeypatch, master=True, entry=True)
    monkeypatch.setattr(transport_mod, "_airgap_blocks", lambda: True)
    assert _names() == []


def test_absent_on_a_shared_deployment(monkeypatch):
    _config_with(monkeypatch, master=True, entry=True)
    monkeypatch.setattr(reg, "_shared_deployment", lambda: True)
    assert _names() == []


def test_shared_deployment_reads_the_existing_signals(monkeypatch):
    monkeypatch.delenv("ICDEV_SAAS_MODE", raising=False)
    monkeypatch.delenv("ICDEV_MULTI_TENANT", raising=False)
    assert reg._shared_deployment() is False
    monkeypatch.setenv("ICDEV_SAAS_MODE", "true")
    assert reg._shared_deployment() is True


# ---------------------------------------------------------------------------
# The pin is checked against what is INSTALLED
# ---------------------------------------------------------------------------
def _install(tmp_path: Path, version: str = "6.4.0") -> Path:
    """A fake installed rea-agents whose entry is the stub server."""
    root = tmp_path / "node_modules" / "rea-agents"
    (root / "scripts").mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "rea-agents", "version": version}),
                                       encoding="utf-8")
    shutil.copy(FIXTURES / "stub_rea_server.py", root / "scripts" / "stub_rea_server.py")
    shutil.copy(FIXTURES / "rea_6.4.0_recorded.json", root / "scripts" / "rea_6.4.0_recorded.json")
    return root / "scripts" / "stub_rea_server.py"


def test_pin_holds_for_the_pinned_install(tmp_path):
    entry = _install(tmp_path)
    assert reg.pin_problem(_rea(), ["node", str(entry), "mcp"]) is None


def test_pin_refuses_a_different_installed_version(tmp_path):
    entry = _install(tmp_path, version="6.5.0")
    problem = reg.pin_problem(_rea(), ["node", str(entry), "mcp"])
    assert problem and "6.5.0" in problem


def test_pin_refuses_a_registry_resolved_launch():
    assert reg.pin_problem(_rea(), ["npx", "-y", "rea-agents@latest", "mcp"])


def test_pin_refuses_a_range():
    spec = {**_rea(), "package": {"name": "rea-agents", "version": "^6.4.0"}}
    assert "exact" in reg.pin_problem(spec, [])


def test_unset_launch_path_spawns_nothing(monkeypatch):
    monkeypatch.delenv("ICDEV_REA_ENTRY", raising=False)
    assert transport_mod.resolve_command(_rea()) is None
    assert transport_mod.build_transport(_rea()) is None


# ---------------------------------------------------------------------------
# pin_census sees the pin
# ---------------------------------------------------------------------------
def test_pin_census_sees_the_rea_pin():
    from tools.ci import pin_census as pc

    report = pc.build_report()
    pins = [p for p in report["mcp_server_pins"] if p["server"] == "rea"]
    assert pins == [{
        "file": "args/external_mcp_servers.yaml", "server": "rea",
        "package": "rea-agents", "version": "6.4.0",
        "integrity": _rea()["package"]["integrity"], "enabled": False,
    }]
    assert not [s for s in report["unregistered"] if s["kind"] == pc.KIND_MCP]


@pytest.mark.parametrize("command,version", [
    (["npx", "-y", "rea-agents", "mcp"], "6.4.0"),
    (["node", "rea-agents@latest", "mcp"], "6.4.0"),
    (["node", "env:ICDEV_REA_ENTRY", "mcp"], "^6.4.0"),
    (["node", "env:ICDEV_REA_ENTRY", "mcp"], ""),
])
def test_pin_census_flags_an_unpinned_mcp_server(command, version):
    from tools.ci import pin_census as pc

    text = yaml.safe_dump({"servers": [{
        "name": "loose", "transport": "stdio", "command": command,
        "package": {"name": "rea-agents", "version": version}, "enabled": False,
    }]})
    sites, pins, problems = pc.scan_mcp_servers("args/external_mcp_servers.yaml", text)
    assert [s["key"] for s in sites] == [f"args/external_mcp_servers.yaml::{pc.KIND_MCP}::loose"]
    assert pins == [] and problems == []


def test_pin_census_reports_an_unparseable_registry_as_unmeasurable():
    from tools.ci import pin_census as pc

    _, _, problems = pc.scan_mcp_servers("args/external_mcp_servers.yaml", "servers: [")
    assert problems and "NOT measured" in problems[0]


# ---------------------------------------------------------------------------
# Against the stub server: allowlist, authorization basis, provenance
# ---------------------------------------------------------------------------
@pytest.fixture
def rea_stub(tmp_path, monkeypatch):
    """The SHIPPED rea entry, switched on, launched against the stub."""
    entry = _install(tmp_path)
    call_log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("ICDEV_REA_ENTRY", str(entry))
    monkeypatch.setenv("STUB_REA_CALL_LOG", str(call_log))
    spec = {**_rea(), "command": [sys.executable, "env:ICDEV_REA_ENTRY"], "enabled": True,
            "timeout_seconds": 30.0}
    monkeypatch.setattr(reg, "enabled_servers", lambda: [spec])

    registry = reg.get_external_registry()
    rows: list[tuple[str, dict]] = []
    registry.recorder = lambda event, action, details, classification: rows.append((event, details)) or 1

    target = tmp_path / "Fixture.dll"
    target.write_bytes(b"MZ icdev-owned rea-wire-01 fixture")

    class Stub:
        pass

    stub = Stub()
    stub.registry, stub.rows, stub.target, stub.call_log = registry, rows, target, call_log
    stub.calls = lambda: ([json.loads(x) for x in call_log.read_text(encoding="utf-8").splitlines()]
                          if call_log.exists() else [])
    return stub


def test_only_allowlisted_tools_are_discovered(rea_stub):
    names = {t["name"] for t in rea_stub.registry.list_tools()}
    assert names == {f"ext__rea__{t}" for t in _rea()["tools"]}
    for never in ("install_adb_package", "set_comment", "capture_process_scenario",
                  "inspect_evm_interface"):
        assert f"ext__rea__{never}" not in names


def test_agent_discovery_sees_the_namespaced_tools(rea_stub):
    from tools.agent_runtime import discovery

    specs = discovery.discover_external_tools()
    assert {s.name for s in specs} == {f"ext__rea__{t}" for t in _rea()["tools"]}
    assert all(s.source == "external" for s in specs)


def test_a_target_with_no_authorization_basis_is_refused(rea_stub):
    result = rea_stub.registry.call("ext__rea__inspect_managed_artifact",
                                    {"path": str(rea_stub.target)}, classification="CUI")
    assert result["ok"] is False
    assert result["refused"] == "no_authorization_basis"
    assert rea_stub.calls() == [], "a refused call must never reach the server"
    event, details = rea_stub.rows[-1]
    assert event == target_auth.EVENT_REFUSED
    assert details["refused_target_sha256"] == target_auth.target_digest(rea_stub.target)
    assert details["targets"] is None


def test_a_session_call_before_any_authorized_open_is_refused(rea_stub):
    result = rea_stub.registry.call("ext__rea__binary_overview", {}, classification="CUI")
    assert result["refused"] == "no_authorized_target_open"
    assert rea_stub.calls() == []


def test_an_authorized_target_is_analysed_and_recorded(rea_stub):
    grant = rea_stub.registry.grant_target("rea", rea_stub.target, basis="owned", operator="op1")
    result = rea_stub.registry.call("ext__rea__inspect_managed_artifact",
                                    {"path": str(rea_stub.target)}, classification="CUI")
    assert result["ok"] is True
    assert result["authorization"] == [{"target_sha256": grant["target_sha256"], "basis": "owned"}]
    text = result["result"]["content"][0]["text"]
    assert json.loads(text)["evidence_id"].startswith("ev_")
    assert [c["name"] for c in rea_stub.calls()] == ["inspect_managed_artifact"]

    event, details = rea_stub.rows[-1]
    assert event == target_auth.EVENT_AUTHORIZED
    assert details["targets"][0]["target_sha256"] == grant["target_sha256"]
    assert details["targets"][0]["basis"] == "owned"
    assert details["targets"][0]["operator"] == "op1"

    # A later call on the open session is allowed, and cites the opened target.
    follow = rea_stub.registry.call("ext__rea__list_unknowns", {}, classification="CUI")
    assert follow["ok"] is True
    assert rea_stub.rows[-1][1]["targets"][0]["target_sha256"] == grant["target_sha256"]


def test_the_grant_names_bytes_not_a_path(rea_stub):
    rea_stub.registry.grant_target("rea", rea_stub.target, basis="licensed")
    rea_stub.target.write_bytes(b"MZ swapped after the grant")
    result = rea_stub.registry.call("ext__rea__inspect_managed_artifact",
                                    {"path": str(rea_stub.target)}, classification="CUI")
    assert result["refused"] == "no_authorization_basis"
    assert rea_stub.calls() == []


def test_the_basis_is_never_read_from_tool_arguments(rea_stub):
    result = rea_stub.registry.call(
        "ext__rea__inspect_managed_artifact",
        {"path": str(rea_stub.target), "basis": "owned", "authorization_basis": "owned"},
        classification="CUI",
    )
    assert result["refused"] == "no_authorization_basis"


def test_an_unknown_basis_cannot_be_granted(rea_stub):
    with pytest.raises(ValueError):
        rea_stub.registry.grant_target("rea", rea_stub.target, basis="found-it-online")


def test_an_unreadable_target_is_refused(rea_stub, tmp_path):
    result = rea_stub.registry.call("ext__rea__open_binary", {"path": str(tmp_path / "absent.exe")},
                                    classification="CUI")
    assert result["refused"] == "target_unreadable"


def test_a_call_whose_provenance_cannot_be_written_is_refused(rea_stub):
    rea_stub.registry.grant_target("rea", rea_stub.target, basis="customer-authorized")

    def broken(*_args):
        raise RuntimeError("audit_trail unreachable")

    rea_stub.registry.recorder = broken
    result = rea_stub.registry.call("ext__rea__inspect_managed_artifact",
                                    {"path": str(rea_stub.target)}, classification="CUI")
    assert result["refused"] == "provenance_unwritable"
    assert rea_stub.calls() == []


def test_the_export_path_is_an_output_not_a_target(rea_stub, tmp_path):
    rea_stub.registry.grant_target("rea", rea_stub.target, basis="owned")
    rea_stub.registry.call("ext__rea__inspect_managed_artifact", {"path": str(rea_stub.target)},
                           classification="CUI")
    result = rea_stub.registry.call("ext__rea__export_evidence_bundle",
                                    {"path": str(tmp_path / "bundle.json")}, classification="CUI")
    assert result["ok"] is True
    assert rea_stub.rows[-1][0] == target_auth.EVENT_AUTHORIZED


def test_a_directory_target_is_digested_as_a_tree(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "app.js").write_text("x", encoding="utf-8")
    first = target_auth.target_digest(dist)
    (dist / "app.js.map").write_text("{}", encoding="utf-8")
    assert target_auth.target_digest(dist) != first


# ---------------------------------------------------------------------------
# The provenance vocabulary and the coverage decision exist
# ---------------------------------------------------------------------------
def test_provenance_event_types_are_admitted():
    from icdev.tools.audit.audit_logger import VALID_EVENT_TYPES

    assert target_auth.EVENT_AUTHORIZED in VALID_EVENT_TYPES
    assert target_auth.EVENT_REFUSED in VALID_EVENT_TYPES


def test_sandbox_coverage_has_the_rea_row():
    doc = (REPO / "docs/security/sandbox-coverage.md").read_text(encoding="utf-8")
    assert "### Gap 73" in doc and "REA external MCP server" in doc
    assert "args/external_mcp_servers.yaml" in doc
