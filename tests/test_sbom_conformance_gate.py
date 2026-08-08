#!/usr/bin/env python3
# CUI // SP-CTI
"""Gate on conformance, not just presence — 2026 SBOM Minimum Elements (sbx-gov-01).

The premise under test is the one from the gap analysis: the five pre-existing
SBOM conditions are presence, freshness and exit-code checks, so a two-line
CycloneDX file with no components clears every one of them. These tests pin that
the new conditions do not.
"""

import json
import subprocess
import sys
import types

import pytest
import yaml

from tools.compliance import sbom_conformance_gate as gate
from tools.compliance.component_producer import (
    KNOWN,
    PROPERTY_PRODUCER,
    PROPERTY_PRODUCER_SOURCE,
    PROPERTY_PROVENANCE,
)

GATES_PATH = gate.PROJECT_ROOT / "args" / "security_gates.yaml"


# =====================================================================================
# Fixtures — one document that conforms, one that does not
# =====================================================================================


#: The document the pre-existing gates cannot distinguish from a real SBOM. It
#: was generated, it is not stale, generation neither failed nor was skipped, and
#: it can be signed — so sbom_not_generated, sbom_stale_over_30_days,
#: sbom_generation_failed, sbom_generation_skipped and sbom_attestation_missing
#: all pass on it.
NON_CONFORMING_SBOM = {"bomFormat": "CycloneDX", "specVersion": "1.4"}


def _component(name, version, purl):
    return {
        "type": "library",
        "bom-ref": f"{name}@{version}",
        "name": name,
        "version": version,
        "purl": purl,
        "hashes": [{"alg": "SHA-256", "content": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}],
        "licenses": [{"license": {"id": "Apache-2.0"}}],
        "properties": [
            {"name": PROPERTY_PRODUCER, "value": "The Apache Software Foundation"},
            {"name": PROPERTY_PROVENANCE, "value": KNOWN},
            {"name": PROPERTY_PRODUCER_SOURCE, "value": "python-dist-info-metadata"},
        ],
    }


def conforming_sbom():
    """A CycloneDX document stating all 17 data-field elements."""
    target = _component("icdev", "1.0.0", "pkg:pypi/icdev@1.0.0")
    target["type"] = "application"
    target["bom-ref"] = "icdev-root"
    components = [
        _component("flask", "3.0.0", "pkg:pypi/flask@3.0.0"),
        _component("pyyaml", "6.0.1", "pkg:pypi/pyyaml@6.0.1"),
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:2b1c9f0e-0f4a-4f7d-9f4b-4a1d3c6e8f21",
        "version": 1,
        "signature": {"algorithm": "ES256", "value": "MEUCIQD..."},
        "metadata": {
            "timestamp": "2026-08-08T03:42:20Z",
            "authors": [{"name": "Integrated Concepts Development"}],
            "tools": [{"vendor": "ICDEV™", "name": "icdev-sbom-generator", "version": "1.0.0"}],
            "component": target,
            "properties": [
                {"name": "icdev:sbom-generation-context", "value": "build"},
                {"name": "icdev:classification", "value": "CUI // SP-CTI"},
            ],
        },
        "components": components,
        "dependencies": [
            {"ref": "icdev-root", "dependsOn": ["flask@3.0.0", "pyyaml@6.0.1"]},
            {"ref": "flask@3.0.0", "dependsOn": []},
            {"ref": "pyyaml@6.0.1", "dependsOn": []},
        ],
    }


# =====================================================================================
# Both directions — the acceptance criterion
# =====================================================================================


def test_gate_blocks_on_a_non_conforming_sbom():
    result = gate.evaluate_sbom_gate(NON_CONFORMING_SBOM, gate="deployment_gates")

    assert result["passed"] is False
    conditions = [entry["condition"] for entry in result["blocking"]]
    assert gate.CONDITION_NOT_MET in conditions
    assert result["component_count"] == 0


def test_gate_passes_on_a_conforming_sbom():
    result = gate.evaluate_sbom_gate(conforming_sbom(), gate="deployment_gates")

    assert result["passed"] is True, f"unexpected gaps: {result['gaps']}"
    assert result["blocking"] == []
    assert result["elements_met"] == result["elements_total"] == 17
    assert result["gaps"] == []


@pytest.mark.parametrize("gate_name", ["deployment_gates", "swft", "devsecops"])
def test_both_directions_hold_for_every_wired_gate(gate_name):
    assert gate.evaluate_sbom_gate(NON_CONFORMING_SBOM, gate=gate_name)["passed"] is False
    assert gate.evaluate_sbom_gate(conforming_sbom(), gate=gate_name)["passed"] is True


def test_an_unknown_gate_is_refused():
    with pytest.raises(gate.SbomGateConfigError):
        gate.evaluate_sbom_gate(conforming_sbom(), gate="merge_gates")


# =====================================================================================
# The five pre-existing conditions cannot see what these two see
# =====================================================================================


def test_the_empty_sbom_is_well_formed_json_that_a_presence_check_accepts():
    """Nothing about the blocking finding is a parse failure or a missing file."""
    assert json.loads(json.dumps(NON_CONFORMING_SBOM))["bomFormat"] == "CycloneDX"

    result = gate.evaluate_sbom_gate(NON_CONFORMING_SBOM)
    reasons = " ".join(entry["reason"] for entry in result["blocking"])
    assert "component" in reasons


def test_a_partial_sbom_warns_without_blocking():
    """Above the blocking floor, short of the full set: warn, do not block."""
    sbom = conforming_sbom()
    for component in sbom["components"]:
        component.pop("licenses")
    sbom["metadata"]["component"].pop("licenses")

    result = gate.evaluate_sbom_gate(sbom, gate="swft")

    assert result["passed"] is True
    assert result["blocking"] == []
    assert [entry["condition"] for entry in result["warnings"]] == [gate.CONDITION_BELOW_THRESHOLD]
    assert "component_license" in result["gaps"]


def test_a_detached_signature_beside_the_file_meets_the_signature_element(tmp_path):
    """sbom_signer (sbx-sig-01) writes <sbom>.sig.json, not an in-document block —
    scoring the document alone would call every signed SBOM unsigned."""
    sbom = conforming_sbom()
    sbom.pop("signature")
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps(sbom), encoding="utf-8")

    assert "sbom_author_signature" in gate.evaluate_sbom_file(path)["gaps"]

    (tmp_path / "sbom.cdx.json.sig.json").write_text(
        json.dumps({"algorithm": "ecdsa-p256", "signature": "MEUCIQD..."}), encoding="utf-8"
    )

    assert gate.evaluate_sbom_file(path)["gaps"] == []


def test_components_without_a_producer_gap_the_producer_element():
    """Component Producer is delegated to the module that owns it (sbx-fld-02)."""
    sbom = conforming_sbom()
    for component in sbom["components"]:
        component["properties"] = []

    result = gate.evaluate_sbom_gate(sbom)

    assert "component_producer" in result["gaps"]


# =====================================================================================
# Thresholds live in args/security_gates.yaml, never in Python
# =====================================================================================


def test_thresholds_are_read_from_the_yaml():
    config = gate.load_gate_config()
    on_disk = yaml.safe_load(GATES_PATH.read_text(encoding="utf-8"))[gate.CONFIG_SECTION]["thresholds"]

    assert config["thresholds"]["block_below_pct"] == float(on_disk["block_below_pct"])
    assert config["thresholds"]["warn_below_pct"] == float(on_disk["warn_below_pct"])
    assert config["thresholds"]["require_components"] is bool(on_disk["require_components"])


def test_retuning_the_yaml_retunes_the_gate(tmp_path):
    """The blocking decision follows the file, which is what 'not in Python' means."""
    sbom = conforming_sbom()
    for component in sbom["components"] + [sbom["metadata"]["component"]]:
        component.pop("licenses")
        component.pop("hashes")

    lenient = _write_gates(tmp_path / "lenient.yaml", block_below_pct=70)
    strict = _write_gates(tmp_path / "strict.yaml", block_below_pct=95)

    assert gate.evaluate_sbom_gate(sbom, gates_path=lenient)["passed"] is True
    assert gate.evaluate_sbom_gate(sbom, gates_path=strict)["passed"] is False


def _write_gates(path, block_below_pct):
    path.write_text(
        yaml.safe_dump(
            {
                gate.CONFIG_SECTION: {
                    "gates": ["deployment_gates", "swft", "devsecops"],
                    "blocking": [gate.CONDITION_NOT_MET],
                    "warning": [gate.CONDITION_BELOW_THRESHOLD],
                    "thresholds": {
                        "block_below_pct": block_below_pct,
                        "warn_below_pct": 100,
                        "require_components": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("missing", ["block_below_pct", "warn_below_pct", "require_components"])
def test_a_missing_threshold_raises_rather_than_defaulting(tmp_path, missing):
    """No silent fallback: an unconfigured gate is a bug to surface, not a constant."""
    path = _write_gates(tmp_path / "incomplete.yaml", block_below_pct=70)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    del config[gate.CONFIG_SECTION]["thresholds"][missing]
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(gate.SbomGateConfigError, match=missing):
        gate.load_gate_config(gates_path=path)


def test_a_missing_section_raises(tmp_path):
    path = tmp_path / "bare.yaml"
    path.write_text(yaml.safe_dump({"merge_gates": {"block_on": []}}), encoding="utf-8")

    with pytest.raises(gate.SbomGateConfigError, match=gate.CONFIG_SECTION):
        gate.load_gate_config(gates_path=path)


def test_no_threshold_value_is_hardcoded_in_the_module():
    source = (gate.PROJECT_ROOT / "tools" / "compliance" / "sbom_conformance_gate.py").read_text(encoding="utf-8")
    on_disk = yaml.safe_load(GATES_PATH.read_text(encoding="utf-8"))[gate.CONFIG_SECTION]["thresholds"]

    for key in ("block_below_pct", "warn_below_pct"):
        assert f"{key} = " not in source
        assert f'"{key}", {on_disk[key]}' not in source, f"{key} has a Python default"


# =====================================================================================
# Wiring — the conditions are in the three gates named on the card
# =====================================================================================


def test_the_conditions_are_wired_into_the_three_gates():
    config = yaml.safe_load(GATES_PATH.read_text(encoding="utf-8"))

    assert gate.CONDITION_NOT_MET in config["deployment_gates"]["block_on"]
    assert gate.CONDITION_BELOW_THRESHOLD in config["deployment_gates"]["warn_on"]

    for name in ("swft", "devsecops"):
        assert gate.CONDITION_NOT_MET in config[name]["blocking"], name
        assert gate.CONDITION_BELOW_THRESHOLD in config[name]["warning"], name

    assert sorted(config[gate.CONFIG_SECTION]["gates"]) == ["deployment_gates", "devsecops", "swft"]


# =====================================================================================
# Scoring defers to sbx-sig-02's validator the day it lands
# =====================================================================================


def test_the_structural_scorer_is_used_while_the_validator_is_absent():
    assert gate.score_sbom(conforming_sbom())["scored_by"] == gate.SCORER_STRUCTURAL


def test_the_validator_takes_over_when_it_is_importable(monkeypatch):
    """sbx-sig-02 owns the measurement instrument; this gate must not shadow it."""
    stub = types.ModuleType(gate.VALIDATOR_MODULE)
    stub.validate_sbom = lambda sbom: {
        "elements": {name: {"status": "gap"} for name in gate.DATA_FIELD_ELEMENTS},
        "component_count": 2,
    }
    monkeypatch.setitem(sys.modules, gate.VALIDATOR_MODULE, stub)

    result = gate.evaluate_sbom_gate(conforming_sbom(), gate="devsecops")

    assert result["scored_by"] == gate.SCORER_VALIDATOR
    assert result["elements_met"] == 0
    assert result["passed"] is False


def test_the_validators_own_aggregate_wins_when_it_supplies_one(monkeypatch):
    """It scores the 6 practices too, so its totals are not this gate's to recompute."""
    stub = types.ModuleType(gate.VALIDATOR_MODULE)
    stub.validate_sbom = lambda sbom: {"elements_met": 21, "elements_total": 23, "component_count": 2}
    monkeypatch.setitem(sys.modules, gate.VALIDATOR_MODULE, stub)

    score = gate.score_sbom(conforming_sbom())

    assert (score["elements_met"], score["elements_total"]) == (21, 23)
    assert score["score_pct"] == 91.3


def test_a_vocabulary_mismatch_raises_instead_of_scoring_zero(monkeypatch):
    """The quiet failure — block everything for a reason no message states."""
    stub = types.ModuleType(gate.VALIDATOR_MODULE)
    stub.validate_sbom = lambda sbom: {
        "elements": {"SBOM Author": {"status": "met"}, "Component Name": {"status": "met"}},
        "component_count": 2,
    }
    monkeypatch.setitem(sys.modules, gate.VALIDATOR_MODULE, stub)

    with pytest.raises(gate.SbomScoreError, match="element vocabulary"):
        gate.score_sbom(conforming_sbom())


def test_an_unreadable_validator_result_raises(monkeypatch):
    stub = types.ModuleType(gate.VALIDATOR_MODULE)
    stub.validate_sbom = lambda sbom: {"component_count": 2}
    monkeypatch.setitem(sys.modules, gate.VALIDATOR_MODULE, stub)

    with pytest.raises(gate.SbomScoreError):
        gate.score_sbom(conforming_sbom())


def test_running_the_file_as_a_script_scores_the_same_as_importing_it(tmp_path):
    """sys.path[0] is the script's own directory, so the sibling delegation
    imports fail unless the module puts the repository root back on the path —
    and a swallowed ImportError would understate the score by one element while
    looking exactly like a real result."""
    sbom = conforming_sbom()
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps(sbom), encoding="utf-8")
    script = gate.PROJECT_ROOT / "tools" / "compliance" / "sbom_conformance_gate.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--sbom", str(path), "--json"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["elements_met"] == gate.score_sbom(sbom)["elements_met"] == 17


def test_an_unimportable_component_producer_raises_rather_than_gapping(monkeypatch):
    monkeypatch.setitem(sys.modules, "tools.compliance.component_producer", None)

    with pytest.raises(gate.SbomScoreError, match="component_producer"):
        gate.score_sbom(conforming_sbom())


def test_cli_exits_two_when_the_gate_cannot_run(tmp_path, monkeypatch):
    stub = types.ModuleType(gate.VALIDATOR_MODULE)
    stub.validate_sbom = lambda sbom: {"elements": {"SBOM Author": "met"}}
    monkeypatch.setitem(sys.modules, gate.VALIDATOR_MODULE, stub)
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps(conforming_sbom()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["sbom_conformance_gate.py", "--sbom", str(path)])

    assert gate.main() == 2


def test_a_partial_element_does_not_count_as_met(monkeypatch):
    """'partial' is the case the 2026 standard withdrew tolerance for."""
    stub = types.ModuleType(gate.VALIDATOR_MODULE)
    stub.validate_sbom = lambda sbom: {
        "elements": {name: {"status": "partial"} for name in gate.DATA_FIELD_ELEMENTS},
        "component_count": 2,
    }
    monkeypatch.setitem(sys.modules, gate.VALIDATOR_MODULE, stub)

    assert gate.score_sbom(conforming_sbom())["elements_met"] == 0


# =====================================================================================
# CLI
# =====================================================================================


def test_cli_exits_nonzero_on_a_non_conforming_sbom(tmp_path, monkeypatch, capsys):
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps(NON_CONFORMING_SBOM), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["sbom_conformance_gate.py", "--sbom", str(path), "--json"])

    assert gate.main() == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def test_cli_exits_zero_on_a_conforming_sbom(tmp_path, monkeypatch, capsys):
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps(conforming_sbom()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["sbom_conformance_gate.py", "--sbom", str(path), "--gate", "swft"])

    assert gate.main() == 0
    assert "PASSED" in capsys.readouterr().out
