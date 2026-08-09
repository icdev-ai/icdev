#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Template-based Dossier Generator for ICDEV™ Research Engine (D-RES-9).

Transforms scored research challenges, regulatory mappings, build/buy analyses,
and capability coverage into structured, template-based industry research
dossiers. No LLM required -- all generation is deterministic and air-gap safe.

Architecture:
    - Reads from research_challenges, research_regulatory_map, research_build_buy,
      research_capability_map, research_signals tables
    - Builds dossier using Python f-string template (D-RES-9, no LLM)
    - Overall opportunity score from top 10 challenge composite_scores
    - Stores results in research_dossiers table (append-only, D6/D-RES-5)
    - Status transitions via INSERT of new row (append-only compliant)
    - Audit trail for all generation and review events

Usage:
    python tools/research/dossier_generator.py --generate --session-id rsess-xxx --json
    python tools/research/dossier_generator.py --get --dossier-id rdoss-xxx --json
    python tools/research/dossier_generator.py --get --session-id rsess-xxx --json
    python tools/research/dossier_generator.py --list --json
    python tools/research/dossier_generator.py --list --status approved --json
    python tools/research/dossier_generator.py --review --dossier-id rdoss-xxx --reviewer "analyst@mil" --status reviewed --json
    python tools/research/dossier_generator.py --trigger-fitness --dossier-id rdoss-xxx --json
    python tools/research/dossier_generator.py --generate --session-id rsess-xxx --human
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "research_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


# =========================================================================
# CONSTANTS
# =========================================================================
VALID_STATUSES = ("generated", "reviewed", "approved", "rejected", "child_app_triggered")

SEVERITY_THRESHOLDS = {
    "critical": 0.80,
    "notable": 0.50,
}


# =========================================================================
# DOSSIER TEMPLATE
# =========================================================================
_DOSSIER_TEMPLATE = """# Industry Research Dossier: {title}

CUI // SP-CTI

**Vertical:** {vertical_name}
**Session:** {session_name} ({session_id})
**Generated:** {generated_at}
**Overall Opportunity Score:** {overall_score:.2f}

---

## Executive Summary

{executive_summary}

## Vertical Overview

{vertical_overview}

## Challenge Analysis

**Total Challenges:** {challenge_count} ({critical_count} critical, {notable_count} notable)

### Critical Challenges (Score >= 0.80)

{critical_challenges_section}

### Notable Challenges (Score 0.50 - 0.79)

{notable_challenges_section}

## Regulatory Landscape

{regulatory_section}

## Competitive Landscape

{competitive_section}

## Build / Buy / Partner Matrix

{build_buy_section}

## ICDEV™ Capability Coverage

**Average Coverage:** {avg_coverage:.1%}
**Enhancements Needed:** {enhancement_count}

{capability_section}

## Opportunity Assessment

{opportunity_section}

## Recommendations

{recommendations_section}

## Predictive Analysis & Surprise Recommendations

{forecast_section}

## Appendix

### Signal Sources
{signal_sources_section}

### All Challenges (Scored)
{appendix_challenges_section}
"""


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dossier_id():
    """Generate a dossier ID with rdoss- prefix."""
    return f"rdoss-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="research-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load research config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _safe_json_loads(text, default=None):
    """Safely parse JSON string, returning default on failure."""
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


# =========================================================================
# SECTION BUILDERS
# =========================================================================
def _build_executive_summary(challenges, regulatory_maps, build_buy, capability_maps):
    """Build 3-5 sentence executive summary from findings.

    Template-based using counts and top items. No LLM.

    Args:
        challenges: list of challenge dicts.
        regulatory_maps: list of regulatory mapping dicts.
        build_buy: list of build/buy decision dicts.
        capability_maps: list of capability mapping dicts.

    Returns:
        str: executive summary paragraph.
    """
    critical = [c for c in challenges if c.get("composite_score", 0) >= SEVERITY_THRESHOLDS["critical"]]
    notable = [
        c
        for c in challenges
        if SEVERITY_THRESHOLDS["notable"] <= c.get("composite_score", 0) < SEVERITY_THRESHOLDS["critical"]
    ]

    # Top categories
    cat_counts = {}
    for c in challenges:
        cat = c.get("category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    top_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    top_cats_str = ", ".join(f"{cat} ({cnt})" for cat, cnt in top_cats) if top_cats else "N/A"

    # Regulatory bodies
    reg_bodies = set()
    for rm in regulatory_maps:
        body = rm.get("regulatory_body")
        if body:
            reg_bodies.add(body)

    # Build/buy recommendations
    build_count = sum(1 for bb in build_buy if bb.get("recommendation") == "build")
    buy_count = sum(1 for bb in build_buy if bb.get("recommendation") == "buy")
    partner_count = sum(1 for bb in build_buy if bb.get("recommendation") == "partner")

    # Average coverage
    coverage_scores = [cm.get("coverage_score", 0.0) for cm in capability_maps if cm.get("coverage_score") is not None]
    avg_coverage = sum(coverage_scores) / max(1, len(coverage_scores)) if coverage_scores else 0.0

    parts = []
    parts.append(
        f"This research identified {len(challenges)} challenges across the vertical, "
        f"with {len(critical)} rated critical and {len(notable)} rated notable."
    )
    if top_cats:
        parts.append(f"The most prevalent challenge categories are {top_cats_str}.")
    if reg_bodies:
        parts.append(
            f"The regulatory landscape involves {len(reg_bodies)} regulatory "
            f"{'body' if len(reg_bodies) == 1 else 'bodies'} "
            f"across {len(regulatory_maps)} mapped regulations."
        )
    if build_buy:
        parts.append(
            f"Build/buy analysis recommends {build_count} build, "
            f"{buy_count} buy, and {partner_count} partner decisions."
        )
    if coverage_scores:
        parts.append(f"ICDEV™ capability coverage averages {avg_coverage:.0%} across mapped challenges.")

    return " ".join(parts)


def _build_vertical_overview(vertical, session):
    """Build vertical overview from vertical config and session focus areas.

    Args:
        vertical: dict with vertical data from DB.
        session: dict with session data from DB.

    Returns:
        str: vertical overview paragraph.
    """
    vert_desc = vertical.get("description") or f"Industry vertical: {vertical.get('name', 'Unknown')}"
    focus_areas = _safe_json_loads(session.get("focus_areas"), [])

    lines = [vert_desc]
    if focus_areas:
        lines.append("")
        lines.append("**Focus Areas:**")
        for area in focus_areas:
            lines.append(f"- {area}")

    keywords = _safe_json_loads(vertical.get("keywords"), [])
    if keywords:
        lines.append("")
        lines.append(f"**Key Topics:** {', '.join(keywords[:10])}")

    return "\n".join(lines)


def _build_challenge_section(challenges, severity_filter):
    """Build markdown table of challenges filtered by severity.

    Args:
        challenges: list of challenge dicts.
        severity_filter: "critical" or "notable" to filter by score threshold.

    Returns:
        str: formatted markdown table string.
    """
    if severity_filter == "critical":
        filtered = [c for c in challenges if c.get("composite_score", 0) >= SEVERITY_THRESHOLDS["critical"]]
    elif severity_filter == "notable":
        filtered = [
            c
            for c in challenges
            if SEVERITY_THRESHOLDS["notable"] <= c.get("composite_score", 0) < SEVERITY_THRESHOLDS["critical"]
        ]
    else:
        filtered = challenges

    if not filtered:
        return "_No challenges in this severity range._"

    lines = [
        "| # | Challenge | Score | Category | Market | Regulatory | Complexity |",
        "|---|-----------|-------|----------|--------|------------|------------|",
    ]
    for i, c in enumerate(filtered, 1):
        title = (c.get("title") or "Untitled")[:50]
        score = c.get("composite_score", 0.0)
        category = c.get("category", "other")
        market = c.get("market_demand", 0.0)
        regulatory = c.get("regulatory_pressure", 0.0)
        complexity = c.get("technical_complexity", 0.0)
        lines.append(
            f"| {i} | {title} | {score:.2f} | {category} | {market:.2f} | {regulatory:.2f} | {complexity:.2f} |"
        )

    return "\n".join(lines)


def _build_regulatory_section(regulatory_maps):
    """Build regulatory landscape section grouped by body.

    Args:
        regulatory_maps: list of regulatory mapping dicts.

    Returns:
        str: formatted markdown with regulatory groupings.
    """
    if not regulatory_maps:
        return "_No regulatory mappings available._"

    # Group by regulatory body
    by_body = {}
    for rm in regulatory_maps:
        body = rm.get("regulatory_body", "Unknown")
        if body not in by_body:
            by_body[body] = []
        by_body[body].append(rm)

    lines = []
    for body, regs in sorted(by_body.items()):
        lines.append(f"### {body}")
        lines.append("")
        total_enforcement = sum(r.get("enforcement_actions", 0) for r in regs)
        coverage_scores = [r.get("crosswalk_coverage", 0.0) for r in regs if r.get("crosswalk_coverage") is not None]
        avg_coverage = sum(coverage_scores) / max(1, len(coverage_scores)) if coverage_scores else 0.0
        lines.append(
            f"**Regulations:** {len(regs)} | **Enforcement Actions:** {total_enforcement} | "
            f"**Avg Crosswalk Coverage:** {avg_coverage:.0%}"
        )
        lines.append("")
        for r in regs:
            name = r.get("regulation_name", "Unknown")
            reg_id = r.get("regulation_id", "")
            coverage = r.get("crosswalk_coverage", 0.0)
            enforcement = r.get("enforcement_actions", 0)
            id_str = f" ({reg_id})" if reg_id else ""
            lines.append(f"- **{name}**{id_str} -- coverage: {coverage:.0%}, enforcement: {enforcement}")
        lines.append("")

    return "\n".join(lines)


def _build_competitive_section(signals):
    """Build competitive landscape section from signal source types.

    Args:
        signals: list of signal dicts.

    Returns:
        str: formatted markdown with competitive analysis.
    """
    if not signals:
        return "_No competitive signals available._"

    # Count by source type
    by_source_type = {}
    for s in signals:
        st = s.get("source_type", "unknown")
        by_source_type[st] = by_source_type.get(st, 0) + 1

    # Separate open_source from saas_commercial
    oss_sources = {"github", "awesome_list", "package_registry"}
    saas_sources = {"product_page", "producthunt"}

    oss_count = sum(v for k, v in by_source_type.items() if k in oss_sources)
    saas_count = sum(v for k, v in by_source_type.items() if k in saas_sources)

    lines = []
    lines.append(f"**Open-Source Signals:** {oss_count} | **Commercial/SaaS Signals:** {saas_count}")
    lines.append("")

    # Top projects/products by upvotes or citations
    top_signals = sorted(
        [s for s in signals if s.get("source_type") in oss_sources | saas_sources],
        key=lambda x: x.get("upvotes", 0) + x.get("citations", 0),
        reverse=True,
    )[:10]

    if top_signals:
        lines.append("**Top Projects/Products:**")
        lines.append("")
        for s in top_signals:
            title = (s.get("title") or "Untitled")[:60]
            st = s.get("source_type", "unknown")
            upvotes = s.get("upvotes", 0)
            url = s.get("url", "")
            url_str = f" -- [{url}]({url})" if url else ""
            lines.append(f"- **{title}** ({st}, {upvotes} upvotes){url_str}")
    else:
        lines.append("_No top projects/products identified._")

    return "\n".join(lines)


def _build_build_buy_section(build_buy_records):
    """Build build/buy/partner decision matrix table.

    Args:
        build_buy_records: list of build/buy decision dicts.

    Returns:
        str: formatted markdown table.
    """
    if not build_buy_records:
        return "_No build/buy analyses available._"

    lines = [
        "| Challenge | Recommendation | Build | Buy | Partner | Effort | Risk |",
        "|-----------|---------------|-------|-----|---------|--------|------|",
    ]
    for bb in build_buy_records:
        challenge_id = bb.get("challenge_id", "")[:16]
        rec = bb.get("recommendation", "N/A")
        build = bb.get("build_score", 0.0)
        buy = bb.get("buy_score", 0.0)
        partner = bb.get("partner_score", 0.0)
        effort = bb.get("estimated_effort") or "N/A"
        risk = bb.get("risk_level", "medium")
        lines.append(f"| {challenge_id} | **{rec}** | {build:.2f} | {buy:.2f} | {partner:.2f} | {effort} | {risk} |")

    return "\n".join(lines)


def _build_capability_section(cap_maps):
    """Build ICDEV™ capability coverage section with visual bars.

    Args:
        cap_maps: list of capability mapping dicts.

    Returns:
        str: formatted capability coverage display.
    """
    if not cap_maps:
        return "_No capability mappings available._"

    # Aggregate by capability (average coverage per capability_name)
    by_cap = {}
    for cm in cap_maps:
        name = cm.get("capability_name", "Unknown")
        score = cm.get("coverage_score", 0.0)
        enhancement = cm.get("enhancement_needed", 0)
        if name not in by_cap:
            by_cap[name] = {"scores": [], "enhancements": 0}
        by_cap[name]["scores"].append(score)
        by_cap[name]["enhancements"] += enhancement

    lines = []
    lines.append("```")
    for name in sorted(by_cap.keys()):
        scores = by_cap[name]["scores"]
        avg = sum(scores) / max(1, len(scores))
        bar_len = int(avg * 20)
        bar = "#" * bar_len + " " * (20 - bar_len)
        enh_marker = " [+]" if by_cap[name]["enhancements"] > 0 else ""
        lines.append(f"  {name:30s} {avg:.2f}  |{bar}|{enh_marker}")
    lines.append("```")
    lines.append("")
    lines.append("_[+] = Enhancement needed to fully address challenge_")

    return "\n".join(lines)


def _build_opportunity_section(overall_score, challenges, build_buy):
    """Build opportunity assessment narrative.

    Args:
        overall_score: float overall opportunity score.
        challenges: list of challenge dicts.
        build_buy: list of build/buy dicts.

    Returns:
        str: opportunity assessment paragraph.
    """
    critical = [c for c in challenges if c.get("composite_score", 0) >= SEVERITY_THRESHOLDS["critical"]]
    build_count = sum(1 for bb in build_buy if bb.get("recommendation") == "build")

    parts = []
    if overall_score >= 0.80:
        parts.append(
            f"**High Opportunity ({overall_score:.2f}).** This vertical presents strong market opportunity "
            f"with {len(critical)} critical challenges that ICDEV™ is well-positioned to address."
        )
    elif overall_score >= 0.60:
        parts.append(
            f"**Moderate Opportunity ({overall_score:.2f}).** This vertical shows meaningful potential "
            f"with {len(critical)} critical challenges, though some areas require capability enhancement."
        )
    elif overall_score >= 0.40:
        parts.append(
            f"**Emerging Opportunity ({overall_score:.2f}).** The vertical has identifiable pain points "
            f"but challenges are less severe. Selective investment recommended."
        )
    else:
        parts.append(
            f"**Low Opportunity ({overall_score:.2f}).** Limited high-impact challenges identified. "
            f"Consider monitoring this vertical rather than active investment."
        )

    if build_count > 0:
        parts.append(
            f"{build_count} challenge{'s' if build_count != 1 else ''} "
            f"recommended for in-house build using ICDEV™ capabilities."
        )

    return " ".join(parts)


def _build_recommendations_section(challenges, build_buy, capability_maps):
    """Build bulleted recommendations based on findings.

    Args:
        challenges: list of challenge dicts.
        build_buy: list of build/buy dicts.
        capability_maps: list of capability mapping dicts.

    Returns:
        str: bulleted recommendation list.
    """
    recommendations = []

    # Top critical challenges
    critical = sorted(
        [c for c in challenges if c.get("composite_score", 0) >= SEVERITY_THRESHOLDS["critical"]],
        key=lambda x: x.get("composite_score", 0),
        reverse=True,
    )[:5]

    if critical:
        recommendations.append(
            f"- **Address {len(critical)} critical challenges** -- these represent the highest-impact "
            f"opportunities with composite scores >= 0.80."
        )
        for c in critical[:3]:
            title = (c.get("title") or "Untitled")[:60]
            score = c.get("composite_score", 0.0)
            recommendations.append(f"  - {title} (score: {score:.2f})")

    # Build recommendations from build/buy
    build_items = [bb for bb in build_buy if bb.get("recommendation") == "build"]
    if build_items:
        recommendations.append(
            f"- **Build {len(build_items)} capabilities in-house** -- leverage existing ICDEV™ "
            f"tools and frameworks to address these challenges directly."
        )

    partner_items = [bb for bb in build_buy if bb.get("recommendation") == "partner"]
    if partner_items:
        recommendations.append(
            f"- **Evaluate {len(partner_items)} partnership opportunities** -- these challenges "
            f"may benefit from external domain expertise or technology partnerships."
        )

    buy_items = [bb for bb in build_buy if bb.get("recommendation") == "buy"]
    if buy_items:
        recommendations.append(
            f"- **Consider buying/integrating {len(buy_items)} solutions** -- commercial solutions "
            f"may accelerate time-to-market for these areas."
        )

    # Coverage gaps
    enhancements_needed = [cm for cm in capability_maps if cm.get("enhancement_needed", 0) > 0]
    if enhancements_needed:
        recommendations.append(
            f"- **Enhance {len(enhancements_needed)} ICDEV™ capabilities** -- existing tooling "
            f"partially covers these areas but requires targeted improvements."
        )

    if not recommendations:
        return "_No specific recommendations at this time._"

    return "\n".join(recommendations)


def _build_forecast_section(session_id, db_path=None):
    """Build forecast/surprise recommendations section (D-RES-21).

    Queries research_forecasts for this session and formats as markdown.
    """
    try:
        from tools.research.forecast_generator import get_forecasts

        forecasts = get_forecasts(session_id, db_path=db_path, limit=5)
    except Exception:
        forecasts = []

    if not forecasts:
        return "_No predictive analysis available. Run the FORECAST stage to generate predictions._"

    lines = [
        "The following predictions are ranked by **composite score** "
        "(confidence × surprise). Higher surprise scores indicate "
        "non-obvious, contrarian, or cross-domain insights.\n"
    ]

    for i, fc in enumerate(forecasts, 1):
        title = fc.get("title", "Untitled")
        desc = fc.get("description", "")
        pred_type = fc.get("prediction_type", "unknown")
        confidence = float(fc.get("confidence", 0))
        surprise = float(fc.get("surprise_score", 0))
        composite = float(fc.get("composite_rank", 0))
        horizon = fc.get("time_horizon", "6mo")

        lines.append(f"### {i}. {title}")
        lines.append("")
        lines.append(f"- **Type:** {pred_type.replace('_', ' ').title()}")
        lines.append(f"- **Confidence:** {confidence:.0%}")
        lines.append(f"- **Surprise Score:** {surprise:.0%}")
        lines.append(f"- **Composite Rank:** {composite:.3f}")
        lines.append(f"- **Time Horizon:** {horizon}")
        if desc:
            lines.append("")
            lines.append(f"{desc}")
        lines.append("")

    return "\n".join(lines)


def _build_signal_sources_section(signals):
    """Build signal source statistics section.

    Args:
        signals: list of signal dicts.

    Returns:
        str: source stream and type counts.
    """
    if not signals:
        return "_No signals collected._"

    # Count by source stream (top-level source field)
    by_stream = {}
    by_type = {}
    for s in signals:
        stream = s.get("source", "unknown")
        stype = s.get("source_type", "unknown")
        by_stream[stream] = by_stream.get(stream, 0) + 1
        by_type[stype] = by_type.get(stype, 0) + 1

    lines = []
    lines.append(f"**Total Signals:** {len(signals)}")
    lines.append("")
    lines.append("**By Stream:**")
    for stream, count in sorted(by_stream.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {stream}: {count}")
    lines.append("")
    lines.append("**By Source Type:**")
    for stype, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {stype}: {count}")

    return "\n".join(lines)


def _build_appendix_challenges(challenges):
    """Build appendix with all challenges including appendix-level.

    Args:
        challenges: list of all challenge dicts.

    Returns:
        str: full scored challenge table.
    """
    if not challenges:
        return "_No challenges to display._"

    lines = [
        "| # | Challenge | Score | Category | Severity | Signals |",
        "|---|-----------|-------|----------|----------|---------|",
    ]
    for i, c in enumerate(challenges, 1):
        title = (c.get("title") or "Untitled")[:50]
        score = c.get("composite_score", 0.0)
        category = c.get("category", "other")
        severity = c.get("severity", "appendix")
        signal_count = c.get("signal_count", 0)
        lines.append(f"| {i} | {title} | {score:.2f} | {category} | {severity} | {signal_count} |")

    return "\n".join(lines)


# =========================================================================
# DOSSIER GENERATION
# =========================================================================
def generate_dossier(session_id, db_path=None):
    """Generate a full research dossier for a session.

    Reads session data, vertical config, challenges, regulatory mappings,
    build/buy analyses, capability maps, and signals. Builds all sections
    using template helpers, computes overall opportunity score, fills
    the dossier template, and stores the result in research_dossiers.

    Args:
        session_id: The research session ID (rsess-...).
        db_path: Optional database path override.

    Returns:
        dict with dossier data or error.
    """
    conn = _get_db(db_path)
    try:
        # 1. Load session
        session_row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not session_row:
            return {"error": f"Session not found: {session_id}"}
        session = dict(session_row)

        # 2. Load vertical config
        vert_row = conn.execute("SELECT * FROM research_verticals WHERE id = %s", (session["vertical_id"],)).fetchone()
        if not vert_row:
            return {"error": f"Vertical not found: {session['vertical_id']}"}
        vertical = dict(vert_row)

        # 3. Query challenges sorted by composite_score DESC
        challenges = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM research_challenges WHERE session_id = %s ORDER BY composite_score DESC",
                (session_id,),
            ).fetchall()
        ]

        # 4. Query regulatory mappings
        regulatory_maps = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM research_regulatory_map WHERE session_id = %s",
                (session_id,),
            ).fetchall()
        ]

        # 5. Query build/buy analyses
        build_buy = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM research_build_buy WHERE session_id = %s",
                (session_id,),
            ).fetchall()
        ]

        # 6. Query capability mappings
        capability_maps = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM research_capability_map WHERE session_id = %s",
                (session_id,),
            ).fetchall()
        ]

        # 7. Query signals (for source stats)
        signals = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM research_signals WHERE session_id = %s",
                (session_id,),
            ).fetchall()
        ]

        # 8. Build all sections
        executive_summary = _build_executive_summary(challenges, regulatory_maps, build_buy, capability_maps)
        vertical_overview = _build_vertical_overview(vertical, session)
        critical_challenges_section = _build_challenge_section(challenges, "critical")
        notable_challenges_section = _build_challenge_section(challenges, "notable")
        regulatory_section = _build_regulatory_section(regulatory_maps)
        competitive_section = _build_competitive_section(signals)
        build_buy_section = _build_build_buy_section(build_buy)
        capability_section = _build_capability_section(capability_maps)
        signal_sources_section = _build_signal_sources_section(signals)
        appendix_challenges_section = _build_appendix_challenges(challenges)

        # 9. Compute overall opportunity score
        top_scores = sorted(
            [c["composite_score"] for c in challenges if c.get("composite_score")],
            reverse=True,
        )[:10]
        overall_score = sum(top_scores) / max(1, len(top_scores)) if top_scores else 0.0
        overall_score = round(max(0.0, min(1.0, overall_score)), 4)

        opportunity_section = _build_opportunity_section(overall_score, challenges, build_buy)
        recommendations_section = _build_recommendations_section(challenges, build_buy, capability_maps)
        forecast_section = _build_forecast_section(session_id, db_path)

        # Counts for template
        critical_count = len([c for c in challenges if c.get("composite_score", 0) >= SEVERITY_THRESHOLDS["critical"]])
        notable_count = len(
            [
                c
                for c in challenges
                if SEVERITY_THRESHOLDS["notable"] <= c.get("composite_score", 0) < SEVERITY_THRESHOLDS["critical"]
            ]
        )

        # Average capability coverage
        coverage_scores = [
            cm.get("coverage_score", 0.0) for cm in capability_maps if cm.get("coverage_score") is not None
        ]
        avg_coverage = sum(coverage_scores) / max(1, len(coverage_scores)) if coverage_scores else 0.0
        enhancement_count = sum(1 for cm in capability_maps if cm.get("enhancement_needed", 0) > 0)

        # 10. Format template
        now = _now()
        title = f"{vertical.get('name', 'Unknown')} Research - {session.get('name', session_id)}"

        dossier_content = _DOSSIER_TEMPLATE.format(
            title=title,
            vertical_name=vertical.get("name", "Unknown"),
            session_name=session.get("name", ""),
            session_id=session_id,
            generated_at=now,
            overall_score=overall_score,
            executive_summary=executive_summary,
            vertical_overview=vertical_overview,
            challenge_count=len(challenges),
            critical_count=critical_count,
            notable_count=notable_count,
            critical_challenges_section=critical_challenges_section,
            notable_challenges_section=notable_challenges_section,
            regulatory_section=regulatory_section,
            competitive_section=competitive_section,
            build_buy_section=build_buy_section,
            avg_coverage=avg_coverage,
            enhancement_count=enhancement_count,
            capability_section=capability_section,
            opportunity_section=opportunity_section,
            recommendations_section=recommendations_section,
            forecast_section=forecast_section,
            signal_sources_section=signal_sources_section,
            appendix_challenges_section=appendix_challenges_section,
        )

        # 11. INSERT into research_dossiers (append-only)
        dossier_id = _dossier_id()
        conn.execute(
            """INSERT INTO research_dossiers
            (id, session_id, vertical_id, title, content, executive_summary,
             signal_count, challenge_count, critical_challenges, notable_challenges,
             regulatory_mappings, build_buy_analyses, capability_coverage,
             overall_opportunity_score, status, metadata, generated_at, classification)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'generated', %s, %s, 'CUI')""",
            (
                dossier_id,
                session_id,
                vertical.get("id", ""),
                title,
                dossier_content,
                executive_summary,
                len(signals),
                len(challenges),
                critical_count,
                notable_count,
                len(regulatory_maps),
                len(build_buy),
                round(avg_coverage, 4),
                overall_score,
                json.dumps(
                    {
                        "enhancement_count": enhancement_count,
                        "signal_source_count": len(set(s.get("source", "") for s in signals)),
                        "build_count": sum(1 for bb in build_buy if bb.get("recommendation") == "build"),
                        "buy_count": sum(1 for bb in build_buy if bb.get("recommendation") == "buy"),
                        "partner_count": sum(1 for bb in build_buy if bb.get("recommendation") == "partner"),
                    }
                ),
                now,
            ),
        )

        # 12. UPDATE research_sessions status and dossier_id
        conn.execute(
            """UPDATE research_sessions
               SET status = 'dossier_ready', dossier_id = %s, updated_at = %s
               WHERE id = %s""",
            (dossier_id, now, session_id),
        )
        conn.commit()

        _audit(
            "research.dossier.generated",
            f"Generated dossier {dossier_id} for session {session_id}",
            {
                "dossier_id": dossier_id,
                "session_id": session_id,
                "overall_score": overall_score,
                "challenge_count": len(challenges),
                "critical_count": critical_count,
                "notable_count": notable_count,
                "signal_count": len(signals),
            },
        )

        return {
            "dossier_id": dossier_id,
            "session_id": session_id,
            "title": title,
            "overall_opportunity_score": overall_score,
            "challenge_count": len(challenges),
            "critical_challenges": critical_count,
            "notable_challenges": notable_count,
            "signal_count": len(signals),
            "regulatory_mappings": len(regulatory_maps),
            "build_buy_analyses": len(build_buy),
            "capability_coverage": round(avg_coverage, 4),
            "enhancement_count": enhancement_count,
            "status": "generated",
            "generated_at": now,
        }
    finally:
        conn.close()


def get_dossier(dossier_id=None, session_id=None, db_path=None):
    """Retrieve dossier by ID or session.

    When querying by session_id, returns the latest dossier (most recent
    generated_at) for that session.

    Args:
        dossier_id: Optional dossier ID (rdoss-...).
        session_id: Optional session ID (rsess-...).
        db_path: Optional database path override.

    Returns:
        dict with full dossier data or error.
    """
    if not dossier_id and not session_id:
        return {"error": "Either --dossier-id or --session-id is required"}

    conn = _get_db(db_path)
    try:
        if dossier_id:
            row = conn.execute("SELECT * FROM research_dossiers WHERE id = %s", (dossier_id,)).fetchone()
        else:
            # Get latest dossier for session
            row = conn.execute(
                "SELECT * FROM research_dossiers WHERE session_id = %s ORDER BY generated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()

        if not row:
            target = dossier_id or session_id
            return {"error": f"Dossier not found for: {target}"}

        dossier = dict(row)

        return {
            "dossier_id": dossier["id"],
            "session_id": dossier["session_id"],
            "vertical_id": dossier["vertical_id"],
            "title": dossier["title"],
            "content": dossier["content"],
            "executive_summary": dossier["executive_summary"],
            "signal_count": dossier["signal_count"],
            "challenge_count": dossier["challenge_count"],
            "critical_challenges": dossier["critical_challenges"],
            "notable_challenges": dossier["notable_challenges"],
            "regulatory_mappings": dossier["regulatory_mappings"],
            "build_buy_analyses": dossier["build_buy_analyses"],
            "capability_coverage": dossier["capability_coverage"],
            "overall_opportunity_score": dossier["overall_opportunity_score"],
            "status": dossier["status"],
            "reviewer": dossier["reviewer"],
            "reviewed_at": dossier["reviewed_at"],
            "review_notes": dossier["review_notes"],
            "fitness_assessment_id": dossier["fitness_assessment_id"],
            "metadata": _safe_json_loads(dossier.get("metadata"), {}),
            "generated_at": dossier["generated_at"],
            "classification": dossier.get("classification", "CUI"),
        }
    finally:
        conn.close()


def list_dossiers(status=None, db_path=None):
    """List all dossiers with optional status filter.

    Args:
        status: Optional status filter.
        db_path: Optional database path override.

    Returns:
        dict with dossiers list and counts.
    """
    conn = _get_db(db_path)
    try:
        query = (
            "SELECT id, session_id, vertical_id, title, "
            "overall_opportunity_score, challenge_count, critical_challenges, "
            "notable_challenges, status, reviewer, reviewed_at, "
            "generated_at, classification "
            "FROM research_dossiers"
        )
        params = []

        if status:
            if status not in VALID_STATUSES:
                return {"error": f"Invalid status: {status}. Valid: {', '.join(VALID_STATUSES)}"}
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY generated_at DESC"

        rows = conn.execute(query, params).fetchall()

        dossiers = []
        for row in rows:
            dossiers.append(
                {
                    "dossier_id": row["id"],
                    "session_id": row["session_id"],
                    "vertical_id": row["vertical_id"],
                    "title": row["title"],
                    "overall_opportunity_score": row["overall_opportunity_score"],
                    "challenge_count": row["challenge_count"],
                    "critical_challenges": row["critical_challenges"],
                    "notable_challenges": row["notable_challenges"],
                    "status": row["status"],
                    "reviewer": row["reviewer"],
                    "reviewed_at": row["reviewed_at"],
                    "generated_at": row["generated_at"],
                }
            )

        # Counts by status
        count_rows = conn.execute("SELECT status, COUNT(*) as cnt FROM research_dossiers GROUP BY status").fetchall()
        counts = {r["status"]: r["cnt"] for r in count_rows}

        return {
            "dossiers": dossiers,
            "returned": len(dossiers),
            "total": sum(counts.values()),
            "filter_status": status,
            "counts_by_status": counts,
        }
    finally:
        conn.close()


def review_dossier(dossier_id, reviewer, status, review_notes=None, db_path=None):
    """Review a dossier by inserting a new row with updated status.

    Because research_dossiers is append-only (D6/D-RES-5), status transitions
    are performed by INSERTing a new row with the same content but updated
    status, reviewer, and review fields. The latest row for a session
    (by generated_at) represents the current state.

    Args:
        dossier_id: The dossier ID to review (rdoss-...).
        reviewer: Reviewer identity string.
        status: New status (reviewed, approved, rejected).
        review_notes: Optional review notes.
        db_path: Optional database path override.

    Returns:
        dict with new dossier version data or error.
    """
    valid_review_statuses = ("reviewed", "approved", "rejected")
    if status not in valid_review_statuses:
        return {"error": f"Invalid review status: {status}. Valid: {', '.join(valid_review_statuses)}"}

    conn = _get_db(db_path)
    try:
        # Fetch original dossier
        row = conn.execute("SELECT * FROM research_dossiers WHERE id = %s", (dossier_id,)).fetchone()
        if not row:
            return {"error": f"Dossier not found: {dossier_id}"}

        original = dict(row)

        # INSERT new row with updated status (append-only)
        new_id = _dossier_id()
        now = _now()

        conn.execute(
            """INSERT INTO research_dossiers
            (id, session_id, vertical_id, title, content, executive_summary,
             signal_count, challenge_count, critical_challenges, notable_challenges,
             regulatory_mappings, build_buy_analyses, capability_coverage,
             overall_opportunity_score, status, reviewer, reviewed_at, review_notes,
             fitness_assessment_id, metadata, generated_at, classification)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI')""",
            (
                new_id,
                original["session_id"],
                original["vertical_id"],
                original["title"],
                original["content"],
                original["executive_summary"],
                original["signal_count"],
                original["challenge_count"],
                original["critical_challenges"],
                original["notable_challenges"],
                original["regulatory_mappings"],
                original["build_buy_analyses"],
                original["capability_coverage"],
                original["overall_opportunity_score"],
                status,
                reviewer,
                now,
                review_notes,
                original["fitness_assessment_id"],
                json.dumps(
                    {
                        **_safe_json_loads(original.get("metadata"), {}),
                        "reviewed_from": dossier_id,
                        "review_action": status,
                    }
                ),
                now,
            ),
        )

        # Update session status if approved
        if status == "approved":
            conn.execute(
                """UPDATE research_sessions
                   SET status = 'reviewed', dossier_id = %s, updated_at = %s
                   WHERE id = %s""",
                (new_id, now, original["session_id"]),
            )

        conn.commit()

        _audit(
            "research.dossier.reviewed",
            f"Dossier {dossier_id} reviewed as {status} by {reviewer}",
            {
                "original_dossier_id": dossier_id,
                "new_dossier_id": new_id,
                "reviewer": reviewer,
                "status": status,
                "session_id": original["session_id"],
            },
        )

        return {
            "dossier_id": new_id,
            "original_dossier_id": dossier_id,
            "session_id": original["session_id"],
            "title": original["title"],
            "overall_opportunity_score": original["overall_opportunity_score"],
            "status": status,
            "reviewer": reviewer,
            "reviewed_at": now,
            "review_notes": review_notes,
        }
    finally:
        conn.close()


def trigger_fitness(dossier_id, db_path=None):
    """Mark an approved dossier as triggering fitness assessment.

    Inserts a new dossier row with status='child_app_triggered' and
    updates the session status accordingly.

    Args:
        dossier_id: The approved dossier ID (rdoss-...).
        db_path: Optional database path override.

    Returns:
        dict with fitness trigger info or error.
    """
    conn = _get_db(db_path)
    try:
        # Fetch the dossier
        row = conn.execute("SELECT * FROM research_dossiers WHERE id = %s", (dossier_id,)).fetchone()
        if not row:
            return {"error": f"Dossier not found: {dossier_id}"}

        original = dict(row)

        if original["status"] not in ("approved", "reviewed"):
            return {
                "error": f"Dossier must be approved or reviewed to trigger fitness. "
                f"Current status: {original['status']}"
            }

        # INSERT new row with child_app_triggered status
        new_id = _dossier_id()
        now = _now()

        conn.execute(
            """INSERT INTO research_dossiers
            (id, session_id, vertical_id, title, content, executive_summary,
             signal_count, challenge_count, critical_challenges, notable_challenges,
             regulatory_mappings, build_buy_analyses, capability_coverage,
             overall_opportunity_score, status, reviewer, reviewed_at, review_notes,
             fitness_assessment_id, metadata, generated_at, classification)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'child_app_triggered',
                    %s, %s, %s, %s, %s, %s, 'CUI')""",
            (
                new_id,
                original["session_id"],
                original["vertical_id"],
                original["title"],
                original["content"],
                original["executive_summary"],
                original["signal_count"],
                original["challenge_count"],
                original["critical_challenges"],
                original["notable_challenges"],
                original["regulatory_mappings"],
                original["build_buy_analyses"],
                original["capability_coverage"],
                original["overall_opportunity_score"],
                original["reviewer"],
                original["reviewed_at"],
                original["review_notes"],
                None,  # fitness_assessment_id will be populated by caller
                json.dumps(
                    {
                        **_safe_json_loads(original.get("metadata"), {}),
                        "triggered_from": dossier_id,
                        "trigger_action": "child_app_triggered",
                    }
                ),
                now,
            ),
        )

        # Update session status
        conn.execute(
            """UPDATE research_sessions
               SET status = 'child_app_triggered', dossier_id = %s, updated_at = %s
               WHERE id = %s""",
            (new_id, now, original["session_id"]),
        )
        conn.commit()

        _audit(
            "research.dossier.fitness_triggered",
            f"Dossier {dossier_id} triggered fitness assessment",
            {
                "original_dossier_id": dossier_id,
                "new_dossier_id": new_id,
                "session_id": original["session_id"],
                "overall_score": original["overall_opportunity_score"],
            },
        )

        return {
            "dossier_id": new_id,
            "original_dossier_id": dossier_id,
            "session_id": original["session_id"],
            "title": original["title"],
            "overall_opportunity_score": original["overall_opportunity_score"],
            "status": "child_app_triggered",
            "triggered_at": now,
            "hint": "Run agentic_fitness.py with dossier challenges as spec input",
        }
    finally:
        conn.close()


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _print_human(action, result):
    """Format output for human-readable terminal display."""
    print("=" * 70)
    print("  DOSSIER GENERATOR -- CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 70)
        return

    if action == "generate":
        print(f"\n  Dossier Generated: {result.get('dossier_id')}")
        print(f"  Session: {result.get('session_id')}")
        print(f"  Title: {result.get('title')}")
        print(f"  Overall Score: {result.get('overall_opportunity_score', 0):.2f}")
        print(
            f"  Challenges: {result.get('challenge_count', 0)} "
            f"({result.get('critical_challenges', 0)} critical, "
            f"{result.get('notable_challenges', 0)} notable)"
        )
        print(f"  Signals: {result.get('signal_count', 0)}")
        print(f"  Regulatory Mappings: {result.get('regulatory_mappings', 0)}")
        print(f"  Build/Buy Analyses: {result.get('build_buy_analyses', 0)}")
        print(f"  Capability Coverage: {result.get('capability_coverage', 0):.1%}")
        print(f"  Enhancements Needed: {result.get('enhancement_count', 0)}")
        print(f"  Status: {result.get('status')}")

    elif action == "get":
        print(f"\n  Dossier: {result.get('dossier_id')}")
        print(f"  Session: {result.get('session_id')}")
        print(f"  Title: {result.get('title')}")
        print(f"  Overall Score: {result.get('overall_opportunity_score', 0):.2f}")
        print(f"  Status: {result.get('status')}")
        if result.get("reviewer"):
            print(f"  Reviewer: {result.get('reviewer')}")
            print(f"  Reviewed: {result.get('reviewed_at')}")
        if result.get("review_notes"):
            print(f"  Notes: {result.get('review_notes')}")
        print("")
        print("-" * 70)
        print("  DOSSIER CONTENT:")
        print("-" * 70)
        print(result.get("content", ""))

    elif action == "list":
        dossiers = result.get("dossiers", [])
        counts = result.get("counts_by_status", {})
        print(f"\n  Dossiers: {result.get('returned', 0)} of {result.get('total', 0)}")
        if counts:
            counts_str = ", ".join(f"{k}={v}" for k, v in counts.items())
            print(f"  Status distribution: {counts_str}")
        print("-" * 70)
        print(f"    {'ID':20s} {'Score':6s} {'Challenges':11s} {'Status':20s} {'Generated':20s}")
        print(f"    {'-' * 20} {'-' * 6} {'-' * 11} {'-' * 20} {'-' * 20}")
        for d in dossiers:
            score = d.get("overall_opportunity_score", 0.0)
            chal = d.get("challenge_count", 0)
            print(
                f"    {d['dossier_id']:20s} {score:5.2f} {chal:11d} {d['status']:20s} {d.get('generated_at', ''):20s}"
            )
            print(f"      Title: {d.get('title', '')[:60]}")
            print("")

    elif action == "review":
        print("\n  Dossier Reviewed")
        print("-" * 70)
        print(f"  New Dossier ID: {result.get('dossier_id')}")
        print(f"  Original ID:    {result.get('original_dossier_id')}")
        print(f"  Status:         {result.get('status')}")
        print(f"  Reviewer:       {result.get('reviewer')}")
        print(f"  Reviewed At:    {result.get('reviewed_at')}")
        if result.get("review_notes"):
            print(f"  Notes:          {result.get('review_notes')}")

    elif action == "trigger_fitness":
        print("\n  Fitness Assessment Triggered")
        print("-" * 70)
        print(f"  New Dossier ID: {result.get('dossier_id')}")
        print(f"  Original ID:    {result.get('original_dossier_id')}")
        print(f"  Session:        {result.get('session_id')}")
        print(f"  Status:         {result.get('status')}")
        print(f"  Overall Score:  {result.get('overall_opportunity_score', 0):.2f}")
        if result.get("hint"):
            print(f"  Hint:           {result.get('hint')}")

    print()
    print("=" * 70)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Dossier Generator -- template-based research dossier generation (CUI // SP-CTI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
        "  %(prog)s --generate --session-id rsess-abc123 --json\n"
        "  %(prog)s --get --dossier-id rdoss-abc123 --json\n"
        "  %(prog)s --get --session-id rsess-abc123 --json\n"
        "  %(prog)s --list --status approved --json\n"
        "  %(prog)s --review --dossier-id rdoss-abc123 --reviewer analyst@mil --status reviewed --json\n"
        "  %(prog)s --trigger-fitness --dossier-id rdoss-abc123 --json\n",
    )

    # Actions
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--generate", action="store_true", help="Generate dossier for a session")
    actions.add_argument("--get", action="store_true", help="Get dossier by ID or session ID")
    actions.add_argument("--list", action="store_true", help="List all dossiers")
    actions.add_argument("--review", action="store_true", help="Review a dossier (append new status row)")
    actions.add_argument(
        "--trigger-fitness", action="store_true", help="Trigger fitness assessment from approved dossier"
    )

    # Parameters
    parser.add_argument(
        "--session-id", type=str, default=None, help="Session ID (required for --generate, optional for --get)"
    )
    parser.add_argument(
        "--dossier-id", type=str, default=None, help="Dossier ID (for --get, --review, --trigger-fitness)"
    )
    parser.add_argument("--reviewer", type=str, default=None, help="Reviewer identity (required for --review)")
    parser.add_argument(
        "--status", type=str, default=None, help="Status for --review (reviewed/approved/rejected) or filter for --list"
    )
    parser.add_argument("--review-notes", type=str, default=None, help="Optional review notes (for --review)")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=str, default=None, help="Override database path")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.generate:
            if not args.session_id:
                result = {"error": "--session-id is required for --generate"}
                action = "generate"
            else:
                result = generate_dossier(args.session_id, db_path)
                action = "generate"

        elif args.get:
            result = get_dossier(
                dossier_id=args.dossier_id,
                session_id=args.session_id,
                db_path=db_path,
            )
            action = "get"

        elif args.list:
            result = list_dossiers(status=args.status, db_path=db_path)
            action = "list"

        elif args.review:
            if not args.dossier_id:
                result = {"error": "--dossier-id is required for --review"}
            elif not args.reviewer:
                result = {"error": "--reviewer is required for --review"}
            elif not args.status:
                result = {"error": "--status is required for --review (reviewed/approved/rejected)"}
            else:
                result = review_dossier(
                    dossier_id=args.dossier_id,
                    reviewer=args.reviewer,
                    status=args.status,
                    review_notes=args.review_notes,
                    db_path=db_path,
                )
            action = "review"

        elif args.trigger_fitness:
            if not args.dossier_id:
                result = {"error": "--dossier-id is required for --trigger-fitness"}
            else:
                result = trigger_fitness(args.dossier_id, db_path)
            action = "trigger_fitness"

        else:
            result = {"error": "No action specified"}
            action = "unknown"

        if args.human:
            _print_human(action, result)
        else:
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as exc:
        err = {"error": str(exc)}
        if args.json or not args.human:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        err = {"error": f"Unexpected error: {exc}"}
        if args.json or not args.human:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
