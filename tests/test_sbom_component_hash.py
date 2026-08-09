#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fld-03 — Component Hash Value and Component Hash Algorithm.

Both elements are NEW in the 2026 SBOM Minimum Elements and both were absent from ICDEV
output: the generator read declared manifests and never touched an artifact, so no hash
was computable at all. The standard asks for an ASCII, hexadecimal-encoded digest of the
executable component artifact under an IANA-registered, authority-approved algorithm,
and — where the artifact is not accessible to the author — for that to be stated rather
than the field omitted.

These tests pin the four things that makes true:

1. Every algorithm name ICDEV emits is on the IANA Hash Function Textual Names registry
   AND is one an authority still approves. A registered-but-unapproved name (`sha-1`,
   `md5`) never reaches a document as the element.
2. A digest that is not over a single artifact is never adopted. `go.sum`'s `h1:` is a
   SHA-256 over a listing of per-file hashes; publishing it would tell a recipient that
   hashing the module zip reproduces it, which is false.
3. No component can leave either element off — every entry in `components[]`, and the
   document's own target component, carries a value and an algorithm, even when the
   answer is the explicit unknown marker.
4. **The artifact-inaccessible path.** It is the common outcome for this repository, and
   it has its own section below: an unreachable artifact must produce the marker, a
   shared reason, a specific detail, an empty `hashes` array and a persisted row —
   never a silent omission and never an invented digest.
"""

import base64
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.compliance import component_hasher as ch  # noqa: E402
from tools.compliance.dependency_resolver import (  # noqa: E402
    RESOLUTION_DECLARED,
    SUBJECT_MAVEN_JAR,
    SUBJECT_NPM_TARBALL,
    _parse_yarn_lock_v1,
    _resolve_cargo,
    _resolve_golang,
    _resolve_maven,
    _resolve_nuget,
    _resolve_package_lock,
    _resolve_python_lock,
)
from tools.compliance.sbom_generator import _build_cyclonedx_sbom, generate_sbom  # noqa: E402
from tools.compliance.spdx_writer import HASH_ALGORITHMS, to_spdx  # noqa: E402
from tools.compliance.unknown_information import (  # noqa: E402
    FIELD_HASH_ALGORITHM,
    FIELD_HASH_VALUE,
    PROPERTY_UNKNOWN_DETAIL_PREFIX,
    PROPERTY_UNKNOWN_PREFIX,
    REASON_ARTIFACT_NOT_ACCESSIBLE,
    REASON_CONTRACTUAL_RESTRICTION,
    UNKNOWN,
    WITHHELD,
    Disclosure,
)

ROOT_COPY = REPO_ROOT / "tools" / "compliance" / "component_hasher.py"
MIRROR_COPY = REPO_ROOT / "icdev" / "tools" / "compliance" / "component_hasher.py"

#: 64 bytes of a known value, so a base64 SHA-512 in a fixture has a checkable hex form.
_SHA512_BYTES = bytes(range(64))
SHA512_HEX = _SHA512_BYTES.hex()
SHA512_B64 = base64.b64encode(_SHA512_BYTES).decode()

SHA256_HEX_VALID = "e3b0c44298fc1c14" * 4  # 64 characters — a well-formed SHA-256
SHA256_HEX_SHORT = SHA256_HEX_VALID[:-1]  # 63 — a SHA-256 of nothing


def _properties(cdx_component):
    return {p["name"]: p["value"] for p in cdx_component.get("properties", [])}


def _component(**overrides):
    """A minimal parsed component of the shape `dependency_resolver` emits."""
    base = {
        "type": "library",
        "name": "widget",
        "version": "1.0.0",
        "purl": "pkg:npm/widget@1.0.0",
        "group": "",
        "scope": "required",
        "ecosystem": "npm",
        "key": "npm|widget@1.0.0",
        "dependencies": [],
        "resolution": "resolved",
        "direct": True,
        "declared_license": "MIT",
        "declared_hashes": [],
        "artifact_path": "",
        "artifact_subject": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The vendored IANA Hash Function Textual Names registry
# ---------------------------------------------------------------------------


def test_registry_carries_the_names_the_element_points_at():
    """A missing name would silently refuse a digest the standard accepts."""
    for name in ("md5", "sha-1", "sha-224", "sha-256", "sha-384", "sha-512"):
        assert name in ch.IANA_HASH_NAMES, f"{name} missing from the IANA registry"


def test_every_emittable_algorithm_is_registered_and_approved():
    """The element requires both. Emitting on either alone is the failure to prevent."""
    for name in ch.EMITTABLE_ALGORITHMS:
        assert ch.is_iana_hash_name(name)
        assert ch.is_approved(name), f"{name} is emitted but is not an approved algorithm"


def test_registered_but_unapproved_names_are_recognised_and_not_emittable():
    """MD5 and SHA-1 must parse — so a digest under them is refused *with its reason*."""
    for name in ("md2", "md5", "sha-1"):
        assert ch.is_iana_hash_name(name)
        assert not ch.is_approved(name)
        assert name not in ch.EMITTABLE_ALGORITHMS


def test_extendable_output_functions_validate_as_names_but_are_never_emitted():
    """A SHAKE digest means nothing without the output length the registry name omits."""
    for name in ("shake128", "shake256"):
        assert ch.is_iana_hash_name(name)
        assert ch.hex_length_of(name) is None
        assert name not in ch.EMITTABLE_ALGORITHMS


def test_default_algorithm_is_sha256_not_md5():
    """The house rule is sha256 over md5; recomputation is where it meets the element."""
    assert ch.DEFAULT_ALGORITHM == "sha-256"
    assert ch.is_approved(ch.DEFAULT_ALGORITHM)


def test_normalization_accepts_real_spellings_and_nothing_else():
    for spelling in ("SHA-256", "sha256", "SHA256", " sha_256 "):
        assert ch.normalize_algorithm(spelling) == "sha-256"
    for junk in ("", None, "crc32", "blake3", "h1"):
        assert ch.normalize_algorithm(junk) == ""


def test_normalization_never_confuses_sha3_with_sha2():
    """`sha3-256` differs from `sha-256` only in punctuation and is a DIFFERENT function.

    A normalizer that deleted separators would relabel a SHA3 digest as SHA-2, which is
    the one mistake in this area a recipient cannot detect.
    """
    assert ch.normalize_algorithm("sha3-256") == ""
    assert ch.normalize_algorithm("sha3-512") == ""


def test_cyclonedx_spelling_round_trips_into_the_spdx_enum():
    """One emission has to give both serializations the element."""
    for name in sorted(ch.EMITTABLE_ALGORITHMS):
        cdx = ch.cyclonedx_algorithm(name)
        assert cdx in HASH_ALGORITHMS, f"{cdx} has no SPDX 2.3 checksum algorithm"
        assert ch.normalize_algorithm(cdx) == name


# ---------------------------------------------------------------------------
# Reading declared digests
# ---------------------------------------------------------------------------


def test_npm_integrity_becomes_a_hexadecimal_element():
    """Subresource Integrity is base64; the element is ASCII hexadecimal."""
    result = ch.resolve_hash(
        _component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}", "subject": SUBJECT_NPM_TARBALL}])
    )
    assert result["state"] == "known"
    assert result["algorithm"] == "sha-512"
    assert result["value"] == SHA512_HEX
    assert result["method"] == ch.METHOD_DECLARED


def test_a_hexadecimal_digest_is_carried_through_lowercased():
    result = ch.resolve_hash(
        _component(
            declared_hashes=[
                {"algorithm": "sha-256", "value": SHA256_HEX_VALID.upper(), "encoding": "hex"}
            ]
        )
    )
    assert result["value"] == SHA256_HEX_VALID.lower()


def test_the_strongest_wellformed_digest_wins_and_the_choice_is_deterministic():
    """An SBOM that churns on regeneration cannot be diffed."""
    entries = [
        {"algorithm": "sha-256", "value": SHA256_HEX_VALID, "encoding": "hex"},
        {"sri": f"sha512-{SHA512_B64}"},
    ]
    first = ch.resolve_hash(_component(declared_hashes=entries))
    second = ch.resolve_hash(_component(declared_hashes=list(reversed(entries))))
    assert first["algorithm"] == second["algorithm"] == "sha-512"
    assert first["value"] == second["value"] == SHA512_HEX


def test_a_digest_whose_length_contradicts_its_label_is_refused():
    """`sha-256` over 63 hex characters is not a SHA-256 of anything."""
    result = ch.resolve_hash(
        _component(declared_hashes=[{"algorithm": "sha-256", "value": SHA256_HEX_SHORT, "encoding": "hex"}])
    )
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_MALFORMED


def test_a_weak_algorithm_is_refused_rather_than_published():
    """A digest the recipient must not rely on is worse than a stated absence."""
    result = ch.resolve_hash(
        _component(declared_hashes=[{"algorithm": "sha1", "value": "a" * 40, "encoding": "hex"}])
    )
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_WEAK_ALGORITHM


def test_a_refused_digest_is_kept_as_unadopted_evidence_not_dropped():
    result = ch.resolve_hash(
        _component(declared_hashes=[{"algorithm": "sha1", "value": "b" * 40, "encoding": "hex"}])
    )
    assert result["unadopted"], "the sidecar digest was discarded instead of carried"
    assert result["unadopted"][0]["reason"] == ch.REASON_WEAK_ALGORITHM
    assert "b" * 40 in result["unadopted"][0]["declared"]


def test_a_digest_that_is_not_over_an_artifact_is_never_adopted():
    """go.sum's `h1:` hashes a listing of per-file hashes, not the module zip."""
    result = ch.resolve_hash(
        _component(
            ecosystem="golang",
            declared_hashes=[
                {
                    "algorithm": "h1",
                    "value": SHA512_B64,
                    "encoding": "base64",
                    "artifact_digest": False,
                }
            ],
        )
    )
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_NOT_AN_ARTIFACT_DIGEST


def test_several_artifact_digests_with_no_installed_one_named_are_all_refused():
    """A Python lock pins an sdist and every platform wheel; picking one is a coin toss."""
    result = ch.resolve_hash(
        _component(
            ecosystem="python",
            declared_hashes=[
                {"algorithm": "sha256", "value": SHA256_HEX_VALID, "encoding": "hex", "ambiguous": True},
                {"algorithm": "sha256", "value": "f" * 64, "encoding": "hex", "ambiguous": True},
            ],
        )
    )
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_MANY_ARTIFACTS
    assert len(result["unadopted"]) == 2


def test_an_unregistered_algorithm_name_is_refused_by_name():
    result = ch.resolve_hash(
        _component(declared_hashes=[{"algorithm": "blake3", "value": "c" * 64, "encoding": "hex"}])
    )
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_UNREGISTERED_ALGORITHM


# ---------------------------------------------------------------------------
# Recomputation from a local artifact
# ---------------------------------------------------------------------------


def test_a_local_artifact_is_hashed_rather_than_a_declaration_repeated(tmp_path):
    """Recomputation is preferred: it is the one path where ICDEV verifies rather than repeats."""
    artifact = tmp_path / "widget-1.0.0.jar"
    artifact.write_bytes(b"artifact bytes")

    result = ch.resolve_hash(
        _component(
            ecosystem="maven",
            artifact_path=artifact,
            artifact_subject=SUBJECT_MAVEN_JAR,
            declared_hashes=[{"algorithm": "sha-256", "value": "d" * 64, "encoding": "hex"}],
        )
    )
    assert result["method"] == ch.METHOD_RECOMPUTED
    assert result["algorithm"] == "sha-256"
    assert result["value"] == ch.hash_file(artifact)
    assert result["value"] != "d" * 64, "the declared digest was repeated instead of recomputed"
    assert result["subject"] == SUBJECT_MAVEN_JAR


def test_recomputation_reads_the_bytes_and_not_the_path(tmp_path):
    """Two files with the same name and different content must not hash alike."""
    one = tmp_path / "a" / "lib.jar"
    two = tmp_path / "b" / "lib.jar"
    one.parent.mkdir()
    two.parent.mkdir()
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    assert ch.hash_file(one) != ch.hash_file(two)


def test_hash_file_returns_empty_rather_than_raising_on_a_missing_file(tmp_path):
    assert ch.hash_file(tmp_path / "not-there.jar") == ""


# ---------------------------------------------------------------------------
# THE ARTIFACT-INACCESSIBLE PATH
# ---------------------------------------------------------------------------


def test_an_artifact_path_that_does_not_exist_yields_the_unknown_marker(tmp_path):
    """The standard's own instruction: state it, do not omit the field."""
    result = ch.resolve_hash(
        _component(ecosystem="maven", artifact_path=tmp_path / "absent" / "widget.jar")
    )
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_NO_ARTIFACT
    assert result["value"] == ""
    assert result["algorithm"] == ""


def test_an_inaccessible_artifact_falls_back_to_the_producers_declared_digest(tmp_path):
    """A missing artifact is not a reason to discard a digest the lockfile does carry."""
    result = ch.resolve_hash(
        _component(
            artifact_path=tmp_path / "absent.tgz",
            declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}],
        )
    )
    assert result["state"] == "known"
    assert result["method"] == ch.METHOD_DECLARED
    assert result["value"] == SHA512_HEX


def test_a_source_that_carries_no_digest_at_all_says_so_specifically():
    """An installed Python environment is an unpacked tree — there is no artifact."""
    result = ch.resolve_hash(_component(ecosystem="python", declared_hashes=[]))
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_SOURCE_HAS_NO_DIGEST


def test_a_component_from_a_declared_manifest_says_that_instead():
    """No resolved artifact was ever identified, which is a different fact."""
    result = ch.resolve_hash(_component(resolution=RESOLUTION_DECLARED))
    assert ch.is_unknown(result)
    assert result["reason"] == ch.REASON_NOT_RESOLVED


def test_the_inaccessible_path_records_both_elements_on_the_disclosure():
    """The algorithm of a digest nobody has is not a fact about the component."""
    disclosure = ch.record_hash_disclosure(ch.unknown_hash(ch.REASON_NO_ARTIFACT))
    assert disclosure.state_of(FIELD_HASH_VALUE) == UNKNOWN
    assert disclosure.state_of(FIELD_HASH_ALGORITHM) == UNKNOWN
    assert disclosure.reason_for(FIELD_HASH_VALUE) == REASON_ARTIFACT_NOT_ACCESSIBLE
    assert disclosure.details[FIELD_HASH_VALUE] == ch.REASON_NO_ARTIFACT


def test_the_inaccessible_path_uses_the_shared_vocabulary_not_a_marker_of_its_own():
    """sbx-prc-01 defines one convention; a second spelling of "unknown" is the drift."""
    disclosure = ch.record_hash_disclosure(ch.unknown_hash(ch.REASON_SOURCE_HAS_NO_DIGEST))
    names = {p["name"] for p in disclosure.properties()}
    assert f"{PROPERTY_UNKNOWN_PREFIX}{FIELD_HASH_VALUE}" in names
    assert f"{PROPERTY_UNKNOWN_PREFIX}{FIELD_HASH_ALGORITHM}" in names
    assert f"{PROPERTY_UNKNOWN_DETAIL_PREFIX}{FIELD_HASH_VALUE}" in names


def test_the_inaccessible_path_emits_no_hashes_array_and_still_states_both_elements():
    """CycloneDX cannot say "unknown" inside `hashes`; an invented entry there is worse."""
    result = ch.unknown_hash(ch.REASON_NO_ARTIFACT)
    disclosure = ch.record_hash_disclosure(result)
    cdx = ch.apply_hash_to_cyclonedx({"name": "widget"}, result, disclosure)

    assert "hashes" not in cdx
    properties = _properties(cdx)
    assert properties[ch.PROPERTY_HASH] == UNKNOWN
    assert properties[ch.PROPERTY_ALGORITHM] == UNKNOWN


def test_the_inaccessible_path_persists_as_the_marker_never_as_null():
    """A row silent about a hash is indistinguishable from one that was never asked."""
    result = ch.unknown_hash(ch.REASON_NO_ARTIFACT)
    disclosure = ch.record_hash_disclosure(result)
    assert ch.hash_db_value(result, disclosure) == (UNKNOWN, UNKNOWN)


def test_an_inaccessible_artifact_is_valid_conformance_not_an_error():
    """The element permits it. What it does not permit is silence."""
    result = ch.unknown_hash(ch.REASON_NO_ARTIFACT)
    disclosure = ch.record_hash_disclosure(result)
    component = ch.apply_hash_to_cyclonedx({"name": "widget", "bom-ref": "x"}, result, disclosure)
    component["properties"].extend(disclosure.properties())

    report = ch.validate_sbom_hashes({"components": [component]})
    assert report["valid"], report["errors"]
    assert report["hashes_undisclosed"] == 1


def test_an_unknown_hash_with_no_recorded_reason_fails_validation():
    """The marker alone is not conformance — the standard asks *why*."""
    component = {
        "name": "widget",
        "bom-ref": "x",
        "properties": [
            {"name": ch.PROPERTY_HASH, "value": UNKNOWN},
            {"name": ch.PROPERTY_ALGORITHM, "value": UNKNOWN},
        ],
    }
    report = ch.validate_sbom_hashes({"components": [component]})
    assert not report["valid"]
    assert "no reason is recorded" in report["errors"][0]


def test_a_document_that_states_the_element_natively_needs_no_icdev_property():
    """CycloneDX `hashes` IS the element. A third-party document conforms without us.

    The properties exist only because CycloneDX cannot spell "unknown" inside `hashes`.
    Requiring them would report every conformant SBOM this generator did not write — and
    its own output re-read by a consumer that kept only the standard fields — as stating
    nothing at all, which is the opposite of what the element asks.
    """
    component = {
        "name": "widget",
        "bom-ref": "x",
        "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
    }
    report = ch.validate_sbom_hashes({"components": [component]})
    assert report["valid"], report["errors"]
    assert report["hashes_stated"] == 1


def test_a_component_stating_the_element_neither_way_is_the_only_failure():
    """No property pair and no `hashes` entry is the omission the standard forbids."""
    report = ch.validate_sbom_hashes({"components": [{"name": "widget", "bom-ref": "x"}]})
    assert not report["valid"]
    assert "not stated at all" in report["errors"][0]


def test_a_native_statement_under_an_unregistered_algorithm_is_still_refused():
    """The fallback reads the document; it does not lower the bar the element sets."""
    component = {
        "name": "widget",
        "bom-ref": "x",
        "hashes": [{"alg": "BLAKE3", "content": "b" * 64}],
    }
    report = ch.validate_sbom_hashes({"components": [component]})
    assert not report["valid"]
    assert "not stated at all" in report["errors"][0]


# ---------------------------------------------------------------------------
# Withholding (sbx-prc-01)
# ---------------------------------------------------------------------------


def test_a_withheld_hash_suppresses_the_digest_and_every_supporting_fact():
    """A digest left in place beside a withheld marker publishes what was withheld."""
    result = ch.resolve_hash(_component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}]))
    disclosure = Disclosure().withheld(FIELD_HASH_VALUE, REASON_CONTRACTUAL_RESTRICTION)

    cdx = ch.apply_hash_to_cyclonedx({"name": "widget"}, result, disclosure)
    properties = _properties(cdx)

    assert "hashes" not in cdx
    assert properties[ch.PROPERTY_HASH] == WITHHELD
    assert SHA512_HEX not in json.dumps(cdx)
    assert ch.PROPERTY_EVIDENCE not in properties
    assert ch.PROPERTY_UNADOPTED not in properties


def test_withholding_outranks_an_unknown_hash():
    """"We are not telling you" is the stronger and more actionable statement."""
    disclosure = Disclosure().withheld(FIELD_HASH_VALUE, REASON_CONTRACTUAL_RESTRICTION)
    ch.record_hash_disclosure(ch.unknown_hash(ch.REASON_NO_ARTIFACT), disclosure)
    assert disclosure.state_of(FIELD_HASH_VALUE) == WITHHELD


# ---------------------------------------------------------------------------
# Per-ecosystem digest sources
# ---------------------------------------------------------------------------


def test_package_lock_v2_carries_the_tarball_integrity(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "root"},
                    "node_modules/widget": {"version": "1.0.0", "integrity": f"sha512-{SHA512_B64}"},
                },
            }
        ),
        encoding="utf-8",
    )
    result = _resolve_package_lock(lock)
    hashed = ch.resolve_hash(result["components"][0])
    assert hashed["value"] == SHA512_HEX
    assert hashed["subject"] == SUBJECT_NPM_TARBALL


def test_a_sha1_only_package_lock_entry_produces_the_unknown_marker(tmp_path):
    """npm lockfiles predating npm 5 carry `sha1-` SRI, which is not adoptable."""
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {
                    "widget": {"version": "1.0.0", "integrity": "sha1-" + base64.b64encode(bytes(20)).decode()}
                },
            }
        ),
        encoding="utf-8",
    )
    result = _resolve_package_lock(lock)
    hashed = ch.resolve_hash(result["components"][0])
    assert ch.is_unknown(hashed)
    assert hashed["reason"] == ch.REASON_WEAK_ALGORITHM


def test_yarn_v1_integrity_is_read():
    entries = _parse_yarn_lock_v1(
        'widget@^1.0.0:\n  version "1.0.0"\n  resolved "https://r/widget-1.0.0.tgz"\n'
        f'  integrity sha512-{SHA512_B64}\n'
    )
    assert entries[0]["integrity"] == f"sha512-{SHA512_B64}"


def test_cargo_lock_checksum_is_the_crate_digest(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "root"\n', encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text(
        '[[package]]\nname = "serde"\nversion = "1.0.0"\n'
        f'checksum = "{SHA256_HEX_VALID}"\n',
        encoding="utf-8",
    )
    result = _resolve_cargo(tmp_path)
    hashed = ch.resolve_hash(result["components"][0])
    assert hashed["algorithm"] == "sha-256"
    assert hashed["value"] == SHA256_HEX_VALID


def test_go_sum_digests_are_carried_and_then_refused(tmp_path):
    """Carried so nothing is dropped, refused so nothing is misstated."""
    (tmp_path / "go.mod").write_text(
        "module example.com/x\n\ngo 1.21\n\nrequire example.com/dep v1.2.3\n", encoding="utf-8"
    )
    (tmp_path / "go.sum").write_text(
        f"example.com/dep v1.2.3 h1:{SHA512_B64}\n"
        f"example.com/dep v1.2.3/go.mod h1:{SHA512_B64}\n",
        encoding="utf-8",
    )
    result = _resolve_golang(tmp_path)
    component = result["components"][0]
    assert component["declared_hashes"], "the go.sum line was never read"

    hashed = ch.resolve_hash(component)
    assert ch.is_unknown(hashed)
    assert hashed["reason"] == ch.REASON_NOT_AN_ARTIFACT_DIGEST
    assert hashed["unadopted"][0]["declared"].startswith("h1:")


def test_nuget_project_assets_sha512_is_read_from_libraries(tmp_path):
    obj = tmp_path / "obj"
    obj.mkdir()
    (obj / "project.assets.json").write_text(
        json.dumps(
            {
                "targets": {"net8.0": {"Newtonsoft.Json/13.0.3": {"type": "package"}}},
                "libraries": {"Newtonsoft.Json/13.0.3": {"type": "package", "sha512": SHA512_B64}},
            }
        ),
        encoding="utf-8",
    )
    result = _resolve_nuget(tmp_path)
    hashed = ch.resolve_hash(result["components"][0])
    assert hashed["algorithm"] == "sha-512"
    assert hashed["value"] == SHA512_HEX


def test_a_python_lock_with_one_artifact_is_adopted(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "widget"\nversion = "1.0.0"\n'
        f'[package.sdist]\nhash = "sha256:{SHA256_HEX_VALID}"\n',
        encoding="utf-8",
    )
    result = _resolve_python_lock(lock, "uv.lock")
    hashed = ch.resolve_hash(result["components"][0])
    assert hashed["value"] == SHA256_HEX_VALID


def test_a_python_lock_with_many_artifacts_names_none_of_them(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "widget"\nversion = "1.0.0"\n'
        f'[package.sdist]\nhash = "sha256:{SHA256_HEX_VALID}"\n'
        f'[[package.wheels]]\nhash = "sha256:{"a" * 64}"\n',
        encoding="utf-8",
    )
    result = _resolve_python_lock(lock, "uv.lock")
    hashed = ch.resolve_hash(result["components"][0])
    assert ch.is_unknown(hashed)
    assert hashed["reason"] == ch.REASON_MANY_ARTIFACTS


def test_maven_recomputes_from_the_local_repository_jar(tmp_path, monkeypatch):
    """The one ecosystem whose executable artifact is genuinely on disk."""
    repository = tmp_path / "m2"
    artifact_dir = repository / "com" / "example" / "widget" / "1.0.0"
    artifact_dir.mkdir(parents=True)
    jar = artifact_dir / "widget-1.0.0.jar"
    jar.write_bytes(b"class bytes")
    monkeypatch.setenv("MAVEN_REPO_LOCAL", str(repository))

    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>", encoding="utf-8")
    (project / "dependency-list.txt").write_text(
        "   com.example:widget:jar:1.0.0:compile\n", encoding="utf-8"
    )

    result = _resolve_maven(project)
    hashed = ch.resolve_hash(result["components"][0])
    assert hashed["method"] == ch.METHOD_RECOMPUTED
    assert hashed["value"] == ch.hash_file(jar)


def test_maven_refuses_a_sha1_sidecar_when_the_jar_is_absent(tmp_path, monkeypatch):
    repository = tmp_path / "m2"
    artifact_dir = repository / "com" / "example" / "widget" / "1.0.0"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "widget-1.0.0.jar.sha1").write_text("a" * 40 + "\n", encoding="utf-8")
    monkeypatch.setenv("MAVEN_REPO_LOCAL", str(repository))

    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>", encoding="utf-8")
    (project / "dependency-list.txt").write_text(
        "   com.example:widget:jar:1.0.0:compile\n", encoding="utf-8"
    )

    result = _resolve_maven(project)
    hashed = ch.resolve_hash(result["components"][0])
    assert ch.is_unknown(hashed)
    assert hashed["reason"] == ch.REASON_WEAK_ALGORITHM
    assert hashed["unadopted"][0]["declared"].endswith("a" * 40)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def _document(components, **kwargs):
    sbom, _count = _build_cyclonedx_sbom(
        {"id": "proj-1", "name": "target", "directory_path": None}, components, **kwargs
    )
    return sbom


def test_no_component_can_leave_either_element_off():
    """The pair of properties is what makes the elements unomittable."""
    sbom = _document(
        [
            _component(name="hashed", declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}]),
            _component(name="unhashed", key="npm|unhashed", purl="pkg:npm/unhashed@1.0.0"),
            _component(
                name="refused",
                key="npm|refused",
                purl="pkg:npm/refused@1.0.0",
                declared_hashes=[{"algorithm": "md5", "value": "a" * 32, "encoding": "hex"}],
            ),
        ]
    )
    everything = list(sbom["components"]) + [sbom["metadata"]["component"]]
    assert len(everything) == 4
    for component in everything:
        properties = _properties(component)
        assert ch.PROPERTY_HASH in properties, component["name"]
        assert ch.PROPERTY_ALGORITHM in properties, component["name"]
        assert properties[ch.PROPERTY_HASH], component["name"]


def test_the_target_component_states_that_it_is_not_built_yet():
    """ICDEV generates before build, so its own artifact genuinely does not exist."""
    target = _document([_component()])["metadata"]["component"]
    properties = _properties(target)
    assert properties[ch.PROPERTY_HASH] == UNKNOWN
    assert properties[f"{PROPERTY_UNKNOWN_PREFIX}{FIELD_HASH_VALUE}"] == REASON_ARTIFACT_NOT_ACCESSIBLE
    assert (
        properties[f"{PROPERTY_UNKNOWN_DETAIL_PREFIX}{FIELD_HASH_VALUE}"]
        == ch.REASON_TARGET_NOT_BUILT
    )
    assert "hashes" not in target


def test_the_conformance_gate_scores_both_elements_met_on_a_real_document():
    """A document where every artifact was out of reach still MEETS the elements.

    The standard's own text says so, and the gate scored the `hashes` array alone — which
    would have reported a conformant SBOM as a gap forever, because a generator reading
    lockfiles could never have filled that array for an unpacked install tree.
    """
    from tools.compliance.sbom_conformance_gate import score_sbom

    sbom = _document(
        [
            _component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}]),
            _component(name="plain", key="npm|plain", purl="pkg:npm/plain@1.0.0"),
        ]
    )
    elements = score_sbom(sbom)["elements"]
    assert elements["component_hash_value"] == "met"
    assert elements["component_hash_algorithm"] == "met"


def test_the_conformance_gate_still_reports_a_component_that_states_nothing():
    """Accepting the unknown marker must not become accepting silence."""
    from tools.compliance.sbom_conformance_gate import score_sbom

    sbom = _document([_component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}])])
    for component in list(sbom["components"]) + [sbom["metadata"]["component"]]:
        component.pop("hashes", None)
        component["properties"] = [
            p for p in component["properties"] if not p["name"].startswith("icdev:component-hash")
        ]

    elements = score_sbom(sbom)["elements"]
    assert elements["component_hash_value"] == "gap"
    assert elements["component_hash_algorithm"] == "gap"


def test_the_conformance_gate_accepts_a_third_party_documents_native_hashes():
    """`--sbom` takes documents ICDEV did not write; they carry no `icdev:` properties."""
    from tools.compliance.sbom_conformance_gate import score_sbom

    foreign = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "name": "app",
                "hashes": [{"alg": "SHA-256", "content": SHA256_HEX_VALID}],
            }
        },
        "components": [
            {"name": "dep", "hashes": [{"alg": "SHA-512", "content": SHA512_HEX}]},
        ],
    }
    elements = score_sbom(foreign)["elements"]
    assert elements["component_hash_value"] == "met"
    assert elements["component_hash_algorithm"] == "met"


def test_a_hashed_component_carries_the_native_cyclonedx_slot():
    sbom = _document([_component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}])])
    component = sbom["components"][0]
    assert component["hashes"] == [{"alg": "SHA-512", "content": SHA512_HEX}]


def test_the_document_validates_against_both_elements():
    sbom = _document(
        [
            _component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}]),
            _component(name="plain", key="npm|plain", purl="pkg:npm/plain@1.0.0"),
        ]
    )
    report = ch.validate_sbom_hashes(sbom)
    assert report["valid"], report["errors"]
    assert report["hashes_stated"] == 1
    assert report["hashes_undisclosed"] == 2  # the unhashed dependency and the target


def test_the_elements_survive_translation_to_spdx():
    """`hashes` is the one slot; spdx_writer turns it into `checksums`."""
    sbom = _document([_component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}])])
    spdx = to_spdx(sbom)
    package = next(p for p in spdx["packages"] if p["name"] == "widget")
    assert package["checksums"] == [{"algorithm": "SHA512", "checksumValue": SHA512_HEX}]


def test_regeneration_produces_the_same_document():
    """A hash element that churns between runs cannot be diffed across releases."""
    components = [
        _component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}]),
        _component(name="plain", key="npm|plain", purl="pkg:npm/plain@1.0.0"),
    ]
    first = _document(components, serial_number="urn:uuid:fixed")
    second = _document(components, serial_number="urn:uuid:fixed")
    first["metadata"]["timestamp"] = second["metadata"]["timestamp"] = "fixed"
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_an_algorithm_a_document_states_is_always_a_registered_iana_name():
    """The acceptance criterion, checked over a whole document rather than a unit."""
    sbom = _document(
        [
            _component(declared_hashes=[{"sri": f"sha512-{SHA512_B64}"}]),
            _component(
                name="c256",
                key="npm|c256",
                purl="pkg:npm/c256@1.0.0",
                declared_hashes=[{"algorithm": "sha256", "value": SHA256_HEX_VALID, "encoding": "hex"}],
            ),
        ]
    )
    for component in list(sbom["components"]) + [sbom["metadata"]["component"]]:
        algorithm = _properties(component)[ch.PROPERTY_ALGORITHM]
        if algorithm in (UNKNOWN, WITHHELD):
            continue
        assert ch.is_iana_hash_name(algorithm), algorithm
        assert ch.is_approved(algorithm), algorithm
        for entry in component.get("hashes") or []:
            assert ch.normalize_algorithm(entry["alg"]) == algorithm


# ---------------------------------------------------------------------------
# Persistence — sbom_components.hash_value / hash_algorithm
# ---------------------------------------------------------------------------


#: The generator writes every column the sbx- migrations added on each run, so a fixture
#: schema that stops at migration 209 fails for reasons unrelated to the element under
#: test. Mirrors `tests/test_sbom_component_license.py::_MINIMAL_SCHEMA`.
_MINIMAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI',
    status TEXT NOT NULL DEFAULT 'active',
    directory_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'cyclonedx',
    file_path TEXT NOT NULL,
    component_count INTEGER,
    vulnerability_count INTEGER,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    sbom_author TEXT,
    author_signature TEXT,
    signature_algorithm TEXT,
    data_format_name TEXT,
    data_format_version TEXT,
    generation_context TEXT,
    tool_name TEXT,
    tool_version TEXT,
    sbom_version TEXT,
    serial_number TEXT,
    supersedes_sbom_id INTEGER REFERENCES sbom_records(id),
    content_digest TEXT,
    source_revision TEXT,
    revision_reason TEXT
);
CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT,
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    producer             TEXT,
    hash_value           TEXT,
    hash_algorithm       TEXT,
    identifiers_json     TEXT NOT NULL DEFAULT '{}',
    unknown_fields_json  TEXT NOT NULL DEFAULT '{}',
    withheld_fields_json TEXT NOT NULL DEFAULT '{}',
    tenant_id            TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def test_persisted_rows_carry_the_hash_and_its_algorithm(tmp_path, monkeypatch):
    """Both columns landed unwritten in migration 20260808030213 — this is their writer."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "root"},
                    "node_modules/widget": {"version": "1.0.0", "integrity": f"sha512-{SHA512_B64}"},
                    "node_modules/plain": {"version": "2.0.0"},
                },
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        ("proj-1", "target", "cli", str(project_dir)),
    )
    conn.commit()
    conn.close()

    generate_sbom("proj-1", output_path=str(tmp_path / "sbom.cdx.json"), db_path=db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT component_name, hash_value, hash_algorithm FROM sbom_components"
    ).fetchall()
    conn.close()

    assert rows, "no sbom_components rows were written"
    by_name = {name: (value, algorithm) for name, value, algorithm in rows}
    assert by_name["widget"] == (SHA512_HEX, "sha-512")
    assert by_name["plain"] == (UNKNOWN, UNKNOWN)
    for _name, (value, algorithm) in by_name.items():
        assert value and algorithm, "a hash column was left NULL or empty"


# ---------------------------------------------------------------------------
# Mirror parity
# ---------------------------------------------------------------------------


def test_the_module_exists_in_both_namespaces_and_is_identical():
    """`icdev.tools.*` is canonical and `tools.*` is the shim; a drift breaks one of them."""
    assert ROOT_COPY.exists(), f"{ROOT_COPY} is missing"
    assert MIRROR_COPY.exists(), f"{MIRROR_COPY} is missing"
    assert ROOT_COPY.read_text(encoding="utf-8") == MIRROR_COPY.read_text(encoding="utf-8")


def test_the_cli_lists_the_registry():
    """Documented in docs/reference/commands.md, so it has to run."""
    import subprocess

    completed = subprocess.run(
        [sys.executable, "tools/compliance/component_hasher.py", "--registry", "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert set(payload["names"]) == set(ch.IANA_HASH_NAMES)
