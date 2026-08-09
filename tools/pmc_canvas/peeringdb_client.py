# CUI // SP-CTI
"""PeeringDB client — fetches ASN, IX, and network data.

Uses stdlib urllib.request only. Responses cached locally for 24h.
PeeringDB API v2: https://www.peeringdb.com/api/
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

_CACHE_DIR = Path(__file__).resolve().parents[3] / ".tmp" / "peeringdb_cache"
_CACHE_TTL = 24 * 3600  # 24 hours
_BASE_URL = "https://www.peeringdb.com/api"
_TIMEOUT = 10


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = key.replace("/", "_").replace("?", "_").replace("&", "_")
    return _CACHE_DIR / f"{safe}.json"


def _cache_get(key: str) -> dict | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > _CACHE_TTL:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(key: str, data: dict) -> None:
    try:
        _cache_path(key).write_text(json.dumps(data), encoding="utf-8", newline="")
    except Exception:
        pass


def _fetch(endpoint: str, params: dict | None = None) -> dict:
    """Fetch from PeeringDB API with caching."""
    qs = ""
    if params:
        qs = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    cache_key = endpoint + qs
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{_BASE_URL}/{endpoint}{qs}"
    headers = {"Accept": "application/json", "User-Agent": "ICDEV-PMC/1.0"}

    # Optional auth token from env
    api_key = os.environ.get("PEERINGDB_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _cache_set(cache_key, data)
        return data
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"PeeringDB HTTP {exc.code} for {url}") from exc
    except Exception as exc:
        raise RuntimeError(f"PeeringDB fetch failed: {exc}") from exc


def get_asn_info(asn: int) -> dict[str, Any]:
    """Fetch network record for an ASN from PeeringDB."""
    data = _fetch("net", {"asn": asn})
    nets = data.get("data", [])
    if not nets:
        return {}
    net = nets[0]
    return {
        "asn": net.get("asn"),
        "org_name": net.get("name", ""),
        "website": net.get("website", ""),
        "noc_email": net.get("poc_set", [{}])[0].get("email", "") if net.get("poc_set") else "",
        "irr_as_set": net.get("irr_as_set", ""),
        "info_prefixes4": net.get("info_prefixes4", 0),
        "info_prefixes6": net.get("info_prefixes6", 0),
        "policy_general": net.get("policy_general", "selective"),
        "peeringdb_net_id": net.get("id"),
        "rir": _infer_rir(net.get("org", {}).get("name", "")),
        "ix_count": len(net.get("netixlan_set", [])),
    }


def search_peers_at_ix(ix_id: int, our_asn: int | None = None) -> list[dict]:
    """Return networks present at a given IX (optionally excluding our ASN)."""
    data = _fetch("netixlan", {"ixlan_id": ix_id})
    results = []
    for entry in data.get("data", []):
        asn = entry.get("asn")
        if our_asn and asn == our_asn:
            continue
        results.append({
            "asn": asn,
            "ipaddr4": entry.get("ipaddr4", ""),
            "ipaddr6": entry.get("ipaddr6", ""),
            "speed": entry.get("speed", 0),
            "is_rs_peer": entry.get("is_rs_peer", False),
        })
    return results


def get_ix_info(ix_id: int) -> dict[str, Any]:
    """Fetch IX (Internet Exchange) details."""
    data = _fetch(f"ix/{ix_id}")
    ix = data.get("data", [{}])[0] if isinstance(data.get("data"), list) else data.get("data", {})
    return {
        "peeringdb_ix_id": ix.get("id"),
        "ix_name": ix.get("name", ""),
        "city": ix.get("city", ""),
        "country": ix.get("country", ""),
        "website": ix.get("website", ""),
        "tech_email": ix.get("tech_email", ""),
    }


def sync_peer_from_peeringdb(asn: int, conn) -> dict[str, Any]:
    """Fetch PeeringDB data for ASN and upsert into peering_peers.

    Returns the merged peer dict.
    """
    info = get_asn_info(asn)
    if not info:
        raise ValueError(f"ASN {asn} not found in PeeringDB")

    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    # Check if peer exists
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM peering_peers WHERE asn = %s", (asn,))
    except Exception:
        cur = conn.cursor()
        cur.execute("SELECT id FROM peering_peers WHERE asn = %s", (asn,))

    row = cur.fetchone()
    peer_id = row[0] if row else str(uuid.uuid4())

    data = {
        "id": peer_id,
        "asn": asn,
        "org_name": info.get("org_name", ""),
        "noc_email": info.get("noc_email", ""),
        "irr_as_set": info.get("irr_as_set", ""),
        "ipv4_prefix_count": info.get("info_prefixes4", 0),
        "ipv6_prefix_count": info.get("info_prefixes6", 0),
        "peeringdb_net_id": info.get("peeringdb_net_id"),
        "rir": info.get("rir", "UNKNOWN"),
        "peeringdb_synced_at": now,
        "updated_at": now,
    }

    if row:
        try:
            conn.cursor().execute(
                """UPDATE peering_peers SET org_name=%s, noc_email=%s, irr_as_set=%s,
                   ipv4_prefix_count=%s, ipv6_prefix_count=%s, peeringdb_net_id=%s,
                   rir=%s, peeringdb_synced_at=%s, updated_at=%s WHERE asn=%s""",
                (data["org_name"], data["noc_email"], data["irr_as_set"],
                 data["ipv4_prefix_count"], data["ipv6_prefix_count"], data["peeringdb_net_id"],
                 data["rir"], now, now, asn),
            )
        except Exception:
            conn.execute(
                """UPDATE peering_peers SET org_name=%s, noc_email=%s, irr_as_set=%s,
                   ipv4_prefix_count=%s, ipv6_prefix_count=%s, peeringdb_net_id=%s,
                   rir=%s, peeringdb_synced_at=%s, updated_at=%s WHERE asn=%s""",
                (data["org_name"], data["noc_email"], data["irr_as_set"],
                 data["ipv4_prefix_count"], data["ipv6_prefix_count"], data["peeringdb_net_id"],
                 data["rir"], now, now, asn),
            )
    else:
        data["status"] = "evaluation"
        data["peer_type"] = "public"
        data["policy"] = "selective"
        data["created_at"] = now
        try:
            conn.cursor().execute(
                """INSERT INTO peering_peers (id,asn,org_name,peer_type,policy,status,
                   noc_email,irr_as_set,ipv4_prefix_count,ipv6_prefix_count,
                   peeringdb_net_id,rir,peeringdb_synced_at,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (peer_id, asn, data["org_name"], "public", "selective", "evaluation",
                 data["noc_email"], data["irr_as_set"], data["ipv4_prefix_count"],
                 data["ipv6_prefix_count"], data["peeringdb_net_id"], data["rir"],
                 now, now, now),
            )
        except Exception:
            conn.execute(
                """INSERT INTO peering_peers (id,asn,org_name,peer_type,policy,status,
                   noc_email,irr_as_set,ipv4_prefix_count,ipv6_prefix_count,
                   peeringdb_net_id,rir,peeringdb_synced_at,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (peer_id, asn, data["org_name"], "public", "selective", "evaluation",
                 data["noc_email"], data["irr_as_set"], data["ipv4_prefix_count"],
                 data["ipv6_prefix_count"], data["peeringdb_net_id"], data["rir"],
                 now, now, now),
            )

    try:
        conn.commit()
    except Exception:
        pass

    return data


def _infer_rir(org_name: str) -> str:
    name = org_name.upper()
    if "RIPE" in name:
        return "RIPE"
    if "APNIC" in name:
        return "APNIC"
    if "LACNIC" in name:
        return "LACNIC"
    if "AFRINIC" in name:
        return "AFRINIC"
    if "ARIN" in name:
        return "ARIN"
    return "UNKNOWN"
