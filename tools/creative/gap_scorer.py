#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""3-Dimension Composite Gap Scorer for ICDEV Creative Engine.

Scores creative pain points using a 3-dimension weighted average
(D21 deterministic scoring pattern, D355):

  1. pain_frequency    (0.40) -- how often users mention this pain point
  2. gap_uniqueness    (0.35) -- how few competitors address this gap
  3. effort_to_impact  (0.25) -- estimated value / estimated effort ratio

Architecture:
    - Weights loaded from args/creative_config.yaml under scoring.weights (D26 pattern)
    - Thresholds: auto_spec >= 0.75, suggest >= 0.50, log_only < 0.50
    - Status transitions: new -> scored (after scoring)
    - Composite score + dimension breakdown stored in creative_pain_points table
    - Feature gaps identified from high-scoring pain points and stored in
      creative_feature_gaps table (append-only, D6)
    - All scoring is deterministic (D21 -- reproducible, not probabilistic)

Usage:
    # Score a single pain point
    python tools/creative/gap_scorer.py --score --pain-point-id "pp-xxx" --json

    # Score all unscored pain points (status='new')
    python tools/creative/gap_scorer.py --score-all --json

    # Get top-scored pain points
    python tools/creative/gap_scorer.py --top --limit 20 --min-score 0.5 --json

    # Identify feature gaps from scored pain points
    python tools/creative/gap_scorer.py --gaps --json

    # Human-readable output
    python tools/creative/gap_scorer.py --score-all --human
    python tools/creative/gap_scorer.py --top --limit 10 --human
"""

import argparse
import json
import math
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
# DEFAULT CONFIGURATION
# =========================================================================
DEFAULT_WEIGHTS = {
    "pain_frequency": 0.40,
    "gap_uniqueness": 0.35,
    "effort_to_impact": 0.25,
}

SEVERITY_WEIGHTS = {
    "critical": 1.0,
    "high": 0.75,
    "medium": 0.5,
    "low": 0.25,
}

# Complexity mapping by pain point category -- used for effort estimation
CATEGORY_COMPLEXITY = {
    "integration": 0.8,
    "api": 0.8,
    "security": 0.8,
    "compliance": 0.8,
    "performance": 0.6,
    "scalability": 0.6,
    "ux": 0.4,
    "reporting": 0.4,
    "customization": 0.4,
    "documentation": 0.4,
    "onboarding": 0.4,
    "automation": 0.4,
    "pricing": 0.3,
    "support": 0.3,
    "other": 0.3,
}

DEFAULT_THRESHOLDS = {
    "auto_spec": 0.75,
    "suggest": 0.50,
    "log_only": 0.0,
}


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gap_id():
    """Generate a feature gap ID with fg- prefix."""
    return f"fg-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (best-effort, never raises)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="creative-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="creative-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load creative config from YAML with fallback defaults."""
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
        "auto_spec": float(thresholds.get("auto_spec", DEFAULT_THRESHOLDS["auto_spec"])),
        "suggest": float(thresholds.get("suggest", DEFAULT_THRESHOLDS["suggest"])),
        "log_only": float(thresholds.get("log_only", DEFAULT_THRESHOLDS["log_only"])),
    }


def _get_severity_weights(config=None):
    """Extract severity weights from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    scoring = config.get("scoring", {})
    sev = scoring.get("severity_weights", {})
    result = {}
    for level, default_val in SEVERITY_WEIGHTS.items():
        result[level] = float(sev.get(level, default_val))
    return result


def _get_latest_by_fingerprint(conn, table="creative_pain_points"):
    """Get latest row per keyword_fingerprint (dedup for append-only table).

    Returns dict mapping keyword_fingerprint -> row dict.
    """
    rows = conn.execute(
        f"SELECT * FROM {table} ORDER BY rowid ASC"
    ).fetchall()
    latest = {}
    for row in rows:
        d = dict(row)
        fp = d.get("keyword_fingerprint")
        if fp:
            latest[fp] = d
    return latest


# =========================================================================
# DIMENSION SCORERS
# =========================================================================
def _score_pain_frequency(pain_point, conn):
    """Score pain frequency dimension.

    Measures how frequently this pain point appears relative to the total
    signal volume. Higher frequency = higher score.

    Args:
        pain_point: Dict of pain point row from DB.
        conn: Open database connection.

    Returns:
        Float in [0.0, 1.0].
    """
    # Get total signal count from creative_signals table
    try:
        total_signals = conn.execute(
            "SELECT COUNT(*) as cnt FROM creative_signals"
        ).fetchone()["cnt"]
    except Exception:
        total_signals = 0

    # Get this pain point's frequency
    frequency = int(pain_point.get("frequency", 1) or 1)

    # Normalize: frequency relative to 10% of total signals
    # If a pain point appears in 10% of all signals, that is a score of 1.0
    denominator = max(total_signals * 0.1, 1)
    score = min(frequency / denominator, 1.0)

    return max(0.0, min(1.0, score))


def _score_gap_uniqueness(pain_point, conn):
    """Score gap uniqueness dimension.

    Measures how few competitors address this pain point. Fewer competitors
    covering it means a bigger market gap (higher score).

    Args:
        pain_point: Dict of pain point row from DB.
        conn: Open database connection.

    Returns:
        Float in [0.0, 1.0]. Higher = bigger gap, fewer competitors address it.
    """
    # Get total confirmed competitors count
    try:
        total_confirmed = conn.execute(
            "SELECT COUNT(*) as cnt FROM creative_competitors WHERE status='confirmed'"
        ).fetchone()["cnt"]
    except Exception:
        total_confirmed = 0

    if total_confirmed == 0:
        # No confirmed competitors -- assume moderate gap
        return 0.6

    # Parse competitor_ids JSON from pain point
    try:
        competitor_ids_raw = pain_point.get("competitor_ids", "[]") or "[]"
        competitor_ids = json.loads(competitor_ids_raw)
        if not isinstance(competitor_ids, list):
            competitor_ids = []
    except (json.JSONDecodeError, TypeError):
        competitor_ids = []

    # Extract keywords from pain point for feature matching
    try:
        keywords_raw = pain_point.get("keywords", "[]") or "[]"
        keywords = json.loads(keywords_raw)
        if not isinstance(keywords, list):
            keywords = []
    except (json.JSONDecodeError, TypeError):
        keywords = []

    title_lower = (pain_point.get("title") or "").lower()
    desc_lower = (pain_point.get("description") or "").lower()
    pain_text = f"{title_lower} {desc_lower} {' '.join(keywords).lower()}"

    # For each confirmed competitor, check if their features overlap with pain keywords
    competitors_addressing = 0
    try:
        confirmed = conn.execute(
            "SELECT id, features FROM creative_competitors WHERE status='confirmed'"
        ).fetchall()
        for comp in confirmed:
            comp_id = comp["id"]
            try:
                features_raw = comp["features"] or "[]"
                features = json.loads(features_raw)
                if not isinstance(features, list):
                    features = []
            except (json.JSONDecodeError, TypeError):
                features = []

            if not features:
                continue

            # Check if any feature keyword matches pain point text
            features_text = " ".join(f.lower() for f in features)
            match_count = 0
            for kw in keywords:
                if len(kw) >= 3 and kw.lower() in features_text:
                    match_count += 1
            # Also check reverse: feature terms in pain text
            for feat in features:
                feat_words = feat.lower().split()
                for fw in feat_words:
                    if len(fw) >= 4 and fw in pain_text:
                        match_count += 1
                        break

            # Require at least 2 overlapping terms to count as "addressing"
            if match_count >= 2:
                competitors_addressing += 1
    except Exception:
        pass

    # gap_score = 1.0 - fraction of competitors that address this pain
    gap_score = 1.0 - (competitors_addressing / max(total_confirmed, 1))

    return max(0.0, min(1.0, gap_score))


def _score_effort_to_impact(pain_point, conn):
    """Score effort-to-impact dimension.

    Computes impact from frequency and severity, then divides by estimated
    complexity based on pain point category. Higher ratio = better investment.

    Args:
        pain_point: Dict of pain point row from DB.
        conn: Open database connection.

    Returns:
        Float in [0.0, 1.0].
    """
    config = _load_config()
    severity_weights = _get_severity_weights(config)

    # Impact = frequency * severity_weight
    frequency = int(pain_point.get("frequency", 1) or 1)
    severity = pain_point.get("severity", "medium") or "medium"
    severity_weight = severity_weights.get(severity, 0.5)
    impact = frequency * severity_weight

    # Estimate complexity from category
    category = pain_point.get("category", "other") or "other"
    complexity = CATEGORY_COMPLEXITY.get(category, 0.3)

    # Maximum possible impact: use the highest observed frequency * 1.0 (critical)
    try:
        max_freq_row = conn.execute(
            "SELECT MAX(frequency) as max_f FROM creative_pain_points"
        ).fetchone()
        max_frequency = max_freq_row["max_f"] if max_freq_row and max_freq_row["max_f"] else frequency
    except Exception:
        max_frequency = frequency

    max_possible_impact = max(max_frequency * 1.0, 1.0)

    # effort_to_impact ratio normalized to [0, 1]
    effort_to_impact = impact / (complexity * max_possible_impact)

    return max(0.0, min(1.0, effort_to_impact))


# =========================================================================
# SCORING FUNCTIONS
# =========================================================================
def score_pain_point(pain_point_id, db_path=None):
    """Score a single pain point across all 3 dimensions.

    Reads the pain point from DB, computes each dimension score, calculates
    the weighted average, inserts a new scored row (append-only), and
    returns the result.

    Args:
        pain_point_id: The pain point ID (e.g., "pp-abc123def456").
        db_path: Optional database path override.

    Returns:
        Dict with pain_point_id, composite_score, breakdown, status.
    """
    config = _load_config()
    weights = _get_weights(config)
    thresholds = _get_thresholds(config)

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM creative_pain_points WHERE id = ?", (pain_point_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Pain point not found: {pain_point_id}")

        pain_point = dict(row)

        # Compute each dimension
        dimensions = {
            "pain_frequency": _score_pain_frequency(pain_point, conn),
            "gap_uniqueness": _score_gap_uniqueness(pain_point, conn),
            "effort_to_impact": _score_effort_to_impact(pain_point, conn),
        }

        # Weighted average (D21 deterministic pattern)
        composite = sum(
            dimensions[dim] * weights.get(dim, 0.0) for dim in dimensions
        )
        composite = round(max(0.0, min(1.0, composite)), 4)

        # Determine threshold band
        if composite >= thresholds["auto_spec"]:
            threshold_band = "auto_spec"
        elif composite >= thresholds["suggest"]:
            threshold_band = "suggest"
        else:
            threshold_band = "log_only"

        # Build score breakdown JSON
        score_breakdown = {
            "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            "weights": weights,
            "composite": composite,
            "threshold_band": threshold_band,
            "scored_at": _now(),
        }

        # Append-only: INSERT new row with same keyword_fingerprint but scored status
        new_id = f"pp-{uuid.uuid4().hex[:12]}"
        conn.execute(
            """INSERT INTO creative_pain_points
               (id, title, description, category, frequency, signal_ids,
                competitor_ids, keyword_fingerprint, keywords, severity,
                status, composite_score, score_breakdown,
                first_seen, last_seen, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scored', ?, ?, ?, ?, 'CUI')""",
            (
                new_id,
                pain_point.get("title", ""),
                pain_point.get("description", ""),
                pain_point.get("category", "other"),
                pain_point.get("frequency", 1),
                pain_point.get("signal_ids", "[]"),
                pain_point.get("competitor_ids", "[]"),
                pain_point.get("keyword_fingerprint", ""),
                pain_point.get("keywords", "[]"),
                pain_point.get("severity", "medium"),
                composite,
                json.dumps(score_breakdown),
                pain_point.get("first_seen", _now()),
                _now(),
            ),
        )
        conn.commit()

        _audit(
            "creative.score",
            f"Scored pain point {pain_point_id} -> {new_id}: "
            f"{composite:.4f} ({threshold_band})",
            {
                "original_id": pain_point_id,
                "scored_id": new_id,
                "composite_score": composite,
                "threshold_band": threshold_band,
                "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            },
        )

        return {
            "pain_point_id": new_id,
            "original_id": pain_point_id,
            "title": pain_point.get("title", ""),
            "category": pain_point.get("category", ""),
            "severity": pain_point.get("severity", "medium"),
            "frequency": pain_point.get("frequency", 1),
            "composite_score": composite,
            "threshold_band": threshold_band,
            "breakdown": {k: round(v, 4) for k, v in dimensions.items()},
            "weights_used": weights,
            "status": "scored",
            "scored_at": score_breakdown["scored_at"],
        }

    finally:
        conn.close()


def score_all_new(db_path=None):
    """Score all pain points with status='new'.

    Deduplicates by keyword_fingerprint (takes latest by rowid), then
    scores each unique pain point.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with scored count, skipped count, avg_score, top_5.
    """
    conn = _get_db(db_path)
    try:
        # Get all new pain points
        rows = conn.execute(
            """SELECT * FROM creative_pain_points
               WHERE status = 'new'
               ORDER BY rowid ASC"""
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "scored": 0,
            "skipped": 0,
            "avg_score": 0.0,
            "top_5": [],
            "scored_at": _now(),
        }

    # Deduplicate by keyword_fingerprint (latest row per fingerprint)
    by_fingerprint = {}
    for row in rows:
        d = dict(row)
        fp = d.get("keyword_fingerprint", "")
        by_fingerprint[fp] = d  # last one wins

    unique_points = list(by_fingerprint.values())
    scored_count = 0
    skipped_count = 0
    all_scores = []
    results = []

    for pp in unique_points:
        try:
            result = score_pain_point(pp["id"], db_path=db_path)
            scored_count += 1
            all_scores.append(result["composite_score"])
            results.append(result)
        except Exception:
            skipped_count += 1

    # Compute average score
    avg_score = round(sum(all_scores) / max(len(all_scores), 1), 4) if all_scores else 0.0

    # Top 5 by composite_score
    results.sort(key=lambda r: r.get("composite_score", 0.0), reverse=True)
    top_5 = []
    for r in results[:5]:
        top_5.append({
            "pain_point_id": r["pain_point_id"],
            "title": r["title"],
            "composite_score": r["composite_score"],
            "threshold_band": r["threshold_band"],
            "category": r["category"],
        })

    _audit(
        "creative.score_batch",
        f"Batch scored {scored_count} pain points ({skipped_count} skipped)",
        {
            "scored": scored_count,
            "skipped": skipped_count,
            "avg_score": avg_score,
        },
    )

    return {
        "scored": scored_count,
        "skipped": skipped_count,
        "avg_score": avg_score,
        "top_5": top_5,
        "scored_at": _now(),
    }


def get_top_scored(limit=20, min_score=0.0, db_path=None):
    """Get highest-scored pain points.

    Deduplicates by keyword_fingerprint (latest row per fingerprint),
    filters by min_score, sorts descending.

    Args:
        limit: Maximum number of pain points to return (default 20).
        min_score: Minimum composite score threshold (default 0.0).
        db_path: Optional database path override.

    Returns:
        List of scored pain point dicts.
    """
    conn = _get_db(db_path)
    try:
        # Fetch all scored pain points
        rows = conn.execute(
            """SELECT * FROM creative_pain_points
               WHERE status = 'scored'
               AND composite_score IS NOT NULL
               ORDER BY rowid ASC"""
        ).fetchall()

        # Deduplicate by keyword_fingerprint (latest row wins)
        by_fingerprint = {}
        for row in rows:
            d = dict(row)
            fp = d.get("keyword_fingerprint", "")
            by_fingerprint[fp] = d

        # Filter by min_score and sort descending
        scored = []
        for pp in by_fingerprint.values():
            score = pp.get("composite_score", 0.0) or 0.0
            if score >= min_score:
                # Parse score_breakdown
                try:
                    breakdown = json.loads(pp.get("score_breakdown") or "{}")
                except (json.JSONDecodeError, TypeError):
                    breakdown = {}

                scored.append({
                    "pain_point_id": pp["id"],
                    "title": pp.get("title", ""),
                    "description": pp.get("description", ""),
                    "category": pp.get("category", ""),
                    "severity": pp.get("severity", "medium"),
                    "frequency": pp.get("frequency", 1),
                    "composite_score": score,
                    "breakdown": breakdown.get("dimensions", {}),
                    "threshold_band": breakdown.get("threshold_band", ""),
                    "keywords": json.loads(pp.get("keywords") or "[]"),
                    "signal_ids": json.loads(pp.get("signal_ids") or "[]"),
                    "competitor_ids": json.loads(pp.get("competitor_ids") or "[]"),
                    "first_seen": pp.get("first_seen", ""),
                    "last_seen": pp.get("last_seen", ""),
                })

        scored.sort(key=lambda x: x.get("composite_score", 0.0), reverse=True)
        return scored[:limit]

    finally:
        conn.close()


def identify_feature_gaps(db_path=None):
    """Identify feature gaps from scored pain points above the suggest threshold.

    For each qualifying pain point, creates a creative_feature_gaps row if one
    does not already exist for that pain point. Uses gap_uniqueness and
    pain_frequency scores from the pain point's breakdown.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with gaps_identified and total_gaps counts.
    """
    config = _load_config()
    thresholds = _get_thresholds(config)
    suggest_threshold = thresholds.get("suggest", 0.50)

    conn = _get_db(db_path)
    try:
        # Get all scored pain points above suggest threshold, deduped by fingerprint
        rows = conn.execute(
            """SELECT * FROM creative_pain_points
               WHERE status = 'scored'
               AND composite_score IS NOT NULL
               AND composite_score >= ?
               ORDER BY rowid ASC""",
            (suggest_threshold,),
        ).fetchall()

        # Deduplicate by keyword_fingerprint (latest row wins)
        by_fingerprint = {}
        for row in rows:
            d = dict(row)
            fp = d.get("keyword_fingerprint", "")
            by_fingerprint[fp] = d

        gaps_identified = 0
        now = _now()

        for pp in by_fingerprint.values():
            pp_id = pp["id"]

            # Check if a feature gap already exists for this pain point
            existing = conn.execute(
                "SELECT id FROM creative_feature_gaps WHERE pain_point_id = ?",
                (pp_id,),
            ).fetchone()
            if existing:
                continue

            # Parse score breakdown for dimension scores
            try:
                breakdown = json.loads(pp.get("score_breakdown") or "{}")
                dims = breakdown.get("dimensions", {})
            except (json.JSONDecodeError, TypeError):
                dims = {}

            gap_uniqueness_score = dims.get("gap_uniqueness", 0.5)
            pain_frequency_score = dims.get("pain_frequency", 0.5)

            # Derive feature name from pain point title
            title = pp.get("title", "Unknown Feature")
            feature_name = title
            # Clean up common prefixes
            for prefix in ("Pain: ", "Issue: ", "Problem: "):
                if feature_name.startswith(prefix):
                    feature_name = feature_name[len(prefix):]

            # Build competitor coverage dict
            competitor_coverage = {}
            try:
                competitor_ids_raw = pp.get("competitor_ids", "[]") or "[]"
                competitor_ids = json.loads(competitor_ids_raw)
                if not isinstance(competitor_ids, list):
                    competitor_ids = []
            except (json.JSONDecodeError, TypeError):
                competitor_ids = []

            try:
                keywords_raw = pp.get("keywords", "[]") or "[]"
                keywords = json.loads(keywords_raw)
                if not isinstance(keywords, list):
                    keywords = []
            except (json.JSONDecodeError, TypeError):
                keywords = []

            pain_text = f"{(pp.get('title') or '').lower()} {(pp.get('description') or '').lower()}"

            # Check each confirmed competitor
            try:
                confirmed_comps = conn.execute(
                    "SELECT id, name, features FROM creative_competitors WHERE status='confirmed'"
                ).fetchall()
                for comp in confirmed_comps:
                    try:
                        features = json.loads(comp["features"] or "[]")
                        if not isinstance(features, list):
                            features = []
                    except (json.JSONDecodeError, TypeError):
                        features = []

                    features_text = " ".join(f.lower() for f in features)
                    has_feature = False
                    match_count = 0
                    for kw in keywords:
                        if len(kw) >= 3 and kw.lower() in features_text:
                            match_count += 1
                    if match_count >= 2:
                        has_feature = True

                    competitor_coverage[comp["name"]] = has_feature
            except Exception:
                pass

            # Get signal_ids from pain point
            try:
                signal_ids = json.loads(pp.get("signal_ids") or "[]")
                if not isinstance(signal_ids, list):
                    signal_ids = []
            except (json.JSONDecodeError, TypeError):
                signal_ids = []

            # Insert feature gap
            fg_id = _gap_id()
            conn.execute(
                """INSERT INTO creative_feature_gaps
                   (id, pain_point_id, feature_name, description,
                    requested_by_count, competitor_coverage,
                    gap_score, market_demand, signal_ids,
                    status, metadata, discovered_at, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'identified', '{}', ?, 'CUI')""",
                (
                    fg_id,
                    pp_id,
                    feature_name,
                    pp.get("description", ""),
                    int(pp.get("frequency", 1) or 1),
                    json.dumps(competitor_coverage),
                    round(gap_uniqueness_score, 4),
                    round(pain_frequency_score, 4),
                    json.dumps(signal_ids),
                    now,
                ),
            )
            gaps_identified += 1

        conn.commit()

        # Total feature gaps in DB
        total_gaps = conn.execute(
            "SELECT COUNT(*) as cnt FROM creative_feature_gaps"
        ).fetchone()["cnt"]

    finally:
        conn.close()

    _audit(
        "creative.gaps",
        f"Identified {gaps_identified} feature gaps ({total_gaps} total)",
        {"gaps_identified": gaps_identified, "total_gaps": total_gaps},
    )

    return {
        "gaps_identified": gaps_identified,
        "total_gaps": total_gaps,
        "threshold_used": suggest_threshold,
        "identified_at": _now(),
    }


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _print_human(args, result):
    """Print human-readable output for each command."""
    print("=" * 70)
    print("  GAP SCORER -- CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 70)
        return

    if args.score and not args.score_all:
        print(f"\n  Pain Point: {result.get('pain_point_id', '')}")
        print(f"  Original:   {result.get('original_id', '')}")
        print(f"  Title:      {result.get('title', '')}")
        print(f"  Category:   {result.get('category', '')}")
        print(f"  Severity:   {result.get('severity', '')}")
        print(f"  Frequency:  {result.get('frequency', 0)}")
        print(f"  Score:      {result.get('composite_score', 0):.4f}  "
              f"[{result.get('threshold_band', '')}]")
        print(f"  Status:     {result.get('status', '')}")
        print()
        print("  Dimensions:")
        for dim, val in result.get("breakdown", {}).items():
            weight = result.get("weights_used", {}).get(dim, 0)
            bar = "#" * int(val * 20)
            print(f"    {dim:20s} {val:.4f} (w={weight:.2f})  |{bar:<20s}|")

    elif args.score_all:
        print(f"\n  Batch scoring completed at {result.get('scored_at', '')}")
        print(f"    Scored:  {result.get('scored', 0)}")
        print(f"    Skipped: {result.get('skipped', 0)}")
        print(f"    Average: {result.get('avg_score', 0):.4f}")
        if result.get("top_5"):
            print()
            print("  Top 5 Pain Points:")
            print(f"    {'#':>3s}  {'Score':>7s}  {'Band':>10s}  {'Category':>14s}  Title")
            sep = "-" * 14
            print(f"    {'---':>3s}  {'-------':>7s}  {'----------':>10s}  "
                  f"{sep:>14s}  -----")
            for i, t in enumerate(result["top_5"], 1):
                print(f"    {i:3d}  {t['composite_score']:7.4f}  "
                      f"{t['threshold_band']:>10s}  {t['category']:>14s}  "
                      f"{t['title'][:40]}")

    elif args.top:
        if isinstance(result, list):
            print(f"\n  Top Scored Pain Points ({len(result)} results):")
            print()
            for i, pp in enumerate(result, 1):
                score = pp.get("composite_score", 0)
                band = pp.get("threshold_band", "")
                print(f"  {i:3d}. [{score:.4f}] {pp.get('title', '')[:60]}")
                print(f"       Category: {pp.get('category', '')}  |  "
                      f"Severity: {pp.get('severity', '')}  |  "
                      f"Freq: {pp.get('frequency', 0)}  |  Band: {band}")
                dims = pp.get("breakdown", {})
                if dims:
                    dim_str = "  ".join(
                        f"{k[:10]}={v:.2f}" for k, v in dims.items()
                    )
                    print(f"       {dim_str}")
                print()
        else:
            print(f"\n  No results.")

    elif args.gaps:
        print(f"\n  Feature Gap Identification")
        print(f"    Gaps identified:  {result.get('gaps_identified', 0)}")
        print(f"    Total gaps in DB: {result.get('total_gaps', 0)}")
        print(f"    Threshold used:   {result.get('threshold_used', 0):.2f}")
        print(f"    Identified at:    {result.get('identified_at', '')}")

    print()
    print("=" * 70)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ICDEV Creative Engine Gap Scorer -- CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --score --pain-point-id pp-abc123 --json\n"
            "  %(prog)s --score-all --json\n"
            "  %(prog)s --top --limit 10 --min-score 0.5 --json\n"
            "  %(prog)s --gaps --json\n"
            "  %(prog)s --top --human\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--score", action="store_true", help="Score a single pain point"
    )
    group.add_argument(
        "--score-all", action="store_true", help="Score all new (unscored) pain points"
    )
    group.add_argument(
        "--top", action="store_true", help="Get top-scored pain points"
    )
    group.add_argument(
        "--gaps", action="store_true", help="Identify feature gaps from scored pain points"
    )

    parser.add_argument(
        "--pain-point-id", type=str, help="Pain point ID to score (with --score)"
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Max pain points to return (with --top)"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum score threshold (with --top)",
    )

    args = parser.parse_args()

    try:
        if args.score and not args.score_all:
            if not args.pain_point_id:
                parser.error("--score requires --pain-point-id")
            result = score_pain_point(args.pain_point_id, db_path=args.db_path)
        elif args.score_all:
            result = score_all_new(db_path=args.db_path)
        elif args.top:
            result = get_top_scored(
                limit=args.limit, min_score=args.min_score, db_path=args.db_path
            )
        elif args.gaps:
            result = identify_feature_gaps(db_path=args.db_path)
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
