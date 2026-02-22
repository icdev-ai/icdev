#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Innovation Scoring Engine — score and rank innovation signals using weighted multi-dimension analysis.

Scores innovation signals discovered by web_scanner.py using a 5-dimension weighted
average (D21 deterministic scoring pattern):

  1. community_demand  (0.30) — GitHub stars, SO votes, upvotes, issue frequency
  2. impact_breadth    (0.25) — Potential number of ICDEV projects/tenants affected
  3. feasibility       (0.20) — Can ICDEV build this with existing tools/layers?
  4. compliance_alignment (0.15) — Does it strengthen compliance posture?
  5. novelty           (0.10) — Not already addressed by existing ICDEV capabilities

Architecture:
    - Weights loaded from args/innovation_config.yaml under scoring.weights (D26 pattern)
    - Thresholds: auto_queue >= 0.80, suggest >= 0.50, log_only < 0.50
    - Status transitions: new -> scored (after scoring)
    - Score + dimension breakdown stored in innovation_signals table
    - Calibration adjusts weights based on marketplace adoption feedback
    - All scoring is deterministic (D21 — reproducible, not probabilistic)

Usage:
    # Score a single signal
    python tools/innovation/signal_ranker.py --score --signal-id "sig-xxx" --json

    # Score all unscored signals
    python tools/innovation/signal_ranker.py --score-all --json

    # Get top-scored signals
    python tools/innovation/signal_ranker.py --top --limit 20 --min-score 0.5 --json

    # Recalibrate weights from marketplace feedback
    python tools/innovation/signal_ranker.py --calibrate --json
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "innovation_config.yaml"

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
DEFAULT_WEIGHTS = {
    "community_demand": 0.30,
    "impact_breadth": 0.25,
    "feasibility": 0.20,
    "compliance_alignment": 0.15,
    "novelty": 0.10,
}

DEFAULT_THRESHOLDS = {
    "auto_queue": 0.80,
    "suggest": 0.50,
    "log_only": 0.0,
}

# Categories that strengthen compliance posture (positive boost)
COMPLIANCE_POSITIVE_CATEGORIES = {
    "security_vulnerability", "compliance_gap", "supply_chain",
}

# Categories neutral to compliance
COMPLIANCE_NEUTRAL_CATEGORIES = {
    "developer_experience", "performance", "infrastructure",
    "testing", "ai_tooling", "modernization",
}

# GOTCHA layer keyword mapping — used for feasibility scoring
# Mirrors triage.gotcha_fit.layer_mapping from innovation_config.yaml
DEFAULT_GOTCHA_LAYERS = {
    "goal": ["workflow", "process", "procedure", "methodology", "best practice"],
    "tool": ["script", "utility", "generator", "scanner", "checker", "validator", "analyzer"],
    "arg": ["configuration", "setting", "threshold", "parameter", "tuning"],
    "context": ["reference", "template", "sample", "example", "guideline", "standard"],
    "hardprompt": ["prompt template", "instruction", "llm directive", "system prompt"],
}


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None, project_id=None):
    """Write audit trail entry (best-effort, never raises)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                details=json.dumps(details) if details else None,
                project_id=project_id or "innovation-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load innovation config from YAML with fallback defaults."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_weights(config=None):
    """Extract scoring weights from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    result = {}
    for dim, default_val in DEFAULT_WEIGHTS.items():
        result[dim] = float(weights.get(dim, default_val))
    # Normalize weights to sum to 1.0
    total = sum(result.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        result = {k: v / total for k, v in result.items()}
    return result


def _get_thresholds(config=None):
    """Extract scoring thresholds from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    scoring = config.get("scoring", {})
    thresholds = scoring.get("thresholds", {})
    return {
        "auto_queue": float(thresholds.get("auto_queue", DEFAULT_THRESHOLDS["auto_queue"])),
        "suggest": float(thresholds.get("suggest", DEFAULT_THRESHOLDS["suggest"])),
        "log_only": float(thresholds.get("log_only", DEFAULT_THRESHOLDS["log_only"])),
    }


def _get_gotcha_layers(config=None):
    """Extract GOTCHA layer mapping from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    triage = config.get("triage", {})
    gotcha_fit = triage.get("gotcha_fit", {})
    layer_mapping = gotcha_fit.get("layer_mapping", {})
    if layer_mapping:
        return {k: [kw.lower() for kw in v] for k, v in layer_mapping.items()}
    return {k: [kw.lower() for kw in v] for k, v in DEFAULT_GOTCHA_LAYERS.items()}


def _get_signal_categories(config=None):
    """Extract signal category keywords from config."""
    if config is None:
        config = _load_config()
    categories = config.get("signal_categories", {})
    return categories


# =========================================================================
# DIMENSION SCORERS
# =========================================================================
def _score_community_demand(signal):
    """Score community demand dimension.

    Uses the community_score field already set by web_scanner.py.
    This field is normalized to [0, 1] by the scanner based on source-specific
    metrics (GitHub stars/1000, SO votes/100, HN score/500, CVSS/10).

    Args:
        signal: Dict of signal row from DB.

    Returns:
        Float in [0.0, 1.0].
    """
    raw = float(signal.get("community_score", 0.0) or 0.0)
    # Clamp to [0, 1]
    return max(0.0, min(1.0, raw))


def _score_impact_breadth(signal, conn):
    """Score impact breadth dimension.

    Estimates how many ICDEV projects/tenants could benefit from addressing
    this signal. Uses signal category to match against project types in DB.

    Args:
        signal: Dict of signal row from DB.
        conn: Open database connection.

    Returns:
        Float in [0.0, 1.0].
    """
    category = signal.get("category") or ""
    title = (signal.get("title") or "").lower()
    description = (signal.get("description") or "").lower()
    text_corpus = f"{title} {description} {category}"

    # Count total active projects
    try:
        total_projects = conn.execute(
            "SELECT COUNT(*) as cnt FROM projects WHERE status = 'active'"
        ).fetchone()["cnt"]
    except Exception:
        total_projects = 0

    if total_projects == 0:
        # No projects in DB — use heuristic based on category breadth
        # Security and compliance affect everyone; niche categories affect fewer
        broad_keywords = [
            "security", "compliance", "testing", "ci/cd", "pipeline",
            "deployment", "monitoring", "authentication", "authorization",
        ]
        narrow_keywords = [
            "specific", "niche", "legacy", "deprecated", "single",
        ]
        broad_matches = sum(1 for kw in broad_keywords if kw in text_corpus)
        narrow_matches = sum(1 for kw in narrow_keywords if kw in text_corpus)
        score = min(1.0, (broad_matches * 0.15) - (narrow_matches * 0.1))
        return max(0.0, min(1.0, score + 0.3))  # Base score of 0.3

    # Match signal against project types and tech stacks
    affected = 0

    # Category-to-project-type relevance mapping
    category_project_map = {
        "security_vulnerability": None,       # Affects all projects
        "compliance_gap": None,               # Affects all projects
        "supply_chain": None,                 # Affects all projects
        "developer_experience": None,         # Affects all projects
        "infrastructure": ["microservice", "api", "webapp"],
        "testing": None,                      # Affects all projects
        "performance": ["webapp", "api", "microservice", "data_pipeline"],
        "modernization": ["webapp", "api", "microservice"],
        "ai_tooling": None,                   # Affects all projects
    }

    relevant_types = category_project_map.get(category)

    if relevant_types is None:
        # Affects all project types
        affected = total_projects
    else:
        try:
            placeholders = ",".join("?" for _ in relevant_types)
            affected = conn.execute(
                f"SELECT COUNT(*) as cnt FROM projects WHERE status = 'active' AND type IN ({placeholders})",
                relevant_types,
            ).fetchone()["cnt"]
        except Exception:
            affected = total_projects // 2  # Conservative estimate

    if total_projects > 0:
        ratio = affected / total_projects
    else:
        ratio = 0.5

    return max(0.0, min(1.0, ratio))


def _score_feasibility(signal, config=None):
    """Score feasibility dimension.

    Checks if the signal category maps to an existing GOTCHA layer,
    indicating ICDEV has the architecture to address it.

    Args:
        signal: Dict of signal row from DB.
        config: Loaded innovation config (optional).

    Returns:
        Float in [0.0, 1.0].
    """
    gotcha_layers = _get_gotcha_layers(config)
    title = (signal.get("title") or "").lower()
    description = (signal.get("description") or "").lower()
    metadata_str = signal.get("metadata") or "{}"
    try:
        metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    text_corpus = f"{title} {description} {json.dumps(metadata).lower()}"

    # Count how many GOTCHA layers this signal maps to
    layers_matched = 0
    total_layers = len(gotcha_layers)
    matched_layer_names = []

    for layer_name, keywords in gotcha_layers.items():
        for kw in keywords:
            if kw in text_corpus:
                layers_matched += 1
                matched_layer_names.append(layer_name)
                break  # One match per layer is sufficient

    if total_layers == 0:
        return 0.5  # Unknown feasibility

    # Base score from layer coverage
    layer_ratio = layers_matched / total_layers

    # Boost: signals matching "tool" layer are most directly actionable
    tool_bonus = 0.15 if "tool" in matched_layer_names else 0.0

    # Penalty: signals requiring external dependencies are harder
    external_penalty = 0.0
    hard_keywords = ["hardware", "physical", "proprietary", "closed-source", "manual"]
    for kw in hard_keywords:
        if kw in text_corpus:
            external_penalty += 0.1

    score = layer_ratio + tool_bonus - external_penalty
    # Ensure minimum feasibility for anything that maps to at least one layer
    if layers_matched > 0:
        score = max(score, 0.3)

    return max(0.0, min(1.0, score))


def _score_compliance_alignment(signal, config=None):
    """Score compliance alignment dimension.

    Boosts signals in security/compliance categories, neutral for others,
    penalizes if the signal could potentially weaken compliance posture.

    Args:
        signal: Dict of signal row from DB.
        config: Loaded innovation config (optional).

    Returns:
        Float in [0.0, 1.0].
    """
    category = signal.get("category") or ""
    title = (signal.get("title") or "").lower()
    description = (signal.get("description") or "").lower()
    text_corpus = f"{title} {description}"

    # Positive: directly strengthens compliance
    if category in COMPLIANCE_POSITIVE_CATEGORIES:
        base_score = 0.85
    elif category in COMPLIANCE_NEUTRAL_CATEGORIES:
        base_score = 0.50
    else:
        base_score = 0.50  # Unknown category gets neutral

    # Boost for compliance-related keywords in text
    compliance_keywords = [
        "nist", "fedramp", "cmmc", "stig", "ato", "fips", "compliance",
        "audit", "authorization", "security control", "cui", "classified",
        "hipaa", "pci", "cjis", "soc 2", "iso 27001", "zero trust",
    ]
    keyword_hits = sum(1 for kw in compliance_keywords if kw in text_corpus)
    keyword_boost = min(0.15, keyword_hits * 0.03)

    # Penalty for potentially weakening compliance
    weakening_keywords = [
        "bypass", "disable security", "skip auth", "remove check",
        "ignore compliance", "workaround security",
    ]
    weakening_hits = sum(1 for kw in weakening_keywords if kw in text_corpus)
    weakening_penalty = min(0.4, weakening_hits * 0.2)

    score = base_score + keyword_boost - weakening_penalty
    return max(0.0, min(1.0, score))


def _score_novelty(signal, conn):
    """Score novelty dimension.

    Checks whether the signal addresses something not already covered by
    existing ICDEV capabilities. Searches knowledge_patterns and tool manifest
    for similar patterns via keyword matching.

    Args:
        signal: Dict of signal row from DB.
        conn: Open database connection.

    Returns:
        Float in [0.0, 1.0]. Higher = more novel (less overlap).
    """
    title = (signal.get("title") or "").lower()
    description = (signal.get("description") or "").lower()

    # Extract significant words (simple tokenization, skip short words)
    stop_words = {
        "the", "and", "for", "that", "this", "with", "from", "are", "was",
        "have", "has", "not", "but", "can", "will", "all", "been", "they",
        "how", "use", "new", "when", "what", "who", "why", "does", "into",
    }
    words = set()
    for token in f"{title} {description}".split():
        cleaned = token.strip(".,;:!?()[]{}\"'`")
        if len(cleaned) > 3 and cleaned not in stop_words:
            words.add(cleaned)

    if not words:
        return 0.7  # No keywords to check — assume moderately novel

    # Check against knowledge_patterns table
    overlap_count = 0
    total_checks = 0

    try:
        patterns = conn.execute(
            "SELECT pattern_signature, description FROM knowledge_patterns"
        ).fetchall()
        for pattern in patterns:
            sig_text = (
                (pattern["pattern_signature"] or "") + " " + (pattern["description"] or "")
            ).lower()
            matches = sum(1 for w in words if w in sig_text)
            if matches >= 3:  # At least 3 keyword overlaps = significant similarity
                overlap_count += 1
            total_checks += 1
    except Exception:
        pass  # Table may not exist or be empty

    # Check against existing innovation_signals that are already scored/queued
    try:
        recent_signals = conn.execute(
            """SELECT title, description FROM innovation_signals
               WHERE status IN ('scored', 'queued', 'in_progress', 'completed')
               AND id != ?
               ORDER BY discovered_at DESC LIMIT 200""",
            (signal.get("id", ""),),
        ).fetchall()
        for recent in recent_signals:
            recent_text = (
                (recent["title"] or "") + " " + (recent["description"] or "")
            ).lower()
            matches = sum(1 for w in words if w in recent_text)
            if matches >= 3:
                overlap_count += 1
            total_checks += 1
    except Exception:
        pass

    if total_checks == 0:
        return 0.9  # Nothing to compare against — very novel

    # Novelty is inversely proportional to overlap
    overlap_ratio = overlap_count / max(total_checks, 1)
    novelty = 1.0 - min(1.0, overlap_ratio * 2.0)  # Scale: 50% overlap = 0 novelty

    return max(0.0, min(1.0, novelty))


# =========================================================================
# SCORING FUNCTIONS
# =========================================================================
def score_signal(signal_id, db_path=None):
    """Score a single innovation signal across all 5 dimensions.

    Reads the signal from DB, computes each dimension score, calculates
    the weighted average, updates the signal row, and returns the result.

    Args:
        signal_id: The signal ID (e.g., "sig-abc123def456").
        db_path: Optional database path override.

    Returns:
        Dict with signal_id, overall score, dimension breakdown, status, and threshold.
    """
    config = _load_config()
    weights = _get_weights(config)
    thresholds = _get_thresholds(config)

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM innovation_signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Signal not found: {signal_id}")

        signal = dict(row)

        # Compute each dimension
        dimensions = {
            "community_demand": _score_community_demand(signal),
            "impact_breadth": _score_impact_breadth(signal, conn),
            "feasibility": _score_feasibility(signal, config),
            "compliance_alignment": _score_compliance_alignment(signal, config),
            "novelty": _score_novelty(signal, conn),
        }

        # Weighted average (D21 deterministic pattern)
        overall_score = sum(
            dimensions[dim] * weights.get(dim, 0.0) for dim in dimensions
        )
        overall_score = round(max(0.0, min(1.0, overall_score)), 4)

        # Determine threshold band
        if overall_score >= thresholds["auto_queue"]:
            threshold_band = "auto_queue"
        elif overall_score >= thresholds["suggest"]:
            threshold_band = "suggest"
        else:
            threshold_band = "log_only"

        # Build score breakdown JSON
        score_breakdown = {
            "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            "weights": weights,
            "overall": overall_score,
            "threshold_band": threshold_band,
            "scored_at": _now(),
        }

        # Update DB: set score, score_breakdown, transition status new -> scored
        conn.execute(
            """UPDATE innovation_signals
               SET innovation_score = ?,
                   score_breakdown = ?,
                   status = 'scored'
               WHERE id = ?""",
            (
                overall_score,
                json.dumps(score_breakdown),
                signal_id,
            ),
        )
        conn.commit()

        _audit(
            "innovation.score",
            "innovation-agent",
            f"Scored signal {signal_id}: {overall_score:.4f} ({threshold_band})",
            {
                "signal_id": signal_id,
                "score": overall_score,
                "threshold_band": threshold_band,
                "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            },
        )

        return {
            "signal_id": signal_id,
            "title": signal.get("title", ""),
            "source": signal.get("source", ""),
            "category": signal.get("category", ""),
            "score": overall_score,
            "threshold_band": threshold_band,
            "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            "weights_used": weights,
            "status": "scored",
            "scored_at": score_breakdown["scored_at"],
        }

    finally:
        conn.close()


def score_all_new(db_path=None):
    """Score all signals with status='new'.

    Iterates through unscored signals and applies the 5-dimension scoring.
    Respects max_signals_per_scan from config to prevent overload.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with count of scored signals, errors, and score distribution.
    """
    config = _load_config()
    max_signals = config.get("scoring", {}).get("max_signals_per_scan", 500)

    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT id FROM innovation_signals
               WHERE status = 'new'
               ORDER BY discovered_at ASC
               LIMIT ?""",
            (max_signals,),
        ).fetchall()
    finally:
        conn.close()

    signal_ids = [row["id"] for row in rows]

    scored = 0
    errors = 0
    error_details = []
    score_distribution = {"auto_queue": 0, "suggest": 0, "log_only": 0}

    for sid in signal_ids:
        try:
            result = score_signal(sid, db_path=db_path)
            scored += 1
            band = result.get("threshold_band", "log_only")
            score_distribution[band] = score_distribution.get(band, 0) + 1
        except Exception as e:
            errors += 1
            error_details.append({"signal_id": sid, "error": str(e)})

    _audit(
        "innovation.score_batch",
        "innovation-agent",
        f"Batch scored {scored} signals ({errors} errors)",
        {
            "scored": scored,
            "errors": errors,
            "distribution": score_distribution,
        },
    )

    return {
        "total_new": len(signal_ids),
        "scored": scored,
        "errors": errors,
        "error_details": error_details[:10],  # Cap error details
        "score_distribution": score_distribution,
        "scored_at": _now(),
    }


def get_top_signals(limit=20, min_score=0.5, db_path=None):
    """Get highest-scored innovation signals.

    Args:
        limit: Maximum number of signals to return (default 20).
        min_score: Minimum score threshold (default 0.5).
        db_path: Optional database path override.

    Returns:
        Dict with list of top signals and summary statistics.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT id, source, source_type, title, category, url,
                      innovation_score, score_breakdown, status, discovered_at
               FROM innovation_signals
               WHERE innovation_score IS NOT NULL AND innovation_score >= ?
               ORDER BY innovation_score DESC
               LIMIT ?""",
            (min_score, limit),
        ).fetchall()

        signals = []
        for row in rows:
            entry = dict(row)
            # Parse score_breakdown for dimension detail
            try:
                breakdown = json.loads(entry.get("score_breakdown") or "{}")
            except (json.JSONDecodeError, TypeError):
                breakdown = {}
            entry["score_breakdown"] = breakdown
            signals.append(entry)

        # Summary statistics
        total_scored = conn.execute(
            "SELECT COUNT(*) as cnt FROM innovation_signals WHERE innovation_score IS NOT NULL"
        ).fetchone()["cnt"]

        avg_score = conn.execute(
            "SELECT AVG(innovation_score) as avg_score FROM innovation_signals WHERE innovation_score IS NOT NULL"
        ).fetchone()["avg_score"] or 0.0

        distribution = {}
        thresholds = _get_thresholds()
        for band_name, band_min in [
            ("auto_queue", thresholds["auto_queue"]),
            ("suggest", thresholds["suggest"]),
            ("log_only", thresholds["log_only"]),
        ]:
            band_count = conn.execute(
                """SELECT COUNT(*) as cnt FROM innovation_signals
                   WHERE innovation_score IS NOT NULL AND innovation_score >= ?""",
                (band_min,),
            ).fetchone()["cnt"]
            distribution[band_name] = band_count

        # Correct distribution to be non-overlapping
        distribution["auto_queue"] = distribution.get("auto_queue", 0)
        distribution["suggest"] = (
            distribution.get("suggest", 0) - distribution.get("auto_queue", 0)
        )
        distribution["log_only"] = (
            distribution.get("log_only", 0)
            - distribution.get("suggest", 0)
            - distribution.get("auto_queue", 0)
        )

        return {
            "signals": signals,
            "count": len(signals),
            "total_scored": total_scored,
            "average_score": round(avg_score, 4),
            "distribution": distribution,
            "query": {"limit": limit, "min_score": min_score},
        }

    finally:
        conn.close()


def calibrate_weights(db_path=None):
    """Recalibrate scoring weights based on marketplace adoption feedback.

    Analyzes which score dimensions best predict marketplace success
    (install count, ratings) and adjusts weights accordingly. Uses
    feedback config for step size and minimum data point requirements.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with old weights, new weights, adjustment rationale, and data points used.
    """
    config = _load_config()
    feedback_config = config.get("feedback", {})
    adjustment_step = float(feedback_config.get("weight_adjustment_step", 0.02))
    min_data_points = int(feedback_config.get("min_data_points", 10))

    current_weights = _get_weights(config)
    old_weights = dict(current_weights)

    conn = _get_db(db_path)
    try:
        # Collect signals that have been implemented and have marketplace feedback
        # Join innovation_signals with marketplace_installations/ratings if available
        completed_signals = []
        try:
            rows = conn.execute(
                """SELECT s.id, s.innovation_score, s.score_breakdown, s.category
                   FROM innovation_signals s
                   WHERE s.status IN ('completed', 'queued')
                   AND s.innovation_score IS NOT NULL
                   AND s.score_breakdown IS NOT NULL
                   ORDER BY s.discovered_at DESC
                   LIMIT 500"""
            ).fetchall()
            completed_signals = [dict(r) for r in rows]
        except Exception:
            pass

        if len(completed_signals) < min_data_points:
            return {
                "calibrated": False,
                "reason": f"Insufficient data points ({len(completed_signals)}/{min_data_points})",
                "old_weights": old_weights,
                "new_weights": old_weights,
                "data_points": len(completed_signals),
                "adjustments": {},
            }

        # Analyze dimension correlations with success
        # Success heuristic: signals that reached 'completed' status are successes;
        # 'queued' signals with high scores that stalled may indicate scoring issues
        dimension_success = {dim: [] for dim in DEFAULT_WEIGHTS}
        dimension_stall = {dim: [] for dim in DEFAULT_WEIGHTS}

        for sig in completed_signals:
            try:
                breakdown = json.loads(sig.get("score_breakdown") or "{}")
                dims = breakdown.get("dimensions", {})
            except (json.JSONDecodeError, TypeError):
                continue

            if sig.get("status") == "completed":
                for dim, val in dims.items():
                    if dim in dimension_success:
                        dimension_success[dim].append(float(val))
            else:
                for dim, val in dims.items():
                    if dim in dimension_stall:
                        dimension_stall[dim].append(float(val))

        # Calculate average dimension scores for successes vs stalls
        adjustments = {}
        for dim in DEFAULT_WEIGHTS:
            success_vals = dimension_success.get(dim, [])
            stall_vals = dimension_stall.get(dim, [])

            success_avg = sum(success_vals) / len(success_vals) if success_vals else 0.5
            stall_avg = sum(stall_vals) / len(stall_vals) if stall_vals else 0.5

            # If successes score higher on this dimension, boost its weight
            delta = success_avg - stall_avg
            if delta > 0.05:
                adj = min(adjustment_step, delta * 0.1)
                adjustments[dim] = {"direction": "increase", "step": round(adj, 4)}
                current_weights[dim] = current_weights[dim] + adj
            elif delta < -0.05:
                adj = min(adjustment_step, abs(delta) * 0.1)
                adjustments[dim] = {"direction": "decrease", "step": round(adj, 4)}
                current_weights[dim] = max(0.02, current_weights[dim] - adj)
            else:
                adjustments[dim] = {"direction": "unchanged", "step": 0.0}

        # Re-normalize to sum to 1.0
        total = sum(current_weights.values())
        if total > 0:
            current_weights = {k: round(v / total, 4) for k, v in current_weights.items()}

        # Ensure no weight drops below 0.02 (2%)
        for dim in current_weights:
            if current_weights[dim] < 0.02:
                current_weights[dim] = 0.02

        # Final normalization
        total = sum(current_weights.values())
        if total > 0 and abs(total - 1.0) > 0.001:
            current_weights = {k: round(v / total, 4) for k, v in current_weights.items()}

        _audit(
            "innovation.calibrate",
            "innovation-agent",
            "Recalibrated scoring weights",
            {
                "old_weights": old_weights,
                "new_weights": current_weights,
                "adjustments": adjustments,
                "data_points": len(completed_signals),
            },
        )

        return {
            "calibrated": True,
            "old_weights": old_weights,
            "new_weights": current_weights,
            "adjustments": adjustments,
            "data_points": len(completed_signals),
            "calibrated_at": _now(),
            "note": "Weights computed but NOT persisted to YAML. "
                    "Review adjustments and update args/innovation_config.yaml manually.",
        }

    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Innovation Scoring Engine — score and rank innovation signals"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--score", action="store_true", help="Score a single signal")
    group.add_argument("--score-all", action="store_true", help="Score all new signals")
    group.add_argument("--top", action="store_true", help="Get top-scored signals")
    group.add_argument(
        "--calibrate", action="store_true", help="Recalibrate weights from feedback"
    )

    parser.add_argument(
        "--signal-id", type=str, help="Signal ID to score (with --score)"
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Max signals to return (with --top)"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.5,
        help="Minimum score threshold (with --top)",
    )

    args = parser.parse_args()

    try:
        if args.score:
            if not args.signal_id:
                parser.error("--score requires --signal-id")
            result = score_signal(args.signal_id, db_path=args.db_path)
        elif args.score_all:
            result = score_all_new(db_path=args.db_path)
        elif args.top:
            result = get_top_signals(
                limit=args.limit, min_score=args.min_score, db_path=args.db_path
            )
        elif args.calibrate:
            result = calibrate_weights(db_path=args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(args, result)

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _print_human(args, result):
    """Print human-readable output for each command."""
    if args.score:
        print(f"Signal:   {result.get('signal_id', '')}")
        print(f"Title:    {result.get('title', '')}")
        print(f"Source:   {result.get('source', '')}")
        print(f"Category: {result.get('category', '')}")
        print(f"Score:    {result.get('score', 0):.4f}  [{result.get('threshold_band', '')}]")
        print(f"Status:   {result.get('status', '')}")
        print("Dimensions:")
        for dim, val in result.get("dimensions", {}).items():
            weight = result.get("weights_used", {}).get(dim, 0)
            bar = "#" * int(val * 20)
            print(f"  {dim:25s} {val:.4f} (w={weight:.2f})  |{bar:<20s}|")

    elif args.score_all:
        print(f"Batch scoring completed at {result.get('scored_at', '')}")
        print(f"  New signals found: {result.get('total_new', 0)}")
        print(f"  Successfully scored: {result.get('scored', 0)}")
        print(f"  Errors: {result.get('errors', 0)}")
        dist = result.get("score_distribution", {})
        print("Score distribution:")
        print(f"  auto_queue (>=0.80): {dist.get('auto_queue', 0)}")
        print(f"  suggest    (>=0.50): {dist.get('suggest', 0)}")
        print(f"  log_only   (<0.50):  {dist.get('log_only', 0)}")
        if result.get("error_details"):
            print("Errors:")
            for err in result["error_details"][:5]:
                print(f"  {err['signal_id']}: {err['error']}")

    elif args.top:
        print(f"Top Innovation Signals (min_score={result.get('query', {}).get('min_score', 0.5)}):")
        print(f"  Total scored: {result.get('total_scored', 0)}")
        print(f"  Average score: {result.get('average_score', 0):.4f}")
        print(f"  Showing: {result.get('count', 0)} signals")
        print()
        for i, sig in enumerate(result.get("signals", []), 1):
            score = sig.get("score", 0) or 0
            print(f"  {i:2d}. [{score:.4f}] {sig.get('title', '')[:70]}")
            print(f"      Source: {sig.get('source', '')}  |  Status: {sig.get('status', '')}")
            breakdown = sig.get("score_breakdown", {})
            dims = breakdown.get("dimensions", {})
            if dims:
                dim_str = "  ".join(f"{k[:8]}={v:.2f}" for k, v in dims.items())
                print(f"      {dim_str}")
            print()

    elif args.calibrate:
        if result.get("calibrated"):
            print(f"Weights recalibrated at {result.get('calibrated_at', '')}")
            print(f"Data points used: {result.get('data_points', 0)}")
            print()
            print(f"  {'Dimension':<25s} {'Old':>8s} {'New':>8s} {'Direction':>12s}")
            print(f"  {'-' * 55}")
            old_w = result.get("old_weights", {})
            new_w = result.get("new_weights", {})
            for dim in DEFAULT_WEIGHTS:
                adj = result.get("adjustments", {}).get(dim, {})
                direction = adj.get("direction", "unchanged")
                print(
                    f"  {dim:<25s} {old_w.get(dim, 0):>8.4f} {new_w.get(dim, 0):>8.4f} {direction:>12s}"
                )
            print()
            print(f"NOTE: {result.get('note', '')}")
        else:
            print(f"Calibration skipped: {result.get('reason', 'unknown')}")
            print(f"Data points available: {result.get('data_points', 0)}")


if __name__ == "__main__":
    main()
