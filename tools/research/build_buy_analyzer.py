#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Build/Buy/Partner Decision Matrix Analyzer for ICDEV™ Research Engine.

Computes deterministic build, buy, and partner scores for each research
challenge, then recommends a strategy (build, buy, partner, or hybrid).
Follows the D21 deterministic weighted-average scoring pattern and the
Creative Engine tool conventions (D-RES-8).

Three factor groups, each with sub-factor weights:

  BUILD  — icdev_coverage (0.30), customization_need (0.25),
           compliance_control (0.25), long_term_cost (0.20)
  BUY    — time_to_market (0.30), maturity (0.25),
           support_ecosystem (0.25), integration_ease (0.20)
  PARTNER — domain_expertise (0.30), shared_risk (0.25),
            market_access (0.25), ip_preservation (0.20)

Decision logic:
  - Clear recommendation if top score >= 0.70 and exceeds others by 0.15
  - Hybrid if top two scores are within 0.15 of each other
  - Otherwise highest score wins

Architecture:
    - Weights loaded from args/research_config.yaml under build_buy.* (D26 pattern)
    - Scores clamped to [0.0, 1.0], rounded to 4 decimal places
    - research_build_buy table is append-only (D6)
    - All decisions are audit-logged (D6 append-only audit trail)
    - Parameterized SQL throughout (no string interpolation)

Usage:
    python tools/research/build_buy_analyzer.py --analyze --session-id rsess-xxx --json
    python tools/research/build_buy_analyzer.py --analyze-one --challenge-id rchal-xxx --session-id rsess-xxx --json
    python tools/research/build_buy_analyzer.py --matrix --session-id rsess-xxx --json
    python tools/research/build_buy_analyzer.py --matrix --session-id rsess-xxx --human
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
# DEFAULT CONFIGURATION
# =========================================================================
DEFAULT_BUILD_FACTORS = {
    "icdev_coverage": 0.30,
    "customization_need": 0.25,
    "compliance_control": 0.25,
    "long_term_cost": 0.20,
}

DEFAULT_BUY_FACTORS = {
    "time_to_market": 0.30,
    "maturity": 0.25,
    "support_ecosystem": 0.25,
    "integration_ease": 0.20,
}

DEFAULT_PARTNER_FACTORS = {
    "domain_expertise": 0.30,
    "shared_risk": 0.25,
    "market_access": 0.25,
    "ip_preservation": 0.20,
}

# Keywords that indicate compliance/regulatory category relevance
COMPLIANCE_KEYWORDS = frozenset(
    {
        "compliance",
        "regulatory",
        "regulation",
        "audit",
        "nist",
        "fedramp",
        "cmmc",
        "hipaa",
        "cjis",
        "pci",
        "soc2",
        "iso27001",
        "ato",
        "stig",
        "fips",
        "cui",
        "classified",
        "authorization",
        "accreditation",
        "governance",
        "policy",
        "framework",
        "mandate",
    }
)

# Keywords that indicate integration ease in commercial signals
INTEGRATION_KEYWORDS = frozenset(
    {
        "api",
        "sdk",
        "plugin",
        "integration",
        "connector",
        "webhook",
        "rest",
        "graphql",
        "openapi",
        "swagger",
        "grpc",
        "library",
    }
)


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


def _bb_id():
    """Generate a build/buy analysis ID with rbb- prefix."""
    return f"rbb-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (best-effort, never raises)."""
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
    """Load research config from YAML with fallback defaults."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _clamp(score):
    """Clamp a score to [0.0, 1.0] and round to 4 decimal places."""
    return round(max(0.0, min(1.0, score)), 4)


def _get_weights(config, group_key, defaults):
    """Extract factor weights from config, normalizing to sum 1.0."""
    bb_config = config.get("build_buy", {})
    raw = bb_config.get(group_key, {})
    result = {}
    for factor, default_val in defaults.items():
        result[factor] = float(raw.get(factor, default_val))
    # Normalize
    total = sum(result.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        result = {k: v / total for k, v in result.items()}
    return result


def _parse_json_field(raw, fallback=None):
    """Safely parse a JSON text field. Returns fallback on failure."""
    if fallback is None:
        fallback = []
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback


# =========================================================================
# BUILD SCORE
# =========================================================================
def _compute_build_score(challenge, cap_coverage, signals, config):
    """Compute deterministic build score from 4 sub-factors.

    Args:
        challenge: Dict of challenge row from DB.
        cap_coverage: Float average coverage_score from research_capability_map.
        signals: List of signal dicts for the session.
        config: Loaded YAML config dict.

    Returns:
        Tuple of (overall_score, breakdown_dict).
    """
    weights = _get_weights(config, "build_factors", DEFAULT_BUILD_FACTORS)

    # 1. icdev_coverage: directly from capability map average
    icdev_coverage = _clamp(cap_coverage)

    # 2. customization_need: fewer existing solutions = more need to build
    total_signals = max(len(signals), 1)
    existing_solution_count = sum(1 for s in signals if s.get("source") == "saas_commercial")
    customization_need = _clamp(1.0 - (existing_solution_count / total_signals))

    # 3. compliance_control: compliance categories get high scores
    category = (challenge.get("category") or "other").lower()
    keywords = _parse_json_field(challenge.get("keywords"), [])
    keywords_lower = {k.lower() for k in keywords if isinstance(k, str)}
    title_lower = (challenge.get("title") or "").lower()
    desc_lower = (challenge.get("description") or "").lower()
    all_text = keywords_lower | {title_lower} | {desc_lower}

    if category in ("compliance", "governance"):
        compliance_control = 1.0
    elif COMPLIANCE_KEYWORDS & all_text:
        compliance_control = 0.7
    else:
        compliance_control = 0.5

    # 4. long_term_cost: building is typically cheaper long-term; adjust by effort
    #    Higher coverage means less effort, so lower long-term cost is better (higher score)
    long_term_cost = _clamp(0.6 + 0.4 * cap_coverage)

    breakdown = {
        "icdev_coverage": icdev_coverage,
        "customization_need": customization_need,
        "compliance_control": _clamp(compliance_control),
        "long_term_cost": _clamp(long_term_cost),
    }

    overall = sum(breakdown[f] * weights.get(f, 0.0) for f in breakdown)
    return _clamp(overall), breakdown


# =========================================================================
# BUY SCORE
# =========================================================================
def _compute_buy_score(challenge, signals, config):
    """Compute deterministic buy score from 4 sub-factors.

    Args:
        challenge: Dict of challenge row from DB.
        signals: List of signal dicts for the session.
        config: Loaded YAML config dict.

    Returns:
        Tuple of (overall_score, breakdown_dict).
    """
    weights = _get_weights(config, "buy_factors", DEFAULT_BUY_FACTORS)

    # 1. time_to_market: based on commercial solution availability
    max_expected = max(config.get("build_buy", {}).get("max_expected_commercial", 10), 1)
    commercial_count = sum(1 for s in signals if s.get("source") == "saas_commercial")
    time_to_market = _clamp(commercial_count / max_expected)

    # 2. maturity: based on review ratings, stars, citations
    ratings = []
    for s in signals:
        if s.get("source") in ("saas_commercial", "review_site"):
            meta = _parse_json_field(s.get("metadata"), {})
            if isinstance(meta, dict):
                rating = meta.get("rating") or meta.get("stars")
                if rating is not None:
                    try:
                        ratings.append(float(rating))
                    except (ValueError, TypeError):
                        pass
    if ratings:
        # Normalize average rating (assume 5-star scale)
        avg_rating = sum(ratings) / len(ratings)
        maturity = _clamp(avg_rating / 5.0)
    else:
        # No rating data available -- moderate default
        maturity = 0.4

    # 3. support_ecosystem: documentation signals, community size
    doc_count = sum(1 for s in signals if s.get("source_type") in ("blog", "news_article", "analyst_report"))
    community_count = sum(1 for s in signals if s.get("source") in ("community_forum", "open_source"))
    ecosystem_raw = (doc_count + community_count) / max(len(signals), 1)
    support_ecosystem = _clamp(min(ecosystem_raw * 5.0, 1.0))

    # 4. integration_ease: keyword presence of integration-related terms
    keywords = _parse_json_field(challenge.get("keywords"), [])
    title = (challenge.get("title") or "").lower()
    desc = (challenge.get("description") or "").lower()
    all_text = " ".join([title, desc] + [k.lower() for k in keywords if isinstance(k, str)])
    integration_hits = sum(1 for kw in INTEGRATION_KEYWORDS if kw in all_text)
    integration_ease = _clamp(min(integration_hits / 3.0, 1.0))

    breakdown = {
        "time_to_market": time_to_market,
        "maturity": maturity,
        "support_ecosystem": support_ecosystem,
        "integration_ease": integration_ease,
    }

    overall = sum(breakdown[f] * weights.get(f, 0.0) for f in breakdown)
    return _clamp(overall), breakdown


# =========================================================================
# PARTNER SCORE
# =========================================================================
def _compute_partner_score(challenge, signals, config):
    """Compute deterministic partner score from 4 sub-factors.

    Args:
        challenge: Dict of challenge row from DB.
        signals: List of signal dicts for the session.
        config: Loaded YAML config dict.

    Returns:
        Tuple of (overall_score, breakdown_dict).
    """
    weights = _get_weights(config, "partner_factors", DEFAULT_PARTNER_FACTORS)

    # 1. domain_expertise: based on academic papers and patents
    academic_count = sum(1 for s in signals if s.get("source") == "academic_paper")
    patent_count = sum(1 for s in signals if s.get("source") == "patent")
    expertise_raw = (academic_count + patent_count) / max(len(signals), 1)
    domain_expertise = _clamp(min(expertise_raw * 5.0, 1.0))

    # 2. shared_risk: partnerships share risk -- moderate default
    shared_risk = 0.6

    # 3. market_access: open source community signals indicate market reach
    oss_count = sum(1 for s in signals if s.get("source") == "open_source")
    community_count = sum(1 for s in signals if s.get("source") == "community_forum")
    market_raw = (oss_count + community_count) / max(len(signals), 1)
    market_access = _clamp(min(market_raw * 5.0, 1.0))

    # 4. ip_preservation: partnerships typically split IP -- moderate default
    ip_preservation = 0.5

    breakdown = {
        "domain_expertise": domain_expertise,
        "shared_risk": shared_risk,
        "market_access": market_access,
        "ip_preservation": ip_preservation,
    }

    overall = sum(breakdown[f] * weights.get(f, 0.0) for f in breakdown)
    return _clamp(overall), breakdown


# =========================================================================
# DECISION LOGIC
# =========================================================================
def _determine_recommendation(build_score, buy_score, partner_score, config):
    """Determine recommendation from the three scores.

    Decision rules:
      - If top score >= clear_threshold (0.70) and exceeds others by margin (0.15):
        clear single recommendation
      - If top two within margin of each other: 'hybrid'
      - Otherwise: highest score wins

    Args:
        build_score: Float build score.
        buy_score: Float buy score.
        partner_score: Float partner score.
        config: Loaded YAML config dict.

    Returns:
        String: 'build', 'buy', 'partner', or 'hybrid'.
    """
    bb_config = config.get("build_buy", {})
    clear_threshold = float(bb_config.get("clear_threshold", 0.70))
    margin = float(bb_config.get("margin", 0.15))

    scores = [
        ("build", build_score),
        ("buy", buy_score),
        ("partner", partner_score),
    ]
    scores.sort(key=lambda x: x[1], reverse=True)

    top_name, top_score = scores[0]
    second_name, second_score = scores[1]

    # Clear winner: high score with sufficient margin
    if top_score >= clear_threshold and (top_score - second_score) >= margin:
        return top_name

    # Top two are close: hybrid
    if (top_score - second_score) < margin:
        return "hybrid"

    # Otherwise: highest score wins
    return top_name


# =========================================================================
# EFFORT / COST / RISK ESTIMATORS
# =========================================================================
def _estimate_effort(challenge, cap_coverage):
    """Estimate effort (S/M/L/XL) based on ICDEV™ capability coverage.

    Args:
        challenge: Dict of challenge row from DB.
        cap_coverage: Float average coverage_score from capability map.

    Returns:
        String: 'S', 'M', 'L', or 'XL'.
    """
    if cap_coverage >= 0.8:
        return "S"
    elif cap_coverage >= 0.5:
        return "M"
    elif cap_coverage >= 0.2:
        return "L"
    else:
        return "XL"


def _estimate_cost_tier(effort, challenge):
    """Map effort + challenge complexity to cost tier.

    Args:
        effort: String effort estimate ('S', 'M', 'L', 'XL').
        challenge: Dict of challenge row from DB.

    Returns:
        String: 'low', 'medium', 'high', or 'very_high'.
    """
    complexity = float(challenge.get("technical_complexity") or 0.0)

    effort_base = {"S": 0, "M": 1, "L": 2, "XL": 3}.get(effort, 2)

    # Adjust by technical complexity
    if complexity >= 0.7:
        effort_base += 1

    tiers = {0: "low", 1: "medium", 2: "high", 3: "very_high"}
    return tiers.get(min(effort_base, 3), "high")


def _assess_risk(recommendation, cap_coverage, challenge):
    """Assess risk level based on recommendation and coverage.

    Args:
        recommendation: String recommendation ('build', 'buy', 'partner', 'hybrid').
        cap_coverage: Float average coverage_score from capability map.
        challenge: Dict of challenge row from DB.

    Returns:
        String: 'low', 'medium', 'high', or 'critical'.
    """
    if recommendation == "build":
        if cap_coverage >= 0.7:
            return "low"
        elif cap_coverage >= 0.4:
            return "medium"
        else:
            return "high"
    elif recommendation == "buy":
        # Vendor dependency is inherently medium risk
        return "medium"
    elif recommendation == "partner":
        return "medium"
    elif recommendation == "hybrid":
        return "medium"
    return "medium"


# =========================================================================
# ANALYSIS FUNCTIONS
# =========================================================================
def analyze_challenge(challenge_id, session_id, db_path=None):
    """Full build/buy/partner analysis for a single challenge.

    Gets the challenge from DB, retrieves capability mappings and signals,
    computes all three scores, determines recommendation, and inserts
    the result into research_build_buy (append-only).

    Args:
        challenge_id: The challenge ID (e.g., "rchal-abc123def456").
        session_id: The session ID this challenge belongs to.
        db_path: Optional database path override.

    Returns:
        Dict with full analysis results.
    """
    config = _load_config()
    conn = _get_db(db_path)
    try:
        # Get challenge
        challenge_row = conn.execute(
            "SELECT * FROM research_challenges WHERE id = %s AND session_id = %s",
            (challenge_id, session_id),
        ).fetchone()
        if not challenge_row:
            return {"error": f"Challenge not found: {challenge_id} in session {session_id}"}
        challenge = dict(challenge_row)

        # Get capability mappings for this challenge
        cap_rows = conn.execute(
            "SELECT * FROM research_capability_map WHERE challenge_id = %s AND session_id = %s",
            (challenge_id, session_id),
        ).fetchall()
        cap_mappings = [dict(r) for r in cap_rows]

        # Average coverage score from capability map
        if cap_mappings:
            cap_coverage = sum(float(c.get("coverage_score") or 0.0) for c in cap_mappings) / len(cap_mappings)
        else:
            cap_coverage = 0.0
        cap_coverage = _clamp(cap_coverage)

        # Get signals for the session
        sig_rows = conn.execute(
            "SELECT * FROM research_signals WHERE session_id = %s",
            (session_id,),
        ).fetchall()
        signals = [dict(r) for r in sig_rows]

        # Compute scores
        build_score, build_breakdown = _compute_build_score(challenge, cap_coverage, signals, config)
        buy_score, buy_breakdown = _compute_buy_score(challenge, signals, config)
        partner_score, partner_breakdown = _compute_partner_score(challenge, signals, config)

        # Determine recommendation
        recommendation = _determine_recommendation(build_score, buy_score, partner_score, config)

        # Effort, cost, risk
        effort = _estimate_effort(challenge, cap_coverage)
        cost_tier = _estimate_cost_tier(effort, challenge)
        risk_level = _assess_risk(recommendation, cap_coverage, challenge)

        # Collect existing commercial solutions from signals
        existing_solutions = []
        for s in signals:
            if s.get("source") == "saas_commercial":
                existing_solutions.append(
                    {
                        "title": s.get("title", ""),
                        "url": s.get("url", ""),
                        "source_type": s.get("source_type", ""),
                    }
                )

        # Build rationale strings
        build_rationale = (
            f"ICDEV™ capability coverage: {cap_coverage:.0%}. "
            f"Customization need: {build_breakdown['customization_need']:.2f}. "
            f"Compliance control: {build_breakdown['compliance_control']:.2f}."
        )
        buy_rationale = (
            f"Commercial solutions available: {len(existing_solutions)}. "
            f"Maturity: {buy_breakdown['maturity']:.2f}. "
            f"Integration ease: {buy_breakdown['integration_ease']:.2f}."
        )
        partner_rationale = (
            f"Domain expertise signals: {partner_breakdown['domain_expertise']:.2f}. "
            f"Market access: {partner_breakdown['market_access']:.2f}. "
            f"IP preservation: {partner_breakdown['ip_preservation']:.2f}."
        )

        # Full score breakdown
        score_breakdown = {
            "build": build_breakdown,
            "buy": buy_breakdown,
            "partner": partner_breakdown,
            "build_weights": _get_weights(config, "build_factors", DEFAULT_BUILD_FACTORS),
            "buy_weights": _get_weights(config, "buy_factors", DEFAULT_BUY_FACTORS),
            "partner_weights": _get_weights(config, "partner_factors", DEFAULT_PARTNER_FACTORS),
            "analyzed_at": _now(),
        }

        # INSERT into research_build_buy (append-only)
        bb_id = _bb_id()
        now = _now()
        conn.execute(
            """INSERT INTO research_build_buy
               (id, session_id, challenge_id, recommendation, build_score, buy_score,
                partner_score, build_rationale, buy_rationale, partner_rationale,
                existing_solutions, icdev_capability_coverage, estimated_effort,
                estimated_cost_tier, risk_level, score_breakdown, metadata,
                analyzed_at, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI')""",
            (
                bb_id,
                session_id,
                challenge_id,
                recommendation,
                build_score,
                buy_score,
                partner_score,
                build_rationale,
                buy_rationale,
                partner_rationale,
                json.dumps(existing_solutions),
                cap_coverage,
                effort,
                cost_tier,
                risk_level,
                json.dumps(score_breakdown),
                json.dumps({}),
                now,
            ),
        )
        conn.commit()

        _audit(
            "research.build_buy.analyzed",
            f"Analyzed challenge {challenge_id}: {recommendation} "
            f"(B={build_score:.4f} / U={buy_score:.4f} / P={partner_score:.4f})",
            {
                "bb_id": bb_id,
                "session_id": session_id,
                "challenge_id": challenge_id,
                "recommendation": recommendation,
                "build_score": build_score,
                "buy_score": buy_score,
                "partner_score": partner_score,
                "effort": effort,
                "cost_tier": cost_tier,
                "risk_level": risk_level,
            },
        )

        return {
            "bb_id": bb_id,
            "session_id": session_id,
            "challenge_id": challenge_id,
            "challenge_title": challenge.get("title", ""),
            "category": challenge.get("category", ""),
            "recommendation": recommendation,
            "build_score": build_score,
            "buy_score": buy_score,
            "partner_score": partner_score,
            "build_breakdown": build_breakdown,
            "buy_breakdown": buy_breakdown,
            "partner_breakdown": partner_breakdown,
            "build_rationale": build_rationale,
            "buy_rationale": buy_rationale,
            "partner_rationale": partner_rationale,
            "existing_solutions_count": len(existing_solutions),
            "icdev_capability_coverage": cap_coverage,
            "estimated_effort": effort,
            "estimated_cost_tier": cost_tier,
            "risk_level": risk_level,
            "analyzed_at": now,
        }

    finally:
        conn.close()


def analyze_all(session_id, db_path=None):
    """Analyze all scored challenges in a session.

    Fetches all challenges with status in ('scored', 'mapped') for the given
    session and runs the full build/buy analysis on each.

    Args:
        session_id: The session ID to analyze.
        db_path: Optional database path override.

    Returns:
        Dict with analyzed count, skipped count, summary.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT id FROM research_challenges
               WHERE session_id = %s AND status IN ('scored', 'mapped')
               ORDER BY composite_score DESC""",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "analyzed": 0,
            "skipped": 0,
            "session_id": session_id,
            "results": [],
            "summary": {"build": 0, "buy": 0, "partner": 0, "hybrid": 0},
            "analyzed_at": _now(),
        }

    analyzed_count = 0
    skipped_count = 0
    results = []
    summary = {"build": 0, "buy": 0, "partner": 0, "hybrid": 0}

    for row in rows:
        cid = row["id"]
        try:
            result = analyze_challenge(cid, session_id, db_path=db_path)
            if "error" in result:
                skipped_count += 1
                continue
            analyzed_count += 1
            rec = result.get("recommendation", "hybrid")
            summary[rec] = summary.get(rec, 0) + 1
            results.append(
                {
                    "bb_id": result["bb_id"],
                    "challenge_id": result["challenge_id"],
                    "challenge_title": result["challenge_title"],
                    "category": result["category"],
                    "recommendation": rec,
                    "build_score": result["build_score"],
                    "buy_score": result["buy_score"],
                    "partner_score": result["partner_score"],
                    "effort": result["estimated_effort"],
                    "risk_level": result["risk_level"],
                }
            )
        except Exception:
            skipped_count += 1

    _audit(
        "research.build_buy.batch",
        f"Batch analyzed {analyzed_count} challenges in session {session_id} ({skipped_count} skipped)",
        {
            "session_id": session_id,
            "analyzed": analyzed_count,
            "skipped": skipped_count,
            "summary": summary,
        },
    )

    return {
        "analyzed": analyzed_count,
        "skipped": skipped_count,
        "session_id": session_id,
        "results": results,
        "summary": summary,
        "analyzed_at": _now(),
    }


def get_decision_matrix(session_id, db_path=None):
    """Get the full build/buy/partner decision matrix for a session.

    Retrieves all analyses from research_build_buy for the session and
    returns a summary with counts per recommendation type.

    Args:
        session_id: The session ID to query.
        db_path: Optional database path override.

    Returns:
        Dict with matrix entries, summary counts, and averages.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT bb.*, c.title as challenge_title, c.category as challenge_category,
                      c.severity as challenge_severity
               FROM research_build_buy bb
               JOIN research_challenges c ON bb.challenge_id = c.id
               WHERE bb.session_id = %s
               ORDER BY bb.build_score DESC""",
            (session_id,),
        ).fetchall()

        if not rows:
            return {
                "session_id": session_id,
                "total": 0,
                "entries": [],
                "summary": {"build": 0, "buy": 0, "partner": 0, "hybrid": 0},
                "avg_build_score": 0.0,
                "avg_buy_score": 0.0,
                "avg_partner_score": 0.0,
                "queried_at": _now(),
            }

        entries = []
        summary = {"build": 0, "buy": 0, "partner": 0, "hybrid": 0}
        build_scores = []
        buy_scores = []
        partner_scores = []

        for row in rows:
            r = dict(row)
            rec = r.get("recommendation", "hybrid")
            summary[rec] = summary.get(rec, 0) + 1
            build_scores.append(float(r.get("build_score") or 0.0))
            buy_scores.append(float(r.get("buy_score") or 0.0))
            partner_scores.append(float(r.get("partner_score") or 0.0))

            entries.append(
                {
                    "bb_id": r["id"],
                    "challenge_id": r["challenge_id"],
                    "challenge_title": r.get("challenge_title", ""),
                    "challenge_category": r.get("challenge_category", ""),
                    "challenge_severity": r.get("challenge_severity", ""),
                    "recommendation": rec,
                    "build_score": round(float(r.get("build_score") or 0.0), 4),
                    "buy_score": round(float(r.get("buy_score") or 0.0), 4),
                    "partner_score": round(float(r.get("partner_score") or 0.0), 4),
                    "icdev_capability_coverage": round(float(r.get("icdev_capability_coverage") or 0.0), 4),
                    "estimated_effort": r.get("estimated_effort", ""),
                    "estimated_cost_tier": r.get("estimated_cost_tier", ""),
                    "risk_level": r.get("risk_level", "medium"),
                    "existing_solutions": _parse_json_field(r.get("existing_solutions"), []),
                    "build_rationale": r.get("build_rationale", ""),
                    "buy_rationale": r.get("buy_rationale", ""),
                    "partner_rationale": r.get("partner_rationale", ""),
                    "analyzed_at": r.get("analyzed_at", ""),
                }
            )

        n = max(len(rows), 1)
        return {
            "session_id": session_id,
            "total": len(rows),
            "entries": entries,
            "summary": summary,
            "avg_build_score": round(sum(build_scores) / n, 4),
            "avg_buy_score": round(sum(buy_scores) / n, 4),
            "avg_partner_score": round(sum(partner_scores) / n, 4),
            "queried_at": _now(),
        }

    finally:
        conn.close()


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _print_human(args, result):
    """Print human-readable output for each command."""
    print("=" * 78)
    print("  ICDEV™ Research Engine -- Build/Buy/Partner Analyzer -- CUI // SP-CTI")
    print("=" * 78)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 78)
        return

    if args.analyze_one:
        print(f"\n  Challenge: {result.get('challenge_title', '')[:60]}")
        print(f"  ID:        {result.get('challenge_id', '')}")
        print(f"  Category:  {result.get('category', '')}")
        print("-" * 78)
        print(f"  Recommendation:  {result.get('recommendation', '').upper()}")
        print(f"  Build Score:     {result.get('build_score', 0):.4f}")
        print(f"  Buy Score:       {result.get('buy_score', 0):.4f}")
        print(f"  Partner Score:   {result.get('partner_score', 0):.4f}")
        print(f"  ICDEV™ Coverage:  {result.get('icdev_capability_coverage', 0):.0%}")
        print(f"  Effort:          {result.get('estimated_effort', '')}")
        print(f"  Cost Tier:       {result.get('estimated_cost_tier', '')}")
        print(f"  Risk Level:      {result.get('risk_level', '')}")
        print()
        print("  Build Factors:")
        for k, v in result.get("build_breakdown", {}).items():
            bar = "#" * int(v * 20)
            print(f"    {k:22s} {v:.4f}  |{bar:<20s}|")
        print("  Buy Factors:")
        for k, v in result.get("buy_breakdown", {}).items():
            bar = "#" * int(v * 20)
            print(f"    {k:22s} {v:.4f}  |{bar:<20s}|")
        print("  Partner Factors:")
        for k, v in result.get("partner_breakdown", {}).items():
            bar = "#" * int(v * 20)
            print(f"    {k:22s} {v:.4f}  |{bar:<20s}|")

    elif args.analyze:
        print(f"\n  Session: {result.get('session_id', '')}")
        print(f"  Analyzed:  {result.get('analyzed', 0)}")
        print(f"  Skipped:   {result.get('skipped', 0)}")
        summary = result.get("summary", {})
        print(
            f"  Summary:   Build={summary.get('build', 0)}  "
            f"Buy={summary.get('buy', 0)}  "
            f"Partner={summary.get('partner', 0)}  "
            f"Hybrid={summary.get('hybrid', 0)}"
        )
        print()
        entries = result.get("results", [])
        if entries:
            print(
                f"    {'#':>3s}  {'Recommendation':>14s}  {'Build':>6s}  "
                f"{'Buy':>6s}  {'Partner':>7s}  {'Effort':>6s}  "
                f"{'Risk':>8s}  Title"
            )
            print(
                f"    {'---':>3s}  {'-' * 14:>14s}  {'-' * 6:>6s}  "
                f"{'-' * 6:>6s}  {'-' * 7:>7s}  {'-' * 6:>6s}  "
                f"{'-' * 8:>8s}  -----"
            )
            for i, e in enumerate(entries, 1):
                print(
                    f"    {i:3d}  {e['recommendation']:>14s}  "
                    f"{e['build_score']:6.4f}  {e['buy_score']:6.4f}  "
                    f"{e['partner_score']:7.4f}  {e.get('effort', ''):>6s}  "
                    f"{e.get('risk_level', ''):>8s}  "
                    f"{e.get('challenge_title', '')[:35]}"
                )

    elif args.matrix:
        print(f"\n  Session: {result.get('session_id', '')}")
        print(f"  Total Analyses: {result.get('total', 0)}")
        summary = result.get("summary", {})
        print(
            f"  Summary:  Build={summary.get('build', 0)}  "
            f"Buy={summary.get('buy', 0)}  "
            f"Partner={summary.get('partner', 0)}  "
            f"Hybrid={summary.get('hybrid', 0)}"
        )
        print(f"  Avg Build:   {result.get('avg_build_score', 0):.4f}")
        print(f"  Avg Buy:     {result.get('avg_buy_score', 0):.4f}")
        print(f"  Avg Partner: {result.get('avg_partner_score', 0):.4f}")
        print()
        entries = result.get("entries", [])
        if entries:
            print(
                f"    {'#':>3s}  {'Rec':>7s}  {'Build':>6s}  "
                f"{'Buy':>6s}  {'Partn':>6s}  {'Covg':>5s}  "
                f"{'Eff':>3s}  {'Cost':>9s}  {'Risk':>8s}  Title"
            )
            sep_line = (
                f"    {'---':>3s}  {'-' * 7:>7s}  {'-' * 6:>6s}  "
                f"{'-' * 6:>6s}  {'-' * 6:>6s}  {'-' * 5:>5s}  "
                f"{'-' * 3:>3s}  {'-' * 9:>9s}  {'-' * 8:>8s}  -----"
            )
            print(sep_line)
            for i, e in enumerate(entries, 1):
                covg = e.get("icdev_capability_coverage", 0)
                print(
                    f"    {i:3d}  {e['recommendation']:>7s}  "
                    f"{e['build_score']:6.4f}  {e['buy_score']:6.4f}  "
                    f"{e['partner_score']:6.4f}  {covg:5.0%}  "
                    f"{e.get('estimated_effort', ''):>3s}  "
                    f"{e.get('estimated_cost_tier', ''):>9s}  "
                    f"{e.get('risk_level', ''):>8s}  "
                    f"{e.get('challenge_title', '')[:30]}"
                )

    print()
    print("=" * 78)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ICDEV™ Research Engine Build/Buy/Partner Analyzer -- CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --analyze --session-id rsess-abc123 --json\n"
            "  %(prog)s --analyze-one --challenge-id rchal-abc --session-id rsess-abc --json\n"
            "  %(prog)s --matrix --session-id rsess-abc123 --json\n"
            "  %(prog)s --matrix --session-id rsess-abc123 --human\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--analyze",
        action="store_true",
        help="Analyze all scored challenges in a session",
    )
    group.add_argument(
        "--analyze-one",
        action="store_true",
        help="Analyze a single challenge",
    )
    group.add_argument(
        "--matrix",
        action="store_true",
        help="Show the full decision matrix for a session",
    )

    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Session ID (required for all actions)",
    )
    parser.add_argument(
        "--challenge-id",
        type=str,
        default=None,
        help="Challenge ID (required for --analyze-one)",
    )

    args = parser.parse_args()

    try:
        if args.analyze:
            if not args.session_id:
                parser.error("--analyze requires --session-id")
            result = analyze_all(args.session_id, db_path=args.db_path)

        elif args.analyze_one:
            if not args.challenge_id:
                parser.error("--analyze-one requires --challenge-id")
            if not args.session_id:
                parser.error("--analyze-one requires --session-id")
            result = analyze_challenge(args.challenge_id, args.session_id, db_path=args.db_path)

        elif args.matrix:
            if not args.session_id:
                parser.error("--matrix requires --session-id")
            result = get_decision_matrix(args.session_id, db_path=args.db_path)

        else:
            result = {"error": "No action specified"}

        if args.human:
            _print_human(args, result)
        elif args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Default to JSON if neither --human nor --json specified
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except ValueError as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
