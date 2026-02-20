#!/usr/bin/env python3
# CUI // SP-CTI
"""Webhook-triggered auto-resolution pipeline (D143-D145).

External alerts (Sentry, monitoring, test failures) trigger:
normalize -> extract features -> match patterns -> decide -> fix -> PR -> notify.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

DEFAULT_CONFIG: Dict[str, Any] = {
    "confidence_threshold": 0.7,
    "escalation_threshold": 0.3,
    "max_auto_fixes_per_hour": 5,
    "cooldown_minutes": 10,
    "auto_create_pr": True,
    "run_tests_before_pr": True,
    "base_branch": "main",
    "branch_prefix": "fix/auto-resolve-",
    "supported_sources": ["sentry", "prometheus", "elk", "generic"],
}


def _load_config() -> Dict[str, Any]:
    """Load auto_resolution config from monitoring_config.yaml with fallback."""
    config = dict(DEFAULT_CONFIG)
    config_path = BASE_DIR / "args" / "monitoring_config.yaml"
    if config_path.exists():
        try:
            import yaml  # type: ignore
            with open(config_path, encoding="utf-8") as fh:
                section = (yaml.safe_load(fh) or {}).get("auto_resolution", {})
            if section:
                config.update(section)
        except (ImportError, Exception):
            pass
    return config


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection to the ICDEV database."""
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix: str = "res") -> str:
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _ensure_table(db_path: Optional[Path] = None) -> None:
    """Create the auto_resolution_log table if it does not exist."""
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auto_resolution_log (
                id TEXT PRIMARY KEY,
                alert_source TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                alert_payload TEXT NOT NULL,
                project_id TEXT,
                confidence REAL DEFAULT 0.0,
                decision TEXT NOT NULL
                    CHECK(decision IN ('auto_fix', 'suggest', 'escalate')),
                resolution_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(resolution_status IN (
                        'pending', 'analyzing', 'fixing', 'testing',
                        'pr_created', 'completed', 'failed',
                        'escalated', 'suggested')),
                branch_name TEXT,
                pr_url TEXT,
                test_passed BOOLEAN,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )""")
        conn.commit()
    finally:
        conn.close()


def _update_status(resolution_id: str, status: str,
                   extra: Optional[Dict[str, Any]] = None,
                   db_path: Optional[Path] = None) -> None:
    """Update resolution_status and optional columns for a log entry."""
    conn = _get_connection(db_path)
    try:
        sets = ["resolution_status = ?", "updated_at = datetime('now')"]
        params: list = [status]
        if extra:
            for col in ("branch_name", "pr_url"):
                if col in extra:
                    sets.append(f"{col} = ?"); params.append(extra[col])
            if "test_passed" in extra:
                sets.append("test_passed = ?"); params.append(extra["test_passed"])
            if "confidence" in extra:
                sets.append("confidence = ?"); params.append(extra["confidence"])
            if "details" in extra:
                sets.append("details = ?")
                val = extra["details"]
                params.append(json.dumps(val) if isinstance(val, dict) else str(val))
        params.append(resolution_id)
        conn.execute(
            f"UPDATE auto_resolution_log SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert normalization
# ---------------------------------------------------------------------------
def normalize_sentry_alert(payload: dict) -> dict:
    """Convert Sentry webhook payload to standard failure_data dict."""
    event = payload.get("event", {})
    exc_values = event.get("exception", {}).get("values", [{}])
    first = exc_values[0] if exc_values else {}
    frames = first.get("stacktrace", {}).get("frames", [])
    stack_lines = [
        f'  File "{f.get("filename", "?")}", line {f.get("lineno", "?")}, in {f.get("function", "?")}'
        for f in frames[-10:]
    ]
    tags = {t[0]: t[1] for t in event.get("tags", []) if len(t) >= 2}
    return {
        "error_type": first.get("type", "UnknownError"),
        "error_message": first.get("value", ""),
        "stack_trace": "\n".join(stack_lines),
        "service_name": tags.get("server_name") or tags.get("service") or payload.get("project_slug", "unknown"),
        "environment": tags.get("environment", "unknown"),
        "source": "sentry", "severity": payload.get("level", "error"),
        "project_id": payload.get("project_id"), "raw_payload": payload,
    }


def normalize_prometheus_alert(payload: dict) -> dict:
    """Convert Prometheus AlertManager webhook to standard failure_data."""
    first = (payload.get("alerts") or [{}])[0] if payload.get("alerts") else {}
    labels = first.get("labels", {})
    ann = first.get("annotations", {})
    return {
        "error_type": labels.get("alertname", "UnknownAlert"),
        "error_message": ann.get("description", ann.get("summary", "")),
        "stack_trace": "",
        "service_name": labels.get("job", labels.get("service", labels.get("instance", "unknown"))),
        "environment": labels.get("environment", labels.get("namespace", "unknown")),
        "source": "prometheus", "severity": labels.get("severity", "warning"),
        "project_id": labels.get("project_id"), "raw_payload": payload,
    }


def normalize_generic_alert(payload: dict) -> dict:
    """Accept flexible alert format and normalize with defaults."""
    return {
        "error_type": payload.get("title", payload.get("error_type", "GenericAlert")),
        "error_message": payload.get("description", payload.get("error_message", "")),
        "stack_trace": payload.get("stack_trace", ""),
        "service_name": payload.get("service", payload.get("service_name", "unknown")),
        "environment": payload.get("environment", "unknown"),
        "source": payload.get("source", "generic"),
        "severity": payload.get("severity", "warning"),
        "project_id": payload.get("project_id"), "raw_payload": payload,
    }


def normalize_alert(payload: dict, source: str = "generic") -> dict:
    """Route to appropriate normalizer based on source string."""
    normalizers = {"sentry": normalize_sentry_alert, "prometheus": normalize_prometheus_alert,
                   "elk": normalize_generic_alert, "generic": normalize_generic_alert}
    result = normalizers.get(source, normalize_generic_alert)(payload)
    result.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    return result


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------
def _extract_features(failure_data: dict) -> dict:
    """Extract features for pattern matching. Delegates to pattern_detector or falls back."""
    try:
        from tools.knowledge.pattern_detector import extract_features
        return extract_features(failure_data)
    except ImportError:
        pass
    msg = failure_data.get("error_message", "").lower()
    sev_map = {"critical": 4, "error": 3, "warning": 2, "info": 1}
    et = failure_data.get("error_type", "unknown")
    sn = failure_data.get("service_name", "unknown")
    return {
        "error_type": et, "service_name": sn,
        "message_length": len(msg), "word_count": len(msg.split()) if msg else 0,
        "has_stack_trace": 1 if failure_data.get("stack_trace") else 0,
        "severity_level": sev_map.get(failure_data.get("severity", "warning"), 2),
        "has_timeout": 1 if "timeout" in msg else 0,
        "has_connection": 1 if "connection" in msg else 0,
        "has_memory": 1 if ("memory" in msg or "oom" in msg) else 0,
        "has_permission": 1 if any(k in msg for k in ("permission", "denied", "403", "401")) else 0,
        "has_database": 1 if any(k in msg for k in ("database", "sql", "deadlock", "pool")) else 0,
        "has_disk": 1 if any(k in msg for k in ("disk", "space", "storage", "quota")) else 0,
        "environment": failure_data.get("environment", "unknown"),
        "signature": f"{et}|{sn}",
    }


def _match_patterns(features: dict, db_path: Optional[Path] = None) -> Tuple[float, Optional[dict]]:
    """Match features against known patterns. Returns (confidence, pattern_or_None)."""
    try:
        from tools.knowledge.pattern_detector import match_known_pattern
        matches = match_known_pattern(features, db_path)
        if matches:
            return matches[0]["combined_score"], matches[0]
        return 0.0, None
    except ImportError:
        pass
    # Fallback: direct DB query
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT id, pattern_type, pattern_signature, description, "
                "root_cause, remediation, confidence, auto_healable "
                "FROM knowledge_patterns ORDER BY confidence DESC LIMIT 100"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            return 0.0, None
        fsig = features.get("signature", "").lower()
        etype = features.get("error_type", "").lower()
        best_score, best_match = 0.0, None
        for r in rows:
            psig = (r["pattern_signature"] or "").lower()
            desc = (r["description"] or "").lower()
            score = 0.0
            if fsig and psig:
                wa, wb = set(fsig.replace("|", " ").split()), set(psig.replace("|", " ").split())
                u = len(wa | wb)
                score = len(wa & wb) / u if u else 0.0
            if etype and etype in desc: score += 0.2
            if etype and etype in psig: score += 0.25
            if features.get("has_timeout") and "timeout" in desc: score += 0.1
            if features.get("has_connection") and "connection" in desc: score += 0.1
            if features.get("has_memory") and "memory" in desc: score += 0.1
            if features.get("has_database") and "database" in desc: score += 0.1
            combined = min(score * r["confidence"], 1.0)
            if combined > best_score:
                best_score = combined
                best_match = {
                    "pattern_id": r["id"], "pattern_type": r["pattern_type"],
                    "description": r["description"], "root_cause": r["root_cause"],
                    "remediation": r["remediation"], "confidence": r["confidence"],
                    "combined_score": round(combined, 3), "auto_healable": bool(r["auto_healable"]),
                }
        return (best_score, best_match) if best_score > 0.1 else (0.0, None)
    except Exception:
        return 0.0, None


def _check_rate_limit(config: dict, db_path: Optional[Path] = None) -> bool:
    """Return True if under the auto-fix rate limit for this hour."""
    _ensure_table(db_path)
    conn = _get_connection(db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM auto_resolution_log "
            "WHERE decision = 'auto_fix' AND created_at > datetime('now', '-1 hour')"
        ).fetchone()[0]
        return count < config.get("max_auto_fixes_per_hour", 5)
    finally:
        conn.close()


def analyze_alert(alert_payload: dict, source: str = "generic",
                  db_path: Optional[Path] = None) -> dict:
    """Analyze alert (phases 1-4) without executing fixes."""
    config = _load_config()
    failure_data = normalize_alert(alert_payload, source)
    features = _extract_features(failure_data)
    confidence, matched = _match_patterns(features, db_path)
    ct, et = config.get("confidence_threshold", 0.7), config.get("escalation_threshold", 0.3)

    if confidence >= ct and matched and matched.get("auto_healable"):
        decision, reason = "auto_fix", f"Confidence {confidence:.2f} >= {ct} and pattern is auto-healable"
    elif confidence < et or matched is None:
        decision = "escalate"
        reason = f"Confidence {confidence:.2f} < {et}" if matched else "No matching pattern found"
    else:
        decision, reason = "suggest", f"Confidence {confidence:.2f} between {et}-{ct}"

    result: Dict[str, Any] = {"status": "ok", "alert_normalized": failure_data,
                               "features": features, "confidence": round(confidence, 3),
                               "decision": decision, "reason": reason}
    if decision == "suggest" and matched:
        result["suggestion"] = {"pattern": matched.get("description"),
                                "root_cause": matched.get("root_cause"),
                                "remediation": matched.get("remediation"),
                                "confidence": round(confidence, 3)}
    if matched:
        result["matched_pattern"] = matched
    return result


def resolve_alert(alert_payload: dict, source: str = "generic",
                  dry_run: bool = False, db_path: Optional[Path] = None) -> dict:
    """Full auto-resolution pipeline: analyze -> record -> fix -> test -> PR -> notify."""
    config = _load_config()
    _ensure_table(db_path)
    analysis = analyze_alert(alert_payload, source, db_path)
    decision = analysis["decision"]
    fd = analysis["alert_normalized"]
    conf = analysis["confidence"]
    rid = _generate_id("res")

    # Record initial entry
    conn = _get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO auto_resolution_log (id, alert_source, alert_type, alert_payload, "
            "project_id, confidence, decision, resolution_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'analyzing')",
            (rid, source, fd.get("error_type", "unknown"), json.dumps(alert_payload),
             fd.get("project_id"), conf, decision))
        conn.commit()
    finally:
        conn.close()

    result: Dict[str, Any] = {"resolution_id": rid, "analysis": analysis, "dry_run": dry_run}

    if decision == "escalate":
        _update_status(rid, "escalated", {"details": analysis}, db_path)
        _notify_resolution(rid, decision, analysis)
        return {**result, "resolution_status": "escalated", "message": analysis["reason"]}

    if decision == "suggest":
        _update_status(rid, "suggested", {"details": analysis}, db_path)
        _notify_resolution(rid, decision, analysis)
        return {**result, "resolution_status": "suggested",
                "suggestion": analysis.get("suggestion", {}), "message": analysis["reason"]}

    # auto_fix path
    if not _check_rate_limit(config, db_path):
        _update_status(rid, "suggested", {"details": {"downgraded": True}}, db_path)
        _notify_resolution(rid, "suggest", {"reason": "Rate limit exceeded"})
        return {**result, "resolution_status": "suggested", "message": "Rate limit exceeded"}

    if dry_run:
        _update_status(rid, "pending", {"details": analysis}, db_path)
        return {**result, "resolution_status": "dry_run_preview",
                "message": "Dry run -- would attempt auto-fix",
                "would_execute": {"pattern": analysis.get("matched_pattern", {}),
                                  "fix_branch": f"{config.get('branch_prefix', 'fix/auto-resolve-')}{rid}"}}

    _update_status(rid, "fixing", db_path=db_path)
    branch = _create_fix_branch(rid, config)
    if branch:
        _update_status(rid, "fixing", {"branch_name": branch}, db_path)

    fix = _attempt_fix(analysis, fd, db_path)
    result["fix_result"] = fix

    if not fix.get("success"):
        _update_status(rid, "failed", {"details": fix, "branch_name": branch}, db_path)
        _notify_resolution(rid, "auto_fix", {"status": "failed", "fix_result": fix})
        return {**result, "resolution_status": "failed", "message": "Auto-fix attempted but failed"}

    test_res: Optional[dict] = None
    if config.get("run_tests_before_pr", True):
        _update_status(rid, "testing", db_path=db_path)
        test_res = _run_tests()
        result["test_result"] = test_res
        _update_status(rid, "testing", {"test_passed": test_res.get("passed", False)}, db_path)

    passed = test_res.get("passed", True) if test_res else True
    pr_url: Optional[str] = None

    if passed and config.get("auto_create_pr", True) and branch:
        title = f"[auto-resolve] Fix {fd.get('error_type', 'unknown')} in {fd.get('service_name', 'unknown')}"
        body = (f"## Auto-Resolution {rid}\n\n**Source:** {source}\n**Confidence:** {conf}\n\n"
                f"### Fix\n```json\n{json.dumps(fix, indent=2, default=str)}\n```\n")
        pr_url = _create_pull_request(branch, title, body, config.get("base_branch", "main"))
        if pr_url:
            _update_status(rid, "pr_created", {"pr_url": pr_url, "details": fix}, db_path)
            result.update(pr_url=pr_url, resolution_status="pr_created")
        else:
            _update_status(rid, "completed", {"details": fix}, db_path)
            result["resolution_status"] = "completed"
    elif passed:
        _update_status(rid, "completed", {"details": fix}, db_path)
        result["resolution_status"] = "completed"
    else:
        _update_status(rid, "failed", {"details": {"fix": fix, "tests": test_res}}, db_path)
        result.update(resolution_status="failed", message="Fix applied but tests failed")

    _notify_resolution(rid, decision, {"status": result.get("resolution_status"),
                                        "pr_url": pr_url, "tests_passed": passed})
    result.setdefault("message", f"Auto-resolution {result.get('resolution_status', 'completed')}")
    return result


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------
def _attempt_fix(analysis: dict, failure_data: dict,
                 db_path: Optional[Path] = None) -> dict:
    """Attempt automated fix via self_heal_analyzer or fallback."""
    try:
        from tools.knowledge.self_heal_analyzer import analyze_and_heal
        hr = analyze_and_heal(failure_data, dry_run=False, db_path=db_path)
        return {"success": hr.get("decision") == "auto_heal"
                and hr.get("remediation_result", {}).get("success", False),
                "method": "self_heal_analyzer", "details": hr}
    except ImportError:
        pass
    except Exception as exc:
        return {"success": False, "method": "self_heal_analyzer", "error": str(exc)}
    m = analysis.get("matched_pattern", {})
    return {"success": False, "method": "fallback",
            "reason": "self_heal_analyzer not available",
            "pattern": m.get("description"), "remediation": m.get("remediation")}


def _create_fix_branch(resolution_id: str, config: Optional[dict] = None) -> Optional[str]:
    """Create a git branch for the fix. Returns branch name or None."""
    cfg = config or _load_config()
    name = f"{cfg.get('branch_prefix', 'fix/auto-resolve-')}{resolution_id}"
    try:
        p = subprocess.run(["git", "checkout", "-b", name], capture_output=True,
                           text=True, timeout=30, cwd=str(BASE_DIR), stdin=subprocess.DEVNULL)
        return name if p.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _run_tests(project_dir: Optional[str] = None) -> dict:
    """Run pytest fail-fast. Returns {passed, output, returncode}."""
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-x", "-q"],
                           capture_output=True, text=True, timeout=300,
                           cwd=project_dir or str(BASE_DIR), stdin=subprocess.DEVNULL)
        return {"passed": p.returncode == 0,
                "output": (p.stdout + p.stderr)[-2000:], "returncode": p.returncode}
    except FileNotFoundError:
        return {"passed": False, "output": "pytest not found", "returncode": -1}
    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "Timed out after 300s", "returncode": -1}


def _create_pull_request(branch: str, title: str, body: str,
                         base: str = "main") -> Optional[str]:
    """Create PR via gh or glab CLI. Returns URL or None."""
    for cmd in (["gh", "pr", "create", "--title", title, "--body", body,
                 "--base", base, "--head", branch],
                ["glab", "mr", "create", "--title", title, "--description", body,
                 "--target-branch", base, "--source-branch", branch, "--yes"]):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                               cwd=str(BASE_DIR), stdin=subprocess.DEVNULL)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def _notify_resolution(resolution_id: str, decision: str, details: dict) -> None:
    """Best-effort notification via audit trail, SSE, and gateway mailbox."""
    try:
        from tools.audit.audit_logger import log_event
        evt = "auto_resolution_failed" if details.get("status") == "failed" else (
            "auto_resolution_escalated" if decision == "escalate" else "auto_resolution_completed")
        log_event(event_type=evt, actor="auto-resolver",
                  action=f"Resolution {resolution_id}: {decision}",
                  details={"resolution_id": resolution_id, "decision": decision, **details})
    except (ImportError, Exception):
        pass
    try:
        import urllib.request
        data = json.dumps({"event_type": "auto_resolution",
                           "data": {"resolution_id": resolution_id, "decision": decision,
                                    "details": details}}).encode("utf-8")
        req = urllib.request.Request("http://localhost:5000/api/events/ingest",
                                    data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
    try:
        from tools.agent.mailbox import send_message
        send_message(sender_id="auto-resolver", recipient_id="monitor-agent",
                     message_type="notification",
                     payload={"resolution_id": resolution_id, "decision": decision,
                              "details": details})
    except (ImportError, Exception):
        pass


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def get_resolution_history(project_id: Optional[str] = None, limit: int = 50,
                           db_path: Optional[Path] = None) -> List[dict]:
    """Query auto_resolution_log ordered by created_at DESC."""
    _ensure_table(db_path)
    conn = _get_connection(db_path)
    try:
        q = "SELECT * FROM auto_resolution_log "
        params: list = []
        if project_id:
            q += "WHERE project_id = ? "; params.append(project_id)
        q += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        results = []
        for row in rows:
            entry = dict(row)
            for f in ("alert_payload", "details"):
                if entry.get(f):
                    try:
                        entry[f] = json.loads(entry[f])
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(entry)
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _format_table(history: List[dict]) -> str:
    """Format history as a simple text table."""
    if not history:
        return "No resolution history found."
    hdr = f"{'ID':<18} {'Source':<12} {'Type':<20} {'Decision':<12} {'Status':<14} {'Conf':>6}  {'Created'}"
    lines = [hdr, "-" * len(hdr)]
    for e in history:
        lines.append(f"{str(e.get('id','')):<18} {str(e.get('alert_source','')):<12} "
                     f"{str(e.get('alert_type',''))[:20]:<20} {str(e.get('decision','')):<12} "
                     f"{str(e.get('resolution_status','')):<14} {e.get('confidence',0.0):>6.3f}  "
                     f"{str(e.get('created_at',''))}")
    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="ICDEV Auto-Resolution Pipeline (D143-D145)")
    parser.add_argument("--analyze", action="store_true", help="Analyze alert without acting")
    parser.add_argument("--resolve", action="store_true", help="Full resolution pipeline")
    parser.add_argument("--history", action="store_true", help="Show resolution history")
    parser.add_argument("--alert-file", type=Path, help="Path to alert JSON file")
    parser.add_argument("--source", default="generic",
                        choices=["sentry", "prometheus", "elk", "generic"])
    parser.add_argument("--dry-run", action="store_true", help="Preview without executing fixes")
    parser.add_argument("--project-id", type=str, help="Filter by project ID")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Override DB path")
    args = parser.parse_args()

    if args.history:
        hist = get_resolution_history(args.project_id, args.limit, args.db_path)
        print(json.dumps(hist, indent=2, default=str) if args.json_output else _format_table(hist))
        return

    if args.analyze or args.resolve:
        if not args.alert_file:
            parser.error("--alert-file is required for --analyze and --resolve")
        ap = Path(args.alert_file)
        if not ap.exists():
            print(json.dumps({"error": f"Alert file not found: {ap}"})); sys.exit(1)
        with open(ap, encoding="utf-8") as fh:
            payload = json.load(fh)
        if args.analyze:
            res = analyze_alert(payload, args.source, args.db_path)
        else:
            res = resolve_alert(payload, args.source, args.dry_run, args.db_path)
        if args.json_output:
            norm = res.get("alert_normalized") or (res.get("analysis") or {}).get("alert_normalized")
            if isinstance(norm, dict):
                norm.pop("raw_payload", None)
            print(json.dumps(res, indent=2, default=str))
        else:
            d = res.get("decision", (res.get("analysis") or {}).get("decision", "?"))
            c = res.get("confidence", (res.get("analysis") or {}).get("confidence", 0.0))
            print(f"Decision:   {d}\nConfidence: {c:.3f}")
            print(f"Reason:     {res.get('reason', res.get('message', ''))}")
            if res.get("resolution_id"): print(f"Resolution: {res['resolution_id']}")
            if res.get("pr_url"): print(f"PR URL:     {res['pr_url']}")
            if res.get("resolution_status"): print(f"Status:     {res['resolution_status']}")
            if res.get("suggestion"):
                print(f"Suggestion: {json.dumps(res['suggestion'], indent=2, default=str)}")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
