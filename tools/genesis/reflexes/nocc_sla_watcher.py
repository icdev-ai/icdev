# CUI // SP-CTI
"""Genesis Reflex — NOCC SLA Watcher (4h cadence).

Reads noc_sla_records, projects end-of-period compliance for each
circuit's SLA, marks breach=1 when measured_value violates target,
and publishes a warning canvas event when projected compliance falls
below the dynamically computed warn margin.

Detection hierarchy:
  1. Statistical (z-score on historical deviations) — if ≥min_samples historical
     records exist for the circuit+sla_type pair (configurable via genesis_config.yaml)
  2. LLM contextual — borderline cases near the dynamic threshold
  3. Static fallback — warn_margin_pct when insufficient history

All anomaly detection parameters are sourced from genesis_config.yaml
reflexes.nocc_sla_watcher.anomaly_detection so they can be tuned without
code changes. Module-level values below are default fallbacks only.

Air-gap safe: LLM calls are optional and degrade gracefully to
statistical/static fallback.
"""
from __future__ import annotations

import os

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import json as _json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = get_logger(__name__)

CADENCE_HOURS = 4

# Static fallback warn margin — overridable via genesis_config.yaml warn_margin_pct
_WARN_MARGIN_PCT = 0.5

# Default anomaly detection parameters — all overridable from ctx (genesis_config.yaml)
_ANOMALY_MIN_HISTORY = 10          # min_samples: minimum historical readings for statistical method
_ANOMALY_HISTORY_DAYS = 30         # history_days: days of SLA history to analyze
_ANOMALY_SIGMA = 1.5               # sigma_multiplier: z-score multiplier for dynamic margin
_ANOMALY_STABLE_STD = 0.01         # stable_std: std threshold below which circuit is "stable"
_BORDERLINE_RATIO = 0.8            # borderline_ratio: LLM only invoked when gap >= this fraction of margin


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
    anomaly_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[float, str]:
    """Derive an adaptive SLA warn margin via z-score on historical deviations.

    Parameters sourced from *anomaly_cfg* (genesis_config.yaml
    nocc_sla_watcher.anomaly_detection); module-level defaults used when absent.

    Returns ``(margin_pct, method)`` where *method* is one of:
    - ``'statistical'``        — mean + sigma*std with adequate history
    - ``'statistical_stable'`` — stable circuit (low std); add fixed buffer
    - ``'static'``             — insufficient history; use *fallback*
    """
    if anomaly_cfg is None:
        anomaly_cfg = {}

    if not anomaly_cfg.get("enabled", True):
        return fallback, "static"

    min_samples = int(anomaly_cfg.get("min_samples", _ANOMALY_MIN_HISTORY))
    sigma_multiplier = float(anomaly_cfg.get("sigma_multiplier", _ANOMALY_SIGMA))
    stable_std = float(anomaly_cfg.get("stable_std", _ANOMALY_STABLE_STD))
    bounds = anomaly_cfg.get("adaptive_bounds", {})
    margin_floor = float(bounds.get("margin_floor", 0.1))
    margin_ceiling = float(bounds.get("margin_ceiling", 10.0))

    if len(history_deviations) < min_samples:
        return fallback, "static"

    mean = sum(history_deviations) / len(history_deviations)
    variance = sum((x - mean) ** 2 for x in history_deviations) / len(history_deviations)
    std = variance ** 0.5

    if std < stable_std:
        # Stable circuit: use mean deviation + fallback buffer so margin > 0
        margin = max(mean + fallback, fallback)
        return min(margin, margin_ceiling), "statistical_stable"

    margin = max(mean + sigma_multiplier * std, margin_floor)
    margin = min(margin, margin_ceiling)
    return margin, "statistical"


def _llm_assess_sla_risk(
    sla_type: str,
    target: float,
    measured: float,
    circuit: str,
    carrier: str,
    margin_pct: float,
    history_len: int,
    model: Optional[str] = None,
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

        # Model: prefer explicit arg → env override → haiku default (never hardcoded)
        resolved_model = (
            model
            or os.environ.get("ICDEV_NOCC_SLA_ANOMALY_MODEL")
            or os.environ.get("ICDEV_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are an SLA anomaly detection assistant. Assess SLA measurements for "
                "genuine breach risk. Be conservative — only warn on real trends."
            ),
            model=resolved_model,
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
            _watch_sla(db, dry_run, ctx, result)
        finally:
            db.close()
    except Exception as exc:
        logger.error("nocc_sla_watcher reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _watch_sla(conn, dry_run: bool, cfg: Dict[str, Any], result: Dict[str, Any]) -> None:
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

    # Source all anomaly detection params from cfg (genesis_config.yaml) with module defaults
    anomaly_cfg = cfg.get("anomaly_detection", {})
    static_fallback = float(cfg.get("warn_margin_pct", _WARN_MARGIN_PCT))
    history_days = int(anomaly_cfg.get("history_days", _ANOMALY_HISTORY_DAYS))
    borderline_ratio = float(anomaly_cfg.get("borderline_ratio", _BORDERLINE_RATIO))
    llm_model = cfg.get("llm_model") or None  # None → resolved in _llm_assess_sla_risk via env

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
        history = _fetch_sla_history(conn, circuit, sla_type, history_days)
        dynamic_margin, margin_method = _compute_dynamic_warn_margin(
            history, fallback=static_fallback, anomaly_cfg=anomaly_cfg
        )

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
            if gap >= dynamic_margin * borderline_ratio:
                llm_result = _llm_assess_sla_risk(
                    sla_type, target, measured, circuit, carrier,
                    dynamic_margin, len(history), model=llm_model,
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
    print(_json.dumps(run({"dry_run": True}), indent=2))
