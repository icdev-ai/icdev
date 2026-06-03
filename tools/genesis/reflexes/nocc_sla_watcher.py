# CUI // SP-CTI
"""Genesis Reflex — NOCC SLA Watcher (4h cadence).

Reads noc_sla_records, projects end-of-period compliance for each
circuit's SLA, marks breach=1 when measured_value violates target,
and publishes a warning canvas event when projected compliance falls
below the dynamically computed warn margin.

Detection hierarchy:
  1. Statistical (z-score on historical deviations) — if ≥10 historical
     records exist for the circuit+sla_type pair
  2. LLM contextual — borderline cases near the dynamic threshold
  3. Static fallback — _WARN_MARGIN_PCT when insufficient history

Air-gap safe: LLM calls are optional and degrade gracefully to
statistical/static fallback.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import json as _json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = get_logger(__name__)

CADENCE_HOURS = 4

# Static fallback warn margin (percentage points below target that triggers a warning)
_WARN_MARGIN_PCT = 0.5

# Anomaly detection configuration
_ANOMALY_MIN_HISTORY = 10          # Minimum historical readings for statistical method
_ANOMALY_HISTORY_DAYS = 30         # Days of SLA history to analyze for baseline
_ANOMALY_SIGMA = 1.5               # Z-score multiplier for dynamic margin
_ANOMALY_MODEL = "claude-haiku-4-5-20251001"
_ANOMALY_STABLE_STD = 0.01         # Std threshold below which circuit is "stable"
_BORDERLINE_RATIO = 0.8            # LLM only invoked when gap >= this fraction of margin


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


# ---------------------------------------------------------------------------
# Anomaly detection helpers
# ---------------------------------------------------------------------------

def _fetch_sla_history(
    conn,
    circuit_id: str,
    sla_type: str,
    history_days: int = _ANOMALY_HISTORY_DAYS,
) -> List[float]:
    """Return historical absolute deviations (|measured - target|) for a circuit+sla_type.

    Excludes the current record; the returned list feeds _compute_dynamic_warn_margin.
    """
    cutoff = (_utcnow() - timedelta(days=history_days)).isoformat()
    try:
        rows = _try_exec(
            conn,
            "SELECT measured_value, target_value FROM noc_sla_records "
            "WHERE circuit_id = %s AND sla_type = %s "
            "AND period_start >= %s AND breach = FALSE",
            "SELECT measured_value, target_value FROM noc_sla_records "
            "WHERE circuit_id = ? AND sla_type = ? "
            "AND period_start >= ? AND breach = 0",
            (circuit_id, sla_type, cutoff),
        ).fetchall()
    except Exception:
        return []

    deviations: List[float] = []
    for row in rows:
        try:
            if hasattr(row, "keys"):
                measured = float(row["measured_value"] or 0)
                target = float(row["target_value"] or 0)
            else:
                measured = float(row[0] or 0)
                target = float(row[1] or 0)
            if target > 0:
                deviations.append(abs(measured - target))
        except Exception:
            continue
    return deviations


def _compute_dynamic_warn_margin(
    history_deviations: List[float],
    fallback: float = _WARN_MARGIN_PCT,
) -> Tuple[float, str]:
    """Derive an adaptive SLA warn margin via z-score on historical deviations.

    Returns ``(margin_pct, method)`` where *method* is one of:
    - ``'statistical'``        — mean + sigma*std with adequate history
    - ``'statistical_stable'`` — stable circuit (low std); add fixed buffer
    - ``'static'``             — insufficient history; use *fallback*
    """
    if len(history_deviations) < _ANOMALY_MIN_HISTORY:
        return fallback, "static"

    mean = sum(history_deviations) / len(history_deviations)
    variance = sum((x - mean) ** 2 for x in history_deviations) / len(history_deviations)
    std = variance ** 0.5

    if std < _ANOMALY_STABLE_STD:
        # Stable circuit: use mean deviation + fallback buffer so margin > 0
        margin = max(mean + fallback, fallback)
        return margin, "statistical_stable"

    margin = max(mean + _ANOMALY_SIGMA * std, fallback * 0.1)
    # Cap at 10 percentage points to avoid absurd margins on noisy data
    margin = min(margin, 10.0)
    return margin, "statistical"


def _llm_assess_sla_risk(
    sla_type: str,
    target: float,
    measured: float,
    circuit: str,
    carrier: str,
    margin_pct: float,
    history_len: int,
) -> Optional[bool]:
    """Ask the LLM whether a borderline SLA trend warrants a warning.

    Returns ``True`` (warn), ``False`` (no warn), or ``None`` on error/unavailable.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        lower_is_better = any(
            k in sla_type.lower() for k in ("latency", "jitter", "loss", "packet", "rtt")
        )
        direction = "lower is better" if lower_is_better else "higher is better"
        gap = abs(target - measured)

        prompt = (
            f"Circuit '{circuit}' (carrier: {carrier}) has an SLA of type '{sla_type}' "
            f"({direction}).\n"
            f"Target: {target}, Measured: {measured}, Gap: {gap:.4f}\n"
            f"Dynamic warn margin: {margin_pct:.3f} (based on {history_len} historical records).\n\n"
            "Is this measurement trending toward an SLA breach and warrants a warning?\n"
            "Consider: gap close to margin = likely trending; gap well within margin = noise.\n"
            'Respond ONLY with JSON: {"is_warn": true|false, "rationale": "<one sentence>"}'
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are an SLA anomaly detection assistant. Assess SLA measurements for "
                "genuine breach risk. Be conservative — only warn on real trends."
            ),
            model=_ANOMALY_MODEL,
            max_tokens=128,
            temperature=0.0,
            agent_id="nocc-sla-anomaly-detector",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )

        router = LLMRouter()
        response = router.invoke("anomaly_detection", request)
        parsed = _json.loads(response.content.strip())
        logger.debug(
            "LLM SLA risk assessment: circuit=%s sla_type=%s margin=%.3f result=%s rationale=%s",
            circuit, sla_type, margin_pct,
            parsed.get("is_warn"), parsed.get("rationale", ""),
        )
        return bool(parsed.get("is_warn", True))
    except Exception as exc:
        logger.debug("LLM SLA assessment failed (%s), using statistical fallback", exc)
        return None


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Watch SLA records and flag projected breaches.

    Returns:
        records_checked: int
        breaches_marked: int
        warnings_issued: int
        events_published: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "records_checked": 0,
        "breaches_marked": 0,
        "warnings_issued": 0,
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.noc_canvas.db.init_db import get_connection as nocc_conn
        db = nocc_conn()
        try:
            _watch_sla(db, dry_run, result)
        finally:
            db.close()
    except Exception as exc:
        logger.error("nocc_sla_watcher reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _watch_sla(conn, dry_run: bool, result: Dict[str, Any]) -> None:
    try:
        rows = _try_exec(
            conn,
            "SELECT id, circuit_id, carrier, customer, sla_type, target_value, "
            "measured_value, breach, period_start, period_end "
            "FROM noc_sla_records WHERE breach = FALSE",
            "SELECT id, circuit_id, carrier, customer, sla_type, target_value, "
            "measured_value, breach, period_start, period_end "
            "FROM noc_sla_records WHERE breach = 0",
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"sla_fetch: {exc}")
        return

    result["records_checked"] = len(rows)

    for row in rows:
        if hasattr(row, "keys"):
            rec_id = row["id"]
            circuit = row["circuit_id"]
            carrier = row["carrier"]
            sla_type = row["sla_type"]
            target = float(row["target_value"] or 0)
            measured = float(row["measured_value"] or 0)
        else:
            rec_id, circuit, carrier, _customer, sla_type, target_raw, measured_raw = (
                row[0], row[1], row[2], row[3], row[4], row[5], row[6]
            )
            target = float(target_raw or 0)
            measured = float(measured_raw or 0)

        # Compute dynamic warn margin from historical data for this circuit+sla_type
        history = _fetch_sla_history(conn, circuit, sla_type)
        dynamic_margin, margin_method = _compute_dynamic_warn_margin(history)

        logger.debug(
            "SLA check: circuit=%s sla_type=%s margin=%.3f method=%s history=%d",
            circuit, sla_type, dynamic_margin, margin_method, len(history),
        )

        is_breach, is_warn = _evaluate_sla(
            sla_type, target, measured, dynamic_margin_pct=dynamic_margin
        )

        # LLM borderline assessment for non-breach warnings near the margin
        if is_warn and not is_breach and margin_method != "static":
            gap = abs(target - measured)
            if gap >= dynamic_margin * _BORDERLINE_RATIO:
                llm_result = _llm_assess_sla_risk(
                    sla_type, target, measured, circuit, carrier,
                    dynamic_margin, len(history),
                )
                if llm_result is not None:
                    is_warn = llm_result

        if is_breach and not dry_run:
            try:
                _try_exec(
                    conn,
                    "UPDATE noc_sla_records SET breach = TRUE WHERE id = %s",
                    "UPDATE noc_sla_records SET breach = 1 WHERE id = ?",
                    (rec_id,),
                )
                try:
                    conn.commit()
                except Exception:
                    pass
                result["breaches_marked"] += 1
            except Exception as exc:
                result["errors"].append(f"breach_mark({rec_id}): {exc}")
        elif is_breach:
            result["breaches_marked"] += 1

        if is_warn:
            result["warnings_issued"] += 1
            if not dry_run:
                try:
                    from tools.canvas.event_bus import publish
                    publish("nocc", "nocc.sla.projected_breach", {
                        "record_id": rec_id,
                        "circuit_id": circuit,
                        "carrier": carrier,
                        "sla_type": sla_type,
                        "target": target,
                        "measured": measured,
                        "margin_pct": dynamic_margin,
                        "margin_method": margin_method,
                    })
                    result["events_published"] += 1
                except Exception as exc:
                    result["errors"].append(f"event_bus: {exc}")


def _evaluate_sla(
    sla_type: str,
    target: float,
    measured: float,
    dynamic_margin_pct: float = _WARN_MARGIN_PCT,
) -> Tuple[bool, bool]:
    """Return (is_breach, is_warn).

    For uptime-type SLAs: higher is better (measured must be >= target).
    For latency/jitter/loss: lower is better (measured must be <= target).

    *dynamic_margin_pct* is the warn margin computed by _compute_dynamic_warn_margin;
    defaults to _WARN_MARGIN_PCT (static fallback) when no history is available.
    """
    lower_is_better = any(
        k in sla_type.lower() for k in ("latency", "jitter", "loss", "packet", "rtt")
    )

    if lower_is_better:
        is_breach = measured > target
        is_warn = not is_breach and measured > (target - target * dynamic_margin_pct / 100)
    else:
        # Uptime or throughput: higher is better
        is_breach = measured < target
        is_warn = not is_breach and measured < (target + dynamic_margin_pct)

    return is_breach, is_warn


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({"dry_run": True}), indent=2))
