#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-03 — Component Hash Value and Component Hash Algorithm.

The 2026 SBOM Minimum Elements make both fields mandatory and forbid omitting them:
where the author cannot access the executable component artifact, the value must be
*marked* unknown. The algorithm must be an IANA Hash Function Textual Name approved by
a relevant authority such as NIST.

These tests pin all three halves of that: the registry vocabulary, the
artifact-inaccessible path (the common case today, since the generator reads declared
manifests and never resolves an artifact), and the guarantee that no component in a
generated SBOM can leave the two fields off.
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from tools.compliance import component_hasher as ch
from tools.compliance.sbom_generator import _build_cyclonedx_sbom, _parse_package_lock_json

REPO_ROOT = Path(__file__).resolve().parent.parent

ROOT_COPY = REPO_ROOT / "tools" / "compliance" / "component_hasher.py"
MIRROR_COPY = REPO_ROOT / "icdev" / "tools" / "compliance" / "component_hasher.py"

# A real SRI digest: sha-512 of b"artifact bytes", base64-encoded, npm style.
_SAMPLE_BYTES = b"artifact bytes"
_SHA512_B64 = base64.b64encode(hashlib.sha512(_SAMPLE_BYTES).digest()).decode()
_SHA256_HEX = hashlib.sha256(_SAMPLE_BYTES).hexdigest()


def _properties(cdx_component):
    return {p["name"]: p["value"] for p in cdx_component.get("properties", [])}


# ---------------------------------------------------------------------------
# Component Hash Algorithm — IANA Hash Function Textual Names
# ---------------------------------------------------------------------------


def test_every_algorithm_this_module_can_emit_is_an_iana_registry_name():
    """Nothing may leave this module under a name the registry does not define."""
    emittable = set(ch.NIST_APPROVED_HASH_FUNCTIONS) | {ch.DEFAULT_HASH_ALGORITHM}
    unregistered = emittable - set(ch.IANA_HASH_FUNCTION_TEXTUAL_NAMES)
    assert not unregistered, f"not IANA Hash Function Textual Names: {sorted(unregistered)}"


@pytest.mark.parametrize("name", sorted(ch.NIST_APPROVED_HASH_FUNCTIONS))
def test_approved_names_are_registry_names(name):
    assert ch.is_iana_hash_name(name)
    assert ch.is_approved_hash_name(name)


@pytest.mark.parametrize("spelling", ["SHA-256", "sha256", "SHA256", "sha_256", "", None])
def test_non_registry_spellings_are_rejected(spelling):
    """The registry is lowercase and hyphenated; a near-miss spelling is not a name."""
    assert not ch.is_iana_hash_name(spelling)


@pytest.mark.parametrize("name", ["md2", "md5", "sha-1"])
def test_registry_members_that_are_not_approved(name):
    """md2/md5/sha-1 are in the registry but not approved — the repo mandates sha256."""
    assert ch.is_iana_hash_name(name)
    assert not ch.is_approved_hash_name(name)


def test_default_algorithm_is_sha256():
    assert ch.DEFAULT_HASH_ALGORITHM == "sha-256"
    assert ch.is_approved_hash_name(ch.DEFAULT_HASH_ALGORITHM)


def test_computing_with_an_unapproved_algorithm_is_refused(tmp_path):
    artifact = tmp_path / "a.whl"
    artifact.write_bytes(_SAMPLE_BYTES)
    with pytest.raises(ValueError):
        ch.hash_artifact(artifact, algorithm="md5")


# ---------------------------------------------------------------------------
# Component Hash Value — recomputation from a local artifact
# ---------------------------------------------------------------------------


def test_hash_artifact_returns_lowercase_ascii_hex(tmp_path):
    artifact = tmp_path / "pkg-1.0.0.whl"
    artifact.write_bytes(_SAMPLE_BYTES)

    result = ch.hash_artifact(artifact)

    assert result["hash_value"] == _SHA256_HEX
    assert result["hash_algorithm"] == "sha-256"
    assert result["source"] == ch.SOURCE_ARTIFACT
    assert result["reason"] is None
    # "ASCII, hexadecimal-encoded" — the standard's words.
    assert result["hash_value"].isascii()
    assert all(c in "0123456789abcdef" for c in result["hash_value"])


def test_hash_artifact_streams_a_file_larger_than_one_chunk(tmp_path):
    artifact = tmp_path / "big.bin"
    payload = b"x" * (ch._READ_CHUNK_BYTES + 1234)
    artifact.write_bytes(payload)

    assert ch.hash_artifact(artifact)["hash_value"] == hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# The artifact-inaccessible path (required by the acceptance criteria)
# ---------------------------------------------------------------------------


def test_missing_artifact_is_marked_unknown_not_omitted(tmp_path):
    result = ch.hash_artifact(tmp_path / "never-written.whl")

    assert result["hash_value"] == ch.UNKNOWN
    assert result["hash_algorithm"] == ch.UNKNOWN
    assert result["reason"] == ch.REASON_ARTIFACT_MISSING
    assert "never-written.whl" in result["detail"]


def test_directory_in_place_of_an_artifact_is_marked_unknown(tmp_path):
    target = tmp_path / "not-a-file"
    target.mkdir()

    result = ch.hash_artifact(target)

    assert result["hash_value"] == ch.UNKNOWN
    assert result["reason"] == ch.REASON_ARTIFACT_NOT_A_FILE


def test_unreadable_artifact_is_marked_unknown_and_does_not_raise(tmp_path, monkeypatch):
    """A permission failure must degrade to the unknown marker, never to an exception.

    chmod is not load-bearing on Windows, so the denial is injected at open().
    """
    artifact = tmp_path / "locked.whl"
    artifact.write_bytes(_SAMPLE_BYTES)

    def _deny(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("tools.compliance.component_hasher.open", _deny, raising=False)

    result = ch.hash_artifact(artifact)

    assert result["hash_value"] == ch.UNKNOWN
    assert result["hash_algorithm"] == ch.UNKNOWN
    assert result["reason"] == ch.REASON_ARTIFACT_UNREADABLE
    assert "Permission denied" in result["detail"]


def test_component_with_no_artifact_and_no_digest_is_unknown_with_a_reason():
    """Today's generator reads manifests only — this is the common outcome."""
    result = ch.component_hash({"name": "flask", "version": "3.0.0", "purl": "pkg:pypi/flask@3.0.0"})

    assert result["hash_value"] == ch.UNKNOWN
    assert result["hash_algorithm"] == ch.UNKNOWN
    assert result["reason"] == ch.REASON_NO_ARTIFACT
    assert result["detail"] == "pkg:pypi/flask@3.0.0"


def test_inaccessible_artifact_reason_survives_into_the_sbom_properties(tmp_path):
    """End-to-end: an unreadable artifact still yields both fields in the document."""
    component = {
        "type": "library",
        "name": "ghost",
        "version": "1.0.0",
        "purl": "pkg:pypi/ghost@1.0.0",
        "artifact_path": str(tmp_path / "gone.whl"),
    }

    sbom, count = _build_cyclonedx_sbom({"id": "p", "name": "P"}, [component])

    assert count == 1
    props = _properties(sbom["components"][0])
    assert props[ch.PROPERTY_HASH_VALUE] == ch.UNKNOWN
    assert props[ch.PROPERTY_HASH_ALGORITHM] == ch.UNKNOWN
    assert props[ch.PROPERTY_HASH_UNKNOWN_REASON] == ch.REASON_ARTIFACT_MISSING
    # CycloneDX cannot express "unknown" inside hashes[], so it must be absent there.
    assert "hashes" not in sbom["components"][0]


# ---------------------------------------------------------------------------
# Declared digests
# ---------------------------------------------------------------------------


def test_npm_integrity_is_accepted_as_a_declared_digest():
    result = ch.parse_declared_digest(f"sha512-{_SHA512_B64}")

    assert result["hash_algorithm"] == "sha-512"
    assert result["hash_value"] == hashlib.sha512(_SAMPLE_BYTES).hexdigest()
    assert result["source"] == ch.SOURCE_DECLARED


def test_oci_style_hex_digest_is_accepted():
    result = ch.parse_declared_digest(f"sha256:{_SHA256_HEX}")

    assert result["hash_algorithm"] == "sha-256"
    assert result["hash_value"] == _SHA256_HEX


@pytest.mark.parametrize(
    "declared,reason",
    [
        ("sha512-not-base64!!", ch.REASON_DIGEST_MALFORMED),
        (f"sha256:{_SHA256_HEX[:-2]}", ch.REASON_DIGEST_MALFORMED),  # wrong length
        (_SHA256_HEX, ch.REASON_DIGEST_MALFORMED),  # bare hex, no algorithm label
        ("", ch.REASON_DIGEST_MALFORMED),
        (None, ch.REASON_DIGEST_MALFORMED),
        ("sha1-2fd4e1c67a2d28fced849ee1bb76e7391b93eb12", ch.REASON_DIGEST_ALGORITHM_NOT_APPROVED),
        ("blake3-abcdef", ch.REASON_DIGEST_ALGORITHM_UNREGISTERED),
    ],
)
def test_untrustworthy_declared_digests_become_unknown_with_a_reason(declared, reason):
    result = ch.parse_declared_digest(declared)

    assert result["hash_value"] == ch.UNKNOWN
    assert result["reason"] == reason


def test_go_sum_h1_line_is_not_accepted_as_a_component_hash():
    """h1: is a dirhash over a file listing, not a digest of the module artifact."""
    result = ch.parse_declared_digest("h1:Wj+qNAeDLnrpZgWjKPYSHK7Zx6NIEDdMuMWQmMuIcQE=")

    assert result["hash_value"] == ch.UNKNOWN
    assert result["reason"] == ch.REASON_DIGEST_ALGORITHM_UNREGISTERED


def test_local_artifact_is_preferred_over_a_declared_digest(tmp_path):
    """The card: prefer recomputing from the local artifact where one is present."""
    artifact = tmp_path / "pkg.tgz"
    artifact.write_bytes(_SAMPLE_BYTES)

    result = ch.component_hash(
        {"name": "p", "artifact_path": str(artifact), "declared_digest": f"sha512-{_SHA512_B64}"}
    )

    assert result["source"] == ch.SOURCE_ARTIFACT
    assert result["hash_algorithm"] == "sha-256"
    assert result["hash_value"] == _SHA256_HEX


def test_declared_digest_is_used_when_no_artifact_is_present():
    result = ch.component_hash({"name": "p", "declared_digest": f"sha512-{_SHA512_B64}"})

    assert result["source"] == ch.SOURCE_DECLARED
    assert result["hash_algorithm"] == "sha-512"


def test_unreadable_artifact_reports_itself_rather_than_a_bad_declared_digest(tmp_path):
    result = ch.component_hash(
        {"name": "p", "artifact_path": str(tmp_path / "gone.whl"), "declared_digest": "garbage"}
    )

    assert result["hash_value"] == ch.UNKNOWN
    assert result["reason"] == ch.REASON_ARTIFACT_MISSING


# ---------------------------------------------------------------------------
# Generator integration — no component may leave the fields off
# ---------------------------------------------------------------------------


def test_package_lock_integrity_is_carried_through_to_the_sbom(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "root"},
                    "node_modules/left-pad": {"version": "1.3.0", "integrity": f"sha512-{_SHA512_B64}"},
                },
            }
        ),
        encoding="utf-8",
    )

    components = _parse_package_lock_json(lock)
    assert components[0]["declared_digest"] == f"sha512-{_SHA512_B64}"

    sbom, _ = _build_cyclonedx_sbom({"id": "p", "name": "P"}, components)
    component = sbom["components"][0]

    assert component["hashes"] == [
        {"alg": "SHA-512", "content": hashlib.sha512(_SAMPLE_BYTES).hexdigest()}
    ]
    props = _properties(component)
    assert props[ch.PROPERTY_HASH_ALGORITHM] == "sha-512"
    assert props[ch.PROPERTY_HASH_SOURCE] == ch.SOURCE_DECLARED


def test_v1_package_lock_integrity_is_carried_through(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {"left-pad": {"version": "1.3.0", "integrity": f"sha512-{_SHA512_B64}"}},
            }
        ),
        encoding="utf-8",
    )

    components = _parse_package_lock_json(lock)

    assert components[0]["declared_digest"] == f"sha512-{_SHA512_B64}"


def test_every_component_in_a_generated_sbom_carries_both_fields(tmp_path):
    """The acceptance criterion, stated as one assertion over a mixed component set."""
    artifact = tmp_path / "real.whl"
    artifact.write_bytes(_SAMPLE_BYTES)

    components = [
        {"name": "manifest-only", "version": "1.0", "purl": "pkg:pypi/manifest-only@1.0"},
        {"name": "resolved", "version": "2.0", "purl": "pkg:npm/resolved@2.0", "artifact_path": str(artifact)},
        {"name": "declared", "version": "3.0", "purl": "pkg:npm/declared@3.0", "declared_digest": f"sha512-{_SHA512_B64}"},
        {"name": "inaccessible", "version": "4.0", "purl": "pkg:npm/inaccessible@4.0", "artifact_path": str(tmp_path / "nope")},
        {"name": "bad-digest", "version": "5.0", "purl": "pkg:npm/bad@5.0", "declared_digest": "sha256:zzz"},
    ]

    sbom, count = _build_cyclonedx_sbom({"id": "p", "name": "P"}, components)

    assert count == len(components)
    for component in sbom["components"]:
        props = _properties(component)
        value = props[ch.PROPERTY_HASH_VALUE]
        algorithm = props[ch.PROPERTY_HASH_ALGORITHM]

        assert value, f"{component['name']} omitted the Component Hash Value"
        assert algorithm, f"{component['name']} omitted the Component Hash Algorithm"

        if value == ch.UNKNOWN:
            assert algorithm == ch.UNKNOWN
            assert props[ch.PROPERTY_HASH_UNKNOWN_REASON], "unknown without a reason"
        else:
            assert ch.is_iana_hash_name(algorithm), f"{algorithm} is not an IANA registry name"
            assert ch.is_approved_hash_name(algorithm), f"{algorithm} is not NIST-approved"
            assert bytes.fromhex(value)  # ASCII hex, decodable
            assert props[ch.PROPERTY_HASH_SOURCE] in (ch.SOURCE_ARTIFACT, ch.SOURCE_DECLARED)


def test_sha224_travels_in_properties_but_not_in_cyclonedx_hashes():
    """CycloneDX has no SHA-224 in its alg enum; the element still has to be reported."""
    result = ch._known_hash("ab" * 28, "sha-224", ch.SOURCE_ARTIFACT)

    assert ch.cyclonedx_hashes(result) == []
    props = {p["name"]: p["value"] for p in ch.hash_properties(result)}
    assert props[ch.PROPERTY_HASH_ALGORITHM] == "sha-224"


def test_hash_properties_are_additive_to_existing_component_properties(tmp_path):
    """Other 2026 elements land in the same properties array — nothing may clobber it."""
    sbom, _ = _build_cyclonedx_sbom(
        {"id": "p", "name": "P"}, [{"name": "a", "version": "1", "purl": "pkg:pypi/a@1"}]
    )
    names = [p["name"] for p in sbom["components"][0]["properties"]]

    assert names.count(ch.PROPERTY_HASH_VALUE) == 1
    assert names.count(ch.PROPERTY_HASH_ALGORITHM) == 1


def test_root_and_mirror_stay_in_sync():
    """Mirror-only or root-only authoring silently drifts the two copies apart."""
    assert ROOT_COPY.read_text(encoding="utf-8") == MIRROR_COPY.read_text(encoding="utf-8"), (
        "tools/compliance/component_hasher.py and icdev/tools/compliance/component_hasher.py "
        "have diverged -- author changes in both."
    )
