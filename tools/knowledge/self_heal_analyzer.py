#!/usr/bin/env python3
"""Self-healing decision engine. Analyzes failures, matches patterns,
decides whether to auto-heal or escalate, executes remediation actions,
and records outcomes."""

import argparse
import json
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Thresholds
CONFIDENCE_THRESHOLD = 0.7   # Auto-heal above this
ESCALATION_THRESHOLD = 0.3   # Always escalate below this
MAX_HEAL_ATTEMPTS = 3        # Max auto-heal attempts per hour per pattern


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Remediation actions
# ---------------------------------------------------------------------------
def _restart_service(context: dict) -> dict:
    """Restart a service via kubectl or systemctl."""
    service = context.get("service_name", context.get("service", "unknown"))
    namespace = context.get("namespace", "default")
    method = context.get("restart_method", "kubectl")

    if method == "kubectl":
        cmd = [
            "kubectl", "rollout", "restart",
            f"deployment/{service}",
            "-n", namespace,
        ]
    else:
        cmd = ["systemctl", "restart", service]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {
            "action": "restart_service",
            "service": service,
            "success": proc.returncode == 0,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "command": " ".join(cmd),
        }
    except FileNotFoundError:
        return {
            "action": "restart_service",
            "service": service,
            "success": False,
            "error": f"Command not found: {cmd[0]}",
            "simulated": True,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "action": "restart_service",
            "service": service,
            "success": False,
            "error": "Command timed out after 60s",
        }


def _rollback(context: dict) -> dict:
    """Roll back a deployment."""
    project_id = context.get("project_id", "unknown")
    environment = context.get("environment", "staging")
    namespace = context.get("namespace", f"{project_id}-{environment}")
    deployment = context.get("deployment_name", project_id)

    cmd = [
        "kubectl", "rollout", "undo",
        f"deployment/{deployment}",
        "-n", namespace,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "action": "rollback",
            "project_id": project_id,
            "environment": environment,
            "success": proc.returncode == 0,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "command": " ".join(cmd),
        }
    except FileNotFoundError:
        return {
            "action": "rollback",
            "success": False,
            "error": f"Command not found: {cmd[0]}",
            "simulated": True,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "action": "rollback",
            "success": False,
            "error": "Rollback timed out after 120s",
        }


def _scale_up(context: dict) -> dict:
    """Scale up a deployment."""
    service = context.get("service_name", context.get("service", "unknown"))
    namespace = context.get("namespace", "default")
    replicas = context.get("target_replicas", 5)

    cmd = [
        "kubectl", "scale",
        f"deployment/{service}",
        f"--replicas={replicas}",
        "-n", namespace,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {
            "action": "scale_up",
            "service": service,
            "target_replicas": replicas,
            "success": proc.returncode == 0,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "command": " ".join(cmd),
        }
    except FileNotFoundError:
        return {
            "action": "scale_up",
            "success": False,
            "error": f"Command not found: {cmd[0]}",
            "simulated": True,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {
            "action": "scale_up",
            "success": False,
            "error": "Scale up timed out",
        }


def _clear_cache(context: dict) -> dict:
    """Clear application cache via API or Redis."""
    service = context.get("service_name", context.get("service", "unknown"))
    cache_endpoint = context.get("cache_endpoint", f"http://{service}:8080/admin/cache/clear")
    method = context.get("cache_method", "http")

    if method == "redis":
        cmd = ["redis-cli", "-h", context.get("redis_host", "localhost"), "FLUSHDB"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return {
                "action": "clear_cache",
                "method": "redis",
                "success": proc.returncode == 0,
                "stdout": proc.stdout.strip(),
            }
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"action": "clear_cache", "method": "redis", "success": False, "error": str(e)}
    else:
        # HTTP cache clear
        try:
            import urllib.request
            req = urllib.request.Request(cache_endpoint, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return {
                    "action": "clear_cache",
                    "method": "http",
                    "endpoint": cache_endpoint,
                    "success": resp.status == 200,
                    "status_code": resp.status,
                }
        except Exception as e:
            return {
                "action": "clear_cache",
                "method": "http",
                "success": False,
                "error": str(e),
                "simulated": True,
            }


# Map of remediation action names to functions
REMEDIATION_ACTIONS = {
    "restart_service": _restart_service,
    "rollback": _rollback,
    "scale_up": _scale_up,
    "clear_cache": _clear_cache,
}


# ---------------------------------------------------------------------------
# Core self-healing logic
# ---------------------------------------------------------------------------
def analyze_and_heal(failure_data: dict, dry_run: bool = False, db_path: Path = None) -> dict:
    """Main entry point: extract features, match pattern, decide action.

    Args:
        failure_data: Dict with error_type, error_message, service, project_id, etc.
        dry_run: If True, show what would happen without executing
        db_path: Override database path

    Returns:
        Dict with decision, action taken, and outcome
    """
    # Import pattern_detector functions
    from tools.knowledge.pattern_detector import extract_features, match_known_pattern

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "failure_data": failure_data,
        "dry_run": dry_run,
    }

    # 1. Extract features
    features = extract_features(failure_data)
    result["features"] = features

    # 2. Match against known patterns
    matches = match_known_pattern(features, db_path)
    result["pattern_matches"] = len(matches)

    if not matches:
        result["decision"] = "escalate"
        result["reason"] = "No known patterns matched"
        result["action"] = "none"
        _record_healing_event(failure_data, None, "escalated", "No pattern match", db_path)
        return result

    top_match = matches[0]
    result["top_pattern"] = top_match

    # 3. Decision logic
    confidence = top_match["combined_score"]
    auto_healable = top_match.get("auto_healable", False)

    if confidence >= CONFIDENCE_THRESHOLD and auto_healable:
        # Check rate limiting (max attempts per hour)
        if not _check_rate_limit(top_match["pattern_id"], db_path):
            result["decision"] = "escalate"
            result["reason"] = f"Rate limit exceeded for pattern #{top_match['pattern_id']} ({MAX_HEAL_ATTEMPTS}/hr)"
            _record_healing_event(failure_data, top_match, "rate_limited", result["reason"], db_path)
            return result

        result["decision"] = "auto_heal"
        result["reason"] = f"Confidence {confidence:.2f} >= {CONFIDENCE_THRESHOLD} and pattern is auto-healable"

        if dry_run:
            result["action"] = f"Would execute: {top_match.get('remediation', 'unknown')}"
            return result

        # 4. Execute remediation
        healing_result = execute_remediation(top_match, failure_data)
        result["remediation_result"] = healing_result

        # 5. Record outcome
        event_id = _record_healing_event(
            failure_data, top_match,
            "succeeded" if healing_result.get("success") else "failed",
            json.dumps(healing_result),
            db_path,
        )
        result["healing_event_id"] = event_id

    elif confidence < ESCALATION_THRESHOLD:
        result["decision"] = "escalate"
        result["reason"] = f"Confidence {confidence:.2f} < {ESCALATION_THRESHOLD} — too uncertain"
        _record_healing_event(failure_data, top_match, "escalated", result["reason"], db_path)

    else:
        result["decision"] = "suggest"
        result["reason"] = (
            f"Confidence {confidence:.2f} between thresholds "
            f"({ESCALATION_THRESHOLD}–{CONFIDENCE_THRESHOLD}) — suggesting but not auto-healing"
        )
        result["suggestion"] = {
            "pattern": top_match["description"],
            "root_cause": top_match["root_cause"],
            "remediation": top_match.get("remediation"),
            "confidence": confidence,
        }
        _record_healing_event(failure_data, top_match, "suggested", result["reason"], db_path)

    return result


def execute_remediation(pattern: dict, context: dict) -> dict:
    """Dispatch to the appropriate remediation action.

    The pattern's remediation field should be a JSON string with at minimum an
    'action' key matching one of: restart_service, rollback, scale_up, clear_cache.
    """
    remediation_str = pattern.get("remediation", "{}")
    if isinstance(remediation_str, str):
        try:
            remediation = json.loads(remediation_str)
        except (json.JSONDecodeError, TypeError):
            remediation = {"action": remediation_str}
    else:
        remediation = remediation_str or {}

    action_name = remediation.get("action", "unknown")

    # Merge pattern remediation config with failure context
    merged_context = {**context, **remediation}

    if action_name in REMEDIATION_ACTIONS:
        start_time = time.time()
        result = REMEDIATION_ACTIONS[action_name](merged_context)
        result["duration_seconds"] = round(time.time() - start_time, 3)
        return result
    else:
        return {
            "action": action_name,
            "success": False,
            "error": f"Unknown remediation action: {action_name}. "
                     f"Available: {list(REMEDIATION_ACTIONS.keys())}",
        }


def _check_rate_limit(pattern_id: int, db_path: Path = None) -> bool:
    """Check if we've exceeded max healing attempts for this pattern in the last hour."""
    conn = _get_db(db_path)
    try:
        count = conn.execute(
            """SELECT COUNT(*) FROM self_healing_events
               WHERE pattern_id = ?
                 AND created_at > datetime('now', '-1 hour')""",
            (pattern_id,),
        ).fetchone()[0]
        return count < MAX_HEAL_ATTEMPTS
    finally:
        conn.close()


def _record_healing_event(
    failure_data: dict,
    pattern: dict,
    outcome: str,
    details: str,
    db_path: Path = None,
) -> int:
    """Record a self-healing event in the database."""
    conn = _get_db(db_path)
    try:
        project_id = failure_data.get("project_id", "unknown")
        pattern_id = pattern["pattern_id"] if pattern else None

        cursor = conn.execute(
            """INSERT INTO self_healing_events
               (project_id, pattern_id, trigger_source, trigger_data,
                action_taken, outcome, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                pattern_id,
                failure_data.get("source", "pattern_detector"),
                json.dumps(failure_data),
                pattern.get("remediation") if pattern else None,
                outcome,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        # Non-critical — don't let DB issues block healing
        print(f"[self-heal] Warning: Could not record healing event: {e}")
        return -1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Record outcome (post-healing callback)
# ---------------------------------------------------------------------------
def record_outcome(
    healing_event_id: int,
    outcome: str,
    details: str = None,
    db_path: Path = None,
) -> dict:
    """Update a self-healing event with the actual outcome and adjust pattern confidence.

    Args:
        healing_event_id: ID from self_healing_events table
        outcome: 'success' or 'failure'
        details: Optional details about the outcome
        db_path: Override database path
    """
    conn = _get_db(db_path)
    try:
        event = conn.execute(
            "SELECT pattern_id, outcome as old_outcome FROM self_healing_events WHERE id = ?",
            (healing_event_id,),
        ).fetchone()

        if not event:
            return {"error": f"Healing event {healing_event_id} not found"}

        # Update the event
        conn.execute(
            """UPDATE self_healing_events
               SET outcome = ?, action_taken = COALESCE(action_taken, '') || ' | outcome: ' || ?
               WHERE id = ?""",
            (outcome, details or outcome, healing_event_id),
        )
        conn.commit()

        # Update pattern confidence
        result = {"healing_event_id": healing_event_id, "outcome": outcome}

        if event["pattern_id"]:
            from tools.knowledge.pattern_detector import update_pattern_confidence
            conf_result = update_pattern_confidence(event["pattern_id"], outcome, db_path)
            result["confidence_update"] = conf_result

        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Self-healing decision engine")
    parser.add_argument("--failure-data", help="Failure data as JSON string")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--record-outcome", type=int, help="Record outcome for healing event ID")
    parser.add_argument("--outcome", choices=["success", "failure"], help="Outcome to record")
    parser.add_argument("--details", help="Outcome details")
    parser.add_argument("--db-path", help="Database path override")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    if args.record_outcome and args.outcome:
        result = record_outcome(args.record_outcome, args.outcome, args.details, db_path)
        print(json.dumps(result, indent=2))
        return

    if not args.failure_data:
        parser.error("--failure-data is required (unless using --record-outcome)")

    try:
        failure_data = json.loads(args.failure_data)
    except json.JSONDecodeError as e:
        parser.error(f"Invalid JSON in --failure-data: {e}")

    result = analyze_and_heal(failure_data, dry_run=args.dry_run, db_path=db_path)

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        decision = result.get("decision", "unknown")
        reason = result.get("reason", "")

        print(f"\n=== Self-Healing Analysis ===")
        print(f"  Decision: {decision.upper()}")
        print(f"  Reason:   {reason}")

        if result.get("top_pattern"):
            p = result["top_pattern"]
            print(f"  Pattern:  {p['description'][:60]}")
            print(f"  Score:    {p['combined_score']}")

        if result.get("remediation_result"):
            r = result["remediation_result"]
            status = "SUCCESS" if r.get("success") else "FAILED"
            print(f"  Action:   {r.get('action', 'unknown')} — {status}")
            if r.get("error"):
                print(f"  Error:    {r['error']}")

        if result.get("suggestion"):
            s = result["suggestion"]
            print(f"\n  SUGGESTION:")
            print(f"    Pattern:     {s['pattern']}")
            print(f"    Root cause:  {s['root_cause']}")
            print(f"    Remediation: {s.get('remediation', 'N/A')}")


if __name__ == "__main__":
    main()
