#!/usr/bin/env python3
# CUI // SP-CTI
"""ARCANE passive beacon attribution: cross-campaign attacker fingerprinting.

Fingerprints RF beacons by hashing (freq_mhz, signal_type, mac_oui) and
correlates recurring hashes across collection dates to attribute campaigns
to a common emitter.

Core API
--------
  hash_beacon(signal)            → SHA-256 hex of (freq_mhz|signal_type|mac_oui)
  match_campaigns(beacon_hash)   → list of {date, count} dicts for that hash
  attribution_score(hash)        → log(occurrence_count) / log(total_campaigns)
  record_event(signal, ...)      → INSERT into sg_sigint_events, return event id

CLI
---
  python tools/strategos/rf_attribution.py --hash --freq 433.92 --type BLE --oui 00:1A:7D
  python tools/strategos/rf_attribution.py --score --hash <hex>
  python tools/strategos/rf_attribution.py --record --freq 433.92 --type BLE --oui 00:1A:7D --lat 38.9 --lon -77.0
  python tools/strategos/rf_attribution.py --match --hash <hex> --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def hash_beacon(signal: dict[str, Any]) -> str:
    """Return SHA-256 hex fingerprint for a beacon signal.

    Canonical form: ``<freq_mhz>|<signal_type>|<mac_oui>`` — all lower-case,
    stripped, pipe-delimited so the hash is stable across whitespace variants.
    """
    freq = str(signal.get("freq_mhz", "")).strip()
    sig_type = str(signal.get("signal_type", "")).strip().lower()
    oui = str(signal.get("mac_oui", "")).strip().lower()
    raw = f"{freq}|{sig_type}|{oui}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Campaign matching
# ---------------------------------------------------------------------------

def match_campaigns(beacon_hash: str) -> list[dict[str, Any]]:
    """Return per-date occurrence counts for *beacon_hash* in sg_sigint_events.

    Each entry: ``{"date": "YYYY-MM-DD", "count": N}``, ordered newest first.
    Returns an empty list if the hash has never been seen.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT substr(detected_at, 1, 10) AS day, COUNT(*) AS cnt
        FROM   sg_sigint_events
        WHERE  beacon_hash = %s
        GROUP  BY day
        ORDER  BY day DESC
        """,
        (beacon_hash,),
    ).fetchall()
    return [{"date": row[0], "count": row[1]} for row in rows]


# ---------------------------------------------------------------------------
# Attribution score
# ---------------------------------------------------------------------------

def attribution_score(beacon_hash: str) -> float:
    """Compute log(occurrence_count) / log(total_campaigns).

    occurrence_count  = total events with this hash across all dates.
    total_campaigns   = total distinct detection dates in sg_sigint_events.

    Returns 0.0 when the hash is unseen or when total_campaigns < 2
    (log denominator undefined / degenerate).
    """
    conn = get_connection()

    occurrence_count: int = conn.execute(
        "SELECT COUNT(*) FROM sg_sigint_events WHERE beacon_hash = %s",
        (beacon_hash,),
    ).fetchone()[0]

    total_campaigns: int = conn.execute(
        "SELECT COUNT(DISTINCT substr(detected_at, 1, 10)) FROM sg_sigint_events",
    ).fetchone()[0]

    if occurrence_count == 0 or total_campaigns < 2:
        return 0.0

    score = math.log(occurrence_count) / math.log(total_campaigns)
    # Clamp to [0.0, 1.0] — theoretically bounded but guard against edge cases
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Event recording
# ---------------------------------------------------------------------------

def record_event(
    signal: dict[str, Any],
    lat: float | None = None,
    lon: float | None = None,
    raw_iq_ref: str | None = None,
    detected_at: str | None = None,
) -> str:
    """Insert a new beacon event into sg_sigint_events.

    Computes the beacon hash and attribution score before writing so every
    row is self-consistent at insert time.  Returns the generated event id.
    """
    b_hash = hash_beacon(signal)
    score = attribution_score(b_hash)

    event_id = f"arc-{uuid.uuid4().hex[:12]}"
    ts = detected_at or datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO sg_sigint_events
            (id, beacon_hash, freq_mhz, signal_type, attribution_score,
             lat, lon, raw_iq_ref, detected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            b_hash,
            signal.get("freq_mhz"),
            signal.get("signal_type"),
            score,
            lat,
            lon,
            raw_iq_ref,
            ts,
        ),
    )
    conn.commit()
    logger.info("Recorded beacon event %s hash=%s score=%.4f", event_id, b_hash[:12], score)
    return event_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="ARCANE RF beacon attribution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--freq", type=float, help="Frequency in MHz")
    p.add_argument("--type", dest="sig_type", help="Signal type (e.g. BLE, WiFi-Probe)")
    p.add_argument("--oui", help="MAC OUI prefix (e.g. 00:1A:7D)")
    p.add_argument("--lat", type=float, help="Latitude")
    p.add_argument("--lon", type=float, help="Longitude")
    p.add_argument("--raw-iq", dest="raw_iq", help="Raw I/Q reference path or URI")
    p.add_argument("--detected-at", help="Override detection timestamp (ISO-8601)")
    p.add_argument("--hash", dest="do_hash", action="store_true", help="Print beacon hash")
    p.add_argument("--match", dest="do_match", action="store_true",
                   help="Show per-date campaign hits for --hash <hex>")
    p.add_argument("--score", dest="do_score", action="store_true",
                   help="Print attribution score for --hash <hex>")
    p.add_argument("--record", dest="do_record", action="store_true",
                   help="Record a new beacon event")
    p.add_argument("--beacon-hash", help="Pre-computed beacon hash (for --match / --score)")
    return p


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)

    def _signal() -> dict[str, Any]:
        return {
            "freq_mhz": args.freq,
            "signal_type": args.sig_type or "",
            "mac_oui": args.oui or "",
        }

    def _out(data: Any) -> None:
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(data)

    if args.do_hash:
        _out(hash_beacon(_signal()))

    elif args.do_match:
        h = args.beacon_hash or hash_beacon(_signal())
        campaigns = match_campaigns(h)
        _out({"beacon_hash": h, "campaigns": campaigns})

    elif args.do_score:
        h = args.beacon_hash or hash_beacon(_signal())
        score = attribution_score(h)
        _out({"beacon_hash": h, "attribution_score": score})

    elif args.do_record:
        event_id = record_event(
            _signal(),
            lat=args.lat,
            lon=args.lon,
            raw_iq_ref=args.raw_iq,
            detected_at=args.detected_at,
        )
        _out({"event_id": event_id, "beacon_hash": hash_beacon(_signal())})

    else:
        _build_parser().print_help()


if __name__ == "__main__":
    main()
