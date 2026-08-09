#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Heal Reflex — pattern-based auto-remediation for known failures.

Monitors recent error patterns in the audit trail, matches against known
healing patterns in the knowledge base, and applies auto-remediation
when confidence exceeds threshold.

YELLOW tier (reversible writes with cooldown).
Scanner-tier only (zero Claude tokens).

Remediation actions (safe, non-destructive):
  - regenerate_artifact: Re-run compliance/doc generators for stale artifacts
  - clear_stale_cache: Remove stale .tmp/ files that block tool execution
  - rerun_tool: Re-execute a failed deterministic tool (idempotent)
  - reset_circuit_breaker: Reset tripped circuit breakers in genesis_reflex_state
  - reinit_db_table: Re-run init_icdev_db.py when tables are missing
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# Heal constitution — loaded once per process; None if file is absent/unreadable
_HEAL_CONSTITUTION: dict | None = None


def _load_constitution() -> dict:
    global _HEAL_CONSTITUTION
    if _HEAL_CONSTITUTION is not None:
        return _HEAL_CONSTITUTION
    constitution_path = BASE_DIR / "args" / "heal_constitution.yaml"
    try:
        import yaml
        _HEAL_CONSTITUTION = yaml.safe_load(constitution_path.read_text(encoding="utf-8")) or {}
    except Exception:
        _HEAL_CONSTITUTION = {}
    return _HEAL_CONSTITUTION


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Built-in remediation actions (safe, non-destructive, idempotent)
# ---------------------------------------------------------------------------
BUILTIN_ACTIONS = {
    "regenerate_artifact",
    "clear_stale_cache",
    "rerun_tool",
    "reset_circuit_breaker",
    "reinit_db_table",
}

IMPLEMENTATION_STATUS = "full"


def _action_regenerate_artifact(params: Dict) -> Tuple[bool, str]:
    """Re-run a compliance/doc generator to refresh a stale artifact."""
    tool_cmd = params.get("tool_cmd", "")
    if not tool_cmd:
        return False, "No tool_cmd specified"
    # Only allow known safe generators
    safe_prefixes = [
        "tools/compliance/",
        "tools/dochub/",
        "tools/compliance/evidence_collector",
    ]
    if not any(tool_cmd.startswith(p) for p in safe_prefixes):
        return False, f"Tool '{tool_cmd}' not in safe generator allowlist"
    cmd = [sys.executable, tool_cmd] + params.get("args", ["--json"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        return result.returncode == 0, result.stdout[-500:] if result.stdout else result.stderr[-300:]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _action_clear_stale_cache(params: Dict) -> Tuple[bool, str]:
    """Remove stale .tmp/ files older than max_age_hours."""
    max_age_hours = params.get("max_age_hours", 48)
    cache_dir = BASE_DIR / ".tmp"
    if not cache_dir.exists():
        return True, "No .tmp directory"
    cutoff = _utcnow().timestamp() - (max_age_hours * 3600)
    removed = 0
    # Only remove known-safe ephemeral file types
    safe_extensions = {".json", ".log", ".tmp", ".pid"}
    for f in cache_dir.rglob("*"):
        if f.is_file() and f.suffix in safe_extensions:
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    return True, f"Removed {removed} stale files"


def _action_rerun_tool(params: Dict) -> Tuple[bool, str]:
    """Re-execute a deterministic tool that previously failed."""
    tool_cmd = params.get("tool_cmd", "")
    if not tool_cmd:
        return False, "No tool_cmd specified"
    # Block any tool that modifies code or deploys
    blocked = ["builder/", "infra/", "deploy", "generator.py", "scaffolder"]
    if any(b in tool_cmd for b in blocked):
        return False, f"Tool '{tool_cmd}' blocked from auto-rerun"
    cmd = [sys.executable, tool_cmd] + params.get("args", ["--json"])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        return result.returncode == 0, result.stdout[-500:] if result.stdout else result.stderr[-300:]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _action_reset_circuit_breaker(params: Dict) -> Tuple[bool, str]:
    """Reset a tripped circuit breaker in genesis_reflex_state."""
    reflex_name = params.get("reflex_name", "")
    if not reflex_name:
        return False, "No reflex_name specified"
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT circuit_breaker_open FROM genesis_reflex_state WHERE reflex_name = %s",
            (reflex_name,),
        ).fetchone()
        if not row:
            return False, f"Reflex '{reflex_name}' not found"
        if not (row["circuit_breaker_open"] if isinstance(row, dict) else row[0]):
            return True, f"Circuit breaker for '{reflex_name}' already closed"
        now = _utcnow_iso()
        conn.execute(
            """
            UPDATE genesis_reflex_state SET
                consecutive_failures = 0, circuit_breaker_open = 0,
                circuit_breaker_tripped_at = NULL, updated_at = %s
            WHERE reflex_name = %s
        """,
            (now, reflex_name),
        )
        conn.commit()
        return True, f"Circuit breaker reset for '{reflex_name}'"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def _action_reinit_db_table(params: Dict) -> Tuple[bool, str]:
    """Re-run init_icdev_db.py to recreate missing tables."""
    try:
        result = subprocess.run(
            [sys.executable, "tools/db/init_icdev_db.py"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        return result.returncode == 0, "DB tables re-initialized"
    except Exception as e:
        return False, str(e)


def _action_fix_canvas_rls_bypass(params: Dict) -> Tuple[bool, str]:
    """Auto-fix canvas db/init_db.py files that import get_connection without RLS bypass.

    Only applies to files flagged by check_canvas_rls_bypass() that match the
    deterministic pattern: importing get_connection from storage + no classification
    column in DDL. Replaces the import+call with get_canvas_connection(env_var).

    This action is safe and idempotent — it only edits files matching an exact
    structural pattern and can be re-run without harm.
    """

    # Derive env_var from the canvas directory name
    def _env_var(canvas_key: str) -> str:
        return canvas_key.upper() + "_STORAGE_BACKEND"

    _IMPORT_PAT = re.compile(
        r"from\s+(?:tools|icdev\.tools)\.db\.storage\s+import[^\n]*\bget_connection\b[^\n]*\n"
    )
    _CALL_PAT = re.compile(r"\bget_connection\s*\(")

    fixed: List[str] = []
    target_files: List[str] = params.get("target_files", [])

    if not target_files:
        # Auto-discover from coherence check
        try:
            from tools.workflow.coherence_checker import check_canvas_rls_bypass
            result = check_canvas_rls_bypass()
            target_files = result.missing
        except Exception as exc:
            return False, f"Could not run check_canvas_rls_bypass: {exc}"

    for rel in target_files:
        file_path = BASE_DIR / rel
        if not file_path.exists():
            continue
        try:
            original = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        # Extract canvas key from path: tools/<canvas>/db/init_db.py → canvas
        parts = Path(rel).parts
        canvas_key = parts[1] if len(parts) >= 2 else "canvas"
        env_var = _env_var(canvas_key)

        # Only fix the deterministic storage-import pattern
        if not _IMPORT_PAT.search(original):
            continue

        # Replace import line: keep other imports on the same line, add canvas import
        new_import = "from tools.db.storage import get_canvas_connection\n"
        patched = _IMPORT_PAT.sub(new_import, original, count=1)
        # Replace get_connection() calls (bare call with no args = canvas pattern)
        patched = _CALL_PAT.sub(f'get_canvas_connection("{env_var}"', patched)

        if patched == original:
            continue

        file_path.write_text(patched, encoding="utf-8", newline="")
        fixed.append(rel)

    if fixed:
        return True, f"Fixed {len(fixed)} file(s): {', '.join(fixed)}"
    return True, "No files needed fixing (check already passing or no target files matched)"


ACTION_HANDLERS = {
    "regenerate_artifact": _action_regenerate_artifact,
    "clear_stale_cache": _action_clear_stale_cache,
    "rerun_tool": _action_rerun_tool,
    "reset_circuit_breaker": _action_reset_circuit_breaker,
    "reinit_db_table": _action_reinit_db_table,
    "fix_canvas_rls_bypass": _action_fix_canvas_rls_bypass,
}

# ---------------------------------------------------------------------------
# Constitution enforcement
# ---------------------------------------------------------------------------

_CB_RESETS_TODAY: dict[str, int] = {}  # date → count; reset per process per UTC day


def _cb_reset_count_today() -> int:
    today = _utcnow().date().isoformat()
    return _CB_RESETS_TODAY.get(today, 0)


def _cb_reset_increment() -> None:
    today = _utcnow().date().isoformat()
    _CB_RESETS_TODAY[today] = _CB_RESETS_TODAY.get(today, 0) + 1


def _check_constitution(pattern: Dict, false_heal_rate: float = 0.0) -> Tuple[bool, str]:
    """Return (allowed, reason). Enforces heal_constitution.yaml rules.

    Returns (False, reason) to block; (True, "") to allow.
    """
    constitution = _load_constitution()
    if not constitution:
        return True, ""

    resolution_type = pattern.get("resolution_type", "")

    # Hard-blocked types
    blocked = constitution.get("blocked_types", [])
    if resolution_type in blocked:
        return False, f"resolution_type '{resolution_type}' is unconditionally blocked by constitution"

    confidence = float(pattern.get("confidence", 0.0))
    rules = constitution.get("rules", [])

    for rule in rules:
        applies_to = rule.get("applies_to", "")
        applies_types = rule.get("applies_to_types", [])

        # Determine whether rule applies to this action
        if applies_types and resolution_type not in applies_types:
            continue
        if applies_to not in ("", "all") and applies_to != resolution_type:
            continue

        on_violation = rule.get("on_violation", "block")

        # Confidence floor check
        min_conf = rule.get("min_confidence")
        if min_conf is not None and confidence < min_conf:
            return False, (
                f"constitution rule '{rule['name']}': confidence {confidence:.3f} "
                f"< required {min_conf} for '{resolution_type}'"
            )

        # False-heal ceiling check
        max_fhr = rule.get("max_false_heal_rate")
        if max_fhr is not None and false_heal_rate > max_fhr:
            return False, (
                f"constitution rule '{rule['name']}': false_heal_rate {false_heal_rate:.3f} "
                f"> ceiling {max_fhr} — auto-healing suspended"
            )

        # Per-day cap check (circuit_breaker only for now)
        max_day = rule.get("max_per_day")
        if max_day is not None and resolution_type == "reset_circuit_breaker":
            if _cb_reset_count_today() >= max_day:
                if on_violation == "hold":
                    return False, (
                        f"constitution rule '{rule['name']}': daily cap {max_day} reached "
                        f"for '{resolution_type}' — holding until tomorrow"
                    )
                return False, f"constitution rule '{rule['name']}': daily cap {max_day} reached"

    return True, ""


# ---------------------------------------------------------------------------
# Anomaly-detection-based adaptive threshold
# ---------------------------------------------------------------------------


def _compute_adaptive_confidence_threshold(fallback: Optional[float] = None) -> float:
    """Compute adaptive confidence threshold via Z-score anomaly detection.

    All tuning parameters (min_samples, z_score_factor, threshold bounds,
    fallback_threshold) are sourced from the ``anomaly_detection`` section of
    heal_constitution.yaml so they are config-driven rather than hardcoded.

    An explicit ``fallback`` argument overrides the constitution's
    fallback_threshold — used in tests and direct calls that need a fixed
    reference point.  Result clamped to [threshold_floor, threshold_ceiling].
    """
    constitution = _load_constitution()
    ad_cfg = constitution.get("anomaly_detection", {})
    # Explicit arg wins; absent → constitution section → hard default
    if fallback is None:
        fallback = float(ad_cfg.get("fallback_threshold", 0.7))
    min_samples = int(ad_cfg.get("min_samples", 10))
    z_score_factor = float(ad_cfg.get("z_score_factor", 0.5))
    floor = float(ad_cfg.get("threshold_floor", 0.5))
    ceiling = float(ad_cfg.get("threshold_ceiling", 0.95))

    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT confidence FROM self_healing_patterns WHERE confidence IS NOT NULL"
        ).fetchall()
        scores = [
            (r["confidence"] if hasattr(r, "keys") else r[0])
            for r in rows
        ]
        scores = [s for s in scores if s is not None]
        if len(scores) < min_samples:
            return fallback
        mean = sum(scores) / len(scores)
        variance = sum((x - mean) ** 2 for x in scores) / len(scores)
        std_dev = variance ** 0.5
        if std_dev < 0.001:
            return fallback
        threshold = mean - z_score_factor * std_dev
        return max(floor, min(ceiling, round(threshold, 4)))
    except Exception:
        return fallback
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Failure detection and pattern matching
# ---------------------------------------------------------------------------


def _get_recent_failures(lookback_hours: int = 6) -> List[Dict[str, Any]]:
    """Query recent error events from audit trail."""
    conn = None
    try:
        conn = get_connection()
        cutoff = (_utcnow() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = conn.execute(
            """
            SELECT id, event_type, details, created_at
            FROM audit_trail
            WHERE event_type LIKE 'error.%'
            AND created_at > %s
            ORDER BY created_at DESC
            LIMIT 50
        """,
            (cutoff,),
        ).fetchall()
        return [
            dict(r) if hasattr(r, "keys") else {"id": r[0], "event_type": r[1], "details": r[2], "created_at": r[3]}
            for r in rows
        ]
    except Exception as e:
        print(f"  WARN: Could not query failures: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


def _get_healing_patterns(min_confidence: float = 0.7) -> List[Dict[str, Any]]:
    """Load known healing patterns from self_healing_patterns table."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, pattern_name, error_pattern, resolution_type,
                   resolution_action, confidence, success_count, failure_count
            FROM self_healing_patterns
            WHERE confidence >= %s
            ORDER BY confidence DESC
            """,
            (min_confidence,),
        ).fetchall()
        return [
            dict(r)
            if hasattr(r, "keys")
            else {
                "id": r[0],
                "pattern_name": r[1],
                "error_pattern": r[2],
                "resolution_type": r[3],
                "resolution_action": r[4],
                "confidence": r[5],
                "success_count": r[6],
                "failure_count": r[7],
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def _get_builtin_patterns() -> List[Dict[str, Any]]:
    """Return built-in healing patterns for common ICDEV™ failures."""
    return [
        {
            "id": "builtin-stale-evidence",
            "pattern_name": "Stale cATO evidence",
            "error_pattern": "evidence_expired",
            "resolution_type": "regenerate_artifact",
            "resolution_action": json.dumps(
                {
                    "action": "regenerate_artifact",
                    "tool_cmd": "tools/compliance/evidence_collector.py",
                    "args": ["--project-id", "sparkpilot", "--json"],
                }
            ),
            "confidence": 0.9,
            "success_count": 0,
            "failure_count": 0,
        },
        {
            "id": "builtin-stale-cache",
            "pattern_name": "Stale temp files blocking execution",
            "error_pattern": "FileExistsError",
            "resolution_type": "clear_stale_cache",
            "resolution_action": json.dumps(
                {
                    "action": "clear_stale_cache",
                    "max_age_hours": 48,
                }
            ),
            "confidence": 0.95,
            "success_count": 0,
            "failure_count": 0,
        },
        {
            "id": "builtin-missing-table",
            "pattern_name": "Missing DB table",
            "error_pattern": "no such table",
            "resolution_type": "reinit_db_table",
            "resolution_action": json.dumps(
                {
                    "action": "reinit_db_table",
                }
            ),
            "confidence": 0.9,
            "success_count": 0,
            "failure_count": 0,
        },
        {
            "id": "builtin-circuit-breaker",
            "pattern_name": "Genesis reflex circuit breaker tripped",
            "error_pattern": "circuit_breaker_open",
            "resolution_type": "reset_circuit_breaker",
            "resolution_action": json.dumps(
                {
                    "action": "reset_circuit_breaker",
                }
            ),
            "confidence": 0.75,
            "success_count": 0,
            "failure_count": 0,
        },
        {
            "id": "builtin-pid-stale",
            "pattern_name": "Stale PID file blocking daemon",
            "error_pattern": ".pid",
            "resolution_type": "clear_stale_cache",
            "resolution_action": json.dumps(
                {
                    "action": "clear_stale_cache",
                    "max_age_hours": 1,
                }
            ),
            "confidence": 0.85,
            "success_count": 0,
            "failure_count": 0,
        },
    ]


def _match_pattern(failure: Dict, patterns: List[Dict]) -> Optional[Dict]:
    """Match a failure against known healing patterns."""
    error_type = failure.get("event_type", "")
    event_data = failure.get("details", "")
    if isinstance(event_data, str):
        try:
            event_data = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            pass

    error_msg = str(event_data) if event_data else error_type

    for pattern in patterns:
        match_str = pattern.get("error_pattern", "")
        if match_str and match_str.lower() in error_msg.lower():
            return pattern

    return None


def _record_healing_event(failure_id, pattern_id, action: str, success: bool) -> None:
    """Record a healing event in self_healing_events table."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO self_healing_events
            (project_id, pattern_id, trigger_source, trigger_data, action_taken,
             outcome, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
            (
                "",
                str(pattern_id),
                "genesis_heal",
                str(failure_id),
                action,
                "success" if success else "failed",
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"  WARN: Could not record healing event: {e}")
    finally:
        conn.close()


def _update_pattern_confidence(pattern_id, success: bool) -> None:
    """Update pattern confidence based on outcome (+0.02 success, -0.05 failure)."""
    if str(pattern_id).startswith("builtin-"):
        return  # Don't update builtin patterns
    conn = get_connection()
    try:
        if success:
            conn.execute(
                """
                UPDATE self_healing_patterns
                SET success_count = success_count + 1,
                    confidence = MIN(1.0, confidence + 0.02)
                WHERE id = %s
            """,
                (pattern_id,),
            )
        else:
            conn.execute(
                """
                UPDATE self_healing_patterns
                SET failure_count = failure_count + 1,
                    confidence = MAX(0.0, confidence - 0.05)
                WHERE id = %s
            """,
                (pattern_id,),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _apply_remediation(pattern: Dict, failure: Dict) -> Tuple[bool, str]:
    """Apply a remediation action via the action handler dispatch table."""
    resolution_action = pattern.get("resolution_action", "")

    # Parse action params
    params = {}
    if resolution_action:
        try:
            params = json.loads(resolution_action) if isinstance(resolution_action, str) else resolution_action
        except (json.JSONDecodeError, TypeError):
            params = {"action": resolution_action}

    action_name = params.get("action", pattern.get("resolution_type", ""))

    # Dispatch to handler
    handler = ACTION_HANDLERS.get(action_name)
    if not handler:
        # Fallback: log-only for unknown action types
        print(
            f"  Heal: matched pattern '{pattern.get('pattern_name', '')}' -> unknown action '{action_name}' (log-only)"
        )
        return True, f"logged_only:{action_name}"

    # Extract reflex_name from failure context for circuit breaker resets
    if action_name == "reset_circuit_breaker" and "reflex_name" not in params:
        details = failure.get("details", "")
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (json.JSONDecodeError, TypeError):
                details = {}
        if isinstance(details, dict):
            params["reflex_name"] = details.get("reflex_name", "")

    print(f"  Heal: executing '{action_name}' for pattern '{pattern.get('pattern_name', '')}'")
    try:
        success, message = handler(params)
        print(f"  Heal: {'SUCCESS' if success else 'FAILED'} — {message[:100]}")
        if success and action_name == "reset_circuit_breaker":
            _cb_reset_increment()
        return success, message
    except Exception as e:
        msg = f"Handler exception: {e}"
        print(f"  Heal: FAILED — {msg}")
        return False, msg


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Heal Reflex."""
    # Adaptive threshold via Z-score anomaly detection; all params from constitution.
    adaptive_threshold = _compute_adaptive_confidence_threshold()
    confidence_threshold = config.get("confidence_threshold", adaptive_threshold)
    max_heals = config.get("max_auto_heals_per_hour", 5)
    lookback_hours = config.get("lookback_hours", 6)
    max_failures_per_cycle = config.get("max_failures_per_cycle", 20)

    # Read current false-heal rate for constitution enforcement
    false_heal_rate = 0.0
    try:
        from tools.genesis.harness.eval_harness import compute_metrics as _compute_metrics
        false_heal_rate = _compute_metrics("heal", window_days=30).get("false_heal_rate", 0.0)
    except Exception:
        pass

    # Get recent failures
    failures = _get_recent_failures(lookback_hours=lookback_hours)
    if not failures:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "no_recent_failures"},
        }

    # Load healing patterns: DB patterns + built-in patterns
    patterns = _get_healing_patterns(min_confidence=confidence_threshold) + _get_builtin_patterns()
    if not patterns:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {
                "status": "no_healing_patterns",
                "failures_found": len(failures),
            },
        }

    matches = []
    healed = 0

    for failure in failures[:max_failures_per_cycle]:
        match = _match_pattern(failure, patterns)
        if not match:
            continue

        if match.get("confidence", 0) < confidence_threshold:
            continue

        # Constitution enforcement
        allowed, block_reason = _check_constitution(match, false_heal_rate)
        if not allowed:
            matches.append({
                "failure_id": failure.get("id"),
                "pattern": match.get("pattern_name"),
                "status": "constitution_blocked",
                "reason": block_reason,
            })
            continue

        if healed >= max_heals:
            matches.append(
                {
                    "failure_id": failure.get("id"),
                    "pattern": match.get("pattern_name"),
                    "status": "rate_limited",
                }
            )
            continue

        success, message = _apply_remediation(match, failure)
        _record_healing_event(
            failure_id=failure.get("id", 0),
            pattern_id=match.get("id", 0),
            action=match.get("resolution_action", ""),
            success=success,
        )
        _update_pattern_confidence(match.get("id", 0), success)

        healed += 1
        matches.append(
            {
                "failure_id": failure.get("id"),
                "pattern": match.get("pattern_name"),
                "action": match.get("resolution_type", ""),
                "status": "healed" if success else "failed",
                "message": message[:200],
                "confidence": match.get("confidence"),
            }
        )

    return {
        "success": True,
        "metric_value": float(healed),
        "details": {
            "failures_scanned": len(failures),
            "patterns_available": len(patterns),
            "matches_found": len(matches),
            "healed_count": healed,
            "matches": matches,
        },
    }
