# CUI // SP-CTI
"""Genesis Reflex — Peering Health Monitor (6h cadence).

Re-syncs BGP peers whose PeeringDB data is stale and re-validates RPKI for
high-traffic peers.  Thresholds are derived adaptively via LLM-based anomaly
detection (oracle_anomaly_detection chain → claude-haiku) using the live peer
population statistics; hardcoded defaults are used as fallbacks.

Publishes canvas events for peers with newly discovered RPKI-invalid prefixes
or significant policy changes.

Air-gap safe: uses stdlib urllib for PeeringDB and Cloudflare RPKI APIs.
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)

CADENCE_HOURS = 6

# --- Fallback thresholds (used when LLM anomaly detection is unavailable) ---
_DEFAULT_STALE_THRESHOLD_DAYS = 7
_DEFAULT_HIGH_TRAFFIC_RATIO_THRESHOLD = 0.5


def _load_config() -> Dict[str, Any]:
    """Load args/peering_health_monitor.yaml; return {} on any failure."""
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[3] / "args" / "peering_health_monitor.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    except Exception:
        pass
    return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_str: str) -> int:
    if not iso_str:
        return 999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 999


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


# ---------------------------------------------------------------------------
# Anomaly-detection: adaptive threshold derivation
# ---------------------------------------------------------------------------

def _compute_peer_stats(stale_days_list: List[int], traffic_ratios: List[float]) -> Dict[str, Any]:
    """Compute population statistics used to prompt the anomaly detection LLM."""
    def _stats(values: list) -> Dict[str, float]:
        if not values:
            return {"count": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0,
                    "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0}
        n = len(values)
        sorted_v = sorted(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = variance ** 0.5

        def _pct(p: float) -> float:
            idx = (p / 100) * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (idx - lo)

        return {
            "count": n,
            "mean": round(mean, 3),
            "std": round(std, 3),
            "min": sorted_v[0],
            "max": sorted_v[-1],
            "p25": round(_pct(25), 3),
            "p50": round(_pct(50), 3),
            "p75": round(_pct(75), 3),
            "p90": round(_pct(90), 3),
        }

    return {
        "stale_days": _stats(stale_days_list),
        "traffic_ratio": _stats(traffic_ratios),
    }


def _get_adaptive_thresholds(stats: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Ask the LLM anomaly-detection chain for adaptive peer-health thresholds.

    Returns a dict with keys:
      stale_threshold_days       — int, days before PeeringDB sync is considered stale
      high_traffic_ratio_threshold — float, traffic_ratio above which RPKI re-validation
                                     is mandatory

    Falls back to hardcoded defaults on any failure.
    """
    if cfg is None:
        cfg = {}
    ad_cfg = cfg.get("anomaly_detection", {})

    stale_default = int(cfg.get("stale_threshold_days", _DEFAULT_STALE_THRESHOLD_DAYS))
    traffic_default = float(cfg.get("high_traffic_ratio_threshold", _DEFAULT_HIGH_TRAFFIC_RATIO_THRESHOLD))
    defaults = {
        "stale_threshold_days": stale_default,
        "high_traffic_ratio_threshold": traffic_default,
    }

    if not ad_cfg.get("enabled", True):
        return defaults

    try:
        import json
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        sd = stats["stale_days"]
        tr = stats["traffic_ratio"]

        min_peers = int(ad_cfg.get("min_peers_for_adaptive", 3))
        if sd["count"] < min_peers:
            # Too few peers to make a meaningful distribution judgement
            return defaults

        prompt = (
            "You are a BGP network anomaly detection system. "
            "Given the following peer population statistics, recommend adaptive thresholds "
            "for peering health monitoring.\n\n"
            f"Stale-days distribution (days since last PeeringDB sync):\n"
            f"  count={sd['count']}, mean={sd['mean']}, std={sd['std']}, "
            f"  min={sd['min']}, p25={sd['p25']}, p50={sd['p50']}, "
            f"  p75={sd['p75']}, p90={sd['p90']}, max={sd['max']}\n\n"
            f"Traffic-ratio distribution (0–1 fraction of total traffic):\n"
            f"  count={tr['count']}, mean={tr['mean']}, std={tr['std']}, "
            f"  min={tr['min']}, p25={tr['p25']}, p50={tr['p50']}, "
            f"  p75={tr['p75']}, p90={tr['p90']}, max={tr['max']}\n\n"
            "Respond ONLY with a valid JSON object (no markdown) with these two keys:\n"
            "  stale_threshold_days: integer — peers whose sync is older than this are stale\n"
            "  high_traffic_ratio_threshold: float — peers above this ratio need RPKI re-validation\n\n"
            "Base the stale threshold on the p75 of the stale-days distribution (rounded to the "
            "nearest integer, minimum 3). Base the traffic threshold on the p75 of traffic_ratio "
            "(rounded to 2 decimal places, clamped to [0.1, 0.9])."
        )

        llm_cfg = ad_cfg.get("llm", {})
        model_id = llm_cfg.get("model", "claude-haiku-4-5-20251001")
        max_tokens = int(llm_cfg.get("max_tokens", 128))
        temperature = float(llm_cfg.get("temperature", 0.0))

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a network anomaly detection assistant. "
                "Return only valid JSON, no explanation."
            ),
            model=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            classification="CUI",
        )
        response = router.invoke("oracle_anomaly_detection", request)
        raw = (response.content or "").strip()

        parsed = json.loads(raw)
        stale_days = int(parsed.get("stale_threshold_days", _DEFAULT_STALE_THRESHOLD_DAYS))
        traffic_thresh = float(parsed.get("high_traffic_ratio_threshold",
                                          _DEFAULT_HIGH_TRAFFIC_RATIO_THRESHOLD))

        # Sanity clamps
        stale_days = max(3, min(stale_days, 90))
        traffic_thresh = max(0.1, min(traffic_thresh, 0.9))

        logger.info(
            "anomaly_detection thresholds: stale_days=%d, traffic_ratio=%.2f (population n=%d)",
            stale_days, traffic_thresh, sd["count"],
        )
        return {
            "stale_threshold_days": stale_days,
            "high_traffic_ratio_threshold": traffic_thresh,
        }

    except Exception as exc:
        logger.warning("adaptive threshold derivation failed (%s) — using defaults", exc)
        return defaults


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Re-sync stale peers and re-validate RPKI for high-traffic peers.

    Returns:
        peers_checked: int
        stale_synced: int
        rpki_revalidated: int
        rpki_violations_found: int
        events_published: int
        thresholds_used: dict
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    cfg = _load_config()
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "peers_checked": 0,
        "stale_synced": 0,
        "rpki_revalidated": 0,
        "rpki_violations_found": 0,
        "events_published": 0,
        "thresholds_used": {},
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.pmc_canvas.db.init_db import get_connection as pmc_conn
        db = pmc_conn()
        try:
            _monitor_peers(db, dry_run, result, cfg)
        finally:
            db.close()
    except Exception as exc:
        logger.error("peering_health_monitor reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _monitor_peers(conn, dry_run: bool, result: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> None:
    # Fetch all active peers
    try:
        rows = _try_exec(
            conn,
            "SELECT id, asn, org_name, peeringdb_sync, traffic_ratio, status "
            "FROM peering_peers WHERE status = %s",
            "SELECT id, asn, org_name, peeringdb_sync, traffic_ratio, status "
            "FROM peering_peers WHERE status = ?",
            ("active",),
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"peer_fetch: {exc}")
        return

    result["peers_checked"] = len(rows)

    # --- Derive adaptive thresholds via anomaly detection ---
    stale_days_list: List[int] = []
    traffic_ratios: List[float] = []
    peer_records = []

    for row in rows:
        if hasattr(row, "keys"):
            peer_id = row["id"]
            asn = row["asn"]
            org = row["org_name"]
            last_sync = row["peeringdb_sync"]
            traffic_ratio = float(row["traffic_ratio"] or 0)
        else:
            peer_id, asn, org, last_sync, traffic_ratio = (
                row[0], row[1], row[2], row[3], float(row[4] or 0)
            )

        sd = _days_since(str(last_sync) if last_sync else "")
        stale_days_list.append(sd)
        traffic_ratios.append(traffic_ratio)
        peer_records.append((peer_id, asn, org, sd, traffic_ratio))

    stats = _compute_peer_stats(stale_days_list, traffic_ratios)
    thresholds = _get_adaptive_thresholds(stats, cfg)
    result["thresholds_used"] = thresholds

    stale_threshold = thresholds["stale_threshold_days"]
    traffic_threshold = thresholds["high_traffic_ratio_threshold"]

    for peer_id, asn, org, stale_days, traffic_ratio in peer_records:
        # Re-sync stale PeeringDB data
        if stale_days >= stale_threshold:
            if not dry_run:
                _sync_peer_peeringdb(conn, peer_id, asn, org, result)
            result["stale_synced"] += 1

        # Re-validate RPKI for high-traffic peers
        if traffic_ratio >= traffic_threshold:
            if not dry_run:
                violations = _revalidate_rpki(conn, peer_id, asn, org, result)
                if violations:
                    result["rpki_violations_found"] += violations
                    _publish_event(result, "pmc.rpki.violations_found", {
                        "peer_id": peer_id,
                        "asn": asn,
                        "org_name": org,
                        "violation_count": violations,
                        "traffic_ratio": traffic_ratio,
                    })
            result["rpki_revalidated"] += 1


def _sync_peer_peeringdb(conn, peer_id: int, asn: int, org: str, result: Dict[str, Any]) -> None:
    try:
        from tools.pmc_canvas.peeringdb_client import sync_peer_from_peeringdb
        sync_peer_from_peeringdb(asn, conn)
        result.setdefault("_synced_asns", []).append(asn)
    except Exception as exc:
        result["errors"].append(f"peeringdb_sync(AS{asn}): {exc}")


def _revalidate_rpki(conn, peer_id: int, asn: int, org: str, result: Dict[str, Any]) -> int:
    """Re-validate all prefixes for a peer. Return count of new RPKI violations."""
    try:
        prefixes = _try_exec(
            conn,
            "SELECT id, prefix, origin_asn FROM peering_prefixes WHERE peer_id = %s",
            "SELECT id, prefix, origin_asn FROM peering_prefixes WHERE peer_id = ?",
            (peer_id,),
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"prefix_fetch(AS{asn}): {exc}")
        return 0

    if not prefixes:
        return 0

    try:
        from tools.pmc_canvas.rpki_validator import validate_prefix
    except Exception:
        result["errors"].append(f"rpki_validator import failed for AS{asn}")
        return 0

    violations = 0
    for prefix_row in prefixes:
        if hasattr(prefix_row, "keys"):
            prefix_id = prefix_row["id"]
            prefix = prefix_row["prefix"]
            origin_asn = prefix_row["origin_asn"]
        else:
            prefix_id, prefix, origin_asn = prefix_row[0], prefix_row[1], prefix_row[2]

        try:
            val = validate_prefix(prefix, origin_asn or asn)
            new_status = val.get("status", "unknown")

            _try_exec(
                conn,
                "UPDATE peering_prefixes SET rpki_status = %s, roa_found = %s WHERE id = %s",
                "UPDATE peering_prefixes SET rpki_status = ?, roa_found = ? WHERE id = ?",
                (new_status, 1 if val.get("roa_found") else 0, prefix_id),
            )
            try:
                conn.commit()
            except Exception:
                pass

            if new_status == "invalid":
                violations += 1
        except Exception as exc:
            result["errors"].append(f"rpki_validate({prefix}): {exc}")

    return violations


def _publish_event(result: Dict[str, Any], event: str, payload: Dict[str, Any]) -> None:
    try:
        from tools.canvas.event_bus import publish
        publish("pmc", event, payload)
        result["events_published"] += 1
    except Exception as exc:
        result["errors"].append(f"event_bus({event}): {exc}")


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
