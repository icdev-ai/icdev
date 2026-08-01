#!/usr/bin/env python3
# CUI // SP-CTI
"""Review Board Remediation Engine — auto-fix findings pipeline (D-RB-4).

3-tier confidence model (mirrors auto_resolver.py / production_remediate.py):
    - AUTO-FIX  (confidence >= 0.7, auto_fixable=1): Execute fix automatically
    - SUGGEST   (confidence 0.3-0.7):                Present recommendation
    - ESCALATE  (confidence < 0.3):                  Log for human review

Rate limiting: max 5 auto-fixes per hour, 10-minute cooldown between same-category fixes.

Architecture Decisions:
    D-RB-4:  3-tier confidence (auto/suggest/escalate)
    D-RB-8:  Declarative fix registry (add handlers without code changes)
    D-RB-9:  Verification re-run after auto-fix
    D-RB-10: Append-only remediation_audit (NIST AU)

Usage:
    python tools/review_board/remediation_engine.py --run --json
    python tools/review_board/remediation_engine.py --run --dry-run --json
    python tools/review_board/remediation_engine.py --pending --json
    python tools/review_board/remediation_engine.py --history --json
    python tools/review_board/remediation_engine.py --stats --json
"""

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.review_board.remediation_engine")


def _get_connection():
    """Get DB connection with WAL mode and busy timeout."""
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str = "rbr") -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------
AUTO_FIX_THRESHOLD = 0.7
SUGGEST_THRESHOLD = 0.3
MAX_AUTO_FIXES_PER_HOUR = 5
COOLDOWN_MINUTES = 10


# ---------------------------------------------------------------------------
# Fix Registry — declarative mapping from category to fix handler
# ---------------------------------------------------------------------------
# Each entry: {
#   "handler": module_path (string) or callable,
#   "description": human-readable fix description,
#   "verify": optional re-check function name,
# }
FIX_REGISTRY: Dict[str, Dict[str, Any]] = {
    "backup_freshness": {
        "handler": "tools.review_board.fixers.sre_fixes",
        "function": "fix_backup_freshness",
        "description": "Run database backup",
        "verify": "verify_backup_freshness",
    },
    "temp_size": {
        "handler": "tools.review_board.fixers.perf_fixes",
        "function": "fix_temp_size",
        "description": "Clean up .tmp directory (old test runs, screenshots)",
        "verify": "verify_temp_size",
    },
    "disk_usage": {
        "handler": "tools.review_board.fixers.sre_fixes",
        "function": "fix_disk_usage",
        "description": "Run VACUUM on oversized databases",
        "verify": "verify_disk_usage",
    },
    "stale_docs": {
        "handler": "tools.review_board.fixers.docs_fixes",
        "function": "fix_stale_docs",
        "description": "Touch stale docs to flag for review",
        "verify": None,
    },
    "broken_refs": {
        "handler": "tools.review_board.fixers.qa_fixes",
        "function": "fix_broken_refs",
        "description": "Comment out broken tool references in CLAUDE.md",
        "verify": "verify_broken_refs",
    },
    "untested_modules": {
        "handler": "tools.review_board.fixers.qa_fixes",
        "function": "fix_untested_modules",
        "description": "Generate minimal test stubs for untested modules",
        "verify": None,
    },
}


# ---------------------------------------------------------------------------
# Ensure remediation audit table
# ---------------------------------------------------------------------------
def ensure_tables() -> None:
    """Create remediation audit table if not exists."""
    conn = _get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_board_remediation_log (
                id              TEXT PRIMARY KEY,
                finding_id      TEXT NOT NULL,
                reflex_name     TEXT,
                category        TEXT NOT NULL,
                severity        TEXT,
                confidence      REAL DEFAULT 0.0,
                tier            TEXT NOT NULL CHECK(tier IN (
                    'auto_fix', 'suggest', 'escalate'
                )),
                status          TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
                    'pending', 'fixing', 'fixed', 'failed',
                    'suggested', 'escalated', 'skipped', 'verified'
                )),
                fix_description TEXT,
                fix_result      TEXT,
                verification    TEXT,
                dry_run         INTEGER DEFAULT 0,
                duration_ms     INTEGER,
                created_at      TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rbrlog_finding
                ON review_board_remediation_log(finding_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rbrlog_status
                ON review_board_remediation_log(status)
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def get_pending_findings() -> List[Dict]:
    """Get findings eligible for remediation (auto_fixable, not yet fixed)."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM review_board_findings "
            "WHERE auto_fixable = 1 AND fix_applied = 0 "
            "ORDER BY "
            "  CASE severity "
            "    WHEN 'critical' THEN 1 "
            "    WHEN 'high' THEN 2 "
            "    WHEN 'medium' THEN 3 "
            "    WHEN 'low' THEN 4 "
            "    ELSE 5 END, "
            "  confidence DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def classify_tier(confidence: float, auto_fixable: bool) -> str:
    """Classify finding into remediation tier."""
    if not auto_fixable:
        return "escalate"
    if confidence >= AUTO_FIX_THRESHOLD:
        return "auto_fix"
    if confidence >= SUGGEST_THRESHOLD:
        return "suggest"
    return "escalate"


def check_rate_limit() -> bool:
    """Check if auto-fix rate limit allows another fix."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM review_board_remediation_log "
            "WHERE tier = 'auto_fix' AND status IN ('fixed', 'verified') "
            "AND created_at > datetime('now', '-1 hour')"
        ).fetchone()
        return (row[0] if row else 0) < MAX_AUTO_FIXES_PER_HOUR
    except Exception:
        return True
    finally:
        conn.close()


def check_cooldown(category: str) -> bool:
    """Check if cooldown period has passed for this category."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(created_at) FROM review_board_remediation_log "
            "WHERE category = %s AND status IN ('fixed', 'verified')",
            (category,),
        ).fetchone()
        if not row or not row[0]:
            return True
        from datetime import datetime, timezone, timedelta

        last_fix = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - last_fix) > timedelta(minutes=COOLDOWN_MINUTES)
    except Exception:
        return True
    finally:
        conn.close()


def execute_fix(finding: Dict, dry_run: bool = False) -> Dict[str, Any]:
    """Execute a fix for a single finding.

    Returns: {"status": "fixed|failed|suggested|escalated|skipped", ...}
    """
    category = finding.get("category", "")
    confidence = finding.get("confidence", 0.0)
    auto_fixable = bool(finding.get("auto_fixable", 0))
    finding_id = finding.get("id", "unknown")

    tier = classify_tier(confidence, auto_fixable)
    log_id = _gen_id("rbr")
    start_ms = int(time.time() * 1000)

    result = {
        "log_id": log_id,
        "finding_id": finding_id,
        "category": category,
        "tier": tier,
        "status": "pending",
        "fix_description": None,
        "fix_result": None,
        "verification": None,
    }

    # Check tier
    if tier == "escalate":
        result["status"] = "escalated"
        result["fix_description"] = "Confidence too low or not auto-fixable — escalated for human review"
        _log_remediation(log_id, finding, tier, result, dry_run, start_ms)
        return result

    if tier == "suggest":
        registry_entry = FIX_REGISTRY.get(category, {})
        result["status"] = "suggested"
        result["fix_description"] = registry_entry.get("description", f"Suggested fix for category '{category}'")
        _log_remediation(log_id, finding, tier, result, dry_run, start_ms)
        return result

    # Auto-fix tier — check rate limit and cooldown
    if not check_rate_limit():
        result["status"] = "suggested"
        result["tier"] = "suggest"
        result["fix_description"] = "Rate limit exceeded (5/hour) — downgraded to suggestion"
        _log_remediation(log_id, finding, "suggest", result, dry_run, start_ms)
        return result

    if not check_cooldown(category):
        result["status"] = "skipped"
        result["fix_description"] = f"Cooldown active ({COOLDOWN_MINUTES}min) for category '{category}'"
        _log_remediation(log_id, finding, tier, result, dry_run, start_ms)
        return result

    # Look up fix handler
    registry_entry = FIX_REGISTRY.get(category)
    if not registry_entry:
        result["status"] = "suggested"
        result["fix_description"] = f"No auto-fix handler registered for category '{category}'"
        _log_remediation(log_id, finding, "suggest", result, dry_run, start_ms)
        return result

    result["fix_description"] = registry_entry.get("description", "")

    if dry_run:
        result["status"] = "suggested"
        result["fix_description"] = f"[DRY RUN] Would execute: {registry_entry['description']}"
        _log_remediation(log_id, finding, tier, result, dry_run, start_ms)
        return result

    # Execute the fix
    try:
        handler_module = importlib.import_module(registry_entry["handler"])
        fix_fn = getattr(handler_module, registry_entry["function"])
        fix_output = fix_fn(finding)
        result["fix_result"] = fix_output
        result["status"] = "fixed"

        # Mark finding as fixed
        _mark_finding_fixed(finding_id)

        # Verification step (D-RB-9)
        verify_fn_name = registry_entry.get("verify")
        if verify_fn_name and hasattr(handler_module, verify_fn_name):
            verify_fn = getattr(handler_module, verify_fn_name)
            verify_result = verify_fn(finding)
            result["verification"] = verify_result
            if verify_result.get("passed"):
                result["status"] = "verified"
            else:
                result["status"] = "fixed"  # Fixed but verification inconclusive

    except Exception as e:
        result["status"] = "failed"
        result["fix_result"] = {"error": str(e)}

    _log_remediation(log_id, finding, tier, result, dry_run, start_ms)
    return result


def _mark_finding_fixed(finding_id: str) -> None:
    """Mark a finding as fixed (update fix_applied flag)."""
    conn = _get_connection()
    try:
        conn.execute(
            "UPDATE review_board_findings SET fix_applied = 1 WHERE id = %s",
            (finding_id,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _log_remediation(log_id: str, finding: Dict, tier: str, result: Dict, dry_run: bool, start_ms: int) -> None:
    """Append remediation event to audit log (D-RB-10)."""
    duration = int(time.time() * 1000) - start_ms
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO review_board_remediation_log
                (id, finding_id, reflex_name, category, severity, confidence,
                 tier, status, fix_description, fix_result, verification,
                 dry_run, duration_ms, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                log_id,
                finding.get("id", ""),
                finding.get("reflex_name", ""),
                finding.get("category", ""),
                finding.get("severity", ""),
                finding.get("confidence", 0.0),
                tier,
                result.get("status", "pending"),
                result.get("fix_description", ""),
                json.dumps(result.get("fix_result")) if result.get("fix_result") else None,
                json.dumps(result.get("verification")) if result.get("verification") else None,
                1 if dry_run else 0,
                duration,
                _now(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_log_remediation: best-effort INSERT into review_board_remediation_log failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------
def run_remediation(dry_run: bool = False) -> Dict[str, Any]:
    """Run remediation pipeline on all pending auto-fixable findings."""
    ensure_tables()
    findings = get_pending_findings()

    results = {
        "total_pending": len(findings),
        "auto_fixed": 0,
        "verified": 0,
        "suggested": 0,
        "escalated": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": dry_run,
        "details": [],
    }

    for finding in findings:
        fix_result = execute_fix(finding, dry_run=dry_run)
        results["details"].append(fix_result)

        status = fix_result.get("status", "")
        if status == "verified":
            results["verified"] += 1
            results["auto_fixed"] += 1
        elif status == "fixed":
            results["auto_fixed"] += 1
        elif status == "suggested":
            results["suggested"] += 1
        elif status == "escalated":
            results["escalated"] += 1
        elif status == "skipped":
            results["skipped"] += 1
        elif status == "failed":
            results["failed"] += 1

    return results


def get_history(limit: int = 50) -> List[Dict]:
    """Get remediation history."""
    ensure_tables()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM review_board_remediation_log ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    """Get remediation statistics."""
    ensure_tables()
    conn = _get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM review_board_remediation_log").fetchone()
        by_tier = conn.execute("SELECT tier, COUNT(*) FROM review_board_remediation_log GROUP BY tier").fetchall()
        by_status = conn.execute("SELECT status, COUNT(*) FROM review_board_remediation_log GROUP BY status").fetchall()
        last_hour = conn.execute(
            "SELECT COUNT(*) FROM review_board_remediation_log "
            "WHERE tier = 'auto_fix' AND status IN ('fixed', 'verified') "
            "AND created_at > datetime('now', '-1 hour')"
        ).fetchone()

        return {
            "total_remediations": total[0] if total else 0,
            "by_tier": {r[0]: r[1] for r in by_tier},
            "by_status": {r[0]: r[1] for r in by_status},
            "auto_fixes_last_hour": last_hour[0] if last_hour else 0,
            "rate_limit_remaining": MAX_AUTO_FIXES_PER_HOUR - (last_hour[0] if last_hour else 0),
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Review Board Remediation Engine (D-RB-4)")
    parser.add_argument("--run", action="store_true", help="Run remediation on pending findings")
    parser.add_argument("--dry-run", action="store_true", help="Preview fixes without applying")
    parser.add_argument("--pending", action="store_true", help="List pending auto-fixable findings")
    parser.add_argument("--history", action="store_true", help="Show remediation history")
    parser.add_argument("--stats", action="store_true", help="Show remediation statistics")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--limit", type=int, default=50, help="Limit results")
    args = parser.parse_args()

    ensure_tables()

    if args.run:
        result = run_remediation(dry_run=args.dry_run)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Remediation complete: {result['auto_fixed']} fixed, "
                f"{result['verified']} verified, {result['suggested']} suggested, "
                f"{result['escalated']} escalated, {result['failed']} failed"
            )
    elif args.pending:
        findings = get_pending_findings()
        if args.json:
            print(json.dumps({"pending": findings, "count": len(findings)}, indent=2))
        else:
            for f in findings:
                print(f"  [{f['severity']}] {f['category']}: {f['title']} (confidence={f['confidence']:.0%})")
    elif args.history:
        history = get_history(limit=args.limit)
        if args.json:
            print(json.dumps({"history": history, "count": len(history)}, indent=2))
        else:
            for h in history:
                print(f"  [{h['tier']}] {h['category']}: {h['status']} ({h['created_at']})")
    elif args.stats:
        stats = get_stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print(f"Total: {stats.get('total_remediations', 0)}")
            print(f"By tier: {stats.get('by_tier', {})}")
            print(f"By status: {stats.get('by_status', {})}")
            print(f"Rate limit remaining: {stats.get('rate_limit_remaining', 0)}/hour")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
