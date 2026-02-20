# CUI // SP-CTI
"""PRD Validator — automated quality checks adapted from BMAD Method.

Runs 6 validation passes on intake requirements:
  1. Density      — detect filler words, wordy phrases, redundancy
  2. Leakage      — detect implementation details in requirements (WHAT vs HOW)
  3. SMART        — score each requirement: Specific, Measurable, Attainable, Relevant, Traceable
  4. Traceability — verify requirements link to decomposition / acceptance criteria
  5. Measurability — check acceptance criteria for quantifiable targets
  6. Completeness — check coverage of requirement types

Usage:
    python tools/requirements/prd_validator.py --session-id <id> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Pattern catalogs
# ---------------------------------------------------------------------------

# Density: filler phrases that add no value
FILLER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bthe system (?:shall|will|should) (?:allow|enable|permit) (?:the )?users? to\b", re.I),
     "Users can"),
    (re.compile(r"\bthe system (?:shall|will|should) provide (?:the )?(?:ability|capability) (?:to|for)\b", re.I),
     "Users can / System supports"),
    (re.compile(r"\bit is (?:important|necessary|essential|critical) (?:that|to)\b", re.I),
     "(state the requirement directly)"),
    (re.compile(r"\bin order to\b", re.I), "to"),
    (re.compile(r"\bdue to the fact that\b", re.I), "because"),
    (re.compile(r"\bat this point in time\b", re.I), "now"),
    (re.compile(r"\bin the event that\b", re.I), "if"),
    (re.compile(r"\bfor the purpose of\b", re.I), "to / for"),
    (re.compile(r"\bhas the ability to\b", re.I), "can"),
    (re.compile(r"\bwith respect to\b", re.I), "about / regarding"),
    (re.compile(r"\bon a regular basis\b", re.I), "regularly"),
    (re.compile(r"\ba large number of\b", re.I), "(use a specific number)"),
    (re.compile(r"\bas (?:quickly|soon) as possible\b", re.I),
     "(specify a measurable target)"),
]

# Density: redundant phrases
REDUNDANT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bfuture plans?\b", re.I), "plans"),
    (re.compile(r"\bend results?\b", re.I), "results"),
    (re.compile(r"\bpast (?:history|experience)\b", re.I), "history / experience"),
    (re.compile(r"\bcurrent(?:ly)? existing\b", re.I), "current / existing"),
    (re.compile(r"\beach and every\b", re.I), "each / every"),
    (re.compile(r"\bfirst and foremost\b", re.I), "first"),
    (re.compile(r"\bbasic(?:ally)? fundamentals?\b", re.I), "fundamentals"),
    (re.compile(r"\babsolutely essential\b", re.I), "essential"),
]

# Leakage: technology names that indicate implementation details in requirements
_TECH_PATTERNS: dict[str, list[str]] = {
    "frontend_framework": [
        "React", "Vue", "Angular", "Svelte", "Next\\.js", "Nuxt",
        "Gatsby", "Remix", "SolidJS", "Ember",
    ],
    "backend_framework": [
        "Express", "Django", "Flask", "FastAPI", "Rails",
        "Spring Boot", "Spring", "NestJS", "Laravel", "ASP\\.NET",
        "Gin", "Actix", "Rocket",
    ],
    "database": [
        "PostgreSQL", "Postgres", "MongoDB", "MySQL", "MariaDB",
        "Redis", "DynamoDB", "Cassandra", "CouchDB", "SQLite",
        "Oracle DB", "SQL Server", "ElasticSearch", "Elasticsearch",
    ],
    "cloud_platform": [
        "AWS", "Amazon Web Services", "GCP", "Google Cloud",
        "Azure", "S3 bucket", "EC2", "Lambda", "CloudFront",
        "ECS", "EKS", "RDS", "SQS", "SNS", "API Gateway",
        "Cloud Run", "Cloud Functions", "App Engine",
    ],
    "infrastructure": [
        "Docker", "Kubernetes", "K8s", "Terraform", "Ansible",
        "Jenkins", "GitHub Actions", "GitLab CI", "CircleCI",
        "Helm", "Istio", "Nginx", "Apache",
    ],
    "library": [
        "Redux", "MobX", "Zustand", "Axios", "Lodash",
        "jQuery", "Bootstrap", "Tailwind", "Material UI",
        "Pandas", "NumPy", "TensorFlow", "PyTorch",
    ],
}

LEAKAGE_PATTERNS: list[tuple[re.Pattern, str]] = []
for category, names in _TECH_PATTERNS.items():
    for name in names:
        LEAKAGE_PATTERNS.append((
            re.compile(r"\b" + name + r"\b", re.I if len(name) > 3 else 0),
            category,
        ))

# Vague quantifiers (measurability anti-patterns)
VAGUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bshould be (?:fast|quick|responsive|efficient|secure|reliable|scalable|robust)\b", re.I),
    re.compile(r"\buser[- ]?friendly\b", re.I),
    re.compile(r"\beasy to (?:use|learn|maintain)\b", re.I),
    re.compile(r"\bhigh(?:ly)? (?:available|performant|secure)\b", re.I),
    re.compile(r"\bas needed\b", re.I),
    re.compile(r"\bwhere (?:appropriate|applicable)\b", re.I),
    re.compile(r"\bminimal (?:downtime|latency|delay)\b", re.I),
    re.compile(r"\breasonable (?:time|response|performance)\b", re.I),
    re.compile(r"\betc\.?\b", re.I),
    re.compile(r"\band so on\b", re.I),
    re.compile(r"\bvarious\b", re.I),
    re.compile(r"\bnumerous\b", re.I),
]

# Measurable indicator patterns (positive signals)
MEASURABLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b\d+\s*(?:ms|seconds?|minutes?|hours?|days?)\b", re.I),
    re.compile(r"\b\d+(?:\.\d+)?\s*%", re.I),
    re.compile(r"\b(?:less|fewer|more|greater|under|over|within|at (?:least|most))\s+\d", re.I),
    re.compile(r"\b\d+\s*(?:users?|requests?|transactions?|records?|items?)\b", re.I),
    re.compile(r"\b(?:99\.9|99\.99|99\.999)\s*%", re.I),
    re.compile(r"\bSLA\b", re.I),
    re.compile(r"\bp\d{2}\b", re.I),  # p95, p99
]

# Specific actor patterns (positive signal for SMART-Specific)
ACTOR_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:user|admin|administrator|operator|analyst|manager|developer|customer|"
               r"system|service|agent|viewer|editor|approver|reviewer|auditor)\b", re.I),
    re.compile(r"\bas a\b", re.I),  # user story format
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Validation passes
# ---------------------------------------------------------------------------

def _validate_density(reqs: list[dict]) -> dict:
    """Check for filler words, wordy phrases, and redundancy."""
    findings: list[dict] = []

    for req in reqs:
        text = req.get("refined_text") or req.get("raw_text", "")
        rid = req.get("id", "?")

        for pattern, suggestion in FILLER_PATTERNS:
            matches = pattern.findall(text)
            for m in matches:
                findings.append({
                    "requirement_id": rid,
                    "category": "filler",
                    "matched": m if isinstance(m, str) else m[0],
                    "suggestion": suggestion,
                })

        for pattern, suggestion in REDUNDANT_PATTERNS:
            matches = pattern.findall(text)
            for m in matches:
                findings.append({
                    "requirement_id": rid,
                    "category": "redundant",
                    "matched": m if isinstance(m, str) else m[0],
                    "suggestion": suggestion,
                })

    count = len(findings)
    if count > 10:
        severity = "critical"
    elif count > 5:
        severity = "warning"
    else:
        severity = "pass"

    return {
        "check": "density",
        "description": "Information density — filler words, wordy/redundant phrases",
        "severity": severity,
        "finding_count": count,
        "findings": findings,
    }


def _validate_leakage(reqs: list[dict]) -> dict:
    """Detect implementation/technology details leaking into requirements."""
    findings: list[dict] = []

    for req in reqs:
        text = req.get("refined_text") or req.get("raw_text", "")
        rid = req.get("id", "?")

        for pattern, category in LEAKAGE_PATTERNS:
            matches = pattern.findall(text)
            for m in matches:
                findings.append({
                    "requirement_id": rid,
                    "category": category,
                    "matched": m,
                    "suggestion": "Requirements should describe WHAT, not HOW. "
                                  "Move technology choices to architecture decisions.",
                })

    count = len(findings)
    if count > 5:
        severity = "critical"
    elif count > 2:
        severity = "warning"
    else:
        severity = "pass"

    return {
        "check": "implementation_leakage",
        "description": "Implementation leakage — technology names in requirements (WHAT vs HOW)",
        "severity": severity,
        "finding_count": count,
        "findings": findings,
    }


def _score_smart(req: dict) -> dict:
    """Score a single requirement on SMART dimensions (1-5 each)."""
    text = req.get("refined_text") or req.get("raw_text", "")
    ac = req.get("acceptance_criteria", "")
    rid = req.get("id", "?")

    # --- Specific (1-5): Does it identify an actor and a clear capability?
    has_actor = any(p.search(text) for p in ACTOR_PATTERNS)
    word_count = len(text.split())
    specific = 1
    if has_actor:
        specific += 2
    if 10 <= word_count <= 80:
        specific += 1
    if req.get("requirement_type") and req["requirement_type"] != "functional":
        specific += 1  # typed requirements are more specific
    specific = min(specific, 5)

    # --- Measurable (1-5): Does it have quantifiable criteria?
    has_measurable = any(p.search(text) for p in MEASURABLE_PATTERNS)
    has_vague = any(p.search(text) for p in VAGUE_PATTERNS)
    ac_measurable = any(p.search(ac) for p in MEASURABLE_PATTERNS) if ac else False
    measurable = 1
    if has_measurable:
        measurable += 2
    if ac_measurable:
        measurable += 1
    if not has_vague:
        measurable += 1
    if has_vague and not has_measurable:
        measurable = max(measurable - 1, 1)
    measurable = min(measurable, 5)

    # --- Attainable (1-5): Is it feasible? (heuristic: reasonable length, no impossibles)
    attainable = 3  # baseline
    if word_count > 150:
        attainable -= 1  # overly complex
    if req.get("feasibility_score") and req["feasibility_score"] > 0:
        attainable = max(2, min(5, int(req["feasibility_score"] * 5)))
    attainable = min(max(attainable, 1), 5)

    # --- Relevant (1-5): Does it trace to a need?
    relevant = 2  # baseline (it was captured during intake)
    if req.get("priority") in ("critical", "high"):
        relevant += 1
    if req.get("source_document"):
        relevant += 1  # sourced from an official document
    if req.get("source_turn") and req["source_turn"] <= 3:
        relevant += 1  # mentioned early = core need
    relevant = min(relevant, 5)

    # --- Traceable (1-5): Does it have acceptance criteria and ID?
    traceable = 1
    if req.get("id"):
        traceable += 1
    if ac:
        traceable += 2
    if req.get("source_turn"):
        traceable += 1
    traceable = min(traceable, 5)

    total = specific + measurable + attainable + relevant + traceable
    return {
        "requirement_id": rid,
        "specific": specific,
        "measurable": measurable,
        "attainable": attainable,
        "relevant": relevant,
        "traceable": traceable,
        "total": total,
        "max_possible": 25,
        "pct": round(total / 25 * 100),
    }


def _validate_smart(reqs: list[dict]) -> dict:
    """Score all requirements on SMART dimensions."""
    scores = [_score_smart(r) for r in reqs]
    avg_pct = sum(s["pct"] for s in scores) / len(scores) if scores else 0

    if avg_pct >= 70:
        severity = "pass"
    elif avg_pct >= 50:
        severity = "warning"
    else:
        severity = "critical"

    # Find weakest dimensions across all requirements
    dim_totals = {"specific": 0, "measurable": 0, "attainable": 0, "relevant": 0, "traceable": 0}
    for s in scores:
        for dim in dim_totals:
            dim_totals[dim] += s[dim]
    n = len(scores) or 1
    dim_avgs = {d: round(t / n, 1) for d, t in dim_totals.items()}
    weakest = min(dim_avgs, key=dim_avgs.get) if dim_avgs else None

    return {
        "check": "smart",
        "description": "SMART scoring — Specific, Measurable, Attainable, Relevant, Traceable (1-5 each)",
        "severity": severity,
        "average_pct": round(avg_pct),
        "dimension_averages": dim_avgs,
        "weakest_dimension": weakest,
        "scores": scores,
    }


def _validate_traceability(reqs: list[dict], decomp: list[dict]) -> dict:
    """Check if requirements trace to decomposition items."""
    req_ids = {r.get("id") for r in reqs}
    traced_ids: set[str] = set()

    for d in decomp:
        src = d.get("source_requirement_ids", "")
        if src:
            try:
                ids = json.loads(src) if src.startswith("[") else [src]
                traced_ids.update(ids)
            except (json.JSONDecodeError, TypeError):
                pass

    orphans = [rid for rid in req_ids if rid and rid not in traced_ids]
    coverage_pct = round((1 - len(orphans) / max(len(req_ids), 1)) * 100)

    if coverage_pct >= 80:
        severity = "pass"
    elif coverage_pct >= 50:
        severity = "warning"
    else:
        severity = "critical"

    return {
        "check": "traceability",
        "description": "Traceability — requirements linked to decomposition (epics/stories)",
        "severity": severity,
        "total_requirements": len(req_ids),
        "traced_requirements": len(req_ids) - len(orphans),
        "orphan_requirements": orphans,
        "coverage_pct": coverage_pct,
    }


def _validate_measurability(reqs: list[dict]) -> dict:
    """Check acceptance criteria for quantifiable targets vs vague language."""
    findings: list[dict] = []

    for req in reqs:
        ac = req.get("acceptance_criteria", "")
        text = req.get("refined_text") or req.get("raw_text", "")
        rid = req.get("id", "?")

        # Check for vague language in requirement text
        vague_matches = []
        for p in VAGUE_PATTERNS:
            m = p.search(text)
            if m:
                vague_matches.append(m.group())

        # Check if acceptance criteria have measurable targets
        has_measurable_ac = any(p.search(ac) for p in MEASURABLE_PATTERNS) if ac else False
        has_ac = bool(ac and ac.strip())

        if vague_matches and not has_measurable_ac:
            findings.append({
                "requirement_id": rid,
                "issue": "vague_without_measure",
                "vague_phrases": vague_matches,
                "suggestion": "Replace vague language with specific measurable targets "
                              "(e.g., 'fast' → '< 200ms p95 latency')",
            })
        elif not has_ac:
            findings.append({
                "requirement_id": rid,
                "issue": "missing_acceptance_criteria",
                "suggestion": "Add BDD acceptance criteria with measurable Given/When/Then",
            })

    count = len(findings)
    measurable_pct = round((1 - count / max(len(reqs), 1)) * 100)

    if measurable_pct >= 80:
        severity = "pass"
    elif measurable_pct >= 60:
        severity = "warning"
    else:
        severity = "critical"

    return {
        "check": "measurability",
        "description": "Measurability — quantifiable acceptance criteria, no vague language",
        "severity": severity,
        "measurable_pct": measurable_pct,
        "finding_count": count,
        "findings": findings,
    }


def _validate_completeness(reqs: list[dict], session: dict) -> dict:
    """Check coverage of requirement types and overall completeness."""
    type_counts: dict[str, int] = {}
    for r in reqs:
        t = r.get("requirement_type", "functional")
        type_counts[t] = type_counts.get(t, 0) + 1

    expected_types = {"functional", "security", "performance", "interface", "data"}
    present_types = set(type_counts.keys())
    missing_types = expected_types - present_types

    # Check for minimum requirement counts
    issues: list[str] = []
    if len(reqs) < 3:
        issues.append(f"Only {len(reqs)} requirements captured — minimum 5 recommended")
    if "security" not in present_types:
        issues.append("No security requirements — critical for Gov/DoD")
    if "performance" not in present_types:
        issues.append("No performance requirements — add SLA/latency/throughput targets")

    impact = session.get("impact_level", "IL5")
    if impact in ("IL4", "IL5", "IL6") and type_counts.get("security", 0) < 3:
        issues.append(f"Only {type_counts.get('security', 0)} security requirements for {impact} — "
                      "minimum 3 recommended for CUI/SECRET systems")

    coverage_pct = round(len(present_types & expected_types) / len(expected_types) * 100)
    if coverage_pct >= 80 and not issues:
        severity = "pass"
    elif coverage_pct >= 60:
        severity = "warning"
    else:
        severity = "critical"

    return {
        "check": "completeness",
        "description": "Completeness — requirement type coverage and minimum counts",
        "severity": severity,
        "type_counts": type_counts,
        "missing_types": list(missing_types),
        "coverage_pct": coverage_pct,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def validate_prd(session_id: str, db_path=None) -> dict:
    """Run all 6 validation passes on an intake session's requirements.

    Returns ``{"status": "ok", "session_id": ..., "overall": ..., "checks": [...]}``.
    """
    conn = _get_connection(db_path)
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"status": "error", "error": f"Session '{session_id}' not found."}
        session = dict(session)

        reqs = [dict(r) for r in conn.execute(
            "SELECT * FROM intake_requirements WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()]

        decomp = [dict(r) for r in conn.execute(
            "SELECT * FROM safe_decomposition WHERE session_id = ?", (session_id,),
        ).fetchall()]
    finally:
        conn.close()

    if not reqs:
        return {
            "status": "ok",
            "session_id": session_id,
            "overall": "critical",
            "overall_score": 0,
            "summary": "No requirements found — nothing to validate.",
            "checks": [],
        }

    # Run all 6 checks
    checks = [
        _validate_density(reqs),
        _validate_leakage(reqs),
        _validate_smart(reqs),
        _validate_traceability(reqs, decomp),
        _validate_measurability(reqs),
        _validate_completeness(reqs, session),
    ]

    # Roll up overall severity
    severities = [c["severity"] for c in checks]
    if "critical" in severities:
        overall = "critical"
    elif "warning" in severities:
        overall = "warning"
    else:
        overall = "pass"

    # Compute overall score (0-100)
    check_scores = {
        "density": 100 - min(checks[0]["finding_count"] * 5, 100),
        "implementation_leakage": 100 - min(checks[1]["finding_count"] * 10, 100),
        "smart": checks[2].get("average_pct", 0),
        "traceability": checks[3].get("coverage_pct", 0),
        "measurability": checks[4].get("measurable_pct", 0),
        "completeness": checks[5].get("coverage_pct", 0),
    }
    overall_score = round(sum(check_scores.values()) / len(check_scores))

    # Summary
    critical_count = severities.count("critical")
    warning_count = severities.count("warning")
    pass_count = severities.count("pass")
    summary = (f"{pass_count} passed, {warning_count} warnings, {critical_count} critical "
               f"across 6 checks. Overall quality score: {overall_score}%.")

    return {
        "status": "ok",
        "session_id": session_id,
        "overall": overall,
        "overall_score": overall_score,
        "check_scores": check_scores,
        "summary": summary,
        "checks": checks,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Validate PRD quality (BMAD-adapted)")
    parser.add_argument("--session-id", required=True, help="Intake session ID")
    parser.add_argument("--json", action="store_true", help="Full JSON output")
    args = parser.parse_args()

    result = validate_prd(args.session_id)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nPRD Validation Report — {result['session_id']}")
        print(f"Overall: {result['overall'].upper()} ({result.get('overall_score', 0)}%)")
        print(f"Summary: {result.get('summary', '')}\n")
        for check in result.get("checks", []):
            sev = check["severity"].upper()
            icon = {"PASS": "+", "WARNING": "!", "CRITICAL": "X"}.get(sev, "?")
            print(f"  [{icon}] {check['check']}: {sev} — {check['description']}")
            fc = check.get("finding_count")
            if fc is not None:
                print(f"      Findings: {fc}")
        print()


if __name__ == "__main__":
    main()
