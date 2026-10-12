# CUI // SP-CTI
"""REA evidence feeds supply chain + attack surface (rea-sec-01, project card ``rea``).

What is pinned here:

* fixture .NET / Electron / HAR / APK evidence reaches the four consumers --
  SBOM hints (``binary_components.rea_hinted_components`` and the CycloneDX
  properties ``sbom_generator`` emits), the attack-surface mapper, the SCRM
  assessor and the ATO evidence manifest -- each item with its REA evidence
  id, target sha256 and authorization basis;
* every item says ``confidence: unasserted``; nothing is scored;
* a report with no authorization basis, or one that did not run, is REFUSED by
  every feed rather than fed or read as empty;
* an unmeasured list is reported under ``unmeasured``, never as ``[]`` -- and
  a measured zero is ``[]``;
* a HAR query string never leaves the feed;
* the ATO block refuses to overwrite, like ``artifact_corroboration.attach``.

Reports are built through ``rea_adapter.map_response`` + ``_report`` from REAL
REA 6.4.0 recordings where one exists (Fixture.dll, the js summary) and from
``rea_sec_01_feed_records.json`` otherwise. No Node, no REA, no engine.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from icdev.tools.analyzers import rea_adapter as ra
from icdev.tools.analyzers import rea_feeds as feeds

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "rea"
RECORDED = json.loads((FIXTURES / "rea_6.4.0_recorded.json").read_text(encoding="utf-8"))
ADAPTER = json.loads((FIXTURES / "rea_6.4.0_adapter_responses.json").read_text(encoding="utf-8"))
SYNTHETIC = json.loads((FIXTURES / "rea_sec_01_feed_records.json").read_text(encoding="utf-8"))

SHA = "a" * 64


def _record(tool: str, result: dict) -> dict:
    step = ra.map_response(tool, {"ok": True, "result": result})
    assert step["kind"] == "evidence", step
    return step["record"]


def _report(family: str, records: list, *, basis: str = "owned", status: str = "ok") -> dict:
    prov = {"target_sha256": SHA, "authorization_basis": basis, "analysis": "local",
            "server": "rea", "rea_version": ra.REA_VERSION}
    return ra._report("SCRATCH/" + family, family, status, records=records, provenance=prov)


def _synthetic(key: str, family: str, **kw) -> dict:
    entry = SYNTHETIC["records"][key]
    rec = _record(entry["tool"], {"structuredContent": entry["body"]})
    return _report(family, [rec], **kw)


def _dotnet_real(**kw) -> dict:
    return _report("dotnet_assembly",
                   [_record("inspect_managed_artifact", RECORDED["responses"]["inspect_managed_artifact"])],
                   **kw)


def _js_summary_real() -> dict:
    result = ADAPTER["responses"]["js_bundle.analyze_javascript_application"]["result"]
    return _report("js_bundle", [_record("analyze_javascript_application", result)])


def _all_reports() -> list:
    return [_dotnet_real(), _synthetic("dotnet_native", "dotnet_assembly"),
            _synthetic("electron", "js_bundle"), _js_summary_real(),
            _synthetic("har", "har"), _synthetic("apk", "apk")]


def _assert_provenance(prov: dict, evidence_id_prefix: str = "ev_") -> None:
    assert prov["evidence_id"].startswith(evidence_id_prefix)
    assert prov["target_sha256"] == SHA
    assert prov["authorization_basis"] == "owned"
    assert prov["analysis"] == "local"
    assert prov["source"] == "rea"


# ---------------------------------------------------------------------------
# SBOM hints
# ---------------------------------------------------------------------------

def test_dotnet_real_recording_becomes_sbom_hints_with_provenance():
    from icdev.tools.compliance import binary_components as bc

    result = bc.rea_hinted_components(_dotnet_real())
    assert result["status"] == "ok"
    (comp,) = result["components"]
    assert (comp["declared_name"], comp["version"]) == ("mscorlib", "4.0.0.0")
    hint = comp["binary_hint"]
    assert hint["evidence"] == bc.EVIDENCE_REA
    assert hint["confidence"] == "unasserted"
    assert hint["version_basis"] == bc.VERSION_BASIS_REA
    assert hint["rea_evidence_id"] == RECORDED["responses"]["inspect_managed_artifact"][
        "structuredContent"]["evidence_id"]
    assert hint["source_sha256"] == SHA and hint["authorization_basis"] == "owned"
    # Fixture.dll has one ModuleRef row and REA names none: unmeasured, not [].
    assert result["unmeasured"] == {"native_module": "count_only:ModuleRef=1"}


def test_rea_hint_reaches_the_cyclonedx_properties_the_generator_emits():
    from icdev.tools.compliance import binary_components as bc

    (comp,) = bc.rea_hinted_components(_dotnet_real())["components"]
    assert bc.is_hinted_component(comp)
    props = {p["name"]: p["value"] for p in bc.binary_hint_properties(comp)}
    assert props[bc.PROPERTY_EVIDENCE] == "rea_evidence"
    assert props[bc.PROPERTY_CONFIDENCE] == "unasserted"
    assert props[bc.PROPERTY_REA_EVIDENCE_ID].startswith("ev_")
    assert props[bc.PROPERTY_AUTHORIZATION] == "owned"
    assert props[bc.PROPERTY_SOURCE_SHA256] == SHA


def test_sbom_generator_imports_the_rea_seam():
    gen = importlib.import_module("icdev.tools.compliance.sbom_generator")
    assert gen.rea_hinted_components is importlib.import_module(
        "icdev.tools.compliance.binary_components").rea_hinted_components
    assert "rea_reports" in gen.generate_sbom.__code__.co_varnames


def test_electron_apk_and_native_dotnet_hints_never_invent_a_version():
    from icdev.tools.compliance import binary_components as bc

    electron = bc.rea_hinted_components(_synthetic("electron", "js_bundle"))
    assert sorted(c["declared_name"] for c in electron["components"]) == [
        "@sentry/electron", "electron-store"]
    apk = bc.rea_hinted_components(_synthetic("apk", "apk"))
    assert sorted(c["declared_name"] for c in apk["components"]) == ["libcrypto.so", "libsqlcipher.so"]
    native = bc.rea_hinted_components(_synthetic("dotnet_native", "dotnet_assembly"))
    assert "vendorcrypt.dll" in [c["declared_name"] for c in native["components"]]
    for comp in electron["components"] + apk["components"]:
        assert comp["version"] == ""
        assert comp["binary_hint"]["version_basis"] == bc.VERSION_BASIS_NOT_OBSERVED
        assert comp["binary_hint"]["confidence"] == "unasserted"


def test_js_summary_detail_reports_packages_unmeasured_not_empty():
    from icdev.tools.compliance import binary_components as bc

    result = bc.rea_hinted_components(_js_summary_real())
    assert result["status"] == bc.STATUS_NO_HINTS
    assert result["unmeasured"] == {"npm_package": feeds.UNMEASURED_SUMMARY_DETAIL}


# ---------------------------------------------------------------------------
# Attack surface
# ---------------------------------------------------------------------------

def test_attack_surface_maps_ipc_requests_har_and_apk_with_provenance():
    from icdev.tools.network import attack_surface_mapper as asm

    out = asm.map_artifact_surface([_synthetic("electron", "js_bundle"), _synthetic("har", "har"),
                                    _synthetic("apk", "apk")])
    assert out["persisted"] is False and out["refused"] == []
    assert out["by_kind"] == {"electron_ipc_channel": 2, "client_request": 1,
                              "http_endpoint": 2, "android_permission": 2,
                              "android_exported_component": 2}
    names = {(e["surface_kind"], e["name"]) for e in out["entries"]}
    assert ("electron_ipc_channel", "settings:read") in names
    assert ("client_request", "https://updates.example.test/latest.json") in names
    assert ("http_endpoint", "GET http://127.0.0.1:5050/api/kanban/tasks") in names
    assert ("android_permission", "android.permission.CAMERA") in names
    assert ("android_exported_component", ".SyncService") not in names
    for entry in out["entries"]:
        assert entry["basis"] == "observed" and entry["confidence"] == "unasserted"
        _assert_provenance(entry["provenance"])
    # The fetch whose endpoint REA could not resolve is counted, not dropped.
    assert {"target": "SCRATCH/js_bundle", "surface_kind": "client_request_endpoint",
            "basis": "dynamic_endpoint:1"} in out["unmeasured"]


def test_har_query_strings_never_leave_the_feed():
    out = feeds.attack_surface(_synthetic("har", "har"))
    tasks = next(i for i in out["items"] if i["name"].startswith("GET "))
    assert tasks["detail"]["observations"] == 2 and tasks["detail"]["query_dropped"] is True
    assert "token=abc" not in json.dumps(out) and "status=open" not in json.dumps(out)


def test_js_summary_measured_zero_ipc_is_empty_but_requests_are_unmeasured():
    out = feeds.attack_surface(_js_summary_real())
    assert out["items"] == []
    assert "electron_ipc_channel" not in out["unmeasured"]   # REA counted 0: a measured zero
    assert out["unmeasured"]["client_request"] == feeds.UNMEASURED_SUMMARY_DETAIL


def test_ipc_counted_but_not_named_is_unmeasured():
    report = _synthetic("electron", "js_bundle")
    del report["body"][0]["normalized_result"]["summary"]["ipc"]["channels"]
    out = feeds.attack_surface(report)
    assert out["unmeasured"]["electron_ipc_channel"] == "count_only:literal_channels=2"
    assert not [i for i in out["items"] if i["surface_kind"] == "electron_ipc_channel"]


# ---------------------------------------------------------------------------
# SCRM
# ---------------------------------------------------------------------------

def test_scrm_receives_declared_managed_and_native_dependencies():
    from icdev.tools.supply_chain import scrm_assessor as scrm

    out = scrm.assess_artifact_dependencies("proj-x", [_synthetic("dotnet_native", "dotnet_assembly")])
    assert out["scored"] is False and out["refused"] == [] and out["unmeasured"] == []
    by_name = {d["name"]: d for d in out["dependencies"]}
    assert by_name["System.Runtime"]["publisher_identity"] == "strong_name_token"
    assert by_name["Vendor.Core"]["publisher_identity"] == "none_declared"
    assert by_name["vendorcrypt.dll"]["kind"] == "native_module"
    assert by_name["vendorcrypt.dll"]["publisher_identity"] == "not_applicable"
    assert out["native_dependency_count"] == 1
    for dep in out["dependencies"]:
        assert dep["confidence"] == "unasserted"
        _assert_provenance(dep["provenance"])


def test_scrm_real_recording_says_native_modules_are_unmeasured():
    from icdev.tools.supply_chain import scrm_assessor as scrm

    out = scrm.assess_artifact_dependencies("proj-x", [_dotnet_real()])
    assert [d["name"] for d in out["dependencies"]] == ["mscorlib"]
    assert out["unmeasured"] == [{"target": "SCRATCH/dotnet_assembly", "kind": "native_module",
                                  "basis": "count_only:ModuleRef=1"}]


# ---------------------------------------------------------------------------
# ATO evidence provenance
# ---------------------------------------------------------------------------

def test_ato_provenance_block_and_refusal_to_overwrite():
    report = _dotnet_real()
    block = feeds.ato_provenance(report)
    assert block["status"] == "attached" and block["confidence"] == "unasserted"
    assert block["target_sha256"] == SHA and block["authorization_basis"] == "owned"
    assert block["evidence_ids"] == report["evidence_ids"]
    assert len(block["bundle_sha256"]) == 64
    assert feeds.ato_provenance(report)["bundle_sha256"] == block["bundle_sha256"]
    record = {"verdict": "verified"}
    merged = feeds.attach_ato_provenance(record, [block])
    assert record == {"verdict": "verified"} and merged["verdict"] == "verified"
    with pytest.raises(ValueError):
        feeds.attach_ato_provenance(merged, [block])


def test_collect_evidence_carries_rea_provenance(tmp_path, monkeypatch):
    stub = types.ModuleType("tools.aisg.roi_tracker")
    stub.emit_roi_event = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tools.aisg.roi_tracker", stub)
    from icdev.tools.compliance import evidence_collector as ec

    db = tmp_path / "evidence.db"
    plain = ec.collect_evidence("proj-x", framework="nist_800_53", db_path=db)
    assert "rea_provenance" not in plain
    out = ec.collect_evidence("proj-x", framework="nist_800_53", db_path=db,
                              rea_reports=[_dotnet_real(), _dotnet_real(basis="")])
    attached, refused = out["rea_provenance"]
    assert attached["status"] == "attached" and attached["evidence_ids"]
    assert refused == {"status": "refused", "reason": "authorization_missing"}


# ---------------------------------------------------------------------------
# Rails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("make,reason", [
    (lambda: _dotnet_real(basis=""), "authorization_missing"),
    (lambda: _dotnet_real(status="error"), "report_status_error"),
    (lambda: {"analyzer": "ghidra"}, "not_a_rea_report"),
])
def test_every_feed_refuses_a_report_without_provenance_or_a_run(make, reason):
    from icdev.tools.compliance import binary_components as bc
    from icdev.tools.network import attack_surface_mapper as asm
    from icdev.tools.supply_chain import scrm_assessor as scrm

    report = make()
    for fn in (feeds.sbom_observations, feeds.attack_surface, feeds.dependency_observations):
        out = fn(report)
        assert out["status"] == "refused" and out["reason"] == reason and out["items"] is None
    assert bc.rea_hinted_components(report)["components"] == []
    assert asm.map_artifact_surface([report])["refused"][0]["reason"] == reason
    assert scrm.assess_artifact_dependencies("p", [report])["refused"][0]["reason"] == reason
    assert feeds.ato_provenance(report) == {"status": "refused", "reason": reason}


def test_confidence_stays_unasserted_across_every_fixture():
    for report in _all_reports():
        for fn in (feeds.sbom_observations, feeds.attack_surface, feeds.dependency_observations):
            for item in fn(report)["items"]:
                assert item.get("confidence", "unasserted") == "unasserted"
                assert item["provenance"]["target_sha256"] == SHA


def test_assumed_shapes_are_absent_from_the_real_recordings():
    """When a real recording carries an assumed key, this fails and the docstring is stale."""
    real = json.dumps(RECORDED["responses"]) + json.dumps(ADAPTER["responses"])
    for key in ("module_references", "\\\"channels\\\"", "native_libraries"):
        assert key not in real, key
