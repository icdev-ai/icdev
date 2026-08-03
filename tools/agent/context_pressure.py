#!/usr/bin/env python3
# CUI // SP-CTI
"""Context Pressure Monitor & Stuck Detection Guard (GSD-adapted).

Two GSD patterns adapted for ICDEV™'s FORGE framework:

1. **Context Pressure Monitor** — Tracks context window consumption and
   signals when agents should finalize durable outputs (commits, summaries)
   before quality degradation occurs.

2. **Stuck Detection Guard** — Detects when agents enter analysis paralysis
   (consecutive read-only tool calls without writes) or loop on the same
   action, then intervenes.

100% deterministic (stdlib only), air-gap safe.

Architecture Decisions:
  D-GSD-4: Context pressure uses token estimation from tool event history (D6)
  D-GSD-5: Stuck detection is advisory — logs + returns signal, never kills
  D-GSD-6: All decisions append-only in context_pressure_events table (NIST AU)
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.common.helpers import now_iso  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent.context_pressure")

DB_PATH = BASE_DIR / "data" / "icdev.db"

# ── Configuration (overridable via args/context_pressure_config.yaml) ────────
DEFAULT_CONFIG = {
    # Context pressure thresholds (percentage of window remaining)
    "warning_threshold_pct": 35,  # WARNING at 35% remaining
    "critical_threshold_pct": 25,  # CRITICAL at 25% remaining
    "context_window_tokens": 200000,  # Default context window size
    # Stuck detection
    "max_consecutive_reads": 5,  # Analysis paralysis after N reads
    "max_duplicate_calls": 3,  # Loop detection after N identical calls
    "duplicate_window_minutes": 5,  # Time window for duplicate detection
    # Token estimation (chars -> tokens approximation)
    "chars_per_token": 4,  # Average chars per token
    # Actions
    "auto_checkpoint": False,  # Auto-trigger checkpoint on critical
    "auto_compress": False,  # Phase 72: Auto-compress on warning/critical
    "preserve_recent_turns": 5,  # Keep N most recent messages uncompressed
    "log_to_db": True,  # Store events in DB
}


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = get_connection(db_path=str(path))
    return conn


def _load_config() -> dict:
    """Load config from YAML if available, else use defaults."""
    config = dict(DEFAULT_CONFIG)
    config_path = BASE_DIR / "args" / "context_pressure_config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f) or {}
            config.update(yaml_config)
        except (ImportError, Exception):
            pass  # YAML not available or parse error — use defaults
    return config


# ── Context Pressure Monitor ────────────────────────────────────────────────
def estimate_context_usage(session_id: str = None, db_path: Path = None) -> dict:
    """Estimate current context window usage from hook_events history.

    Calculates token consumption by summing tool input/output sizes
    from the current session's hook_events.
    """
    config = _load_config()
    result = {
        "timestamp": now_iso(),
        "session_id": session_id or "unknown",
        "estimated_tokens_used": 0,
        "context_window_tokens": config["context_window_tokens"],
        "estimated_remaining_pct": 100.0,
        "pressure_level": "normal",  # normal, warning, critical
        "tool_call_count": 0,
        "recommendation": None,
    }

    conn = _get_db(db_path)
    try:
        # Query recent tool events for this session
        query = """
            SELECT tool_name, payload
            FROM hook_events
            WHERE hook_type = 'post_tool_use'
        """
        params = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        # Limit to last 500 events for performance
        query += " ORDER BY id DESC LIMIT 500"

        rows = conn.execute(query, params).fetchall()
        result["tool_call_count"] = len(rows)

        total_chars = 0
        for row in rows:
            payload = row["payload"]
            if payload:
                try:
                    data = json.loads(payload) if isinstance(payload, str) else payload
                    total_chars += data.get("output_length", 0)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Estimate tokens
        chars_per_token = config["chars_per_token"]
        result["estimated_tokens_used"] = total_chars // chars_per_token

        # Calculate remaining percentage
        window = config["context_window_tokens"]
        used = result["estimated_tokens_used"]
        remaining_pct = max(0, ((window - used) / window) * 100)
        result["estimated_remaining_pct"] = round(remaining_pct, 1)

        # Determine pressure level
        if remaining_pct <= config["critical_threshold_pct"]:
            result["pressure_level"] = "critical"
            result["recommendation"] = (
                "CRITICAL: Context window near saturation. "
                "Finalize durable outputs (commit, write summaries) immediately. "
                "Consider spawning a fresh subagent for remaining work."
            )
        elif remaining_pct <= config["warning_threshold_pct"]:
            result["pressure_level"] = "warning"
            result["recommendation"] = (
                "WARNING: Context window filling. "
                "Prioritize completing current task and committing results. "
                "Avoid loading additional large files."
            )
        else:
            result["pressure_level"] = "normal"
            result["recommendation"] = "Context usage within normal range."

    except Exception as e:
        result["error"] = str(e)
    finally:
        conn.close()

    # Phase 72 (D-CMP-1): Auto-compress on warning/critical
    if config.get("auto_compress", False) and result["pressure_level"] in ("warning", "critical"):
        compress_result = trigger_auto_compression(
            budget_tokens=config["context_window_tokens"],
            preserve_recent_n=config.get("preserve_recent_turns", 5),
        )
        result["auto_compression"] = compress_result

    # Log to DB if configured
    if config["log_to_db"] and result["pressure_level"] != "normal":
        _log_pressure_event(result, db_path)

    return result


def _log_pressure_event(result: dict, db_path: Path = None):
    """Log a pressure event to the database (append-only)."""
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO context_pressure_events
               (session_id, event_type, pressure_level, estimated_tokens_used,
                estimated_remaining_pct, tool_call_count, recommendation,
                created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                result.get("session_id", "unknown"),
                "pressure_check",
                result["pressure_level"],
                result["estimated_tokens_used"],
                result["estimated_remaining_pct"],
                result["tool_call_count"],
                result.get("recommendation", ""),
                now_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Best effort — never block on DB issues
        logger.warning(
            "_log_pressure_event: best-effort INSERT into context_pressure_events failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ── Stuck Detection Guard ───────────────────────────────────────────────────
def detect_stuck(session_id: str = None, db_path: Path = None) -> dict:
    """Detect if the current session is stuck in analysis paralysis or loops.

    Checks:
    1. Consecutive read-only tool calls without writes (analysis paralysis)
    2. Duplicate identical tool calls within time window (looping)
    3. Repeated failures on the same operation (retry spiral)
    """
    config = _load_config()
    result = {
        "timestamp": now_iso(),
        "session_id": session_id or "unknown",
        "is_stuck": False,
        "stuck_type": None,  # analysis_paralysis, duplicate_loop, retry_spiral
        "severity": "normal",  # normal, warning, stuck
        "consecutive_reads": 0,
        "duplicate_calls": 0,
        "recommendation": None,
        "details": [],
    }

    # Read-only tools (don't produce durable output)
    READ_ONLY_TOOLS = {
        "Read",
        "Glob",
        "Grep",
        "Bash",  # when used for reads
    }
    # Write tools (produce durable output)
    WRITE_TOOLS = {
        "Write",
        "Edit",
        "NotebookEdit",
    }

    conn = _get_db(db_path)
    try:
        # Get recent tool events
        query = """
            SELECT tool_name, payload, created_at
            FROM hook_events
            WHERE hook_type IN ('pre_tool_use', 'post_tool_use')
        """
        params = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        query += " ORDER BY id DESC LIMIT 50"
        rows = conn.execute(query, params).fetchall()

        if not rows:
            result["details"].append("No tool events found")
            return result

        # 1. Analysis Paralysis: consecutive reads without writes
        consecutive_reads = 0
        for row in rows:
            tool = row["tool_name"]
            if tool in READ_ONLY_TOOLS:
                consecutive_reads += 1
            elif tool in WRITE_TOOLS:
                break
            else:
                # Agent tool, Bash for writes, etc. — neutral
                consecutive_reads += 1

        result["consecutive_reads"] = consecutive_reads

        if consecutive_reads >= config["max_consecutive_reads"]:
            result["is_stuck"] = True
            result["stuck_type"] = "analysis_paralysis"
            result["severity"] = "stuck"
            result["recommendation"] = (
                f"STUCK: {consecutive_reads} consecutive read-only tool calls "
                f"without any writes. Stop reading and start implementing. "
                f"If blocked, try a different approach or ask for clarification."
            )
            result["details"].append(
                f"Analysis paralysis: {consecutive_reads} reads (threshold: {config['max_consecutive_reads']})"
            )

        # 2. Duplicate Loop: same tool+input repeated
        # Only check if analysis paralysis was NOT already detected
        if not result["is_stuck"]:
            recent_calls = []
            for row in rows[:20]:
                tool = row["tool_name"]
                payload = row["payload"] or ""
                # Create a signature from tool name + payload hash
                sig = f"{tool}:{hash(payload)}"
                recent_calls.append(sig)

            if recent_calls:
                # Count most frequent call
                from collections import Counter

                counts = Counter(recent_calls)
                most_common, count = counts.most_common(1)[0]
                result["duplicate_calls"] = count

                if count >= config["max_duplicate_calls"]:
                    result["is_stuck"] = True
                    result["stuck_type"] = "duplicate_loop"
                    result["severity"] = "stuck"
                    tool_name = most_common.split(":")[0]
                    result["recommendation"] = (
                        f"STUCK: Tool '{tool_name}' called {count} times with "
                        f"similar input. This suggests a loop. Try a fundamentally "
                        f"different approach or escalate for human guidance."
                    )
                    result["details"].append(
                        f"Duplicate loop: {tool_name} called {count}x (threshold: {config['max_duplicate_calls']})"
                    )

        # 3. Warning level: approaching thresholds
        if not result["is_stuck"]:
            if consecutive_reads >= config["max_consecutive_reads"] - 1:
                result["severity"] = "warning"
                result["recommendation"] = (
                    f"Approaching analysis paralysis: {consecutive_reads} "
                    f"consecutive reads. Consider writing code or committing."
                )

    except Exception as e:
        result["details"].append(f"Error during detection: {e}")
    finally:
        conn.close()

    # Log stuck events to DB
    if config["log_to_db"] and result["is_stuck"]:
        _log_stuck_event(result, db_path)

    return result


def _log_stuck_event(result: dict, db_path: Path = None):
    """Log a stuck detection event (append-only)."""
    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO context_pressure_events
               (session_id, event_type, pressure_level, estimated_tokens_used,
                estimated_remaining_pct, tool_call_count, recommendation,
                created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                result.get("session_id", "unknown"),
                f"stuck_{result.get('stuck_type', 'unknown')}",
                result["severity"],
                0,
                0,
                result.get("consecutive_reads", 0),
                result.get("recommendation", ""),
                now_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Best effort
        logger.warning(
            "_log_stuck_event: best-effort INSERT into context_pressure_events failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ── Phase 72: Auto-Compression (D-CMP-1, Hermes adaptation) ─────────────────


def trigger_auto_compression(
    budget_tokens: int = 200000,
    preserve_recent_n: int = 5,
) -> dict:
    """Trigger history compression when context pressure is elevated.

    Delegates to tools/memory/history_compressor.py for actual compression.
    This function is called automatically when auto_compress is enabled
    and pressure reaches WARNING or CRITICAL.

    Args:
        budget_tokens: Target context window size in tokens.
        preserve_recent_n: Number of most recent messages to preserve verbatim.

    Returns:
        Dict with compression results.
    """
    result = {
        "triggered_at": now_iso(),
        "budget_tokens": budget_tokens,
        "preserve_recent": preserve_recent_n,
    }
    try:
        from tools.memory.history_compressor import HistoryCompressor

        compressor = HistoryCompressor()

        # Compress using 3-tier budget with recent preservation
        compress_out = compressor.compress(
            budget_tokens=budget_tokens,
            preserve_recent_n=preserve_recent_n,
        )
        result["status"] = "compressed"
        result["original_tokens"] = compress_out.get("original_tokens", 0)
        result["compressed_tokens"] = compress_out.get("compressed_tokens", 0)
        result["savings_pct"] = compress_out.get("savings_pct", 0)
    except ImportError:
        result["status"] = "unavailable"
        result["error"] = "history_compressor module not found"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


# ── Combined Health Check ────────────────────────────────────────────────────
def health_check(session_id: str = None, db_path: Path = None) -> dict:
    """Run both context pressure and stuck detection checks."""
    pressure = estimate_context_usage(session_id, db_path)
    stuck = detect_stuck(session_id, db_path)

    return {
        "timestamp": now_iso(),
        "session_id": session_id or "unknown",
        "context_pressure": pressure,
        "stuck_detection": stuck,
        "overall_healthy": (pressure["pressure_level"] == "normal" and not stuck["is_stuck"]),
        "actions_needed": [
            a
            for a in [
                pressure.get("recommendation") if pressure["pressure_level"] != "normal" else None,
                stuck.get("recommendation") if stuck["is_stuck"] else None,
            ]
            if a
        ],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Context Pressure Monitor & Stuck Detection Guard (GSD-adapted)")
    parser.add_argument(
        "--check",
        choices=["pressure", "stuck", "health"],
        default="health",
        help="Which check to run",
    )
    parser.add_argument("--session-id", help="Session ID to analyze")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    session_id = args.session_id

    if args.check == "pressure":
        result = estimate_context_usage(session_id)
    elif args.check == "stuck":
        result = detect_stuck(session_id)
    else:
        result = health_check(session_id)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        _print_human(result, args.check)
    else:
        print(json.dumps(result, indent=2, default=str))


def _print_human(result: dict, check_type: str):
    """Human-readable output."""
    print(f"\n{'=' * 60}")

    if check_type == "pressure":
        level = result.get("pressure_level", "unknown").upper()
        pct = result.get("estimated_remaining_pct", 0)
        print(f"  Context Pressure: {level}")
        print(f"  Remaining: {pct:.1f}%")
        print(f"  Tool calls: {result.get('tool_call_count', 0)}")
        if result.get("recommendation"):
            print(f"  >> {result['recommendation']}")

    elif check_type == "stuck":
        stuck = result.get("is_stuck", False)
        print(f"  Stuck Detection: {'STUCK' if stuck else 'OK'}")
        if stuck:
            print(f"  Type: {result.get('stuck_type', 'unknown')}")
        print(f"  Consecutive reads: {result.get('consecutive_reads', 0)}")
        print(f"  Duplicate calls: {result.get('duplicate_calls', 0)}")
        if result.get("recommendation"):
            print(f"  >> {result['recommendation']}")

    else:  # health
        healthy = result.get("overall_healthy", False)
        print(f"  Agent Health: {'HEALTHY' if healthy else 'NEEDS ATTENTION'}")
        pressure = result.get("context_pressure", {})
        stuck = result.get("stuck_detection", {})
        print(f"  Context: {pressure.get('pressure_level', 'unknown').upper()}")
        print(f"  Stuck: {'YES' if stuck.get('is_stuck') else 'NO'}")
        for action in result.get("actions_needed", []):
            print(f"  >> {action}")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
