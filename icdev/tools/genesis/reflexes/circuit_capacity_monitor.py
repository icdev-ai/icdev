# CUI // SP-CTI
"""Genesis Reflex — CCC Circuit Capacity Monitor (4h cadence).

Monitors all active CCC circuits; for any circuit above the warn/critical
utilization threshold, publishes a canvas warning event and updates
noc_alarms if NOCC is enabled.

Thresholds are loaded from args/circuit_capacity_monitor.yaml and
adaptively calibrated at runtime via LLM anomaly detection (haiku) based
on the fleet's current utilization distribution.  Falls back to YAML
defaults — and ultimately to built-in constants — when LLM is unavailable.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
from pathlib import Path
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict, List

logger = get_logger(__name__)

CADENCE_HOURS = 4

# Fallback constants — used only when args YAML and LLM are both unavailable.
_DEFAULT_WARN_PCT = 70.0
_DEFAULT_CRITICAL_PCT = 85.0

_ARGS_PATH = Path(__file__).resolve().parents[3] / "args" / "circuit_capacity_monitor.yaml"

_DEFAULT_THRESHOLDS: Dict[str, float] = {
    "warn_pct": _DEFAULT_WARN_PCT,
    "critical_pct": _DEFAULT_CRITICAL_PCT,
    "max_warn_pct": 82.0,
    "max_critical_pct": 95.0,
    "min_warn_pct": 55.0,
    "min_critical_pct": 70.0,
}


def _load_thresholds() -> Dict[str, float]:
    """Load threshold config from args YAML, falling back to built-in defaults."""
    try:
        import yaml
        if _ARGS_PATH.exists():
            with open(_ARGS_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return {**_DEFAULT_THRESHOLDS, **data.get("thresholds", {})}
    except Exception:
        pass
    return _DEFAULT_THRESHOLDS.copy()


def _compute_fleet_stats(rows: list) -> Dict[str, float]:
    """Compute utilization distribution stats from current circuit rows."""
    utils = []
    for row in rows:
        util = row["utilization_pct"] if hasattr(row, "keys") else row[3]
        try:
            utils.append(float(util))
        except (TypeError, ValueError):
            pass
    if not utils:
        return {}
    utils.sort()
    n = len(utils)
    mean = sum(utils) / n
    p75 = utils[int(n * 0.75)]
    p90 = utils[min(int(n * 0.90), n - 1)]
    p95 = utils[min(int(n * 0.95), n - 1)]
    return {"count": n, "mean": round(mean, 1), "p75": p75, "p90": p90, "p95": p95}


def _calibrate_thresholds_with_ai(fleet_stats: Dict[str, float], base: Dict[str, float]) -> Dict[str, float]:
    """Use haiku LLM to adaptively calibrate warn/critical thresholds.

    High-utilization fleets need higher thresholds to suppress noise;
    low-utilization fleets benefit from lower thresholds to catch anomalies early.
    Falls back to base thresholds on any LLM error.
    """
    if not fleet_stats:
        return base

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        prompt = (
            "Calibrate anomaly detection thresholds for a network circuit capacity monitor.\n"
            f"Fleet utilization statistics (current snapshot, {fleet_stats['count']} active circuits):\n"
            f"  Mean: {fleet_stats['mean']}%, P75: {fleet_stats['p75']}%, "
            f"P90: {fleet_stats['p90']}%, P95: {fleet_stats['p95']}%.\n"
            f"Base thresholds: warn={base['warn_pct']}%, critical={base['critical_pct']}%.\n"
            f"Allowed warn range: [{base['min_warn_pct']}%, {base['max_warn_pct']}%].\n"
            f"Allowed critical range: [{base['min_critical_pct']}%, {base['max_critical_pct']}%].\n"
            "Rules:\n"
            "- If fleet mean > 65% → raise both thresholds to reduce alert fatigue\n"
            "- If fleet mean < 30% → lower both thresholds to catch anomalies earlier\n"
            "- critical_pct must always be at least 10% above warn_pct\n"
            "- Stay within the allowed ranges\n"
            "Respond ONLY with valid JSON:\n"
            '{"warn_pct": <float>, "critical_pct": <float>, "rationale": "<one sentence>"}'
        )

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a network capacity anomaly detection calibrator. "
                "Output only compact JSON with the three requested keys."
            ),
            agent_id="circuit-capacity-anomaly-calibrator",
            classification="CUI",
            max_tokens=150,
            effort="low",
        )
        response = router.invoke("anomaly_detection", request)
        raw = (response.content or "").strip()
        # Strip markdown fences if present
        import re
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        calibrated = json.loads(raw)

        warn = float(calibrated.get("warn_pct", base["warn_pct"]))
        critical = float(calibrated.get("critical_pct", base["critical_pct"]))
        # Clamp to allowed ranges
        warn = max(base["min_warn_pct"], min(base["max_warn_pct"], warn))
        critical = max(base["min_critical_pct"], min(base["max_critical_pct"], critical))
        # Enforce gap invariant
        if critical < warn + 10.0:
            critical = warn + 10.0

        rationale = calibrated.get("rationale", "")
        if rationale:
            logger.debug("circuit_capacity_monitor: AI calibration — %s", rationale)

        return {**base, "warn_pct": round(warn, 1), "critical_pct": round(critical, 1)}

    except Exception as exc:
        logger.debug("circuit_capacity_monitor: AI calibration skipped (%s), using base thresholds", exc)
        return base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Scan CCC circuits for high utilization and publish events.

    Returns:
        circuits_checked: int
        warn_circuits: list of {circuit_id, carrier, utilization_pct}
        critical_circuits: list of {circuit_id, carrier, utilization_pct}
        events_published: int
        alarms_created: int
        thresholds_used: dict with warn_pct and critical_pct (AI-calibrated)
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "circuits_checked": 0,
        "warn_circuits": [],
        "critical_circuits": [],
        "events_published": 0,
        "alarms_created": 0,
        "thresholds_used": {},
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.ccc_canvas.db.init_db import get_connection as ccc_conn
        conn_ccc = ccc_conn()
        try:
            rows = conn_ccc.execute(
                "SELECT circuit_id, carrier, bandwidth_gbps, utilization_pct, status "
                "FROM ccc_circuits WHERE status='active' ORDER BY utilization_pct DESC"
            ).fetchall()
        finally:
            conn_ccc.close()
    except Exception as exc:
        logger.error("circuit_capacity_monitor: CCC DB unavailable: %s", exc)
        result["status"] = "error"
        result["errors"].append(f"ccc_db: {exc}")
        return result

    # Adaptively calibrate thresholds based on fleet utilization distribution
    base_thresholds = _load_thresholds()
    fleet_stats = _compute_fleet_stats(rows)
    thresholds = _calibrate_thresholds_with_ai(fleet_stats, base_thresholds)
    warn_threshold = thresholds["warn_pct"]
    critical_threshold = thresholds["critical_pct"]
    result["thresholds_used"] = {
        "warn_pct": warn_threshold,
        "critical_pct": critical_threshold,
        "fleet_stats": fleet_stats,
    }

    warn: List[Dict] = []
    critical: List[Dict] = []

    for row in rows:
        result["circuits_checked"] += 1
        cid = row["circuit_id"] if hasattr(row, "keys") else row[0]
        carrier = row["carrier"] if hasattr(row, "keys") else row[1]
        bw = row["bandwidth_gbps"] if hasattr(row, "keys") else row[2]
        util = row["utilization_pct"] if hasattr(row, "keys") else row[3]

        entry = {"circuit_id": cid, "carrier": carrier,
                 "bandwidth_gbps": bw, "utilization_pct": util}

        if util >= critical_threshold:
            critical.append(entry)
        elif util >= warn_threshold:
            warn.append(entry)

    result["warn_circuits"] = warn
    result["critical_circuits"] = critical

    if dry_run:
        return result

    # Publish canvas events
    try:
        from tools.canvas.event_bus import publish
        for c in critical:
            publish("ccc", "ccc.circuit.capacity_critical", {
                "circuit_id": c["circuit_id"],
                "carrier": c["carrier"],
                "utilization_pct": c["utilization_pct"],
                "threshold": critical_threshold,
            })
            result["events_published"] += 1
        for w in warn:
            publish("ccc", "ccc.circuit.capacity_warn", {
                "circuit_id": w["circuit_id"],
                "carrier": w["carrier"],
                "utilization_pct": w["utilization_pct"],
                "threshold": warn_threshold,
            })
            result["events_published"] += 1
    except Exception as exc:
        result["errors"].append(f"event_bus: {exc}")

    # Create NOC alarms for critical circuits if NOCC is enabled
    if critical:
        try:
            from tools.noc_canvas.db.init_db import get_connection as nocc_conn
            conn_nocc = nocc_conn()
            try:
                for c in critical:
                    _maybe_insert_alarm(conn_nocc, c, critical_threshold)
                    result["alarms_created"] += 1
                conn_nocc.commit()
            finally:
                conn_nocc.close()
        except Exception as exc:
            result["errors"].append(f"nocc_alarm: {exc}")

    return result


def _maybe_insert_alarm(conn, circuit: Dict, critical_threshold: float) -> None:
    cid = circuit["circuit_id"]
    desc = (
        f"Circuit {cid} ({circuit['carrier']}) at "
        f"{circuit['utilization_pct']:.1f}% utilization "
        f"(threshold: {critical_threshold:.1f}%)"
    )
    # Suppress duplicate alarms for the same circuit that are still active
    existing = conn.execute(
        "SELECT id FROM noc_alarms WHERE circuit_id=%s AND alarm_type='circuit' "
        "AND severity='critical' AND cleared=0 LIMIT 1",
        (cid,),
    ).fetchone()
    if existing:
        # Refresh last_seen
        try:
            conn.execute(
                "UPDATE noc_alarms SET last_seen=%s, description=%s WHERE id=%s",
                (_now(), desc, existing[0]),
            )
        except Exception:
            conn.execute(
                "UPDATE noc_alarms SET last_seen=%s, description=%s WHERE id=%s",
                (_now(), desc, existing[0]),
            )
        return

    try:
        conn.execute(
            "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, "
            "circuit_id, description, first_seen, last_seen, cleared, suppressed, acknowledged) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,0)",
            ("circuit_capacity_monitor", "critical", "circuit",
             f"CCC/{circuit['carrier']}", cid, desc, _now(), _now()),
        )
    except Exception:
        conn.execute(
            "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, "
            "circuit_id, description, first_seen, last_seen, cleared, suppressed, acknowledged) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,0)",
            ("circuit_capacity_monitor", "critical", "circuit",
             f"CCC/{circuit['carrier']}", cid, desc, _now(), _now()),
        )


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
    import json as _json
    print(_json.dumps(run({}), indent=2))
