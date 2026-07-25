# CUI // SP-CTI
"""Genesis Reflex — BGP Route Monitor (1h cadence).

Queries LibreNMS and/or SolarWinds for BGP session state on all
monitored routers, creates noc_alarms entries for sessions that are
down or idle, and clears alarms for sessions that have recovered.

Air-gap safe: no LLM calls — pure heuristics + NMS API queries.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.request import Request, urlopen

from tools.logging.icdev_logger import get_logger

IMPLEMENTATION_STATUS = "full"

logger = get_logger(__name__)

CADENCE_HOURS = 1

_LIBRENMS_URL = os.environ.get("LIBRENMS_URL", "")
_LIBRENMS_TOKEN = os.environ.get("LIBRENMS_API_TOKEN", "")
_SOLARWINDS_URL = os.environ.get("SOLARWINDS_URL", "")
_SOLARWINDS_USER = os.environ.get("SOLARWINDS_USER", "")
_SOLARWINDS_PASS = os.environ.get("SOLARWINDS_PASS", "")
_REQUEST_TIMEOUT = 15

# BGP states that are considered "down"
_DOWN_STATES = {"idle", "connect", "active", "opensent", "openconfirm"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


def _fetch_librenms_bgp() -> List[Dict[str, Any]]:
    """Fetch BGP peers from LibreNMS /api/v0/bgp."""
    if not _LIBRENMS_URL or not _LIBRENMS_TOKEN:
        return []
    url = f"{_LIBRENMS_URL.rstrip('/')}/api/v0/bgp"
    req = Request(url, headers={"X-Auth-Token": _LIBRENMS_TOKEN, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return data.get("bgp_sessions", [])
    except Exception as exc:
        logger.warning("LibreNMS BGP fetch failed: %s", exc)
        return []


def _fetch_solarwinds_bgp() -> List[Dict[str, Any]]:
    """Fetch BGP session state from SolarWinds SWIS /Query."""
    if not _SOLARWINDS_URL or not _SOLARWINDS_USER:
        return []
    import base64
    creds = base64.b64encode(f"{_SOLARWINDS_USER}:{_SOLARWINDS_PASS}".encode()).decode()
    query = "SELECT NodeID, NodeName, LocalAS, RemoteAS, RemoteAddress, BGPState FROM Orion.BGPNeighbors"
    url = f"{_SOLARWINDS_URL.rstrip('/')}/SolarWinds/InformationService/v3/Json/Query"
    payload = json.dumps({"query": query}).encode()
    req = Request(
        url, data=payload,
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return [
            {
                "device_name": r.get("NodeName", ""),
                "remote_address": r.get("RemoteAddress", ""),
                "remote_asn": r.get("RemoteAS", 0),
                "bgpPeerState": r.get("BGPState", "unknown"),
            }
            for r in data.get("results", [])
        ]
    except Exception as exc:
        logger.warning("SolarWinds BGP fetch failed: %s", exc)
        return []


def _normalize_session(raw: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Normalize a BGP session record from different NMS sources."""
    if source == "librenms":
        return {
            "device_name": raw.get("device_id", ""),
            "device_ip": raw.get("bgpPeerIdentifier", ""),
            "remote_asn": raw.get("bgpPeerRemoteAs", 0),
            "state": (raw.get("bgpPeerState") or "unknown").lower(),
            "source": "librenms",
        }
    # solarwinds or generic
    return {
        "device_name": raw.get("device_name", ""),
        "device_ip": raw.get("remote_address", ""),
        "remote_asn": raw.get("remote_asn", 0),
        "state": (raw.get("bgpPeerState") or "unknown").lower(),
        "source": source,
    }


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Monitor BGP sessions and create/clear NOCC alarms.

    Returns:
        sessions_checked: int
        sessions_down: int
        alarms_created: int
        alarms_cleared: int
        events_published: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "sessions_checked": 0,
        "sessions_down": 0,
        "alarms_created": 0,
        "alarms_cleared": 0,
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }

    # Collect sessions from all configured NMS sources
    sessions: List[Dict[str, Any]] = []
    for raw in _fetch_librenms_bgp():
        sessions.append(_normalize_session(raw, "librenms"))
    for raw in _fetch_solarwinds_bgp():
        sessions.append(_normalize_session(raw, "solarwinds"))

    if not sessions:
        result["errors"].append("No NMS configured or no BGP sessions returned — set LIBRENMS_URL or SOLARWINDS_URL")
        return result

    result["sessions_checked"] = len(sessions)

    try:
        from tools.noc_canvas.db.init_db import get_connection as nocc_conn
        db = nocc_conn()
        try:
            _process_sessions(db, sessions, dry_run, result)
        finally:
            db.close()
    except Exception as exc:
        logger.error("bgp_route_monitor reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _process_sessions(conn, sessions: List[Dict[str, Any]], dry_run: bool, result: Dict[str, Any]) -> None:
    for session in sessions:
        device = session["device_name"]
        remote_ip = session["device_ip"]
        remote_asn = session["remote_asn"]
        state = session["state"]
        is_down = state in _DOWN_STATES

        if is_down:
            result["sessions_down"] += 1
            # Check for existing open alarm on this session
            try:
                existing = _try_exec(
                    conn,
                    "SELECT id FROM noc_alarms WHERE device_name = %s AND device_ip = %s "
                    "AND alarm_type = 'bgp' AND cleared = FALSE LIMIT 1",
                    "SELECT id FROM noc_alarms WHERE device_name = ? AND device_ip = ? "
                    "AND alarm_type = 'bgp' AND cleared = 0 LIMIT 1",
                    (device, remote_ip),
                ).fetchone()
            except Exception:
                existing = None

            if not existing and not dry_run:
                desc = f"BGP session to AS{remote_asn} ({remote_ip}) is {state.upper()}"
                try:
                    _try_exec(
                        conn,
                        "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, "
                        "device_ip, description, first_seen, last_seen) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        "INSERT INTO noc_alarms (alarm_source, severity, alarm_type, device_name, "
                        "device_ip, description, first_seen, last_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (session["source"], "critical", "bgp", device, remote_ip, desc, _now(), _now()),
                    )
                    try:
                        conn.commit()
                    except Exception:
                        pass
                    result["alarms_created"] += 1
                    _publish_event(result, "nocc.alarm.bgp_session_down", {
                        "device": device, "remote_asn": remote_asn, "state": state,
                    })
                except Exception as exc:
                    result["errors"].append(f"alarm_create({device}:{remote_ip}): {exc}")
            elif not existing:
                result["alarms_created"] += 1  # dry_run count

        else:
            # Session up — clear any existing alarm
            if not dry_run:
                try:
                    _try_exec(
                        conn,
                        "UPDATE noc_alarms SET cleared = TRUE, last_seen = %s "
                        "WHERE device_name = %s AND device_ip = %s AND alarm_type = 'bgp' AND cleared = FALSE",
                        "UPDATE noc_alarms SET cleared = 1, last_seen = ? "
                        "WHERE device_name = ? AND device_ip = ? AND alarm_type = 'bgp' AND cleared = 0",
                        (_now(), device, remote_ip),
                    )
                    try:
                        conn.commit()
                    except Exception:
                        pass
                    result["alarms_cleared"] += 1
                except Exception as exc:
                    result["errors"].append(f"alarm_clear({device}:{remote_ip}): {exc}")


def _publish_event(result: Dict[str, Any], event: str, payload: Dict[str, Any]) -> None:
    try:
        from tools.canvas.event_bus import publish
        publish("nocc", event, payload)
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
