#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Shipley Process Adaptation + ICDEV™-to-Shipley mapping.

Maps ICDEV™ Proposal Genesis reflexes to Shipley process phases,
recommends ceremony levels based on team size, generates process
guides, aligns to APMP Body of Knowledge, and assesses proposal
process maturity.  Deterministic, air-gap safe.

DB tables (read): proposal_opportunities, pg_proposal_quality_scores,
                   workflow_loops, audit_trail
DB tables (write): audit_trail

Usage:
    python tools/govcon/shipley_mapper.py --shipley-mapping --json
    python tools/govcon/shipley_mapper.py --recommend --team-size 8 --json
    python tools/govcon/shipley_mapper.py --process-guide --level 2 --json
    python tools/govcon/shipley_mapper.py --apmp-alignment --json
    python tools/govcon/shipley_mapper.py --maturity --json
    python tools/govcon/shipley_mapper.py --maturity --project-id "proj-123" --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, table_exists  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────


SHIPLEY_PHASES = [
    {
        "phase_num": 0,
        "phase_name": "Market Assessment",
        "shipley_gates": ["Long-Range Positioning"],
        "reflexes": [
            {"name": "R1 discover", "role": "SAM.gov polling, pre-solicitation monitoring"},
            {"name": "R19 vehicle", "role": "Contract vehicle tracking, on-ramp alerts"},
        ],
    },
    {
        "phase_num": 1,
        "phase_name": "Opportunity Assessment",
        "shipley_gates": ["Bid Decision (Pursue/No-Pursue)"],
        "reflexes": [
            {"name": "R2 scout", "role": "Competitor award tracking, intel briefs"},
            {"name": "R20 talent", "role": "Competitor hiring signal analysis"},
            {"name": "R9 decide (Pgo)", "role": "Bid/no-bid scoring, pursue probability"},
        ],
    },
    {
        "phase_num": 2,
        "phase_name": "Capture Planning",
        "shipley_gates": ["Capture Readiness Review"],
        "reflexes": [
            {"name": "R3 shape", "role": "Capture planning, teaming assessment"},
            {"name": "R4 engage", "role": "CRM engagement scoring, customer contact"},
            {"name": "R23 team", "role": "Partner scoring, workshare, TA/NDA, OCI screening"},
            {"name": "R16 regulate", "role": "FAR/DFARS/GSA clause change monitoring"},
        ],
    },
    {
        "phase_num": 3,
        "phase_name": "Proposal Planning",
        "shipley_gates": ["Proposal Kickoff", "Pink Team (Storyboard Review)"],
        "reflexes": [
            {"name": "R5 extract", "role": "Shall statement mining, amendment re-extraction"},
            {"name": "R6 map", "role": "Capability matching, compliance matrix build"},
            {"name": "R22 trace", "role": "L/M/C traceability, coverage tracking"},
            {"name": "R17 price", "role": "LCAT mapping, wrap rates, PTW analysis"},
        ],
    },
    {
        "phase_num": 4,
        "phase_name": "Proposal Development",
        "shipley_gates": ["Red Team", "Gold Team (Executive Review)"],
        "reflexes": [
            {"name": "R7 draft", "role": "Two-tier LLM drafting, Pulse reuse, KB enrichment"},
            {"name": "R8 polish", "role": "WriteGuard quality gate, grammar, readability, tone"},
            {"name": "R15 review", "role": "AI color team reviews (Pink/Red/Green/Gold)"},
        ],
    },
    {
        "phase_num": 5,
        "phase_name": "Post-Submission",
        "shipley_gates": ["Lessons Learned"],
        "reflexes": [
            {"name": "R9 decide (Pwin)", "role": "Win probability estimation, post-submission scoring"},
            {"name": "R10 monitor", "role": "CPARS prediction, EVM early warnings"},
            {"name": "R11 fulfill", "role": "CDRL auto-generation, compliance refresh"},
            {"name": "R24 bridge", "role": "Proposal-to-Program handoff on award"},
        ],
    },
]


CEREMONY_LEVELS = {
    1: {
        "level_name": "Micro",
        "team_range": "1-3 people",
        "ceremony": {
            "capture": "Informal capture notes in KB; no formal capture plan",
            "reviews": "AI-only reviews (Pink + Red); no human color teams",
            "pricing": "Simplified wrap-rate calculator; single cost model",
            "compliance": "Automated compliance matrix; no manual traceability",
        },
        "recommended_reflexes": [
            "R1 discover",
            "R5 extract",
            "R6 map",
            "R7 draft",
            "R8 polish",
            "R15 review",
            "R9 decide",
        ],
        "skippable_reflexes": [
            "R3 shape",
            "R4 engage",
            "R23 team",
            "R22 trace",
            "R16 regulate",
            "R19 vehicle",
            "R20 talent",
        ],
    },
    2: {
        "level_name": "Small",
        "team_range": "4-10 people",
        "ceremony": {
            "capture": "Lightweight capture plan template; basic win theme development",
            "reviews": "Pink + Red with AI pre-screen; optional human Gold Team",
            "pricing": "Full wrap rates + CALC+ benchmarking; basic PTW",
            "compliance": "Automated compliance matrix + L/M/C traceability",
        },
        "recommended_reflexes": [
            "R1 discover",
            "R2 scout",
            "R5 extract",
            "R6 map",
            "R7 draft",
            "R8 polish",
            "R9 decide",
            "R15 review",
            "R17 price",
            "R22 trace",
        ],
        "skippable_reflexes": [
            "R4 engage",
            "R16 regulate",
            "R19 vehicle",
            "R20 talent",
        ],
    },
    3: {
        "level_name": "Mid-Market",
        "team_range": "10-25 people",
        "ceremony": {
            "capture": "Full capture plan; win theme workshops; Black Hat sessions",
            "reviews": "All color teams with AI augmentation; human reviewers required",
            "pricing": "Full PTW analysis; sensitivity modeling; competitive rate analysis",
            "compliance": "Full L/M/C traceability + amendment tracking + CMMC flow-down",
        },
        "recommended_reflexes": [
            "R1 discover",
            "R2 scout",
            "R3 shape",
            "R4 engage",
            "R5 extract",
            "R6 map",
            "R7 draft",
            "R8 polish",
            "R9 decide",
            "R15 review",
            "R16 regulate",
            "R17 price",
            "R18 comply_cmmc",
            "R22 trace",
            "R23 team",
        ],
        "skippable_reflexes": ["R19 vehicle", "R20 talent"],
    },
    4: {
        "level_name": "Enterprise",
        "team_range": "25+ people",
        "ceremony": {
            "capture": "Full Shipley capture plan with formal gate reviews; BD pipeline",
            "reviews": "All color teams + external SME reviewers; formal gate approvals",
            "pricing": "Full PTW + sensitivity + Monte Carlo cost simulation; DCAA prep",
            "compliance": "Full traceability + CMMC + FAR/DFARS monitoring + audit-ready",
        },
        "recommended_reflexes": [
            "R1 discover",
            "R2 scout",
            "R3 shape",
            "R4 engage",
            "R5 extract",
            "R6 map",
            "R7 draft",
            "R8 polish",
            "R9 decide",
            "R10 monitor",
            "R11 fulfill",
            "R15 review",
            "R16 regulate",
            "R17 price",
            "R18 comply_cmmc",
            "R19 vehicle",
            "R20 talent",
            "R22 trace",
            "R23 team",
            "R24 bridge",
        ],
        "skippable_reflexes": [],
    },
}


APMP_ALIGNMENT = {
    "foundation": [
        {
            "competency": "Proposal Process",
            "tools": [
                "shipley_mapper.py (process guide)",
                "opportunity_lifecycle.py",
                "proposal_genesis daemon",
            ],
            "coverage_pct": 90,
        },
        {
            "competency": "Compliance",
            "tools": [
                "compliance_matrix_builder.py",
                "compliance_populator.py",
                "compliance_traceability.py (R22 trace)",
            ],
            "coverage_pct": 95,
        },
        {
            "competency": "Content Planning",
            "tools": [
                "gap_analyzer.py (R5 extract)",
                "capability_mapper.py (R6 map)",
                "response_drafter.py (R7 draft)",
            ],
            "coverage_pct": 85,
        },
        {
            "competency": "Graphic Design",
            "tools": [
                "Manual (AI cannot generate compliant graphics)",
            ],
            "coverage_pct": 10,
        },
        {
            "competency": "Reviews",
            "tools": [
                "color_review_simulator.py (R15 review)",
                "proposal_quality_evaluator.py (R8 polish)",
            ],
            "coverage_pct": 80,
        },
    ],
    "practitioner": [
        {
            "competency": "Capture Planning",
            "tools": [
                "govcon_engine.py (R3 shape)",
                "bayesian_bid_scorer.py (R9 decide)",
                "competitor_profiler.py (R2 scout)",
            ],
            "coverage_pct": 85,
        },
        {
            "competency": "Competitive Analysis",
            "tools": [
                "competitor_profiler.py (R2 scout)",
                "talent_intelligence.py (R20 talent)",
                "rate_benchmarker.py (PTW)",
            ],
            "coverage_pct": 75,
        },
        {
            "competency": "Pricing Strategy",
            "tools": [
                "rate_benchmarker.py (R17 price)",
                "lcat_mapper.py",
                "evm_engine.py",
            ],
            "coverage_pct": 80,
        },
        {
            "competency": "Volume Management",
            "tools": [
                "response_drafter.py (R7 draft)",
                "question_generator.py",
                "question_exporter.py",
            ],
            "coverage_pct": 70,
        },
    ],
    "professional": [
        {
            "competency": "Strategic Capture",
            "tools": [
                "govcon_engine.py (full pipeline)",
                "bayesian_bid_scorer.py (Pgo/Pwin)",
                "opportunity_lifecycle.py",
            ],
            "coverage_pct": 70,
        },
        {
            "competency": "Business Development",
            "tools": [
                "crm_manager.py (R4 engage)",
                "award_tracker.py",
                "sam_bridge.py (R1 discover)",
            ],
            "coverage_pct": 65,
        },
        {
            "competency": "Portfolio Management",
            "tools": [
                "portfolio_manager.py",
                "cpars_predictor.py (R10 monitor)",
                "program_bridge.py (R24 bridge)",
            ],
            "coverage_pct": 60,
        },
        {
            "competency": "Organizational Excellence",
            "tools": [
                "shipley_mapper.py (maturity assessment)",
                "proposal_quality_evaluator.py (trend)",
                "win/loss analytics (R13 analyze)",
            ],
            "coverage_pct": 55,
        },
    ],
}


# ── Helpers ───────────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="sm"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details):
    det = json.dumps(details) if isinstance(details, dict) else str(details)
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_now(), event_type, "shipley_mapper", action, det, None, None),
        )
    except Exception:
        try:
            conn.execute(
                "INSERT INTO audit_trail "
                "(project_id, event_type, actor, action, details, session_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (None, event_type, "shipley_mapper", action, det, None),
            )
        except Exception:
            pass  # audit is best-effort


def _safe_table_exists(conn, table_name):
    """Check if a table exists in the database (backend-aware, translation-independent)."""
    return table_exists(conn, table_name)


def _safe_count(conn, query, params=()):
    """Execute a COUNT query, returning 0 on error."""
    try:
        row = conn.execute(query, params).fetchone()
        if row:
            return row[0] if not isinstance(row, dict) else list(row.values())[0]
    except Exception:
        pass
    return 0


# ── Core Functions ────────────────────────────────────────────────────


def get_shipley_mapping():
    """Return the complete ICDEV™-to-Shipley process mapping."""
    return {
        "status": "ok",
        "phases": SHIPLEY_PHASES,
        "total_phases": len(SHIPLEY_PHASES),
        "total_reflexes_mapped": sum(len(p["reflexes"]) for p in SHIPLEY_PHASES),
    }


def recommend_process_level(team_size, annual_proposals=None, avg_contract_value=None):
    """Recommend Shipley ceremony level based on team parameters."""
    # Determine base level from team size
    if team_size <= 3:
        level = 1
    elif team_size <= 10:
        level = 2
    elif team_size <= 25:
        level = 3
    else:
        level = 4

    # Upgrade if contract value or volume warrants it
    if avg_contract_value is not None and avg_contract_value > 50_000_000:
        level = max(level, 3)
    if avg_contract_value is not None and avg_contract_value > 200_000_000:
        level = max(level, 4)
    if annual_proposals is not None and annual_proposals > 50:
        level = max(level, 3)

    info = CEREMONY_LEVELS[level]
    result = {
        "status": "ok",
        "level": level,
        "level_name": info["level_name"],
        "team_size": team_size,
        "annual_proposals": annual_proposals,
        "avg_contract_value": avg_contract_value,
        "ceremony": info["ceremony"],
        "recommended_reflexes": info["recommended_reflexes"],
        "skippable_reflexes": info["skippable_reflexes"],
    }
    return result


def generate_process_guide(level, opportunity_title=None):
    """Generate a markdown process guide for the given ceremony level."""
    if level not in CEREMONY_LEVELS:
        return {"status": "error", "message": f"Invalid level {level}. Must be 1-4."}

    info = CEREMONY_LEVELS[level]
    title = opportunity_title or "Proposal Effort"

    lines = [
        f"# Shipley Process Guide — Level {level}: {info['level_name']}",
        "",
        f"**Opportunity:** {title}",
        f"**Team Size:** {info['team_range']}",
        f"**Generated:** {_now()}",
        "",
        "---",
        "",
    ]

    # Phase-by-phase guide
    for phase in SHIPLEY_PHASES:
        pnum = phase["phase_num"]
        lines.append(f"## Phase {pnum}: {phase['phase_name']}")
        lines.append("")

        # Gates
        for gate in phase["shipley_gates"]:
            if level >= 3:
                lines.append(f"- **Gate:** {gate} (formal review required)")
            elif level == 2:
                lines.append(f"- **Gate:** {gate} (lightweight review)")
            else:
                lines.append(f"- **Gate:** {gate} (AI-automated)")

        lines.append("")
        lines.append("### Active Reflexes")
        lines.append("")

        recommended = set(info["recommended_reflexes"])
        for reflex in phase["reflexes"]:
            name = reflex["name"]
            if name in recommended:
                lines.append(f"- [x] **{name}** — {reflex['role']}")
            else:
                lines.append(f"- [ ] ~~{name}~~ — {reflex['role']} *(skipped at this level)*")

        lines.append("")

    # Timeline template
    lines.append("---")
    lines.append("")
    lines.append("## Timeline Template")
    lines.append("")

    if level == 1:
        lines.append("| Phase | Duration | Notes |")
        lines.append("|-------|----------|-------|")
        lines.append("| Capture | 1-2 days | Quick opportunity assessment |")
        lines.append("| Planning | 1 day | Extract requirements, map capabilities |")
        lines.append("| Development | 3-5 days | Draft + AI review |")
        lines.append("| Submission | 1 day | Final polish + submit |")
        lines.append("")
        lines.append("**Total: 6-9 days**")
    elif level == 2:
        lines.append("| Phase | Duration | Notes |")
        lines.append("|-------|----------|-------|")
        lines.append("| Capture | 3-5 days | Capture plan, initial teaming |")
        lines.append("| Planning | 2-3 days | Requirements, compliance matrix |")
        lines.append("| Development | 5-10 days | Draft + Pink + Red review |")
        lines.append("| Pricing | 2-3 days | Wrap rates, cost volume |")
        lines.append("| Submission | 1-2 days | Gold review + submit |")
        lines.append("")
        lines.append("**Total: 13-23 days**")
    elif level == 3:
        lines.append("| Phase | Duration | Notes |")
        lines.append("|-------|----------|-------|")
        lines.append("| Capture | 1-2 weeks | Full capture plan, Black Hat |")
        lines.append("| Planning | 1 week | Kickoff, compliance, traceability |")
        lines.append("| Development | 2-3 weeks | Draft + all color reviews |")
        lines.append("| Pricing | 1 week | PTW, sensitivity, DCAA review |")
        lines.append("| Submission | 3-5 days | Final production + submit |")
        lines.append("")
        lines.append("**Total: 5-8 weeks**")
    else:
        lines.append("| Phase | Duration | Notes |")
        lines.append("|-------|----------|-------|")
        lines.append("| Market Assessment | Ongoing | BD pipeline, vehicle tracking |")
        lines.append("| Opportunity Assessment | 1-2 weeks | Formal bid decision gate |")
        lines.append("| Capture | 2-4 weeks | Full Shipley capture plan |")
        lines.append("| Proposal Planning | 1-2 weeks | Kickoff, storyboards, Pink Team |")
        lines.append("| Proposal Development | 3-6 weeks | All color teams, external SMEs |")
        lines.append("| Pricing | 1-2 weeks | Full PTW + Monte Carlo |")
        lines.append("| Final Production | 1 week | Gold Team + production |")
        lines.append("")
        lines.append("**Total: 8-16 weeks**")

    # Checklist
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Pre-Submission Checklist")
    lines.append("")
    lines.append("- [ ] All Section L requirements addressed")
    lines.append("- [ ] Compliance matrix 100% populated")
    lines.append("- [ ] Page/volume limits verified")

    if level >= 2:
        lines.append("- [ ] Win themes consistent across volumes")
        lines.append("- [ ] Past performance references confirmed")
        lines.append("- [ ] Pricing reviewed against CALC+ benchmarks")

    if level >= 3:
        lines.append("- [ ] L/M/C traceability verified (R22)")
        lines.append("- [ ] CMMC flow-down validated (R18)")
        lines.append("- [ ] All color team findings resolved")
        lines.append("- [ ] Teaming agreements executed")

    if level >= 4:
        lines.append("- [ ] Executive sponsor sign-off")
        lines.append("- [ ] External legal review complete")
        lines.append("- [ ] FAR/DFARS clause compliance verified (R16)")
        lines.append("- [ ] DCAA-ready cost documentation")

    lines.append("")

    markdown = "\n".join(lines)
    return {
        "status": "ok",
        "level": level,
        "level_name": info["level_name"],
        "markdown": markdown,
    }


def get_apmp_alignment():
    """Map ICDEV™ features to APMP Body of Knowledge competencies."""
    total_competencies = (
        len(APMP_ALIGNMENT["foundation"]) + len(APMP_ALIGNMENT["practitioner"]) + len(APMP_ALIGNMENT["professional"])
    )

    all_coverages = []
    for tier_name in ("foundation", "practitioner", "professional"):
        for comp in APMP_ALIGNMENT[tier_name]:
            all_coverages.append(comp["coverage_pct"])

    avg_coverage = sum(all_coverages) / len(all_coverages) if all_coverages else 0

    return {
        "status": "ok",
        "foundation": APMP_ALIGNMENT["foundation"],
        "practitioner": APMP_ALIGNMENT["practitioner"],
        "professional": APMP_ALIGNMENT["professional"],
        "summary": {
            "total_competencies": total_competencies,
            "avg_coverage_pct": round(avg_coverage, 1),
            "strongest": max(all_coverages) if all_coverages else 0,
            "weakest": min(all_coverages) if all_coverages else 0,
        },
    }


def assess_maturity(project_id=None):
    """Assess proposal process maturity using the Shipley 5-level model.

    Level 1 (Ad Hoc):       No defined process
    Level 2 (Basic):        Some processes but inconsistent
    Level 3 (Defined):      Standard processes documented
    Level 4 (Managed):      Processes measured with metrics
    Level 5 (Optimized):    Continuous improvement with data-driven decisions
    """
    evidence = []
    score = 0

    try:
        conn = _get_db()
    except Exception:
        return {
            "status": "ok",
            "maturity_level": 1,
            "level_name": "Ad Hoc",
            "evidence": ["Could not connect to database"],
            "recommendations": [
                "Initialize ICDEV™ database",
                "Start tracking proposals in the system",
            ],
        }

    # Check Level 2 indicators: basic processes exist
    opp_count = _safe_count(
        conn,
        "SELECT COUNT(*) FROM proposal_opportunities"  # nosec B608 -- table/column names are internal constants, not user input
        + (" WHERE id IS NOT NULL" if not project_id else ""),
    )
    if opp_count > 0:
        evidence.append(f"Found {opp_count} tracked opportunities (process exists)")
        score += 1

    # Check for quality scores (Level 3: defined and measured)
    if _safe_table_exists(conn, "pg_proposal_quality_scores"):
        qs_count = _safe_count(conn, "SELECT COUNT(*) FROM pg_proposal_quality_scores")
        if qs_count > 0:
            evidence.append(f"Found {qs_count} quality evaluations (reviews defined)")
            score += 1

    # Check for workflow loops (Level 3: processes documented)
    if _safe_table_exists(conn, "workflow_loops"):
        wl_params = ()
        wl_query = "SELECT COUNT(*) FROM workflow_loops"
        if project_id:
            wl_query += " WHERE project_id = ?"
            wl_params = (project_id,)
        wl_count = _safe_count(conn, wl_query, wl_params)
        if wl_count > 0:
            evidence.append(f"Found {wl_count} workflow loops (process discipline)")
            score += 1

    # Check for audit trail entries related to proposals (Level 4: measured)
    audit_count = _safe_count(
        conn,
        "SELECT COUNT(*) FROM audit_trail WHERE event_type LIKE ?",
        ("%proposal%",),
    )
    if audit_count > 10:
        evidence.append(f"Found {audit_count} proposal audit events (process measured)")
        score += 1

    # Check for win/loss data (Level 4: metrics-driven)
    if _safe_table_exists(conn, "proposal_opportunities"):
        won_count = _safe_count(
            conn,
            "SELECT COUNT(*) FROM proposal_opportunities WHERE status IN (?, ?)",
            ("won", "lost"),
        )
        if won_count > 0:
            evidence.append(f"Found {won_count} win/loss records (outcome tracking)")
            score += 1

    # Check for experiment results (Level 5: data-driven improvement)
    if _safe_table_exists(conn, "autoresearch_experiments"):
        exp_count = _safe_count(conn, "SELECT COUNT(*) FROM autoresearch_experiments")
        if exp_count > 0:
            evidence.append(f"Found {exp_count} experiment results (continuous improvement)")
            score += 1

    # Map score to maturity level
    if score >= 6:
        maturity_level = 5
        level_name = "Optimized"
    elif score >= 4:
        maturity_level = 4
        level_name = "Managed"
    elif score >= 2:
        maturity_level = 3
        level_name = "Defined"
    elif score >= 1:
        maturity_level = 2
        level_name = "Basic"
    else:
        maturity_level = 1
        level_name = "Ad Hoc"

    # Generate recommendations based on level
    recommendations = []
    if maturity_level < 2:
        recommendations.append("Start tracking opportunities in ICDEV™ (R1 discover)")
        recommendations.append("Use proposal_quality_evaluator.py for consistent reviews")
    if maturity_level < 3:
        recommendations.append("Define standard proposal workflow using workflow loops")
        recommendations.append("Implement color team reviews (R15 review)")
    if maturity_level < 4:
        recommendations.append("Track win/loss outcomes and conduct post-mortems (R13 analyze)")
        recommendations.append("Enable full audit trail for proposal activities")
    if maturity_level < 5:
        recommendations.append("Run Bayesian experiments to optimize proposal processes")
        recommendations.append("Enable feedback loop from win/loss to training (R14 train)")

    # Audit
    try:
        _audit(
            conn,
            "proposal.maturity_assessed",
            "assess_maturity",
            {
                "maturity_level": maturity_level,
                "score": score,
                "project_id": project_id,
            },
        )
        conn.commit()
    except Exception:
        pass

    return {
        "status": "ok",
        "maturity_level": maturity_level,
        "level_name": level_name,
        "raw_score": score,
        "max_score": 6,
        "evidence": evidence if evidence else ["No proposal activity detected"],
        "recommendations": recommendations,
    }


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Shipley Process Adaptation + ICDEV™-to-Shipley mapping")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--shipley-mapping", action="store_true", help="Show ICDEV™-to-Shipley phase mapping")
    group.add_argument("--recommend", action="store_true", help="Recommend ceremony level")
    group.add_argument("--process-guide", action="store_true", help="Generate process guide for a level")
    group.add_argument("--apmp-alignment", action="store_true", help="Show APMP Body of Knowledge alignment")
    group.add_argument("--maturity", action="store_true", help="Assess proposal process maturity")

    parser.add_argument("--team-size", type=int, help="Team size for --recommend")
    parser.add_argument("--annual-proposals", type=int, help="Annual proposal count for --recommend")
    parser.add_argument("--avg-contract-value", type=float, help="Average contract value for --recommend")
    parser.add_argument("--level", type=int, choices=[1, 2, 3, 4], help="Ceremony level for --process-guide")
    parser.add_argument("--opportunity-title", help="Title for --process-guide header")
    parser.add_argument("--project-id", help="Project ID for --maturity")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = {}

    if args.shipley_mapping:
        result = get_shipley_mapping()

    elif args.recommend:
        if not args.team_size:
            result = {"status": "error", "message": "Provide --team-size"}
        else:
            result = recommend_process_level(
                args.team_size,
                args.annual_proposals,
                args.avg_contract_value,
            )

    elif args.process_guide:
        if not args.level:
            result = {"status": "error", "message": "Provide --level (1-4)"}
        else:
            result = generate_process_guide(args.level, args.opportunity_title)

    elif args.apmp_alignment:
        result = get_apmp_alignment()

    elif args.maturity:
        result = assess_maturity(args.project_id)

    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
