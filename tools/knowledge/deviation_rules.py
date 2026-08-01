#!/usr/bin/env python3
# CUI // SP-CTI
"""Category-Based Deviation Rules Engine (GSD-adapted).

Layers category-based deviation rules ON TOP of ICDEV™'s existing
confidence-based self-healing system. When a failure matches a
known category, the category rule overrides the confidence threshold.

GSD's 4 rules + ICDEV™'s compliance addition:
  Rule 1: Security vulns    → auto-fix (lower threshold)
  Rule 2: Critical function  → auto-fix (moderate threshold)
  Rule 3: Blocking issues    → auto-fix (lower threshold)
  Rule 4: Architectural      → ALWAYS human gate
  Rule 5: Compliance         → ALWAYS human gate

100% deterministic, air-gap safe (stdlib + optional PyYAML).

Architecture Decisions:
  D-GSD-7: Category rules override confidence when matched
  D-GSD-8: Architectural + compliance changes always require human approval
  D-GSD-9: Category match logged to append-only deviation_rule_events (D6)
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.knowledge.deviation_rules")

DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "deviation_rules_config.yaml"

# Fallback config if YAML not available
FALLBACK_CATEGORIES = {
    "security_vulnerability": {
        "keywords": [
            "CVE-",
            "sql injection",
            "xss",
            "command injection",
            "authentication bypass",
            "hardcoded secret",
            "bandit",
            "sast finding",
            "privilege escalation",
        ],
        "min_confidence_override": 0.5,
        "max_auto_heals_per_hour": 10,
        "decision": "auto_heal",
        "priority": 1,
    },
    "critical_functionality": {
        "keywords": [
            "missing error handling",
            "unhandled exception",
            "input validation",
            "missing auth check",
            "rate limiting missing",
            "resource leak",
        ],
        "min_confidence_override": 0.6,
        "max_auto_heals_per_hour": 5,
        "decision": "auto_heal",
        "priority": 2,
    },
    "blocking_issue": {
        "keywords": [
            "import error",
            "module not found",
            "missing dependency",
            "syntax error",
            "indentation error",
            "name error",
            "connection refused",
            "file not found",
            "compile error",
            "type error",
        ],
        "min_confidence_override": 0.5,
        "max_auto_heals_per_hour": 8,
        "decision": "auto_heal",
        "priority": 3,
    },
    "architectural_change": {
        "keywords": [
            "schema change",
            "migration",
            "api contract",
            "breaking change",
            "new dependency",
            "architecture change",
            "framework upgrade",
            "data model change",
        ],
        "min_confidence_override": 1.0,
        "max_auto_heals_per_hour": 0,
        "decision": "escalate",
        "priority": 4,
    },
    "compliance_violation": {
        "keywords": [
            "cui marking",
            "classification",
            "nist control",
            "ato boundary",
            "fedramp",
            "cmmc",
            "stig finding",
            "compliance gap",
            "audit trail",
            "append-only",
        ],
        "min_confidence_override": 1.0,
        "max_auto_heals_per_hour": 0,
        "decision": "escalate",
        "priority": 5,
    },
}


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = get_connection(db_path=str(path))
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config() -> dict:
    """Load deviation rules config from YAML or use fallback."""
    if CONFIG_PATH.exists():
        try:
            import yaml

            with open(CONFIG_PATH, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            return config.get("categories", FALLBACK_CATEGORIES)
        except (ImportError, Exception):
            pass
    return FALLBACK_CATEGORIES


def classify_failure(failure_data: dict) -> dict:
    """Classify a failure into a deviation category.

    Matches failure data against category keywords using case-insensitive
    substring matching. Returns the highest-priority matching category.

    Args:
        failure_data: Dict with error_type, error_message, service, etc.

    Returns:
        Dict with category, decision override, and match details.
    """
    categories = load_config()

    # Build searchable text from failure data
    search_text = " ".join(
        [
            str(failure_data.get("error_type", "")),
            str(failure_data.get("error_message", "")),
            str(failure_data.get("description", "")),
            str(failure_data.get("source", "")),
            str(failure_data.get("context", "")),
        ]
    ).lower()

    matches = []

    for cat_name, cat_config in categories.items():
        keywords = cat_config.get("keywords", [])
        matched_keywords = []

        for keyword in keywords:
            if keyword.lower() in search_text:
                matched_keywords.append(keyword)

        if matched_keywords:
            matches.append(
                {
                    "category": cat_name,
                    "priority": cat_config.get("priority", 99),
                    "decision_override": cat_config.get("decision", "suggest"),
                    "min_confidence": cat_config.get("min_confidence_override", 0.7),
                    "max_auto_heals_per_hour": cat_config.get("max_auto_heals_per_hour", 5),
                    "matched_keywords": matched_keywords,
                    "match_count": len(matched_keywords),
                }
            )

    if not matches:
        return {
            "category": None,
            "has_override": False,
            "decision_override": None,
            "details": "No category matched — use confidence-based decision",
        }

    # Sort by priority (lower = higher priority), then by match count
    matches.sort(key=lambda m: (m["priority"], -m["match_count"]))
    best = matches[0]

    return {
        "category": best["category"],
        "has_override": True,
        "decision_override": best["decision_override"],
        "min_confidence": best["min_confidence"],
        "max_auto_heals_per_hour": best["max_auto_heals_per_hour"],
        "matched_keywords": best["matched_keywords"],
        "all_matches": matches,
        "details": (
            f"Matched category '{best['category']}' "
            f"(priority {best['priority']}) with {best['match_count']} keyword(s): "
            f"{', '.join(best['matched_keywords'][:5])}"
        ),
    }


def apply_deviation_rules(
    failure_data: dict,
    confidence: float,
    auto_healable: bool = True,
    db_path: Path = None,
) -> dict:
    """Apply category-based deviation rules to a healing decision.

    This function is designed to be called FROM the existing
    self_heal_analyzer.py's analyze_and_heal() function to layer
    category rules on top of confidence scoring.

    Args:
        failure_data: The failure data dict
        confidence: The confidence score from pattern matching
        auto_healable: Whether the pattern is flagged as auto-healable
        db_path: Override database path

    Returns:
        Dict with final decision, whether category overrode confidence,
        and full details.
    """
    classification = classify_failure(failure_data)

    result = {
        "timestamp": _now(),
        "confidence": confidence,
        "auto_healable": auto_healable,
        "classification": classification,
        "original_decision": _confidence_decision(confidence, auto_healable),
        "final_decision": None,
        "category_overrode": False,
        "reason": "",
    }

    if not classification["has_override"]:
        # No category match — use original confidence-based decision
        result["final_decision"] = result["original_decision"]
        result["reason"] = "No category match; using confidence-based decision"
        return result

    category = classification["category"]
    decision_override = classification["decision_override"]
    min_confidence = classification.get("min_confidence", 0.7)

    # Category always escalate (rules 4 & 5)
    if decision_override == "escalate":
        result["final_decision"] = "escalate"
        result["category_overrode"] = True
        result["reason"] = f"Category '{category}' requires human approval regardless of confidence ({confidence:.2f})"
        _log_deviation_event(result, db_path)
        return result

    # Category allows auto-heal with lowered threshold
    if decision_override == "auto_heal" and confidence >= min_confidence:
        # Check category-specific rate limit
        max_per_hour = classification.get("max_auto_heals_per_hour", 5)
        if _check_category_rate_limit(category, max_per_hour, db_path):
            result["final_decision"] = "auto_heal"
            result["category_overrode"] = result["original_decision"] != "auto_heal"
            result["reason"] = (
                f"Category '{category}' allows auto-heal at confidence {confidence:.2f} >= {min_confidence}"
            )
        else:
            result["final_decision"] = "suggest"
            result["reason"] = f"Category '{category}' rate limit exceeded ({max_per_hour}/hr)"
    elif decision_override == "auto_heal" and confidence < min_confidence:
        result["final_decision"] = "suggest"
        result["reason"] = f"Category '{category}' matched but confidence {confidence:.2f} < {min_confidence}"
    else:
        result["final_decision"] = result["original_decision"]
        result["reason"] = f"Category '{category}' matched, no override needed"

    _log_deviation_event(result, db_path)
    return result


def _confidence_decision(confidence: float, auto_healable: bool) -> str:
    """Replicate the existing self-healing confidence decision."""
    if confidence >= 0.7 and auto_healable:
        return "auto_heal"
    elif confidence < 0.3:
        return "escalate"
    else:
        return "suggest"


def _check_category_rate_limit(category: str, max_per_hour: int, db_path: Path = None) -> bool:
    """Check if we've exceeded the category-specific rate limit."""
    if max_per_hour <= 0:
        return False

    conn = _get_db(db_path)
    try:
        count = conn.execute(
            """SELECT COUNT(*) FROM deviation_rule_events
               WHERE category = %s
                 AND final_decision = 'auto_heal'
                 AND created_at > datetime('now', '-1 hour')""",
            (category,),
        ).fetchone()[0]
        return count < max_per_hour
    except Exception:
        return True  # If DB unavailable, allow (fail-open for healing)
    finally:
        conn.close()


def _log_deviation_event(result: dict, db_path: Path = None):
    """Log a deviation rule event (append-only, NIST AU)."""
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO deviation_rule_events
               (category, confidence, original_decision, final_decision,
                category_overrode, matched_keywords, reason, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                result["classification"].get("category", "none"),
                result["confidence"],
                result["original_decision"],
                result["final_decision"],
                1 if result["category_overrode"] else 0,
                json.dumps(result["classification"].get("matched_keywords", [])),
                result["reason"],
                _now(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Best effort — never block healing on DB issues
        logger.warning(
            "_log_deviation_event: best-effort INSERT into deviation_rule_events failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ── Statistics ───────────────────────────────────────────────────────────────
def get_stats(db_path: Path = None) -> dict:
    """Get deviation rule statistics."""
    conn = _get_db(db_path)
    try:
        stats = {
            "timestamp": _now(),
            "total_events": 0,
            "by_category": {},
            "override_rate": 0.0,
            "decisions": {"auto_heal": 0, "suggest": 0, "escalate": 0},
        }

        # Total events
        row = conn.execute("SELECT COUNT(*) FROM deviation_rule_events").fetchone()
        stats["total_events"] = row[0] if row else 0

        if stats["total_events"] == 0:
            return stats

        # By category
        rows = conn.execute(
            """SELECT category, COUNT(*) as cnt,
                      SUM(CASE WHEN category_overrode = 1 THEN 1 ELSE 0 END) as overrides
               FROM deviation_rule_events
               GROUP BY category"""
        ).fetchall()
        for row in rows:
            stats["by_category"][row["category"]] = {
                "count": row["cnt"],
                "overrides": row["overrides"],
            }

        # Override rate
        override_count = conn.execute(
            "SELECT COUNT(*) FROM deviation_rule_events WHERE category_overrode = 1"
        ).fetchone()[0]
        stats["override_rate"] = round(override_count / stats["total_events"], 2) if stats["total_events"] > 0 else 0.0

        # Decision distribution
        for decision in ["auto_heal", "suggest", "escalate"]:
            row = conn.execute(
                "SELECT COUNT(*) FROM deviation_rule_events WHERE final_decision = %s",
                (decision,),
            ).fetchone()
            stats["decisions"][decision] = row[0] if row else 0

        return stats
    except Exception as e:
        return {"error": str(e), "timestamp": _now()}
    finally:
        conn.close()


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Category-Based Deviation Rules Engine (GSD-adapted)")
    parser.add_argument("--classify", help="Classify failure data (JSON string)")
    parser.add_argument("--apply", help="Apply deviation rules to failure (JSON string)")
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.5,
        help="Confidence score (for --apply)",
    )
    parser.add_argument("--stats", action="store_true", help="Show deviation rule statistics")
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="List all deviation categories",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.classify:
        try:
            failure_data = json.loads(args.classify)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON: {e}"}))
            raise SystemExit(1)
        result = classify_failure(failure_data)
        print(json.dumps(result, indent=2, default=str))

    elif args.apply:
        try:
            failure_data = json.loads(args.apply)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"Invalid JSON: {e}"}))
            raise SystemExit(1)
        result = apply_deviation_rules(failure_data, args.confidence)
        print(json.dumps(result, indent=2, default=str))

    elif args.stats:
        result = get_stats()
        print(json.dumps(result, indent=2, default=str))

    elif args.list_categories:
        categories = load_config()
        if args.json_output:
            print(json.dumps(categories, indent=2))
        else:
            for name, config in sorted(
                categories.items(),
                key=lambda x: x[1].get("priority", 99),
            ):
                decision = config.get("decision", "suggest")
                priority = config.get("priority", 99)
                print(f"  [{priority}] {name}: {decision}")
                kw = config.get("keywords", [])
                print(f"      Keywords: {', '.join(kw[:5])}")
                if len(kw) > 5:
                    print(f"      ... and {len(kw) - 5} more")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
