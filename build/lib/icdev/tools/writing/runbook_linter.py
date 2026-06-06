# CUI // SP-CTI
"""WriteGuard Runbook Linter (WG 19).

Deterministic best-practice checks for operational runbooks.

Checks:
    - Numbered step count
    - Imperative verbs at step start
    - Per-step verification / rollback language
    - Prerequisites section present
    - Rollback section or per-step rollback
    - Imperative tone (flag pronouns: you, we, our)
    - Ambiguous modals (might, could, should)
    - Commands wrapped in backticks / code fences
    - Time estimates on steps

Usage:
    from tools.writing.runbook_linter import lint_runbook

    result = lint_runbook(text)

Scanner-tier only (zero LLM). Stdlib only.

CUI // SP-CTI
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_STEP_RE = re.compile(r"^\s*(?:(\d+)\.|Step\s+(\d+))\s+(.*)$", re.IGNORECASE)
_IMPERATIVE_VERBS = {
    "install", "configure", "restart", "verify", "check", "deploy",
    "run", "execute", "stop", "start", "apply", "create", "delete", "update",
}
_VERIFY_WORDS = {"verify", "check", "confirm", "validate", "rollback", "revert", "undo"}
_PREREQ_HEADINGS = ("prerequisite", "prereq", "before you begin", "requirements")
_ROLLBACK_HEADINGS = ("rollback", "revert", "recovery")
_BARE_COMMANDS = ("grep", "curl", "kubectl", "aws", "gcloud")
_TIME_RE = re.compile(r"\(\s*~?\s*\d+(?:[-\u2013]\d+)?\s*(?:s|sec|seconds?|min|minutes?|h|hr|hours?)\s*\)", re.IGNORECASE)
_MODALS_RE = re.compile(r"\b(might|could|should)\b", re.IGNORECASE)
_PRONOUN_RE = re.compile(r"\b(you|we|our)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_headings(lines: List[str], keywords: tuple) -> bool:
    for line in lines:
        stripped = line.strip().lower()
        # Heading-like: starts with #, or is a short ALL-CAPS/title line
        is_heading = stripped.startswith("#") or (len(stripped) < 80 and stripped.endswith(":"))
        if not is_heading:
            # also accept plain lines that are obvious section markers
            if len(stripped) > 80:
                continue
        for kw in keywords:
            if kw in stripped:
                return True
    return False


def _in_code_fence(lines: List[str]) -> List[bool]:
    """Return a list parallel to lines, True where line is inside a ``` fenced block."""
    inside = False
    flags: List[bool] = []
    for line in lines:
        if line.strip().startswith("```"):
            inside = not inside
            flags.append(True)
            continue
        flags.append(inside)
    return flags


# ---------------------------------------------------------------------------
# Main linter
# ---------------------------------------------------------------------------


def lint_runbook(text: str) -> Dict[str, Any]:
    """Lint a runbook document and return a structured report."""
    lines = text.splitlines()
    in_fence = _in_code_fence(lines)

    issues: List[Dict[str, Any]] = []

    # Locate steps
    steps: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if in_fence[idx]:
            continue
        m = _STEP_RE.match(line)
        if m:
            number = m.group(1) or m.group(2)
            body = m.group(3) or ""
            steps.append({"line": idx, "number": number, "body": body})

    # Imperative verb check per step
    for s in steps:
        first_word = re.split(r"\s+", s["body"].strip(), maxsplit=1)[0].lower().strip(".,:;")
        if first_word and first_word not in _IMPERATIVE_VERBS:
            issues.append({
                "category": "step_not_imperative",
                "line": s["line"] + 1,
                "detail": f"Step {s['number']} starts with '{first_word}' (expected imperative verb)",
            })

    # Verification / rollback within 3 lines of each step
    steps_with_verification = 0
    for s in steps:
        window = " ".join(lines[s["line"]: s["line"] + 4]).lower()
        if any(w in window for w in _VERIFY_WORDS):
            steps_with_verification += 1
        else:
            issues.append({
                "category": "step_missing_verification",
                "line": s["line"] + 1,
                "detail": f"Step {s['number']} lacks verify/check/rollback within 3 lines",
            })

    # Per-step rollback tally
    steps_with_rollback = 0
    for s in steps:
        window = " ".join(lines[s["line"]: s["line"] + 4]).lower()
        if any(w in window for w in ("rollback", "revert", "undo")):
            steps_with_rollback += 1

    # Prerequisites section
    has_prereqs = _find_headings(lines, _PREREQ_HEADINGS)
    if not has_prereqs:
        issues.append({
            "category": "missing_prerequisites_section",
            "line": 0,
            "detail": "No Prerequisites / Before You Begin / Requirements heading found",
        })

    # Rollback section (global or per-step coverage)
    has_rollback_section = _find_headings(lines, _ROLLBACK_HEADINGS) or (
        bool(steps) and steps_with_rollback == len(steps)
    )
    if not has_rollback_section:
        issues.append({
            "category": "missing_rollback_section",
            "line": 0,
            "detail": "No Rollback / Revert / Recovery section and not every step has rollback",
        })

    # Pronouns (imperative tone)
    for idx, line in enumerate(lines):
        if in_fence[idx]:
            continue
        for m in _PRONOUN_RE.finditer(line):
            issues.append({
                "category": "pronoun_in_runbook",
                "line": idx + 1,
                "detail": f"Use imperative, avoid '{m.group(1)}'",
            })

    # Ambiguous modals
    for idx, line in enumerate(lines):
        if in_fence[idx]:
            continue
        for m in _MODALS_RE.finditer(line):
            issues.append({
                "category": "ambiguous_modal",
                "line": idx + 1,
                "detail": f"'{m.group(1)}' is ambiguous — prefer 'will' or 'must'",
            })

    # Bare commands outside backticks / code fences
    for idx, line in enumerate(lines):
        if in_fence[idx]:
            continue
        # Strip inline-backticked spans so we don't flag commands already in backticks
        stripped = re.sub(r"`[^`]*`", "", line)
        for cmd in _BARE_COMMANDS:
            if re.search(r"\b" + re.escape(cmd) + r"\b", stripped):
                issues.append({
                    "category": "bare_command",
                    "line": idx + 1,
                    "detail": f"Command '{cmd}' should be wrapped in backticks or a code fence",
                })
                break  # one flag per line is enough

    # Time estimates on steps
    for s in steps:
        window = " ".join(lines[s["line"]: s["line"] + 2])
        if not _TIME_RE.search(window):
            issues.append({
                "category": "step_missing_time_estimate",
                "line": s["line"] + 1,
                "detail": f"Step {s['number']} lacks a time estimate like (~5min)",
            })

    # Score: start from 100 and subtract based on issue density + structural gaps
    penalty = len(issues) * 2
    if not has_prereqs:
        penalty += 5
    if not has_rollback_section:
        penalty += 10
    score = max(0, 100 - penalty)

    return {
        "step_count": len(steps),
        "steps_with_verification": steps_with_verification,
        "steps_with_rollback": steps_with_rollback,
        "has_prereqs": has_prereqs,
        "has_rollback_section": has_rollback_section,
        "issues": issues,
        "score": score,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WriteGuard Runbook Linter (WG 19)")
    parser.add_argument("--text", type=str, default=None)
    parser.add_argument("--file", type=str, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    result = lint_runbook(text)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Score: {result['score']}/100")
        print(f"Steps: {result['step_count']} | verified: {result['steps_with_verification']} | "
              f"with rollback: {result['steps_with_rollback']}")
        print(f"Prereqs: {result['has_prereqs']} | Rollback section: {result['has_rollback_section']}")
        for i in result["issues"][:50]:
            print(f"  [{i['category']}] line {i['line']}: {i['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
