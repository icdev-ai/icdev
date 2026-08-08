#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-sig-01 — the SBOM Author Signature element of the 2026 Minimum Elements.

Module under test: ``tools/compliance/sbom_signer.py`` (mirrored at
``icdev/tools/compliance/``), plus its wiring into
``tools/compliance/sbom_generator.py``.

Every signing test here uses a REAL keypair generated into ``tmp_path`` and a
real signature over a real file. Nothing is mocked, because the thing being
asserted is that the cryptography round-trips — a test that stubbed
``sign_artifact`` would pass for a signer that emits a constant.

The four properties the card is graded on, and where each is pinned:

  round-trip      test_sign_then_verify_round_trips
  tamper detected test_a_tampered_sbom_fails_verification
  approved algo   test_only_nist_approved_algorithms_are_accepted and the
                  refusal tests below it
  works offline   test_verification_needs_neither_the_private_key_nor_a_network
                  and test_the_signer_has_no_network_import
  persisted       test_the_signature_and_algorithm_are_persisted_to_sbom_records
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.compliance import sbom_signer
from tools.compliance.sbom_signer import (
    APPROVED_ALGORITHMS,
    SbomSigningError,
    sign_sbom,
    signature_path_for,
    verify_sbom,
)
from tools.crypto.key_manager import generate_keypair, sign_payload

SBOM_FIXTURE = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.4",
    "serialNumber": "urn:uuid:6f6b8d2e-0a1b-4c3d-8e9f-000000000001",
    "version": 1,
    "metadata": {"timestamp": "2026-08-08T00:00:00Z"},
    "components": [
        {"type": "library", "bom-ref": "a", "name": "alpha", "version": "1.0.0"},
        {"type": "library", "bom-ref": "b", "name": "beta", "version": "2.0.0"},
    ],
}


@pytest.fixture(autouse=True)
def no_ambient_signing_config(monkeypatch):
    """A host that happens to export a signing key must not decide these results."""
    monkeypatch.delenv("ICDEV_SBOM_SIGNING_KEY_PATH", raising=False)
    monkeypatch.delenv("ICDEV_AUDIT_SIGNING_KEY_PATH", raising=False)
    monkeypatch.delenv("ICDEV_AUDIT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("ICDEV_SBOM_REQUIRE_SIGNATURE", raising=False)


@pytest.fixture
def keypair(tmp_path):
    return generate_keypair(tmp_path / "keys", "ecdsa-p256")


@pytest.fixture
def sbom_file(tmp_path):
    path = tmp_path / "sbom_fixture.cdx.json"
    path.write_text(json.dumps(SBOM_FIXTURE, indent=2), encoding="utf-8")
    return path


def _rewrite(path, mutate):
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def _write_key(path, private_key):
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(path)


# ---------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------
def test_sign_then_verify_round_trips(sbom_file, keypair):
    block = sign_sbom(sbom_file, key_path=keypair["private_key"])

    assert block["algorithm"] == "ECDSA-P256-SHA256"
    assert block["value"]
    assert block["public_key_fp"] == keypair["public_key_fp"]

    result = verify_sbom(sbom_file)
    assert result["verified"] is True
    assert result["algorithm_approved"] is True
    assert result["bytes_modified"] is False
    assert result["reason"] == ""


def test_the_signature_is_detached_and_leaves_the_sbom_byte_identical(sbom_file, keypair):
    """A signed SBOM must still parse for every consumer that never heard of us."""
    before = sbom_file.read_bytes()
    sign_sbom(sbom_file, key_path=keypair["private_key"])

    assert sbom_file.read_bytes() == before
    sig_path = signature_path_for(sbom_file)
    assert sig_path.is_file()
    assert sig_path.name == "sbom_fixture.cdx.json.sig.json"


def test_ed25519_round_trips(sbom_file, tmp_path):
    """The second algorithm FIPS 186-5 approves; both branches must work."""
    keys = generate_keypair(tmp_path / "ed", "ed25519")
    block = sign_sbom(sbom_file, key_path=keys["private_key"])

    assert block["algorithm"] == "Ed25519"
    assert verify_sbom(sbom_file)["verified"] is True


def test_verification_reports_integrity_and_authorship_separately(sbom_file, keypair):
    """`verified` alone does not say WHO signed. Collapsing the two would overstate it."""
    sign_sbom(sbom_file, key_path=keypair["private_key"])

    unpinned = verify_sbom(sbom_file)
    assert unpinned["verified"] is True
    assert unpinned["trusted"] is None, "authorship must be 'unknown', never assumed True"

    pinned = verify_sbom(sbom_file, expected_fp=keypair["public_key_fp"])
    assert pinned["verified"] is True
    assert pinned["trusted"] is True


# ---------------------------------------------------------------------------
# tampering
# ---------------------------------------------------------------------------
def test_a_tampered_sbom_fails_verification(sbom_file, keypair):
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    _rewrite(sbom_file, lambda d: d["components"].append({"name": "backdoor", "version": "9.9.9"}))

    result = verify_sbom(sbom_file)
    assert result["verified"] is False
    assert "modified after signing" in result["reason"]


def test_removing_a_component_fails_verification(sbom_file, keypair):
    """Deletion is the tamper that matters most: it hides a vulnerable component."""
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    _rewrite(sbom_file, lambda d: d["components"].pop())

    assert verify_sbom(sbom_file)["verified"] is False


def test_changing_a_single_component_version_fails_verification(sbom_file, keypair):
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    _rewrite(sbom_file, lambda d: d["components"][0].update({"version": "1.0.1"}))

    assert verify_sbom(sbom_file)["verified"] is False


def test_a_tampered_sbom_resigned_by_another_key_fails_a_pinned_fingerprint(sbom_file, tmp_path):
    """The attack an embedded public key invites, and the defence against it.

    An attacker who rewrites the SBOM can re-sign it with their own key and
    overwrite the public key inside the detached file, so unpinned verification
    reports intact — truthfully, since the document does match its signature.
    Authorship is what is broken, and pinning the fingerprint out of band is
    what detects it. This test exists so nobody 'fixes' verify_sbom into
    claiming unpinned verification proves authorship.
    """
    author = generate_keypair(tmp_path / "author", "ecdsa-p256")
    attacker = generate_keypair(tmp_path / "attacker", "ecdsa-p256")

    sign_sbom(sbom_file, key_path=author["private_key"])
    _rewrite(sbom_file, lambda d: d["components"].append({"name": "backdoor", "version": "9.9.9"}))
    sign_sbom(sbom_file, key_path=attacker["private_key"])

    unpinned = verify_sbom(sbom_file)
    assert unpinned["verified"] is True
    assert unpinned["trusted"] is None

    pinned = verify_sbom(sbom_file, expected_fp=author["public_key_fp"])
    assert pinned["verified"] is False
    assert pinned["trusted"] is False
    assert "signed by a different key" in pinned["reason"]


def test_reformatting_is_not_tampering_but_is_reported(sbom_file, keypair):
    """The signature covers the bill of materials, not the indentation.

    A signature that broke when a pipeline pretty-printed the file is one people
    learn to ignore. The byte change is still surfaced, just not as a failure.
    """
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    doc = json.loads(sbom_file.read_text(encoding="utf-8"))
    sbom_file.write_text(json.dumps(doc, indent=8, sort_keys=True), encoding="utf-8")

    result = verify_sbom(sbom_file)
    assert result["verified"] is True
    assert result["bytes_modified"] is True


def test_a_signature_from_a_different_sbom_does_not_verify(tmp_path, keypair):
    """Swapping in a valid signature file from another artifact must not pass."""
    first = tmp_path / "first.cdx.json"
    second = tmp_path / "second.cdx.json"
    first.write_text(json.dumps(SBOM_FIXTURE), encoding="utf-8")
    second.write_text(json.dumps({**SBOM_FIXTURE, "components": []}), encoding="utf-8")

    sign_sbom(first, key_path=keypair["private_key"])
    signature_path_for(second).write_text(
        signature_path_for(first).read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert verify_sbom(second)["verified"] is False


def test_an_unsigned_sbom_reports_unsigned_rather_than_raising(sbom_file):
    result = verify_sbom(sbom_file)
    assert result["verified"] is False
    assert "unsigned" in result["reason"].lower()


def test_a_corrupt_signature_file_reports_rather_than_raising(sbom_file, keypair):
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    signature_path_for(sbom_file).write_text("{not json", encoding="utf-8")

    result = verify_sbom(sbom_file)
    assert result["verified"] is False
    assert "not valid JSON" in result["reason"]


# ---------------------------------------------------------------------------
# approved algorithms — the standard's hard requirement
# ---------------------------------------------------------------------------
def test_only_nist_approved_algorithms_are_accepted():
    """The approved set is exactly the FIPS 186-5 algorithms this tree can emit."""
    assert set(APPROVED_ALGORITHMS) == {
        "ECDSA-P256-SHA256",
        "ECDSA-P384-SHA256",
        "ECDSA-P521-SHA256",
        "Ed25519",
    }
    for authority in APPROVED_ALGORITHMS.values():
        assert "FIPS 186-5" in authority and "ENISA" in authority


def test_hmac_is_refused_as_an_author_signature(sbom_file, monkeypatch):
    """A MAC anyone who can verify can also forge is not attributable to an author.

    key_manager falls back to HMAC-SHA256 by design, so audit logging never
    breaks. That fallback must not reach an SBOM Author Signature.
    """
    monkeypatch.setenv("ICDEV_AUDIT_HMAC_SECRET", "dev-secret")

    # Precondition: the underlying primitive really would hand us an HMAC here.
    assert sign_payload(b"probe")["algorithm"] == "HMAC-SHA256"

    with pytest.raises(SbomSigningError) as excinfo:
        sign_sbom(sbom_file)
    assert not signature_path_for(sbom_file).exists(), "a refused signing wrote a file anyway"
    assert "symmetric" in str(excinfo.value) or "No signing key" in str(excinfo.value)


def test_an_hmac_signature_file_is_refused_at_verification_too(sbom_file, keypair):
    """Verification enforces the algorithm gate independently of signing."""
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    sig_path = signature_path_for(sbom_file)
    block = json.loads(sig_path.read_text(encoding="utf-8"))
    block["algorithm"] = "HMAC-SHA256"
    sig_path.write_text(json.dumps(block), encoding="utf-8")

    result = verify_sbom(sbom_file)
    assert result["verified"] is False
    assert result["algorithm_approved"] is False
    assert "symmetric MAC" in result["reason"]


def test_signing_without_any_key_is_refused(sbom_file):
    with pytest.raises(SbomSigningError) as excinfo:
        sign_sbom(sbom_file)
    assert "No signing key is configured" in str(excinfo.value)
    assert not signature_path_for(sbom_file).exists()


def test_a_non_nist_curve_is_labelled_truthfully_and_refused(sbom_file, tmp_path):
    """Regression: key_manager used to call every EC key 'ECDSA-P256-SHA256'.

    secp256k1 is approved by no authority the 2026 standard names. Under the old
    constant label it would have been signed, recorded and reported as P-256 —
    an approved-algorithm check that reads `algorithm` would have passed on a
    key that is not approved. The label now names the curve the key actually
    uses, which is what makes APPROVED_ALGORITHMS mean anything.
    """
    key_path = _write_key(tmp_path / "k1.pem", ec.generate_private_key(ec.SECP256K1()))

    assert sign_payload(b"probe", key_path=key_path)["algorithm"] == "ECDSA-secp256k1-SHA256"

    with pytest.raises(SbomSigningError) as excinfo:
        sign_sbom(sbom_file, key_path=key_path)
    assert "secp256k1" in str(excinfo.value)
    assert "not approved" in str(excinfo.value)


@pytest.mark.parametrize(
    "curve,expected",
    [
        (ec.SECP384R1(), "ECDSA-P384-SHA256"),
        (ec.SECP521R1(), "ECDSA-P521-SHA256"),
    ],
    ids=["p384", "p521"],
)
def test_the_other_approved_nist_curves_round_trip(sbom_file, tmp_path, curve, expected):
    key_path = _write_key(tmp_path / "curve.pem", ec.generate_private_key(curve))

    block = sign_sbom(sbom_file, key_path=key_path)
    assert block["algorithm"] == expected
    assert verify_sbom(sbom_file)["verified"] is True


def test_a_missing_key_file_is_refused_by_path(sbom_file, tmp_path):
    with pytest.raises(SbomSigningError) as excinfo:
        sign_sbom(sbom_file, key_path=str(tmp_path / "absent.pem"))
    assert "not found" in str(excinfo.value)


# ---------------------------------------------------------------------------
# air gap
# ---------------------------------------------------------------------------
def test_verification_needs_neither_the_private_key_nor_a_network(sbom_file, tmp_path, monkeypatch):
    """The consumer's position: holds the SBOM and the .sig.json, nothing else.

    The private key is deleted outright before verifying, so this cannot pass by
    quietly re-deriving the public key from it. No sigstore, no Fulcio, no Rekor
    and no OCSP are involved on either path.
    """
    keys = generate_keypair(tmp_path / "keys", "ecdsa-p256")
    sign_sbom(sbom_file, key_path=keys["private_key"])

    Path(keys["private_key"]).unlink()
    Path(keys["public_key"]).unlink()
    monkeypatch.delenv("ICDEV_SBOM_SIGNING_KEY_PATH", raising=False)
    monkeypatch.delenv("ICDEV_AUDIT_SIGNING_KEY_PATH", raising=False)

    result = verify_sbom(sbom_file, expected_fp=keys["public_key_fp"])
    assert result["verified"] is True
    assert result["trusted"] is True


def test_verification_also_works_against_a_locally_held_key(sbom_file, keypair):
    """The producer re-checking their own artifact ignores the embedded key."""
    sign_sbom(sbom_file, key_path=keypair["private_key"])
    sig_path = signature_path_for(sbom_file)
    block = json.loads(sig_path.read_text(encoding="utf-8"))
    block.pop("public_key_pem")
    sig_path.write_text(json.dumps(block), encoding="utf-8")

    assert verify_sbom(sbom_file)["verified"] is False, "no key material anywhere should fail"
    assert verify_sbom(sbom_file, key_path=keypair["private_key"])["verified"] is True


@pytest.mark.parametrize(
    "path",
    [
        Path("tools") / "compliance" / "sbom_signer.py",
        Path("icdev") / "tools" / "compliance" / "sbom_signer.py",
    ],
    ids=["root", "mirror"],
)
def test_the_signer_has_no_network_import(path):
    """Air gap as a structural property, not a promise in a docstring."""
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    banned = [
        "import socket",
        "import requests",
        "import httpx",
        "import urllib",
        "urllib.request",
        "http.client",
        "import subprocess",
    ]
    found = [token for token in banned if token in source]
    assert not found, f"{path.as_posix()} gained a network or subprocess path: {found}"


# ---------------------------------------------------------------------------
# generator wiring + persistence
# ---------------------------------------------------------------------------
def _seed_project(db_path, project_id, directory):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        (project_id, "Signature Fixture", "api", str(directory)),
    )
    conn.commit()
    conn.close()


def _read_record(db_path, project_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT author_signature, signature_algorithm, file_path FROM sbom_records "
        "WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.fixture
def python_project(tmp_path):
    project = tmp_path / "signed-project"
    project.mkdir()
    (project / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    return project


def test_the_signature_and_algorithm_are_persisted_to_sbom_records(
    icdev_db, python_project, tmp_path, monkeypatch
):
    """The card's persistence criterion, proved against a real INSERT.

    Goes through generate_sbom rather than writing the row directly: an INSERT
    naming a column the live table lacks raises at runtime, and this repo has
    been bitten by exactly that failing silently inside a bare except.
    """
    keys = generate_keypair(tmp_path / "keys", "ecdsa-p256")
    monkeypatch.setenv("ICDEV_SBOM_SIGNING_KEY_PATH", keys["private_key"])

    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "sig-persist", python_project)
    out_file = tmp_path / "persisted.cdx.json"
    sbom_generator.generate_sbom(
        project_id="sig-persist", output_path=str(out_file), db_path=icdev_db
    )

    record = _read_record(icdev_db, "sig-persist")
    assert record is not None, "generate_sbom wrote no sbom_records row"
    assert record["signature_algorithm"] == "ECDSA-P256-SHA256"
    assert record["author_signature"], "author_signature persisted empty"

    # The persisted value is the signature that actually verifies the artifact.
    block = json.loads(signature_path_for(out_file).read_text(encoding="utf-8"))
    assert record["author_signature"] == block["value"]
    assert verify_sbom(out_file, expected_fp=keys["public_key_fp"])["verified"] is True


def test_a_generated_sbom_is_signed_and_verifies_end_to_end(
    icdev_db, python_project, tmp_path, monkeypatch
):
    keys = generate_keypair(tmp_path / "keys", "ecdsa-p256")
    monkeypatch.setenv("ICDEV_SBOM_SIGNING_KEY_PATH", keys["private_key"])

    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "sig-e2e", python_project)
    out_file = tmp_path / "e2e.cdx.json"
    sbom_generator.generate_sbom(project_id="sig-e2e", output_path=str(out_file), db_path=icdev_db)

    assert verify_sbom(out_file)["verified"] is True

    # And the generated artifact is tamper-evident, not merely signed.
    _rewrite(out_file, lambda d: d["components"].append({"name": "backdoor", "version": "9"}))
    assert verify_sbom(out_file)["verified"] is False


def test_generation_without_a_key_stays_unsigned_rather_than_failing(
    icdev_db, python_project, tmp_path, capsys
):
    """~25 call sites and a blocking gate depend on generation not breaking.

    An install with no signing key must still get an SBOM — and must be told, on
    stdout, that it is unsigned. Silence here is how an unsigned artifact gets
    mistaken for a signed one.
    """
    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "sig-none", python_project)
    out_file = tmp_path / "unsigned.cdx.json"
    sbom_generator.generate_sbom(project_id="sig-none", output_path=str(out_file), db_path=icdev_db)

    assert out_file.is_file()
    assert "NOT SIGNED" in capsys.readouterr().out

    record = _read_record(icdev_db, "sig-none")
    assert record["author_signature"] is None
    assert record["signature_algorithm"] is None


def test_require_signature_turns_an_unsigned_sbom_into_a_failure(
    icdev_db, python_project, tmp_path, monkeypatch
):
    """The fail-closed toggle, for operators who require signed SBOMs."""
    monkeypatch.setenv("ICDEV_SBOM_REQUIRE_SIGNATURE", "1")

    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "sig-required", python_project)

    with pytest.raises(SbomSigningError) as excinfo:
        sbom_generator.generate_sbom(
            project_id="sig-required",
            output_path=str(tmp_path / "required.cdx.json"),
            db_path=icdev_db,
        )
    assert "ICDEV_SBOM_REQUIRE_SIGNATURE" in str(excinfo.value)

    assert _read_record(icdev_db, "sig-required") is None, "a failed run recorded a row"


def test_require_signature_also_rejects_a_key_that_yields_an_unapproved_algorithm(
    icdev_db, python_project, tmp_path, monkeypatch
):
    """Fail-closed must cover the wrong-key case, not only the no-key case."""
    key_path = _write_key(tmp_path / "k1.pem", ec.generate_private_key(ec.SECP256K1()))
    monkeypatch.setenv("ICDEV_SBOM_REQUIRE_SIGNATURE", "1")
    monkeypatch.setenv("ICDEV_SBOM_SIGNING_KEY_PATH", key_path)

    from tools.compliance import sbom_generator

    _seed_project(icdev_db, "sig-badcurve", python_project)

    with pytest.raises(SbomSigningError):
        sbom_generator.generate_sbom(
            project_id="sig-badcurve",
            output_path=str(tmp_path / "badcurve.cdx.json"),
            db_path=icdev_db,
        )


# ---------------------------------------------------------------------------
# module hygiene
# ---------------------------------------------------------------------------
def test_root_and_mirror_stay_in_sync():
    root = REPO_ROOT / "tools" / "compliance" / "sbom_signer.py"
    mirror = REPO_ROOT / "icdev" / "tools" / "compliance" / "sbom_signer.py"
    assert root.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8"), (
        "tools/compliance/sbom_signer.py and its icdev/ mirror have diverged -- "
        "author changes in both."
    )


def test_the_crypto_primitives_stay_mirrored():
    """sbx-sig-01 changed key_manager; the icdev/ copy must not be left behind."""
    for name in ("key_manager.py", "attestation_signer.py"):
        root = REPO_ROOT / "tools" / "crypto" / name
        mirror = REPO_ROOT / "icdev" / "tools" / "crypto" / name
        assert root.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8"), (
            f"tools/crypto/{name} and its icdev/ mirror have diverged."
        )


def test_signing_availability_ignores_the_hmac_secret(tmp_path, monkeypatch):
    """has_signing_key() counts the HMAC secret; signing_available() must not.

    Otherwise the generator would announce that it is about to sign, then refuse.
    """
    monkeypatch.setenv("ICDEV_AUDIT_HMAC_SECRET", "dev-secret")
    assert sbom_signer.signing_available() is False

    keys = generate_keypair(tmp_path / "keys", "ecdsa-p256")
    monkeypatch.setenv("ICDEV_SBOM_SIGNING_KEY_PATH", keys["private_key"])
    assert sbom_signer.signing_available() is True
