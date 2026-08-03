#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Daemon — Daily Autonomous ICDEV™ Self-Improvement Scanner.

Single unified daemon that scans three pillars daily:
1. Self-Introspection — analyze ICDEV™'s own codebase
2. Trending Open Source — GitHub, HackerNews, Reddit, arXiv
3. Competitive Intel — monitor competitors, identify new ones

Then generates a digest, updates configs with new repos, feeds signals
to the innovation engine, and triggers Genesis to auto-build the
highest-value improvement.

Runs via Windows Task Scheduler. Defers if CPU/VRAM is high or
Claude Code is active. Retries every 30 min until 11pm.

Usage:
    python tools/scout/daemon.py --once --json        # Single scan (Task Scheduler mode)
    python tools/scout/daemon.py --status --json      # Last scan results
    python tools/scout/daemon.py --digest 2026-03-20  # View past digest
    python tools/scout/daemon.py --pillar introspect --json   # Run one pillar
    python tools/scout/daemon.py --pillar trending --json
    python tools/scout/daemon.py --pillar competitive --json
"""

import argparse
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.scout.daemon")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS scout_scans (
    id TEXT PRIMARY KEY,
    scan_date TEXT NOT NULL,
    pillar_results TEXT,
    digest_path TEXT,
    total_findings INTEGER DEFAULT 0,
    signals_fed INTEGER DEFAULT 0,
    repos_added INTEGER DEFAULT 0,
    genesis_status TEXT,
    genesis_branch TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scout_audit (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    pillar TEXT,
    details TEXT,
    success INTEGER,
    duration_ms INTEGER,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);
"""


def _ensure_tables() -> None:
    conn = get_connection()
    try:
        conn.executescript(CREATE_TABLES_SQL)
        conn.commit()
    except Exception:
        # executescript may not work on postgres — try individually
        for stmt in CREATE_TABLES_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_id() -> str:
    return f"scout-{uuid.uuid4().hex[:12]}"


def _audit(event_type: str, details: str = "", pillar: str = "", success: bool = True, duration_ms: int = 0) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO scout_audit (id, event_type, pillar, details, success, duration_ms, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                f"saud-{uuid.uuid4().hex[:12]}",
                event_type,
                pillar,
                details[:2000],
                1 if success else 0,
                duration_ms,
                _now(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into scout_audit failed (non-blocking): %s", exc)


def _load_config() -> dict:
    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _feed_innovation_signals(findings: List[dict], config: dict) -> int:
    """Insert qualifying Scout findings as innovation signals."""
    min_score = config.get("integration", {}).get("min_signal_score", 0.6)
    count = 0
    try:
        conn = get_connection()
        for f in findings:
            if f.get("relevance_score", 0) < min_score:
                continue
            try:
                now = _now()
                url = f.get("url", "")
                title = f.get("title", "")[:500]
                # score/raw_data are not columns on innovation_signals
                # (swp-scan-01); the live names are raw_score/metadata. And
                # content_hash/discovered_at are NOT NULL with no default, so
                # omitting them failed the row on its own. Every miss landed in
                # the bare `except: pass` below, so Scout has never fed a single
                # signal to the Innovation Engine.
                conn.execute(
                    """INSERT INTO innovation_signals
                       (id, source, source_type, title, description, url,
                        category, raw_score, metadata, content_hash,
                        discovered_at, status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s)""",
                    (
                        f.get("id", f"scout-sig-{uuid.uuid4().hex[:12]}"),
                        "scout_daemon",
                        f.get("category", "scout"),
                        title,
                        f.get("description", "")[:2000],
                        url,
                        f.get("category", "scout"),
                        f.get("relevance_score", 0),
                        json.dumps(f.get("metadata", {})),
                        hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest(),
                        now,
                        now,
                    ),
                )
                count += 1
            except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # Duplicate or schema mismatch — skip
                logger.warning(
                    "_feed_innovation_signals: best-effort INSERT into innovation_signals failed (non-blocking): %s",
                    exc,
                )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_feed_innovation_signals: best-effort INSERT into innovation_signals failed (non-blocking): %s",
            exc,
        )
    return count


def _update_heartbeat(config: dict, findings_count: int, duration_ms: int) -> None:
    """Update heartbeat system so dashboard shows Scout status."""
    if not config.get("heartbeat", {}).get("enabled", True):
        return
    try:
        conn = get_connection()
        check_name = config.get("heartbeat", {}).get("check_name", "scout_daily_scan")
        now = _now()
        # Upsert heartbeat check
        conn.execute(
            # The live column is result_summary, not details (swp-scan-01) —
            # the bare `except: pass` below meant no scout heartbeat ever
            # landed.
            """INSERT INTO heartbeat_checks (check_type, last_run, next_run, status, duration_ms, result_summary)
               VALUES (%s, %s, %s, 'healthy', %s, %s)
               ON CONFLICT(check_type) DO UPDATE SET
                   last_run = excluded.last_run,
                   status = 'healthy',
                   duration_ms = excluded.duration_ms,
                   result_summary = excluded.result_summary""",
            (check_name, now, now, duration_ms, json.dumps({"findings": findings_count, "scan_date": now[:10]})),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Heartbeat table may not exist
        logger.warning("_update_heartbeat: best-effort INSERT into heartbeat_checks failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------


def run_scout(config: dict = None) -> dict:
    """Main orchestrator: preflight → pillars → digest → config → genesis."""
    config = config or _load_config()
    _ensure_tables()

    scan_id = _scan_id()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_ms = int(time.time() * 1000)
    result = {"scan_id": scan_id, "date": date_str, "started_at": _now()}

    # Step 0: Preflight
    from tools.scout.preflight import check_all

    ok, reason, pf_details = check_all(config)
    result["preflight"] = {"ok": ok, "reason": reason, "details": pf_details}

    if not ok:
        _audit("scout.deferred", reason, success=False)
        result["status"] = "deferred"
        result["reason"] = reason
        return result

    _audit("scout.started", f"Scan {scan_id} started")
    ollama_ok = pf_details.get("ollama", {}).get("ok", False)
    all_findings = []

    # Step 1: Pillar 1 — Introspect
    try:
        from tools.scout.pillars.introspect import scan as introspect_scan

        p1 = introspect_scan(config)
        result["introspect"] = {
            "finding_count": p1.get("finding_count", 0),
            "duration": p1.get("completed_at", ""),
        }
        all_findings.extend(p1.get("findings", []))
        _audit("scout.pillar.introspect", f"{p1.get('finding_count', 0)} findings", "introspect")
    except Exception as exc:
        result["introspect"] = {"error": str(exc)}
        _audit("scout.pillar.introspect", str(exc), "introspect", success=False)

    # Step 2: Pillar 2 — Trending
    try:
        from tools.scout.pillars.trending import scan as trending_scan

        p2 = trending_scan(config)
        result["trending"] = {
            "finding_count": p2.get("finding_count", 0),
            "duration": p2.get("completed_at", ""),
        }
        all_findings.extend(p2.get("findings", []))
        _audit("scout.pillar.trending", f"{p2.get('finding_count', 0)} findings", "trending")
    except Exception as exc:
        result["trending"] = {"error": str(exc)}
        _audit("scout.pillar.trending", str(exc), "trending", success=False)

    # Step 3: Pillar 3 — Competitive (receives trending findings for cross-ref)
    trending_findings = [f for f in all_findings if f.get("pillar") == "trending"]
    try:
        from tools.scout.pillars.competitive import scan as competitive_scan

        p3 = competitive_scan(config, trending_findings)
        result["competitive"] = {
            "finding_count": p3.get("finding_count", 0),
            "new_competitors": p3.get("new_competitors_identified", 0),
            "duration": p3.get("completed_at", ""),
        }
        all_findings.extend(p3.get("findings", []))
        _audit("scout.pillar.competitive", f"{p3.get('finding_count', 0)} findings", "competitive")
    except Exception as exc:
        result["competitive"] = {"error": str(exc)}
        _audit("scout.pillar.competitive", str(exc), "competitive", success=False)

    # Step 4: LLM Synthesis (Ollama only, skip if unavailable)
    synthesis = None
    if ollama_ok:
        try:
            from tools.scout.llm_summarizer import synthesize

            synthesis = synthesize(all_findings, config)
            result["synthesis"] = "generated" if synthesis else "skipped"
        except Exception:
            result["synthesis"] = "failed"
    else:
        result["synthesis"] = "ollama_unavailable"

    # Step 5: Config updates (add new repos)
    try:
        from tools.scout.config_updater import update_from_findings

        comp_findings = [f for f in all_findings if f.get("pillar") == "competitive"]
        config_result = update_from_findings(comp_findings, config)
        result["config_updates"] = config_result
        repos_added = config_result.get("total_repos_added", 0)
    except Exception as exc:
        result["config_updates"] = {"error": str(exc)}
        repos_added = 0

    # Step 6: Feed innovation signals
    signals_fed = 0
    if config.get("integration", {}).get("feed_to_innovation_signals", True):
        signals_fed = _feed_innovation_signals(all_findings, config)
        result["signals_fed"] = signals_fed

    # Step 7: Genesis build trigger
    genesis_result = None
    if config.get("genesis_trigger", {}).get("enabled", True) and all_findings:
        try:
            from tools.scout.genesis_trigger import trigger_build

            genesis_result = trigger_build(all_findings, config)
            result["genesis"] = {
                "status": genesis_result.get("status"),
                "branch": genesis_result.get("branch"),
                "finding_title": genesis_result.get("finding_title"),
                "attempts": len(genesis_result.get("attempts", [])),
            }
            _audit("scout.genesis", json.dumps(result["genesis"])[:500], "genesis")
        except Exception as exc:
            result["genesis"] = {"error": str(exc)}
            _audit("scout.genesis", str(exc), "genesis", success=False)

    # Step 8: Generate digest
    duration_ms = int(time.time() * 1000) - start_ms
    stats = {
        "duration_ms": duration_ms,
        "signals_fed": signals_fed,
        "repos_added": repos_added,
    }

    try:
        from tools.scout.digest import generate

        digest_path = generate(
            date_str=date_str,
            findings=all_findings,
            synthesis=synthesis,
            genesis_result=genesis_result,
            stats=stats,
            config=config,
        )
        result["digest_path"] = str(digest_path)
    except Exception as exc:
        result["digest_path"] = None
        result["digest_error"] = str(exc)

    # Step 9: Record scan in DB
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO scout_scans
               (id, scan_date, pillar_results, digest_path, total_findings,
                signals_fed, repos_added, genesis_status, genesis_branch,
                duration_ms, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                scan_id,
                date_str,
                json.dumps(
                    {
                        "introspect": result.get("introspect", {}),
                        "trending": result.get("trending", {}),
                        "competitive": result.get("competitive", {}),
                    }
                ),
                result.get("digest_path"),
                len(all_findings),
                signals_fed,
                repos_added,
                result.get("genesis", {}).get("status"),
                result.get("genesis", {}).get("branch"),
                duration_ms,
                _now(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("run_scout: best-effort INSERT into scout_scans failed (non-blocking): %s", _exc)

    # Step 10: Update heartbeat
    _update_heartbeat(config, len(all_findings), duration_ms)

    result["status"] = "completed"
    result["total_findings"] = len(all_findings)
    result["duration_ms"] = duration_ms
    result["completed_at"] = _now()
    _audit("scout.completed", f"{len(all_findings)} findings, {duration_ms}ms")

    return result


def run_with_retry(config: dict = None) -> dict:
    """Run scout with retry on deferral (CPU/VRAM too high)."""
    config = config or _load_config()
    sched = config.get("schedule", {})
    retry_interval = sched.get("retry_interval_minutes", 30)
    max_retries = sched.get("max_retries", 30)

    for attempt in range(max_retries + 1):
        result = run_scout(config)

        if result.get("status") != "deferred":
            return result

        # Check if still within operating window
        from tools.scout.preflight import check_operating_window

        ok, _ = check_operating_window(config)
        if not ok:
            result["status"] = "window_closed"
            result["reason"] = "Operating window closed, giving up"
            return result

        if attempt < max_retries:
            _audit("scout.retry", f"Attempt {attempt + 1}, waiting {retry_interval}min")
            time.sleep(retry_interval * 60)

    result["status"] = "max_retries"
    result["reason"] = f"Exhausted {max_retries} retries"
    return result


def get_status() -> dict:
    """Get last scan results from DB."""
    _ensure_tables()
    try:
        conn = get_connection()
        row = conn.execute("""SELECT * FROM scout_scans ORDER BY created_at DESC LIMIT 1""").fetchone()
        conn.close()
        if row:
            return dict(row)
        return {"status": "no_scans", "message": "No Scout scans recorded yet"}
    except Exception as exc:
        return {"error": str(exc)}


def run_single_pillar(pillar_name: str, config: dict = None) -> dict:
    """Run a single pillar for testing/debugging."""
    config = config or _load_config()

    if pillar_name == "introspect":
        from tools.scout.pillars.introspect import scan

        return scan(config)
    elif pillar_name == "trending":
        from tools.scout.pillars.trending import scan

        return scan(config)
    elif pillar_name == "competitive":
        from tools.scout.pillars.competitive import scan

        return scan(config)
    else:
        return {"error": f"Unknown pillar: {pillar_name}"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Daemon — Daily Autonomous ICDEV™ Self-Improvement Scanner")
    parser.add_argument("--once", action="store_true", help="Single scan pass (Task Scheduler mode, with retry)")
    parser.add_argument("--run", action="store_true", help="Single scan pass (no retry)")
    parser.add_argument("--status", action="store_true", help="Show last scan results")
    parser.add_argument("--digest", metavar="DATE", help="View past digest (YYYY-MM-DD)")
    parser.add_argument("--pillar", choices=["introspect", "trending", "competitive"], help="Run a single pillar")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    config = _load_config()

    # Check master switch
    import os

    env_key = config.get("env_override", "ICDEV_SCOUT_ENABLED")
    env_val = os.environ.get(env_key, "").lower()
    if env_val in ("false", "0", "no"):
        print(json.dumps({"status": "disabled", "reason": f"{env_key}=false"}))
        return
    if not config.get("enabled", True) and env_val not in ("true", "1", "yes"):
        print(json.dumps({"status": "disabled", "reason": "enabled=false in config"}))
        return

    if args.once:
        result = run_with_retry(config)
    elif args.run:
        result = run_scout(config)
    elif args.status:
        result = get_status()
    elif args.digest:
        from tools.scout.digest import get_digest

        content = get_digest(args.digest, config)
        if content:
            print(content)
        else:
            print(f"No digest found for {args.digest}")
        return
    elif args.pillar:
        result = run_single_pillar(args.pillar, config)
    else:
        parser.error("Specify --once, --run, --status, --digest, or --pillar")
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        # Human-readable summary
        status = result.get("status", "?")
        total = result.get("total_findings", result.get("finding_count", 0))
        duration = result.get("duration_ms", 0)
        print(f"Scout scan: {status}")
        print(f"  Findings: {total}")
        print(f"  Duration: {duration / 1000:.1f}s")
        if result.get("digest_path"):
            print(f"  Digest: {result['digest_path']}")
        if result.get("genesis", {}).get("branch"):
            print(f"  Genesis: {result['genesis']['branch']}")


if __name__ == "__main__":
    main()
