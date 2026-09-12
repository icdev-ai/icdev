#!/usr/bin/env python3
# CUI // SP-CTI
r"""A string in a binary is a HINT, and the component says so (xrv-bin-03).

WHY. ``sbom_generator`` discovers components from DECLARED MANIFESTS only --
``dependency_resolver.resolve_project`` reads each ecosystem's lockfile and
degrades to this repo's manifest parsers -- so every component in every SBOM
this platform has produced is something a *build file* said. The classic
rebuttal to that inventory is "the binary does not match the SBOM", and until
``binary_triage`` (xrv-bin-01) nothing in this tree opened a compiled artifact
at all. This module is the consumer that turns what the triage OBSERVED into
components the existing generator can carry.

IT IS A HINT AND IT IS LABELLED ONE, EVERYWHERE. A vendored copy of OpenSSL
leaves ``OpenSSL 1.1.1k  25 Mar 2021`` in ``.rodata``; so does a *string
constant naming a version somebody else's code requires*, and so does a
user-agent template. Nothing here can tell those apart, so nothing here
asserts. Every component produced carries ``evidence: binary_strings`` and
``confidence: unasserted`` in its CycloneDX properties, plus the RAW STRING the
hint came from -- because the reader's next question is always "what did it
actually say", and a component that has thrown the evidence away cannot answer.

THE SEAM'S TOKEN IS NOT THE VERSION, AND SAYING SO IS THE WHOLE OF
``_version_text``. ``binary_triage._VERSION_TOKEN`` is
``\b\d+\.\d+(?:\.\d+){0,2}\b`` and a trailing ``\b`` cannot sit between ``1``
and ``k`` -- so the token it returns for ``OpenSSL 1.1.1k`` is **1.1**. Emitting
``OpenSSL @ 1.1`` would be a WRONG version, not a vague one: a CVE lookup
against it answers about a release the artifact does not contain. So the token
is extended over the run of version characters that FOLLOWS IT IN THE STRING IT
WAS FOUND IN, the extension is bounded, and BOTH values ride on the component
(``version_token`` is the seam's, ``version`` is the observed text) with
``version_basis`` naming which rule produced it. Nothing is invented: the
extension only ever copies characters the artifact itself contains.

A HINT WITH NO NAME BESIDE IT IS NOT A COMPONENT. Binaries are full of
version-shaped numbers -- timestamps, coordinates, format strings -- and a
component named after nothing is noise that buries the real entries. So a hint
whose string carries no identifier before it is SKIPPED, and skipped BY NAME
with its reason (``no_name_adjacent``) and its raw string, because a silently
dropped observation is the same defect one layer down.

FIVE STATUSES, NEVER MERGED, because each sends a reader somewhere different:

    ok                    the artifact was read and hints were derived
    no_hints              it WAS read and carries no version-shaped string.
                          A MEASURED zero, and not the same as the next two.
    unmeasurable          the triage could not read the file
                          (``source_unreadable``) -- NOTHING is known about the
                          artifact, and this is never "no components"
    unsupported_format    the bytes were read and carry no magic the triage
                          knows. Strings ARE still scanned, so this status can
                          arrive WITH components
    truncated             only a prefix of the file was read; everything here
                          is a statement about that prefix and says so

MEASURED AND NOT FIXED HERE, because it belongs one module up: that same
``\b`` means there is NO word boundary between the ``v`` and the ``1`` of
``v1.1.1k``, so the seam returns NO hint at all for that very common spelling.
This module reports the measured zero rather than running a second version
regex over the strings -- a private scanner here would make an SBOM and
``binary_triage --json`` disagree about what the same artifact says, and a
reader would have no way to tell which one had looked. Widening the token is
``binary_triage``'s card; a test here pins today's behaviour so the day it
changes is visible.

NOTHING HERE OPENS THE FILE. ``binary_triage.triage`` is the one reader, is
IMPORTED rather than re-implemented, and carries the sandbox posture
(docs/security/sandbox-coverage.md, Gap 71) that a second private ``open(...,
"rb")`` here would step around. An AST test pins that.

Usage:
    python -m tools.compliance.binary_components /path/to/artifact --json
    python -m tools.compliance.binary_components /path/to/artifact

    >>> from icdev.tools.compliance.binary_components import hinted_components
    >>> result = hinted_components("/usr/lib/libssl.so.1.1")
    >>> result["status"], result["component_count"]
    ('ok', 3)

Exit codes: 0 a report was produced, whatever it says; 2 no report could be
produced at all.
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

from tools.analyzers.binary_triage import (  # noqa: E402
    STATUS_SOURCE_UNREADABLE,
    STATUS_TRUNCATED,
    STATUS_UNSUPPORTED_FORMAT,
    triage,
)

__all__ = [
    "EVIDENCE_BINARY_STRINGS",
    "STATUSES",
    "SKIP_REASONS",
    "binary_hint_properties",
    "hinted_components",
    "is_hinted_component",
]

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

#: The one spelling of where a hinted component came from. It is the value of
#: the `icdev:sbom:binary-hint:evidence` property AND of the component dict's
#: `binary_hint.evidence`, so a consumer filtering on either sees one set.
EVIDENCE_BINARY_STRINGS = "binary_strings"

#: A hint is never an assertion. There is deliberately no second value here and
#: no numeric score: a confidence number computed from a string match would be a
#: measurement of the regex, not of the artifact.
CONFIDENCE_UNASSERTED = "unasserted"

STATUS_OK = "ok"
STATUS_NO_HINTS = "no_hints"
STATUS_UNMEASURABLE = "unmeasurable"
STATUSES: Tuple[str, ...] = (
    STATUS_OK,
    STATUS_NO_HINTS,
    STATUS_UNMEASURABLE,
    STATUS_UNSUPPORTED_FORMAT,
    STATUS_TRUNCATED,
)

#: Every way a hint fails to become a component. Closed, and every skip is
#: reported with one of these beside the string it came from.
SKIP_NO_NAME = "no_name_adjacent"
SKIP_NO_STRING = "hint_string_not_recoverable"
SKIP_DUPLICATE = "duplicate_of_earlier_hint"
SKIP_OVER_BUDGET = "over_max_components"
SKIP_REASONS: Tuple[str, ...] = (
    SKIP_NO_NAME,
    SKIP_NO_STRING,
    SKIP_DUPLICATE,
    SKIP_OVER_BUDGET,
)

VERSION_BASIS_TOKEN = "triage_token"
VERSION_BASIS_EXTENDED = "triage_token_extended_in_string"

# ---------------------------------------------------------------------------
# Property names. One prefix, so a recipient reads every field of this element
# without having to know which ones exist.
# ---------------------------------------------------------------------------

PROPERTY_PREFIX = "icdev:sbom:binary-hint"
PROPERTY_EVIDENCE = PROPERTY_PREFIX + ":evidence"
PROPERTY_CONFIDENCE = PROPERTY_PREFIX + ":confidence"
PROPERTY_SOURCE = PROPERTY_PREFIX + ":source-artifact"
PROPERTY_SOURCE_SHA256 = PROPERTY_PREFIX + ":source-sha256"
PROPERTY_SOURCE_SHA256_SCOPE = PROPERTY_PREFIX + ":source-sha256-scope"
PROPERTY_STRING = PROPERTY_PREFIX + ":evidence-string"
PROPERTY_VERSION_TOKEN = PROPERTY_PREFIX + ":version-token"
PROPERTY_VERSION_BASIS = PROPERTY_PREFIX + ":version-basis"
PROPERTY_TRIAGE_STATUS = PROPERTY_PREFIX + ":triage-status"

# ---------------------------------------------------------------------------
# Bounds. Every one is reported on the result rather than quietly applied.
# ---------------------------------------------------------------------------

#: `binary_triage` already caps its own hint list at MAX_VERSION_HINTS (32).
#: This is the second bound, on what becomes a COMPONENT, and it exists so a
#: future widening of that cap cannot silently flood an SBOM.
DEFAULT_MAX_COMPONENTS = 32

#: Longest observed version text. A run longer than this is not a version, so
#: the extension is abandoned and the seam's token is used with the basis saying
#: so -- never a 200-character `version` field.
MAX_VERSION_TEXT = 40

#: The raw evidence string is carried verbatim up to here. `binary_triage`
#: already truncates a run at 256; this is the SBOM-facing bound.
MAX_EVIDENCE_STRING = 200

#: Characters that may extend a version token rightwards. Deliberately excludes
#: whitespace and `/`, so `1.1.1k  25 Mar 2021` stops at the `k` and
#: `libcurl/7.68.0 OpenSSL/1.1.1` cannot run one version into the next.
_VERSION_TAIL = re.compile(r"[0-9A-Za-z._+~-]*")

#: The trailing identifier before a version token. Must START with a letter: a
#: name derived from digits alone is the address of something, not a component.
_TRAILING_NAME = re.compile(r"([A-Za-z][A-Za-z0-9_+.\-]*)$")

#: Separators stripped between a name and its version. `v` is NOT in this set --
#: rstripping it turns `libuv 1.44` into a component called `libu`. The `v` in
#: `OpenSSL v1.1.1k` is handled by _NAME_STOPWORDS instead.
_NAME_SEPARATORS = " \t/\\-_:=@[](),#|"

#: An identifier that is a version LABEL rather than a component name. One retry
#: is made behind each of these, which is what makes `OpenSSL v1.1.1k` resolve.
_NAME_STOPWORDS = frozenset(
    {"v", "ver", "vers", "version", "rev", "revision", "release", "build", "r", "b"}
)

#: Shortest name kept. One character is what falls out of `Copyright (c) 1998`
#: and of any punctuation run, and it names nothing.
MIN_NAME_LENGTH = 2


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _version_text(text: str, token: str, start: int) -> Tuple[str, str]:
    """Extend *token* over the version characters that follow it in *text*.

    Returns ``(version, basis)``. The extension only ever copies characters the
    artifact already contains, and is abandoned -- basis ``triage_token`` -- when
    the resulting run is longer than a version plausibly is, so a ``version``
    field can never carry a sentence.
    """
    tail = _VERSION_TAIL.match(text, start + len(token)).group()
    if not tail:
        return token, VERSION_BASIS_TOKEN
    candidate = token + tail
    if len(candidate) > MAX_VERSION_TEXT:
        return token, VERSION_BASIS_TOKEN
    return candidate, VERSION_BASIS_EXTENDED


def _derive_name(prefix: str, _depth: int = 0) -> Optional[str]:
    """The identifier immediately before a version token, or None.

    None is a real answer and is what stops a version-shaped number in a format
    string from becoming a component. One retry is made behind a version LABEL
    (``v``, ``version``, ``build``), because that label is not the name either.
    """
    trimmed = prefix.rstrip(_NAME_SEPARATORS)
    if not trimmed:
        return None
    match = _TRAILING_NAME.search(trimmed)
    if not match:
        return None
    name = match.group(1).strip("._-+")
    if name.lower() in _NAME_STOPWORDS:
        if _depth >= 1:
            return None
        return _derive_name(trimmed[: match.start(1)], _depth + 1)
    if len(name) < MIN_NAME_LENGTH:
        return None
    return name


def _locate(strings: List[str], token: str) -> Optional[Tuple[str, int]]:
    """First string carrying *token*, and where.

    File order, so the first occurrence wins -- a deterministic rule, and the
    same one on every run.
    """
    for text in strings:
        index = text.find(token)
        if index != -1:
            return text, index
    return None


def _component(
    *,
    name: str,
    version: str,
    version_token: str,
    version_basis: str,
    evidence_string: str,
    report: Dict[str, Any],
) -> Dict[str, Any]:
    """One component dict in the shape ``sbom_generator``'s manifest parsers emit.

    ``purl`` is deliberately ABSENT rather than guessed: a purl asserts an
    ecosystem and a namespace, and a string in a binary names neither. An
    invented ``pkg:generic/openssl@1.1.1k`` would be looked up by a downstream
    scanner as though somebody had declared it.
    """
    return {
        "type": "library",
        "name": name.lower(),
        # The spelling the ARTIFACT used. `name` above is ICDEV's normalization
        # of it, and the 2026 Component Name element is the producer's name.
        "declared_name": name,
        "version": version,
        "scope": "required",
        "group": "",
        "source": report.get("path"),
        # Everything that makes this a hint rather than a declaration. Read by
        # `binary_hint_properties` and by the corroboration seam; invisible to
        # every other part of the generator, which treats it as any component.
        "binary_hint": {
            "evidence": EVIDENCE_BINARY_STRINGS,
            "confidence": CONFIDENCE_UNASSERTED,
            "source_artifact": report.get("path"),
            "source_sha256": report.get("sha256"),
            "source_sha256_scope": report.get("sha256_scope"),
            "evidence_string": evidence_string[:MAX_EVIDENCE_STRING],
            "version_token": version_token,
            "version_basis": version_basis,
            "triage_status": report.get("status"),
        },
    }


def is_hinted_component(component: Dict[str, Any]) -> bool:
    """True for a component this module produced.

    The one predicate -- a caller testing ``component.get("source", "")
    .endswith(".so")`` is guessing.
    """
    hint = component.get("binary_hint")
    return isinstance(hint, dict) and hint.get("evidence") == EVIDENCE_BINARY_STRINGS


def binary_hint_properties(component: Dict[str, Any]) -> List[Dict[str, str]]:
    """CycloneDX properties for a hinted component; ``[]`` for anything else.

    Empty for an ordinary component is what makes this safe to call in the
    generator's per-component loop: a manifest-declared entry is byte-identical
    with and without this hook.
    """
    if not is_hinted_component(component):
        return []
    hint = component["binary_hint"]
    pairs = (
        (PROPERTY_EVIDENCE, hint.get("evidence")),
        (PROPERTY_CONFIDENCE, hint.get("confidence")),
        (PROPERTY_SOURCE, hint.get("source_artifact")),
        (PROPERTY_SOURCE_SHA256, hint.get("source_sha256")),
        (PROPERTY_SOURCE_SHA256_SCOPE, hint.get("source_sha256_scope")),
        (PROPERTY_STRING, hint.get("evidence_string")),
        (PROPERTY_VERSION_TOKEN, hint.get("version_token")),
        (PROPERTY_VERSION_BASIS, hint.get("version_basis")),
        (PROPERTY_TRIAGE_STATUS, hint.get("triage_status")),
    )
    return [{"name": key, "value": str(value)} for key, value in pairs if value is not None]


def hinted_components(
    path: Any,
    *,
    triage_report: Optional[Dict[str, Any]] = None,
    max_components: int = DEFAULT_MAX_COMPONENTS,
) -> Dict[str, Any]:
    """Components hinted by the strings in the artifact at *path*. Never raises.

    *triage_report* lets a caller that has already triaged the artifact reuse
    that report rather than reading the file twice. It is the same shape
    ``binary_triage.triage`` returns, and passing one that describes a DIFFERENT
    artifact is the caller's error -- the report's own ``path`` is what this
    module records as the source.
    """
    report = triage_report if triage_report is not None else triage(path)
    triage_status = report.get("status")

    result: Dict[str, Any] = {
        "artifact": report.get("path", str(path)),
        "status": STATUS_UNMEASURABLE,
        "reason": None,
        "components": [],
        "component_count": 0,
        "skipped": [],
        "skipped_count": 0,
        "hints_seen": None,
        "triage": {
            "status": triage_status,
            "reason": report.get("reason"),
            "format": report.get("format"),
            "sha256": report.get("sha256"),
            "sha256_scope": report.get("sha256_scope"),
            "strings_truncated": report.get("strings_truncated"),
        },
        "limits": {
            "max_components": max_components,
            "max_version_text": MAX_VERSION_TEXT,
            "max_evidence_string": MAX_EVIDENCE_STRING,
        },
    }

    # NOTHING is known about the artifact. `components: []` here is the absence
    # of a measurement and must never be read as the absence of components --
    # which is exactly why `status` is not `no_hints`.
    if triage_status == STATUS_SOURCE_UNREADABLE:
        result["reason"] = report.get("reason") or "triage_source_unreadable"
        return result

    hints = report.get("version_hints")
    strings = report.get("strings")
    if hints is None or strings is None:
        result["reason"] = "triage_reported_no_strings"
        return result

    result["hints_seen"] = len(hints)
    seen_identity = set()
    for token in hints:
        located = _locate(strings, token)
        if located is None:
            # The seam derives hints FROM the strings it returns, so this cannot
            # happen today. Reported rather than asserted away: a future change
            # to either side turns it into a visible skip, not a silent drop.
            result["skipped"].append({"version_token": token, "reason": SKIP_NO_STRING})
            continue
        text, index = located
        name = _derive_name(text[:index])
        if name is None:
            result["skipped"].append(
                {
                    "version_token": token,
                    "reason": SKIP_NO_NAME,
                    "evidence_string": text[:MAX_EVIDENCE_STRING],
                }
            )
            continue
        version, basis = _version_text(text, token, index)
        identity = (name.lower(), version)
        if identity in seen_identity:
            result["skipped"].append(
                {"version_token": token, "reason": SKIP_DUPLICATE, "name": name}
            )
            continue
        if len(result["components"]) >= max_components:
            result["skipped"].append(
                {"version_token": token, "reason": SKIP_OVER_BUDGET, "name": name}
            )
            continue
        seen_identity.add(identity)
        result["components"].append(
            _component(
                name=name,
                version=version,
                version_token=token,
                version_basis=basis,
                evidence_string=text,
                report=report,
            )
        )

    result["component_count"] = len(result["components"])
    result["skipped_count"] = len(result["skipped"])

    if triage_status == STATUS_TRUNCATED:
        # A prefix read. Everything above is a statement about that prefix, and
        # the status is what carries that to a reader who sees only the summary.
        result["status"] = STATUS_TRUNCATED
    elif triage_status == STATUS_UNSUPPORTED_FORMAT:
        result["status"] = STATUS_UNSUPPORTED_FORMAT
    elif result["components"]:
        result["status"] = STATUS_OK
    else:
        # The file WAS read end to end and carries no usable version string.
        # A measured zero.
        result["status"] = STATUS_NO_HINTS
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(result: Dict[str, Any]) -> str:
    lines = [
        "artifact   %s" % result["artifact"],
        "status     %s%s"
        % (result["status"], "  (%s)" % result["reason"] if result.get("reason") else ""),
        "triage     %s / format %s" % (result["triage"]["status"], result["triage"]["format"]),
        "hints      %s seen, %d component(s), %d skipped"
        % (
            "unmeasured" if result["hints_seen"] is None else result["hints_seen"],
            result["component_count"],
            result["skipped_count"],
        ),
    ]
    for comp in result["components"]:
        hint = comp["binary_hint"]
        lines.append(
            "  %s %s  [%s, %s]  <- %r"
            % (
                comp["declared_name"],
                comp["version"],
                hint["evidence"],
                hint["confidence"],
                hint["evidence_string"],
            )
        )
    for skip in result["skipped"]:
        lines.append("  SKIPPED %s  %s" % (skip["version_token"], skip["reason"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Components HINTED by the strings in a compiled artifact (xrv-bin-03)"
    )
    parser.add_argument("path", help="Path to the compiled artifact")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--max-components",
        type=int,
        default=DEFAULT_MAX_COMPONENTS,
        help="Cap on hinted components; the overflow is reported by name",
    )
    args = parser.parse_args(argv)

    try:
        result = hinted_components(args.path, max_components=args.max_components)
    except Exception as exc:  # noqa: BLE001 -- exit 2 is "no report", not "clean"
        print("could not produce a report: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(_render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
