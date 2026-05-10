# CUI // SP-CTI
"""WriteGuard deterministic rewriter — regex-based text fixes (D-WG-7a).

Applies common writing fixes found by WriteGuard analysis without any LLM calls.
Pure deterministic: regex substitutions and word-level replacements.

Usage:
    from tools.writing.rewriter import apply_deterministic_fixes, analyze_transitions
    result = apply_deterministic_fixes(text)
    transitions = analyze_transitions(text)

CLI:
    python tools/writing/rewriter.py --text "some text"
    python tools/writing/rewriter.py --file input.txt
    python tools/writing/rewriter.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Replacement tables
# ---------------------------------------------------------------------------

# Informal language → formal equivalents (case-insensitive, whole-word)
_INFORMAL_REPLACEMENTS: List[tuple] = [
    # Contractions / slang
    (r"\bgonna\b", "will"),
    (r"\bwanna\b", "want to"),
    (r"\bgotta\b", "must"),
    (r"\bkinda\b", "somewhat"),
    (r"\bsorta\b", "somewhat"),
    (r"\bain't\b", "is not"),
    # Vague nouns
    (r"\bstuff\b", "components"),
    (r"\bthings\b", "elements"),
    # Filler words (remove entirely — collapse surrounding whitespace)
    (r"\bbasically\s*,?\s*", ""),
    (r"\bobviously\s*,?\s*", ""),
    # Vague quantifiers
    (r"\ba lot of\b", "numerous"),
    (r"\btons of\b", "numerous"),
    (r"\bpretty much\b", "largely"),
]

# Weak / hedging language → confident equivalents
_HEDGING_REPLACEMENTS: List[tuple] = [
    (r"\bwe think\b", "we assess"),
    (r"\bwe believe\b", "we are confident"),
    (r"\bwe hope\b", "we expect"),
    (r"\btry to\b", "will"),
    # "maybe" removed (collapse whitespace)
    (r"\bmaybe\s*,?\s*", ""),
]

# Transition words for pacing analysis
_TRANSITION_WORDS: List[str] = [
    # Additive
    "additionally", "furthermore", "moreover", "also", "likewise",
    "similarly", "in addition", "as well as", "not only", "besides",
    # Adversative
    "however", "nevertheless", "nonetheless", "although", "whereas",
    "on the other hand", "in contrast", "conversely", "despite",
    "on the contrary", "even so", "yet", "still", "instead",
    # Causal
    "therefore", "consequently", "accordingly", "thus", "hence",
    "as a result", "because", "since", "due to", "owing to",
    "for this reason",
    # Sequential
    "first", "second", "third", "finally", "subsequently",
    "meanwhile", "next", "then", "previously", "afterward",
    "in conclusion", "to summarize", "in summary", "ultimately",
    # Clarification
    "specifically", "in particular", "namely", "for example",
    "for instance", "that is", "in other words",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_regex_fix(
    text: str,
    pattern: str,
    replacement: str,
    rule_name: str,
    changes: List[Dict[str, str]],
    flags: int = re.IGNORECASE,
) -> str:
    """Apply a single regex substitution, recording each change."""
    compiled = re.compile(pattern, flags)
    for match in compiled.finditer(text):
        before_snippet = match.group(0)
        # Only record if there is an actual change
        expanded = match.expand(replacement) if replacement else ""
        if before_snippet != expanded:
            changes.append({
                "before": before_snippet,
                "after": expanded if expanded else "(removed)",
                "rule": rule_name,
            })
    return compiled.sub(replacement, text)


def _capitalise_after_period(text: str, changes: List[Dict[str, str]]) -> str:
    """Capitalise the first letter of sentences following periods."""

    def _cap(m: re.Match) -> str:
        original = m.group(0)
        fixed = m.group(1) + m.group(2).upper()
        if original != fixed:
            changes.append({
                "before": original,
                "after": fixed,
                "rule": "capitalise_sentence",
            })
        return fixed

    return re.sub(r"(\.\s+)([a-z])", _cap, text)


def _fix_repeated_words(text: str, changes: List[Dict[str, str]]) -> str:
    """Remove repeated consecutive word pairs ('the the' -> 'the')."""

    def _dedup(m: re.Match) -> str:
        original = m.group(0)
        kept = m.group(1)
        changes.append({
            "before": original,
            "after": kept,
            "rule": "repeated_word",
        })
        return kept

    return re.sub(r"\b(\w+)\s+\1\b", _dedup, text, flags=re.IGNORECASE)


def _fix_double_spaces(text: str, changes: List[Dict[str, str]]) -> str:
    """Collapse runs of multiple spaces into a single space."""
    count = len(re.findall(r"  +", text))
    if count:
        changes.append({
            "before": f"({count} double-space occurrences)",
            "after": "(single spaces)",
            "rule": "double_space",
        })
    return re.sub(r"  +", " ", text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_deterministic_fixes(text: str) -> Dict[str, Any]:
    """Apply regex-based fixes for common writing issues.

    Returns
    -------
    dict
        Keys:
        - ``rewritten_text`` (str): The corrected text.
        - ``changes_made`` (list[dict]): Each dict has ``before``, ``after``,
          and ``rule`` keys describing the substitution.
        - ``change_count`` (int): Total number of individual changes applied.
        - ``rewrite_instructions`` (list[str]): Suggested instructions for an
          LLM rewrite pass covering issues that cannot be fixed deterministically.
    """
    if not text or not text.strip():
        return {
            "rewritten_text": text or "",
            "changes_made": [],
            "change_count": 0,
            "rewrite_instructions": [],
        }

    changes: List[Dict[str, str]] = []
    result = text

    # 1. Double spaces → single space
    result = _fix_double_spaces(result, changes)

    # 2. Capitalise sentences after periods
    result = _capitalise_after_period(result, changes)

    # 3. Remove repeated word pairs
    result = _fix_repeated_words(result, changes)

    # 4. Informal language → formal equivalents
    for pattern, replacement in _INFORMAL_REPLACEMENTS:
        result = _apply_regex_fix(
            result, pattern, replacement, "informal_language", changes
        )

    # 5. Weak / hedging language → confident equivalents
    for pattern, replacement in _HEDGING_REPLACEMENTS:
        result = _apply_regex_fix(
            result, pattern, replacement, "hedging_language", changes
        )

    # Clean up double spaces and duplicate adjacent words introduced by replacements
    result = re.sub(r"  +", " ", result).strip()
    result = re.sub(r"\b(\w+)\s+\1\b", r"\1", result, flags=re.IGNORECASE)

    # Build LLM rewrite instructions for issues beyond deterministic fixing
    rewrite_instructions: List[str] = []
    if any(c["rule"] == "informal_language" for c in changes):
        rewrite_instructions.append(
            "Review replaced informal terms in context — ensure substitutions "
            "read naturally and preserve the original meaning."
        )
    if any(c["rule"] == "hedging_language" for c in changes):
        rewrite_instructions.append(
            "Strengthen remaining hedging language. Replace tentative phrasing "
            "with confident, active-voice assertions where appropriate."
        )
    if any(c["rule"] == "capitalise_sentence" for c in changes):
        rewrite_instructions.append(
            "Check sentence boundaries — some periods may be abbreviations "
            "(e.g. 'U.S. government') where capitalisation is incorrect."
        )

    return {
        "rewritten_text": result,
        "changes_made": changes,
        "change_count": len(changes),
        "rewrite_instructions": rewrite_instructions,
    }


def analyze_transitions(text: str) -> Dict[str, Any]:
    """Analyse pacing via transition word usage per paragraph.

    Returns
    -------
    dict
        Keys:
        - ``transition_count`` (int): Total transition words/phrases found.
        - ``transitions_per_paragraph`` (list[dict]): Per-paragraph breakdown
          with ``paragraph_index``, ``count``, and ``found`` keys.
        - ``pacing_score`` (int): 0-100 composite pacing score.
        - ``findings`` (list[str]): Human-readable observations.
    """
    if not text or not text.strip():
        return {
            "transition_count": 0,
            "transitions_per_paragraph": [],
            "pacing_score": 0,
            "findings": ["empty text"],
        }

    # Split into paragraphs on blank lines
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return {
            "transition_count": 0,
            "transitions_per_paragraph": [],
            "pacing_score": 0,
            "findings": ["no paragraphs detected"],
        }

    total_count = 0
    per_paragraph: List[Dict[str, Any]] = []

    for idx, para in enumerate(paragraphs):
        para_lower = para.lower()
        found: List[str] = []
        for tw in _TRANSITION_WORDS:
            # Use word-boundary matching for single words, substring for phrases
            if " " in tw:
                if tw in para_lower:
                    found.append(tw)
            else:
                if re.search(r"\b" + re.escape(tw) + r"\b", para_lower):
                    found.append(tw)
        total_count += len(found)
        per_paragraph.append({
            "paragraph_index": idx,
            "count": len(found),
            "found": found,
        })

    # Sentences for density calculation
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = max(1, len(sentences))

    # Pacing score: ideal is ~1 transition per 2-3 sentences
    density = total_count / sentence_count
    if 0.25 <= density <= 0.60:
        pacing_score = 100
    elif 0.15 <= density < 0.25 or 0.60 < density <= 0.80:
        pacing_score = 75
    elif 0.05 <= density < 0.15 or 0.80 < density <= 1.0:
        pacing_score = 50
    else:
        pacing_score = 25

    # Findings
    findings: List[str] = []
    if density < 0.15:
        findings.append(
            f"low transition density ({density:.2f} per sentence) — "
            "add connective phrases to improve flow"
        )
    elif density > 0.80:
        findings.append(
            f"high transition density ({density:.2f} per sentence) — "
            "reduce filler transitions to tighten prose"
        )

    # Flag paragraphs with zero transitions (if they have 3+ sentences)
    for pp in per_paragraph:
        para = paragraphs[pp["paragraph_index"]]
        para_sentences = [s.strip() for s in re.split(r"[.!?]+", para) if s.strip()]
        if len(para_sentences) >= 3 and pp["count"] == 0:
            findings.append(
                f"paragraph {pp['paragraph_index'] + 1} has {len(para_sentences)} "
                "sentences but no transitions — consider adding a connective"
            )

    return {
        "transition_count": total_count,
        "transitions_per_paragraph": per_paragraph,
        "pacing_score": pacing_score,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI interface for deterministic rewriter."""
    parser = argparse.ArgumentParser(
        description="WriteGuard deterministic rewriter (D-WG-7a)"
    )
    parser.add_argument("--text", type=str, help="Text to rewrite")
    parser.add_argument("--file", type=str, help="Read text from file")
    parser.add_argument(
        "--transitions", action="store_true", help="Run transition analysis only"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="Output as JSON"
    )
    args = parser.parse_args()

    # Resolve input text
    if args.file:
        input_text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        input_text = args.text
    else:
        # Read from stdin
        input_text = sys.stdin.read()

    if args.transitions:
        result = analyze_transitions(input_text)
    else:
        result = apply_deterministic_fixes(input_text)

    if args.json_output:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if "rewritten_text" in result:
            print("=== Rewritten Text ===")
            print(result["rewritten_text"])
            print(f"\n=== {result['change_count']} changes applied ===")
            for c in result["changes_made"]:
                print(f"  [{c['rule']}] {c['before']} -> {c['after']}")
            if result.get("rewrite_instructions"):
                print("\n=== LLM Rewrite Instructions ===")
                for instr in result["rewrite_instructions"]:
                    print(f"  - {instr}")
        else:
            print(f"=== Transition Analysis (score: {result['pacing_score']}/100) ===")
            print(f"Total transitions: {result['transition_count']}")
            for pp in result["transitions_per_paragraph"]:
                print(
                    f"  Paragraph {pp['paragraph_index'] + 1}: "
                    f"{pp['count']} transitions"
                )
            if result["findings"]:
                print("\nFindings:")
                for f in result["findings"]:
                    print(f"  - {f}")


if __name__ == "__main__":
    main()
