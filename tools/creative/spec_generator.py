#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Feature Spec Generator for ICDEV Creative Engine -- template-based spec generation from ranked gaps.

Transforms scored feature gaps and their associated pain points into structured,
template-based feature specifications with competitive analysis, acceptance criteria,
effort estimation, and persona targeting. No LLM required -- all generation is
deterministic and air-gap safe.

Architecture:
    - Reads from creative_feature_gaps and creative_pain_points tables
    - Builds specs using Python string template (D356, no LLM)
    - Effort estimation from config-driven category/area thresholds
    - BDD acceptance criteria generated per category
    - Stores results in creative_specs table (append-only, D6)
    - Audit trail for all generation events

Usage:
    python tools/creative/spec_generator.py --generate --feature-gap-id "fg-xxx" --json
    python tools/creative/spec_generator.py --generate-all --json
    python tools/creative/spec_generator.py --list --json
    python tools/creative/spec_generator.py --list --status generated --limit 20 --json
    python tools/creative/spec_generator.py --get --spec-id "cspec-xxx" --json
    python tools/creative/spec_generator.py --generate --feature-gap-id "fg-xxx" --human
"""

import argparse
import json
import os
import sqlite3
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

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "creative_config.yaml"

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
EFFORT_MAP = {
    "S": {"label": "Small", "days": "1-2"},
    "M": {"label": "Medium", "days": "3-5"},
    "L": {"label": "Large", "days": "5-10"},
    "XL": {"label": "Extra-Large", "days": "10-20"},
}

PERSONA_MAP = {
    "ux": "developer",
    "compliance": "isso",
    "pricing": "pm",
    "security": "isso",
    "api": "developer",
    "reporting": "pm",
    "integration": "developer",
    "automation": "developer",
    "performance": "developer",
    "scalability": "developer",
    "documentation": "pm",
    "onboarding": "pm",
    "customization": "pm",
    "support": "pm",
    "other": "developer",
}

PERSONA_DESCRIPTIONS = {
    "developer": "Software developer or engineer building features and integrations",
    "isso": "Information System Security Officer responsible for compliance and security posture",
    "pm": "Product/Project Manager overseeing delivery, pricing, and stakeholder communication",
}

VALID_STATUSES = ("generated", "reviewed", "approved", "building", "rejected")

# Category-specific acceptance criteria outcome templates
_CATEGORY_OUTCOMES = {
    "ux": [
        "the user interface renders without visual regressions",
        "page load time stays under 2 seconds",
        "WCAG 2.1 AA accessibility checks pass",
    ],
    "compliance": [
        "all NIST 800-53 control mappings are populated via crosswalk engine",
        "CUI markings are applied to generated artifacts",
        "the audit trail records each compliance event",
    ],
    "security": [
        "SAST scanning reports 0 critical/high findings",
        "secret detection finds no hardcoded credentials",
        "dependency audit shows 0 critical CVEs",
    ],
    "pricing": [
        "subscription tier limits are enforced correctly",
        "usage metering records are accurate",
        "billing events are audit-logged",
    ],
    "api": [
        "the API returns valid JSON with correct HTTP status codes",
        "rate limiting is enforced per tier",
        "OpenAPI spec is auto-generated and validates",
    ],
    "reporting": [
        "report data matches source query results",
        "CSV export contains all expected columns",
        "dashboard charts render with correct data",
    ],
    "integration": [
        "bidirectional sync completes without data loss",
        "webhook payloads match documented schema",
        "retry logic handles transient failures gracefully",
    ],
    "automation": [
        "automated workflow completes end-to-end without manual intervention",
        "scheduled triggers fire within the configured interval",
        "error recovery preserves intermediate outputs",
    ],
    "performance": [
        "P95 latency stays below the configured threshold",
        "concurrent requests do not cause resource exhaustion",
        "database queries execute within 100ms",
    ],
    "scalability": [
        "the system handles 10x normal load without degradation",
        "horizontal scaling adds capacity within 2 minutes",
        "per-tenant isolation is maintained under load",
    ],
    "documentation": [
        "all public APIs have corresponding documentation",
        "code examples execute without errors",
        "broken links are detected and flagged",
    ],
    "onboarding": [
        "new users complete setup in under 5 minutes",
        "the wizard provides contextual help at each step",
        "configuration defaults are sensible for the selected profile",
    ],
    "customization": [
        "custom configurations are persisted and reloaded correctly",
        "template overrides take precedence over defaults",
        "invalid configurations produce clear error messages",
    ],
    "support": [
        "support ticket is created with full context",
        "SLA timers track elapsed time accurately",
        "escalation triggers at the configured threshold",
    ],
    "other": [
        "the feature produces expected output with valid input",
        "error cases return structured error responses",
        "the audit trail records the event",
    ],
}


# =========================================================================
# SPEC TEMPLATE
# =========================================================================
_SPEC_TEMPLATE = """# Feature Spec: {title}

CUI // SP-CTI

## Problem Statement
{pain_point_description}

## Evidence
- **Pain Frequency:** {frequency} mentions across {signal_count} signals
- **Sources:** {sources}
- **Severity:** {severity}
- **Category:** {category}

## User Quotes (anonymized)
{quotes_section}

## Competitive Landscape
{competitive_analysis}

## Proposed Feature
**{feature_name}**

{feature_description}

## Justification
{justification}

## Composite Score: {composite_score:.2f}
| Dimension | Weight | Score |
|-----------|--------|-------|
| Pain Frequency | 0.40 | {pain_frequency_score:.2f} |
| Gap Uniqueness | 0.35 | {gap_uniqueness_score:.2f} |
| Effort to Impact | 0.25 | {effort_to_impact_score:.2f} |

## Target Persona
{target_persona}

## Competitive Advantage
{competitive_advantage}

## Estimated Effort
**{effort}** ({effort_details})

## Acceptance Criteria
{acceptance_criteria}
"""


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spec_id():
    """Generate a spec ID with cspec- prefix."""
    return f"cspec-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(event_type=event_type, actor="creative-engine",
                            action=action,
                            details=json.dumps(details) if details else None,
                            project_id="creative-engine")
        except Exception:
            pass


def _load_config():
    """Load creative config from YAML."""
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
# EFFORT ESTIMATION
# =========================================================================
def _estimate_effort(pain_point, config):
    """Estimate effort (S/M/L/XL) based on number of affected categories/areas.

    Counts unique categories from signal keywords and pain point metadata.
    Uses config thresholds: small_threshold, medium_threshold, large_threshold.

    Args:
        pain_point: dict with pain point data from DB.
        config: loaded creative_config.yaml dict.

    Returns:
        str: "S", "M", "L", or "XL"
    """
    spec_config = config.get("spec_generation", {}).get("effort_estimation", {})
    small_threshold = spec_config.get("small_threshold", 3)
    medium_threshold = spec_config.get("medium_threshold", 6)
    large_threshold = spec_config.get("large_threshold", 10)

    # Count affected areas from keywords and metadata
    keywords = _safe_json_loads(pain_point.get("keywords"), [])
    metadata = _safe_json_loads(pain_point.get("metadata"), {})
    affected_areas = set()

    # Check keywords against all extraction categories
    extraction_cats = config.get("extraction", {}).get("categories", {})
    for cat_name, cat_keywords in extraction_cats.items():
        cat_kw_lower = [kw.lower() for kw in cat_keywords]
        for kw in keywords:
            if kw.lower() in cat_kw_lower:
                affected_areas.add(cat_name)
                break

    # Also count the pain point's own category as an area
    category = pain_point.get("category", "other")
    affected_areas.add(category)

    # If metadata contains affected_components or similar, count those
    components = metadata.get("affected_components", [])
    if isinstance(components, list):
        for comp in components:
            affected_areas.add(str(comp))

    area_count = len(affected_areas)

    if area_count <= small_threshold:
        return "S"
    elif area_count <= medium_threshold:
        return "M"
    elif area_count <= large_threshold:
        return "L"
    else:
        return "XL"


# =========================================================================
# SECTION BUILDERS
# =========================================================================
def _build_quotes_section(signal_ids, config, db_path=None):
    """Build anonymized user quotes section from creative_signals.

    Fetches signals by ID and formats their body text as anonymized quotes.

    Args:
        signal_ids: list of signal IDs to fetch.
        config: loaded creative_config.yaml dict.
        db_path: optional database path override.

    Returns:
        str: formatted markdown string of anonymized quotes.
    """
    max_quotes = config.get("spec_generation", {}).get("max_quotes_per_spec", 5)
    include_quotes = config.get("spec_generation", {}).get("include_user_quotes", True)

    if not include_quotes or not signal_ids:
        return "_No user quotes available._"

    conn = _get_db(db_path)
    try:
        # Build parameterized query for signal IDs
        ids_to_fetch = signal_ids[:max_quotes * 2]  # fetch extra in case some lack body
        placeholders = ",".join("?" for _ in ids_to_fetch)
        query = (f"SELECT id, body, source, rating FROM creative_signals "
                 f"WHERE id IN ({placeholders}) ORDER BY discovered_at DESC")
        rows = conn.execute(query, ids_to_fetch).fetchall()
    finally:
        conn.close()

    if not rows:
        return "_No user quotes available._"

    quotes = []
    for row in rows:
        body = row["body"]
        if not body or not body.strip():
            continue
        # Truncate to 200 chars
        text = body.strip()
        if len(text) > 200:
            text = text[:200] + "..."
        # Sanitize: remove potential PII patterns (emails, names are already anonymous in source)
        source = row["source"] or "unknown"
        rating = row["rating"]
        if rating is not None:
            quotes.append(f'- *"{text}"* -- {source} review ({rating:.1f}/5)')
        else:
            quotes.append(f'- *"{text}"* -- {source} feedback')

        if len(quotes) >= max_quotes:
            break

    if not quotes:
        return "_No user quotes available._"

    return "\n".join(quotes)


def _build_competitive_analysis(feature_gap, config, db_path=None):
    """Build competitive landscape section from feature gap competitor_coverage.

    Parses the competitor_coverage JSON field which maps competitor names to
    whether they support the feature.

    Args:
        feature_gap: dict with feature gap data from DB.
        config: loaded creative_config.yaml dict.
        db_path: optional database path override.

    Returns:
        str: formatted markdown string with competitive analysis.
    """
    include_competitive = config.get("spec_generation", {}).get("include_competitive_analysis", True)
    if not include_competitive:
        return "_Competitive analysis not included._"

    coverage = _safe_json_loads(feature_gap.get("competitor_coverage"), {})
    if not coverage:
        return "_No competitor coverage data available._"

    lines = []
    supports_count = 0
    total_count = 0

    for name, has_feature in sorted(coverage.items()):
        total_count += 1
        if has_feature:
            supports_count += 1
            lines.append(f"- **{name}:** Supports this capability")
        else:
            lines.append(f"- **{name}:** Does not support this capability")

    if total_count > 0:
        lines.append("")
        lines.append(f"**{supports_count} out of {total_count}** competitors address this need.")
        gap_pct = ((total_count - supports_count) / total_count) * 100
        if gap_pct >= 75:
            lines.append("This represents a **significant market gap** -- most competitors do not address this.")
        elif gap_pct >= 50:
            lines.append("This represents a **moderate market gap** -- over half of competitors lack this feature.")
        elif gap_pct >= 25:
            lines.append("Some competitors address this, but a meaningful gap remains.")
        else:
            lines.append("Most competitors already address this -- differentiation will require a superior implementation.")

    return "\n".join(lines)


def _build_justification(pain_point, feature_gap):
    """Build justification narrative from pain point and feature gap data.

    Combines frequency, severity, gap_score into a narrative explaining
    why this feature should be built.

    Args:
        pain_point: dict with pain point data from DB.
        feature_gap: dict with feature gap data from DB.

    Returns:
        str: justification narrative string.
    """
    frequency = pain_point.get("frequency", 0)
    signal_ids = _safe_json_loads(pain_point.get("signal_ids"), [])
    signal_count = len(signal_ids)
    severity = pain_point.get("severity", "medium")
    gap_score = feature_gap.get("gap_score", 0.0) or 0.0

    # Calculate competitor gap percentage
    coverage = _safe_json_loads(feature_gap.get("competitor_coverage"), {})
    total_comp = len(coverage)
    supports = sum(1 for v in coverage.values() if v)
    gap_pct = 0
    if total_comp > 0:
        gap_pct = int(((total_comp - supports) / total_comp) * 100)

    market_demand = feature_gap.get("market_demand", 0.0) or 0.0
    requested_count = feature_gap.get("requested_by_count", 0) or 0

    parts = [
        f"This pain point was mentioned {frequency} time{'s' if frequency != 1 else ''} "
        f"across {signal_count} signal{'s' if signal_count != 1 else ''} with "
        f"**{severity}** severity."
    ]

    if total_comp > 0:
        parts.append(
            f"Only {100 - gap_pct}% of competitors address this gap, "
            f"representing a {'significant' if gap_pct >= 50 else 'moderate'} "
            f"market opportunity."
        )

    if requested_count > 0:
        parts.append(f"This feature has been explicitly requested by {requested_count} users.")

    if market_demand > 0.5:
        parts.append(f"Market demand score is {market_demand:.2f}, indicating strong user interest.")

    if gap_score > 0.7:
        parts.append("The gap score is high, suggesting this is a well-differentiated opportunity.")

    return " ".join(parts)


def _build_acceptance_criteria(pain_point, category):
    """Generate BDD-style acceptance criteria based on pain point category.

    Produces 3-5 Given/When/Then acceptance criteria appropriate to the
    feature's domain category.

    Args:
        pain_point: dict with pain point data from DB.
        category: str category of the pain point.

    Returns:
        str: numbered list of acceptance criteria.
    """
    title = pain_point.get("title", "the feature")
    outcomes = _CATEGORY_OUTCOMES.get(category, _CATEGORY_OUTCOMES["other"])

    criteria = []
    for i, outcome in enumerate(outcomes, 1):
        criteria.append(
            f"{i}. Given a user experiences **{category}** issues, "
            f"When **{title}** is implemented, "
            f"Then {outcome}"
        )

    # Always add CUI marking criterion
    criteria.append(
        f"{len(criteria) + 1}. Given CUI markings are required, "
        f"When files are generated, "
        f"Then appropriate CUI // SP-CTI markings are present"
    )

    # Add audit trail criterion
    criteria.append(
        f"{len(criteria) + 1}. Given the feature is invoked, "
        f"When it completes successfully, "
        f"Then an audit trail entry is recorded"
    )

    return "\n".join(criteria)


def _build_competitive_advantage(feature_gap, pain_point):
    """Build a competitive advantage statement.

    Analyzes the gap between our planned feature and competitor offerings
    to articulate the differentiation.

    Args:
        feature_gap: dict with feature gap data from DB.
        pain_point: dict with pain point data from DB.

    Returns:
        str: competitive advantage narrative.
    """
    coverage = _safe_json_loads(feature_gap.get("competitor_coverage"), {})
    total_comp = len(coverage)
    supports = sum(1 for v in coverage.values() if v)
    category = pain_point.get("category", "other")
    severity = pain_point.get("severity", "medium")

    if total_comp == 0:
        return ("No competitor data available. This feature addresses a "
                f"**{severity}**-severity {category} pain point identified from user feedback.")

    gap_pct = int(((total_comp - supports) / total_comp) * 100)

    if gap_pct >= 75:
        return (f"**Strong first-mover advantage.** {gap_pct}% of competitors ({total_comp - supports} "
                f"of {total_comp}) do not offer this capability. Implementing this feature "
                f"addresses a {severity}-severity {category} gap and positions us as the "
                f"market leader in this area.")
    elif gap_pct >= 50:
        return (f"**Meaningful differentiation.** {gap_pct}% of competitors lack this feature. "
                f"Delivering a well-designed solution for this {severity}-severity {category} "
                f"issue will provide a clear competitive edge.")
    elif gap_pct >= 25:
        return (f"**Incremental advantage.** While some competitors ({supports} of {total_comp}) "
                f"address this, a superior implementation that resolves the underlying "
                f"{severity}-severity {category} pain point can still differentiate our offering.")
    else:
        return (f"**Table-stakes feature.** Most competitors ({supports} of {total_comp}) "
                f"already address this. Implementation is necessary to maintain competitive parity, "
                f"but differentiation will require a notably better user experience.")


def _build_target_persona(category):
    """Build target persona section from category.

    Args:
        category: str category of the pain point.

    Returns:
        str: formatted persona description.
    """
    persona_key = PERSONA_MAP.get(category, "developer")
    persona_desc = PERSONA_DESCRIPTIONS.get(persona_key, PERSONA_DESCRIPTIONS["developer"])
    return f"**{persona_key.upper()}** -- {persona_desc}"


def _build_sources_list(pain_point, db_path=None):
    """Build a comma-separated source list from the pain point's signals.

    Args:
        pain_point: dict with pain point data from DB.
        db_path: optional database path override.

    Returns:
        str: comma-separated list of unique sources.
    """
    signal_ids = _safe_json_loads(pain_point.get("signal_ids"), [])
    if not signal_ids:
        return "N/A"

    conn = _get_db(db_path)
    try:
        placeholders = ",".join("?" for _ in signal_ids[:50])
        query = f"SELECT DISTINCT source FROM creative_signals WHERE id IN ({placeholders})"
        rows = conn.execute(query, signal_ids[:50]).fetchall()
    finally:
        conn.close()

    sources = [row["source"] for row in rows if row["source"]]
    return ", ".join(sorted(set(sources))) if sources else "N/A"


# =========================================================================
# SPEC GENERATION
# =========================================================================
def generate_spec(feature_gap_id, db_path=None):
    """Generate a full feature specification from a feature gap.

    Reads the feature gap and its associated pain point from the database,
    builds all spec sections using helper functions, fills the template,
    and stores the result in creative_specs.

    Args:
        feature_gap_id: The feature gap identifier.
        db_path: Optional database path override.

    Returns:
        dict with spec_id, title, composite_score, estimated_effort, status.
    """
    config = _load_config()
    conn = _get_db(db_path)
    try:
        # Fetch feature gap
        gap_row = conn.execute(
            "SELECT * FROM creative_feature_gaps WHERE id = ?",
            (feature_gap_id,)
        ).fetchone()
        if not gap_row:
            return {"error": f"Feature gap not found: {feature_gap_id}"}
        feature_gap = dict(gap_row)

        # Check if spec already exists for this gap
        existing = conn.execute(
            "SELECT id, status FROM creative_specs WHERE feature_gap_id = ?",
            (feature_gap_id,)
        ).fetchone()
        if existing:
            return {"error": f"Spec already exists for feature gap {feature_gap_id}",
                    "spec_id": existing["id"], "spec_status": existing["status"]}

        # Fetch associated pain point
        pain_point_id = feature_gap.get("pain_point_id")
        pain_point = None
        if pain_point_id:
            pp_row = conn.execute(
                "SELECT * FROM creative_pain_points WHERE id = ?",
                (pain_point_id,)
            ).fetchone()
            if pp_row:
                pain_point = dict(pp_row)

        # If no pain point found, build a minimal one from the gap
        if not pain_point:
            pain_point = {
                "id": None,
                "title": feature_gap.get("feature_name", "Unknown"),
                "description": feature_gap.get("description", ""),
                "category": "other",
                "frequency": feature_gap.get("requested_by_count", 1) or 1,
                "signal_ids": feature_gap.get("signal_ids", "[]"),
                "keywords": "[]",
                "severity": "medium",
                "composite_score": feature_gap.get("gap_score", 0.0),
                "score_breakdown": "{}",
                "metadata": "{}",
            }

        # Parse score breakdown
        score_breakdown = _safe_json_loads(pain_point.get("score_breakdown"), {})
        pain_frequency_score = score_breakdown.get("pain_frequency", 0.0)
        gap_uniqueness_score = score_breakdown.get("gap_uniqueness", 0.0)
        effort_to_impact_score = score_breakdown.get("effort_to_impact", 0.0)

        # Compute composite score using configured weights
        weights = config.get("scoring", {}).get("weights", {})
        w_freq = weights.get("pain_frequency", 0.40)
        w_gap = weights.get("gap_uniqueness", 0.35)
        w_effort = weights.get("effort_to_impact", 0.25)

        # If breakdown scores are all zero but pain_point has composite_score, use it
        if pain_frequency_score == 0 and gap_uniqueness_score == 0 and effort_to_impact_score == 0:
            composite_score = float(pain_point.get("composite_score") or feature_gap.get("gap_score") or 0.0)
            # Distribute evenly if we only have composite
            if composite_score > 0:
                pain_frequency_score = composite_score
                gap_uniqueness_score = composite_score
                effort_to_impact_score = composite_score
        else:
            composite_score = (w_freq * pain_frequency_score +
                               w_gap * gap_uniqueness_score +
                               w_effort * effort_to_impact_score)

        # Build sections
        category = pain_point.get("category", "other")
        signal_ids = _safe_json_loads(pain_point.get("signal_ids"), [])

        effort = _estimate_effort(pain_point, config)
        effort_info = EFFORT_MAP.get(effort, EFFORT_MAP["M"])

        quotes_section = _build_quotes_section(signal_ids, config, db_path)
        competitive_analysis = _build_competitive_analysis(feature_gap, config, db_path)
        justification = _build_justification(pain_point, feature_gap)
        acceptance_criteria = _build_acceptance_criteria(pain_point, category)
        competitive_advantage = _build_competitive_advantage(feature_gap, pain_point)
        target_persona = _build_target_persona(category)
        sources = _build_sources_list(pain_point, db_path)

        title = feature_gap.get("feature_name", pain_point.get("title", "Untitled"))

        # Fill template
        spec_content = _SPEC_TEMPLATE.format(
            title=title,
            pain_point_description=pain_point.get("description") or pain_point.get("title", "No description."),
            frequency=pain_point.get("frequency", 0),
            signal_count=len(signal_ids),
            sources=sources,
            severity=pain_point.get("severity", "medium"),
            category=category,
            quotes_section=quotes_section,
            competitive_analysis=competitive_analysis,
            feature_name=feature_gap.get("feature_name", "Unnamed Feature"),
            feature_description=feature_gap.get("description", "No description provided."),
            justification=justification,
            composite_score=composite_score,
            pain_frequency_score=pain_frequency_score,
            gap_uniqueness_score=gap_uniqueness_score,
            effort_to_impact_score=effort_to_impact_score,
            target_persona=target_persona,
            competitive_advantage=competitive_advantage,
            effort=effort,
            effort_details=f"{effort_info['label']}, ~{effort_info['days']} days",
            acceptance_criteria=acceptance_criteria,
        )

        # Store in creative_specs
        spec_id = _spec_id()
        now = _now()
        conn.execute(
            """INSERT INTO creative_specs
            (id, feature_gap_id, pain_point_id, title, spec_content,
             composite_score, justification, estimated_effort,
             target_persona, competitive_advantage, status, metadata, created_at, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'generated', ?, ?, 'CUI')""",
            (
                spec_id,
                feature_gap_id,
                pain_point_id,
                title,
                spec_content,
                composite_score,
                justification,
                effort,
                PERSONA_MAP.get(category, "developer"),
                competitive_advantage,
                json.dumps({
                    "score_breakdown": score_breakdown,
                    "signal_count": len(signal_ids),
                    "category": category,
                }),
                now,
            ),
        )
        conn.commit()

        _audit("creative.spec.generated",
               f"Generated spec {spec_id} from feature gap {feature_gap_id}",
               {"spec_id": spec_id, "feature_gap_id": feature_gap_id,
                "pain_point_id": pain_point_id, "composite_score": composite_score,
                "effort": effort, "category": category})

        return {
            "spec_id": spec_id,
            "title": title,
            "composite_score": round(composite_score, 4),
            "estimated_effort": effort,
            "effort_details": effort_info,
            "target_persona": PERSONA_MAP.get(category, "developer"),
            "category": category,
            "status": "generated",
            "feature_gap_id": feature_gap_id,
            "pain_point_id": pain_point_id,
            "created_at": now,
        }
    finally:
        conn.close()


def generate_all_eligible(db_path=None):
    """Generate specs for all eligible feature gaps.

    Queries feature gaps where status='identified', checks that the associated
    pain point's composite_score meets the auto_spec threshold, and generates
    specs up to the max_auto_specs_per_cycle budget.

    Args:
        db_path: Optional database path override.

    Returns:
        dict with generated, skipped_low_score, skipped_budget counts.
    """
    config = _load_config()
    thresholds = config.get("scoring", {}).get("thresholds", {})
    auto_spec_threshold = thresholds.get("auto_spec", 0.75)
    max_budget = config.get("scoring", {}).get("max_auto_specs_per_cycle", 20)

    conn = _get_db(db_path)
    try:
        # Fetch all feature gaps with status='identified' that don't already have specs
        gaps = [dict(r) for r in conn.execute("""
            SELECT fg.* FROM creative_feature_gaps fg
            LEFT JOIN creative_specs cs ON fg.id = cs.feature_gap_id
            WHERE fg.status = 'identified' AND cs.id IS NULL
            ORDER BY fg.gap_score DESC
        """).fetchall()]
    finally:
        conn.close()

    if not gaps:
        return {"generated": 0, "skipped_low_score": 0, "skipped_budget": 0,
                "message": "No eligible feature gaps found.", "results": []}

    generated = 0
    skipped_low_score = 0
    skipped_budget = 0
    results = []

    for gap in gaps:
        # Check budget
        if generated >= max_budget:
            skipped_budget += 1
            continue

        # Check composite score from associated pain point
        pain_point_id = gap.get("pain_point_id")
        composite_score = 0.0
        if pain_point_id:
            conn2 = _get_db(db_path)
            try:
                pp_row = conn2.execute(
                    "SELECT composite_score FROM creative_pain_points WHERE id = ?",
                    (pain_point_id,)
                ).fetchone()
                if pp_row and pp_row["composite_score"] is not None:
                    composite_score = pp_row["composite_score"]
            finally:
                conn2.close()

        # Fall back to gap_score if no pain point score
        if composite_score == 0.0:
            composite_score = gap.get("gap_score", 0.0) or 0.0

        if composite_score < auto_spec_threshold:
            skipped_low_score += 1
            results.append({
                "feature_gap_id": gap["id"],
                "feature_name": gap.get("feature_name", ""),
                "composite_score": composite_score,
                "result": "skipped_low_score",
                "status": "skipped",
            })
            continue

        # Generate spec
        result = generate_spec(gap["id"], db_path)
        if "error" in result:
            results.append({
                "feature_gap_id": gap["id"],
                "feature_name": gap.get("feature_name", ""),
                "result": result["error"],
                "status": "error",
            })
        else:
            generated += 1
            results.append({
                "feature_gap_id": gap["id"],
                "feature_name": gap.get("feature_name", ""),
                "spec_id": result["spec_id"],
                "composite_score": result["composite_score"],
                "estimated_effort": result["estimated_effort"],
                "result": result["spec_id"],
                "status": "generated",
            })

    _audit("creative.spec.batch",
           f"Batch spec generation: {generated} generated, "
           f"{skipped_low_score} below threshold, {skipped_budget} over budget",
           {"generated": generated, "skipped_low_score": skipped_low_score,
            "skipped_budget": skipped_budget, "budget": max_budget,
            "threshold": auto_spec_threshold, "total_gaps": len(gaps)})

    return {
        "generated": generated,
        "skipped_low_score": skipped_low_score,
        "skipped_budget": skipped_budget,
        "budget": max_budget,
        "threshold": auto_spec_threshold,
        "total_gaps": len(gaps),
        "results": results,
    }


def list_specs(status=None, limit=20, db_path=None):
    """List generated specs with optional status filter.

    Args:
        status: Filter by status (generated/reviewed/approved/building/rejected).
        limit: Maximum number of results (default 20).
        db_path: Optional database path override.

    Returns:
        list of spec summary dicts.
    """
    conn = _get_db(db_path)
    try:
        query = ("SELECT cs.id, cs.feature_gap_id, cs.pain_point_id, cs.title, "
                 "cs.composite_score, cs.estimated_effort, cs.target_persona, "
                 "cs.status, cs.reviewer, cs.reviewed_at, cs.created_at "
                 "FROM creative_specs cs")
        params = []

        if status:
            if status not in VALID_STATUSES:
                return {"error": f"Invalid status: {status}. Valid: {', '.join(VALID_STATUSES)}"}
            query += " WHERE cs.status = ?"
            params.append(status)

        query += " ORDER BY cs.composite_score DESC, cs.created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        specs = []
        for row in rows:
            specs.append({
                "spec_id": row["id"],
                "feature_gap_id": row["feature_gap_id"],
                "pain_point_id": row["pain_point_id"],
                "title": row["title"],
                "composite_score": row["composite_score"],
                "estimated_effort": row["estimated_effort"],
                "effort_details": EFFORT_MAP.get(row["estimated_effort"], {}),
                "target_persona": row["target_persona"],
                "status": row["status"],
                "reviewer": row["reviewer"],
                "reviewed_at": row["reviewed_at"],
                "created_at": row["created_at"],
            })

        # Counts by status
        count_rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM creative_specs GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in count_rows}

        return {
            "specs": specs,
            "returned": len(specs),
            "total": sum(counts.values()),
            "filter_status": status,
            "counts_by_status": counts,
        }
    finally:
        conn.close()


def get_spec(spec_id, db_path=None):
    """Get full spec details including spec_content.

    Args:
        spec_id: The spec identifier (cspec-xxx).
        db_path: Optional database path override.

    Returns:
        dict with full spec details or error.
    """
    if not spec_id:
        return {"error": "spec_id is required"}

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM creative_specs WHERE id = ?",
            (spec_id,)
        ).fetchone()
        if not row:
            return {"error": f"Spec not found: {spec_id}"}

        spec = dict(row)

        # Fetch associated feature gap summary
        gap_summary = {}
        if spec.get("feature_gap_id"):
            gap_row = conn.execute(
                "SELECT id, feature_name, gap_score, market_demand, status "
                "FROM creative_feature_gaps WHERE id = ?",
                (spec["feature_gap_id"],)
            ).fetchone()
            if gap_row:
                gap_summary = dict(gap_row)

        # Fetch associated pain point summary
        pp_summary = {}
        if spec.get("pain_point_id"):
            pp_row = conn.execute(
                "SELECT id, title, category, severity, frequency, composite_score "
                "FROM creative_pain_points WHERE id = ?",
                (spec["pain_point_id"],)
            ).fetchone()
            if pp_row:
                pp_summary = dict(pp_row)

        return {
            "spec_id": spec["id"],
            "feature_gap_id": spec["feature_gap_id"],
            "pain_point_id": spec["pain_point_id"],
            "title": spec["title"],
            "spec_content": spec["spec_content"],
            "composite_score": spec["composite_score"],
            "justification": spec["justification"],
            "estimated_effort": spec["estimated_effort"],
            "effort_details": EFFORT_MAP.get(spec["estimated_effort"], {}),
            "target_persona": spec["target_persona"],
            "competitive_advantage": spec["competitive_advantage"],
            "status": spec["status"],
            "reviewer": spec["reviewer"],
            "reviewed_at": spec["reviewed_at"],
            "metadata": _safe_json_loads(spec.get("metadata"), {}),
            "created_at": spec["created_at"],
            "classification": spec.get("classification", "CUI"),
            "feature_gap": gap_summary,
            "pain_point": pp_summary,
        }
    finally:
        conn.close()


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _format_human(action, result):
    """Format output for human-readable terminal display."""
    lines = []
    lines.append("=" * 70)
    lines.append("  SPEC GENERATOR -- CUI // SP-CTI")
    lines.append("=" * 70)

    if isinstance(result, dict) and "error" in result:
        lines.append(f"\n  ERROR: {result['error']}\n")
        lines.append("=" * 70)
        return "\n".join(lines)

    if action == "generate":
        lines.append(f"\n  Spec Generated: {result.get('spec_id')}")
        lines.append(f"  Title: {result.get('title')}")
        lines.append(f"  Score: {result.get('composite_score', 0):.2f}")
        lines.append(f"  Effort: {result.get('estimated_effort')} "
                      f"({EFFORT_MAP.get(result.get('estimated_effort', 'M'), {}).get('label', '')})")
        lines.append(f"  Persona: {result.get('target_persona')}")
        lines.append(f"  Category: {result.get('category')}")
        lines.append(f"  Gap: {result.get('feature_gap_id')}")
        lines.append(f"  Pain Point: {result.get('pain_point_id')}")

    elif action == "generate_all":
        lines.append(f"\n  Batch Generation Summary")
        lines.append(f"  {'=' * 40}")
        lines.append(f"  Generated:       {result.get('generated', 0)}")
        lines.append(f"  Skipped (score): {result.get('skipped_low_score', 0)}")
        lines.append(f"  Skipped (budget):{result.get('skipped_budget', 0)}")
        lines.append(f"  Total gaps:      {result.get('total_gaps', 0)}")
        lines.append(f"  Budget:          {result.get('budget', 0)}")
        lines.append(f"  Threshold:       {result.get('threshold', 0)}")
        lines.append("")
        for r in result.get("results", []):
            icon = "OK" if r["status"] == "generated" else "SKIP" if r["status"] == "skipped" else "ERR"
            name = r.get("feature_name", "")[:50]
            lines.append(f"  [{icon:4s}] {r.get('feature_gap_id', '')}: {name}")
            if r["status"] == "generated":
                lines.append(f"         -> {r.get('spec_id', '')} "
                              f"(score={r.get('composite_score', 0):.2f}, "
                              f"effort={r.get('estimated_effort', 'N/A')})")

    elif action == "list":
        specs = result.get("specs", [])
        counts = result.get("counts_by_status", {})
        lines.append(f"\n  Specs: {result.get('returned', 0)} of {result.get('total', 0)}")
        if counts:
            counts_str = ", ".join(f"{k}={v}" for k, v in counts.items())
            lines.append(f"  Status distribution: {counts_str}")
        lines.append("-" * 70)
        for s in specs:
            lines.append(f"  {s['spec_id']}  [{s['status']:10s}]  "
                          f"score={s['composite_score']:.2f}  "
                          f"effort={s['estimated_effort']}")
            lines.append(f"    Title: {s['title'][:60]}")
            lines.append(f"    Persona: {s.get('target_persona', 'N/A')}  "
                          f"Created: {s.get('created_at', 'N/A')}")
            lines.append("")

    elif action == "get":
        lines.append(f"\n  Spec: {result.get('spec_id')}")
        lines.append(f"  Title: {result.get('title')}")
        lines.append(f"  Score: {result.get('composite_score', 0):.2f}")
        lines.append(f"  Effort: {result.get('estimated_effort')} "
                      f"({result.get('effort_details', {}).get('label', '')})")
        lines.append(f"  Status: {result.get('status')}")
        lines.append(f"  Persona: {result.get('target_persona')}")
        pp = result.get("pain_point", {})
        if pp:
            lines.append(f"  Pain Point: {pp.get('title', 'N/A')} "
                          f"({pp.get('category', 'N/A')}, {pp.get('severity', 'N/A')})")
        fg = result.get("feature_gap", {})
        if fg:
            lines.append(f"  Feature Gap: {fg.get('feature_name', 'N/A')} "
                          f"(gap_score={fg.get('gap_score', 0):.2f})")
        lines.append("")
        lines.append("-" * 70)
        lines.append("  SPEC CONTENT:")
        lines.append("-" * 70)
        lines.append(result.get("spec_content", ""))

    lines.append("=" * 70)
    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Feature Spec Generator -- template-based spec generation from ranked gaps (CUI // SP-CTI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s --generate --feature-gap-id fg-abc123 --json\n"
               "  %(prog)s --generate-all --json\n"
               "  %(prog)s --list --status generated --limit 10 --json\n"
               "  %(prog)s --get --spec-id cspec-abc123 --json\n"
               "  %(prog)s --generate --feature-gap-id fg-abc123 --human\n",
    )

    # Actions
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--generate", action="store_true",
                         help="Generate spec for a single feature gap")
    actions.add_argument("--generate-all", action="store_true",
                         help="Generate specs for all eligible feature gaps")
    actions.add_argument("--list", action="store_true",
                         help="List generated specs")
    actions.add_argument("--get", action="store_true",
                         help="Get full spec content by ID")

    # Parameters
    parser.add_argument("--feature-gap-id", type=str, default=None,
                        help="Feature gap ID (for --generate)")
    parser.add_argument("--spec-id", type=str, default=None,
                        help="Spec ID (for --get)")
    parser.add_argument("--status", type=str, default=None,
                        choices=list(VALID_STATUSES),
                        help="Filter by status (for --list)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Max results (for --list, default 20)")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=str, default=None,
                        help="Override database path")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.generate:
            if not args.feature_gap_id:
                parser.error("--generate requires --feature-gap-id")
            result = generate_spec(args.feature_gap_id, db_path)
            action = "generate"
        elif args.generate_all:
            result = generate_all_eligible(db_path)
            action = "generate_all"
        elif args.list:
            result = list_specs(args.status, args.limit, db_path)
            action = "list"
        elif args.get:
            if not args.spec_id:
                parser.error("--get requires --spec-id")
            result = get_spec(args.spec_id, db_path)
            action = "get"
        else:
            result = {"error": "No action specified"}
            action = "unknown"

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        elif args.human:
            print(_format_human(action, result))
        else:
            # Default: JSON
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
