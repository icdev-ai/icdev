#!/usr/bin/env python3
# CUI // SP-CTI
"""xrv-bin-03 — corroboration is reported BESIDE a verdict, never folded into it.

``attestation_verify`` and ``slsa_verify`` check METADATA about an artifact and
have never opened it, which is what the "the binary does not match the SBOM"
rebuttal is about. This suite asserts the three things that make the answer
usable rather than merely present:

* **The closed set.** ``agrees | disagrees | unmeasurable``, and
  ``unmeasurable`` is its own verdict — an absent attestation, an unreadable
  artifact and an SBOM nobody generated are "I could not ask", which is never
  "the answers match".
* **The verdict beside it does not move.** Asserted as EQUALITY over the whole
  result with and without the evidence arguments, on both verifiers. A test
  that merely checked ``meets_target`` would still pass for a future edit that
  rewrote ``gaps`` from a triage.
* **The asymmetry is deliberate and is pinned.** Only a version contradiction
  on a name BOTH sides carry can reach ``disagrees``. An SBOM component the
  binary never mentions, and a string the SBOM never declares, are context —
  because a statically linked library leaves no trace and a version string can
  name a protocol, a file format or somebody else's requirement.
"""

import ast
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.devsecops import artifact_corroboration as ac  # noqa: E402


def _elf64(payload: bytes) -> bytes:
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[6] = 1
    struct.pack_into("<HH", header, 16, 2, 0x3E)
    return bytes(header) + b"\x00" * 64 + payload


@pytest.fixture()
def artifact(tmp_path):
    path = tmp_path / "app.bin"
    path.write_bytes(_elf64(b"OpenSSL 1.1.1k  25 Mar 2021\x00zlib 1.2.11\x00"))
    return path


@pytest.fixture()
def matching_attestation(tmp_path, artifact):
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    path = tmp_path / "provenance.json"
    path.write_text(
        json.dumps(
            {
                "_type": "https://in-toto.io/Statement/v1",
                "subject": [{"name": "app.bin", "digest": {"sha256": digest}}],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def mismatched_attestation(tmp_path):
    path = tmp_path / "wrong.json"
    path.write_text(
        json.dumps({"subject": [{"name": "app.bin", "digest": {"sha256": "0" * 64}}]}),
        encoding="utf-8",
    )
    return path


def _sbom(tmp_path, components, name="sbom.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"components": components}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def test_a_digest_mismatch_disagrees(artifact, mismatched_attestation):
    """The strong axis: the statement is about a DIFFERENT FILE."""
    block = ac.corroborate(str(artifact), attestation=str(mismatched_attestation))

    assert block["corroboration"] == ac.DISAGREES
    assert block["digest"]["verdict"] == ac.DISAGREES
    assert block["digest"]["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_a_digest_match_agrees(artifact, matching_attestation):
    block = ac.corroborate(str(artifact), attestation=str(matching_attestation))

    assert block["digest"]["verdict"] == ac.AGREES
    assert block["digest"]["matched_subject"]["name"] == "app.bin"
    assert block["corroboration"] == ac.AGREES


def test_no_sbom_is_unmeasurable_and_never_agrees(artifact):
    block = ac.corroborate(str(artifact))

    assert block["corroboration"] == ac.UNMEASURABLE
    assert block["sbom"]["verdict"] == ac.UNMEASURABLE
    assert block["sbom"]["reason"] == ac.REASON_NO_SBOM
    assert block["digest"]["reason"] == ac.REASON_NO_ATTESTATION
    # Counts are None, never 0, on the side that was never read.
    assert block["sbom"]["declared_count"] is None


def test_an_unreadable_artifact_is_unmeasurable_on_both_axes(tmp_path, matching_attestation):
    block = ac.corroborate(str(tmp_path / "gone.bin"), attestation=str(matching_attestation))

    assert block["corroboration"] == ac.UNMEASURABLE
    assert block["digest"]["verdict"] == ac.UNMEASURABLE
    assert block["sbom"]["verdict"] == ac.UNMEASURABLE
    assert ac.REASON_ARTIFACT_UNREADABLE in block["digest"]["reason"]


def test_the_rebuttal_the_card_is_about(artifact, tmp_path):
    """The SBOM says openssl 3.0.2; the artifact says OpenSSL 1.1.1k."""
    sbom = _sbom(tmp_path, [{"name": "openssl", "version": "3.0.2"}])

    block = ac.corroborate(str(artifact), sbom=str(sbom))

    assert block["sbom"]["verdict"] == ac.DISAGREES
    assert block["corroboration"] == ac.DISAGREES
    (contradiction,) = block["sbom"]["contradictions"]
    assert contradiction["observed_version"] == "1.1.1k"
    assert contradiction["declared_version"] == "3.0.2"


def test_an_agreeing_sbom_agrees(artifact, tmp_path):
    sbom = _sbom(tmp_path, [{"name": "openssl", "version": "1.1.1k"}])

    block = ac.corroborate(str(artifact), sbom=str(sbom))

    assert block["sbom"]["verdict"] == ac.AGREES
    assert block["sbom"]["contradictions"] == []


def test_a_component_the_binary_never_mentions_is_context_not_a_disagreement(artifact, tmp_path):
    """A statically linked library leaves no import and need leave no string."""
    sbom = _sbom(tmp_path, [{"name": "libsomethingelse", "version": "9.9.9"}])

    block = ac.corroborate(str(artifact), sbom=str(sbom))

    assert block["sbom"]["verdict"] == ac.UNMEASURABLE
    assert block["sbom"]["reason"] == ac.REASON_NO_SHARED_NAME
    assert "libsomethingelse" in block["sbom"]["declared_not_observed"]
    # And the observations that matched nothing are named too.
    assert block["sbom"]["observed_not_declared"]


def test_an_unknown_declared_version_cannot_contradict(artifact, tmp_path):
    """The generator's own honest unknown marker must not become a finding."""
    sbom = _sbom(tmp_path, [{"name": "openssl", "version": "unknown"}])

    block = ac.corroborate(str(artifact), sbom=str(sbom))

    assert block["sbom"]["contradictions"] == []
    assert block["sbom"]["verdict"] == ac.AGREES


def test_a_prefix_version_is_compatible_only_at_a_boundary(artifact, tmp_path):
    compatible = ac.corroborate(
        str(artifact), sbom=str(_sbom(tmp_path, [{"name": "openssl", "version": "1.1"}], "a.json"))
    )
    assert compatible["sbom"]["contradictions"] == []

    # `1.1` and `1.14` share four characters and are different releases.
    contradicting = ac.corroborate(
        str(artifact),
        sbom=str(_sbom(tmp_path, [{"name": "zlib", "version": "1.2.114"}], "b.json")),
    )
    assert contradicting["sbom"]["contradictions"]


def test_every_verdict_is_in_the_closed_set(artifact, tmp_path, mismatched_attestation):
    sbom = _sbom(tmp_path, [{"name": "openssl", "version": "3.0.2"}])
    for kwargs in (
        {},
        {"attestation": str(mismatched_attestation)},
        {"sbom": str(sbom)},
        {"attestation": str(mismatched_attestation), "sbom": str(sbom)},
    ):
        block = ac.corroborate(str(artifact), **kwargs)
        assert block["corroboration"] in ac.CORROBORATIONS
        assert block["digest"]["verdict"] in ac.CORROBORATIONS
        assert block["sbom"]["verdict"] in ac.CORROBORATIONS


def test_an_spdx_document_is_read_too(artifact, tmp_path):
    """`sbom_generator` emits both serializations from one build; a
    corroboration that worked on only one goes unmeasurable on exactly the
    document a recipient was handed."""
    path = tmp_path / "sbom.spdx.json"
    path.write_text(
        json.dumps({"packages": [{"name": "openssl", "versionInfo": "3.0.2"}]}),
        encoding="utf-8",
    )

    block = ac.corroborate(str(artifact), sbom=str(path))

    assert block["sbom"]["verdict"] == ac.DISAGREES


# ---------------------------------------------------------------------------
# The verdict beside it does not move
# ---------------------------------------------------------------------------


def test_attestation_verify_is_byte_identical_with_and_without_artifact(
    artifact, mismatched_attestation
):
    from tools.devsecops.attestation_manager import verify_attestation

    policies = ["image_signature", "sbom_cyclonedx"]
    plain = verify_attestation("proj-1", "registry/app:v1.0", expected_policies=policies)
    enriched = verify_attestation(
        "proj-1",
        "registry/app:v1.0",
        expected_policies=policies,
        artifact=str(artifact),
        attestation=str(mismatched_attestation),
    )

    assert enriched[ac.RESULT_KEY]["corroboration"] == ac.DISAGREES
    # The corroboration DISAGREES and every other key is unchanged. That is the
    # property, asserted over the whole mapping rather than over one field.
    assert {k: v for k, v in enriched.items() if k != ac.RESULT_KEY} == plain
    assert ac.RESULT_KEY not in plain


def test_slsa_verify_is_byte_identical_with_and_without_artifact(
    artifact, mismatched_attestation, monkeypatch
):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.compliance.slsa_attestation_generator import verify_slsa_level

    plain = verify_slsa_level("proj-corroboration-1", 3)
    enriched = verify_slsa_level(
        "proj-corroboration-1",
        3,
        artifact=str(artifact),
        attestation=str(mismatched_attestation),
    )

    assert enriched[ac.RESULT_KEY]["corroboration"] == ac.DISAGREES
    # In particular `current_level` and `meets_target`: a triage that cannot
    # load pefile must never downgrade a SLSA level.
    assert {k: v for k, v in enriched.items() if k != ac.RESULT_KEY} == plain


def test_attach_refuses_to_overwrite_an_existing_verdict():
    """Silently overwriting one verifier's verdict with another's evidence is
    precisely the conflation this module exists to prevent."""
    with pytest.raises(ValueError):
        ac.attach({ac.RESULT_KEY: "already here"}, ac.corroborate())


def test_attach_does_not_mutate_the_result_it_was_given():
    result = {"meets_target": True}
    merged = ac.attach(result, ac.corroborate())

    assert result == {"meets_target": True}
    assert merged["meets_target"] is True
    assert merged is not result


# ---------------------------------------------------------------------------
# The MCP doors
# ---------------------------------------------------------------------------


def test_the_attestation_verify_handler_passes_the_evidence_through(
    artifact, mismatched_attestation, monkeypatch
):
    """A parameter declared in a schema and dropped by its handler is the
    declared-but-never-consumed defect at tool-call scale."""
    from tools.devsecops import attestation_manager
    from tools.mcp.devsecops_server import handle_attestation_verify

    # The profile read is not what this test is about, and leaving it live makes
    # the assertion depend on whichever database the runner happens to be on.
    monkeypatch.setattr(
        attestation_manager,
        "_get_profile",
        lambda project_id: {"active_stages": ["image_signing", "sbom_attestation"]},
    )

    plain = handle_attestation_verify({"project_id": "p1", "image": "reg/app:v1"})
    enriched = handle_attestation_verify(
        {
            "project_id": "p1",
            "image": "reg/app:v1",
            "artifact": str(artifact),
            "attestation": str(mismatched_attestation),
        }
    )

    assert ac.RESULT_KEY not in plain
    assert enriched[ac.RESULT_KEY]["corroboration"] == ac.DISAGREES
    assert {k: v for k, v in enriched.items() if k != ac.RESULT_KEY} == plain


def test_the_slsa_verify_handler_forwards_only_the_flags_it_was_given(monkeypatch):
    from tools.mcp import gap_handlers

    seen = {}

    def fake_run_cli(script, cli_args, **kwargs):
        seen["script"] = script
        seen["args"] = cli_args
        return {}

    monkeypatch.setattr(gap_handlers, "_run_cli", fake_run_cli)

    gap_handlers.handle_slsa_verify({"project_id": "p1"})
    assert "--artifact" not in seen["args"]

    gap_handlers.handle_slsa_verify({"project_id": "p1", "artifact": "a.exe", "sbom": "s.json"})
    assert seen["args"][seen["args"].index("--artifact") + 1] == "a.exe"
    assert seen["args"][seen["args"].index("--sbom") + 1] == "s.json"
    # Not supplied means not forwarded, so the CLI's own "nothing was asked"
    # branch is reachable through MCP too.
    assert "--attestation" not in seen["args"]


@pytest.mark.parametrize("tool_name", ["attestation_verify", "slsa_verify"])
def test_both_registry_schemas_declare_the_evidence_arguments(tool_name):
    from tools.mcp.tool_registry import TOOL_REGISTRY

    properties = TOOL_REGISTRY[tool_name]["input_schema"]["properties"]
    assert {"artifact", "attestation", "sbom"} <= set(properties)
    # And none of them is required: the whole point is that omitting them leaves
    # the verifier's result exactly as it was.
    required = set(TOOL_REGISTRY[tool_name]["input_schema"].get("required", []))
    assert not required & {"artifact", "attestation", "sbom"}


# ---------------------------------------------------------------------------
# Structural — a behavioural test cannot see these
# ---------------------------------------------------------------------------


def test_nothing_here_verifies_a_signature():
    """This module answers one question and the signature door answers the
    other. A behavioural test over today's callers would still pass the day
    somebody threads a `cosign` call through here."""
    source = Path(ac.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "subprocess" not in imported
    assert "cryptography" not in imported
    assert "nacl" not in imported
    # The docstring names cosign; a CALL to it is what is refused.
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run", "check_output", "Popen", "system"}
    ]
    assert calls == []
