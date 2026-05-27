"""AIS live feed receiver — SDR hardware (Mode A) or AISHub UDP (Mode B)."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

try:
    from pyais import decode as ais_decode
    from pyais.exceptions import InvalidNMEAMessageException
    HAS_PYAIS = True
except ImportError:
    HAS_PYAIS = False
    InvalidNMEAMessageException = Exception  # type: ignore[misc,assignment]

from tools.db.storage import get_connection

log = get_logger(__name__)

HAVE_SDR_HARDWARE: bool = os.getenv("HAVE_SDR_HARDWARE", "false").lower() in ("1", "true", "yes")
AIS_UDP_HOST: str = os.getenv("AIS_UDP_HOST", "0.0.0.0")  # nosec B104 — intentional broadcast listen, overridable via env
AIS_UDP_PORT: int = int(os.getenv("AIS_UDP_PORT", "9999"))
RTL_AIS_BIN: str = os.getenv("RTL_AIS_BIN", "rtl_ais")

_SHIP_TYPE_MAP = {
    range(0, 1):    "unknown",
    range(20, 30):  "other",
    range(30, 31):  "fishing",
    range(35, 36):  "warship",
    range(36, 38):  "other",
    range(40, 50):  "other",
    range(60, 70):  "other",
    range(70, 80):  "cargo",
    range(80, 90):  "tanker",
    range(90, 100): "other",
}


def _map_vessel_type(ship_type: int) -> str:
    for r, label in _SHIP_TYPE_MAP.items():
        if ship_type in r:
            return label
    return "unknown"


def _write_track(conn, decoded: dict, source: str) -> bool:
    """Insert a position fix into sg_vessel_tracks; returns True on success."""
    mmsi = str(decoded.get("mmsi", "") or "")
    if not mmsi:
        return False

    lat = decoded.get("lat") or decoded.get("y")
    lon = decoded.get("lon") or decoded.get("x")
    if lat is None or lon is None:
        return False

    vessel_name = (
        decoded.get("shipname") or decoded.get("name") or f"VESSEL-{mmsi[-4:]}"
    )
    vessel_type = _map_vessel_type(int(decoded.get("ship_type", 0) or 0))
    speed = float(decoded.get("speed", 0) or 0)
    heading = float(decoded.get("course", decoded.get("heading", 0)) or 0)
    ts = datetime.now(timezone.utc).isoformat()

    row_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sg_vessel_tracks "
            "(id, mmsi, vessel_name, vessel_type, flag, lat, lon, speed, heading, ts, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row_id, mmsi, vessel_name, vessel_type, None,
             float(lat), float(lon), speed, heading, ts, source),
        )
        conn.commit()
        return True
    except Exception as exc:
        # source column absent — migration 076 may not have run yet
        if "no column named source" in str(exc).lower() or "source" in str(exc).lower():
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sg_vessel_tracks "
                    "(id, mmsi, vessel_name, vessel_type, flag, lat, lon, speed, heading, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row_id, mmsi, vessel_name, vessel_type, None,
                     float(lat), float(lon), speed, heading, ts),
                )
                conn.commit()
                return True
            except Exception as inner:
                log.warning("track write failed (no-source fallback): %s", inner)
                return False
        log.warning("track write failed: %s", exc)
        return False


def _decode_sentence(sentence: str) -> Optional[dict]:
    """Return decoded dict from a single NMEA sentence, or None on failure."""
    if not HAS_PYAIS:
        return None
    try:
        msg = ais_decode(sentence)
        return msg.asdict()
    except InvalidNMEAMessageException as exc:
        log.debug("invalid NMEA: %s — %s", sentence[:40], exc)
        return None
    except Exception as exc:
        log.debug("decode error: %s — %s", sentence[:40], exc)
        return None


def _run_aishub(duration_s: int, dry_run: bool, conn) -> tuple[int, int]:
    """UDP receive loop for AISHub. Returns (messages_received, vessels_updated)."""
    if dry_run:
        return 0, 0

    received = 0
    updated = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((AIS_UDP_HOST, AIS_UDP_PORT))  # nosec B104 — intentional broadcast listen
    sock.settimeout(1.0)
    log.info("AISHub UDP listening on %s:%d for %ds", AIS_UDP_HOST, AIS_UDP_PORT, duration_s)

    deadline = time.monotonic() + duration_s
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            text = data.decode("ascii", errors="ignore").strip()
            for line in text.splitlines():
                line = line.strip()
                if not (line.startswith("!AIVDM") or line.startswith("!AIVDO")):
                    continue
                received += 1
                decoded = _decode_sentence(line)
                if decoded and _write_track(conn, decoded, "aishub"):
                    updated += 1
    finally:
        sock.close()

    return received, updated


def _run_sdr(duration_s: int, dry_run: bool, conn) -> tuple[int, int]:
    """rtl_ais subprocess loop. Returns (messages_received, vessels_updated)."""
    if dry_run:
        return 0, 0

    received = 0
    updated = 0
    cmd = [RTL_AIS_BIN, "-n", str(duration_s)]
    log.info("Starting SDR: %s", " ".join(cmd))

    try:
        proc = subprocess.Popen(  # nosec B603 — RTL_AIS_BIN is env-configured, not user input
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        log.error("rtl_ais binary not found at '%s'", RTL_AIS_BIN)
        return 0, 0

    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not (line.startswith("!AIVDM") or line.startswith("!AIVDO")):
                continue
            received += 1
            decoded = _decode_sentence(line)
            if decoded and _write_track(conn, decoded, "sdr"):
                updated += 1
    finally:
        proc.wait()

    return received, updated


def receive_loop(duration_s: int = 60, dry_run: bool = False) -> dict:
    """Run the AIS receiver for *duration_s* seconds.

    Returns {'messages_received': int, 'vessels_updated': int, 'source': str}.
    In dry_run mode the socket/subprocess is skipped and zeroed stats are returned.
    """
    mode = "sdr" if HAVE_SDR_HARDWARE else "aishub"
    log.info("receive_loop mode=%s duration=%ds dry_run=%s", mode, duration_s, dry_run)

    if dry_run:
        return {"messages_received": 0, "vessels_updated": 0, "source": mode}

    conn = get_connection()
    try:
        if mode == "sdr":
            received, updated = _run_sdr(duration_s, dry_run=False, conn=conn)
        else:
            received, updated = _run_aishub(duration_s, dry_run=False, conn=conn)
    finally:
        conn.close()

    return {"messages_received": received, "vessels_updated": updated, "source": mode}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AIS live feed receiver")
    parser.add_argument("--duration", type=int, default=60, metavar="SECONDS",
                        help="Collection window in seconds (default: 60)")
    parser.add_argument("--mode", choices=["sdr", "aishub"], default=None,
                        help="Force mode (default: auto-detect via HAVE_SDR_HARDWARE)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip socket/subprocess; return zeroed stats")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Print result as JSON")
    args = parser.parse_args()

    import sys
    _this = sys.modules[__name__]
    if args.mode == "sdr":
        _this.HAVE_SDR_HARDWARE = True  # type: ignore[attr-defined]
    elif args.mode == "aishub":
        _this.HAVE_SDR_HARDWARE = False  # type: ignore[attr-defined]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    if args.dry_run:
        effective_mode = args.mode or ("sdr" if HAVE_SDR_HARDWARE else "aishub")
        result = {"status": "ok", "mode": effective_mode}
    else:
        result = receive_loop(duration_s=args.duration, dry_run=False)
        result["status"] = "ok"

    if args.as_json:
        print(json.dumps(result))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
