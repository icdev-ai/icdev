#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Log-Triage Reflex — autonomous build-log → Kanban remediation (LOG-06).

Reads .logs/build.ndjson tail, deduplicates failure signatures by
(component, message_hash), and creates Kanban tasks for each unseen
failure so the Kanban scheduler can dispatch them for automated repair.

Schedule: every 30m (same cadence as failure_triage).
Risk tier: GREEN — read-only on logs, write-only to Kanban API.

Returns: {"reflex": "log_triage", "signatures_seen": N, "tasks_created": N}
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger

log = get_logger("log_triage")

_BUILD_LOG = BASE_DIR / ".logs" / "build.ndjson"
_KANBAN_URL = "http://localhost:5050/api/kanban/tasks"
_SEEN_SIGS_FILE = BASE_DIR / ".tmp" / "genesis" / "log_triage_seen.json"
_TAIL_LINES = 500


def _read_ndjson_tail(path: Path, lines: int = _TAIL_LINES) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        all_lines = path.read_text(encoding="utf-8").splitlines()
        events = []
        for raw in all_lines[-lines:]:
            raw = raw.strip()
            if raw:
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        return events
    except OSError:
        return []


def _extract_signatures(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by (component, message_hash[:8])."""
    seen: set = set()
    sigs: List[Dict[str, Any]] = []
    for e in events:
        is_failure = (
            e.get("level") == "ERROR"
            or int(e.get("failed", 0)) > 0
            or int(e.get("returncode", 0)) != 0
        )
        if not is_failure:
            continue
        msg = e.get("message", e.get("event_type", "unknown"))
        comp = e.get("component", "unknown")
        sig_hash = hashlib.sha256(msg.encode()).hexdigest()[:8]
        key = f"{comp}:{sig_hash}"
        if key not in seen:
            seen.add(key)
            sigs.append({**e, "_sig_key": key})
    return sigs


def _load_seen() -> set:
    try:
        if _SEEN_SIGS_FILE.exists():
            data = json.loads(_SEEN_SIGS_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen", []))
    except Exception:
        pass
    return set()


def _save_seen(seen: set) -> None:
    try:
        _SEEN_SIGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SEEN_SIGS_FILE.write_text(
            json.dumps({"seen": list(seen), "updated_at": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("Could not persist seen-sigs: %s", exc)


def _create_task(sig: Dict[str, Any]) -> bool:
    try:
        import urllib.request
        comp = sig.get("component", "unknown")
        msg = sig.get("message", sig.get("event_type", "unknown"))
        event_type = sig.get("event_type", "")
        failed = sig.get("failed", "")
        failures = sig.get("failures", [])

        title = f"[LOG-TRIAGE] {comp}: {msg[:80]}"
        description_lines = [
            f"Auto-created by Genesis log_triage reflex at {datetime.now(timezone.utc).isoformat()}",
            f"Component: {comp}",
            f"Event type: {event_type}",
            f"Failed count: {failed}",
            "",
            "Failure details:",
        ]
        for f in failures[:10]:
            description_lines.append(f"  - {f.get('test', '')} — {f.get('error', '')}")
        description_lines += ["", "Raw event:", json.dumps(sig, indent=2, default=str)]

        payload = json.dumps({
            "title": title,
            "task_type": "bug",
            "priority": "high",
            "status": "backlog",
            "description": "\n".join(description_lines),
        }).encode("utf-8")

        req = urllib.request.Request(
            _KANBAN_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — URL is hardcoded internal constant
            return resp.status in (200, 201)
    except Exception as exc:
        log.error("Failed to create Kanban task: %s", exc)
        return False


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute one log-triage cycle."""
    log_path = Path(config.get("build_log", str(_BUILD_LOG)))
    tail = int(config.get("tail_lines", _TAIL_LINES))

    events = _read_ndjson_tail(log_path, lines=tail)
    sigs = _extract_signatures(events)

    seen = _load_seen()
    new_sigs = [s for s in sigs if s["_sig_key"] not in seen]

    created = 0
    for sig in new_sigs:
        if _create_task(sig):
            seen.add(sig["_sig_key"])
            created += 1
            log.info("Created Kanban task for sig %s", sig["_sig_key"])

    _save_seen(seen)

    result = {
        "success": True,  # completing the scan cycle without error is success
        "metric_value": float(created),  # tasks_created is the tracked metric
        "reflex": "log_triage",
        "events_scanned": len(events),
        "signatures_seen": len(sigs),
        "new_signatures": len(new_sigs),
        "tasks_created": created,
        "details": {"status": "no_changes" if created == 0 else "tasks_created", "tasks_created": created},
    }
    log.info("log_triage cycle complete", extra={"extra": result})
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}, None), indent=2))