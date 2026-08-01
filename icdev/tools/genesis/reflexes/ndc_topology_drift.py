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

import json
import re
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
# ACOIC drift bridge helpers
# ---------------------------------------------------------------------------

def _configs_by_device(graph: Dict[str, Any], topo_name: str) -> Dict[str, str]:
    """Generate {device_id: config_text} for a topology graph.

    ``generate_device_configs`` keys its result by *filename*
    (e.g. ``core-router_ios.txt``); ``detect_drift`` compares by device. Strip
    the generator's suffix so drift items name the device, not a file.
    Deterministic and air-gap safe — no LLM, no network.
    """
    from tools.network.config_generator import generate_device_configs

    out: Dict[str, str] = {}
    for filename, text in (generate_device_configs(graph, topo_name) or {}).items():
        device_id = re.sub(r"(_ios)?\.txt$", "", filename)
        out[device_id] = text
    return out


def _load_baseline_graph(conn, topology_id: str) -> Dict[str, Any] | None:
    """Return the baseline graph for a topology, or None when none is saved.

    Baseline = an explicitly-labelled ``baseline``/``golden`` version if one
    exists, else the highest ``version_num``. Returning None is a real answer:
    a topology with no saved version has nothing to diff against and MUST be
    reported, never fabricated.
    """
    row = conn.execute(
        "SELECT graph_json FROM nc_versions WHERE topology_id = %s "
        "ORDER BY CASE WHEN LOWER(COALESCE(label,'')) LIKE '%%baseline%%' "
        "           OR LOWER(COALESCE(label,'')) LIKE '%%golden%%' THEN 0 ELSE 1 END, "
        "         version_num DESC LIMIT 1",
        (topology_id,),
    ).fetchone()
    if not row:
        return None
    raw = dict(row).get("graph_json") if not isinstance(row, (list, tuple)) else row[0]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _affected_doc_ids(topology_id: str, topo_name: str, tenant_id: str, limit: int = 5) -> List[str]:
    """Resolve DIC documents impacted by this topology's drift.

    Reuses the existing ``args/dic_canvas_integrations.yaml`` mapping via
    canvas_adapter — no new config. Returns [] when nothing maps, which is an
    honest outcome (drift happened; no document is tagged for it).
    """
    from tools.document_intelligence.canvas_adapter import resolve_affected_collections

    affected = resolve_affected_collections({
        "event_type": "ndc.topology.drift_detected",
        "source_canvas": "ndc",
        "payload_json": json.dumps({"topology_id": topology_id, "topology_name": topo_name}),
        "tenant_id": tenant_id,
    })
    if not affected:
        return []

    from tools.db.storage import get_connection

    doc_ids: List[str] = []
    conn = get_connection()
    try:
        for entry in affected:
            cid = entry.get("collection_id")
            if not cid:
                continue
            rows = conn.execute(
                "SELECT doc_id FROM dic_documents WHERE collection_id = %s LIMIT %s",
                (cid, limit),
            ).fetchall()
            for r in rows:
                doc_ids.append(dict(r)["doc_id"] if not isinstance(r, (list, tuple)) else r[0])
    except Exception as exc:
        logger.warning("[ndc_topology_drift] doc resolution failed: %s", exc)
    finally:
        conn.close()
    return doc_ids


def _check_topology_drift(
    conn, tid: str, name: str, graph_raw: str, tenant_id: str, classification: str,
    dry_run: bool, result: Dict[str, Any],
) -> bool:
    """Diff one topology against its baseline and feed ACOIC. Returns True on drift."""
    from tools.network.drift_detector import detect_drift, emit_drift_events

    baseline_graph = _load_baseline_graph(conn, tid)
    if baseline_graph is None:
        result["baselines_missing"].append({"id": tid, "name": name})
        return False

    try:
        current_graph = json.loads(graph_raw or "{}")
    except Exception:
        result["errors"].append(f"{tid}: unreadable graph_json")
        return False

    baseline_cfgs = _configs_by_device(baseline_graph, name)
    current_cfgs = _configs_by_device(current_graph, name)
    if not baseline_cfgs and not current_cfgs:
        result["no_configurable_devices"].append({"id": tid, "name": name})
        return False

    report = detect_drift(tid, baseline_cfgs, current_cfgs)
    if not report.items:
        return False

    result["drifted_topologies"].append({
        "id": tid, "name": name,
        "items": len(report.items), "severity": report.overall_severity,
    })
    if dry_run:
        return True

    doc_ids = _affected_doc_ids(tid, name, tenant_id)
    if not doc_ids:
        result["docs_unmapped"].append({"id": tid, "name": name})

    # Record the drift events once, then enqueue each impacted doc against them.
    emitted = emit_drift_events(
        report, document_id="", tenant_id=tenant_id, classification=classification,
    )
    result["drift_events_recorded"] += len(emitted.get("event_ids", []))

    if doc_ids and emitted.get("event_ids"):
        from tools.document_intelligence import acoic
        for doc_id in doc_ids:
            try:
                acoic.enqueue_regen(
                    doc_id,
                    event_id=emitted["event_ids"][0],
                    drift_source=f"network/{tid}",
                    drift_entity=tid,
                    severity=report.overall_severity,
                    dedup_key=f"{doc_id}|{tid}|{report.current_snapshot_id}",
                    tenant_id=tenant_id or None,
                    classification=classification or None,
                )
                result["regen_enqueued"] += 1
            except Exception as exc:
                result["errors"].append(f"{tid}: enqueue_regen failed: {exc}")
    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], trust: Any = None) -> Dict[str, Any]:
    """Check NDC topologies for config drift vs their saved baseline.

    Producer for ACOIC: diffs each topology's generated device configs against
    its ``nc_versions`` baseline, records drift into ``dic_drift_events``, and
    enqueues impacted DIC documents for HITL regeneration.

    Note the daemon dispatches reflexes as ``fn(config, trust)`` — the second
    positional arg is the TrustKernel, NOT a DB connection.

    ctx keys: dry_run, staleness_threshold_days, topology_ids (filter),
    tenant_id, classification.

    Returns:
        stale_topologies, drifted_topologies, drift_events_recorded,
        regen_enqueued, baselines_missing, docs_unmapped,
        no_configurable_devices, events_published, errors, status
    """
    dry_run = ctx.get("dry_run", False)
    threshold = int(ctx.get("staleness_threshold_days", _STALENESS_THRESHOLD_DAYS))
    only_ids = {t for t in (ctx.get("topology_ids") or []) if t}
    tenant_id = ctx.get("tenant_id") or ""
    classification = ctx.get("classification") or "CUI"

    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "staleness_threshold_days": threshold,
        "stale_topologies": [],
        "drifted_topologies": [],
        "drift_events_recorded": 0,
        "regen_enqueued": 0,
        "baselines_missing": [],
        "docs_unmapped": [],
        "no_configurable_devices": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.network.db.init_db import get_connection as ndc_conn
        conn_ndc = ndc_conn()
        try:
            rows = conn_ndc.execute(
                "SELECT id, name, updated_at, graph_json FROM topologies "
                "ORDER BY updated_at DESC"
            ).fetchall()

            # ── ACOIC bridge: real config drift vs saved baseline ────────────
            # Each topology is isolated so one bad graph never kills the run.
            for row in rows:
                d = dict(row) if not isinstance(row, (list, tuple)) else None
                tid = d["id"] if d else row[0]
                name = d["name"] if d else row[1]
                graph_raw = d["graph_json"] if d else row[3]
                if only_ids and tid not in only_ids:
                    continue
                try:
                    _check_topology_drift(
                        conn_ndc, tid, name, graph_raw, tenant_id,
                        classification, dry_run, result,
                    )
                except Exception as exc:
                    result["errors"].append(f"{tid}: drift check failed: {exc}")
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

        # Publish only for topologies with REAL config drift, not mere staleness
        # — the editorial consumer turns each event into a dic_suggestions row,
        # and "updated recently" is not evidence that a document is now wrong.
        # target_canvas="dic" is required: dic_integration filters on
        # `WHERE target_canvas = 'dic'`, so an untargeted publish is invisible.
        if result["drifted_topologies"] and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for t in result["drifted_topologies"]:
                    publish(
                        "ndc",
                        "ndc.topology.drift_detected",
                        {
                            "topology_id": t["id"],
                            "topology_name": t["name"],
                            "drift_items": t["items"],
                            "severity": t["severity"],
                        },
                        target_canvas="dic",
                    )
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("ndc_topology_drift reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


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
