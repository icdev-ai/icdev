#!/usr/bin/env python3
# CUI // SP-CTI
# Authored in both tools/compliance/ and icdev/tools/compliance/ — keep the two in sync.
"""Find the SBOMs in a project and grade them — the four assessors' shared eye.

WHAT THIS REPLACES
------------------
``fedramp_assessor``, ``sbd_assessor``, ``cssp_assessor`` and ``ivv_assessor``
each decided a project had an SBOM because a file matched ``*sbom*.json``,
``*bom*.xml``, ``*cyclonedx*`` or ``*spdx*``. Not one of them opened the file.
An empty file named ``sbom.json`` therefore satisfied all four controls, and
so did a ``.sig.json`` sidecar, a ``spdx-headers.md`` note, or a directory
called ``cyclonedx/``.

This module keeps the same discovery step — the glob is a reasonable way to
*find* candidates — and then does the part that was missing: it opens each
candidate, parses it, and scores it with
``sbom_minimum_elements_validator`` (sbx-sig-02). The answer an assessor gets
back changes from "an SBOM-shaped filename exists" to "a conforming SBOM
exists, scoring N of 23".

WHY FOUR VERDICTS AND NOT A BOOLEAN
-----------------------------------
The four states need different work and must not collapse into one:

``absent``       nothing SBOM-shaped was found at all — generate one.
``ungradeable``  a candidate exists but cannot be read as an SBOM: empty,
                 not JSON, CycloneDX/SPDX XML, or a format this tree
                 declines to grade. This is the bucket the empty
                 ``sbom.json`` lands in, and it is NOT evidence of an SBOM.
``deficient``    a real SBOM that does not yet meet all 23 elements — a
                 defect to fix, with a score naming which ones.
``conforming``   every data field and applicable practice met.

WHAT THIS DOES NOT DO
---------------------
It does not gate. Blocking on a conformance threshold is
``tools/compliance/sbom_conformance_gate.py`` (sbx-gov-01), which owns the
threshold and reads it from ``args/security_gates.yaml``. Nothing here carries
a pass mark, so nothing here has a number to hardcode: assessors map the
verdict onto their own status vocabulary and put the score in the evidence
string a human reads.

CycloneDX/SPDX **XML** is deliberately ``ungradeable`` rather than assumed
good. The validator is JSON-only and says so; crediting an unparsed XML file
would reintroduce exactly the presence check this module removes. The reason
is stated on the finding, so an operator sees "present, not gradeable" and not
a silent failure.
"""

import argparse
import json
import sys
from pathlib import Path

from tools.compliance.sbom_minimum_elements_validator import (
    DETACHED_SIGNATURE_SUFFIX,
    STATUS_GAP,
    STATUS_PARTIAL,
    UnsupportedFormatError,
    validate_file,
)

CLASSIFICATION = "CUI // SP-CTI"

#: The discovery globs the four assessors already used. Kept verbatim so this
#: module finds exactly what they found — only the verdict changes.
SBOM_GLOB_PATTERNS = (
    "*sbom*.json",
    "*bom*.xml",
    "*sbom*.xml",
    "*cyclonedx*",
    "*spdx*",
)

#: A candidate larger than this is reported ungradeable rather than read into
#: memory. Real SBOMs for very large projects run to a few tens of MB.
MAX_SBOM_BYTES = 64 * 1024 * 1024

VERDICT_ABSENT = "absent"
VERDICT_UNGRADEABLE = "ungradeable"
VERDICT_DEFICIENT = "deficient"
VERDICT_CONFORMING = "conforming"

#: Ordered worst-to-best so a project's verdict is `max()` over its files.
VERDICT_RANK = {
    VERDICT_ABSENT: 0,
    VERDICT_UNGRADEABLE: 1,
    VERDICT_DEFICIENT: 2,
    VERDICT_CONFORMING: 3,
}

#: Suffixes that match the discovery globs but are never themselves an SBOM.
_NOT_SBOM_SUFFIXES = (
    DETACHED_SIGNATURE_SUFFIX,  # `<sbom>.sig.json` from sbom_signer (sbx-sig-01)
    ".md",
    ".txt",
    ".rst",
    ".log",
    ".html",
)

#: How many gap/partial elements to name in the human-readable details.
_MAX_NAMED_GAPS = 4


def find_sbom_candidates(project_dir):
    """Return every file under ``project_dir`` matching the SBOM discovery globs.

    Directories are dropped (``*cyclonedx*`` matches a directory named
    ``cyclonedx/``) and so are the sidecars and prose files that match the
    globs but are not SBOMs.
    """
    root = Path(project_dir)
    found = []
    seen = set()
    for pattern in SBOM_GLOB_PATTERNS:
        try:
            matches = root.rglob(pattern)
        except OSError:
            continue
        for match in matches:
            path = str(match)
            if path in seen:
                continue
            seen.add(path)
            try:
                if not match.is_file():
                    continue
            except OSError:
                continue
            lowered = match.name.lower()
            if any(lowered.endswith(suffix) for suffix in _NOT_SBOM_SUFFIXES):
                continue
            found.append(path)
    return sorted(found)


def _ungradeable(path, reason):
    return {
        "path": str(path),
        "name": Path(path).name,
        "gradeable": False,
        "conformant": False,
        "verdict": VERDICT_UNGRADEABLE,
        "reason": reason,
        "elements_met": 0,
        "elements_total": 0,
        "score_pct": 0.0,
        "format": "",
        "format_version": "",
        "component_count": 0,
        "gaps": [],
    }


def grade_sbom_file(path):
    """Parse and score one candidate. Never raises — returns a finding dict.

    A finding is ungradeable when the file could not be read *as an SBOM*.
    That is the answer for an empty file, for prose, for XML, and for a
    format the validator declines to grade approximately; each carries the
    concrete reason, because "no SBOM" and "an SBOM I cannot read" call for
    different work.
    """
    sbom_path = Path(path)
    try:
        size = sbom_path.stat().st_size
    except OSError as exc:
        return _ungradeable(path, f"cannot stat file: {exc}")

    if size == 0:
        return _ungradeable(path, "file is empty (0 bytes) — an SBOM-shaped name, no SBOM")
    if size > MAX_SBOM_BYTES:
        return _ungradeable(path, f"file is {size} bytes, over the {MAX_SBOM_BYTES}-byte read limit")

    if sbom_path.suffix.lower() == ".xml":
        return _ungradeable(
            path,
            "CycloneDX/SPDX XML is not gradeable — the 2026 minimum-elements "
            "validator reads JSON only. Re-emit as JSON to have it scored.",
        )

    try:
        report = validate_file(sbom_path)
    except UnsupportedFormatError as exc:
        return _ungradeable(path, str(exc))
    except (OSError, UnicodeDecodeError) as exc:
        return _ungradeable(path, f"cannot read file: {exc}")
    except ValueError as exc:
        # load_document raises UnsupportedFormatError (a ValueError) on non-JSON;
        # this catches any other malformed-document ValueError the reader raises.
        return _ungradeable(path, f"not a readable SBOM: {exc}")

    document = report.get("document") or {}
    score = report.get("score") or {}
    gaps = [
        {"id": element["id"], "title": element["title"], "status": element["status"]}
        for element in report.get("elements") or []
        if element.get("status") in (STATUS_GAP, STATUS_PARTIAL)
    ]
    conformant = bool(report.get("conformant"))

    return {
        "path": str(sbom_path),
        "name": sbom_path.name,
        "gradeable": True,
        "conformant": conformant,
        "verdict": VERDICT_CONFORMING if conformant else VERDICT_DEFICIENT,
        "reason": "",
        "elements_met": report.get("elements_met", 0),
        "elements_total": report.get("elements_total", 0),
        "score_pct": score.get("weighted_pct", 0.0),
        "format": document.get("format_name") or "",
        "format_version": document.get("format_version") or "",
        "component_count": document.get("component_count", 0),
        "gaps": gaps,
        "report": report,
    }


def collect_sbom_evidence(project_dir):
    """Discover, parse and score every SBOM candidate under ``project_dir``.

    Returns a dict with the project-level ``verdict`` (the best any single
    file achieved), the findings split into buckets, and ``best`` — the
    highest-scoring gradeable document, which is the one an assessor should
    quote.
    """
    candidates = find_sbom_candidates(project_dir)
    findings = [grade_sbom_file(path) for path in candidates]

    gradeable = [f for f in findings if f["gradeable"]]
    conforming = [f for f in gradeable if f["conformant"]]
    deficient = [f for f in gradeable if not f["conformant"]]
    ungradeable = [f for f in findings if not f["gradeable"]]

    if conforming:
        verdict = VERDICT_CONFORMING
    elif deficient:
        verdict = VERDICT_DEFICIENT
    elif ungradeable:
        verdict = VERDICT_UNGRADEABLE
    else:
        verdict = VERDICT_ABSENT

    best = max(gradeable, key=lambda f: f["elements_met"]) if gradeable else None

    return {
        "project_dir": str(project_dir),
        "verdict": verdict,
        "candidates": candidates,
        "findings": findings,
        "conforming": conforming,
        "deficient": deficient,
        "ungradeable": ungradeable,
        "gradeable_count": len(gradeable),
        "best": best,
        "classification": CLASSIFICATION,
    }


def has_real_sbom(evidence):
    """True when at least one candidate actually parsed as an SBOM.

    This is the honest replacement for the old ``bool(glob_matches)``. It is
    deliberately not "conforming" — a deficient SBOM is still an SBOM, and
    deciding that a deficient one fails a control is the caller's judgement to
    make with the score in hand.
    """
    return evidence["gradeable_count"] > 0


def score_phrase(evidence):
    """One clause naming the best document's score, for an evidence string.

    Empty string when nothing was gradeable, so callers can concatenate it
    without producing "scoring 0 of 0".
    """
    best = evidence.get("best")
    if not best:
        return ""
    return f"scoring {best['elements_met']} of {best['elements_total']} 2026 minimum elements"


def describe(evidence):
    """A sentence stating what was found and what it scored.

    Written for the person reading an assessment report, so it always names
    the count, the verdict and — when there is one — the score. The four
    assessors put this in ``evidence``, which is the field they persist.
    """
    verdict = evidence["verdict"]
    if verdict == VERDICT_ABSENT:
        return "No SBOM artifacts detected."

    if verdict == VERDICT_UNGRADEABLE:
        count = len(evidence["ungradeable"])
        first = evidence["ungradeable"][0]
        return (
            f"{count} SBOM-shaped file(s) found but none could be parsed as an SBOM "
            f"— e.g. {first['name']}: {first['reason']}"
        )

    best = evidence["best"]
    fmt = f"{best['format']} {best['format_version']}".strip()
    lead = (
        f"Conforming SBOM: {best['name']}"
        if verdict == VERDICT_CONFORMING
        else f"SBOM present but non-conforming: {best['name']}"
    )
    tail = (
        f"({fmt}, {best['component_count']} component(s), "
        f"{score_phrase(evidence)}, {best['score_pct']}% weighted)"
    )
    unreadable = len(evidence["ungradeable"])
    suffix = f" {unreadable} further candidate(s) were not gradeable." if unreadable else ""
    return f"{lead} {tail}.{suffix}"


def detail(evidence):
    """The follow-on line naming which elements are unmet, or why nothing parsed."""
    if evidence["verdict"] == VERDICT_ABSENT:
        return (
            "Expected: a CycloneDX (1.x) or SPDX 2.2/2.3 JSON document. "
            "Generate with tools/compliance/sbom_generator.py."
        )

    if evidence["verdict"] == VERDICT_UNGRADEABLE:
        return "; ".join(f"{f['name']}: {f['reason']}" for f in evidence["ungradeable"][:5])

    best = evidence["best"]
    if best["conformant"]:
        return f"{best['path']} meets all {best['elements_total']} elements."

    named = "; ".join(f"{g['id']} ({g['status']})" for g in best["gaps"][:_MAX_NAMED_GAPS])
    more = len(best["gaps"]) - _MAX_NAMED_GAPS
    if more > 0:
        named += f"; +{more} more"
    return (
        f"{len(best['gaps'])} element(s) unmet in {best['name']}: {named}. "
        "Score with tools/compliance/sbom_minimum_elements_validator.py --sbom "
        f"'{best['path']}' --json."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Discover and grade the SBOMs in a project — what the compliance assessors see."
    )
    parser.add_argument("--project-dir", default=".", help="Project root to scan (default: .)")
    parser.add_argument("--json", action="store_true", help="Emit the full evidence dict as JSON")
    args = parser.parse_args(argv)

    evidence = collect_sbom_evidence(args.project_dir)

    if args.json:
        # `report` holds the validator's full 23-element verdict per file and
        # would swamp the summary; it stays available to importers.
        printable = dict(evidence)
        printable["findings"] = [
            {k: v for k, v in f.items() if k != "report"} for f in evidence["findings"]
        ]
        for key in ("conforming", "deficient", "ungradeable"):
            printable[key] = [{k: v for k, v in f.items() if k != "report"} for f in evidence[key]]
        if evidence["best"]:
            printable["best"] = {k: v for k, v in evidence["best"].items() if k != "report"}
        print(json.dumps(printable, indent=2))
    else:
        print(f"[{CLASSIFICATION}]")
        print(f"Project:  {evidence['project_dir']}")
        print(f"Verdict:  {evidence['verdict']}")
        print(f"Evidence: {describe(evidence)}")
        print(f"Details:  {detail(evidence)}")

    return 0 if evidence["verdict"] == VERDICT_CONFORMING else 1


if __name__ == "__main__":
    sys.exit(main())
