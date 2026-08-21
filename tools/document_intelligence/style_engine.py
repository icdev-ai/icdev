# CUI // SP-CTI
"""DIC Style Engine — deterministic "one voice" gate.

Checks document section text against configurable rules in args/dic_style_rules.yaml.
No LLM required; all checks are regex + heuristic (air-gap safe).

Usage::

    from tools.document_intelligence.style_engine import check_style, check_sections

    result = check_style("The Contractor will utilize AI to facilitate...")
    print(result.score, result.passed, result.violations)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_RULES_PATH = Path(__file__).resolve().parents[2] / "args" / "dic_style_rules.yaml"
_rules_cache: dict | None = None


def _load_rules() -> dict:
    global _rules_cache
    if _rules_cache is None:
        with open(_RULES_PATH, encoding="utf-8") as f:
            _rules_cache = yaml.safe_load(f)
    return _rules_cache


@dataclass
class Violation:
    rule_id: str
    rule_name: str
    severity: str          # error | warning | info
    message: str
    suggestion: str = ""
    match: str = ""
    offset: int = -1


@dataclass
class StyleResult:
    score: float           # 0–100; higher = better
    passed: bool
    violations: list[Violation] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "warning"]

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "stats": self.stats,
            "violations": [
                {
                    "rule_id": v.rule_id,
                    "rule_name": v.rule_name,
                    "severity": v.severity,
                    "message": v.message,
                    "suggestion": v.suggestion,
                    "match": v.match,
                }
                for v in self.violations
            ],
        }


# ── Individual rule checkers ──────────────────────────────────────────────────

def _check_forbidden_terms(text: str, rule: dict) -> list[Violation]:
    violations = []
    cap = rule.get("max_violations_per_rule") or 5
    for term in rule.get("terms", []):
        pattern = term.get("pattern", "")
        suggestion = term.get("suggestion", "")
        try:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
        except re.error:
            continue
        for m in matches[:cap]:
            violations.append(Violation(
                rule_id=rule["id"],
                rule_name=rule["name"],
                severity=rule.get("severity", "warning"),
                message=f'Found "{m.group()}" — {rule["description"]}',
                suggestion=suggestion,
                match=m.group(),
                offset=m.start(),
            ))
        if len(violations) >= cap:
            break
    return violations


def _check_passive_ratio(text: str, rule: dict) -> list[Violation]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sentences:
        return []
    pattern = rule.get("passive_pattern", r"\b(is|are|was|were|be|been|being)\s+\w+ed\b")
    threshold = rule.get("threshold", 0.25)
    passive_count = sum(1 for s in sentences if re.search(pattern, s, re.IGNORECASE))
    ratio = passive_count / len(sentences)
    if ratio > threshold:
        return [Violation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule.get("severity", "warning"),
            message=f"Passive voice in {round(ratio * 100)}% of sentences (threshold: {round(threshold * 100)}%)",
            suggestion="Rewrite passive constructions with an active subject (e.g., 'The Contractor executes…')",
            match=f"{passive_count}/{len(sentences)} sentences",
        )]
    return []


def _check_sentence_length(text: str, rule: dict) -> list[Violation]:
    violations = []
    max_avg = rule.get("max_avg_words", 35)
    max_single = rule.get("max_single_sentence", 60)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences:
        return []
    word_counts = [len(s.split()) for s in sentences]
    avg = sum(word_counts) / len(word_counts)
    if avg > max_avg:
        violations.append(Violation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule.get("severity", "warning"),
            message=f"Average sentence length {round(avg, 1)} words exceeds {max_avg}",
            suggestion="Break long sentences at conjunctions or restructure as bullet points",
            match=f"avg {round(avg, 1)} words",
        ))
    for i, (s, wc) in enumerate(zip(sentences, word_counts)):
        if wc > max_single:
            snippet = s[:80] + ("…" if len(s) > 80 else "")
            violations.append(Violation(
                rule_id=rule["id"],
                rule_name=rule["name"],
                severity=rule.get("severity", "warning"),
                message=f"Sentence {i + 1} is {wc} words (max: {max_single})",
                suggestion="Split into two or more sentences",
                match=snippet,
            ))
    return violations


def _check_acronyms(text: str, rule: dict) -> list[Violation]:
    pattern = rule.get("acronym_pattern", r"\b([A-Z]{2,6})\b")
    always_defined = set(rule.get("always_defined", []))
    cap = 5

    # Find first definition: "Full Name (ACRONYM)" or "ACRONYM (Full Name)"
    defined: set[str] = set(always_defined)
    for m in re.finditer(r"\b[A-Z][a-z].*?\(([A-Z]{2,6})\)", text):
        defined.add(m.group(1))
    for m in re.finditer(r"\b([A-Z]{2,6})\s*\(", text):
        defined.add(m.group(1))

    violations = []
    seen_undefined: set[str] = set()
    for m in re.finditer(pattern, text):
        acr = m.group(1)
        if acr in defined or acr in seen_undefined:
            continue
        seen_undefined.add(acr)
        violations.append(Violation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule.get("severity", "warning"),
            message=f'Acronym "{acr}" used without prior definition in this section',
            suggestion=f'Define on first use: "Full Name ({acr})"',
            match=acr,
        ))
        if len(violations) >= cap:
            break
    return violations


# ── Dispatch ──────────────────────────────────────────────────────────────────

_CHECKERS = {
    "forbidden_terms": _check_forbidden_terms,
    "replacement_terms": _check_forbidden_terms,  # same logic, different label
    "passive_ratio": _check_passive_ratio,
    "sentence_length": _check_sentence_length,
    "acronym_check": _check_acronyms,
}


def check_style(text: str, rules_override: dict | None = None) -> StyleResult:
    """Run all enabled style rules against *text* and return a StyleResult."""
    cfg = rules_override or _load_rules()
    rules = cfg.get("rules", [])
    passing_score = cfg.get("meta", {}).get("passing_score", 70)
    global_cap = cfg.get("meta", {}).get("max_violations_per_rule", 5)

    all_violations: list[Violation] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rule_with_cap = {**rule, "max_violations_per_rule": global_cap}
        checker = _CHECKERS.get(rule.get("type", ""))
        if checker:
            all_violations.extend(checker(text, rule_with_cap))

    # Score: start at 100, deduct per violation weighted by severity
    deductions = {"error": 15, "warning": 5, "info": 1}
    raw_deduction = sum(deductions.get(v.severity, 5) for v in all_violations)
    score = max(0.0, 100.0 - raw_deduction)

    # Stats
    word_count = len(text.split())
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    stats = {
        "word_count": word_count,
        "sentence_count": len(sentences),
        "avg_sentence_words": round(sum(len(s.split()) for s in sentences) / len(sentences), 1) if sentences else 0,
        "error_count": sum(1 for v in all_violations if v.severity == "error"),
        "warning_count": sum(1 for v in all_violations if v.severity == "warning"),
        "info_count": sum(1 for v in all_violations if v.severity == "info"),
    }

    return StyleResult(
        score=score,
        passed=score >= passing_score,
        violations=all_violations,
        stats=stats,
    )


def check_sections(sections: list[dict]) -> dict:
    """Check a list of section dicts (each with 'heading' and 'content').

    Returns::

        {
          "overall_score": float | None,   # None == NOT ASSESSED
          "passed": bool | None,
          "assessed_sections": int,
          "sections": [{"heading": ..., "assessed": bool,
                        "result": StyleResult.to_dict()}, ...]
        }

    ``overall_score`` is ``None`` — never a number — when no section carried any
    prose to assess (rem-hyg-13). ``None`` and ``0.0`` are different claims and
    the caller must be able to tell them apart.
    """
    results = []
    total_score = 0.0
    assessed = 0
    for sec in sections:
        content = (sec.get("content") or "").strip()
        heading = (sec.get("heading") or "").strip()
        if not content:
            # NOT ASSESSED. An empty section has no prose, so there is nothing
            # for a style rule to fire on — which is not the same claim as
            # "this prose is flawless". Every key of the StyleResult shape is
            # kept so the response stays shape-compatible; the two that would
            # be a fabrication are null.
            empty = StyleResult(score=0.0, passed=False).to_dict()
            empty["score"] = None
            empty["passed"] = None
            results.append({"heading": heading, "assessed": False, "result": empty})
            continue
        r = check_style(content)
        total_score += r.score
        assessed += 1
        results.append({"heading": heading, "assessed": True, "result": r.to_dict()})

    # Divided by the number of sections ACTUALLY SCORED, not by len(results).
    # The old spelling divided by every section including the empty ones while
    # summing only the scored ones, so one clean section beside four empty ones
    # averaged to 20 — a second fabrication, in the opposite direction.
    overall = total_score / assessed if assessed else None
    cfg = _load_rules()
    passing = cfg.get("meta", {}).get("passing_score", 70)
    return {
        "overall_score": round(overall, 1) if overall is not None else None,
        "passed": (overall >= passing) if overall is not None else None,
        "assessed_sections": assessed,
        "sections": results,
    }
