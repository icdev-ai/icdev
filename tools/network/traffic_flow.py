# CUI // SP-CTI — NDC Traffic Flow Engine
"""DoD BCAP path traffic flow analysis and walkthrough generator.

Provides DOMAIN_ACTION_MAP, PROTOCOL_TABLE, DOMAIN_DEFAULTS constants and
TrafficFlowEngine for creating flows and generating hop-by-hop walkthroughs
across the on_prem → NIPR → BCAP-VDMS → BCAP-VDSS → CSP IL4/IL5 path.
"""
from __future__ import annotations

import json
import uuid
from typing import Any


# ── Domain action maps ────────────────────────────────────────────────────────
# (domain_type, app_type) -> ordered list of action_type strings per hop.

DOMAIN_ACTION_MAP: dict[tuple[str, str], list[str]] = {
    # on_prem — user/endpoint originates traffic
    ("on_prem", "sso_saml"):     ["authenticate", "redirect"],
    ("on_prem", "sso_oauth"):    ["authorize", "redirect"],
    ("on_prem", "api_rest"):     ["request", "egress"],
    ("on_prem", "ipsec_tunnel"): ["encapsulate", "egress"],
    ("on_prem", "bgp"):          ["advertise", "egress"],
    ("on_prem", "dns"):          ["query", "resolve"],
    # nipr — DISA NIPRNet boundary: route + stateful inspection
    ("nipr", "sso_saml"):        ["route", "inspect", "forward"],
    ("nipr", "sso_oauth"):       ["route", "inspect", "forward"],
    ("nipr", "api_rest"):        ["route", "inspect", "forward"],
    ("nipr", "ipsec_tunnel"):    ["encrypt", "route", "forward"],
    ("nipr", "bgp"):             ["route-advertisement", "peering", "forward"],
    ("nipr", "dns"):             ["resolve", "route", "forward"],
    # bcap_vdms — BCAP DMZ Management Service: proxy + NAT + load-balance
    ("bcap_vdms", "sso_saml"):   ["proxy", "nat", "load-balance"],
    ("bcap_vdms", "sso_oauth"):  ["proxy", "nat", "load-balance"],
    ("bcap_vdms", "api_rest"):   ["proxy", "nat", "load-balance"],
    ("bcap_vdms", "ipsec_tunnel"): ["decrypt", "nat", "proxy"],
    ("bcap_vdms", "bgp"):        ["peer", "route-reflect", "forward"],
    ("bcap_vdms", "dns"):        ["resolve", "cache", "forward"],
    # bcap_vdss — BCAP DMZ Security Service: deep inspection + CDM
    ("bcap_vdss", "sso_saml"):   ["tls-inspect", "idps-scan", "cdm-check", "forward"],
    ("bcap_vdss", "sso_oauth"):  ["tls-inspect", "idps-scan", "cdm-check", "forward"],
    ("bcap_vdss", "api_rest"):   ["tls-inspect", "idps-scan", "waf-filter", "forward"],
    ("bcap_vdss", "ipsec_tunnel"): ["idps-scan", "cdm-check", "forward"],
    ("bcap_vdss", "bgp"):        ["inspect", "route-filter", "forward"],
    ("bcap_vdss", "dns"):        ["dns-inspect", "rpz-filter", "forward"],
    # csp_il4 — Cloud IL4: MFA + PKI + cloud-native firewall
    ("csp_il4", "sso_saml"):     ["mfa-verify", "pki-validate", "app-deliver"],
    ("csp_il4", "sso_oauth"):    ["mfa-verify", "token-issue", "app-deliver"],
    ("csp_il4", "api_rest"):     ["mfa-verify", "api-gateway", "app-deliver"],
    ("csp_il4", "ipsec_tunnel"): ["terminate", "decrypt", "app-deliver"],
    ("csp_il4", "bgp"):          ["peer", "route-import", "app-deliver"],
    ("csp_il4", "dns"):          ["resolve", "filter", "app-deliver"],
    # csp_il5 — Cloud IL5: same as IL4 + FIPS-140-2 gate
    ("csp_il5", "sso_saml"):     ["mfa-verify", "pki-validate", "fips-check", "app-deliver"],
    ("csp_il5", "sso_oauth"):    ["mfa-verify", "token-issue", "fips-check", "app-deliver"],
    ("csp_il5", "api_rest"):     ["mfa-verify", "api-gateway", "fips-check", "app-deliver"],
    ("csp_il5", "ipsec_tunnel"): ["terminate", "decrypt", "fips-check", "app-deliver"],
    ("csp_il5", "bgp"):          ["peer", "route-import", "fips-check", "app-deliver"],
    ("csp_il5", "dns"):          ["resolve", "filter", "fips-check", "app-deliver"],
}

# ── Protocol table ────────────────────────────────────────────────────────────
# Default ports/protocols/services per application type.

PROTOCOL_TABLE: dict[str, list[dict[str, Any]]] = {
    "sso_saml": [
        {"port": 443, "protocol": "tcp", "service": "HTTPS/TLS"},
        {"port": 80,  "protocol": "tcp", "service": "HTTP-redirect"},
    ],
    "sso_oauth": [
        {"port": 443, "protocol": "tcp", "service": "HTTPS/TLS"},
    ],
    "api_rest": [
        {"port": 443,  "protocol": "tcp", "service": "HTTPS/TLS"},
        {"port": 8443, "protocol": "tcp", "service": "HTTPS-alt"},
    ],
    "ipsec_tunnel": [
        {"port": 500,  "protocol": "udp", "service": "IKEv2"},
        {"port": 4500, "protocol": "udp", "service": "NAT-T"},
        {"protocol": "esp", "service": "ESP-encrypt"},
    ],
    "bgp": [
        {"port": 179, "protocol": "tcp", "service": "BGP"},
    ],
    "dns": [
        {"port": 53, "protocol": "udp", "service": "DNS"},
        {"port": 53, "protocol": "tcp", "service": "DNS-TCP"},
    ],
}

# ── Domain defaults ───────────────────────────────────────────────────────────
# Default security_detail and network_detail per domain_type.

DOMAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "on_prem": {
        "inspection_type": "endpoint-AV",
        "encryption_required": False,
    },
    "nipr": {
        "inspection_type": "stateful-firewall",
        "bgp_as": "AS65000-DISA",
        "route_source": "BGP",
        "encryption_required": True,
    },
    "bcap_vdms": {
        "inspection_type": "proxy",
        "dns_resolution": "DISA-DNS",
        "nat_translate": True,
        "load_balance_algo": "round-robin",
    },
    "bcap_vdss": {
        "inspection_type": "TLS-inspection + IDPS + CDM",
        "cdm_sensor": True,
        "idps_enabled": True,
        "tls_version": "1.3",
        "waF": True,
    },
    "csp_il4": {
        "inspection_type": "cloud-native-firewall",
        "encryption_required": True,
        "mfa_required": True,
        "pki_required": "DoD-PKI",
    },
    "csp_il5": {
        "inspection_type": "cloud-native-firewall + SIEM",
        "encryption_required": True,
        "mfa_required": True,
        "pki_required": "DoD-PKI",
        "fips_140_2": True,
    },
}

# ── Domain type resolver ──────────────────────────────────────────────────────

def resolve_domain_type(node: dict) -> str:
    """Infer domain_type from node properties (type, label, config).

    Returns one of: on_prem, nipr, bcap_vdms, bcap_vdss, csp_il4, csp_il5.
    Falls back to 'on_prem' when no pattern matches.
    """
    label = (node.get("label") or "").lower()
    ntype = (node.get("type") or "").lower()
    config = node.get("config") or {}
    config_str = json.dumps(config).lower() if isinstance(config, dict) else str(config).lower()

    # VDSS must be checked before BCAP/VDMS (more specific)
    if "vdss" in label or "vdss" in ntype:
        return "bcap_vdss"
    if "vdms" in label or "vdms" in ntype:
        return "bcap_vdms"
    if "bcap" in label or "bcap" in ntype:
        return "bcap_vdms"

    # IL5 before IL4 (more restrictive)
    if "il5" in label or "il5" in ntype or "il5" in config_str:
        return "csp_il5"
    if any(k in ntype or k in label for k in ("aws", "azure", "gcp", "oci")):
        return "csp_il4"
    if "csp" in label or "csp" in ntype or "cloud" in ntype:
        return "csp_il4"

    if "nipr" in label or "nipr" in ntype:
        return "nipr"

    if any(k in label or k in ntype for k in ("on.prem", "on_prem", "workstation", "endpoint", "user")):
        return "on_prem"

    return "on_prem"


# ── TrafficFlowEngine ─────────────────────────────────────────────────────────

class TrafficFlowEngine:
    """Create, list, delete traffic flows and generate DoD hop walkthroughs."""

    def _ensure_tables(self, conn) -> None:
        """Create nc_ traffic flow tables if not yet initialised by init_db."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nc_traffic_flows (
                id TEXT PRIMARY KEY,
                topology_id TEXT NOT NULL,
                name TEXT NOT NULL,
                src_zone TEXT NOT NULL,
                dst_zone TEXT NOT NULL,
                app_type TEXT NOT NULL,
                classification TEXT DEFAULT 'CUI',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nc_security_domain_policies (
                id TEXT PRIMARY KEY,
                topology_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                domain_type TEXT NOT NULL,
                inspection_type TEXT,
                policy_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nc_flow_walkthrough_steps (
                id TEXT PRIMARY KEY,
                flow_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                node_label TEXT NOT NULL,
                action_type TEXT NOT NULL,
                security_detail TEXT DEFAULT '{}',
                network_detail TEXT DEFAULT '{}',
                narrative TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_flow(
        self,
        topology_id: str,
        name: str,
        src_zone: str,
        dst_zone: str,
        app_type: str,
        classification: str,
        conn,
    ) -> str:
        """Insert a new traffic flow record and return its UUID."""
        self._ensure_tables(conn)
        flow_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO nc_traffic_flows
               (id, topology_id, name, src_zone, dst_zone, app_type, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (flow_id, topology_id, name, src_zone, dst_zone, app_type, classification),
        )
        conn.commit()
        return flow_id

    def list_flows(self, topology_id: str, conn) -> list[dict]:
        """Return all flows for a topology as plain dicts."""
        self._ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM nc_traffic_flows WHERE topology_id = ? ORDER BY created_at",
            (topology_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_flow(self, flow_id: str, conn) -> bool:
        """Delete a flow and its walkthrough steps. Returns True if a row was deleted."""
        self._ensure_tables(conn)
        conn.execute(
            "DELETE FROM nc_flow_walkthrough_steps WHERE flow_id = ?", (flow_id,)
        )
        cur = conn.execute(
            "DELETE FROM nc_traffic_flows WHERE id = ?", (flow_id,)
        )
        conn.commit()
        deleted = getattr(cur, "rowcount", None)
        return bool(deleted and deleted > 0)

    # ── Walkthrough generation ────────────────────────────────────────────────

    def generate_walkthrough(self, flow_id: str, conn) -> list[dict]:
        """Build hop-by-hop walkthrough for a flow and persist it.

        Steps:
          1. Load flow from nc_traffic_flows
          2. Load topology graph_json; convert nodes list → dict by id
          3. BFS path via path_analyzer.find_paths()
          4. For each hop: resolve domain_type, look up domain policy (DB then DOMAIN_DEFAULTS)
          5. Build step dict, persist to nc_flow_walkthrough_steps
          6. Return steps list
        """
        from tools.network.path_analyzer import find_paths

        self._ensure_tables(conn)

        flow_row = conn.execute(
            "SELECT * FROM nc_traffic_flows WHERE id = ?", (flow_id,)
        ).fetchone()
        if not flow_row:
            return []
        flow = dict(flow_row)

        topo_row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = ?", (flow["topology_id"],)
        ).fetchone()
        if not topo_row or not topo_row["graph_json"]:
            return []

        raw_graph = json.loads(topo_row["graph_json"])
        nodes_list: list[dict] = raw_graph.get("nodes", [])
        edges: list[dict] = raw_graph.get("edges", [])

        # path_analyzer expects nodes as a dict keyed by node id
        nodes_dict: dict[str, dict] = {n["id"]: n for n in nodes_list}
        graph = {"nodes": nodes_dict, "edges": edges}

        result = find_paths(flow["src_zone"], flow["dst_zone"], graph)
        hops: list[str] = []
        if result.get("paths"):
            # Use first open path; fall back to first path if all blocked
            open_paths = [p for p in result["paths"] if not p.get("acl_blocked")]
            chosen = open_paths[0] if open_paths else result["paths"][0]
            hops = chosen.get("hops", [])

        if not hops:
            # No path found — still emit src/dst as two steps
            hops = [flow["src_zone"], flow["dst_zone"]]

        app_type: str = flow["app_type"]
        protocols = PROTOCOL_TABLE.get(app_type, [])

        # Load per-node domain policy overrides from DB (keyed by node_id)
        policy_rows = conn.execute(
            "SELECT * FROM nc_security_domain_policies WHERE topology_id = ?",
            (flow["topology_id"],),
        ).fetchall()
        policy_by_node: dict[str, dict] = {r["node_id"]: dict(r) for r in policy_rows}

        # Delete stale steps before regenerating
        conn.execute(
            "DELETE FROM nc_flow_walkthrough_steps WHERE flow_id = ?", (flow_id,)
        )

        steps: list[dict] = []
        for step_number, node_id in enumerate(hops, start=1):
            node = nodes_dict.get(node_id, {"id": node_id, "label": node_id, "type": ""})
            node_label = node.get("label") or node_id
            domain_type = resolve_domain_type(node)

            # Security detail: DB override > DOMAIN_DEFAULTS
            if node_id in policy_by_node:
                db_policy = policy_by_node[node_id]
                policy_json = json.loads(db_policy.get("policy_json") or "{}")
                security_detail: dict = {
                    "domain_type": db_policy.get("domain_type", domain_type),
                    "inspection_type": db_policy.get("inspection_type") or "",
                    **policy_json,
                }
            else:
                security_detail = dict(DOMAIN_DEFAULTS.get(domain_type, {}))
                security_detail["domain_type"] = domain_type

            # Network detail: protocol table entries for this app_type
            network_detail: dict = {
                "protocols": protocols,
                "app_type": app_type,
            }

            # Primary action for this hop
            action_list = DOMAIN_ACTION_MAP.get((domain_type, app_type), [])
            action_type = action_list[0] if action_list else "forward"

            step: dict = {
                "step_number": step_number,
                "node_id": node_id,
                "node_label": node_label,
                "action_type": action_type,
                "security_detail": security_detail,
                "network_detail": network_detail,
                "narrative": "",
            }
            steps.append(step)

            conn.execute(
                """INSERT INTO nc_flow_walkthrough_steps
                   (id, flow_id, step_number, node_id, node_label,
                    action_type, security_detail, network_detail, narrative)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    flow_id,
                    step_number,
                    node_id,
                    node_label,
                    action_type,
                    json.dumps(security_detail),
                    json.dumps(network_detail),
                    "",
                ),
            )

        conn.commit()
        return steps

    def get_walkthrough(self, flow_id: str, conn) -> list[dict]:
        """Return persisted walkthrough steps for a flow."""
        self._ensure_tables(conn)
        rows = conn.execute(
            """SELECT step_number, node_id, node_label, action_type,
                      security_detail, network_detail, narrative
               FROM nc_flow_walkthrough_steps
               WHERE flow_id = ?
               ORDER BY step_number""",
            (flow_id,),
        ).fetchall()
        steps = []
        for r in rows:
            step = dict(r)
            for field in ("security_detail", "network_detail"):
                val = step.get(field)
                if isinstance(val, str):
                    try:
                        step[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        step[field] = {}
            steps.append(step)
        return steps
