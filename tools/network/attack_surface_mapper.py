# CUI // SP-CTI
"""PVM — Attack Surface Mapper (pvm-asm-01).

Cross-correlates Forward Networks NQE device inventory with Nessus/ACAS scan
findings and CVE advisory data to produce a unified attack surface map stored
in nc_attack_surface.

Public API
----------
map_attack_surface(network_id)  → summary dict
get_attack_surface(...)          → list[dict] from nc_attack_surface
get_surface_summary()            → aggregate counts

CLI
---
python tools/network/attack_surface_mapper.py --map [--network-id <id>] --json
python tools/network/attack_surface_mapper.py --surface [--cve CVE-...] [--device name] --json
python tools/network/attack_surface_mapper.py --summary --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.network.db.init_db import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Criticality / score helpers
# ---------------------------------------------------------------------------

def _criticality_from_cvss(cvss: float | None) -> int:
    if cvss is None:
        return 3
    if cvss >= 9.0:
        return 5
    if cvss >= 7.0:
        return 4
    if cvss >= 5.0:
        return 3
    if cvss >= 3.0:
        return 2
    return 1


def _surface_score(cvss: float | None, reachable: bool, bgp_exposed: bool) -> float:
    cvss_norm = (cvss or 0.0) / 10.0
    score = cvss_norm * 0.5 + (1.0 if reachable else 0.0) * 0.3 + (1.0 if bgp_exposed else 0.0) * 0.2
    return round(min(1.0, max(0.0, score)), 4)


def _exposure_type(bgp_exposed: bool, plugin_name: str | None) -> str:
    if bgp_exposed:
        return "network"
    if not plugin_name:
        return "unknown"
    pn = plugin_name.lower()
    if "local" in pn:
        return "local"
    if "network" in pn or "remote" in pn:
        return "network"
    return "combined"


# ---------------------------------------------------------------------------
# NQE query helpers
# ---------------------------------------------------------------------------

def _run_nqe_queries(network_id: str | None) -> dict[str, Any]:
    """Run the three device/interface/BGP NQL queries. Returns raw results."""
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
    except Exception as exc:
        logger.warning("FallbackNQEClient unavailable: %s", exc)
        return {"devices": [], "interfaces": [], "bgp_down": [], "source": "unavailable"}

    results: dict[str, Any] = {"devices": [], "interfaces": [], "bgp_down": [], "source": ""}

    for key, nql in [
        ("devices", "network.devices[config]"),
        ("interfaces", "network.interfaces[ip]"),
        ("bgp_down", "network.bgp.sessions[down]"),
    ]:
        try:
            r = client.run_query(nql, network_id)
            results[key] = r.get("rows") or []
            if not results["source"]:
                results["source"] = r.get("source", "unknown")
        except Exception as exc:
            logger.warning("NQE query '%s' failed: %s", nql, exc)
            results[key] = []

    return results


def _build_device_map(nqe: dict[str, Any]) -> dict[str, dict]:
    """Build {ip → device_info} from NQE results."""
    device_map: dict[str, dict] = {}

    reachable_ips: set[str] = set()
    for iface in nqe.get("interfaces", []):
        ip = iface.get("ip") or iface.get("managementIp") or ""
        if ip:
            reachable_ips.add(ip.split("/")[0])  # strip prefix length

    bgp_down_ips: set[str] = set()
    for sess in nqe.get("bgp_down", []):
        peer = sess.get("peerAddress") or sess.get("neighbor") or ""
        if peer:
            bgp_down_ips.add(peer)

    for dev in nqe.get("devices", []):
        ip = (dev.get("managementIp") or dev.get("management", {}).get("ip") or "").split("/")[0]
        name = dev.get("name") or dev.get("hostname") or ip or "unknown"
        if not ip and not name:
            continue
        device_map[ip or name] = {
            "name": name,
            "ip": ip,
            "platform": dev.get("platform", {}).get("ostype") or dev.get("platform.ostype") or "",
            "os_version": dev.get("platform", {}).get("osversion") or dev.get("platform.osversion") or "",
            "location": dev.get("location") or "",
            "reachable": ip in reachable_ips if ip else False,
            "bgp_exposed": ip in bgp_down_ips if ip else False,
        }

    return device_map


# ---------------------------------------------------------------------------
# Nessus scan helpers
# ---------------------------------------------------------------------------

def _get_latest_scan_id(conn) -> str | None:
    try:
        row = conn.execute(
            "SELECT id FROM nc_vuln_scans ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


def _advisories_by_cve(conn) -> dict[str, dict]:
    """Return {cve_id: advisory_row} for all open advisories."""
    out: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT * FROM nc_advisories WHERE status NOT IN ('closed')"
        ).fetchall()
        for r in rows:
            row = dict(r)
            cve = row.get("cve_id", "")
            if cve:
                out[cve] = row
    except Exception as exc:
        logger.warning("Could not load advisories: %s", exc)
    return out


# ---------------------------------------------------------------------------
# UPSERT into nc_attack_surface
# ---------------------------------------------------------------------------

def _upsert_surface_row(conn, row: dict) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO nc_attack_surface
           (id, device_id, device_name, ip, cve_id, advisory_id, exposure_type,
            reachable, criticality, surface_score, nqe_source, nessus_scan_id,
            assessed_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(device_name, cve_id) DO UPDATE SET
             ip=excluded.ip,
             advisory_id=excluded.advisory_id,
             exposure_type=excluded.exposure_type,
             reachable=excluded.reachable,
             criticality=excluded.criticality,
             surface_score=excluded.surface_score,
             nqe_source=excluded.nqe_source,
             nessus_scan_id=excluded.nessus_scan_id,
             assessed_at=excluded.assessed_at,
             updated_at=excluded.updated_at""",
        (
            str(uuid.uuid4()),
            row.get("device_id", ""),
            row["device_name"],
            row.get("ip", ""),
            row["cve_id"],
            row.get("advisory_id"),
            row.get("exposure_type", "unknown"),
            1 if row.get("reachable") else 0,
            row.get("criticality", 3),
            row.get("surface_score", 0.0),
            row.get("nqe_source", "local_mapping"),
            row.get("nessus_scan_id"),
            now,
            now,
        ),
    )


def _append_audit_log(conn, action: str, input_text: str, nqe_source: str) -> None:
    try:
        conn.execute(
            """INSERT INTO nc_nqe_audit_log
               (action, input_text, data_source, created_at)
               VALUES (%s,%s,%s,%s)""",
            (action, input_text, nqe_source, _now()),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # audit log may not exist yet
        logger.warning("_append_audit_log: best-effort INSERT into nc_nqe_audit_log failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Core mapping logic
# ---------------------------------------------------------------------------

def _process_nessus_findings(
    conn,
    device_map: dict[str, dict],
    advisories: dict[str, dict],
    scan_id: str | None,
    nqe_source: str,
) -> int:
    """Map Nessus findings to devices, upsert rows. Returns count written."""
    if not scan_id:
        return 0

    from tools.network.vuln_overlay import get_host_findings

    count = 0
    for ip_or_key, dev in device_map.items():
        ip = dev.get("ip") or ip_or_key
        if not ip:
            continue
        try:
            findings = get_host_findings(conn, scan_id, ip, limit=100)
        except Exception:
            findings = []

        for f in findings:
            raw_cve = f.get("cve_id") or f.get("cve") or ""
            cve_list = [c.strip() for c in raw_cve.split(",") if c.strip().startswith("CVE-")]
            if not cve_list:
                continue

            for cve_id in cve_list:
                adv = advisories.get(cve_id)
                cvss = float(adv["cvss_score"]) if adv and adv.get("cvss_score") else None
                row = {
                    "device_id": dev.get("platform", ""),
                    "device_name": dev["name"],
                    "ip": ip,
                    "cve_id": cve_id,
                    "advisory_id": adv["id"] if adv else None,
                    "exposure_type": _exposure_type(dev["bgp_exposed"], f.get("plugin_name")),
                    "reachable": dev["reachable"],
                    "criticality": _criticality_from_cvss(cvss),
                    "surface_score": _surface_score(cvss, dev["reachable"], dev["bgp_exposed"]),
                    "nqe_source": nqe_source,
                    "nessus_scan_id": int(scan_id) if scan_id and scan_id.isdigit() else None,
                }
                _upsert_surface_row(conn, row)
                count += 1

    return count


def _process_advisory_model_match(
    conn,
    device_map: dict[str, dict],
    advisories: dict[str, dict],
    nqe_source: str,
) -> int:
    """Match advisories to devices by affected_models_json substring. Returns count."""
    count = 0
    device_names_lower = {
        dev["name"].lower(): (ip_key, dev)
        for ip_key, dev in device_map.items()
        if dev.get("name")
    }

    for cve_id, adv in advisories.items():
        try:
            affected_models = json.loads(adv.get("affected_models_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            affected_models = []

        if not affected_models:
            continue

        cvss = float(adv["cvss_score"]) if adv.get("cvss_score") else None

        for model in affected_models:
            model_lower = model.lower()
            for dev_name_lower, (ip_key, dev) in device_names_lower.items():
                if model_lower in dev_name_lower or dev_name_lower in model_lower:
                    row = {
                        "device_id": dev.get("platform", ""),
                        "device_name": dev["name"],
                        "ip": dev.get("ip", ""),
                        "cve_id": cve_id,
                        "advisory_id": adv["id"],
                        "exposure_type": _exposure_type(dev["bgp_exposed"], None),
                        "reachable": dev["reachable"],
                        "criticality": _criticality_from_cvss(cvss),
                        "surface_score": _surface_score(cvss, dev["reachable"], dev["bgp_exposed"]),
                        "nqe_source": nqe_source,
                        "nessus_scan_id": None,
                    }
                    _upsert_surface_row(conn, row)
                    count += 1

    return count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def map_attack_surface(network_id: str | None = None) -> dict:
    """Run full attack surface mapping pass.

    1. Queries NQE for device/interface/BGP topology.
    2. Correlates with Nessus findings from latest scan.
    3. Matches open advisories to devices by model name.
    4. Writes results to nc_attack_surface.
    """
    started = _now()
    nqe = _run_nqe_queries(network_id)
    device_map = _build_device_map(nqe)
    nqe_source = nqe.get("source", "local_mapping") or "local_mapping"

    conn = _conn()
    try:
        advisories = _advisories_by_cve(conn)
        scan_id = _get_latest_scan_id(conn)

        nessus_count = _process_nessus_findings(conn, device_map, advisories, scan_id, nqe_source)
        model_count = _process_advisory_model_match(conn, device_map, advisories, nqe_source)
        conn.commit()

        total = nessus_count + model_count
        _append_audit_log(conn, "run", f"attack_surface_mapper network_id={network_id}", nqe_source)
        conn.commit()

        summary = {
            "success": True,
            "devices_queried": len(device_map),
            "advisories_checked": len(advisories),
            "nessus_rows_written": nessus_count,
            "model_match_rows_written": model_count,
            "total_rows_written": total,
            "nqe_source": nqe_source,
            "scan_id": scan_id,
            "started_at": started,
            "completed_at": _now(),
        }
        logger.info("attack_surface_mapper: %d rows written", total)
        return summary
    except Exception as exc:
        logger.error("map_attack_surface failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def get_attack_surface(
    cve_id: str | None = None,
    device_name: str | None = None,
    min_score: float = 0.0,
    limit: int = 500,
) -> list[dict]:
    """Query nc_attack_surface with optional filters."""
    conn = _conn()
    try:
        sql = "SELECT * FROM nc_attack_surface WHERE surface_score >= ?"
        params: list = [min_score]
        if cve_id:
            sql += " AND cve_id = ?"
            params.append(cve_id)
        if device_name:
            sql += " AND device_name LIKE ?"
            params.append(f"%{device_name}%")
        sql += " ORDER BY surface_score DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("get_attack_surface failed: %s", exc)
        return []
    finally:
        conn.close()


def get_surface_summary() -> dict:
    """Return aggregate counts from nc_attack_surface."""
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM nc_attack_surface").fetchone()[0]
        reachable = conn.execute(
            "SELECT COUNT(*) FROM nc_attack_surface WHERE reachable=1"
        ).fetchone()[0]
        critical = conn.execute(
            "SELECT COUNT(*) FROM nc_attack_surface WHERE criticality=5"
        ).fetchone()[0]
        by_crit: dict[str, int] = {}
        for row in conn.execute(
            "SELECT criticality, COUNT(*) FROM nc_attack_surface GROUP BY criticality"
        ).fetchall():
            by_crit[str(row[0])] = row[1]
        return {
            "total_entries": total,
            "reachable_count": reachable,
            "critical_count": critical,
            "by_criticality": by_crit,
        }
    except Exception as exc:
        logger.error("get_surface_summary failed: %s", exc)
        return {"total_entries": 0, "reachable_count": 0, "critical_count": 0, "by_criticality": {}}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="PVM Attack Surface Mapper")
    parser.add_argument("--map", action="store_true", help="Run full mapping pass")
    parser.add_argument("--network-id", default=None, help="Forward Networks network ID")
    parser.add_argument("--surface", action="store_true", help="Query attack surface")
    parser.add_argument("--cve", default=None, help="Filter by CVE ID")
    parser.add_argument("--device", default=None, help="Filter by device name")
    parser.add_argument("--min-score", type=float, default=0.0, help="Minimum surface_score filter")
    parser.add_argument("--summary", action="store_true", help="Get aggregate summary")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if args.map:
        result = map_attack_surface(network_id=args.network_id)
    elif args.surface:
        result = get_attack_surface(
            cve_id=args.cve,
            device_name=args.device,
            min_score=args.min_score,
        )
    elif args.summary:
        result = get_surface_summary()
    else:
        parser.print_help()
        sys.exit(0)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)


if __name__ == "__main__":
    _main()
