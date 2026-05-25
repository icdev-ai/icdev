#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis GovChain Anchor Reflex — periodic Merkle root submission to Hyperledger Fabric.

Runs every 30 minutes (configurable via args/awareness_config.yaml govchain.interval_minutes).
Collects unanchored audit entries and provenance registry citations, batches them into
Merkle trees, and submits roots to the GovChain channel.

In air-gap mode, operations are queued to govchain_pending_operations for deferred flush.

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
            timeout=120,
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
        return {"status": "error", "error": "timeout after 120s"}
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


def run(ctx: dict, _trigger) -> Dict[str, Any]:
    """Main reflex entrypoint called by the Genesis daemon.

    Args:
        ctx: Genesis execution context (unused — reflex is self-contained).
        _trigger: Trigger event (unused).

    Returns:
        Reflex result dict with anchor summary and pending queue depth.
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

    result = {
        "reflex": "govchain_anchor",
        "timestamp": _utcnow_iso(),
        "anchor": anchor_result,
        "pending_queue_depth": pending,
        "status": "ok" if anchor_result.get("status") not in ("error",) else "error",
    }

    if anchor_result.get("status") == "error":
        print(f"[govchain_anchor] ERROR: {anchor_result.get('error')}")

    return result


if __name__ == "__main__":
    import json as _json

    out = run({}, None)
    print(_json.dumps(out, indent=2))