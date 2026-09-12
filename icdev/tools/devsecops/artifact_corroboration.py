#!/usr/bin/env python3
# CUI // SP-CTI
"""Does the ARTIFACT corroborate what was attested about it? (xrv-bin-03)

WHY. ``attestation_manager.verify_attestation`` and
``slsa_attestation_generator.verify_slsa_level`` both verify METADATA -- a
signature over a digest, a set of build-evidence predicates -- and neither has
ever opened the bytes that digest covers. That is the classic rebuttal to
attestation-only provenance: *the binary does not match the SBOM*, and a
perfectly valid signature over a perfectly stale statement answers it not at
all. ``binary_triage`` (xrv-bin-01) can now open the artifact and
``binary_components`` (this card) can say what it appears to contain, so the
question is finally askable.

IT IS REPORTED BESIDE THE SIGNATURE VERDICT AND NEVER FOLDED INTO IT. This is
the whole of ``attach``: it returns a NEW mapping carrying every key of the
verifier's own result unchanged and exactly one added key, and it REFUSES
rather than overwrite. A corroboration is EVIDENCE ABOUT THE SUBJECT; a
signature verdict is a statement about a KEY and a DOCUMENT. Letting the first
move the second would mean a triage that cannot load ``pefile`` silently
downgrades a cryptographically sound verification -- and would mean an
adversary who can make the triage unmeasurable can make the verification say
whatever they like.

TWO AXES, NEVER MERGED, because they fail for unrelated reasons and send a
reader to different fixes:

    digest    the artifact's own SHA-256 against the attestation SUBJECT's.
              This is the strong one: a mismatch means the statement is about a
              DIFFERENT FILE.
    sbom      what the triage OBSERVED in the artifact against what the SBOM
              DECLARES. This is the weak one, and its asymmetry is stated
              below.

THREE VERDICTS PER AXIS AND ONE OVERALL: ``agrees`` | ``disagrees`` |
``unmeasurable``. ``unmeasurable`` is its own verdict and is NEVER folded into
``agrees``: an absent attestation subject, an unreadable artifact and an SBOM
nobody generated all read "I could not ask", which is not "the answers match".
The overall verdict is ``disagrees`` if ANY axis disagrees, ``agrees`` if at
least one axis agrees and none disagrees, and ``unmeasurable`` if neither axis
could be measured -- so one measurable axis is never hidden behind the other's
silence.

THE SBOM AXIS IS DELIBERATELY ASYMMETRIC, AND THAT IS NOT TIMIDITY.

  * An SBOM component the binary does NOT mention is not a disagreement. A
    statically linked library leaves no import and need leave no string; a
    stripped build leaves neither.
  * An observation the SBOM does NOT carry is not a disagreement either. A
    version string can name a protocol, a file format, a requirement of
    somebody else's code, or a user-agent template. ``binary_components``
    labels every one of them ``confidence: unasserted`` for exactly this
    reason, and promoting an unasserted hint to a finding would make the first
    real run a wall of false positives.
  * A NAME BOTH SIDES CARRY AT TWO DIFFERENT VERSIONS **is** a disagreement,
    and it is the one the rebuttal is actually about: the SBOM says
    ``openssl 3.0.2`` and the artifact says ``OpenSSL 1.1.1k``.

So the only route to ``disagrees`` on this axis is a version contradiction on a
shared name. Everything else is carried as CONTEXT -- ``observed_not_declared``
and ``declared_not_observed`` are on the result, counted and named, so a reader
sees the whole comparison rather than only its verdict.

NOTHING HERE VERIFIES A SIGNATURE and nothing here reads a key. There is no
``cosign`` invocation, no ``subprocess``, and no import of a crypto module -- an
AST test pins that. This module answers one question and the signature door
answers the other.

Usage:
    python -m tools.devsecops.artifact_corroboration --artifact app.exe \\
        --attestation provenance.json --sbom sbom.json --json
    python -m tools.devsecops.artifact_corroboration --artifact app.exe --sbom sbom.json

    >>> from icdev.tools.devsecops.artifact_corroboration import corroborate
    >>> block = corroborate("app.exe", attestation="provenance.json")
    >>> block["corroboration"]
    'disagrees'

Exit codes: 0 a report was produced, whatever it says -- including
``disagrees``, because this module REPORTS and never gates; 2 no report could
be produced at all.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- never
# the import root. Bootstrap it before the first first-party import below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.analyzers.binary_triage import triage  # noqa: E402
from tools.compliance.binary_components import hinted_components  # noqa: E402

__all__ = [
    "CORROBORATIONS",
    "RESULT_KEY",
    "attach",
    "corroborate",
]

# ---------------------------------------------------------------------------
# Closed vocabulary
# ---------------------------------------------------------------------------

AGREES = "agrees"
DISAGREES = "disagrees"
UNMEASURABLE = "unmeasurable"
#: The closed set the card names. A fourth value is a change to this tuple and
#: to the test that reads it, never a string invented at a call site.
CORROBORATIONS: Tuple[str, ...] = (AGREES, DISAGREES, UNMEASURABLE)

#: The ONE key `attach` adds to a verifier's result. Named here so a consumer
#: reads it from the vocabulary rather than from a literal in three modules.
RESULT_KEY = "corroboration"

# Reasons an axis could not be measured. Each names the thing that was missing,
# because "unmeasurable" without a reason is an alarm nobody can action.
REASON_NO_ARTIFACT = "no_artifact_supplied"
REASON_ARTIFACT_UNREADABLE = "artifact_unreadable"
REASON_ARTIFACT_DIGEST_IS_PREFIX = "artifact_digest_covers_a_prefix_only"
REASON_NO_ATTESTATION = "no_attestation_supplied"
REASON_ATTESTATION_UNREADABLE = "attestation_unreadable"
REASON_NO_SUBJECT_DIGEST = "attestation_carries_no_sha256_subject"
REASON_NO_SBOM = "no_sbom_supplied"
REASON_SBOM_UNREADABLE = "sbom_unreadable"
REASON_SBOM_EMPTY = "sbom_declares_no_components"
REASON_NO_OBSERVATIONS = "triage_observed_nothing_comparable"
REASON_NO_SHARED_NAME = "no_name_carried_by_both_sides"

#: Version comparison outcomes on a shared name.
VERSION_MATCH = "match"
VERSION_COMPATIBLE = "compatible"
VERSION_CONTRADICTION = "contradiction"

#: A `lib` prefix and a `.so.1.1` / `.dll` / `.dylib` suffix are packaging, not
#: identity: an SBOM says `openssl`, a PE import says `LIBSSL-1_1-x64.dll`.
#: Stripping them is what makes the two comparable at all, and it is the only
#: normalisation applied -- nothing here maps one project's name onto another's.
_LIB_SUFFIX = re.compile(r"\.(so|dll|dylib|a|lib)(\.[0-9][0-9A-Za-z._-]*)?$", re.IGNORECASE)
_TRAILING_VERSION = re.compile(r"[-_]?[0-9][0-9A-Za-z._]*$")
_NON_IDENT = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_json(source: Any) -> Tuple[Optional[Any], Optional[str]]:
    """``(document, error)``. A dict or list passed straight through.

    A caller that already holds the parsed statement should not have to write it
    back out to disk for this module to read it.
    """
    if source is None:
        return None, None
    if isinstance(source, (dict, list)):
        return source, None
    try:
        text = Path(source).read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _subject_digests(statement: Any) -> List[Dict[str, str]]:
    """SHA-256 subject digests from an in-toto v1 statement.

    Only ``sha256`` is read. A subject carrying only a ``sha512`` is NOT a
    mismatch -- the triage computes SHA-256 and nothing else, so the honest
    answer there is that the two sides do not share an algorithm, which lands as
    ``no_sha256_subject`` rather than as a failed comparison.
    """
    subjects = []
    if isinstance(statement, dict):
        raw = statement.get("subject")
        # A caller may hand the whole `generate_slsa_provenance` result, whose
        # statement lives under `provenance`. Both shapes, no guessing: the key
        # is checked for, never inferred from the absence of `subject`.
        if raw is None and isinstance(statement.get("provenance"), dict):
            raw = statement["provenance"].get("subject")
    else:
        raw = statement
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        digest = entry.get("digest") or {}
        value = digest.get("sha256") if isinstance(digest, dict) else None
        if isinstance(value, str) and value.strip():
            subjects.append({"name": entry.get("name"), "sha256": value.strip().lower()})
    return subjects


def _sbom_components(document: Any) -> List[Dict[str, Optional[str]]]:
    """``[{name, version}]`` from a CycloneDX or an SPDX document, or a list.

    Both serializations are read because ``sbom_generator`` emits both from the
    same build -- a corroboration that worked only against CycloneDX would go
    unmeasurable on exactly the document a recipient was given.
    """
    entries: List[Dict[str, Optional[str]]] = []
    if isinstance(document, list):
        raw = document
    elif isinstance(document, dict) and isinstance(document.get("components"), list):
        raw = document["components"]
    elif isinstance(document, dict) and isinstance(document.get("packages"), list):
        raw = [
            {"name": pkg.get("name"), "version": pkg.get("versionInfo")}
            for pkg in document["packages"]
            if isinstance(pkg, dict)
        ]
    else:
        return entries
    for comp in raw:
        if not isinstance(comp, dict):
            continue
        name = comp.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        version = comp.get("version")
        entries.append(
            {
                "name": name.strip(),
                "version": version.strip() if isinstance(version, str) and version.strip() else None,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _normalise_name(name: str) -> str:
    """The comparable form of a component/import/hint name.

    ``LIBSSL-1_1-x64.dll`` and ``libssl.so.1.1`` and ``OpenSSL`` do not
    normalise to one another and are not meant to: this strips PACKAGING, not
    identity. A mapping table that turned ``libssl`` into ``openssl`` would be a
    claim about the world maintained in a dict, and a stale entry there produces
    a confident wrong verdict.
    """
    text = _LIB_SUFFIX.sub("", name.strip().lower())
    text = _TRAILING_VERSION.sub("", text)
    if text.startswith("lib") and len(text) > 5:
        text = text[3:]
    return _NON_IDENT.sub("", text)


def _compare_versions(observed: Optional[str], declared: Optional[str]) -> Optional[str]:
    """``match`` | ``compatible`` | ``contradiction``, or None when unaskable.

    None -- not ``contradiction`` -- when either side has no version: an SBOM
    component whose version is ``unknown`` cannot contradict anything, and
    reading it as a mismatch would turn the generator's own honest unknown
    marker into a finding.
    """
    if not observed or not declared:
        return None
    left = observed.strip().lower().lstrip("v")
    right = declared.strip().lower().lstrip("v")
    if not left or not right or right == "unknown":
        return None
    if left == right:
        return VERSION_MATCH
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    # A prefix only counts at a component boundary: `1.1` is compatible with
    # `1.1.1k` and is NOT compatible with `1.14`, which shares four characters
    # and is a different release.
    if longer.startswith(shorter) and not longer[len(shorter)].isalnum():
        return VERSION_COMPATIBLE
    return VERSION_CONTRADICTION


def _observations(report: Dict[str, Any], hints: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What the artifact appears to contain: version hints, then imports.

    An import carries NO version -- a DLL name or an ELF dynamic symbol says
    what is linked and never which release -- so it can corroborate a NAME and
    can never contradict a VERSION. That is recorded on each observation as
    ``kind``, so the verdict rule does not have to re-derive it.
    """
    observations = []
    for comp in hints.get("components") or []:
        observations.append(
            {
                "kind": "version_hint",
                "name": comp["declared_name"],
                "version": comp["version"],
                "evidence_string": comp["binary_hint"]["evidence_string"],
            }
        )
    for symbol in report.get("imports") or []:
        observations.append({"kind": "import", "name": symbol, "version": None})
    return observations


# ---------------------------------------------------------------------------
# The two axes
# ---------------------------------------------------------------------------


def _digest_axis(
    report: Optional[Dict[str, Any]],
    artifact_error: Optional[str],
    statement: Any,
    statement_error: Optional[str],
    attestation_supplied: bool,
) -> Dict[str, Any]:
    axis: Dict[str, Any] = {
        "verdict": UNMEASURABLE,
        "reason": None,
        "artifact_sha256": None,
        "artifact_sha256_scope": None,
        "subject_digests": None,
        "matched_subject": None,
    }
    if report is None:
        axis["reason"] = artifact_error or REASON_NO_ARTIFACT
        return axis
    axis["artifact_sha256"] = report.get("sha256")
    axis["artifact_sha256_scope"] = report.get("sha256_scope")
    if not axis["artifact_sha256"]:
        axis["reason"] = REASON_ARTIFACT_UNREADABLE
        return axis
    if axis["artifact_sha256_scope"] != "whole_file":
        # A digest over a PREFIX cannot match a digest over the file, and
        # reporting that as `disagrees` would accuse a correct attestation of
        # describing a different artifact because of OUR byte cap.
        axis["reason"] = REASON_ARTIFACT_DIGEST_IS_PREFIX
        return axis
    if not attestation_supplied:
        axis["reason"] = REASON_NO_ATTESTATION
        return axis
    if statement_error is not None:
        axis["reason"] = "%s: %s" % (REASON_ATTESTATION_UNREADABLE, statement_error)
        return axis
    subjects = _subject_digests(statement)
    axis["subject_digests"] = subjects
    if not subjects:
        axis["reason"] = REASON_NO_SUBJECT_DIGEST
        return axis
    for subject in subjects:
        if subject["sha256"] == axis["artifact_sha256"]:
            axis["verdict"] = AGREES
            axis["matched_subject"] = subject
            return axis
    axis["verdict"] = DISAGREES
    axis["reason"] = "no attestation subject digest matches the artifact"
    return axis


def _sbom_axis(
    observations: Optional[List[Dict[str, Any]]],
    declared: Optional[List[Dict[str, Optional[str]]]],
    artifact_error: Optional[str],
    sbom_error: Optional[str],
    sbom_supplied: bool,
) -> Dict[str, Any]:
    axis: Dict[str, Any] = {
        "verdict": UNMEASURABLE,
        "reason": None,
        "observed_count": None if observations is None else len(observations),
        "declared_count": None if declared is None else len(declared),
        "contradictions": [],
        "corroborated": [],
        "observed_not_declared": None,
        "declared_not_observed": None,
    }
    if observations is None:
        axis["reason"] = artifact_error or REASON_NO_ARTIFACT
        return axis
    if not sbom_supplied:
        axis["reason"] = REASON_NO_SBOM
        return axis
    if sbom_error is not None:
        axis["reason"] = "%s: %s" % (REASON_SBOM_UNREADABLE, sbom_error)
        return axis
    if not declared:
        axis["reason"] = REASON_SBOM_EMPTY
        return axis
    if not observations:
        axis["reason"] = REASON_NO_OBSERVATIONS
        return axis

    declared_index: Dict[str, List[Dict[str, Optional[str]]]] = {}
    for entry in declared:
        key = _normalise_name(entry["name"])
        if key:
            declared_index.setdefault(key, []).append(entry)

    matched_keys = set()
    for observation in observations:
        key = _normalise_name(observation["name"])
        if not key or key not in declared_index:
            continue
        matched_keys.add(key)
        for entry in declared_index[key]:
            outcome = _compare_versions(observation.get("version"), entry.get("version"))
            record = {
                "observed_name": observation["name"],
                "observed_version": observation.get("version"),
                "observed_kind": observation["kind"],
                "declared_name": entry["name"],
                "declared_version": entry.get("version"),
                "version_outcome": outcome,
            }
            if outcome == VERSION_CONTRADICTION:
                axis["contradictions"].append(record)
            else:
                axis["corroborated"].append(record)

    # CONTEXT, never a verdict. A library can be present without leaving a
    # string, and a string can name something that is not a component -- the
    # module docstring says why neither direction is a finding.
    axis["observed_not_declared"] = sorted(
        {
            observation["name"]
            for observation in observations
            if _normalise_name(observation["name"]) not in declared_index
        }
    )
    axis["declared_not_observed"] = sorted(
        {entry["name"] for entry in declared if _normalise_name(entry["name"]) not in matched_keys}
    )

    if axis["contradictions"]:
        axis["verdict"] = DISAGREES
        axis["reason"] = "a name carried by both sides is at two different versions"
    elif axis["corroborated"]:
        axis["verdict"] = AGREES
    else:
        axis["reason"] = REASON_NO_SHARED_NAME
    return axis


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def corroborate(
    artifact: Any = None,
    *,
    attestation: Any = None,
    sbom: Any = None,
    triage_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Corroborate an attested subject against the artifact itself. Never raises.

    Every argument is optional and every absence is a named ``unmeasurable``
    reason rather than an error: this block is attached to a verifier's result
    whatever the operator supplied, so the result must always be able to say
    what it could not ask.
    """
    block: Dict[str, Any] = {
        "corroboration": UNMEASURABLE,
        "measured_at": _now(),
        "artifact": None if artifact is None else str(artifact),
        "triage": None,
        "digest": None,
        "sbom": None,
        "note": (
            "Corroboration is evidence ABOUT THE SUBJECT and never moves the "
            "signature verdict beside it."
        ),
    }

    report: Optional[Dict[str, Any]] = None
    artifact_error: Optional[str] = None
    observations: Optional[List[Dict[str, Any]]] = None
    if artifact is None and triage_report is None:
        artifact_error = REASON_NO_ARTIFACT
    else:
        report = triage_report if triage_report is not None else triage(artifact)
        block["artifact"] = report.get("path", block["artifact"])
        hints = hinted_components(report.get("path"), triage_report=report)
        block["triage"] = {
            "status": report.get("status"),
            "reason": report.get("reason"),
            "format": report.get("format"),
            "sha256": report.get("sha256"),
            "sha256_scope": report.get("sha256_scope"),
            "imports_basis": report.get("imports_basis"),
            "hint_status": hints["status"],
            "hint_component_count": hints["component_count"],
        }
        if report.get("status") == "source_unreadable":
            # NOTHING is known about the artifact. Both axes report that, and
            # neither reports "no components".
            artifact_error = "%s: %s" % (REASON_ARTIFACT_UNREADABLE, report.get("reason"))
            report = None
        else:
            observations = _observations(report, hints)

    statement, statement_error = _load_json(attestation)
    sbom_document, sbom_error = _load_json(sbom)
    declared = None if sbom_document is None else _sbom_components(sbom_document)

    block["digest"] = _digest_axis(
        report, artifact_error, statement, statement_error, attestation is not None
    )
    block["sbom"] = _sbom_axis(
        observations, declared, artifact_error, sbom_error, sbom is not None
    )

    verdicts = (block["digest"]["verdict"], block["sbom"]["verdict"])
    if DISAGREES in verdicts:
        block["corroboration"] = DISAGREES
    elif AGREES in verdicts:
        block["corroboration"] = AGREES
    else:
        block["corroboration"] = UNMEASURABLE
    return block


def attach(result: Dict[str, Any], block: Dict[str, Any]) -> Dict[str, Any]:
    """A NEW mapping: *result* unchanged, plus the one ``corroboration`` key.

    The input is not mutated and no existing key is touched -- that is what
    makes "the signature verdict is byte-identical with and without
    ``--artifact``" a property of this function rather than a habit of its
    callers. A result that already carries the key is a programming error and
    raises, because silently overwriting one verifier's verdict with another's
    evidence is precisely the conflation this module exists to prevent.
    """
    if RESULT_KEY in result:
        raise ValueError(
            "refusing to overwrite an existing %r key on a verifier result" % RESULT_KEY
        )
    merged = dict(result)
    merged[RESULT_KEY] = block
    return merged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(block: Dict[str, Any]) -> str:
    lines = [
        "artifact       %s" % block["artifact"],
        "corroboration  %s" % block["corroboration"],
        "  digest       %s%s"
        % (
            block["digest"]["verdict"],
            "  (%s)" % block["digest"]["reason"] if block["digest"]["reason"] else "",
        ),
        "  sbom         %s%s"
        % (
            block["sbom"]["verdict"],
            "  (%s)" % block["sbom"]["reason"] if block["sbom"]["reason"] else "",
        ),
    ]
    for record in block["sbom"]["contradictions"]:
        lines.append(
            "    CONTRADICTION %s: artifact says %s, SBOM says %s"
            % (record["observed_name"], record["observed_version"], record["declared_version"])
        )
    for record in block["sbom"]["corroborated"]:
        lines.append(
            "    corroborated  %s %s (%s)"
            % (record["observed_name"], record["observed_version"], record["observed_kind"])
        )
    lines.append("  %s" % block["note"])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Corroborate an attestation subject against the ARTIFACT itself (xrv-bin-03). "
            "Reports beside a signature verdict; never changes one."
        )
    )
    parser.add_argument("--artifact", help="Path to the compiled artifact")
    parser.add_argument("--attestation", help="Path to an in-toto v1 statement (JSON)")
    parser.add_argument("--sbom", help="Path to a CycloneDX or SPDX document (JSON)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    try:
        block = corroborate(
            args.artifact, attestation=args.attestation, sbom=args.sbom
        )
    except Exception as exc:  # noqa: BLE001 -- exit 2 is "no report", not "clean"
        print("could not produce a report: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(block, indent=2, default=str))
    else:
        print(_render(block))
    # Exit 0 even on `disagrees`: this module REPORTS. A survey with a --gate
    # earns itself a `|| true` (kpr-fix-03).
    return 0


if __name__ == "__main__":
    sys.exit(main())
