#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Compliance-First Triage Pipeline — 5-stage safety gate for innovation signals.

Every innovation signal discovered by the web scanner passes through this
5-stage pipeline before solution generation is permitted. Each stage can BLOCK
the signal, preventing unsafe, non-compliant, or duplicate innovations from
entering the build pipeline.

Pipeline Stages:
    1. Classify Signal     — Map to signal_categories from innovation_config.yaml
    2. GOTCHA Fit Check    — Signal must map to at least one GOTCHA layer
    3. Boundary Impact     — Estimate ATO boundary impact (GREEN/YELLOW/ORANGE/RED)
    4. Compliance Pre-Check — Detect compliance-weakening anti-patterns
    5. Duplicate/License   — Content-hash dedup + blocked license detection

Triage Outcomes:
    - approved  — All stages passed, score >= auto_queue threshold (0.80)
    - suggested — All stages passed, score >= suggest threshold (0.50)
    - blocked   — One or more stages blocked the signal
    - logged    — Score below suggest threshold, logged for trend analysis

Architecture:
    - All triage decisions stored in innovation_triage_log (append-only, D6)
    - Signals updated with triage_result, gotcha_layer, boundary_tier columns
    - Status transitions: scored -> triaged (after triage completes)
    - Config-driven thresholds from args/innovation_config.yaml (D26 pattern)
    - Audit trail via audit_logger (NIST AU compliance)

Usage:
    # Triage a single signal
    python tools/innovation/triage_engine.py --triage --signal-id "sig-xxx" --json

    # Triage all scored signals
    python tools/innovation/triage_engine.py --triage-all --json

    # Get triage summary
    python tools/innovation/triage_engine.py --summary --json

    # Triage with custom DB path
    python tools/innovation/triage_engine.py --triage-all --db-path /path/to/icdev.db --json
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
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
# CONSTANTS
# =========================================================================
GOTCHA_LAYERS = ["goal", "tool", "arg", "context", "hardprompt"]

BOUNDARY_TIERS = {
    "GREEN": "No ATO boundary change",
    "YELLOW": "Minor adjustment — new component within boundary",
    "ORANGE": "Significant change — cross-boundary data flow",
    "RED": "ATO-invalidating — boundary expansion or classification change",
}

# Keywords that indicate compliance-weakening intent
COMPLIANCE_ANTI_PATTERNS = [
    r"\bdisable\s+security\b",
    r"\bskip\s+(checks?|validation|gates?|scans?|tests?)\b",
    r"\bremove\s+(gates?|controls?|guardrails?|restrictions?)\b",
    r"\bbypass\s+(auth|authentication|authorization|mfa|rbac)\b",
    r"\bno\s+(encryption|tls|mtls|audit)\b",
    r"\bweaken\s+(security|compliance|controls?)\b",
    r"\bignore\s+(stig|nist|fedramp|cmmc|cve|vulnerability)\b",
    r"\bhardcode\s+(secret|password|key|credential|token)\b",
    r"\bdisable\s+(logging|audit|monitoring|alerting)\b",
    r"\ballow\s+anonymous\b",
    r"\broot\s+access\b",
    r"\bprivileged\s+container\b",
    r"\bno\s+rbac\b",
]

# Keywords indicating new external connections (ORANGE+)
EXTERNAL_CONNECTION_KEYWORDS = [
    "external api", "third-party", "third party", "new endpoint",
    "outbound connection", "webhook to external", "saas integration",
    "public internet", "cross-boundary", "inter-enclave",
]

# Keywords indicating classification changes (RED)
CLASSIFICATION_CHANGE_KEYWORDS = [
    "classification change", "upgrade to secret", "downgrade",
    "reclassify", "il6", "sipr", "secret data", "ts/sci",
    "boundary expansion", "new enclave", "new authorization boundary",
]

# Keywords indicating new data flows (YELLOW+)
DATA_FLOW_KEYWORDS = [
    "new data flow", "data exchange", "new integration",
    "ingest from", "export to", "data pipeline",
    "new database", "new storage", "new queue",
]

# License patterns for detection in signal text
LICENSE_PATTERNS = {
    "GPL-3.0": [r"\bGPL[\s-]?3", r"\bGPLv3\b", r"\bGNU General Public License.*3\b"],
    "AGPL-3.0": [r"\bAGPL[\s-]?3", r"\bAGPLv3\b", r"\bAffero\b"],
    "SSPL-1.0": [r"\bSSPL\b", r"\bServer Side Public License\b"],
    "BSL-1.1": [r"\bBSL[\s-]?1\.1\b", r"\bBusiness Source License\b"],
}


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None, project_id=None):
    """Write audit trail entry."""
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
    """Load innovation config from YAML."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _ensure_triage_tables(conn):
    """Ensure the innovation_triage_log table exists.

    Creates the append-only triage log table if it does not already exist.
    Also adds triage columns to innovation_signals if they are missing.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS innovation_triage_log (
            id TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            stage INTEGER NOT NULL,
            stage_name TEXT NOT NULL,
            result TEXT NOT NULL CHECK(result IN ('pass', 'block', 'warn')),
            details TEXT,
            triaged_at TEXT NOT NULL
        )
    """)

    # Add triage columns to innovation_signals if not present
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(innovation_signals)").fetchall()
    }

    alter_stmts = []
    if "triage_result" not in existing_cols:
        alter_stmts.append(
            "ALTER TABLE innovation_signals ADD COLUMN triage_result TEXT"
        )
    if "gotcha_layer" not in existing_cols:
        alter_stmts.append(
            "ALTER TABLE innovation_signals ADD COLUMN gotcha_layer TEXT"
        )
    if "boundary_tier" not in existing_cols:
        alter_stmts.append(
            "ALTER TABLE innovation_signals ADD COLUMN boundary_tier TEXT"
        )

    for stmt in alter_stmts:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # Column may already exist in a concurrent scenario

    conn.commit()


def _log_triage_stage(conn, signal_id, stage, stage_name, result, details):
    """Insert a triage log entry (append-only)."""
    log_id = f"tlog-{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO innovation_triage_log
           (id, signal_id, stage, stage_name, result, details, triaged_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            log_id,
            signal_id,
            stage,
            stage_name,
            result,
            json.dumps(details) if isinstance(details, dict) else details,
            _now(),
        ),
    )
    return log_id


# =========================================================================
# STAGE 1: CLASSIFY SIGNAL
# =========================================================================
def _stage_classify_signal(signal, config):
    """Map signal to signal_categories via keyword matching.

    Scans signal title + description against each category's keyword list.
    Returns the best-matching category (highest keyword hit count).

    Args:
        signal: Dict with at least 'title' and 'description'.
        config: Full innovation config dict.

    Returns:
        Tuple of (result, details) where result is 'pass' or 'warn'.
        Never blocks — classification is informational.
    """
    categories = config.get("signal_categories", {})
    if not categories:
        return "pass", {
            "category": "uncategorized",
            "priority_boost": 1.0,
            "auto_triage": False,
            "note": "No signal_categories in config; defaulting to uncategorized",
        }

    text = f"{signal.get('title', '')} {signal.get('description', '')}".lower()

    best_category = None
    best_hits = 0
    best_boost = 1.0
    best_auto = False

    for cat_name, cat_config in categories.items():
        keywords = cat_config.get("keywords", [])
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits > best_hits:
            best_hits = hits
            best_category = cat_name
            best_boost = cat_config.get("priority_boost", 1.0)
            best_auto = cat_config.get("auto_triage", False)

    if best_category is None:
        best_category = "uncategorized"

    return "pass", {
        "category": best_category,
        "keyword_hits": best_hits,
        "priority_boost": best_boost,
        "auto_triage": best_auto,
    }


# =========================================================================
# STAGE 2: GOTCHA FIT CHECK
# =========================================================================
def _stage_gotcha_fit(signal, config):
    """Check whether signal maps to at least one GOTCHA layer.

    Uses triage.gotcha_fit.layer_mapping from config to match keywords
    in the signal title + description against each GOTCHA layer.

    Args:
        signal: Dict with at least 'title' and 'description'.
        config: Full innovation config dict.

    Returns:
        Tuple of (result, details). Blocks if no GOTCHA layer matches
        and triage.gotcha_fit.required_layer is true.
    """
    triage_config = config.get("triage", {})
    gotcha_config = triage_config.get("gotcha_fit", {})
    layer_mapping = gotcha_config.get("layer_mapping", {})
    required = gotcha_config.get("required_layer", True)

    text = f"{signal.get('title', '')} {signal.get('description', '')}".lower()

    matched_layers = []
    layer_scores = {}

    for layer, keywords in layer_mapping.items():
        hits = sum(1 for kw in keywords if kw.lower() in text)
        layer_scores[layer] = hits
        if hits > 0:
            matched_layers.append(layer)

    if not matched_layers and required:
        return "block", {
            "matched_layers": [],
            "layer_scores": layer_scores,
            "reason": "Signal does not map to any GOTCHA layer (goal/tool/arg/context/hardprompt)",
        }

    # Pick the best-matching layer (most keyword hits)
    best_layer = None
    if matched_layers:
        best_layer = max(matched_layers, key=lambda l: layer_scores.get(l, 0))

    return "pass", {
        "matched_layers": matched_layers,
        "best_layer": best_layer,
        "layer_scores": layer_scores,
    }


# =========================================================================
# STAGE 3: BOUNDARY IMPACT ASSESSMENT
# =========================================================================
def _stage_boundary_impact(signal, config):
    """Estimate ATO boundary impact tier.

    Analyzes signal text for indicators of boundary change severity:
    - RED:    Classification changes, boundary expansion (BLOCKS)
    - ORANGE: Cross-boundary data flows, new external connections (WARN)
    - YELLOW: New data flows within boundary (PASS with note)
    - GREEN:  No boundary impact (PASS)

    Args:
        signal: Dict with at least 'title' and 'description'.
        config: Full innovation config dict.

    Returns:
        Tuple of (result, details). Blocks on RED if configured.
    """
    triage_config = config.get("triage", {})
    boundary_config = triage_config.get("boundary_check", {})
    block_on_red = boundary_config.get("block_on_red", True)
    require_coa_orange = boundary_config.get("require_coa_for_orange", True)

    if not boundary_config.get("enabled", True):
        return "pass", {"tier": "GREEN", "note": "Boundary check disabled in config"}

    text = f"{signal.get('title', '')} {signal.get('description', '')}".lower()

    # Check for RED indicators (most severe first)
    red_hits = [kw for kw in CLASSIFICATION_CHANGE_KEYWORDS if kw.lower() in text]
    if red_hits:
        result = "block" if block_on_red else "warn"
        return result, {
            "tier": "RED",
            "indicators": red_hits,
            "reason": "Signal implies ATO-invalidating change (classification or boundary expansion)",
            "blocked": block_on_red,
        }

    # Check for ORANGE indicators
    orange_hits = [kw for kw in EXTERNAL_CONNECTION_KEYWORDS if kw.lower() in text]
    if orange_hits:
        return "warn", {
            "tier": "ORANGE",
            "indicators": orange_hits,
            "reason": "Signal implies cross-boundary or external connection change",
            "requires_coa": require_coa_orange,
        }

    # Check for YELLOW indicators
    yellow_hits = [kw for kw in DATA_FLOW_KEYWORDS if kw.lower() in text]
    if yellow_hits:
        return "pass", {
            "tier": "YELLOW",
            "indicators": yellow_hits,
            "reason": "Signal implies new data flow within existing boundary",
        }

    # Default: GREEN
    return "pass", {
        "tier": "GREEN",
        "indicators": [],
        "reason": "No boundary impact indicators detected",
    }


# =========================================================================
# STAGE 4: COMPLIANCE PRE-CHECK
# =========================================================================
def _stage_compliance_precheck(signal, config):
    """Check whether implementing this signal would weaken compliance posture.

    Scans signal text for anti-patterns that indicate compliance-weakening
    intent (e.g., "disable security", "skip checks", "bypass auth").

    Args:
        signal: Dict with at least 'title' and 'description'.
        config: Full innovation config dict.

    Returns:
        Tuple of (result, details). Blocks if compliance-weakening detected
        and triage.compliance_check.block_on_weakening is true.
    """
    triage_config = config.get("triage", {})
    compliance_config = triage_config.get("compliance_check", {})

    if not compliance_config.get("enabled", True):
        return "pass", {"note": "Compliance pre-check disabled in config"}

    block_on_weakening = compliance_config.get("block_on_weakening", True)
    text = f"{signal.get('title', '')} {signal.get('description', '')}".lower()

    violations = []
    for pattern in COMPLIANCE_ANTI_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            violations.append({
                "pattern": pattern,
                "matches": matches,
            })

    if violations:
        result = "block" if block_on_weakening else "warn"
        return result, {
            "compliance_weakening": True,
            "violation_count": len(violations),
            "violations": violations,
            "reason": "Signal contains compliance-weakening anti-patterns",
            "blocked": block_on_weakening,
        }

    # Also check which frameworks would be validated
    frameworks = compliance_config.get("frameworks_to_validate", [])

    return "pass", {
        "compliance_weakening": False,
        "violation_count": 0,
        "frameworks_validated": frameworks,
    }


# =========================================================================
# STAGE 5: DUPLICATE / LICENSE CHECK
# =========================================================================
def _stage_duplicate_license(signal, config, conn):
    """Check for duplicate signals and blocked licenses.

    Deduplication: compares content_hash against existing signals within
    the configured time window (triage.dedup.time_window_days). Uses exact
    hash match (not semantic similarity, which requires embeddings).

    License: scans signal text for references to blocked licenses
    (GPL-3.0, AGPL-3.0, SSPL-1.0, BSL-1.1 from triage.license_check).

    Args:
        signal: Dict with at least 'title', 'description', 'content_hash'.
        config: Full innovation config dict.
        conn: Active database connection.

    Returns:
        Tuple of (result, details). Blocks on duplicate or blocked license.
    """
    triage_config = config.get("triage", {})
    dedup_config = triage_config.get("dedup", {})
    license_config = triage_config.get("license_check", {})

    issues = []

    # --- Duplicate check ---
    if dedup_config.get("enabled", True):
        time_window = dedup_config.get("time_window_days", 90)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=time_window)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        content_hash = signal.get("content_hash", "")

        if content_hash:
            existing = conn.execute(
                """SELECT id, title, discovered_at
                   FROM innovation_signals
                   WHERE content_hash = ?
                     AND id != ?
                     AND discovered_at >= ?
                   ORDER BY discovered_at DESC
                   LIMIT 1""",
                (content_hash, signal.get("id", ""), cutoff),
            ).fetchone()

            if existing:
                issues.append({
                    "type": "duplicate",
                    "existing_signal_id": existing["id"],
                    "existing_title": existing["title"],
                    "existing_date": existing["discovered_at"],
                })

        # Also do a title similarity check (simple exact-prefix match)
        title = signal.get("title", "").strip()
        if title and len(title) > 20:
            title_prefix = title[:50]
            similar = conn.execute(
                """SELECT id, title
                   FROM innovation_signals
                   WHERE title LIKE ?
                     AND id != ?
                     AND discovered_at >= ?
                   LIMIT 3""",
                (f"{title_prefix}%", signal.get("id", ""), cutoff),
            ).fetchall()

            if similar:
                issues.append({
                    "type": "similar_title",
                    "similar_count": len(similar),
                    "similar_signals": [
                        {"id": s["id"], "title": s["title"]} for s in similar
                    ],
                })

    # --- License check ---
    if license_config.get("enabled", True):
        blocked_licenses = license_config.get("blocked_licenses", [])
        text = f"{signal.get('title', '')} {signal.get('description', '')} {signal.get('metadata', '')}"

        detected_licenses = []
        for license_id, patterns in LICENSE_PATTERNS.items():
            if license_id in blocked_licenses or any(
                bl.replace("-", "").lower() == license_id.replace("-", "").lower()
                for bl in blocked_licenses
            ):
                for pat in patterns:
                    if re.search(pat, text, re.IGNORECASE):
                        detected_licenses.append(license_id)
                        break

        if detected_licenses:
            issues.append({
                "type": "blocked_license",
                "licenses": detected_licenses,
                "allowed_licenses": license_config.get("allowed_licenses", []),
            })

    # Determine result
    has_duplicate = any(i["type"] == "duplicate" for i in issues)
    has_blocked_license = any(i["type"] == "blocked_license" for i in issues)

    if has_duplicate or has_blocked_license:
        reasons = []
        if has_duplicate:
            reasons.append("Duplicate signal detected within time window")
        if has_blocked_license:
            lics = [
                i["licenses"]
                for i in issues
                if i["type"] == "blocked_license"
            ]
            flat_lics = [l for sublist in lics for l in sublist]
            reasons.append(f"Blocked license(s) detected: {', '.join(flat_lics)}")
        return "block", {
            "issues": issues,
            "reason": "; ".join(reasons),
        }

    has_similar = any(i["type"] == "similar_title" for i in issues)
    if has_similar:
        return "warn", {
            "issues": issues,
            "reason": "Similar signal titles found (not exact duplicate)",
        }

    return "pass", {
        "issues": [],
        "reason": "No duplicates or license issues detected",
    }


# =========================================================================
# TRIAGE ORCHESTRATOR
# =========================================================================
STAGE_REGISTRY = [
    (1, "classify_signal", _stage_classify_signal),
    (2, "gotcha_fit_check", _stage_gotcha_fit),
    (3, "boundary_impact", _stage_boundary_impact),
    (4, "compliance_precheck", _stage_compliance_precheck),
    # Stage 5 handled separately (needs DB connection)
]


def triage_signal(signal_id, db_path=None):
    """Run the full 5-stage compliance-first triage on a single signal.

    Pipeline stages execute in order. Any stage returning 'block' marks the
    signal as blocked and stops further evaluation (fail-fast). Warnings
    are accumulated but do not block.

    Args:
        signal_id: ID of the signal in innovation_signals table.
        db_path: Optional database path override.

    Returns:
        Dict with triage outcome, per-stage results, and final disposition.
    """
    conn = _get_db(db_path)
    try:
        _ensure_triage_tables(conn)

        # Fetch the signal
        signal_row = conn.execute(
            "SELECT * FROM innovation_signals WHERE id = ?",
            (signal_id,),
        ).fetchone()

        if not signal_row:
            return {"error": f"Signal not found: {signal_id}"}

        signal = dict(signal_row)
        config = _load_config()

        # Load scoring thresholds
        scoring = config.get("scoring", {})
        thresholds = scoring.get("thresholds", {})
        auto_queue_threshold = thresholds.get("auto_queue", 0.80)
        suggest_threshold = thresholds.get("suggest", 0.50)

        stage_results = []
        blocked = False
        block_stage = None
        warnings = []
        gotcha_layer = None
        boundary_tier = "GREEN"
        category = None

        # --- Stages 1-4 (signal + config only) ---
        for stage_num, stage_name, stage_fn in STAGE_REGISTRY:
            result, details = stage_fn(signal, config)

            _log_triage_stage(conn, signal_id, stage_num, stage_name, result, details)

            stage_results.append({
                "stage": stage_num,
                "name": stage_name,
                "result": result,
                "details": details,
            })

            if result == "block":
                blocked = True
                block_stage = stage_name
                break
            elif result == "warn":
                warnings.append(stage_name)

            # Extract metadata from stage results
            if stage_name == "classify_signal":
                category = details.get("category", "uncategorized")
            elif stage_name == "gotcha_fit_check":
                gotcha_layer = details.get("best_layer")
            elif stage_name == "boundary_impact":
                boundary_tier = details.get("tier", "GREEN")

        # --- Stage 5 (needs DB for dedup) ---
        if not blocked:
            result, details = _stage_duplicate_license(signal, config, conn)

            _log_triage_stage(conn, signal_id, 5, "duplicate_license_check", result, details)

            stage_results.append({
                "stage": 5,
                "name": "duplicate_license_check",
                "result": result,
                "details": details,
            })

            if result == "block":
                blocked = True
                block_stage = "duplicate_license_check"
            elif result == "warn":
                warnings.append("duplicate_license_check")

        # --- Determine final triage outcome ---
        score = signal.get("community_score", 0.0) or 0.0

        # Apply priority boost from category classification
        if category and not blocked:
            cat_config = config.get("signal_categories", {}).get(category, {})
            priority_boost = cat_config.get("priority_boost", 1.0)
            score = min(score * priority_boost, 1.0)

        if blocked:
            triage_result = "blocked"
        elif score >= auto_queue_threshold:
            triage_result = "approved"
        elif score >= suggest_threshold:
            triage_result = "suggested"
        else:
            triage_result = "logged"

        # --- Update signal record ---
        conn.execute(
            """UPDATE innovation_signals
               SET status = 'triaged',
                   triage_result = ?,
                   gotcha_layer = ?,
                   boundary_tier = ?,
                   category = ?
               WHERE id = ?""",
            (triage_result, gotcha_layer, boundary_tier, category, signal_id),
        )
        conn.commit()

        outcome = {
            "signal_id": signal_id,
            "title": signal.get("title", ""),
            "triage_result": triage_result,
            "score": round(score, 4),
            "category": category,
            "gotcha_layer": gotcha_layer,
            "boundary_tier": boundary_tier,
            "blocked": blocked,
            "block_stage": block_stage,
            "warnings": warnings,
            "stages": stage_results,
            "thresholds": {
                "auto_queue": auto_queue_threshold,
                "suggest": suggest_threshold,
            },
            "triaged_at": _now(),
        }

        _audit(
            "innovation.triage",
            "triage-engine",
            f"Triaged signal {signal_id}: {triage_result}",
            {
                "signal_id": signal_id,
                "result": triage_result,
                "blocked": blocked,
                "block_stage": block_stage,
                "category": category,
                "boundary_tier": boundary_tier,
            },
        )

        return outcome

    finally:
        conn.close()


def triage_all_scored(db_path=None):
    """Triage all signals currently in 'scored' status.

    Iterates over every signal with status='scored' and runs the full
    5-stage pipeline. Continues on individual signal errors.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with counts per triage outcome and list of individual results.
    """
    conn = _get_db(db_path)
    try:
        _ensure_triage_tables(conn)

        signals = conn.execute(
            """SELECT id FROM innovation_signals
               WHERE status = 'scored'
               ORDER BY community_score DESC""",
        ).fetchall()
    finally:
        conn.close()

    signal_ids = [row["id"] for row in signals]

    results = []
    counts = {"approved": 0, "suggested": 0, "blocked": 0, "logged": 0, "error": 0}

    for sid in signal_ids:
        try:
            outcome = triage_signal(sid, db_path=db_path)
            if "error" in outcome:
                counts["error"] += 1
                results.append({"signal_id": sid, "error": outcome["error"]})
            else:
                triage_result = outcome.get("triage_result", "logged")
                counts[triage_result] = counts.get(triage_result, 0) + 1
                results.append({
                    "signal_id": sid,
                    "title": outcome.get("title", ""),
                    "triage_result": triage_result,
                    "score": outcome.get("score", 0.0),
                    "category": outcome.get("category"),
                    "gotcha_layer": outcome.get("gotcha_layer"),
                    "boundary_tier": outcome.get("boundary_tier"),
                    "blocked": outcome.get("blocked", False),
                    "block_stage": outcome.get("block_stage"),
                })
        except Exception as e:
            counts["error"] += 1
            results.append({"signal_id": sid, "error": str(e)})

    _audit(
        "innovation.triage_batch",
        "triage-engine",
        f"Batch triaged {len(signal_ids)} signals",
        counts,
    )

    return {
        "total_processed": len(signal_ids),
        "counts": counts,
        "results": results,
        "triaged_at": _now(),
    }


def get_triage_summary(db_path=None):
    """Summarize triage outcomes across all triaged signals.

    Provides aggregate counts by triage result, category, GOTCHA layer,
    boundary tier, and recent triage log entries.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with summary statistics and recent triage activity.
    """
    conn = _get_db(db_path)
    try:
        _ensure_triage_tables(conn)

        # Count by triage result
        by_result = {}
        rows = conn.execute(
            """SELECT triage_result, COUNT(*) as cnt
               FROM innovation_signals
               WHERE triage_result IS NOT NULL
               GROUP BY triage_result"""
        ).fetchall()
        for row in rows:
            by_result[row["triage_result"] or "none"] = row["cnt"]

        # Count by category
        by_category = {}
        rows = conn.execute(
            """SELECT category, COUNT(*) as cnt
               FROM innovation_signals
               WHERE status = 'triaged'
               GROUP BY category"""
        ).fetchall()
        for row in rows:
            by_category[row["category"] or "uncategorized"] = row["cnt"]

        # Count by GOTCHA layer
        by_gotcha = {}
        rows = conn.execute(
            """SELECT gotcha_layer, COUNT(*) as cnt
               FROM innovation_signals
               WHERE gotcha_layer IS NOT NULL
               GROUP BY gotcha_layer"""
        ).fetchall()
        for row in rows:
            by_gotcha[row["gotcha_layer"]] = row["cnt"]

        # Count by boundary tier
        by_boundary = {}
        rows = conn.execute(
            """SELECT boundary_tier, COUNT(*) as cnt
               FROM innovation_signals
               WHERE boundary_tier IS NOT NULL
               GROUP BY boundary_tier"""
        ).fetchall()
        for row in rows:
            by_boundary[row["boundary_tier"]] = row["cnt"]

        # Pending triage (scored but not yet triaged)
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM innovation_signals WHERE status = 'scored'"
        ).fetchone()["cnt"]

        # Recent triage log entries (last 20 blocked)
        recent_blocks = []
        rows = conn.execute(
            """SELECT tl.signal_id, tl.stage_name, tl.details, tl.triaged_at,
                      s.title
               FROM innovation_triage_log tl
               LEFT JOIN innovation_signals s ON s.id = tl.signal_id
               WHERE tl.result = 'block'
               ORDER BY tl.triaged_at DESC
               LIMIT 20"""
        ).fetchall()
        for row in rows:
            details = row["details"]
            try:
                details = json.loads(details) if details else {}
            except (json.JSONDecodeError, TypeError):
                details = {"raw": details}
            recent_blocks.append({
                "signal_id": row["signal_id"],
                "title": row["title"],
                "blocked_at_stage": row["stage_name"],
                "details": details,
                "triaged_at": row["triaged_at"],
            })

        # Total triage log entries
        total_log_entries = conn.execute(
            "SELECT COUNT(*) as cnt FROM innovation_triage_log"
        ).fetchone()["cnt"]

        # Stage pass/block/warn distribution
        stage_stats = {}
        rows = conn.execute(
            """SELECT stage_name, result, COUNT(*) as cnt
               FROM innovation_triage_log
               GROUP BY stage_name, result"""
        ).fetchall()
        for row in rows:
            stage = row["stage_name"]
            if stage not in stage_stats:
                stage_stats[stage] = {}
            stage_stats[stage][row["result"]] = row["cnt"]

        return {
            "by_triage_result": by_result,
            "by_category": by_category,
            "by_gotcha_layer": by_gotcha,
            "by_boundary_tier": by_boundary,
            "pending_triage": pending,
            "total_triage_log_entries": total_log_entries,
            "stage_stats": stage_stats,
            "recent_blocks": recent_blocks,
            "generated_at": _now(),
        }

    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Compliance-First Triage Pipeline — 5-stage safety gate for innovation signals"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--triage",
        action="store_true",
        help="Triage a single signal (requires --signal-id)",
    )
    group.add_argument(
        "--triage-all",
        action="store_true",
        help="Triage all signals with status='scored'",
    )
    group.add_argument(
        "--summary",
        action="store_true",
        help="Show triage outcome summary",
    )

    parser.add_argument(
        "--signal-id", type=str, help="Signal ID to triage (with --triage)"
    )

    args = parser.parse_args()

    try:
        if args.triage:
            if not args.signal_id:
                parser.error("--triage requires --signal-id")
            result = triage_signal(args.signal_id, db_path=args.db_path)
        elif args.triage_all:
            result = triage_all_scored(db_path=args.db_path)
        elif args.summary:
            result = get_triage_summary(db_path=args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(args, result)

    except FileNotFoundError as e:
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
    """Print human-readable output for CLI."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    if args.triage:
        sig = result
        status_icon = {
            "approved": "[APPROVED]",
            "suggested": "[SUGGESTED]",
            "blocked": "[BLOCKED]",
            "logged": "[LOGGED]",
        }.get(sig.get("triage_result", ""), "[?]")

        print(f"Triage Result: {status_icon} {sig.get('triage_result', 'unknown')}")
        print(f"Signal:        {sig.get('signal_id', '')} — {sig.get('title', '')}")
        print(f"Score:         {sig.get('score', 0.0):.4f}")
        print(f"Category:      {sig.get('category', 'N/A')}")
        print(f"GOTCHA Layer:  {sig.get('gotcha_layer', 'N/A')}")
        print(f"Boundary Tier: {sig.get('boundary_tier', 'N/A')}")

        if sig.get("blocked"):
            print(f"Blocked at:    Stage '{sig.get('block_stage', '?')}'")

        if sig.get("warnings"):
            print(f"Warnings:      {', '.join(sig['warnings'])}")

        print("\nStage Details:")
        for stage in sig.get("stages", []):
            icon = {"pass": "OK", "block": "BLOCK", "warn": "WARN"}.get(
                stage["result"], "?"
            )
            print(f"  {stage['stage']}. {stage['name']}: [{icon}]")
            details = stage.get("details", {})
            if isinstance(details, dict):
                reason = details.get("reason", "")
                if reason:
                    print(f"     Reason: {reason}")

    elif args.triage_all:
        counts = result.get("counts", {})
        total = result.get("total_processed", 0)
        print(f"Batch Triage Complete — {total} signals processed")
        print(f"  Approved:  {counts.get('approved', 0)}")
        print(f"  Suggested: {counts.get('suggested', 0)}")
        print(f"  Blocked:   {counts.get('blocked', 0)}")
        print(f"  Logged:    {counts.get('logged', 0)}")
        print(f"  Errors:    {counts.get('error', 0)}")

        # Show blocked signals
        blocked = [r for r in result.get("results", []) if r.get("blocked")]
        if blocked:
            print(f"\nBlocked Signals ({len(blocked)}):")
            for b in blocked[:10]:
                print(f"  {b['signal_id']}: {b.get('title', '')[:60]} — blocked at {b.get('block_stage', '?')}")

    elif args.summary:
        print("Triage Summary")
        print("=" * 50)

        by_result = result.get("by_triage_result", {})
        print("\nBy Outcome:")
        for outcome, count in sorted(by_result.items()):
            print(f"  {outcome:12s}: {count}")

        by_cat = result.get("by_category", {})
        if by_cat:
            print("\nBy Category:")
            for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
                print(f"  {cat:25s}: {count}")

        by_gotcha = result.get("by_gotcha_layer", {})
        if by_gotcha:
            print("\nBy GOTCHA Layer:")
            for layer, count in sorted(by_gotcha.items()):
                print(f"  {layer:12s}: {count}")

        by_boundary = result.get("by_boundary_tier", {})
        if by_boundary:
            print("\nBy Boundary Tier:")
            for tier, count in sorted(by_boundary.items()):
                print(f"  {tier:8s}: {count}")

        pending = result.get("pending_triage", 0)
        print(f"\nPending Triage: {pending}")
        print(f"Total Log Entries: {result.get('total_triage_log_entries', 0)}")

        blocks = result.get("recent_blocks", [])
        if blocks:
            print(f"\nRecent Blocks ({len(blocks)}):")
            for b in blocks[:5]:
                title = (b.get("title") or "untitled")[:50]
                print(f"  {b['signal_id']}: {title} — blocked at {b['blocked_at_stage']}")


if __name__ == "__main__":
    main()
