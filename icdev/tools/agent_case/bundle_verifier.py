#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — case-bundle verification that names WHICH records failed.

Three independent layers, verified from the bundle alone (no database):

1. **manifest** — every member listed in ``manifest.json`` is present and its
   SHA-256 matches; nothing unlisted was added to the bundle.
2. **hmac** — each ``hook_events`` record's ``signature`` recomputed exactly as
   ``.claude/hooks/send_event.py::compute_hmac`` does, using
   ``ICDEV_HOOK_HMAC_SECRET``.
3. **chain** — each ``audit_trail`` row's ``hash`` recomputed and each
   ``previous_hash`` matched against its predecessor (migration 149).

Every layer reports per-record findings — member path with expected vs actual
digest, event id, audit row id. A single pass/fail boolean is not an acceptable
output for a forensic tool: the value of the bundle is being able to say
"these 3 of 412 events do not verify, here they are".

**Honest degradation.** A layer that could not be checked reports
``NOT_VERIFIED`` — never ``PASSED`` and never ``FAILED``:

  * no ``ICDEV_HOOK_HMAC_SECRET`` in the environment (the verifier will NOT
    fall back to the shipped default the way the writer does);
  * the secret in the environment IS the shipped default
    ``icdev-default-hmac-key`` — a signature anyone can forge is not evidence;
  * every signature mismatches, which is what a rotated secret looks like and
    is not distinguishable from wholesale tampering;
  * the migration-149 hash columns are NULL (no ICDEV writer populates them
    today, so this is the common case on a real database);
  * an audit row's predecessor is outside the bundle slice and no
    ``chain_context`` anchor was supplied.

Exit codes:
    0  every layer PASSED and complete
    1  at least one layer FAILED (a record demonstrably does not verify)
    2  no layer failed, but at least one layer or record could NOT be verified
    3  the bundle could not be read at all

Usage:
    python tools/agent_case/bundle_verifier.py --bundle <dir> --json
    python tools/agent_case/bundle_verifier.py --bundle <dir> --layer hmac
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_case.bundle_format import (  # noqa: E402
    AUDIT_TRAIL_MEMBER,
    DEFAULT_HOOK_HMAC_SECRET,
    DIGEST_ALGORITHM,
    GENESIS_HASH,
    HMAC_SECRET_ENV,
    HOOK_EVENTS_MEMBER,
    MANIFEST_NAME,
    compute_audit_row_hash,
    compute_event_hmac,
    is_safe_member_path,
    iter_bundle_files,
    read_manifest,
    sha256_file,
)

PASSED = "PASSED"
FAILED = "FAILED"
NOT_VERIFIED = "NOT_VERIFIED"

LAYERS = ("manifest", "hmac", "chain")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 2
EXIT_UNREADABLE = 3

DEFAULT_SECRET_CAVEAT = (
    "hook_events signatures are HMAC-SHA256 keyed by ICDEV_HOOK_HMAC_SECRET. "
    ".claude/hooks/send_event.py falls back to the shipped default "
    f"'{DEFAULT_HOOK_HMAC_SECRET}' when no operator secret is set, and that value is "
    "public in the repository — a signature that verifies against it proves only that "
    "the payload matches a digest anyone could compute, NOT that it is authentic. "
    "This layer is cryptographically meaningful only where an operator set a real secret."
)

CHAIN_UNPOPULATED_CAVEAT = (
    "audit_trail.hash / previous_hash are added by migration 149 but no ICDEV writer "
    "populates them today, so an empty chain means 'never recorded', not 'erased'."
)


def _finding(layer, kind, severity, **fields):
    f = {"layer": layer, "kind": kind, "severity": severity}
    f.update(fields)
    return f


def _read_member_json(bundle_dir, member):
    """Parse a JSON member. Returns (data, error_string)."""
    if not is_safe_member_path(member):
        return None, "unsafe member path"
    path = Path(bundle_dir) / member
    if not path.is_file():
        return None, "member not present"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 — any parse failure is reportable
        return None, f"unreadable JSON: {exc}"


# ---------------------------------------------------------------------------
# Layer 1 — manifest
# ---------------------------------------------------------------------------

def verify_manifest_layer(bundle_dir) -> dict:
    """Check every manifest member is present with a matching SHA-256."""
    bundle_dir = Path(bundle_dir)
    findings = []
    layer = {
        "layer": "manifest",
        "status": PASSED,
        "complete": True,
        "checked": 0,
        "verified": 0,
        "failed": 0,
        "not_verified": 0,
        "findings": findings,
    }

    try:
        manifest = read_manifest(bundle_dir)
    except FileNotFoundError:
        layer["status"] = FAILED
        layer["failed"] = 1
        findings.append(_finding("manifest", "manifest_missing", "fail",
                                 member=MANIFEST_NAME,
                                 detail="bundle has no manifest.json — nothing is sealed"))
        return layer
    except Exception as exc:  # noqa: BLE001
        layer["status"] = FAILED
        layer["failed"] = 1
        findings.append(_finding("manifest", "manifest_unreadable", "fail",
                                 member=MANIFEST_NAME, detail=str(exc)))
        return layer

    algorithm = manifest.get("algorithm", DIGEST_ALGORITHM)
    if algorithm != DIGEST_ALGORITHM:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = f"unsupported digest algorithm '{algorithm}'"
        findings.append(_finding("manifest", "unsupported_algorithm", "not_verified",
                                 member=MANIFEST_NAME, expected=DIGEST_ALGORITHM,
                                 actual=algorithm))
        return layer

    members = manifest.get("members") or []
    listed = set()

    for entry in members:
        member_path = entry.get("path") if isinstance(entry, dict) else None
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        layer["checked"] += 1

        if not is_safe_member_path(member_path or ""):
            layer["failed"] += 1
            findings.append(_finding("manifest", "unsafe_member_path", "fail",
                                     member=member_path, expected=expected, actual=None,
                                     detail="member path escapes the bundle directory; not read"))
            continue

        listed.add(member_path)
        path = bundle_dir / member_path

        if not path.is_file():
            layer["failed"] += 1
            findings.append(_finding("manifest", "member_missing", "fail",
                                     member=member_path, expected=expected, actual=None,
                                     detail="manifest lists this member but it is not in the bundle"))
            continue

        if not expected:
            layer["not_verified"] += 1
            findings.append(_finding("manifest", "member_digest_absent", "not_verified",
                                     member=member_path, expected=None,
                                     actual=sha256_file(path),
                                     detail="manifest entry carries no sha256"))
            continue

        actual = sha256_file(path)
        if actual == expected:
            layer["verified"] += 1
        else:
            layer["failed"] += 1
            findings.append(_finding("manifest", "member_digest_mismatch", "fail",
                                     member=member_path, expected=expected, actual=actual,
                                     detail="file content does not match the sealed digest"))

    for rel, _path in iter_bundle_files(bundle_dir):
        if rel not in listed:
            layer["failed"] += 1
            findings.append(_finding("manifest", "unmanifested_file", "fail",
                                     member=rel, expected=None, actual=sha256_file(bundle_dir / rel),
                                     detail="file present in the bundle but absent from the manifest"))

    if layer["failed"]:
        layer["status"] = FAILED
    elif layer["checked"] == 0:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = "manifest lists no members"
    elif layer["not_verified"]:
        layer["complete"] = False

    return layer


# ---------------------------------------------------------------------------
# Layer 2 — per-event HMAC
# ---------------------------------------------------------------------------

def resolve_hmac_secret(secret: str = None, env: dict = None) -> dict:
    """Resolve the HMAC key and say honestly what it is worth.

    Returns ``{"secret": str|None, "source": str, "usable": bool, "reason": str|None}``.
    The verifier deliberately does NOT reproduce send_event.py's fallback to the
    shipped default: verifying with a public key is not verification.
    """
    env = os.environ if env is None else env
    if secret:
        return {"secret": secret, "source": "argument", "usable": True, "reason": None}

    from_env = env.get(HMAC_SECRET_ENV)
    if not from_env:
        return {
            "secret": None,
            "source": "unset",
            "usable": False,
            "reason": f"{HMAC_SECRET_ENV} is not set; refusing to fall back to the shipped default",
        }
    if from_env == DEFAULT_HOOK_HMAC_SECRET:
        return {
            "secret": from_env,
            "source": "environment",
            "usable": False,
            "reason": (f"{HMAC_SECRET_ENV} is the shipped default "
                       f"'{DEFAULT_HOOK_HMAC_SECRET}', which is public — signatures "
                       "computed with it carry no authenticity assurance"),
        }
    return {"secret": from_env, "source": "environment", "usable": True, "reason": None}


def verify_hmac_layer(bundle_dir, secret: str = None, env: dict = None) -> dict:
    """Recompute the hook_events HMAC for every event in the bundle."""
    findings = []
    layer = {
        "layer": "hmac",
        "status": PASSED,
        "complete": True,
        "checked": 0,
        "verified": 0,
        "failed": 0,
        "not_verified": 0,
        "findings": findings,
        "caveats": [DEFAULT_SECRET_CAVEAT],
    }

    key = resolve_hmac_secret(secret, env)
    layer["secret_source"] = key["source"]

    data, err = _read_member_json(bundle_dir, HOOK_EVENTS_MEMBER)
    if err:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = f"{HOOK_EVENTS_MEMBER}: {err}"
        findings.append(_finding("hmac", "hook_events_unavailable", "not_verified",
                                 member=HOOK_EVENTS_MEMBER, detail=err))
        return layer

    records = data.get("records") or []
    layer["checked"] = len(records)

    if not records:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = "bundle carries no hook_events records"
        return layer

    if not key["usable"]:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = key["reason"]
        layer["not_verified"] = len(records)
        for rec in records:
            findings.append(_finding("hmac", "hmac_not_verified", "not_verified",
                                     event_id=rec.get("id"),
                                     session_id=rec.get("session_id"),
                                     detail=key["reason"]))
        return layer

    mismatched = []
    for rec in records:
        event_id = rec.get("id")
        stored = rec.get("signature")
        if not stored:
            layer["not_verified"] += 1
            findings.append(_finding("hmac", "event_unsigned", "not_verified",
                                     event_id=event_id, session_id=rec.get("session_id"),
                                     detail="record carries no signature"))
            continue
        recomputed = compute_event_hmac(rec.get("payload"), key["secret"])
        if recomputed == stored:
            layer["verified"] += 1
        else:
            mismatched.append(
                _finding("hmac", "hmac_mismatch", "fail",
                         event_id=event_id, session_id=rec.get("session_id"),
                         hook_type=rec.get("hook_type"), tool_name=rec.get("tool_name"),
                         expected=recomputed, actual=stored,
                         detail="recomputed HMAC does not match the stored signature")
            )

    signed = layer["verified"] + len(mismatched)

    if mismatched and layer["verified"] == 0:
        # Indistinguishable from a rotated secret. Name the records, but do NOT
        # call it tampering — that is a forensic claim the evidence cannot support.
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["not_verified"] += len(mismatched)
        layer["reason"] = (
            f"all {len(mismatched)} signed events mismatch — consistent with a secret "
            "rotated since capture, so this layer is indeterminate rather than failed"
        )
        for f in mismatched:
            f["severity"] = "not_verified"
            f["kind"] = "hmac_mismatch_all"
            findings.append(f)
    elif mismatched:
        layer["status"] = FAILED
        layer["failed"] = len(mismatched)
        findings.extend(mismatched)
    elif signed == 0:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = "no event in the bundle carries a signature"
    elif layer["not_verified"]:
        layer["complete"] = False

    return layer


# ---------------------------------------------------------------------------
# Layer 3 — audit hash chain (migration 149)
# ---------------------------------------------------------------------------

def verify_chain_layer(bundle_dir) -> dict:
    """Recompute each audit_trail row hash and re-link the chain."""
    findings = []
    layer = {
        "layer": "chain",
        "status": PASSED,
        "complete": True,
        "checked": 0,
        "verified": 0,
        "failed": 0,
        "not_verified": 0,
        "findings": findings,
        "caveats": [],
    }

    data, err = _read_member_json(bundle_dir, AUDIT_TRAIL_MEMBER)
    if err:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = f"{AUDIT_TRAIL_MEMBER}: {err}"
        findings.append(_finding("chain", "audit_trail_unavailable", "not_verified",
                                 member=AUDIT_TRAIL_MEMBER, detail=err))
        return layer

    records = data.get("records") or []
    # Hashes of rows adjacent to the slice, so a partial export can still be linked.
    context = {str(k): v for k, v in (data.get("chain_context") or {}).items()}
    rows = sorted(records, key=lambda r: (r.get("id") is None, r.get("id")))
    layer["checked"] = len(rows)

    if not rows:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = "bundle carries no audit_trail records"
        return layer

    by_id = {}
    for row in rows:
        if row.get("id") is not None and row.get("hash"):
            by_id[str(row["id"])] = row["hash"]

    hashed_rows = 0
    for row in rows:
        row_id = row.get("id")
        stored_hash = row.get("hash")

        # --- row hash
        if not stored_hash:
            layer["not_verified"] += 1
            findings.append(_finding("chain", "row_hash_absent", "not_verified",
                                     row_id=row_id, event_type=row.get("event_type"),
                                     detail="audit_trail.hash is NULL for this row"))
        else:
            hashed_rows += 1
            recomputed = compute_audit_row_hash(row)
            if recomputed == stored_hash:
                layer["verified"] += 1
            else:
                layer["failed"] += 1
                findings.append(_finding("chain", "row_hash_mismatch", "fail",
                                         row_id=row_id, event_type=row.get("event_type"),
                                         actor=row.get("actor"), action=row.get("action"),
                                         expected=recomputed, actual=stored_hash,
                                         detail="row content does not hash to the stored hash"))

        # --- chain link
        stored_prev = row.get("previous_hash")
        expected_prev, link_source = _expected_previous_hash(row_id, by_id, context)

        if expected_prev is None:
            layer["not_verified"] += 1
            findings.append(_finding("chain", "chain_link_not_verifiable", "not_verified",
                                     row_id=row_id, detail=link_source))
        elif not stored_prev:
            layer["not_verified"] += 1
            findings.append(_finding("chain", "chain_link_absent", "not_verified",
                                     row_id=row_id, expected=expected_prev, actual=None,
                                     detail="audit_trail.previous_hash is NULL for this row"))
        elif stored_prev != expected_prev:
            layer["failed"] += 1
            findings.append(_finding("chain", "chain_link_broken", "fail",
                                     row_id=row_id, event_type=row.get("event_type"),
                                     expected=expected_prev, actual=stored_prev,
                                     link_source=link_source,
                                     detail="previous_hash does not match the predecessor row's hash"))

    if hashed_rows == 0:
        layer["caveats"].append(CHAIN_UNPOPULATED_CAVEAT)

    if layer["failed"]:
        layer["status"] = FAILED
    elif layer["verified"] == 0:
        layer["status"] = NOT_VERIFIED
        layer["complete"] = False
        layer["reason"] = ("no audit_trail row in the bundle carries a hash — the "
                           "migration-149 chain was never populated")
    elif layer["not_verified"]:
        layer["complete"] = False

    return layer


def _expected_previous_hash(row_id, by_id, context):
    """Return (expected previous hash | None, explanation of where it came from)."""
    if row_id is None:
        return None, "row has no id; its position in the chain is unknown"
    try:
        prev_id = int(row_id) - 1
    except (TypeError, ValueError):
        return None, f"row id {row_id!r} is not an integer; cannot locate a predecessor"

    if str(row_id) == "1":
        return GENESIS_HASH, "genesis row"
    if str(prev_id) in by_id:
        return by_id[str(prev_id)], f"row {prev_id} in this bundle"
    if str(prev_id) in context:
        return context[str(prev_id)], f"chain_context anchor for row {prev_id}"
    return None, (f"predecessor row {prev_id} is outside this bundle slice and no "
                  "chain_context anchor was supplied")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def verify_bundle(bundle_dir, secret: str = None, layers=None, env: dict = None) -> dict:
    """Run every layer and return the full per-record report."""
    bundle_dir = Path(bundle_dir)
    selected = tuple(layers) if layers else LAYERS

    report = {
        "bundle_dir": str(bundle_dir),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "classification": "CUI",
        "layers": {},
        "findings": [],
        "caveats": [],
    }

    if not bundle_dir.is_dir():
        report["status"] = FAILED
        report["exit_code"] = EXIT_UNREADABLE
        report["error"] = f"not a directory: {bundle_dir}"
        return report

    runners = {
        "manifest": lambda: verify_manifest_layer(bundle_dir),
        "hmac": lambda: verify_hmac_layer(bundle_dir, secret=secret, env=env),
        "chain": lambda: verify_chain_layer(bundle_dir),
    }
    for name in selected:
        layer = runners[name]()
        report["layers"][name] = layer
        report["findings"].extend(layer["findings"])
        for caveat in layer.get("caveats", []):
            if caveat not in report["caveats"]:
                report["caveats"].append(caveat)

    statuses = [layer["status"] for layer in report["layers"].values()]
    incomplete = any(not layer["complete"] for layer in report["layers"].values())

    if FAILED in statuses:
        report["status"] = FAILED
        report["exit_code"] = EXIT_FAILED
    elif NOT_VERIFIED in statuses or incomplete:
        report["status"] = "INCOMPLETE"
        report["exit_code"] = EXIT_INCOMPLETE
    else:
        report["status"] = PASSED
        report["exit_code"] = EXIT_OK

    report["summary"] = {
        name: {
            "status": layer["status"],
            "checked": layer["checked"],
            "verified": layer["verified"],
            "failed": layer["failed"],
            "not_verified": layer["not_verified"],
            "reason": layer.get("reason"),
        }
        for name, layer in report["layers"].items()
    }
    return report


def format_report(report: dict) -> str:
    """Human-readable report. Every failing record is named, never summarized away."""
    lines = ["CUI // SP-CTI", f"Case bundle: {report['bundle_dir']}"]
    if report.get("error"):
        lines.append(f"ERROR: {report['error']}")
        return "\n".join(lines)

    lines.append(f"Verified at: {report['verified_at']}")
    lines.append("")
    for name in LAYERS:
        layer = report["layers"].get(name)
        if not layer:
            continue
        # Counts are per CHECK, not per record: one record can clear the row-hash
        # check and still break its chain link, so these need not sum to `checked`.
        head = (f"[{layer['status']}] {name}: {layer['checked']} records checked, "
                f"{layer['verified']} verified")
        if layer["failed"]:
            head += f", {layer['failed']} FAILED"
        if layer["not_verified"]:
            head += f", {layer['not_verified']} not verified"
        lines.append(head)
        if layer.get("reason"):
            lines.append(f"    reason: {layer['reason']}")
        for f in layer["findings"]:
            target = (f.get("member") or (f"event {f.get('event_id')}" if "event_id" in f else None)
                      or (f"audit row {f.get('row_id')}" if "row_id" in f else "?"))
            lines.append(f"    - {f['kind']} [{f['severity']}] {target}")
            if f.get("detail"):
                lines.append(f"        {f['detail']}")
            if f.get("expected") is not None or f.get("actual") is not None:
                lines.append(f"        expected: {f.get('expected')}")
                lines.append(f"        actual:   {f.get('actual')}")
        lines.append("")

    if report.get("caveats"):
        lines.append("Caveats:")
        for c in report["caveats"]:
            lines.append(f"  * {c}")
        lines.append("")

    lines.append(f"OVERALL: {report['status']} (exit {report['exit_code']})")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify an AGOV CASE bundle and name which records failed which layer.")
    parser.add_argument("--bundle", required=True, help="Path to the case bundle directory")
    parser.add_argument("--layer", action="append", choices=LAYERS,
                        help="Verify only this layer (repeatable). Default: all three.")
    parser.add_argument("--secret", help=f"HMAC key; defaults to ${HMAC_SECRET_ENV}")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    report = verify_bundle(args.bundle, secret=args.secret, layers=args.layer)
    print(json.dumps(report, indent=2, default=str) if args.json else format_report(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
