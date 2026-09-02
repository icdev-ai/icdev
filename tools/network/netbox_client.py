# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Network Canvas — NetBox REST API Client.

Pulls device inventories, IP allocations, rack layouts, VLANs, and circuits
from a NetBox instance and pushes canvas changes back.

Usage:
    python tools/network/netbox_client.py --url http://netbox:8000 --token <token> --test
    python tools/network/netbox_client.py --url http://netbox:8000 --token <token> --pull devices --json
    python tools/network/netbox_client.py --url http://netbox:8000 --token <token> --pull all --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.network.netbox")

# NetBox device role → canvas node type mapping
_ROLE_TO_TYPE: dict[str, str] = {
    "router": "router",
    "firewall": "firewall",
    "switch": "switch-l3",
    "leaf": "switch-l3",
    "spine": "switch-l3",
    "access-switch": "switch-l2",
    "distribution-switch": "switch-l3",
    "core-switch": "switch-l3",
    "server": "server",
    "load-balancer": "load-balancer",
    "access-point": "wap",
    "wireless-controller": "wlc",
    "siem": "siem",
    "network-tap": "network-tap",
    "patch-panel": "patch-panel",
}

# NetBox circuit type → canvas circuit_type
_CIRCUIT_TYPE_MAP: dict[str, str] = {
    "Internet Transit": "internet",
    "MPLS": "mpls",
    "Dark Fiber": "dark-fiber",
    "Direct Connect": "direct-connect",
    "Broadband": "broadband",
    "Dedicated Internet Access": "dia",
    "Colocation": "colo",
}


class NetBoxError(Exception):
    """Raised when NetBox API call fails."""


class NetBoxClient:
    """Thin synchronous client for the NetBox REST API (v3.x / v4.x).

    NetBox's API uses token authentication and returns paginated JSON.
    All paginated results are collected automatically.
    """

    def __init__(self, url: str, token: str, timeout: int = 15, verify_ssl: bool = True):
        self.base_url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    # ── Low-level HTTP ────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            # urllib doesn't support ssl context override easily on all platforms;
            # we trust the caller to validate connectivity via test_connection first
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — URL is admin-configured NetBox endpoint
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:400]
            raise NetBoxError(f"HTTP {exc.code} {exc.reason}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise NetBoxError(f"Connection failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise NetBoxError(f"Invalid JSON response: {exc}") from exc

    def _get_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        """Collect all pages from a NetBox list endpoint."""
        results: list[dict] = []
        params = dict(params or {})
        params.setdefault("limit", 100)
        url_path = path + "?" + urllib.parse.urlencode(params)
        while url_path:
            data = self._request("GET", url_path)
            results.extend(data.get("results", []))
            next_url: str | None = data.get("next")
            if next_url:
                # next_url is absolute — strip base so _request can prefix it
                url_path = next_url[len(self.base_url) :]
            else:
                url_path = None  # type: ignore[assignment]
        return results

    # ── Connection test ───────────────────────────────────────────────────────

    def test_connection(self) -> dict:
        """Verify connectivity and token. Returns NetBox version info."""
        data = self._request("GET", "/api/")
        return {
            "ok": True,
            "netbox_version": data.get("netbox-version", "unknown"),
            "url": self.base_url,
        }

    # ── Pull: Devices ─────────────────────────────────────────────────────────

    def get_devices(self, site: str | None = None) -> list[dict]:
        """Pull all devices (optionally filtered by site slug)."""
        params: dict[str, Any] = {}
        if site:
            params["site"] = site
        raw = self._get_paginated("/api/dcim/devices/", params)
        return [self._map_device(d) for d in raw]

    def get_devices_raw(self, site: str | None = None) -> list[dict]:
        """Pull devices UNMAPPED, exactly as NetBox returned them.

        ``get_devices`` maps to the canvas-node shape and drops
        ``device_type.manufacturer`` / ``device_type.model`` — the two fields
        the asset-discovery adapter and the de-facto standard learner both
        need. Callers that want the canvas shape keep using ``get_devices``;
        this is purely additive and changes nothing for them (rmf-disc-01).
        """
        params: dict[str, Any] = {}
        if site:
            params["site"] = site
        return self._get_paginated("/api/dcim/devices/", params)

    def _map_device(self, d: dict) -> dict:
        """Convert NetBox device object to canvas node format."""
        role_slug = ""
        if d.get("role"):
            role_slug = d["role"].get("slug", "") or d["role"].get("name", "").lower()
        elif d.get("device_role"):  # v3 compat
            role_slug = d["device_role"].get("slug", "") or d["device_role"].get("name", "").lower()
        node_type = _ROLE_TO_TYPE.get(role_slug, "server")
        primary_ip = ""
        if d.get("primary_ip"):
            primary_ip = d["primary_ip"].get("address", "").split("/")[0]
        site_name = ""
        if d.get("site"):
            site_name = d["site"].get("name", "")
        rack = ""
        if d.get("rack"):
            rack = d["rack"].get("name", "")
        return {
            "netbox_id": d["id"],
            "netbox_url": d.get("url", ""),
            "label": d.get("name", f"device-{d['id']}"),
            "type": node_type,
            "ip": primary_ip,
            "site": site_name,
            "rack": rack,
            "platform": (d.get("platform") or {}).get("name", ""),
            "status": (d.get("status") or {}).get("value", "active"),
            "serial": d.get("serial", ""),
            "tags": [t.get("name", "") for t in (d.get("tags") or [])],
        }

    # ── Pull: IP Addresses ────────────────────────────────────────────────────

    def get_ip_addresses(self, vrf: str | None = None) -> list[dict]:
        """Pull all IP addresses."""
        params: dict[str, Any] = {}
        if vrf:
            params["vrf"] = vrf
        raw = self._get_paginated("/api/ipam/ip-addresses/", params)
        return [self._map_ip(ip) for ip in raw]

    def _map_ip(self, ip: dict) -> dict:
        assigned = ip.get("assigned_object") or {}
        device_name = ""
        if assigned.get("device"):
            device_name = assigned["device"].get("name", "")
        vrf_name = ""
        if ip.get("vrf"):
            vrf_name = ip["vrf"].get("name", "global")
        return {
            "netbox_id": ip["id"],
            "address": ip.get("address", ""),
            "status": (ip.get("status") or {}).get("value", "active"),
            "role": (ip.get("role") or {}).get("value", ""),
            "vrf": vrf_name,
            "device": device_name,
            "description": ip.get("description", ""),
            "tags": [t.get("name", "") for t in (ip.get("tags") or [])],
        }

    # ── Pull: VLANs ───────────────────────────────────────────────────────────

    def get_vlans(self, site: str | None = None) -> list[dict]:
        """Pull all VLANs."""
        params: dict[str, Any] = {}
        if site:
            params["site"] = site
        raw = self._get_paginated("/api/ipam/vlans/", params)
        return [self._map_vlan(v) for v in raw]

    def _map_vlan(self, v: dict) -> dict:
        site_name = (v.get("site") or {}).get("name", "")
        group_name = (v.get("group") or {}).get("name", "")
        return {
            "netbox_id": v["id"],
            "vid": v.get("vid", 0),
            "name": v.get("name", f"VLAN-{v.get('vid', v['id'])}"),
            "status": (v.get("status") or {}).get("value", "active"),
            "site": site_name,
            "group": group_name,
            "description": v.get("description", ""),
            "prefix_count": v.get("prefix_count", 0),
            "tags": [t.get("name", "") for t in (v.get("tags") or [])],
        }

    # ── Pull: Prefixes ────────────────────────────────────────────────────────

    def get_prefixes(self, vrf: str | None = None) -> list[dict]:
        """Pull all IP prefixes (subnets)."""
        params: dict[str, Any] = {}
        if vrf:
            params["vrf"] = vrf
        raw = self._get_paginated("/api/ipam/prefixes/", params)
        return [self._map_prefix(p) for p in raw]

    def _map_prefix(self, p: dict) -> dict:
        vrf_name = (p.get("vrf") or {}).get("name", "global")
        site_name = (p.get("site") or {}).get("name", "")
        vlan_id = None
        if p.get("vlan"):
            vlan_id = p["vlan"].get("vid")
        return {
            "netbox_id": p["id"],
            "prefix": p.get("prefix", ""),
            "status": (p.get("status") or {}).get("value", "active"),
            "role": (p.get("role") or {}).get("name", ""),
            "vrf": vrf_name,
            "site": site_name,
            "vlan_id": vlan_id,
            "description": p.get("description", ""),
            "is_pool": p.get("is_pool", False),
            "utilization": p.get("utilization", 0),
        }

    # ── Pull: Racks ───────────────────────────────────────────────────────────

    def get_racks(self, site: str | None = None) -> list[dict]:
        """Pull rack layouts."""
        params: dict[str, Any] = {}
        if site:
            params["site"] = site
        raw = self._get_paginated("/api/dcim/racks/", params)
        return [self._map_rack(r) for r in raw]

    def _map_rack(self, r: dict) -> dict:
        site_name = (r.get("site") or {}).get("name", "")
        location = (r.get("location") or {}).get("name", "")
        return {
            "netbox_id": r["id"],
            "name": r.get("name", f"rack-{r['id']}"),
            "site": site_name,
            "location": location,
            "u_height": r.get("u_height", 42),
            "u_count": r.get("u_count", 0),
            "device_count": r.get("device_count", 0),
            "status": (r.get("status") or {}).get("value", "active"),
            "serial": r.get("serial", ""),
            "tags": [t.get("name", "") for t in (r.get("tags") or [])],
        }

    # ── Pull: Circuits ────────────────────────────────────────────────────────

    def get_circuits(self, provider: str | None = None) -> list[dict]:
        """Pull circuits."""
        params: dict[str, Any] = {}
        if provider:
            params["provider"] = provider
        raw = self._get_paginated("/api/circuits/circuits/", params)
        return [self._map_circuit(c) for c in raw]

    def _map_circuit(self, c: dict) -> dict:
        provider_name = (c.get("provider") or {}).get("name", "")
        ctype = (c.get("type") or {}).get("name", "")
        term_a = c.get("termination_a") or {}
        term_z = c.get("termination_z") or {}
        site_a = (term_a.get("site") or {}).get("name", "")
        site_z = (term_z.get("site") or {}).get("name", "")
        return {
            "netbox_id": c["id"],
            "circuit_id": c.get("cid", f"CKT-{c['id']}"),
            "provider": provider_name,
            "circuit_type": _CIRCUIT_TYPE_MAP.get(ctype, ctype.lower().replace(" ", "-")),
            "status": (c.get("status") or {}).get("value", "active"),
            "description": c.get("description", ""),
            "commit_rate_kbps": c.get("commit_rate"),
            "site_a": site_a,
            "site_z": site_z,
            "tags": [t.get("name", "") for t in (c.get("tags") or [])],
        }

    # ── Push: Create/update device ────────────────────────────────────────────

    def push_device(self, node: dict, site_id: int | None = None) -> dict:
        """Create or update a device in NetBox from a canvas node.

        If node has netbox_id, issues a PATCH. Otherwise creates via POST.
        Returns the NetBox device object.
        """
        payload: dict[str, Any] = {
            "name": node.get("label", "unnamed"),
        }
        if site_id:
            payload["site"] = site_id
        if node.get("netbox_type"):
            payload["device_type"] = {"model": node["netbox_type"]}
        netbox_id = node.get("netbox_id")
        if netbox_id:
            result = self._request("PATCH", f"/api/dcim/devices/{netbox_id}/", payload)
        else:
            result = self._request("POST", "/api/dcim/devices/", payload)
        return result

    # ── Bulk pull ─────────────────────────────────────────────────────────────

    def pull_all(self, site: str | None = None) -> dict:
        """Pull all resource types in one call. Returns dict keyed by type."""
        return {
            "devices": self.get_devices(site=site),
            "ip_addresses": self.get_ip_addresses(),
            "vlans": self.get_vlans(site=site),
            "prefixes": self.get_prefixes(),
            "racks": self.get_racks(site=site),
            "circuits": self.get_circuits(),
            "pulled_at": datetime.now(timezone.utc).isoformat(),
        }


# ── Canvas graph builder ───────────────────────────────────────────────────────


def devices_to_canvas_nodes(devices: list[dict], start_x: int = 100, start_y: int = 100) -> list[dict]:
    """Convert NetBox device list to canvas node list for graph_json."""
    nodes = []
    col_width, row_height = 200, 120
    for i, dev in enumerate(devices):
        col = i % 5
        row = i // 5
        node = {
            "id": f"nb-{dev['netbox_id']}",
            "label": dev["label"],
            "type": dev["type"],
            "x": start_x + col * col_width,
            "y": start_y + row * row_height,
            "ip": dev.get("ip", ""),
            "netbox_id": dev["netbox_id"],
            "netbox_url": dev.get("netbox_url", ""),
            "site": dev.get("site", ""),
            "rack": dev.get("rack", ""),
        }
        nodes.append(node)
    return nodes


def prefixes_to_ipam_blocks(prefixes: list[dict]) -> list[dict]:
    """Convert NetBox prefixes to nc_ipam_blocks rows."""
    import uuid

    blocks = []
    for p in prefixes:
        vlan_id = p.get("vlan_id")
        blocks.append(
            {
                "id": str(uuid.uuid4()),
                "network": p["prefix"],
                "vlan_id": vlan_id,
                "vrf": p.get("vrf", "global"),
                "description": p.get("description", ""),
                "site_id": None,
            }
        )
    return blocks


def circuits_to_nc_circuits(circuits: list[dict], topology_id: str) -> list[dict]:
    """Convert NetBox circuits to nc_circuits rows."""
    import uuid

    rows = []
    for c in circuits:
        bw = ""
        if c.get("commit_rate_kbps"):
            kbps = c["commit_rate_kbps"]
            if kbps >= 1_000_000:
                bw = f"{kbps // 1_000_000}Gbps"
            elif kbps >= 1_000:
                bw = f"{kbps // 1_000}Mbps"
            else:
                bw = f"{kbps}Kbps"
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "topology_id": topology_id,
                "circuit_id": c["circuit_id"],
                "carrier": c.get("provider", ""),
                "circuit_type": c.get("circuit_type", ""),
                "bandwidth": bw,
                "handoff_a": c.get("site_a", ""),
                "handoff_z": c.get("site_z", ""),
                "install_status": c.get("status", "active"),
            }
        )
    return rows


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NetBox IPAM client for ICDEV Network Canvas")
    parser.add_argument("--url", required=True, help="NetBox base URL (e.g. http://netbox:8000)")
    parser.add_argument("--token", required=True, help="NetBox API token")
    parser.add_argument("--test", action="store_true", help="Test connection only")
    parser.add_argument(
        "--pull", choices=["devices", "ips", "vlans", "prefixes", "racks", "circuits", "all"], help="Pull resource type"
    )
    parser.add_argument("--site", default=None, help="Filter by site slug")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if connection fails")
    args = parser.parse_args()

    client = NetBoxClient(url=args.url, token=args.token)

    try:
        if args.test or not args.pull:
            result = client.test_connection()
            if args.json:
                print(json.dumps(result))
            else:
                print(f"[netbox_client] Connected to {result['url']} — NetBox {result['netbox_version']}")
            sys.exit(0)

        pull_map = {
            "devices": lambda: client.get_devices(site=args.site),
            "ips": lambda: client.get_ip_addresses(),
            "vlans": lambda: client.get_vlans(site=args.site),
            "prefixes": lambda: client.get_prefixes(),
            "racks": lambda: client.get_racks(site=args.site),
            "circuits": lambda: client.get_circuits(),
            "all": lambda: client.pull_all(site=args.site),
        }
        data = pull_map[args.pull]()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            count = len(data) if isinstance(data, list) else sum(len(v) for v in data.values() if isinstance(v, list))
            print(f"[netbox_client] Pulled {count} {args.pull} records from {args.url}")

    except NetBoxError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"[netbox_client] ERROR: {exc}", file=sys.stderr)
        sys.exit(1 if args.gate else 0)
