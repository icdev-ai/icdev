#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis GovChain Anchor Reflex — periodic Merkle root submission to Hyperledger Fabric.

Runs every 30 minutes (configurable via args/awareness_config.yaml govchain.interval_minutes).
Collects unanchored audit entries and provenance registry citations, batches them into
Merkle trees, and submits roots to the GovChain channel.

In air-gap mode, operations are queued to govchain_pending_operations for deferred flush.

Thresholds (anchor_timeout_seconds, pending_queue_warning) are sourced from
args/aiify_config.yaml (govchain_anchor section) — aiify-rm-ff651-phase-5283.
LLM anomaly assessment of anchor result + queue depth is opt-in via
aiify_config.yaml (govchain_anchor.llm_anomaly_detection.enabled).

Risk tier: GREEN (read + write DB only, no network unless Fabric is reachable).
"""
IMPLEMENTATION_STATUS = "full"

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

_AIIFY_CONFIG_PATH = BASE_DIR / "args" / "aiify_config.yaml"

# ---------------------------------------------------------------------------
# Defaults — overridden by args/aiify_config.yaml govchain_anchor section
# ---------------------------------------------------------------------------
_FALLBACK_CFG: Dict[str, Any] = {
    "anchor_timeout_seconds": 120,
    "pending_queue_warning": 500,
    "llm_anomaly_detection": {
        "enabled": False,
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 256,
    },
}


def _load_aiify_config() -> Dict[str, Any]:
    """Load govchain_anchor section from args/aiify_config.yaml."""
    try:
        import yaml

        with open(_AIIFY_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and "govchain_anchor" in cfg:
            return cfg["govchain_anchor"]
    except Exception:
        pass
    return dict(_FALLBACK_CFG)


_anchor_cfg: Dict[str, Any] = _load_aiify_config()
_ANCHOR_TIMEOUT: int = int(_anchor_cfg.get("anchor_timeout_seconds", 120))
_PENDING_QUEUE_WARNING: int = int(_anchor_cfg.get("pending_queue_warning", 500))
_llm_cfg: Dict[str, Any] = _anchor_cfg.get("llm_anomaly_detection", {})
_LLM_ENABLED: bool = bool(_llm_cfg.get("enabled", False))
_LLM_MODEL: str = str(_llm_cfg.get("model", "claude-haiku-4-5-20251001"))
_LLM_MAX_TOKENS: int = int(_llm_cfg.get("max_tokens", 256))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_config() -> Dict[str, Any]:
    """Load govchain schedule config from args/awareness_config.yaml."""
    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "awareness_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return raw.get("govchain", {})
    except Exception:
        return {}


def _run_periodic_anchor() -> Dict[str, Any]:
    """Invoke chain_anchor --periodic and return parsed result."""
    try:
        result = subprocess.run(
            [sys.executable, "tools/blockchain/chain_anchor.py", "--periodic", "--json"],
            capture_output=True,
            text=True,
            timeout=_ANCHOR_TIMEOUT,
            cwd=str(BASE_DIR),
            env={**__import__("os").environ, "PYTHONPATH": str(BASE_DIR)},
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            try:
                return json.loads(stdout[json_start:])
            except json.JSONDecodeError:
                pass
        return {"status": "ok", "raw": stdout[:500]}
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"timeout after {_ANCHOR_TIMEOUT}s"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _get_pending_count() -> int:
    """Return the number of govchain_pending_operations with status='pending'."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM govchain_pending_operations WHERE status='pending'"
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception:
        return -1


def _llm_assess_anomaly(
    anchor_result: Dict[str, Any],
    pending: int,
) -> Dict[str, Any]:
    """LLM anomaly assessment for anchor result and pending queue depth (opt-in only).

    Returns {anomaly_score, anomaly_label, rationale}.
    anomaly_score 0.0 = nominal, 1.0 = severe anomaly.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        anchor_status = anchor_result.get("status", "unknown")
        anchor_error = anchor_result.get("error", "")
        anchored = anchor_result.get("anchored", "unknown")
        prompt = (
            f"GovChain anchor reflex result: status={anchor_status}, "
            f"anchored={anchored}, pending_queue_depth={pending}"
            + (f", error={anchor_error}" if anchor_error else "")
            + f"\nPending queue warning threshold: {_PENDING_QUEUE_WARNING}.\n\n"
            "Assess whether this pattern is anomalous. Return JSON only:\n"
            '{"anomaly_score": <0.0-1.0>, '
            '"anomaly_label": "nominal|elevated|anomalous|critical", '
            '"rationale": "<one sentence>"}'
        )
        req = LLMRequest(
            system_prompt=(
                "You are a blockchain anchor anomaly detector. "
                "Evaluate GovChain Merkle root anchor results and queue depth. "
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
            import json as _json

            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data = _json.loads(raw)
            return {
                "anomaly_score": float(data.get("anomaly_score", 0.0)),
                "anomaly_label": str(data.get("anomaly_label", "unknown")),
                "rationale": str(data.get("rationale", "")),
            }
    except Exception as exc:
        return {"anomaly_score": 0.0, "anomaly_label": "unknown", "rationale": f"LLM error: {exc}"}
    return {"anomaly_score": 0.0, "anomaly_label": "unknown", "rationale": "no response"}


def run(ctx: dict, _trigger) -> Dict[str, Any]:
    """Main reflex entrypoint called by the Genesis daemon.

    Args:
        ctx: Genesis execution context (unused — reflex is self-contained).
        _trigger: Trigger event (unused).

    Returns:
        Reflex result dict with anchor summary, pending queue depth, and
        optional LLM anomaly assessment.
    """
    cfg = _load_config()
    enabled = cfg.get("enabled", True)

    print(f"[govchain_anchor] {'enabled' if enabled else 'disabled'} — {_utcnow_iso()}")

    if not enabled:
        return {"status": "skipped", "reason": "govchain.enabled=false in awareness_config.yaml"}

    print("[govchain_anchor] Running periodic anchor scan...")
    anchor_result = _run_periodic_anchor()

    pending = _get_pending_count()
    print(f"[govchain_anchor] Pending ops in queue: {pending}")

    if pending > _PENDING_QUEUE_WARNING:
        print(
            f"[govchain_anchor] WARNING: pending queue depth {pending} exceeds "
            f"threshold {_PENDING_QUEUE_WARNING}"
        )

    result: Dict[str, Any] = {
        "reflex": "govchain_anchor",
        "timestamp": _utcnow_iso(),
        "anchor": anchor_result,
        "pending_queue_depth": pending,
        "pending_queue_warning_threshold": _PENDING_QUEUE_WARNING,
        "status": "ok" if anchor_result.get("status") not in ("error",) else "error",
    }

    if anchor_result.get("status") == "error":
        print(f"[govchain_anchor] ERROR: {anchor_result.get('error')}")

    if _LLM_ENABLED:
        print("[govchain_anchor] Running LLM anomaly assessment...")
        anomaly = _llm_assess_anomaly(anchor_result, pending)
        result["anomaly_detection"] = anomaly
        print(
            f"[govchain_anchor] anomaly_detection  "
            f"label={anomaly['anomaly_label']}  score={anomaly['anomaly_score']:.2f}"
        )

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

    out = run({}, None)
    print(_json.dumps(out, indent=2))
