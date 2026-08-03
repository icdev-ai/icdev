# CUI // SP-CTI
# ICDEV™ GovCon Gap Analyzer — Phase 59 (D363)
# Identifies unmet requirements and generates enhancement recommendations.

"""
Gap Analyzer — find requirement patterns where ICDEV™ coverage is insufficient.

Reads from:
    - icdev_capability_map (coverage scores)
    - rfp_requirement_patterns (pattern frequency, domain)
    - rfp_shall_statements (individual statements)

Outputs:
    - Prioritized gap list (frequency × gap severity)
    - Enhancement recommendations per gap
    - Domain-level gap heatmap
    - Innovation Engine cross-registration for high-priority gaps

Usage:
    python tools/govcon/gap_analyzer.py --analyze --json
    python tools/govcon/gap_analyzer.py --recommendations --json
    python tools/govcon/gap_analyzer.py --heatmap --json
    python tools/govcon/gap_analyzer.py --register-innovation --json
"""

import argparse
import hashlib
import json
import os
import sys
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.gap_analyzer")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── helpers ───────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection(db_path=str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action, details="", actor="gap_analyzer"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (_now(), "govcon.gap_analysis", actor, action, details, "govcon"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


# ── gap analysis ──────────────────────────────────────────────────────


def analyze_gaps():
    """Identify and prioritize all coverage gaps.

    A gap is a requirement pattern where:
    - No capability maps at all (coverage = 0)
    - Best capability coverage < 0.40 (grade N)

    Priority = frequency × (1 - best_coverage)
    Higher frequency + lower coverage = higher priority.
    """
    conn = _get_db()

    # Wrap the multi-table JOIN in a CTE so RLS sees only one classification column
    rows = conn.execute("""
        WITH pattern_coverage AS (
            SELECT p.id, p.pattern_name, p.description, p.domain_category,
                   p.frequency, p.representative_text, p.keyword_fingerprint,
                   p.status, p.classification,
                   COALESCE(MAX(m.coverage_score), 0.0) AS best_coverage,
                   COUNT(m.id) AS capability_count
            FROM rfp_requirement_patterns p
            LEFT JOIN icdev_capability_map m ON p.id = m.pattern_id
            GROUP BY p.id, p.pattern_name, p.description, p.domain_category,
                     p.frequency, p.representative_text, p.keyword_fingerprint,
                     p.status, p.classification
        )
        SELECT * FROM pattern_coverage WHERE TRUE ORDER BY frequency DESC
    """).fetchall()

    gaps = []
    partial = []
    compliant = []

    for row in rows:
        best = row["best_coverage"]
        item = {
            "pattern_id": row["id"],
            "pattern_name": row["pattern_name"],
            "description": row["description"],
            "domain": row["domain_category"],
            "frequency": row["frequency"],
            "representative_text": (row["representative_text"] or "")[:300],
            "best_coverage": best,
            "capability_count": row["capability_count"],
            "status": row["status"],
        }

        if best < 0.40:
            item["grade"] = "N"
            item["severity"] = "high"
            item["priority"] = round(row["frequency"] * (1.0 - best), 2)
            gaps.append(item)
        elif best < 0.80:
            item["grade"] = "M"
            item["severity"] = "medium"
            item["priority"] = round(row["frequency"] * (1.0 - best) * 0.5, 2)
            partial.append(item)
        else:
            item["grade"] = "L"
            item["severity"] = "low"
            item["priority"] = 0
            compliant.append(item)

    # Sort gaps by priority
    gaps.sort(key=lambda x: x["priority"], reverse=True)
    partial.sort(key=lambda x: x["priority"], reverse=True)

    _audit(conn, "analyze_gaps", f"Gaps: {len(gaps)}, Partial: {len(partial)}, Compliant: {len(compliant)}")
    conn.close()

    return {
        "status": "ok",
        "summary": {
            "total_patterns": len(gaps) + len(partial) + len(compliant),
            "N_gaps": len(gaps),
            "M_partial": len(partial),
            "L_compliant": len(compliant),
            "gap_rate": round(len(gaps) / max(len(gaps) + len(partial) + len(compliant), 1), 4),
        },
        "gaps": gaps,
        "partial": partial[:20],
        "compliant_count": len(compliant),
    }


# ── recommendations ───────────────────────────────────────────────────

_ENHANCEMENT_TEMPLATES = {
    "devsecops": {
        "approach": "Extend DevSecOps pipeline with new scanning stage or tool integration",
        "effort": "M",
        "typical_tools": ["pipeline_security_generator.py", "policy_generator.py"],
        "compliance_benefit": "SA-11, SA-15, SI-7 coverage improvement",
    },
    "ai_ml": {
        "approach": "Add new AI governance assessor or extend existing AI RMF coverage",
        "effort": "L",
        "typical_tools": ["nist_ai_rmf_assessor.py", "model_card_generator.py"],
        "compliance_benefit": "PM-32, SA-15(12) coverage improvement",
    },
    "ato_rmf": {
        "approach": "Extend ATO automation with new artifact generator or assessment",
        "effort": "M",
        "typical_tools": ["ssp_generator.py", "cato_monitor.py", "oscal_tools.py"],
        "compliance_benefit": "CA-2, CA-7, PL-2 coverage improvement",
    },
    "cloud": {
        "approach": "Add new CSP support or extend cloud migration tooling",
        "effort": "L",
        "typical_tools": ["terraform_generator.py", "k8s_generator.py"],
        "compliance_benefit": "CM-2, SC-7 coverage improvement",
    },
    "security": {
        "approach": "Add new security scanner or extend OWASP/ATLAS coverage",
        "effort": "M",
        "typical_tools": ["sast_runner.py", "container_scanner.py"],
        "compliance_benefit": "RA-5, SI-2, SI-7 coverage improvement",
    },
    "compliance": {
        "approach": "Add new compliance framework via BaseAssessor pattern or extend crosswalk",
        "effort": "S",
        "typical_tools": ["crosswalk_engine.py", "multi_regime_assessor.py"],
        "compliance_benefit": "PL-2, CA-2 coverage improvement",
    },
    "agile": {
        "approach": "Extend RICOAS intake or SAFe decomposition capabilities",
        "effort": "M",
        "typical_tools": ["intake_engine.py", "decomposition_engine.py"],
        "compliance_benefit": "PL-2, SA-3 coverage improvement",
    },
    "data": {
        "approach": "Add new data analytics or reporting capability",
        "effort": "M",
        "typical_tools": ["nlq_engine (dashboard)"],
        "compliance_benefit": "AU-6, AU-7 coverage improvement",
    },
    "management": {
        "approach": "Extend project management, simulation, or reporting",
        "effort": "M",
        "typical_tools": ["simulation_engine.py", "coa_generator.py"],
        "compliance_benefit": "PL-2, PM-4 coverage improvement",
    },
}


def generate_recommendations():
    """Generate enhancement recommendations for each gap.

    For each gap pattern:
    1. Determine domain
    2. Match to enhancement template
    3. Extract specific keywords that are unmet
    4. Generate actionable recommendation
    """
    analysis = analyze_gaps()
    gaps = analysis.get("gaps", [])

    recommendations = []
    for gap in gaps:
        domain = gap.get("domain", "")
        template = _ENHANCEMENT_TEMPLATES.get(domain, _ENHANCEMENT_TEMPLATES.get("management", {}))

        rec = {
            "pattern_id": gap["pattern_id"],
            "pattern_name": gap["pattern_name"],
            "domain": domain,
            "frequency": gap["frequency"],
            "priority": gap["priority"],
            "current_coverage": gap["best_coverage"],
            "recommendation": {
                "approach": template.get("approach", "Custom enhancement needed"),
                "effort_estimate": template.get("effort", "M"),
                "existing_tools": template.get("typical_tools", []),
                "compliance_benefit": template.get("compliance_benefit", ""),
                "action": _generate_action(gap, template),
            },
        }
        recommendations.append(rec)

    return {
        "status": "ok",
        "total_recommendations": len(recommendations),
        "recommendations": recommendations,
    }


def _generate_action(gap, template):
    """Generate specific action text for a gap."""
    name = gap.get("pattern_name", "Unknown requirement")
    domain = gap.get("domain", "unknown")
    freq = gap.get("frequency", 0)
    coverage = gap.get("best_coverage", 0)

    if coverage == 0:
        return (
            f"NEW CAPABILITY NEEDED: '{name}' appears in {freq} RFPs with zero ICDEV™ coverage. "
            f"Create new tool in tools/ targeting {domain} domain. "
            f"Follow BaseAssessor pattern (D116) if compliance-related."
        )
    else:
        return (
            f"ENHANCEMENT NEEDED: '{name}' has {coverage:.0%} coverage across {freq} RFPs. "
            f"Extend existing {domain} tools to improve keyword coverage. "
            f"Target: {template.get('typical_tools', ['unknown'])[0]} or similar."
        )


# ── heatmap ───────────────────────────────────────────────────────────


def get_heatmap():
    """Domain × Grade heatmap for visualization."""
    conn = _get_db()

    # Wrap JOIN in a CTE so RLS sees only one classification column (avoids ambiguity).
    # p.classification is explicitly selected so the RLS predicate resolves unambiguously.
    rows = conn.execute("""
        WITH domain_coverage AS (
            SELECT
                p.domain_category,
                p.id,
                p.frequency,
                p.classification,
                COALESCE(MAX(m.coverage_score), 0) AS best_coverage
            FROM rfp_requirement_patterns p
            LEFT JOIN icdev_capability_map m ON p.id = m.pattern_id
            GROUP BY p.domain_category, p.id, p.frequency, p.classification
        )
        SELECT
            domain_category,
            CASE
                WHEN best_coverage >= 0.80 THEN 'L'
                WHEN best_coverage >= 0.40 THEN 'M'
                ELSE 'N'
            END AS grade,
            COUNT(DISTINCT id) AS pattern_count,
            SUM(frequency) AS total_frequency
        FROM domain_coverage
        WHERE TRUE
        GROUP BY domain_category, grade
        ORDER BY domain_category
    """).fetchall()

    conn.close()

    heatmap = {}
    for row in rows:
        domain = row["domain_category"] or "uncategorized"
        if domain not in heatmap:
            heatmap[domain] = {"L": 0, "M": 0, "N": 0, "total_frequency": 0}
        grade = row["grade"]
        heatmap[domain][grade] = row["pattern_count"]
        heatmap[domain]["total_frequency"] += row["total_frequency"]

    # Calculate health score per domain
    for domain, data in heatmap.items():
        total = data["L"] + data["M"] + data["N"]
        if total > 0:
            data["health_score"] = round((data["L"] * 1.0 + data["M"] * 0.5 + data["N"] * 0.0) / total, 2)
        else:
            data["health_score"] = 0

    return {"status": "ok", "heatmap": heatmap}


# ── innovation cross-registration ─────────────────────────────────────


def register_gaps_as_innovation_signals():
    """Register high-priority gaps as innovation signals for self-improvement.

    Only registers gaps with priority >= 3.0 (high frequency + low coverage).
    """
    analysis = analyze_gaps()
    gaps = analysis.get("gaps", [])

    conn = _get_db()
    registered = 0

    for gap in gaps:
        if gap["priority"] < 3.0:
            continue

        # Check if already registered. `source_url` is not a column on
        # innovation_signals — the live column is `url` (swp-scan-01), so this
        # lookup used to raise UndefinedColumn and the whole gap was skipped by
        # the except-handler below on the very next statement.
        existing = conn.execute(
            "SELECT id FROM innovation_signals WHERE source_type = 'govcon_gap' AND url = %s",
            (gap["pattern_id"],),
        ).fetchone()

        if existing:
            continue

        try:
            # The column list named 13 columns and the value tuple carried 12:
            # `id` had no value, so every subsequent value was shifted one
            # column left. `source` and `discovered_at` are NOT NULL and were
            # never supplied at all. Both are fixed here alongside the rename.
            signal_id = hashlib.sha256(
                f"govcon_gap:{gap['pattern_id']}".encode()
            ).hexdigest()[:20]
            conn.execute(
                "INSERT INTO innovation_signals "
                "(id, source, source_type, url, title, description, category, "
                "raw_score, composite_score, keywords, content_hash, status, "
                "discovered_at, created_at, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    signal_id,
                    "gap_analyzer",
                    "govcon_gap",
                    gap["pattern_id"],
                    f"GovCon Gap: {gap['pattern_name']}",
                    f"Requirement pattern appearing in {gap['frequency']} RFPs with "
                    f"{gap['best_coverage']:.0%} coverage. Domain: {gap['domain']}.",
                    gap["domain"],
                    gap["priority"] / 10.0,
                    gap["priority"] / 10.0,
                    json.dumps([gap["domain"], gap["pattern_name"]]),
                    signal_id,
                    "new",
                    _now(),
                    _now(),
                    json.dumps(
                        {
                            "source": "gap_analyzer",
                            "frequency": gap["frequency"],
                            "best_coverage": gap["best_coverage"],
                        }
                    ),
                ),
            )
            registered += 1
        except Exception as exc:
            print(f"Warning: failed to register gap as innovation signal: {exc}", file=sys.stderr)

    if registered:
        _audit(conn, "register_innovation", f"Registered {registered} gaps as innovation signals")
        conn.commit()
    conn.close()

    return {
        "status": "ok",
        "registered": registered,
        "total_gaps": len(gaps),
        "threshold": 3.0,
    }


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovCon Gap Analyzer (D363)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--analyze", action="store_true", help="Full gap analysis")
    group.add_argument("--recommendations", action="store_true", help="Enhancement recommendations")
    group.add_argument("--heatmap", action="store_true", help="Domain × Grade heatmap")
    group.add_argument("--register-innovation", action="store_true", help="Cross-register gaps to Innovation Engine")

    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    if args.analyze:
        result = analyze_gaps()
    elif args.recommendations:
        result = generate_recommendations()
    elif args.heatmap:
        result = get_heatmap()
    elif args.register_innovation:
        result = register_gaps_as_innovation_signals()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        _print_human(result, args)
    else:
        print(json.dumps(result, indent=2, default=str))


def _print_human(result, args):
    """Human-readable output."""
    print(f"\n{'=' * 60}")
    print(f"  ICDEV™ Gap Analyzer — {result.get('status', 'unknown').upper()}")
    print(f"{'=' * 60}")

    if "summary" in result:
        s = result["summary"]
        print(f"\n  Total patterns: {s['total_patterns']}")
        print(f"  Gaps (N):       {s['N_gaps']}")
        print(f"  Partial (M):    {s['M_partial']}")
        print(f"  Compliant (L):  {s['L_compliant']}")
        print(f"  Gap rate:       {s['gap_rate']:.0%}")

    if "gaps" in result:
        print("\n  Top Gaps (priority-ranked):")
        for g in result["gaps"][:15]:
            print(
                f"  ❌ [{g['domain']:12s}] priority={g['priority']:5.1f}  freq={g['frequency']:3d}  {g['pattern_name'][:45]}"
            )

    if "recommendations" in result:
        print(f"\n  Recommendations: {result['total_recommendations']}")
        for r in result["recommendations"][:10]:
            rec = r["recommendation"]
            print(f"\n  [{r['domain']}] {r['pattern_name']}")
            print(f"    Effort: {rec['effort_estimate']}  Coverage: {r['current_coverage']:.0%}")
            print(f"    Action: {rec['action'][:100]}")

    if "heatmap" in result:
        print(f"\n  {'Domain':<15s} {'L':>4s} {'M':>4s} {'N':>4s} {'Health':>8s} {'Freq':>6s}")
        print(f"  {'─' * 45}")
        for domain, data in sorted(result["heatmap"].items()):
            health = data["health_score"]
            bar = "🟢" if health >= 0.7 else ("🟡" if health >= 0.4 else "🔴")
            print(
                f"  {domain:<15s} {data['L']:>4d} {data['M']:>4d} {data['N']:>4d}   {bar} {health:.2f} {data['total_frequency']:>6d}"
            )

    if "registered" in result:
        print(f"\n  Innovation signals registered: {result['registered']}/{result['total_gaps']}")

    print()


if __name__ == "__main__":
    main()
