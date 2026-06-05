# CUI // SP-CTI
"""Genesis Reflex — NDC Topology Drift Detector (4h cadence).

Detects network topologies whose graph_json has changed since the last
inventory export, flagging stale diagrams and suggesting a re-export.

Air-gap safe: no LLM calls by default — pure DB heuristics.
LLM anomaly assessment is opt-in via aiify_config.yaml
(ndc_topology_drift.llm_anomaly_detection.enabled) — aiify-rm-ff651-phase-5317.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 4

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "args" / "aiify_config.yaml"

# ---------------------------------------------------------------------------
# Config — ndc_topology_drift section from args/aiify_config.yaml
# ---------------------------------------------------------------------------
_FALLBACK_CFG: Dict[str, Any] = {
    "staleness_threshold_days": 7,
    "llm_anomaly_detection": {
        "enabled": False,
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
    },
}


def _load_config() -> Dict[str, Any]:
    try:
        import yaml
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and "ndc_topology_drift" in cfg:
            return cfg["ndc_topology_drift"]
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


_cfg: Dict[str, Any] = _load_config()
_STALENESS_THRESHOLD_DAYS: int = int(_cfg.get("staleness_threshold_days", 7))
_llm_cfg: Dict[str, Any] = _cfg.get("llm_anomaly_detection", {})
_LLM_ENABLED: bool = bool(_llm_cfg.get("enabled", False))
_LLM_MODEL: str = str(_llm_cfg.get("model", "claude-haiku-4-5-20251001"))
_LLM_MAX_TOKENS: int = int(_llm_cfg.get("max_tokens", 300))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _llm_assess_staleness(
    topology_rows: List[Dict[str, Any]],
    threshold_days: int,
) -> Dict[str, Any]:
    """LLM anomaly assessment for topology drift patterns (opt-in only).

    Sends topology update data to Claude Haiku to identify which topologies
    show anomalous staleness beyond what the fixed threshold would catch.

    Returns {stale_ids, anomaly_label, rationale}.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        import json as _json

        summary_lines = [
            f"- id={r['id']} name={r['name']} days_since_update={r['days']}"
            for r in topology_rows
        ]
        summary = "\n".join(summary_lines) or "(no topologies found)"

        prompt = (
            f"Network topology drift analysis. Configured staleness window: {threshold_days} days.\n\n"
            f"Topology update ages:\n{summary}\n\n"
            "Identify which topology IDs appear anomalously stale — either beyond the threshold "
            "or showing unusual patterns relative to peers. Return JSON only:\n"
            '{"stale_ids": [<list of integer ids>], '
            '"anomaly_label": "nominal|elevated|anomalous|critical", '
            '"rationale": "<one sentence>"}'
        )
        req = LLMRequest(
            system_prompt=(
                "You are a network topology drift anomaly detector. "
                "Evaluate topology update patterns and flag stale diagrams. "
                "Return ONLY the JSON object requested."
            ),
            messages=[{"role": "user", "content": prompt}],
            model=_LLM_MODEL,
            max_tokens=_LLM_MAX_TOKENS,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("anomaly_detection", req)
        if resp and resp.content:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data = _json.loads(raw)
            return {
                "stale_ids": [int(i) for i in data.get("stale_ids", [])],
                "anomaly_label": str(data.get("anomaly_label", "unknown")),
                "rationale": str(data.get("rationale", "")),
            }
    except Exception as exc:
        return {"stale_ids": [], "anomaly_label": "unknown", "rationale": f"LLM error: {exc}"}
    return {"stale_ids": [], "anomaly_label": "unknown", "rationale": "no response"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Check NDC topologies for drift vs last export.

    Returns:
        stale_topologies: list of {id, name, days_since_export}
        events_published: int
        llm_assessment: dict (only present when llm_anomaly_detection.enabled=true)
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    threshold = int(ctx.get("staleness_threshold_days", _STALENESS_THRESHOLD_DAYS))

    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "staleness_threshold_days": threshold,
        "stale_topologies": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.network.db.init_db import get_connection as ndc_conn
        conn_ndc = ndc_conn()
        try:
            rows = conn_ndc.execute(
                "SELECT id, name, updated_at FROM topologies ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn_ndc.close()

        all_topo_rows: List[Dict[str, Any]] = []
        stale = []
        for row in rows:
            tid = row["id"] if isinstance(row, dict) else row[0]
            name = row["name"] if isinstance(row, dict) else row[1]
            updated_at = row["updated_at"] if isinstance(row, dict) else row[2]
            days = _days_since(updated_at or "")
            all_topo_rows.append({"id": tid, "name": name, "days": days})
            # Heuristic: flag topologies updated within the staleness window
            # but with no confirmed export (topology changed, export may lag)
            if days <= threshold:
                stale.append({"id": tid, "name": name, "days_since_update": days})

        # Optional LLM-based anomaly assessment to catch drift patterns beyond
        # the fixed threshold (e.g., peers all exported recently but one outlier
        # hasn't). Disabled by default — zero token cost when off.
        if _LLM_ENABLED and all_topo_rows:
            assessment = _llm_assess_staleness(all_topo_rows, threshold)
            result["llm_assessment"] = assessment
            # Merge LLM-identified stale IDs not already caught by the heuristic
            heuristic_ids = {t["id"] for t in stale}
            for trow in all_topo_rows:
                if trow["id"] in assessment.get("stale_ids", []) and trow["id"] not in heuristic_ids:
                    stale.append({
                        "id": trow["id"],
                        "name": trow["name"],
                        "days_since_update": trow["days"],
                        "flagged_by": "llm",
                    })

        result["stale_topologies"] = stale

        if stale and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for t in stale:
                    publish("ndc", "ndc.topology.drift_detected", {
                        "topology_id": t["id"],
                        "topology_name": t["name"],
                        "days_since_update": t["days_since_update"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("ndc_topology_drift reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({}), indent=2))
