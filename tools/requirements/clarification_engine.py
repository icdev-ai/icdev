#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Structured clarification engine -- Impact x Uncertainty prioritized questions.

Adapted from GitHub spec-kit's clarification workflow for ICDEV.
Uses a 2D matrix (Impact x Uncertainty) to prioritize which unclear
requirements to clarify first.

ADR D159: Deterministic prioritization (consistent with D21 readiness scoring).

Usage:
    python tools/requirements/clarification_engine.py --spec-file specs/foo.md \
        --max-questions 5 --json
    python tools/requirements/clarification_engine.py --session-id sess-abc \
        --max-questions 5 --json
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1

# ---------------------------------------------------------------------------
# Impact x Uncertainty priority matrix  (1 = highest priority)
# ---------------------------------------------------------------------------
PRIORITY_MATRIX = {
    ("mission_critical", "unknown"):   1,
    ("mission_critical", "ambiguous"):  2,
    ("mission_critical", "assumed"):    3,
    ("compliance_required", "unknown"):  2,
    ("compliance_required", "ambiguous"): 3,
    ("compliance_required", "assumed"):  4,
    ("enhancement", "unknown"):         3,
    ("enhancement", "ambiguous"):       4,
    ("enhancement", "assumed"):         5,
}

# Keyword sets used for impact classification
_MISSION_CRITICAL_KEYWORDS = frozenset({
    "mission", "operational", "safety", "availability", "core capability",
    "primary function", "critical", "life-threatening", "warfighter",
    "combat", "command and control", "c2", "real-time", "failover",
})

_COMPLIANCE_KEYWORDS = frozenset({
    "nist", "stig", "fedramp", "cmmc", "audit", "encryption",
    "authentication", "ato", "fips", "cui", "authorization",
    "compliance", "accreditation", "rmf", "poam", "ssp",
    "cjis", "hipaa", "pci", "iso 27001", "soc 2",
})

# Hedging words that signal assumptions
_HEDGING_WORDS = frozenset({
    "should", "probably", "likely", "typically", "usually",
    "might", "perhaps", "may", "could", "assume", "assumed",
    "expected", "ideally", "generally", "presumably",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_ambiguity_patterns() -> list:
    """Load known ambiguity patterns from context file.

    Returns a list of pattern dicts.  Graceful fallback to empty list.
    """
    path = BASE_DIR / "context" / "requirements" / "ambiguity_patterns.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("ambiguity_patterns", [])
    except (json.JSONDecodeError, OSError):
        return []


def _load_config() -> dict:
    """Load clarification config from args/spec_config.yaml.

    Returns the ``clarification`` section or sensible defaults.
    """
    config_path = BASE_DIR / "args" / "spec_config.yaml"
    if config_path.exists():
        try:
            import yaml  # optional -- air-gap safe fallback below
            with open(config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            return cfg.get("clarification", {})
        except ImportError:
            pass
    # Defaults if YAML unavailable
    return {
        "max_questions_per_spec": 5,
        "max_questions_per_turn": 3,
    }


def _parse_spec_sections(spec_path: Path) -> dict:
    """Parse a Markdown spec into {heading: body_text} pairs.

    Splits on ``## `` headers.  Text before the first ``## `` is stored
    under the key ``"_preamble"``.
    """
    content = spec_path.read_text(encoding="utf-8")
    sections: dict = {}
    current_heading = "_preamble"
    current_lines: list = []

    for line in content.splitlines():
        if line.startswith("## "):
            sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    sections[current_heading] = "\n".join(current_lines).strip()
    return sections


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _score_impact(text: str, context: dict = None) -> str:
    """Classify a text snippet by impact level.

    Returns one of: ``mission_critical``, ``compliance_required``, ``enhancement``.
    """
    lower = text.lower()

    # Check mission-critical keywords
    for kw in _MISSION_CRITICAL_KEYWORDS:
        if kw in lower:
            return "mission_critical"

    # Check compliance keywords
    for kw in _COMPLIANCE_KEYWORDS:
        if kw in lower:
            return "compliance_required"

    # Check context hints (e.g. requirement_type from DB)
    if context:
        rtype = (context.get("requirement_type") or "").lower()
        if rtype in ("security", "compliance"):
            return "compliance_required"
        if rtype in ("performance", "infrastructure"):
            return "mission_critical"

    return "enhancement"


def _score_uncertainty(text: str, patterns: list) -> str:
    """Classify a text snippet by uncertainty level.

    Returns one of: ``unknown``, ``ambiguous``, ``assumed``.

    Decision rules:
        - **unknown**: very short (<10 words), empty, or missing key details.
        - **ambiguous**: contains known ambiguity pattern phrases.
        - **assumed**: contains hedging words.
        - Default fallback: ``assumed``.
    """
    stripped = text.strip()

    # Unknown -- empty or very short
    word_count = len(stripped.split())
    if word_count < 10:
        return "unknown"

    lower = stripped.lower()

    # Ambiguous -- check loaded patterns
    for pat in patterns:
        phrase = pat.get("phrase", "").lower()
        if phrase and phrase in lower:
            return "ambiguous"

    # Assumed -- hedging words
    words = set(re.findall(r"\b[a-z]+\b", lower))
    if words & _HEDGING_WORDS:
        return "assumed"

    return "assumed"


def _find_ambiguous_phrase(text: str, patterns: list) -> dict | None:
    """Return the first matching ambiguity pattern dict, or None."""
    lower = text.lower()
    for pat in patterns:
        phrase = pat.get("phrase", "").lower()
        if phrase and phrase in lower:
            return pat
    return None


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

def _generate_question(item: dict) -> str:
    """Generate a human-readable clarification question for an item.

    The question style depends on the uncertainty type:
        - unknown: broad open-ended question
        - ambiguous: quote the vague phrase and ask to specify
        - assumed: state the assumption and ask for confirmation
    """
    section = item.get("section", "this area")
    uncertainty = item.get("uncertainty", "unknown")
    snippet = item.get("snippet", "").strip()
    pattern = item.get("pattern")  # matched ambiguity pattern dict

    if uncertainty == "unknown":
        return (
            f"The section '{section}' appears incomplete or empty. "
            f"What are the specific requirements for {section.lower()}?"
        )

    if uncertainty == "ambiguous" and pattern:
        phrase = pattern.get("phrase", "")
        clarification = pattern.get("clarification", "provide a measurable definition")
        return (
            f"In '{section}', you mentioned '{phrase}'. "
            f"{clarification}"
        )

    # assumed
    if snippet:
        # Extract the hedging word for context
        lower = snippet.lower()
        found_hedge = None
        for hw in _HEDGING_WORDS:
            if hw in lower:
                found_hedge = hw
                break
        if found_hedge:
            return (
                f"In '{section}', the text uses '{found_hedge}', which implies an assumption. "
                f"Is this a firm requirement (MUST), or a recommendation (SHOULD)? "
                f"Please clarify the exact expectation."
            )

    return (
        f"The section '{section}' contains assumptions that need confirmation. "
        f"Can you clarify the exact requirements?"
    )


def _prioritize_questions(items: list, max_questions: int = 5) -> list:
    """Sort items by priority (1 = highest) and return top N.

    Ties broken by: impact severity (mission_critical > compliance_required > enhancement),
    then by section name alphabetically for determinism.
    """
    impact_order = {"mission_critical": 0, "compliance_required": 1, "enhancement": 2}

    def sort_key(item):
        return (
            item.get("priority", 99),
            impact_order.get(item.get("impact", "enhancement"), 9),
            item.get("section", ""),
        )

    sorted_items = sorted(items, key=sort_key)
    return sorted_items[:max_questions]


# ---------------------------------------------------------------------------
# Main analysis entry points
# ---------------------------------------------------------------------------

def analyze_spec_clarity(spec_path: Path, max_questions: int = 5) -> dict:
    """Analyze a spec file for clarity and generate prioritized clarification questions.

    Args:
        spec_path: Path to the Markdown spec file.
        max_questions: Maximum number of questions to return.

    Returns:
        Result dict with questions, clarity score, and analysis metadata.
    """
    spec_path = Path(spec_path)
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    patterns = _load_ambiguity_patterns()
    config = _load_config()
    max_q = min(max_questions, config.get("max_questions_per_spec", 10))

    sections = _parse_spec_sections(spec_path)

    # Required sections from checklist (subset for clarity check)
    expected_sections = {
        "Feature Description", "User Story", "Solution Statement",
        "ATO Impact Assessment", "Acceptance Criteria",
        "Implementation Plan", "Testing Strategy",
    }

    items: list = []
    section_scores: list = []

    # Check each section
    for heading, body in sections.items():
        if heading == "_preamble":
            continue

        impact = _score_impact(body)
        uncertainty = _score_uncertainty(body, patterns)
        priority = PRIORITY_MATRIX.get((impact, uncertainty), 5)

        # Track per-section clarity (1.0 for assumed, 0.5 for ambiguous, 0.0 for unknown)
        clarity_val = {"unknown": 0.0, "ambiguous": 0.5, "assumed": 0.8}.get(uncertainty, 0.8)
        section_scores.append(clarity_val)

        if uncertainty in ("unknown", "ambiguous"):
            matched_pattern = _find_ambiguous_phrase(body, patterns)
            item = {
                "section": heading,
                "impact": impact,
                "uncertainty": uncertainty,
                "priority": priority,
                "snippet": body[:200] if body else "",
                "pattern": matched_pattern,
            }
            item["question"] = _generate_question(item)
            item["context"] = body[:300] if body else ""
            # Remove internal pattern dict from final output
            item.pop("pattern", None)
            item.pop("snippet", None)
            items.append(item)

    # Check for missing required sections
    present_sections = set(sections.keys()) - {"_preamble"}
    for expected in expected_sections:
        # Case-insensitive matching
        found = any(expected.lower() == s.lower() for s in present_sections)
        if not found:
            impact = _score_impact(expected)
            item = {
                "section": expected,
                "impact": impact,
                "uncertainty": "unknown",
                "priority": PRIORITY_MATRIX.get((impact, "unknown"), 3),
                "question": (
                    f"The required section '{expected}' is missing from the spec. "
                    f"What are the requirements for {expected.lower()}?"
                ),
                "context": "Section not found in specification.",
            }
            items.append(item)
            section_scores.append(0.0)

    # Prioritize and select top N
    top_items = _prioritize_questions(items, max_q)

    # Clarity score: average of section scores (0.0 - 1.0)
    clarity_score = sum(section_scores) / max(len(section_scores), 1)

    return {
        "status": "ok",
        "spec_file": str(spec_path),
        "total_items_analyzed": len(sections) - (1 if "_preamble" in sections else 0),
        "total_issues_found": len(items),
        "questions": top_items,
        "clarity_score": round(clarity_score, 4),
    }


def analyze_requirements_clarity(
    session_id: str,
    max_questions: int = 5,
    db_path=None,
) -> dict:
    """Analyze intake session requirements for clarity and generate questions.

    Loads requirements from ``intake_requirements`` table, scores each for
    impact and uncertainty, and returns prioritized clarification questions.

    Args:
        session_id: Intake session identifier.
        max_questions: Maximum number of questions to return.
        db_path: Optional DB path override.

    Returns:
        Result dict with questions, clarity score, and analysis metadata.
    """
    conn = _get_connection(db_path)

    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    reqs = [dict(r) for r in reqs]
    conn.close()

    if not reqs:
        return {
            "status": "ok",
            "session_id": session_id,
            "total_items_analyzed": 0,
            "total_issues_found": 0,
            "questions": [],
            "clarity_score": 0.0,
            "message": "No requirements found in session.",
        }

    patterns = _load_ambiguity_patterns()
    config = _load_config()
    max_q = min(max_questions, config.get("max_questions_per_spec", 10))

    items: list = []
    clarity_values: list = []

    for req in reqs:
        raw_text = req.get("raw_text", "") or ""
        req_type = req.get("requirement_type", "functional")
        req_id = req.get("id", "unknown")

        context_info = {"requirement_type": req_type}
        impact = _score_impact(raw_text, context=context_info)
        uncertainty = _score_uncertainty(raw_text, patterns)
        priority = PRIORITY_MATRIX.get((impact, uncertainty), 5)

        clarity_val = {"unknown": 0.0, "ambiguous": 0.5, "assumed": 0.8}.get(uncertainty, 0.8)

        # Also factor in existing scores from intake
        existing_clarity = req.get("clarity_score")
        if existing_clarity is not None:
            try:
                existing_clarity = float(existing_clarity)
                clarity_val = min(clarity_val, existing_clarity)
            except (TypeError, ValueError):
                pass

        clarity_values.append(clarity_val)

        if uncertainty in ("unknown", "ambiguous"):
            matched_pattern = _find_ambiguous_phrase(raw_text, patterns)
            item = {
                "section": f"Requirement {req_id} ({req_type})",
                "impact": impact,
                "uncertainty": uncertainty,
                "priority": priority,
                "snippet": raw_text[:200],
                "pattern": matched_pattern,
                "requirement_id": req_id,
            }
            item["question"] = _generate_question(item)
            item["context"] = raw_text[:300]
            # Clean up internal fields
            item.pop("pattern", None)
            item.pop("snippet", None)
            items.append(item)

        elif uncertainty == "assumed":
            # Check if it has low completeness
            completeness = req.get("completeness_score")
            if completeness is not None:
                try:
                    if float(completeness) < 0.5:
                        item = {
                            "section": f"Requirement {req_id} ({req_type})",
                            "impact": impact,
                            "uncertainty": "assumed",
                            "priority": priority,
                            "requirement_id": req_id,
                        }
                        item["question"] = _generate_question({
                            **item,
                            "snippet": raw_text[:200],
                        })
                        item["context"] = raw_text[:300]
                        items.append(item)
                except (TypeError, ValueError):
                    pass

    top_items = _prioritize_questions(items, max_q)
    clarity_score = sum(clarity_values) / max(len(clarity_values), 1)

    if _HAS_AUDIT:
        log_event(
            event_type="clarification_analyzed",
            actor="icdev-requirements-analyst",
            action=f"Clarity analysis for session {session_id}: {clarity_score:.1%}, "
                   f"{len(items)} issues, {len(top_items)} questions",
            project_id=dict(session).get("project_id"),
            details={
                "session_id": session_id,
                "clarity_score": round(clarity_score, 4),
                "issues_found": len(items),
            },
        )

    return {
        "status": "ok",
        "session_id": session_id,
        "total_items_analyzed": len(reqs),
        "total_issues_found": len(items),
        "questions": top_items,
        "clarity_score": round(clarity_score, 4),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_human(result: dict) -> str:
    """Format result dict as human-readable text output."""
    lines: list = []

    source = result.get("spec_file") or result.get("session_id", "unknown")
    lines.append(f"Clarity Analysis: {source}")
    lines.append(f"  Clarity Score: {result['clarity_score']:.0%}")
    lines.append(f"  Items Analyzed: {result['total_items_analyzed']}")
    lines.append(f"  Issues Found: {result['total_issues_found']}")

    questions = result.get("questions", [])
    if questions:
        lines.append(f"\n  Top {len(questions)} Clarification Questions:")
        for i, q in enumerate(questions, 1):
            impact_tag = q.get("impact", "enhancement").upper().replace("_", " ")
            uncertainty_tag = q.get("uncertainty", "unknown").upper()
            prio = q.get("priority", 5)
            lines.append(f"\n  {i}. [P{prio}] [{impact_tag}] [{uncertainty_tag}]")
            lines.append(f"     Section: {q.get('section', 'N/A')}")
            lines.append(f"     Q: {q.get('question', '')}")
    else:
        lines.append("\n  No clarification questions needed.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Clarification Engine (ADR D159)"
    )
    parser.add_argument("--spec-file", help="Path to spec file to analyze")
    parser.add_argument("--session-id", help="Intake session ID to analyze")
    parser.add_argument("--max-questions", type=int, default=5,
                        help="Maximum clarification questions (default 5)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable colored output")
    args = parser.parse_args()

    if not args.spec_file and not args.session_id:
        parser.error("Either --spec-file or --session-id is required.")

    try:
        if args.spec_file:
            result = analyze_spec_clarity(
                spec_path=Path(args.spec_file),
                max_questions=args.max_questions,
            )
        else:
            result = analyze_requirements_clarity(
                session_id=args.session_id,
                max_questions=args.max_questions,
            )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(_format_human(result))

    except (ValueError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
