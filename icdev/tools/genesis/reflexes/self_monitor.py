#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Self-Monitor Reflex — project internal health into operator alerts.

Wires real self-monitoring onto the ``/monitoring`` dashboard page. The
Internal Awareness Engine already produces rich health snapshots in
``awareness_component_health`` (http_head, module_import, coherence_status,
gap, twin probes). Those tables are analyst-facing; the operator-facing
``/monitoring`` page reads ``alerts`` + ``failure_log`` instead, which no
running process populated — so the page rendered empty.

This reflex is a thin PROJECTION layer (no new health logic):

  1. refresh — re-run the cheap ``http_head`` probe live so "is the app up"
     is current truth every cycle (configurable via ``refresh_probes``).
  2. read    — take the latest snapshot per component node and keep the ones
     whose current status is ``fail``.
  3. project —
       * one AGGREGATED ``alerts`` row per failing category (e.g. "46 tools
         fail to import"), deduped on a stable source signature and
         auto-resolved when the category recovers.
       * one ``failure_log`` row per failing component, deduped against
         existing unresolved identical rows (capped per cycle).

Healing stays the heal reflex's job — this reflex only surfaces signal.

GREEN tier (read analysis + bounded inserts into alerts/failure_log only;
no code mutation). Zero LLM in the hot path; fully deterministic.

CLI (manual / smoke):
    python tools/genesis/reflexes/self_monitor.py --json
    python tools/genesis/reflexes/self_monitor.py --no-refresh --json
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

LOG = get_logger("self_monitor")

try:
    from tools.db.storage import get_connection
except ImportError:  # pragma: no cover - slim env
    get_connection = None  # type: ignore[assignment]

try:
    from tools.awareness.health_prober import run_all as _prober_run_all
except ImportError:  # pragma: no cover - slim env
    _prober_run_all = None  # type: ignore[assignment]

try:
    from tools.monitor.metric_collector import store_snapshot as _store_snapshot
except ImportError:  # pragma: no cover - slim env
    _store_snapshot = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Defaults (overridable from genesis_config.yaml reflex entry)
# ---------------------------------------------------------------------------

# Probes re-run live each cycle for current truth. http_head is cheap (~20
# HEAD requests) and most operationally meaningful ("is the dashboard up").
# Heavier probes (module_import, coherence, gap, twin) are reused from the
# most recent awareness cycle so this reflex stays light on a short cadence.
DEFAULT_REFRESH_PROBES = ["http_head"]

# Per-category minimum failing count before an alert fires.
DEFAULT_MIN_FAIL_TO_ALERT = 1

# Cap failure_log inserts per cycle so a mass regression can't flood the log.
DEFAULT_MAX_FAILURE_ROWS = 200

# Ignore failures whose latest snapshot is older than this. A probe we haven't
# confirmed in this long is not trustworthy as a CURRENT failure (e.g. a node
# that stopped being probed because it was disabled/suppressed, or a deleted
# tool whose last snapshot lingers). 7 days comfortably spans the awareness
# reflex's 3h cadence while ageing out abandoned snapshots.
DEFAULT_MAX_SNAPSHOT_AGE_HOURS = 168

# probe_type → (severity, human label). Severity is constrained by the alerts
# table CHECK to one of: critical | warning | info. Categories not listed
# default to 'warning'.
SEVERITY_MAP: Dict[str, Dict[str, str]] = {
    "module_import":            {"severity": "critical", "label": "tool(s) failing to import"},
    # http_head is informational: a single page returning 4xx/5xx is rarely an
    # outage, and the probe now sources real mounted routes (a true app-down shows
    # up as the dedicated 'dashboard::unreachable' failure instead).
    "http_head":               {"severity": "info",     "label": "dashboard route(s) returning errors"},
    "coherence_status":        {"severity": "warning",  "label": "coherence check(s) failing"},
    "twin_probe":              {"severity": "warning",  "label": "digital twin probe(s) failing"},
    "gap::tool_not_in_manifest": {"severity": "info",   "label": "tool(s) missing from manifest"},
}

# All alerts this reflex owns carry a source prefixed with this token so we can
# dedup + auto-resolve only our own rows without touching alerts from other
# subsystems (vuln_scanner, watchcon, alert_correlator).
SOURCE_PREFIX = "self_monitor"

# Board throughput stall alerts get their OWN source prefix, deliberately not
# under SOURCE_PREFIX: _sync_alerts() auto-resolves every firing 'self_monitor:*'
# alert whose category is absent from the probe results, which would resolve a
# real stall alert on the very next cycle. Separate prefix, separate lifecycle.
STALL_SOURCE_PREFIX = "board_throughput"
STALL_SOURCE = f"{STALL_SOURCE_PREFIX}:done_flatline"

# --- Board throughput stall rule (kax-stall-01) -----------------------------
# Documented defaults only. The live values come from genesis_config.yaml
# (self_monitor.board_throughput) with per-key env overrides below, so an
# operator can retune or kill the rule without a code change.
DEFAULT_STALL_ENABLED = True
DEFAULT_STALL_WINDOW_HOURS = 24.0      # no 'done' in this long ⇒ candidate stall
DEFAULT_STALL_MIN_ACTIVE_TASKS = 1     # ... but only with this much active work
DEFAULT_STALL_COOLDOWN_HOURS = 12.0    # don't re-open the same stall this soon
DEFAULT_STALL_SEVERITY = "critical"    # alerts CHECK: critical | warning | info

# env key → (config key, coercion). Env wins over YAML so an operator can
# silence or retune the rule on a running daemon.
_STALL_ENV_OVERRIDES = {
    "ICDEV_BOARD_STALL_ENABLED": ("enabled", "bool"),
    "ICDEV_BOARD_STALL_WINDOW_HOURS": ("window_hours", "float"),
    "ICDEV_BOARD_STALL_MIN_ACTIVE": ("min_active_tasks", "int"),
    "ICDEV_BOARD_STALL_COOLDOWN_HOURS": ("cooldown_hours", "float"),
    "ICDEV_BOARD_STALL_SEVERITY": ("severity", "str"),
}

# LLM anomaly detection — disabled by default; enable via genesis_config.yaml:
#   anomaly_detection: {enabled: true, llm_enabled: true, baseline_hours: 72}
DEFAULT_ANOMALY_BASELINE_HOURS = 72

# metric_snapshots persistence.
#
# metric_snapshots had two INSERT sites (metric_collector.store_snapshot and
# log_analyzer._record_findings) and zero rows: both are reachable only from
# their own CLI, and both need a metrics backend (Prometheus / ELK) that is not
# deployed here. Meanwhile four reader surfaces — project_status, infra_status,
# the dashboard metrics API, and mcp/core_server — query the table.
#
# This reflex is the one monitoring path that actually runs on a cadence, and it
# already computes the numbers below every cycle before throwing them away into
# a threshold check. Persisting them through the existing store_snapshot writer
# gives the table a live producer without inventing a new surface.
DEFAULT_RECORD_METRICS = True

# metric_snapshots.project_id is NOT NULL (and FK-constrained to projects(id) on
# SQLite), so platform self-telemetry needs a project row. 'icdev-tools-rtm' is
# the platform's own project and the convention already used by
# tools/genesis/reflexes/integrity_monitor.py.
DEFAULT_METRICS_PROJECT_ID = "icdev-tools-rtm"

# Source label written to metric_snapshots.source, so these rows are
# distinguishable from prometheus / log_analyzer rows.
METRICS_SOURCE = "self_monitor"

# Per-category guidance thresholds for the LLM anomaly classifier.
# Configurable via genesis_config.yaml: self_monitor.anomaly_detection.category_thresholds
# Each entry: {guidance: <human-readable hint>, noise_max: <int>, anomalous_min: <int>}
# 'noise_max' and 'anomalous_min' are included in the prompt to aid the LLM;
# 'guidance' is a plain-English description used as a fallback/override hint.
DEFAULT_CATEGORY_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "module_import": {
        "guidance": "any failure (>=1) is critical and anomalous",
        "noise_max": 0,
        "anomalous_min": 1,
    },
    "http_head": {
        "guidance": "1-3 route errors may be noise; 5+ is anomalous",
        "noise_max": 3,
        "anomalous_min": 5,
    },
    "coherence_status": {
        "guidance": "1-2 may be transient; 3+ is anomalous",
        "noise_max": 2,
        "anomalous_min": 3,
    },
    "twin_probe": {
        "guidance": "treat like coherence_status (1-2 transient; 3+ anomalous)",
        "noise_max": 2,
        "anomalous_min": 3,
    },
    "gap::tool_not_in_manifest": {
        "guidance": "informational; <=4 is not anomalous",
        "noise_max": 4,
        "anomalous_min": 5,
    },
}

_ANOMALY_PROMPT_HEADER = (
    "You are a system health anomaly detector for an AI development platform.\n"
    "Given per-category health probe failure counts (current vs recent baseline avg), "
    "determine which categories represent genuine anomalies worth alerting on "
    "versus normal operational noise.\n\n"
    "General guidance (override with baseline data when available):\n"
)

_ANOMALY_PROMPT_FOOTER = (
    "If baseline_avg is provided and current_count is within 1 std dev of it, "
    "it is NOT anomalous.\n\n"
    '{"categories": [{"name": <str>, "is_anomaly": <bool>, "confidence": <float 0-1>}]}\n'
    "Return ONLY that JSON. No markdown, no extra text."
)


def _build_anomaly_system_prompt(
    category_thresholds: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Build the LLM system prompt from configurable per-category thresholds.

    Falls back to DEFAULT_CATEGORY_THRESHOLDS for any missing category.
    """
    thresholds = dict(DEFAULT_CATEGORY_THRESHOLDS)
    if category_thresholds:
        for cat, cfg in category_thresholds.items():
            if cat in thresholds:
                thresholds[cat] = {**thresholds[cat], **cfg}
            else:
                thresholds[cat] = cfg
    lines = []
    for cat, cfg in thresholds.items():
        lines.append(f"- {cat}: {cfg.get('guidance', 'evaluate with baseline data')}")
    guidance_block = "\n".join(lines) + "\n\n"
    return _ANOMALY_PROMPT_HEADER + guidance_block + _ANOMALY_PROMPT_FOOTER


_ANOMALY_SYSTEM_PROMPT = _build_anomaly_system_prompt()


class _AnomalyDetector:
    """LLM-based per-category anomaly classifier with static-threshold fallback.

    Instantiate once per cycle. Gracefully degrades when the LLM router is
    unavailable or the call fails — falls back to ``len(items) >= min_fail``.

    ``category_thresholds`` — optional per-category config from genesis_config.yaml
    (self_monitor.anomaly_detection.category_thresholds). Merged over
    DEFAULT_CATEGORY_THRESHOLDS so operator overrides only need to specify the
    keys they want to change.
    """

    def __init__(
        self,
        category_thresholds: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._system_prompt = _build_anomaly_system_prompt(category_thresholds)
        self._router: Any = None
        self._LLMRequest: Any = None
        try:
            from tools.llm.router import LLMRouter  # type: ignore[import]
            from tools.llm.provider import LLMRequest  # type: ignore[import]
            self._router = LLMRouter()
            self._LLMRequest = LLMRequest
        except Exception as exc:
            LOG.debug("LLMRouter unavailable for anomaly detection; using static threshold: %s", exc)

    def breaching_categories(
        self,
        failures_by_cat: Dict[str, List[Dict[str, Any]]],
        min_fail: int,
        baseline: Optional[Dict[str, float]] = None,
    ) -> Dict[str, int]:
        """Return {category: count} for categories deemed anomalous.

        Falls back to ``len(items) >= min_fail`` when LLM is unavailable.
        """
        current = {cat: len(items) for cat, items in failures_by_cat.items()}
        static = {cat: cnt for cat, cnt in current.items() if cnt >= min_fail}
        if not current:
            return {}
        if self._router is None:
            return static
        try:
            return self._llm_classify(current, static, baseline)
        except Exception as exc:
            LOG.warning("LLM anomaly detection failed; using static threshold: %s", exc)
            return static

    def _llm_classify(
        self,
        current: Dict[str, int],
        static_fallback: Dict[str, int],
        baseline: Optional[Dict[str, float]],
    ) -> Dict[str, int]:
        payload = [
            {
                "name": cat,
                "current_count": cnt,
                **({"baseline_avg": round(baseline[cat], 2)} if baseline and cat in baseline else {}),
            }
            for cat, cnt in current.items()
        ]
        request = self._LLMRequest(
            messages=[{
                "role": "user",
                "content": (
                    f"Evaluate {len(payload)} health probe categor(ies) for anomalies:\n"
                    + json.dumps(payload, ensure_ascii=False)
                ),
            }],
            system_prompt=self._system_prompt,
            agent_id="self_monitor_anomaly",
            classification="CUI",
            max_tokens=256,
            temperature=0.0,
            skip_injection_scan=True,
        )
        response = self._router.invoke("anomaly_detection", request)
        parsed = json.loads(response.content.strip())
        result: Dict[str, int] = {}
        for entry in parsed.get("categories", []):
            name = entry.get("name", "")
            if entry.get("is_anomaly") and name in current:
                result[name] = current[name]
        LOG.debug(
            "self_monitor LLM anomaly: %d/%d categor(ies) flagged",
            len(result), len(current),
        )
        return result


def _get_failure_baseline(conn: Any, baseline_hours: float) -> Dict[str, float]:
    """Rolling average failure count per probe_type over the past N hours.

    Returns empty dict on error — callers degrade to static threshold.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=baseline_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    sql = (
        "SELECT probe_type, COUNT(*) AS cnt "
        "FROM awareness_component_health "
        "WHERE status = 'fail' AND probed_at >= ? "
        "GROUP BY probe_type"
    )
    try:
        rows = conn.execute(sql, (cutoff,)).fetchall()
        cycles = max(1.0, baseline_hours / 3.0)
        out: Dict[str, float] = {}
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {"probe_type": r[0], "cnt": r[1]}
            out[d["probe_type"]] = float(d["cnt"]) / cycles
        return out
    except Exception as exc:
        LOG.debug("failure baseline query failed (non-fatal): %s", exc)
        return {}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Read: latest failing snapshot per component node
# ---------------------------------------------------------------------------


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Best-effort parse of a stored probed_at into an aware UTC datetime."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _latest_failures(conn: Any, max_age_hours: float) -> Dict[str, List[Dict[str, Any]]]:
    """Return current failures grouped by probe_type.

    Takes the most recent snapshot per node_id and keeps only those whose
    current status is 'fail' (not 'warn' — warn is pre-confirmation and not a
    confirmed regression) AND whose snapshot is newer than ``max_age_hours``
    (a stale snapshot is not a trustworthy current failure). Portable across
    SQLite + PostgreSQL (no DISTINCT ON / window functions).
    """
    sql = (
        "SELECT h.node_id, h.probe_type, h.status, h.detail, h.probed_at "
        "FROM awareness_component_health h "
        "JOIN (SELECT node_id, MAX(probed_at) AS mx "
        "      FROM awareness_component_health GROUP BY node_id) m "
        "  ON h.node_id = m.node_id AND h.probed_at = m.mx "
        "WHERE h.status = 'fail'"
    )
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as exc:
        LOG.warning("latest-failures query failed: %s", exc)
        return out
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    stale = 0
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else {
            "node_id": r[0], "probe_type": r[1], "status": r[2], "detail": r[3], "probed_at": r[4],
        }
        ts = _parse_ts(d.get("probed_at"))
        if ts is not None and ts < cutoff:
            stale += 1
            continue  # snapshot too old to assert as a current failure
        detail: Dict[str, Any] = {}
        try:
            detail = json.loads(d.get("detail") or "{}")
        except Exception:
            detail = {}
        cat = d.get("probe_type") or "unknown"
        out.setdefault(cat, []).append({"node_id": d.get("node_id"), "detail": detail})
    if stale:
        LOG.info("self_monitor: ignored %d stale failure snapshot(s) (> %sh old)", stale, max_age_hours)
    return out


# ---------------------------------------------------------------------------
# Project: failure_log
# ---------------------------------------------------------------------------


def _existing_open_failures(conn: Any) -> set:
    """Signatures of currently-unresolved failure_log rows, to avoid duplicates."""
    sigs: set = set()
    try:
        rows = conn.execute(
            "SELECT source, error_type, error_message FROM failure_log WHERE resolved = 0"
        ).fetchall()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {"source": r[0], "error_type": r[1], "error_message": r[2]}
            sigs.add((d.get("source"), d.get("error_type"), d.get("error_message")))
    except Exception as exc:
        LOG.warning("read open failure_log failed: %s", exc)
    return sigs


def _record_failures(conn: Any, failures_by_cat: Dict[str, List[Dict[str, Any]]], cap: int) -> int:
    """Insert one failure_log row per failing component, deduped + capped."""
    existing = _existing_open_failures(conn)
    inserted = 0
    now = _utcnow_iso()
    for cat, items in failures_by_cat.items():
        source = f"{SOURCE_PREFIX}:{cat}"
        for item in items:
            if inserted >= cap:
                LOG.info("failure_log cap (%d) reached; remaining failures not logged this cycle", cap)
                return inserted
            node_id = item.get("node_id") or "unknown"
            detail = item.get("detail") or {}
            error_type = cat
            reason = str(
                detail.get("error")
                or detail.get("message")
                or detail.get("route")
                or detail.get("file_path")
                or "failing probe"
            )[:300]
            # Prefix with the component id so each failing component is a
            # distinct, informative row (otherwise a shared generic message
            # like "file not found" collapses many failures into one).
            error_message = f"{node_id} — {reason}"[:500]
            sig = (source, error_type, error_message)
            if sig in existing:
                continue
            context = json.dumps({"node_id": node_id, **detail}, ensure_ascii=False)[:2000]
            try:
                conn.execute(
                    "INSERT INTO failure_log "
                    "(project_id, source, error_type, error_message, context, resolved, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, 0, %s)",
                    (None, source, error_type, error_message, context, now),
                )
                conn.commit()
                existing.add(sig)
                inserted += 1
            except Exception as exc:
                LOG.warning("failure_log insert failed (%s): %s", node_id, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
    return inserted


# ---------------------------------------------------------------------------
# Anomaly detection: adaptive min_fail_to_alert
# ---------------------------------------------------------------------------


def _adaptive_min_fail(conn: Any, ad_cfg: Dict[str, Any], static_fallback: int) -> int:
    """Compute adaptive min_fail_to_alert from historical failure_log data.

    Groups daily failure_log insert counts for the configured history window,
    then returns mean + sigma*std bounded by adaptive_bounds. Falls back to
    static_fallback when history is insufficient or anomaly detection is off.
    Cross-DB: date grouping is done in Python so no DATE() dialect divergence.
    """
    from collections import Counter  # stdlib; import here to keep top-level clean

    min_samples = int(ad_cfg.get("min_samples", 10))
    sigma = float(ad_cfg.get("sigma_multiplier", 1.0))
    history_days = int(ad_cfg.get("history_days", 30))
    bounds = ad_cfg.get("adaptive_bounds", {})
    floor_val = int(bounds.get("min_fail_floor", 1))
    ceiling_val = int(bounds.get("min_fail_ceiling", 20))

    cutoff = (datetime.now(timezone.utc) - timedelta(days=history_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = conn.execute(
            "SELECT created_at FROM failure_log WHERE source LIKE %s AND created_at > %s",
            (f"{SOURCE_PREFIX}:%", cutoff),
        ).fetchall()
    except Exception as exc:
        LOG.debug("adaptive_min_fail: query failed (%s); using static fallback", exc)
        return static_fallback

    day_counts: Counter = Counter()
    for r in rows:
        raw = r[0] if not hasattr(r, "keys") else dict(r).get("created_at")
        ts = _parse_ts(raw)
        if ts:
            day_counts[ts.date()] += 1

    n = len(day_counts)
    if n < min_samples:
        LOG.debug(
            "adaptive_min_fail: %d day(s) of data (need %d); using static fallback",
            n, min_samples,
        )
        return static_fallback

    vals = list(day_counts.values())
    mean = sum(vals) / n
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5
    adaptive = int(mean + sigma * std)
    bounded = max(floor_val, min(ceiling_val, adaptive))
    LOG.debug(
        "adaptive_min_fail: n=%d mean=%.1f std=%.1f raw_adaptive=%d bounded=%d",
        n, mean, std, adaptive, bounded,
    )
    return bounded


# ---------------------------------------------------------------------------
# Project: alerts (aggregated, deduped, auto-resolved)
# ---------------------------------------------------------------------------


def _firing_self_alerts(conn: Any) -> Dict[str, Dict[str, Any]]:
    """Map of source → firing alert row, for alerts this reflex owns."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = conn.execute(
            "SELECT id, source, title, severity FROM alerts "
            "WHERE status = 'firing' AND source LIKE %s",
            (f"{SOURCE_PREFIX}:%",),
        ).fetchall()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {"id": r[0], "source": r[1], "title": r[2], "severity": r[3]}
            out[d["source"]] = d
    except Exception as exc:
        LOG.warning("read firing alerts failed: %s", exc)
    return out


def _sync_alerts(
    conn: Any,
    failures_by_cat: Dict[str, List[Dict[str, Any]]],
    min_fail: int,
    breaching_override: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """One aggregated alert per failing category; auto-resolve recovered ones.

    ``breaching_override`` — when provided (from LLM anomaly detector) it
    replaces the static ``len(items) >= min_fail`` threshold check so the
    LLM's per-category anomaly decision drives which alerts fire.

    Returns counts: {"opened": n, "resolved": n, "firing": n}.
    """
    now = _utcnow_iso()
    firing = _firing_self_alerts(conn)
    opened = 0
    resolved = 0
    updated = 0

    # Categories that currently breach: prefer LLM-classified set when provided.
    if breaching_override is not None:
        breaching: Dict[str, int] = breaching_override
    else:
        breaching = {
            cat: len(items) for cat, items in failures_by_cat.items() if len(items) >= min_fail
        }

    # 1) Open (or refresh) one aggregated alert per breaching category.
    for cat, count in breaching.items():
        source = f"{SOURCE_PREFIX}:{cat}"
        meta = SEVERITY_MAP.get(cat, {"severity": "warning", "label": f"{cat} probe(s) failing"})
        title = f"{count} {meta['label']}"
        description = (
            f"Self-monitor detected {count} component(s) currently failing the "
            f"'{cat}' probe (latest health snapshot). Source: Internal Awareness Engine."
        )
        if source in firing:
            # Already firing — keep the count accurate as failures rise/fall.
            if firing[source].get("title") == title:
                continue  # unchanged, nothing to do
            try:
                conn.execute(
                    "UPDATE alerts SET title = %s, description = %s, severity = %s WHERE id = %s",
                    (title, description, meta["severity"], firing[source]["id"]),
                )
                conn.commit()
                updated += 1
            except Exception as exc:
                LOG.warning("alert refresh failed (%s): %s", cat, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
            continue
        try:
            conn.execute(
                "INSERT INTO alerts "
                "(project_id, severity, source, title, description, status, auto_healed, created_at) "
                "VALUES (%s, %s, %s, %s, %s, 'firing', %s, %s)",
                (None, meta["severity"], source, title, description, False, now),
            )
            conn.commit()
            opened += 1
        except Exception as exc:
            LOG.warning("alert insert failed (%s): %s", cat, exc)
            try:
                conn.rollback()
            except Exception:
                pass

    # 2) Auto-resolve firing alerts whose category no longer breaches.
    for source, row in firing.items():
        cat = source.split(":", 1)[1] if ":" in source else source
        if cat in breaching:
            continue
        try:
            conn.execute(
                "UPDATE alerts SET status = 'resolved', resolved_at = %s WHERE id = %s",
                (now, row["id"]),
            )
            conn.commit()
            resolved += 1
        except Exception as exc:
            LOG.warning("alert resolve failed (%s): %s", source, exc)
            try:
                conn.rollback()
            except Exception:
                pass

    return {"opened": opened, "updated": updated, "resolved": resolved, "firing": len(breaching)}


# ---------------------------------------------------------------------------
# Rule: board throughput stall (kax-stall-01)
# ---------------------------------------------------------------------------


def _stall_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve board_throughput settings: defaults ← genesis_config.yaml ← env."""
    cfg: Dict[str, Any] = {
        "enabled": DEFAULT_STALL_ENABLED,
        "window_hours": DEFAULT_STALL_WINDOW_HOURS,
        "min_active_tasks": DEFAULT_STALL_MIN_ACTIVE_TASKS,
        "cooldown_hours": DEFAULT_STALL_COOLDOWN_HOURS,
        "severity": DEFAULT_STALL_SEVERITY,
    }
    cfg.update(config.get("board_throughput") or {})
    for env_key, (cfg_key, kind) in _STALL_ENV_OVERRIDES.items():
        raw = os.getenv(env_key)
        if raw is None or raw.strip() == "":
            continue
        try:
            if kind == "bool":
                cfg[cfg_key] = raw.strip().lower() in ("1", "true", "yes", "on")
            elif kind == "float":
                cfg[cfg_key] = float(raw)
            elif kind == "int":
                cfg[cfg_key] = int(raw)
            else:
                cfg[cfg_key] = raw.strip()
        except ValueError:
            LOG.warning("ignoring malformed %s=%r", env_key, raw)
    return cfg


def _last_stall_alert(conn: Any) -> Optional[Dict[str, Any]]:
    """Most recent board-throughput alert row, whatever its status."""
    try:
        rows = conn.execute(
            "SELECT id, status, title, created_at FROM alerts "
            "WHERE source = %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (STALL_SOURCE,),
        ).fetchall()
    except Exception as exc:
        LOG.warning("read board-throughput alerts failed: %s", exc)
        return None
    if not rows:
        return None
    r = rows[0]
    return dict(r) if hasattr(r, "keys") else {
        "id": r[0], "status": r[1], "title": r[2], "created_at": r[3],
    }


def _check_board_throughput(conn: Any, config: Dict[str, Any]) -> Dict[str, Any]:
    """Open / refresh / resolve the single board-throughput stall alert.

    Fires when nothing reached 'done' inside the window WHILE tasks were
    scheduled or in_progress. Exactly one alert row exists per stall episode:

      * already firing  → refresh the text in place, never a second row
      * recently opened → suppressed by cooldown, even if a human resolved it
      * board recovers  → the firing alert is resolved
    """
    cfg = _stall_config(config)
    if not cfg.get("enabled", True):
        return {"enabled": False, "action": "disabled"}

    try:
        from tools.kanban.metrics import throughput_stall_check
    except ImportError as exc:  # pragma: no cover - slim env
        LOG.warning("board throughput check unavailable: %s", exc)
        return {"enabled": True, "action": "unavailable", "error": str(exc)[:200]}

    try:
        signal = throughput_stall_check(
            conn=conn,
            window_hours=float(cfg["window_hours"]),
            min_active_tasks=int(cfg["min_active_tasks"]),
        )
    except Exception as exc:
        LOG.warning("board throughput check failed: %s", exc)
        return {"enabled": True, "action": "error", "error": str(exc)[:200]}

    now = _utcnow_iso()
    latest = _last_stall_alert(conn)
    result: Dict[str, Any] = {"enabled": True, "signal": signal}

    # --- Recovered: resolve the open alert, if any -------------------------
    if not signal["stalled"]:
        if latest and latest.get("status") == "firing":
            try:
                conn.execute(
                    "UPDATE alerts SET status = 'resolved', resolved_at = %s WHERE id = %s",
                    (now, latest["id"]),
                )
                conn.commit()
                result["action"] = "resolved"
                LOG.info("board throughput recovered (%s); stall alert resolved", signal["reason"])
                return result
            except Exception as exc:
                LOG.warning("stall alert resolve failed: %s", exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
                result["action"] = "resolve_failed"
                return result
        result["action"] = "healthy"
        return result

    # --- Stalled -----------------------------------------------------------
    hours = signal.get("hours_since_last_done")
    since = f"{hours:.0f}h" if isinstance(hours, (int, float)) else "ever"
    title = (
        f"Board throughput stalled — nothing done in {signal['window_hours']:.0f}h "
        f"with {signal['active_tasks']} task(s) active"
    )
    description = (
        f"No kanban task reached 'done' in the last {signal['window_hours']:.0f} hours while "
        f"{signal['active_tasks']} task(s) sat in {list(signal['active_by_status'].keys()) or 'scheduled/in_progress'}. "
        f"Last completion: {signal.get('last_done_at') or 'none on record'} ({since} ago). "
        f"Active breakdown: {json.dumps(signal['active_by_status'], ensure_ascii=False)}. "
        "The scheduler, the PR watcher, or the executors are not moving work through."
    )

    # Already firing → refresh in place. This is the no-duplicate guarantee.
    if latest and latest.get("status") == "firing":
        if latest.get("title") == title:
            result["action"] = "unchanged"
            return result
        try:
            conn.execute(
                "UPDATE alerts SET title = %s, description = %s, severity = %s WHERE id = %s",
                (title, description, cfg["severity"], latest["id"]),
            )
            conn.commit()
            result["action"] = "updated"
        except Exception as exc:
            LOG.warning("stall alert refresh failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            result["action"] = "update_failed"
        return result

    # Cooldown: a human may have acknowledged/resolved the alert while the stall
    # continues. Re-opening on the next cycle would be exactly the every-cycle
    # noise the rule is supposed to avoid.
    cooldown_hours = float(cfg["cooldown_hours"])
    if latest and cooldown_hours > 0:
        opened = _parse_ts(latest.get("created_at"))
        if opened is not None:
            age_h = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
            if age_h < cooldown_hours:
                LOG.info(
                    "board stall persists but last alert is %.1fh old (< %.1fh cooldown); suppressed",
                    age_h, cooldown_hours,
                )
                result["action"] = "cooldown"
                result["cooldown_age_hours"] = round(age_h, 2)
                return result

    try:
        conn.execute(
            "INSERT INTO alerts "
            "(project_id, severity, source, title, description, status, auto_healed, created_at) "
            "VALUES (%s, %s, %s, %s, %s, 'firing', %s, %s)",
            (None, cfg["severity"], STALL_SOURCE, title, description, False, now),
        )
        conn.commit()
        result["action"] = "opened"
        LOG.warning("BOARD THROUGHPUT STALLED: %s", title)
    except Exception as exc:
        LOG.warning("stall alert insert failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        result["action"] = "open_failed"
    return result


# ---------------------------------------------------------------------------
# Refresh: re-run cheap probes for current truth
# ---------------------------------------------------------------------------


def _refresh_probes(probe_types: List[str]) -> Dict[str, Any]:
    """Re-run the configured probes so the latest snapshot reflects 'now'."""
    if not probe_types or _prober_run_all is None:
        return {"skipped": True, "reason": "no probes or prober unavailable"}
    summary: Dict[str, Any] = {}
    for pt in probe_types:
        try:
            summary[pt] = _prober_run_all(probe_types=[pt])
        except Exception as exc:
            LOG.warning("probe refresh failed (%s): %s", pt, exc)
            summary[pt] = {"error": str(exc)[:200]}
    return summary


# ---------------------------------------------------------------------------
# Reflex entry point
# ---------------------------------------------------------------------------


def _record_metrics(
    project_id: str,
    total_failing: int,
    alert_counts: Dict[str, int],
    failures_logged: int,
    elapsed_ms: int,
) -> int:
    """Persist this cycle's numbers to metric_snapshots. Returns rows written.

    Never raises: losing a metrics row must not fail the reflex, whose primary
    job is alerting. The count is returned so the caller can report an honest
    0 instead of implying a write that did not happen.
    """
    if _store_snapshot is None:
        return 0

    metrics = {
        "self_monitor_failing_components": float(total_failing),
        "self_monitor_alerts_firing": float(alert_counts.get("firing", 0)),
        "self_monitor_alerts_opened": float(alert_counts.get("opened", 0)),
        "self_monitor_alerts_resolved": float(alert_counts.get("resolved", 0)),
        "self_monitor_failures_logged": float(failures_logged),
        "self_monitor_cycle_ms": float(elapsed_ms),
    }
    try:
        return int(_store_snapshot(project_id, metrics, source=METRICS_SOURCE))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("self_monitor: metric_snapshots write failed: %s", exc)
        return 0


def run(config: Optional[Dict[str, Any]] = None, trust: Any = None) -> Dict[str, Any]:
    """Execute one self-monitor cycle. Daemon calls this on the reflex cadence."""
    config = config or {}
    refresh_probes = config.get("refresh_probes", DEFAULT_REFRESH_PROBES)
    min_fail = int(config.get("min_fail_to_alert", DEFAULT_MIN_FAIL_TO_ALERT))
    cap = int(config.get("max_failure_rows", DEFAULT_MAX_FAILURE_ROWS))
    max_age_hours = float(config.get("max_snapshot_age_hours", DEFAULT_MAX_SNAPSHOT_AGE_HOURS))

    start = time.time()

    if get_connection is None:
        return {"success": False, "metric_value": 0.0, "details": {"error": "get_connection unavailable"}}

    # 1) refresh cheap probes (best-effort; reuse snapshots if it fails)
    refresh_summary = _refresh_probes(list(refresh_probes))

    conn = get_connection()
    try:
        # Background task: no request/tenant context available.
        conn.set_security_context(None)  # rls-bypass: background reflex, no user session
    except Exception:
        pass

    # 2) Anomaly detection: adaptive statistical threshold + optional LLM classifier
    ad_cfg = config.get("anomaly_detection", {})
    llm_breaching: Optional[Dict[str, int]] = None
    if ad_cfg.get("enabled", False):
        # Statistical: shift min_fail based on historical daily failure counts
        min_fail = _adaptive_min_fail(conn, ad_cfg, min_fail)
        # LLM: per-category contextual anomaly classification (overrides static check)
        if ad_cfg.get("llm_enabled", False):
            baseline_hours = float(ad_cfg.get("baseline_hours", DEFAULT_ANOMALY_BASELINE_HOURS))
            baseline = _get_failure_baseline(conn, baseline_hours)
            category_thresholds = ad_cfg.get("category_thresholds") or {}
            detector = _AnomalyDetector(category_thresholds=category_thresholds or None)
            # Detector needs failures_by_cat — fetch it early for the baseline call
            _early_failures = _latest_failures(conn, max_age_hours)
            llm_breaching = detector.breaching_categories(_early_failures, min_fail, baseline)

    try:
        failures_by_cat = (
            _early_failures  # type: ignore[possibly-undefined]
            if llm_breaching is not None
            else _latest_failures(conn, max_age_hours)
        )
        total_failing = sum(len(v) for v in failures_by_cat.values())
        failures_logged = _record_failures(conn, failures_by_cat, cap)
        alert_counts = _sync_alerts(conn, failures_by_cat, min_fail, breaching_override=llm_breaching)
        # 3) Board throughput: is the pipeline actually completing work?
        board_throughput = _check_board_throughput(conn, config)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    elapsed_ms = int((time.time() - start) * 1000)
    by_category = {cat: len(items) for cat, items in failures_by_cat.items()}

    metrics_recorded = 0
    if config.get("record_metrics", DEFAULT_RECORD_METRICS):
        metrics_recorded = _record_metrics(
            config.get("metrics_project_id") or DEFAULT_METRICS_PROJECT_ID,
            total_failing,
            alert_counts,
            failures_logged,
            elapsed_ms,
        )

    LOG.info(
        "self_monitor: %d failing component(s) across %d categor(ies); "
        "alerts opened=%d updated=%d resolved=%d firing=%d; failure_log +%d; "
        "metric_snapshots +%d; board_throughput=%s; %dms",
        total_failing, len(failures_by_cat), alert_counts["opened"], alert_counts["updated"],
        alert_counts["resolved"], alert_counts["firing"], failures_logged, metrics_recorded,
        board_throughput.get("action"), elapsed_ms,
    )

    # A live stall counts toward the reflex's firing-alert metric so the Genesis
    # success_metric and the /monitoring header both reflect it.
    stall_firing = 1 if board_throughput.get("action") in ("opened", "updated", "unchanged") else 0

    return {
        "success": True,
        # success_metric: alerts currently firing (gte 0 ⇒ always passes; >0 is signal)
        "metric_value": float(alert_counts["firing"] + stall_firing),
        "details": {
            "board_throughput": board_throughput,
            "total_failing_components": total_failing,
            "by_category": by_category,
            "alerts_opened": alert_counts["opened"],
            "alerts_updated": alert_counts["updated"],
            "alerts_resolved": alert_counts["resolved"],
            "alerts_firing": alert_counts["firing"],
            "failures_logged": failures_logged,
            "metrics_recorded": metrics_recorded,
            "min_fail_to_alert": min_fail,
            "adaptive_threshold": ad_cfg.get("enabled", False),
            "llm_anomaly_detection": ad_cfg.get("llm_enabled", False),
            "refresh": refresh_summary,
            "elapsed_ms": elapsed_ms,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="self_monitor",
        description="Project Internal Awareness health into operator alerts + failure_log",
    )
    parser.add_argument(
        "--no-refresh", action="store_true",
        help="Skip live probe refresh; project from existing snapshots only",
    )
    parser.add_argument("--min-fail", type=int, default=DEFAULT_MIN_FAIL_TO_ALERT,
                        help="Per-category minimum failing count before an alert fires")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    cfg: Dict[str, Any] = {"min_fail_to_alert": args.min_fail}
    if args.no_refresh:
        cfg["refresh_probes"] = []

    result = run(cfg, None)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        d = result.get("details", {})
        print(f"  success: {result.get('success')}")
        print(f"  failing components: {d.get('total_failing_components')}")
        print(f"  by category: {d.get('by_category')}")
        print(f"  alerts opened/resolved/firing: "
              f"{d.get('alerts_opened')}/{d.get('alerts_resolved')}/{d.get('alerts_firing')}")
        print(f"  failure_log inserted: {d.get('failures_logged')}")
        _bt = d.get("board_throughput") or {}
        _sig = _bt.get("signal") or {}
        print(f"  board throughput: {_bt.get('action')} "
              f"(stalled={_sig.get('stalled')}, done in window={_sig.get('completed_in_window')}, "
              f"active={_sig.get('active_tasks')})")
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    sys.exit(main())
