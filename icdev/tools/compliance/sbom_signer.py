#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""SBOM Author Signature — the 2026 Minimum Elements element (sbx-sig-01).

The standard: "A digital signature attributable to the SBOM author, providing
assurance the claimed signatory signed the data and that it was not modified
after signing." The algorithm must be approved for secure use by a relevant
authority, and the guidance is to build on existing software signing
infrastructure and key management (NIST SP 800-57 Part 1 Rev 5) rather than
invent a scheme.

So this module invents nothing. It is a thin, opinionated wrapper over the two
primitives that already existed in the tree and had no caller on the SBOM path:
``tools/crypto/attestation_signer.py::sign_artifact`` and, beneath it,
``tools/crypto/key_manager.py::sign_payload``.

WHAT IS SIGNED

The canonicalized SBOM document — ``json.dumps(sorted keys, no whitespace)`` —
not the file's bytes. Re-indenting an SBOM does not change the bill of
materials, and a signature that broke on formatting would be a signature people
learn to ignore. The exact bytes are digested too (``file_sha256``) and a
mismatch is *reported* on verification, so byte-level edits are still visible;
they are just not, on their own, a verification failure.

Author attribution rides on the key fingerprint, not on a name field. When
sbx-fld-01 puts SBOM Author into the document itself, the signature will cover
it automatically, because the signature covers the whole document. Putting an
author name in the detached file today would have created a trusted-looking
field that nothing signs.

DETACHED, AND WHY

The signature is written to ``<sbom>.sig.json`` beside the SBOM, per the card.
Detached also dodges the obvious circularity of embedding a signature in the
document the signature covers, and leaves the CycloneDX output byte-identical
to what every existing consumer already parses.

FAIL CLOSED ON THE ALGORITHM

``key_manager.sign_payload`` degrades to HMAC-SHA256 and then to a
``"none"`` no-op when no PEM key is configured, because audit logging must never
break. Neither is acceptable here and both are refused:

  - HMAC-SHA256 is a symmetric MAC. Every party who can verify it can also
    forge it, so it cannot be "attributable to the SBOM author" — the property
    the element exists to provide. It is not a digital signature.
  - ``"none"`` is not a signature at all.

Writing either one under the name "SBOM Author Signature" would produce an
artifact that passes a presence check and means nothing, so ``sign_sbom``
raises ``SbomSigningError`` instead of emitting it.

AIR GAP

No network, on either path, by construction. There is no sigstore, no Fulcio,
no Rekor, no transparency log and no OCSP: signing reads a local PEM private
key and verification reads the public key embedded in the detached file (or one
supplied locally). This is deliberate — ICDEV already emits
``cosign attest --type cyclonedx`` steps in CI YAML it generates for *other*
projects, and cosign's keyless mode cannot run in a disconnected enclave.

INTEGRITY IS NOT AUTHENTICITY

Verification with only the SBOM and its detached signature proves the document
was not modified after signing. It does NOT prove who signed it: an attacker
who rewrites the SBOM can re-sign it with their own key and overwrite the
embedded public key. Pin the expected fingerprint (``expected_fp`` /
``--expect-fp``, distributed out of band) to get authorship. ``verify_sbom``
reports the two facts separately — ``verified`` and ``trusted`` — rather than
collapsing them into one boolean that overstates what was checked.

Usage:
    from tools.compliance.sbom_signer import sign_sbom, verify_sbom

    block = sign_sbom("compliance/sbom_proj_20260808.cdx.json")
    result = verify_sbom("compliance/sbom_proj_20260808.cdx.json")
    assert result["verified"]

CLI:
    python tools/compliance/sbom_signer.py --sign <sbom.json> [--key-path P] --json
    python tools/compliance/sbom_signer.py --verify <sbom.json> [--expect-fp FP] --json
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.crypto.attestation_signer import sign_artifact, verify_artifact
from tools.crypto.key_manager import ENV_KEY_PATH, export_public_key_pem

#: Preferred signing key for SBOMs specifically. Falls back to the audit signing
#: key so an install that already configured one does not need a second.
ENV_SBOM_KEY_PATH = "ICDEV_SBOM_SIGNING_KEY_PATH"

#: When truthy, SBOM generation FAILS rather than emitting an unsigned SBOM.
#: Default off: sbom_generator has ~25 call sites and most installs have no key
#: configured yet, so defaulting this on would turn a missing key into a broken
#: compliance pipeline. Operators who require signed SBOMs turn it on.
ENV_REQUIRE_SIGNATURE = "ICDEV_SBOM_REQUIRE_SIGNATURE"

#: Suffix of the detached signature written beside the SBOM.
SIGNATURE_SUFFIX = ".sig.json"

#: Schema tag of the detached signature file, so a future format change is
#: detectable rather than silently mis-parsed.
SIGNATURE_SCHEMA = "icdev-sbom-signature/1"

#: Algorithms accepted as an SBOM Author Signature, each with the authority that
#: approves it. The 2026 Minimum Elements name NIST DSS, ISO/IEC 14888-4:2024
#: and the ENISA Agreed Cryptographic Mechanisms as the relevant authorities.
#:
#: This list is the intersection of "approved by an authority the standard
#: names" and "producible by tools/crypto/key_manager" — listing an algorithm
#: this tree cannot emit would be a claim, not a capability. RSA-PSS is approved
#: by FIPS 186-5 and is deliberately absent for exactly that reason:
#: key_manager.sign_payload has no RSA branch.
#:
#: The curve is part of the label on purpose. key_manager derives the label from
#: the key's actual curve (_ecdsa_algorithm), so a secp256k1 key — approved by
#: no authority for this use — reports "ECDSA-secp256k1-SHA256" and is rejected
#: here instead of passing under a P-256 name.
APPROVED_ALGORITHMS = {
    "ECDSA-P256-SHA256": "FIPS 186-5 (NIST DSS) ECDSA over P-256; ENISA Agreed Cryptographic Mechanisms",
    "ECDSA-P384-SHA256": "FIPS 186-5 (NIST DSS) ECDSA over P-384; ENISA Agreed Cryptographic Mechanisms",
    "ECDSA-P521-SHA256": "FIPS 186-5 (NIST DSS) ECDSA over P-521; ENISA Agreed Cryptographic Mechanisms",
    "Ed25519": "FIPS 186-5 (NIST DSS) EdDSA over Curve25519; ENISA Agreed Cryptographic Mechanisms",
}

#: Why a specific algorithm key_manager can produce is refused. Kept separate
#: from "unrecognised" so the error message can say what is actually wrong.
REJECTED_ALGORITHMS = {
    "HMAC-SHA256": (
        "HMAC-SHA256 is a symmetric MAC, not a digital signature: any party able to "
        "verify it can also forge it, so it cannot be attributable to the SBOM author. "
        "Configure an asymmetric signing key "
        f"({ENV_SBOM_KEY_PATH} or {ENV_KEY_PATH})."
    ),
    "none": (
        "No signing key is configured, so key_manager produced an empty signature. "
        f"Set {ENV_SBOM_KEY_PATH} (or {ENV_KEY_PATH}) to a PEM private key; generate one "
        "with: python tools/crypto/key_manager.py --generate-keys --key-type ecdsa-p256"
    ),
}


class SbomSigningError(Exception):
    """Signing could not produce a conformant SBOM Author Signature."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def resolve_signing_key(key_path: Optional[str] = None) -> Optional[str]:
    """Return the private key path to sign with, or None if none is configured."""
    return key_path or os.environ.get(ENV_SBOM_KEY_PATH) or os.environ.get(ENV_KEY_PATH) or None


def signing_available(key_path: Optional[str] = None) -> bool:
    """True if an asymmetric signing key is configured and readable.

    Deliberately ignores the HMAC secret: key_manager.has_signing_key() counts
    it, and it cannot produce an SBOM Author Signature.
    """
    path = resolve_signing_key(key_path)
    return bool(path) and Path(path).is_file()


def signature_required() -> bool:
    """True if an unsigned SBOM should be treated as a generation failure."""
    return os.environ.get(ENV_REQUIRE_SIGNATURE, "").strip().lower() in ("1", "true", "yes", "on")


def signature_path_for(sbom_path) -> Path:
    """Return the detached signature path for an SBOM file."""
    return Path(str(sbom_path) + SIGNATURE_SUFFIX)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_sbom(sbom_path: Path) -> dict:
    try:
        return json.loads(sbom_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SbomSigningError(f"SBOM not found: {sbom_path}")
    except json.JSONDecodeError as e:
        raise SbomSigningError(f"SBOM is not valid JSON ({sbom_path}): {e}")


# ---------------------------------------------------------------------------
# Sign
# ---------------------------------------------------------------------------
def sign_sbom(sbom_path, key_path: Optional[str] = None, signature_path=None) -> dict:
    """Sign an SBOM and write the detached signature beside it.

    Args:
        sbom_path: path to the SBOM JSON document
        key_path: PEM private key override; otherwise resolved from the environment
        signature_path: detached signature path override

    Returns:
        The signature block that was written.

    Raises:
        SbomSigningError: no key configured, key unusable, or the algorithm
            key_manager produced is not approved for an SBOM Author Signature.
    """
    sbom_file = Path(sbom_path)
    sbom = _load_sbom(sbom_file)

    resolved_key = resolve_signing_key(key_path)
    if not resolved_key:
        raise SbomSigningError(REJECTED_ALGORITHMS["none"])
    if not Path(resolved_key).is_file():
        raise SbomSigningError(
            f"Signing key not found: {resolved_key} "
            f"(from {ENV_SBOM_KEY_PATH}/{ENV_KEY_PATH} or --key-path)"
        )

    block = sign_artifact(sbom, key_path=resolved_key)
    algorithm = block.get("algorithm", "none")

    if algorithm in REJECTED_ALGORITHMS:
        raise SbomSigningError(REJECTED_ALGORITHMS[algorithm])
    if algorithm not in APPROVED_ALGORITHMS:
        raise SbomSigningError(
            f"Algorithm '{algorithm}' is not approved for an SBOM Author Signature. "
            f"The 2026 Minimum Elements require an algorithm approved by a relevant "
            f"authority (NIST DSS, ISO/IEC 14888-4:2024, ENISA). Approved here: "
            f"{', '.join(sorted(APPROVED_ALGORITHMS))}."
        )
    if not block.get("value"):
        raise SbomSigningError(f"Signing with {resolved_key} produced an empty signature.")

    public_key_pem = export_public_key_pem(resolved_key)
    if not public_key_pem:
        raise SbomSigningError(
            f"Could not export the public key from {resolved_key}; a detached signature "
            "without verification material cannot be verified offline."
        )

    out_path = Path(signature_path) if signature_path else signature_path_for(sbom_file)
    signature = {
        "schema": SIGNATURE_SCHEMA,
        "element": "SBOM Author Signature",
        "standard": "2026 Minimum Elements for a Software Bill of Materials (CISA, 2026-07-29)",
        "sbom_file": sbom_file.name,
        "algorithm": algorithm,
        "algorithm_authority": APPROVED_ALGORITHMS[algorithm],
        "value": block["value"],
        "public_key_fp": block.get("public_key_fp", ""),
        "public_key_pem": public_key_pem,
        "artifact_hash": block.get("artifact_hash", ""),
        "file_sha256": _file_sha256(sbom_file),
        "signed_at": block.get("signed_at", ""),
        "signed_over": (
            "Canonicalized SBOM JSON (sorted keys, no whitespace). Fields of this "
            "signature file itself are NOT covered by the signature."
        ),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(signature, indent=2) + "\n", encoding="utf-8", newline="")

    result = dict(signature)
    result["signature_path"] = str(out_path)
    return result


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
def _failure(sbom_file, sig_path, reason, **extra) -> dict:
    result = {
        "verified": False,
        "trusted": False,
        "reason": reason,
        "sbom_path": str(sbom_file),
        "signature_path": str(sig_path) if sig_path else "",
        "algorithm": "",
        "algorithm_approved": False,
        "public_key_fp": "",
        "bytes_modified": None,
    }
    result.update(extra)
    return result


def verify_sbom(
    sbom_path,
    signature_path=None,
    expected_fp: Optional[str] = None,
    key_path: Optional[str] = None,
) -> dict:
    """Verify an SBOM against its detached signature. Never raises on a bad input.

    Args:
        sbom_path: the SBOM JSON document
        signature_path: detached signature override; defaults to ``<sbom>.sig.json``
        expected_fp: public key fingerprint expected out of band. Supplying it is
            what turns integrity into authenticity — see the module docstring.
        key_path: verify against a local PRIVATE key's public half instead of the
            public key embedded in the signature file.

    Returns:
        ``verified``   — the SBOM content is intact and signed by the embedded key.
        ``trusted``    — ``verified`` AND the key matched ``expected_fp``. Without
                         ``expected_fp`` this is None: unknown, not True.
        ``bytes_modified`` — the file's bytes changed but its content did not
                         (e.g. re-indented). Informational; not a failure.
    """
    sbom_file = Path(sbom_path)
    sig_path = Path(signature_path) if signature_path else signature_path_for(sbom_file)

    if not sbom_file.is_file():
        return _failure(sbom_file, sig_path, f"SBOM not found: {sbom_file}")
    if not sig_path.is_file():
        return _failure(
            sbom_file, sig_path, f"No detached signature found at {sig_path}. The SBOM is unsigned."
        )

    try:
        signature = json.loads(sig_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _failure(sbom_file, sig_path, f"Signature file is not valid JSON: {e}")
    if not isinstance(signature, dict):
        return _failure(sbom_file, sig_path, "Signature file is not a JSON object.")

    try:
        sbom = _load_sbom(sbom_file)
    except SbomSigningError as e:
        return _failure(sbom_file, sig_path, str(e))

    algorithm = signature.get("algorithm", "")
    fp = signature.get("public_key_fp", "")

    # Refuse an unapproved algorithm BEFORE checking the maths. A valid
    # HMAC-SHA256 tag over this SBOM still is not an SBOM Author Signature, and
    # reporting it as verified would be the failure this element exists to
    # prevent.
    if algorithm not in APPROVED_ALGORITHMS:
        reason = REJECTED_ALGORITHMS.get(
            algorithm,
            f"Algorithm '{algorithm}' is not approved for an SBOM Author Signature. "
            f"Approved: {', '.join(sorted(APPROVED_ALGORITHMS))}.",
        )
        return _failure(sbom_file, sig_path, reason, algorithm=algorithm, public_key_fp=fp)

    # Authorship, when the caller told us whose key to expect.
    if expected_fp and fp != expected_fp:
        return _failure(
            sbom_file,
            sig_path,
            f"Public key fingerprint mismatch: signature carries {fp or '(none)'}, "
            f"expected {expected_fp}. The SBOM was signed by a different key.",
            algorithm=algorithm,
            algorithm_approved=True,
            public_key_fp=fp,
        )

    public_key_pem = None if key_path else signature.get("public_key_pem")
    if not key_path and not public_key_pem:
        return _failure(
            sbom_file,
            sig_path,
            "Signature carries no public_key_pem and no --key-path was supplied; "
            "nothing to verify against.",
            algorithm=algorithm,
            algorithm_approved=True,
            public_key_fp=fp,
        )

    ok = verify_artifact(sbom, signature, key_path=key_path, public_key_pem=public_key_pem)
    if not ok:
        return _failure(
            sbom_file,
            sig_path,
            "Signature does not verify. The SBOM was modified after signing, or the "
            "signature does not belong to this SBOM.",
            algorithm=algorithm,
            algorithm_approved=True,
            public_key_fp=fp,
        )

    recorded_digest = signature.get("file_sha256", "")
    bytes_modified = bool(recorded_digest) and recorded_digest != _file_sha256(sbom_file)

    return {
        "verified": True,
        "trusted": True if expected_fp else None,
        "reason": "",
        "sbom_path": str(sbom_file),
        "signature_path": str(sig_path),
        "algorithm": algorithm,
        "algorithm_approved": True,
        "algorithm_authority": APPROVED_ALGORITHMS[algorithm],
        "public_key_fp": fp,
        "signed_at": signature.get("signed_at", ""),
        "bytes_modified": bytes_modified,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Sign and verify an SBOM Author Signature (2026 Minimum Elements)"
    )
    parser.add_argument("--sign", metavar="SBOM", help="SBOM JSON file to sign")
    parser.add_argument("--verify", metavar="SBOM", help="SBOM JSON file to verify")
    parser.add_argument("--signature", help=f"Detached signature path (default: <sbom>{SIGNATURE_SUFFIX})")
    parser.add_argument("--key-path", help="PEM private key path override")
    parser.add_argument(
        "--expect-fp",
        help="Public key fingerprint expected out of band. Without it, verification "
        "proves integrity but NOT authorship.",
    )
    parser.add_argument(
        "--list-algorithms", action="store_true", help="List approved signature algorithms"
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.list_algorithms:
        if args.json_output:
            print(json.dumps({"approved": APPROVED_ALGORITHMS}, indent=2))
        else:
            for algo, authority in sorted(APPROVED_ALGORITHMS.items()):
                print(f"  {algo:22} {authority}")
        return 0

    if args.sign:
        try:
            block = sign_sbom(args.sign, key_path=args.key_path, signature_path=args.signature)
        except SbomSigningError as e:
            if args.json_output:
                print(json.dumps({"signed": False, "error": str(e)}, indent=2))
            else:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        if args.json_output:
            print(json.dumps({"signed": True, **block}, indent=2))
        else:
            print(f"Signed:      {block['sbom_file']}")
            print(f"  Algorithm: {block['algorithm']}")
            print(f"  Key fp:    {block['public_key_fp']}")
            print(f"  Signature: {block['signature_path']}")
        return 0

    if args.verify:
        result = verify_sbom(
            args.verify,
            signature_path=args.signature,
            expected_fp=args.expect_fp,
            key_path=args.key_path,
        )
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Verified:  {result['verified']}")
            if result["verified"]:
                print(f"  Algorithm: {result['algorithm']}")
                print(f"  Key fp:    {result['public_key_fp']}")
                if result["trusted"] is None:
                    print(
                        "  NOTE: integrity only. Pass --expect-fp with an out-of-band "
                        "fingerprint to establish authorship."
                    )
                if result["bytes_modified"]:
                    print(
                        "  NOTE: the file's bytes changed since signing but its content "
                        "did not (reformatted)."
                    )
            else:
                print(f"  {result['reason']}")
        return 0 if result["verified"] else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
