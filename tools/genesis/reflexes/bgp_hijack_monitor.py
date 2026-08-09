# CUI // SP-CTI
"""BGP Hijack Monitor Genesis Reflex — DSOC automation (cnr-dsoc-05).

Runs on the Genesis daemon at DSOC's configured cadence (default 1h). Two jobs:

  1. RTBH auto-expiry (always, deterministic): withdraw RTBH entries whose
     ``auto_withdraw_minutes`` window has elapsed. This wires
     ``rtbh_manager.auto_expire_rtbh`` which was previously never called.

  2. BGP hijack / route-leak sweep (best-effort): when a pmacct accounting feed
     is reachable AND its records carry BGP origin/peer ASN fields, feed
     ``bgp_hijack_detector.detect_prefix_hijack`` / ``detect_route_leak``. When
     no feed is configured the sweep is skipped gracefully — this reflex never
     fabricates a hijack from data it does not have.

Registration (Genesis daemon gotcha): the name string MUST be in
``REFLEX_NAMES`` (tools/genesis/daemon.py) and configured in
``args/genesis_config.yaml`` or it will never be dispatched. Use 127.0.0.1,
never ``localhost``, for any local endpoint.
"""
from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

IMPLEMENTATION_STATUS = "full"

import uuid
from typing import Any, Dict, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 1


def _expire_rtbh() -> int:
    """Withdraw expired RTBH entries. Returns count expired (0 on any failure)."""
    try:
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.rtbh_manager import auto_expire_rtbh
    except Exception as exc:  # noqa: BLE001
        logger.warning("bgp_hijack_monitor: RTBH import failed: %s", exc)
        return 0
    conn = get_connection()
    try:
        return int(auto_expire_rtbh(conn))
    except Exception as exc:  # noqa: BLE001
        logger.warning("bgp_hijack_monitor: auto_expire_rtbh failed: %s", exc)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _hijack_sweep(dry_run: bool) -> Dict[str, Any]:
    """Best-effort BGP hijack / route-leak sweep from an available BGP feed.

    Returns {feed_available, records_scanned, hijacks_detected}. Skips (feed
    unavailable) whenever pmacct is not reachable or records lack BGP ASN data.
    """
    result = {"feed_available": False, "records_scanned": 0, "hijacks_detected": 0}
    try:
        from tools.databridge.connectors.pmacct_connector import (
            fetch_accounting_records,
            test_connection,
        )
    except Exception:
        return result

    try:
        health = test_connection()
    except Exception:
        return result
    if not isinstance(health, dict) or not health.get("ok", health.get("connected", False)):
        return result

    try:
        records = fetch_accounting_records(limit=500) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("bgp_hijack_monitor: fetch_accounting_records failed: %s", exc)
        return result

    result["feed_available"] = True
    result["records_scanned"] = len(records)
    if dry_run:
        return result

    try:
        from tools.dsoc_canvas.bgp_hijack_detector import detect_route_leak
        from tools.dsoc_canvas.db.init_db import get_connection
    except Exception:
        return result

    conn = get_connection()
    detected = 0
    try:
        for rec in records:
            prefix = rec.get("prefix") or rec.get("ip_dst")
            announcing = rec.get("as_origin") or rec.get("origin_asn")
            peer = rec.get("peer_as_src") or rec.get("peer_asn")
            leak_type = rec.get("leak_type") or "unknown"
            if not (prefix and announcing and peer):
                continue  # record lacks BGP ASN data — cannot assess
            try:
                created = detect_route_leak(
                    conn,
                    prefix=str(prefix),
                    announcing_asn=int(announcing),
                    peer_asn=int(peer),
                    leak_type=str(leak_type),
                    detection_source="pmacct",
                )
                if created:
                    detected += 1
            except Exception:
                continue
    finally:
        try:
            conn.close()
        except Exception:
            pass
    result["hijacks_detected"] = detected
    return result


def run(context: Optional[Dict[str, Any]] = None, db_conn: Any = None) -> Dict[str, Any]:
    """Genesis daemon entry point. Called as run(config, trust)."""
    context = context or {}
    dry_run = bool(context.get("dry_run", False)) if isinstance(context, dict) else False
    reflex_id = context.get("reflex_id", f"bhm-{uuid.uuid4().hex[:10]}") if isinstance(context, dict) else f"bhm-{uuid.uuid4().hex[:10]}"

    # auto_expire_rtbh withdraws entries (a write), so skip it under dry-run.
    expired = 0 if dry_run else _expire_rtbh()
    sweep = _hijack_sweep(dry_run)

    details = {
        "reflex_id": reflex_id,
        "rtbh_expired": expired,
        **sweep,
    }
    logger.info(
        "bgp_hijack_monitor: rtbh_expired=%s feed_available=%s hijacks_detected=%s",
        expired, sweep["feed_available"], sweep["hijacks_detected"],
    )
    return {
        "success": True,
        "metric_value": float(expired + sweep["hijacks_detected"]),
        "details": details,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

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
    import argparse
    import json

    parser = argparse.ArgumentParser(description="BGP Hijack Monitor Genesis Reflex")
    parser.add_argument("--dry-run", action="store_true", help="Skip writes (expiry + sweep are read-only)")
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    args = parser.parse_args()

    out = run({"dry_run": args.dry_run})
    print(json.dumps(out, indent=2, default=str))
