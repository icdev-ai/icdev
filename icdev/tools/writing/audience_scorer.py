# CUI // SP-CTI
"""WriteGuard WG 13 — Audience persona scorer.

Scores written content against a target audience persona using deterministic
metrics (readability grade, jargon density, required-term presence). No LLM.

Personas:
    CEO, CFO, Board, Engineer, Auditor, Lawyer

Returns a dict with match_score (0-100), issues, suggestions, and metrics.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from tools.writing.analysis_engine import _readability_check


# ---------------------------------------------------------------------------
# Jargon dictionary (~40 technical acronyms)
# ---------------------------------------------------------------------------

JARGON_TERMS = {
    "AI", "ML", "LLM", "NLP", "API", "SDK", "CLI", "GUI", "CI/CD", "TLS",
    "HTTPS", "REST", "SaaS", "PaaS", "IaaS", "K8s", "IaC", "SLA", "SLO",
    "SRE", "DevOps", "DevSecOps", "OAuth", "SAML", "JWT", "RBAC", "ABAC",
    "MFA", "SSO", "VPC", "CDN", "DNS", "CRUD", "ETL", "ORM", "JSON", "XML",
    "YAML", "gRPC", "GraphQL", "CORS", "SQL", "NoSQL", "RDBMS", "CVE",
    "SAST", "DAST", "SBOM", "MTTR", "RTO", "RPO",
}


# ---------------------------------------------------------------------------
# Persona definitions
# ---------------------------------------------------------------------------

PERSONAS: Dict[str, Dict[str, Any]] = {
    "CEO": {
        "description": "Chief Executive — wants quantified impact up front",
        "grade_min": 9,
        "grade_max": 11,
        "max_jargon_density": 3.0,
        "requires_first_paragraph": "quantified_impact_or_ask",
    },
    "CFO": {
        "description": "Chief Financial Officer — wants numbers and cost terms",
        "grade_min": 10,
        "grade_max": 12,
        "max_jargon_density": 5.0,
        "requires_number": True,
        "requires_any_term": ["ROI", "TCO", "budget", "cost"],
    },
    "Board": {
        "description": "Board of Directors — risk-forward, no unexplained acronyms",
        "grade_min": 10,
        "grade_max": 11,
        "max_jargon_density": 2.0,
        "requires_any_term": ["risk", "compliance", "audit", "posture"],
        "no_unexplained_acronyms": True,
    },
    "Engineer": {
        "description": "Engineer — technical depth welcome",
        "grade_min": 12,
        "grade_max": 15,
        "max_jargon_density": 100.0,
        "allow_code_blocks": True,
    },
    "Auditor": {
        "description": "Auditor — must cite control framework",
        "grade_min": 11,
        "grade_max": 13,
        "max_jargon_density": 6.0,
        "requires_any_term": ["NIST", "FedRAMP", "CMMC", "ISO 27001", "SOC 2"],
    },
    "Lawyer": {
        "description": "Lawyer — defined terms, no ambiguous modals",
        "grade_min": 13,
        "grade_max": 15,
        "max_jargon_density": 8.0,
        "requires_defined_terms": True,
        "no_ambiguous_modals": True,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tokenize_words(text: str) -> List[str]:
    return re.findall(r"\b[\w/]+\b", text)


def _jargon_density(text: str) -> Dict[str, Any]:
    words = _tokenize_words(text)
    if not words:
        return {"density_pct": 0.0, "hits": [], "total_words": 0}
    hits: List[str] = []
    for w in words:
        up = w.upper()
        if up in JARGON_TERMS:
            hits.append(up)
    density = (len(hits) / len(words)) * 100.0
    return {
        "density_pct": round(density, 2),
        "hits": sorted(set(hits)),
        "total_words": len(words),
    }


def _first_paragraph(text: str) -> str:
    parts = re.split(r"\n\s*\n", text.strip(), maxsplit=1)
    return parts[0] if parts else ""


def _has_quantified_impact_or_ask(paragraph: str) -> bool:
    if re.search(r"\b\d+(\.\d+)?\s*(%|percent|M|B|K|million|billion|thousand|USD|\$)", paragraph, re.IGNORECASE):
        return True
    if re.search(r"\$\s*\d", paragraph):
        return True
    if re.search(r"\b(request|ask|approve|fund|authorize|decision)\b", paragraph, re.IGNORECASE):
        return True
    return False


def _contains_number(text: str) -> bool:
    return bool(re.search(r"\b\d+(\.\d+)?\b", text))


def _contains_any_term(text: str, terms: List[str]) -> List[str]:
    found = []
    low = text.lower()
    for t in terms:
        if t.lower() in low:
            found.append(t)
    return found


def _unexplained_acronyms(text: str) -> List[str]:
    """Find ALL-CAPS 2-5 letter tokens never defined as 'ACRONYM (expansion)' or 'expansion (ACRONYM)'."""
    candidates = set(re.findall(r"\b[A-Z]{2,5}\b", text))
    common_english = {"I", "A", "AN", "THE", "AND", "OR", "BUT", "IS", "IT", "TO", "OF", "IN", "ON", "AT", "BY", "AS", "IF", "SO", "NO", "US", "WE"}
    candidates -= common_english
    unexplained = []
    for ac in candidates:
        # Pattern 1: ACRONYM (expansion)
        pat1 = re.compile(rf"\b{re.escape(ac)}\s*\([^)]+\)")
        # Pattern 2: expansion (ACRONYM)
        pat2 = re.compile(rf"\([^)]*{re.escape(ac)}[^)]*\)")
        if not (pat1.search(text) or pat2.search(text)):
            unexplained.append(ac)
    return sorted(unexplained)


def _ambiguous_modals(text: str) -> int:
    """Count modal verbs that are often ambiguous in legal contexts."""
    return len(re.findall(r"\b(may|might|should|could)\b", text, re.IGNORECASE))


def _has_defined_terms(text: str) -> bool:
    """Heuristic: presence of 'Defined Terms', 'means', or quoted Capitalized Terms."""
    if re.search(r"defined terms?|definitions?:", text, re.IGNORECASE):
        return True
    if re.search(r"\"[A-Z][A-Za-z ]+\"\s+means\b", text):
        return True
    if re.search(r"\b[A-Z][a-zA-Z]+\s+means\b", text):
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def available_personas() -> List[Dict[str, Any]]:
    """Return list of available persona configs."""
    out = []
    for name, cfg in PERSONAS.items():
        out.append({
            "name": name,
            "description": cfg.get("description", ""),
            "grade_min": cfg.get("grade_min"),
            "grade_max": cfg.get("grade_max"),
            "max_jargon_density": cfg.get("max_jargon_density"),
        })
    return out


def score_for_audience(text: str, persona: str) -> Dict[str, Any]:
    """Score text against a target audience persona.

    Returns: {match_score 0-100, issues (list), suggestions (list), metrics (dict)}
    """
    if not text or not text.strip():
        return {
            "match_score": 0,
            "persona": persona,
            "issues": ["empty text"],
            "suggestions": ["provide non-empty input"],
            "metrics": {},
        }

    key = None
    for p in PERSONAS:
        if p.lower() == persona.lower():
            key = p
            break
    if key is None:
        return {
            "match_score": 0,
            "persona": persona,
            "issues": [f"unknown persona: {persona}"],
            "suggestions": [f"use one of: {', '.join(PERSONAS.keys())}"],
            "metrics": {},
        }

    cfg = PERSONAS[key]
    issues: List[str] = []
    suggestions: List[str] = []

    readability = _readability_check(text)
    grade = float(readability.get("composite_grade", 0.0))
    jargon = _jargon_density(text)

    metrics: Dict[str, Any] = {
        "grade_level": grade,
        "flesch_kincaid": readability.get("flesch_kincaid"),
        "jargon_density_pct": jargon["density_pct"],
        "jargon_hits": jargon["hits"],
        "total_words": jargon["total_words"],
    }

    score = 100.0

    # Grade bracket check
    gmin, gmax = cfg["grade_min"], cfg["grade_max"]
    if grade < gmin:
        issues.append(f"grade {grade} below target range {gmin}-{gmax}")
        suggestions.append(f"add complexity; target grade {gmin}-{gmax}")
        score -= min(25.0, (gmin - grade) * 5.0)
    elif grade > gmax:
        issues.append(f"grade {grade} above target range {gmin}-{gmax}")
        suggestions.append(f"simplify sentences; target grade {gmin}-{gmax}")
        score -= min(25.0, (grade - gmax) * 5.0)

    # Jargon density
    max_j = cfg["max_jargon_density"]
    if jargon["density_pct"] > max_j:
        issues.append(f"jargon density {jargon['density_pct']}% exceeds {max_j}% limit")
        suggestions.append("replace acronyms with plain-language equivalents")
        score -= min(20.0, (jargon["density_pct"] - max_j) * 3.0)

    # First paragraph requirement
    if cfg.get("requires_first_paragraph") == "quantified_impact_or_ask":
        fp = _first_paragraph(text)
        ok = _has_quantified_impact_or_ask(fp)
        metrics["first_paragraph_has_quantified_impact_or_ask"] = ok
        if not ok:
            issues.append("first paragraph lacks quantified impact or explicit ask")
            suggestions.append("open with the number or the decision you need")
            score -= 15.0

    # Numbers
    if cfg.get("requires_number"):
        ok = _contains_number(text)
        metrics["contains_number"] = ok
        if not ok:
            issues.append("no numeric value present")
            suggestions.append("add quantified metrics (dollars, percentages, counts)")
            score -= 15.0

    # Required any-term
    req_terms = cfg.get("requires_any_term")
    if req_terms:
        found = _contains_any_term(text, req_terms)
        metrics["required_terms_found"] = found
        if not found:
            issues.append(f"missing required term (one of: {', '.join(req_terms)})")
            suggestions.append(f"reference at least one of: {', '.join(req_terms)}")
            score -= 15.0

    # Unexplained acronyms (Board)
    if cfg.get("no_unexplained_acronyms"):
        unexplained = _unexplained_acronyms(text)
        # Filter common jargon that isn't defined
        metrics["unexplained_acronyms"] = unexplained
        if unexplained:
            issues.append(f"unexplained acronyms: {', '.join(unexplained[:5])}")
            suggestions.append("expand acronyms on first use, e.g., 'ATO (Authority to Operate)'")
            score -= min(15.0, len(unexplained) * 3.0)

    # Defined terms (Lawyer)
    if cfg.get("requires_defined_terms"):
        ok = _has_defined_terms(text)
        metrics["has_defined_terms"] = ok
        if not ok:
            issues.append("no defined-terms section or 'X means Y' constructions found")
            suggestions.append("include a Defined Terms section or use 'Term means ...' constructions")
            score -= 15.0

    # Ambiguous modals (Lawyer)
    if cfg.get("no_ambiguous_modals"):
        n = _ambiguous_modals(text)
        metrics["ambiguous_modal_count"] = n
        if n > 0:
            issues.append(f"{n} ambiguous modal verbs (may/might/should/could) without definition")
            suggestions.append("replace with 'shall', 'will', or 'must' where obligation is intended")
            score -= min(15.0, n * 2.0)

    match_score = max(0, int(round(score)))
    return {
        "match_score": match_score,
        "persona": key,
        "issues": issues,
        "suggestions": suggestions,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="WriteGuard WG 13 — Audience persona scorer")
    parser.add_argument("--text", help="Inline text to score")
    parser.add_argument("--file", help="Path to text file")
    parser.add_argument("--persona", help="Target persona name")
    parser.add_argument("--list-personas", action="store_true", help="List available personas")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    if args.list_personas:
        personas = available_personas()
        if args.json:
            print(json.dumps(personas, indent=2))
        else:
            for p in personas:
                print(f"  {p['name']:<10} grade {p['grade_min']}-{p['grade_max']}  jargon<={p['max_jargon_density']}%  {p['description']}")
        return

    if not args.persona:
        parser.error("--persona is required (or use --list-personas)")

    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        text = sys.stdin.read()

    result = score_for_audience(text, args.persona)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Persona: {result['persona']}")
        print(f"Match score: {result['match_score']}/100")
        print(f"Metrics: grade={result['metrics'].get('grade_level')}  jargon={result['metrics'].get('jargon_density_pct')}%")
        if result["issues"]:
            print("Issues:")
            for i in result["issues"]:
                print(f"  - {i}")
        if result["suggestions"]:
            print("Suggestions:")
            for s in result["suggestions"]:
                print(f"  - {s}")


if __name__ == "__main__":
    main()
