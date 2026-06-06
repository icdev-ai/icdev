#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Log-Triage Reflex — autonomous log → Kanban remediation (LOG-06).

Two ingestion paths, one shared anomaly scorer + (component, message_hash[:8])
dedup:

  1. Build log — reads .logs/build.ndjson tail and creates task_type='bug' cards
     for each unseen build-failure signature.
  2. centralized_logs autofix bridge (eqo-log-05) — reads ERROR/CRITICAL events
     from the append-only centralized_logs sink (migration 181, eqo-log-01)
     across ALL components via the RLS-aware query_logs (eqo-log-04), and opens
     one task_type='fix' card per NEW anomaly signature. Each card carries the
     offending component, a log excerpt, and a centralized_logs query to
     reproduce, so the autonomous pipeline can troubleshoot/autofix.

Each path keeps its own persistent seen-signature store so it never re-alerts;
the Kanban scheduler then dispatches the cards for automated repair.

Anomaly detection replaces hardcoded level/failed/returncode thresholds with an
LLM scoring pass (claude-haiku) that assigns a continuous [0,1] severity score
and derives task priority dynamically. Rule-based fallback activates when the
LLM is unavailable.

Configurable via genesis_config.yaml reflexes.log_triage:
  tail_lines:        lines to read per cycle (default 500)
  anomaly_threshold: minimum score to treat an event as a failure (default 0.4)
  score_batch_size:  events per LLM call (default 20)

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
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger

log = get_logger("log_triage")

_BUILD_LOG = BASE_DIR / ".logs" / "build.ndjson"
_KANBAN_URL = "http://localhost:5050/api/kanban/tasks"
_SEEN_SIGS_FILE = BASE_DIR / ".tmp" / "genesis" / "log_triage_seen.json"

# --- centralized_logs autofix bridge (eqo-log-05) ---------------------------
# A second, independent signature store so build-log dedup and centralized-log
# dedup never collide. Anomalies pulled from the centralized_logs sink (migration
# 181, eqo-log-01) across ALL components become task_type='fix' Kanban cards.
_CLOG_SEEN_SIGS_FILE = BASE_DIR / ".tmp" / "genesis" / "log_triage_centralized_seen.json"
_CLOG_LEVELS = ("ERROR", "CRITICAL")  # levels treated as candidate anomalies
_CLOG_LIMIT = 500                      # max rows pulled per level per cycle

# Defaults — all overridable from genesis_config.yaml or the config dict passed to run()
_TAIL_LINES = 500
_ANOMALY_THRESHOLD = 0.4   # events below this score are not treated as failures
_SCORE_BATCH_SIZE = 20     # max events per single LLM call


_ANOMALY_SYSTEM_PROMPT = """You are a build-log anomaly detector for a software platform.
Given a JSON array of log event summaries, classify each for anomaly severity.

Return a JSON array with one object per input event:
  {"index": <int>, "is_anomaly": <bool>, "score": <float 0.0-1.0>, "priority": "<critical|high|medium|low>", "reason": "<10 words max>"}

Priority / score guide:
  critical (0.85-1.0): system crash, data corruption, cascading failure, security violation
  high     (0.65-0.85): test failure, import error, unhandled exception, build break
  medium   (0.40-0.65): degraded state warning, retryable error, config issue
  low      (0.00-0.40): normal retry, informational, expected transient

Return ONLY the JSON array. No markdown, no commentary."""


class AnomalyDetector:
    """LLM-based anomaly scorer for build-log events.

    Sends batches of candidate events to the LLM and receives a continuous
    severity score per event. Falls back to deterministic rules when the LLM
    is unavailable.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._threshold = float(config.get("anomaly_threshold", _ANOMALY_THRESHOLD))
        self._batch_size = int(config.get("score_batch_size", _SCORE_BATCH_SIZE))
        self._router: Optional[Any] = None
        # _disable_llm forces the deterministic rule-based path (no LLMRouter
        # construction). Used by the no-arg _extract_signatures default and by
        # tests that must not touch the LLM/DB.
        if config.get("_disable_llm"):
            return
        try:
            from tools.llm.router import LLMRouter
            self._router = LLMRouter()
        except Exception as exc:
            log.debug("LLMRouter unavailable, using rule-based anomaly fallback: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Score events in place; returns events annotated with anomaly fields."""
        if not events:
            return events
        if self._router is None:
            return self._fallback_score(events)
        try:
            return self._llm_score(events)
        except Exception as exc:
            log.warning("LLM anomaly scoring failed, using rule-based fallback: %s", exc)
            return self._fallback_score(events)

    def is_anomaly(self, event: Dict[str, Any]) -> bool:
        return bool(event.get("_is_anomaly", False))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _llm_score(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        from tools.llm.provider import LLMRequest

        score_map: Dict[int, Dict[str, Any]] = {}

        for batch_start in range(0, len(events), self._batch_size):
            batch = events[batch_start: batch_start + self._batch_size]
            summaries = [
                {
                    "index": batch_start + i,
                    "level": e.get("level", ""),
                    "component": e.get("component", ""),
                    "event_type": e.get("event_type", ""),
                    "message": str(e.get("message", ""))[:300],
                    "failed": e.get("failed", 0),
                    "returncode": e.get("returncode", 0),
                }
                for i, e in enumerate(batch)
            ]
            request = LLMRequest(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Score these {len(summaries)} log events for anomaly:\n"
                        + json.dumps(summaries)
                    ),
                }],
                system_prompt=_ANOMALY_SYSTEM_PROMPT,
                agent_id="log_triage_anomaly",
                classification="CUI",
                max_tokens=1024,
                effort="low",
                skip_injection_scan=True,
            )
            response = self._router.invoke("anomaly_detection", request)
            batch_scores = json.loads(response.content)
            for s in batch_scores:
                score_map[s["index"]] = s

        for i, e in enumerate(events):
            sc = score_map.get(i, {})
            score = float(sc.get("score", 0.0))
            e["_anomaly_score"] = score
            e["_anomaly_priority"] = sc.get("priority", "medium")
            e["_is_anomaly"] = bool(sc.get("is_anomaly", False)) and score >= self._threshold

        return events

    def _fallback_score(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deterministic rule-based scoring — secondary path only."""
        for e in events:
            is_error = e.get("level") == "ERROR"
            has_failed = int(e.get("failed", 0)) > 0
            bad_rc = int(e.get("returncode", 0)) != 0

            if is_error and has_failed:
                score, priority = 0.85, "high"
            elif is_error or has_failed:
                score, priority = 0.65, "high"
            elif bad_rc:
                score, priority = 0.55, "medium"
            else:
                score, priority = 0.0, "low"

            e["_anomaly_score"] = score
            e["_anomaly_priority"] = priority
            e["_is_anomaly"] = score >= self._threshold

        return events


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


def _extract_signatures(
    events: List[Dict[str, Any]], detector: Optional[AnomalyDetector] = None
) -> List[Dict[str, Any]]:
    """Score all events and deduplicate anomalies by (component, message_hash[:8]).

    When ``detector`` is omitted a deterministic rule-based detector is used
    (no LLM/DB access), so callers and tests can extract signatures cheaply.
    """
    if detector is None:
        detector = AnomalyDetector({"_disable_llm": True})
    scored = detector.score(events)
    seen: set = set()
    sigs: List[Dict[str, Any]] = []
    for e in scored:
        if not detector.is_anomaly(e):
            continue
        msg = e.get("message", e.get("event_type", "unknown"))
        comp = e.get("component", "unknown")
        sig_hash = hashlib.sha256(msg.encode()).hexdigest()[:8]
        key = f"{comp}:{sig_hash}"
        if key not in seen:
            seen.add(key)
            sigs.append({**e, "_sig_key": key})
    return sigs


def _load_seen(path: Optional[Path] = None) -> set:
    store = path if path is not None else _SEEN_SIGS_FILE
    try:
        if store.exists():
            data = json.loads(store.read_text(encoding="utf-8"))
            return set(data.get("seen", []))
    except Exception:
        pass
    return set()


def _save_seen(seen: set, path: Optional[Path] = None) -> None:
    store = path if path is not None else _SEEN_SIGS_FILE
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(
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
        priority = sig.get("_anomaly_priority", "high")
        score = sig.get("_anomaly_score", 0.0)

        title = f"[LOG-TRIAGE] {comp}: {msg[:80]}"
        description_lines = [
            f"Auto-created by Genesis log_triage reflex at {datetime.now(timezone.utc).isoformat()}",
            f"Component: {comp}",
            f"Event type: {event_type}",
            f"Failed count: {failed}",
            f"Anomaly score: {score:.3f} | Priority: {priority}",
            "",
            "Failure details:",
        ]
        for f in failures[:10]:
            description_lines.append(f"  - {f.get('test', '')} — {f.get('error', '')}")
        description_lines += ["", "Raw event:", json.dumps(sig, indent=2, default=str)]

        payload = json.dumps({
            "title": title,
            "task_type": "bug",
            "priority": priority,
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


# ---------------------------------------------------------------------------
# Centralized-logs autofix bridge (eqo-log-05)
# ---------------------------------------------------------------------------

def _read_centralized_logs(
    levels=_CLOG_LEVELS,
    since: Optional[str] = None,
    limit: int = _CLOG_LIMIT,
    query_fn: Any = None,
) -> List[Dict[str, Any]]:
    """Pull ERROR/CRITICAL rows from centralized_logs across ALL components.

    Returns event dicts shaped like build-log events (level/component/message)
    so they flow through the same AnomalyDetector + signature dedup. Reads via
    the RLS-aware ``query_logs`` (eqo-log-04); ``query_fn`` is injectable for
    tests. Degrades to ``[]`` when the table is absent or the read fails.
    """
    if query_fn is None:
        from tools.logging.log_query import query_logs as query_fn  # noqa: PLC0415

    events: List[Dict[str, Any]] = []
    for level in levels:
        try:
            rows = query_fn(level=level, since=since, limit=limit)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("centralized_logs read failed for level %s: %s", level, exc)
            rows = []
        for row in rows or []:
            events.append({
                "ts": row.get("ts"),
                "level": row.get("level", level),
                "component": row.get("component", "unknown"),
                "message": row.get("message", ""),
                "event_type": "centralized_log",
                "trace_id": row.get("trace_id"),
                "session_id": row.get("session_id"),
                "_source": "centralized_logs",
                "_log_id": row.get("id"),
            })
    return events


def _repro_query(comp: str, level: str, msg: str) -> str:
    """Build a copy-pasteable centralized_logs query that reproduces the row."""
    needle = str(msg)[:40].replace('"', "").replace("\n", " ").strip()
    return (
        f'python tools/logging/log_query.py --component "{comp}" '
        f'--level {level} --contains "{needle}" --json'
    )


def _create_fix_card(sig: Dict[str, Any]) -> bool:
    """Open a task_type='fix' Kanban card for one centralized-log anomaly.

    Includes the offending component, a log excerpt, and a centralized_logs
    query to reproduce so the autonomous pipeline can troubleshoot/autofix.
    """
    try:
        import urllib.request
        comp = sig.get("component", "unknown")
        msg = str(sig.get("message", sig.get("event_type", "unknown")))
        level = sig.get("level", "ERROR")
        priority = sig.get("_anomaly_priority", "high")
        score = sig.get("_anomaly_score", 0.0)
        trace_id = sig.get("trace_id") or "-"
        session_id = sig.get("session_id") or "-"
        excerpt = msg[:500]

        title = f"[LOG-AUTOFIX] {comp}: {msg[:80]}"
        description_lines = [
            "Auto-created by Genesis log_triage reflex — centralized_logs autofix "
            f"bridge (eqo-log-05) at {datetime.now(timezone.utc).isoformat()}",
            f"Offending component: {comp}",
            f"Level: {level}",
            f"Anomaly score: {score:.3f} | Priority: {priority}",
            f"trace_id: {trace_id} | session_id: {session_id}",
            "",
            "Log excerpt:",
            "```",
            excerpt,
            "```",
            "",
            "Reproduce via centralized_logs query:",
            "```",
            _repro_query(comp, level, msg),
            "```",
        ]

        payload = json.dumps({
            "title": title,
            "task_type": "fix",
            "priority": priority,
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
        log.error("Failed to create Kanban fix card: %s", exc)
        return False


def run_centralized(
    config: Dict[str, Any],
    *,
    detector: Optional[AnomalyDetector] = None,
    query_fn: Any = None,
    seen_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Triage ERROR/CRITICAL events from centralized_logs into fix cards.

    Reads across ALL components, scores+dedups by (component, message_hash[:8])
    via the shared AnomalyDetector, and opens one task_type='fix' card per NEW
    signature. Dedup is persisted in its own seen-store so it never re-alerts.
    """
    since = config.get("centralized_since")
    limit = int(config.get("centralized_limit", _CLOG_LIMIT))
    store = seen_file if seen_file is not None else _CLOG_SEEN_SIGS_FILE
    if detector is None:
        detector = AnomalyDetector(config)

    events = _read_centralized_logs(since=since, limit=limit, query_fn=query_fn)
    sigs = _extract_signatures(events, detector)

    seen = _load_seen(store)
    new_sigs = [s for s in sigs if s["_sig_key"] not in seen]

    created = 0
    for sig in new_sigs:
        if _create_fix_card(sig):
            seen.add(sig["_sig_key"])
            created += 1
            log.info(
                "Created Kanban fix card for centralized-log sig %s (score=%.3f priority=%s)",
                sig["_sig_key"],
                sig.get("_anomaly_score", 0.0),
                sig.get("_anomaly_priority", "high"),
            )

    _save_seen(seen, store)
    return {
        "events_scanned": len(events),
        "signatures_seen": len(sigs),
        "new_signatures": len(new_sigs),
        "fix_cards_created": created,
    }


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute one log-triage cycle."""
    log_path = Path(config.get("build_log", str(_BUILD_LOG)))
    tail = int(config.get("tail_lines", _TAIL_LINES))

    detector = AnomalyDetector(config)
    events = _read_ndjson_tail(log_path, lines=tail)
    sigs = _extract_signatures(events, detector)

    seen = _load_seen()
    new_sigs = [s for s in sigs if s["_sig_key"] not in seen]

    created = 0
    for sig in new_sigs:
        if _create_task(sig):
            seen.add(sig["_sig_key"])
            created += 1
            log.info(
                "Created Kanban task for sig %s (score=%.3f priority=%s)",
                sig["_sig_key"],
                sig.get("_anomaly_score", 0.0),
                sig.get("_anomaly_priority", "high"),
            )

    _save_seen(seen)

    # Centralized-logs autofix bridge (eqo-log-05): triage ERROR/CRITICAL events
    # from centralized_logs across ALL components into task_type='fix' cards.
    # Isolated so a read/scoring failure here never sinks the build-log path.
    clog: Dict[str, Any] = {
        "events_scanned": 0, "signatures_seen": 0, "new_signatures": 0, "fix_cards_created": 0,
    }
    if config.get("centralized_enabled", True):
        try:
            clog = run_centralized(config, detector=detector)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("centralized_logs triage failed: %s", exc)

    fix_cards = clog["fix_cards_created"]
    total_created = created + fix_cards
    result = {
        "success": True,
        "metric_value": float(total_created),
        "reflex": "log_triage",
        "events_scanned": len(events) + clog["events_scanned"],
        "signatures_seen": len(sigs) + clog["signatures_seen"],
        "new_signatures": len(new_sigs) + clog["new_signatures"],
        "tasks_created": created,
        "fix_cards_created": fix_cards,
        "centralized": clog,
        "details": {
            "status": "no_changes" if total_created == 0 else "tasks_created",
            "tasks_created": created,
            "fix_cards_created": fix_cards,
        },
    }
    log.info("log_triage cycle complete", extra={"extra": result})
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}, None), indent=2))
