# [CUI // SP-CTI]
"""sbx-fmt-02 — SBOM ingest parity: parse and validate instead of glob.

Two claims are under test.

1. The ZIG external adapter ingests SPDX, not only CycloneDX, and reports the
   document's conformance score alongside the activities it mapped.

2. ``fedramp_assessor``, ``sbd_assessor``, ``cssp_assessor`` and
   ``ivv_assessor`` no longer decide an SBOM exists because a filename matched
   ``*sbom*``. Each opens the file, scores it against the 2026 Minimum
   Elements, and says which of the 23 it met.

The load-bearing case is ``test_empty_sbom_json_no_longer_passes_*``: before
this change, a zero-byte file named ``sbom.json`` satisfied all four controls.
"""

import json
import pathlib

import pytest

ZIG_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "zig"
SBOM_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sbom"


# ── Helpers ───────────────────────────────────────────────────────────────────


class _ActivityLog:
    """Records set_activity_status calls instead of writing to the DB."""

    def __init__(self):
        self.calls = []

    def record(self, activity_id, status=None, target_id=None, evidence_note=None,
               completed_by=None, **kwargs):
        self.calls.append(
            {
                "activity_id": activity_id,
                "status": status,
                "target_id": target_id,
                "evidence_note": evidence_note,
            }
        )
        return True

    def notes(self):
        return [c["evidence_note"] or "" for c in self.calls]


@pytest.fixture
def stub_activity_tracker(monkeypatch):
    """Stub set_activity_status so ingest tests never touch the database."""
    import importlib
    import sys

    log = _ActivityLog()
    tracker_mod = importlib.import_module("tools.security_canvas.zig_activity_tracker")
    monkeypatch.setattr(tracker_mod, "set_activity_status", log.record)

    adapter_key = "tools.security_canvas.zig_external_adapter"
    if adapter_key in sys.modules:
        monkeypatch.setattr(sys.modules[adapter_key], "set_activity_status", log.record)
    return log


def _spdx_fixture():
    return (ZIG_FIXTURES / "sbom_spdx_2.3.json").read_text(encoding="utf-8")


def _write_empty_sbom(tmp_path):
    """The exact artifact the four assessors used to accept: a name, no SBOM."""
    target = tmp_path / "sbom.json"
    target.write_text("", encoding="utf-8")
    return target


def _write_conforming_sbom(tmp_path, name="project.sbom.cdx.json"):
    source = SBOM_FIXTURES / "conformant_cyclonedx_1.6.cdx.json"
    target = tmp_path / name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _write_deficient_sbom(tmp_path, name="legacy.sbom.cdx.json"):
    source = SBOM_FIXTURES / "baseline_cyclonedx_pre_sbx.cdx.json"
    target = tmp_path / name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return target


# ═══════════════════════════════════════════════════════════════════════════
# 1. ZIG adapter — SPDX parity
# ═══════════════════════════════════════════════════════════════════════════


class TestZigAdapterSpdxIngest:
    def test_spdx_fixture_ingests(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        result = ingest_sbom("payments-edge", _spdx_fixture())

        assert "error" not in result
        assert result["source"] == "sbom"
        assert result["target_id"] == "payments-edge"

    def test_spdx_format_is_recognised_not_guessed(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        conformance = ingest_sbom("payments-edge", _spdx_fixture())["conformance"]

        assert conformance["format"] == "SPDX"
        assert conformance["format_version"] == "SPDX-2.3"

    def test_spdx_packages_are_read_as_components(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        # The fixture's third package (legacy-shim) carries no versionInfo, so
        # the missing-version mapping must fire — proving the SPDX packages
        # were genuinely walked and not merely counted.
        result = ingest_sbom("payments-edge", _spdx_fixture())

        assert "zig-act-p1-21" in result["activities_updated"]
        assert any("legacy-shim missing version" in n for n in stub_activity_tracker.notes())

    def test_spdx_ingest_reports_a_conformance_score(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        conformance = ingest_sbom("payments-edge", _spdx_fixture())["conformance"]

        assert conformance["elements_total"] == 23
        assert 0 < conformance["elements_met"] < 23
        assert conformance["conformant"] is False

    def test_spdx_accepts_a_dict_payload(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        result = ingest_sbom("payments-edge", json.loads(_spdx_fixture()))

        assert result["conformance"]["format"] == "SPDX"

    def test_third_party_spdx_document_ingests(self, stub_activity_tracker):
        """A vendor's SPDX file ICDEV did not generate."""
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        vendor = (SBOM_FIXTURES / "third_party_spdx_2.3.spdx.json").read_text(encoding="utf-8")
        result = ingest_sbom("acme-gateway", vendor)

        assert "error" not in result
        assert result["conformance"]["format"] == "SPDX"


class TestZigAdapterCycloneDxUnchanged:
    def test_cyclonedx_still_ingests_and_now_scores(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        cdx = (ZIG_FIXTURES / "sbom_cyclonedx_full.json").read_text(encoding="utf-8")
        result = ingest_sbom("billing-svc", cdx)

        assert result["conformance"]["format"] == "CycloneDX"
        assert result["findings"] >= 1

    def test_top_level_vulnerabilities_are_read(self, stub_activity_tracker):
        """CycloneDX puts vulnerabilities at the document root, not in components.

        The pre-sbx-fmt-02 adapter only read a nested ``component.vulnerabilities``
        list, so a spec-shaped document reported zero findings.
        """
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        document = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "components": [
                {"name": "openssl", "version": "3.0.0", "bom-ref": "pkg:generic/openssl@3.0.0"},
            ],
            "vulnerabilities": [
                {
                    "id": "CVE-2026-0001",
                    "ratings": [{"severity": "critical"}],
                    "affects": [{"ref": "pkg:generic/openssl@3.0.0"}],
                }
            ],
        }
        result = ingest_sbom("edge", document)

        assert result["findings"] == 1
        assert "zig-act-d08" in result["activities_updated"]
        assert any("openssl CVE-critical CVE-2026-0001" in n for n in stub_activity_tracker.notes())

    def test_unsupported_format_is_named_not_silently_ingested(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        result = ingest_sbom("edge", json.dumps({"@context": "https://spdx.org/rdf/3.0.1/"}))

        assert "error" in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_undeclared_component_list_still_ingests_but_says_so(self, stub_activity_tracker):
        """Back-compat: callers have always passed bare {"components": [...]}."""
        from tools.security_canvas.zig_external_adapter import ingest_sbom

        result = ingest_sbom("edge", {"components": [{"name": "lib", "version": "1.0"}]})

        assert "error" not in result
        assert result["conformance"]["format"] == "undeclared"
        assert "undeclared" in result["conformance"]["note"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. The shared evidence collector
# ═══════════════════════════════════════════════════════════════════════════


class TestSbomEvidence:
    def test_empty_file_is_ungradeable_not_present(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence, has_real_sbom

        _write_empty_sbom(tmp_path)
        evidence = collect_sbom_evidence(tmp_path)

        assert evidence["verdict"] == "ungradeable"
        assert has_real_sbom(evidence) is False
        assert "empty" in evidence["ungradeable"][0]["reason"]

    def test_no_candidates_is_absent(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence

        (tmp_path / "README.md").write_text("no sboms here", encoding="utf-8")
        assert collect_sbom_evidence(tmp_path)["verdict"] == "absent"

    def test_conforming_sbom_scores_full_marks(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence

        _write_conforming_sbom(tmp_path)
        evidence = collect_sbom_evidence(tmp_path)

        assert evidence["verdict"] == "conforming"
        assert evidence["best"]["elements_met"] == evidence["best"]["elements_total"]

    def test_deficient_sbom_is_distinguished_from_absent(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence, has_real_sbom

        _write_deficient_sbom(tmp_path)
        evidence = collect_sbom_evidence(tmp_path)

        assert evidence["verdict"] == "deficient"
        assert has_real_sbom(evidence) is True
        assert evidence["best"]["elements_met"] < evidence["best"]["elements_total"]

    def test_signature_sidecar_is_not_mistaken_for_an_sbom(self, tmp_path):
        from tools.compliance.sbom_evidence import find_sbom_candidates

        sbom = _write_conforming_sbom(tmp_path)
        pathlib.Path(str(sbom) + ".sig.json").write_text(
            json.dumps({"algorithm": "ecdsa-p256-sha256", "value": "deadbeef"}), encoding="utf-8"
        )
        candidates = find_sbom_candidates(tmp_path)

        assert str(sbom) in candidates
        assert not any(c.endswith(".sig.json") for c in candidates)

    def test_a_directory_named_cyclonedx_is_not_an_sbom(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence

        (tmp_path / "cyclonedx").mkdir()
        assert collect_sbom_evidence(tmp_path)["verdict"] == "absent"

    def test_xml_is_ungradeable_with_a_stated_reason(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence

        (tmp_path / "bom.xml").write_text("<bom xmlns='http://cyclonedx.org/schema/bom/1.4'/>",
                                          encoding="utf-8")
        evidence = collect_sbom_evidence(tmp_path)

        assert evidence["verdict"] == "ungradeable"
        assert "JSON only" in evidence["ungradeable"][0]["reason"]

    def test_spdx_document_is_graded(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence

        (tmp_path / "vendor.spdx.json").write_text(_spdx_fixture(), encoding="utf-8")
        evidence = collect_sbom_evidence(tmp_path)

        assert evidence["best"]["format"] == "SPDX"
        assert evidence["best"]["elements_total"] == 23

    def test_describe_names_the_score(self, tmp_path):
        from tools.compliance.sbom_evidence import collect_sbom_evidence, describe

        _write_deficient_sbom(tmp_path)
        sentence = describe(collect_sbom_evidence(tmp_path))

        assert "2026 minimum elements" in sentence
        assert " of 23" in sentence


# ═══════════════════════════════════════════════════════════════════════════
# 3. The four assessors — a score, not a boolean
# ═══════════════════════════════════════════════════════════════════════════


def _fedramp_check(project_dir):
    from tools.compliance.fedramp_assessor import _check_supply_chain

    return _check_supply_chain(str(project_dir))


def _sbd_check(project_dir):
    from tools.compliance.sbd_assessor import _check_sbom_freshness

    return _check_sbom_freshness(str(project_dir))


def _cssp_check(project_dir):
    from tools.compliance.cssp_assessor import _check_sbom_exists

    return _check_sbom_exists(str(project_dir))


def _ivv_check(project_dir):
    from tools.compliance.ivv_assessor import _check_artifact_integrity

    return _check_artifact_integrity(str(project_dir))


ALL_FOUR = [
    pytest.param(_fedramp_check, id="fedramp"),
    pytest.param(_sbd_check, id="sbd"),
    pytest.param(_cssp_check, id="cssp"),
    pytest.param(_ivv_check, id="ivv"),
]


@pytest.mark.parametrize("check", ALL_FOUR)
def test_empty_sbom_json_no_longer_passes(check, tmp_path):
    """The regression this task exists for.

    A zero-byte ``sbom.json`` matched ``*sbom*.json`` and satisfied all four
    controls. It must now satisfy none of them.
    """
    _write_empty_sbom(tmp_path)

    result = check(tmp_path)

    assert result["status"] != "satisfied", (
        f"an empty file named sbom.json still passes: {result}"
    )


@pytest.mark.parametrize("check", ALL_FOUR)
def test_empty_sbom_json_says_why(check, tmp_path):
    """A rejection has to be actionable, not just negative."""
    _write_empty_sbom(tmp_path)

    result = check(tmp_path)
    text = f"{result['evidence']} {result.get('details', '')}"

    assert "empty" in text.lower() or "not be parsed" in text.lower()


@pytest.mark.parametrize("check", ALL_FOUR)
def test_conformance_score_is_reported(check, tmp_path):
    """Each assessor states N of 23, not merely 'found'."""
    _write_deficient_sbom(tmp_path)

    result = check(tmp_path)
    text = f"{result['evidence']} {result.get('details', '')}"

    assert " of 23" in text, f"no conformance score in the evidence: {result}"


@pytest.mark.parametrize("check", ALL_FOUR)
def test_conforming_sbom_is_recognised(check, tmp_path):
    """A document that meets all 23 elements reads as conforming everywhere."""
    _write_conforming_sbom(tmp_path)

    result = check(tmp_path)

    assert "23 of 23" in f"{result['evidence']} {result.get('details', '')}"


class TestAssessorStatusTransitions:
    """The status each assessor returns, per verdict, in its own vocabulary."""

    def test_cssp_conforming_is_satisfied(self, tmp_path):
        _write_conforming_sbom(tmp_path)
        assert _cssp_check(tmp_path)["status"] == "satisfied"

    def test_cssp_deficient_is_partial(self, tmp_path):
        _write_deficient_sbom(tmp_path)
        assert _cssp_check(tmp_path)["status"] == "partially_satisfied"

    def test_cssp_absent_is_not_satisfied(self, tmp_path):
        assert _cssp_check(tmp_path)["status"] == "not_satisfied"

    def test_sbd_fresh_and_conforming_is_satisfied(self, tmp_path):
        _write_conforming_sbom(tmp_path)
        assert _sbd_check(tmp_path)["status"] == "satisfied"

    def test_sbd_fresh_but_deficient_is_partial(self, tmp_path):
        """A brand-new SBOM missing elements is fresh AND wrong."""
        _write_deficient_sbom(tmp_path)
        assert _sbd_check(tmp_path)["status"] == "partially_satisfied"

    def test_sbd_reads_its_freshness_window_from_the_gate_config(self):
        from tools.compliance.sbd_assessor import _sbom_max_age_days

        assert _sbom_max_age_days() > 0

    def test_ivv_checksums_alone_still_satisfy(self, tmp_path):
        """SBOM parsing must not break the independent integrity route."""
        (tmp_path / "SHA256SUMS").write_text("abc  artifact.tar.gz\n", encoding="utf-8")
        assert _ivv_check(tmp_path)["status"] == "satisfied"

    def test_ivv_empty_sbom_alone_is_not_satisfied(self, tmp_path):
        _write_empty_sbom(tmp_path)
        assert _ivv_check(tmp_path)["status"] == "not_satisfied"

    def test_fedramp_empty_sbom_does_not_evidence_supply_chain(self, tmp_path):
        """Even with dependency auditing configured, the SBOM half must fail."""
        _write_empty_sbom(tmp_path)
        (tmp_path / "ci.yml").write_text("steps:\n  - run: pip-audit\n", encoding="utf-8")

        result = _fedramp_check(tmp_path)

        assert result["status"] == "other_than_satisfied"
        # The artifact list must credit the audit config and not the SBOM.
        assert "Partial supply chain: SBOM" not in result["evidence"]
        assert "dependency auditing" in result["evidence"]

    def test_fedramp_real_sbom_plus_audit_is_satisfied(self, tmp_path):
        _write_conforming_sbom(tmp_path)
        (tmp_path / "ci.yml").write_text("steps:\n  - run: pip-audit\n", encoding="utf-8")

        assert _fedramp_check(tmp_path)["status"] == "satisfied"


def test_new_fixture_exists():
    assert (ZIG_FIXTURES / "sbom_spdx_2.3.json").exists()
