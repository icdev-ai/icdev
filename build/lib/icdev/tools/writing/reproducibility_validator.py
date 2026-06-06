#!/usr/bin/env python3
# CUI // SP-CTI
"""Reproducibility Validator (WG 26) — deterministic checks for reproducible
technical claims. Scanner-tier only (no LLM).

Checks:
  1. Unpinned versions           ("latest", "newest" near install/version)
  2. Package install w/o version (npm/pip/gem)
  3. Perf claims w/o methodology (no baseline/sample-size/benchmark nearby)
  4. Missing environment         (deploy/install/run without OS or version)
  5. Crypto without spec         (TLS/SSL/cipher w/o algo + mode + key size)
  6. Commands without expected output (backtick/code-fence shell w/o "expected"/"output")
  7. "It works" statements       (no evidence)

Entry point:
    validate_reproducibility(text: str) -> dict
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _window(text: str, pos: int, radius: int = 120) -> str:
    return text[max(0, pos - radius) : pos + radius]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


PAT_UNPINNED_WORDS = re.compile(
    r"\b(latest|newest|current|most recent)\b", re.IGNORECASE
)
PAT_INSTALL_CONTEXT = re.compile(
    r"\b(install|version|upgrade)\b", re.IGNORECASE
)


def _check_unpinned_versions(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in PAT_UNPINNED_WORDS.finditer(text):
        win = _window(text, m.start(), 80)
        if PAT_INSTALL_CONTEXT.search(win):
            out.append(
                {
                    "check": "unpinned_version",
                    "severity": "high",
                    "line": _line_of(text, m.start()),
                    "match": m.group(0),
                    "suggestion": (
                        "Pin an explicit version (e.g. '1.2.3') rather than "
                        f"'{m.group(0)}'."
                    ),
                }
            )
    return out


PAT_NPM = re.compile(r"\bnpm\s+install\s+(?P<pkg>[@\w/\-.]+)")
PAT_PIP = re.compile(r"\bpip\s+install\s+(?P<pkg>[\w\-.]+)")
PAT_GEM = re.compile(r"\bgem\s+install\s+(?P<pkg>[\w\-.]+)")


def _check_package_no_version(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in PAT_NPM.finditer(text):
        pkg = m.group("pkg")
        # pkg has a version if "@" present (not just leading scope)
        has_version = "@" in pkg.lstrip("@")
        if not has_version:
            out.append(
                {
                    "check": "npm_no_version",
                    "severity": "medium",
                    "line": _line_of(text, m.start()),
                    "match": m.group(0),
                    "suggestion": f"Pin version: npm install {pkg}@X.Y.Z",
                }
            )
    for m in PAT_PIP.finditer(text):
        pkg = m.group("pkg")
        tail_start = m.end()
        tail = text[tail_start : tail_start + 20]
        if not re.match(r"\s*(==|>=|<=|~=|!=)", tail):
            out.append(
                {
                    "check": "pip_no_version",
                    "severity": "medium",
                    "line": _line_of(text, m.start()),
                    "match": m.group(0),
                    "suggestion": f"Pin version: pip install {pkg}==X.Y.Z",
                }
            )
    for m in PAT_GEM.finditer(text):
        pkg = m.group("pkg")
        tail = text[m.end() : m.end() + 40]
        if "--version" not in tail and "-v" not in tail:
            out.append(
                {
                    "check": "gem_no_version",
                    "severity": "medium",
                    "line": _line_of(text, m.start()),
                    "match": m.group(0),
                    "suggestion": f"Pin version: gem install {pkg} --version X.Y.Z",
                }
            )
    return out


PAT_PERF_MULT = re.compile(
    r"\b\d+x\s+(?:faster|slower)\b", re.IGNORECASE
)
PAT_PERF_PCT = re.compile(
    r"\b\d+%\s+(?:faster|slower|improvement)\b", re.IGNORECASE
)
PAT_METHOD = re.compile(
    r"\b(baseline|sample\s+size|n\s?=\s?\d+|test|tests|benchmark|benchmarks|"
    r"measured|methodology)\b",
    re.IGNORECASE,
)


def _check_perf_no_methodology(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pattern in (PAT_PERF_MULT, PAT_PERF_PCT):
        for m in pattern.finditer(text):
            win = _window(text, m.start(), 160)
            if not PAT_METHOD.search(win):
                out.append(
                    {
                        "check": "perf_no_methodology",
                        "severity": "high",
                        "line": _line_of(text, m.start()),
                        "match": m.group(0),
                        "suggestion": (
                            "Include baseline, sample size, and benchmark "
                            "details so the claim is reproducible."
                        ),
                    }
                )
    return out


PAT_ENV_VERBS = re.compile(
    r"\b(deploy|install|run|build|compile)\b", re.IGNORECASE
)
PAT_ENV_HINT = re.compile(
    r"\b("
    r"linux|ubuntu|debian|rhel|centos|alpine|macos|osx|windows|"
    r"python\s*3(?:\.\d+)*|node\s*\d+|java\s*\d+|go\s*1\.\d+|"
    r"docker|kubernetes|k8s|container|"
    r"\d+\.\d+(?:\.\d+)?|rhel\s*\d+|ubuntu\s*\d+\.\d+"
    r")\b",
    re.IGNORECASE,
)


def _check_missing_environment(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in PAT_ENV_VERBS.finditer(text):
        win = _window(text, m.start(), 140)
        if not PAT_ENV_HINT.search(win):
            out.append(
                {
                    "check": "missing_environment",
                    "severity": "medium",
                    "line": _line_of(text, m.start()),
                    "match": m.group(0),
                    "suggestion": (
                        "Specify OS / runtime version / container image so "
                        "the step is reproducible."
                    ),
                }
            )
    return out


PAT_CRYPTO = re.compile(
    r"\b(encryption|encrypted|TLS|SSL|cipher)\b", re.IGNORECASE
)
PAT_CRYPTO_SPEC = re.compile(
    r"\b("
    r"AES[-_]?\d{3}(?:[-_]?(?:GCM|CBC|CTR|CCM))?|"
    r"ChaCha20(?:-Poly1305)?|"
    r"TLS\s*1\.[0-3]|"
    r"RSA[-_]?\d{3,4}|"
    r"ECDSA|Ed25519|"
    r"SHA-?256|SHA-?384|SHA-?512|"
    r"\b\d{3,4}-bit\b"
    r")\b",
    re.IGNORECASE,
)


def _check_crypto_no_spec(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_lines: set[int] = set()
    for m in PAT_CRYPTO.finditer(text):
        win = _window(text, m.start(), 160)
        line = _line_of(text, m.start())
        if line in seen_lines:
            continue
        if not PAT_CRYPTO_SPEC.search(win):
            seen_lines.add(line)
            out.append(
                {
                    "check": "crypto_no_spec",
                    "severity": "high",
                    "line": line,
                    "match": m.group(0),
                    "suggestion": (
                        "Specify algorithm, mode, and key size "
                        "(e.g. AES-256-GCM, TLS 1.3)."
                    ),
                }
            )
    return out


PAT_FENCE = re.compile(
    r"```(?:bash|sh|shell|console|powershell|ps1)?\n(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)
PAT_BACKTICK_CMD = re.compile(
    r"`(?P<cmd>(?:sudo\s+)?(?:npm|pip|gem|apt|apt-get|yum|dnf|brew|curl|wget|"
    r"docker|kubectl|git|python|node|go|make|cmake|cargo)\b[^`]*)`"
)
PAT_EXPECT_MARK = re.compile(
    r"\b(expected|output|result|returns?|should (?:print|return|show))\b",
    re.IGNORECASE,
)


def _check_cmd_no_expected(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in PAT_FENCE.finditer(text):
        tail_start = m.end()
        tail = text[tail_start : tail_start + 200]
        if not PAT_EXPECT_MARK.search(tail):
            out.append(
                {
                    "check": "cmd_no_expected_output",
                    "severity": "low",
                    "line": _line_of(text, m.start()),
                    "match": "<code fence>",
                    "suggestion": (
                        "Document expected output / return code so readers "
                        "can verify the command ran correctly."
                    ),
                }
            )
    for m in PAT_BACKTICK_CMD.finditer(text):
        tail = text[m.end() : m.end() + 160]
        if not PAT_EXPECT_MARK.search(tail):
            out.append(
                {
                    "check": "cmd_no_expected_output",
                    "severity": "low",
                    "line": _line_of(text, m.start()),
                    "match": m.group("cmd"),
                    "suggestion": (
                        "State what the command should print / return so the "
                        "claim is verifiable."
                    ),
                }
            )
    return out


PAT_IT_WORKS = re.compile(
    r"\b(works\s+(?:fine|well|great)|runs\s+(?:well|fine|smoothly)|"
    r"functions?\s+correctly|just\s+works)\b",
    re.IGNORECASE,
)
PAT_EVIDENCE = re.compile(
    r"\b(test|tests|benchmark|measured|verified|proof|log|logs|"
    r"output|coverage|ci|ci/cd|pipeline)\b",
    re.IGNORECASE,
)


def _check_it_works(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in PAT_IT_WORKS.finditer(text):
        win = _window(text, m.start(), 160)
        if not PAT_EVIDENCE.search(win):
            out.append(
                {
                    "check": "it_works_no_evidence",
                    "severity": "medium",
                    "line": _line_of(text, m.start()),
                    "match": m.group(0),
                    "suggestion": (
                        "Replace with concrete evidence — test results, "
                        "benchmark numbers, or a verification step."
                    ),
                }
            )
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


SEVERITY_WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.0}


def validate_reproducibility(text: str) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    issues.extend(_check_unpinned_versions(text))
    issues.extend(_check_package_no_version(text))
    issues.extend(_check_perf_no_methodology(text))
    issues.extend(_check_missing_environment(text))
    issues.extend(_check_crypto_no_spec(text))
    issues.extend(_check_cmd_no_expected(text))
    issues.extend(_check_it_works(text))

    issues.sort(key=lambda i: (i["line"], i["check"]))

    by_sev = {"low": 0, "medium": 0, "high": 0}
    for i in issues:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1

    total_weight = sum(SEVERITY_WEIGHT[i["severity"]] for i in issues)
    # Scale: each "high" issue costs ~6 points, capped at 100 lost.
    score = max(0.0, 100.0 - total_weight * 6.0)

    suggestions = [i["suggestion"] for i in issues]

    return {
        "issues": issues,
        "severity": by_sev,
        "suggestions": suggestions,
        "reproducibility_score": round(score, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="WG 26 Reproducibility Validator"
    )
    parser.add_argument("--file", "-f", help="Path to text file")
    parser.add_argument("--text", "-t", help="Text string")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        content = args.text
    else:
        content = sys.stdin.read()

    result = validate_reproducibility(content)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Reproducibility score : {result['reproducibility_score']}/100")
        sev = result["severity"]
        print(
            f"Issues                : high={sev['high']} "
            f"medium={sev['medium']} low={sev['low']}"
        )
        for i in result["issues"]:
            print(
                f"  [{i['severity'].upper():6s}] line {i['line']:4d} "
                f"{i['check']:24s} {i['match']!r}"
            )
            print(f"      -> {i['suggestion']}")
