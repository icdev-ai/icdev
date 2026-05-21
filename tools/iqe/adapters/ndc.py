"""IQE NDC adapter — wraps the Network Design Canvas DB (network_canvas.db).

Collections exposed:
    network.topologies       — all topologies
    network.devices          — nc_objects filtered to device-like object_types
    network.objects          — all nc_objects (unfiltered)
    network.circuits         — nc_circuits
    network.sites            — nc_sites
    network.ipam             — nc_ipam_blocks
    network.findings         — nc_compliance_findings
    network.projects         — nc_projects
    network.versions         — nc_versions
    network.groups           — nc_groups
    network.cables           — nc_cables
    network.cross_connects   — nc_cross_connects

The adapter uses the NDC's own get_connection() so it respects NC_STORAGE_BACKEND
and can target SQLite or PostgreSQL without any changes here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running without ICDEV on sys.path by resolving project root
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.iqe.adapters import IQEAdapter

_DEVICE_TYPES = {
    "router", "switch", "firewall", "server", "load_balancer", "waf",
    "vpn_gateway", "host", "virtual_machine", "container", "appliance",
    "generic", "cloud_instance", "nat_gateway", "transit_gateway",
}

_COLLECTIONS = [
    "network.topologies",
    "network.devices",
    "network.objects",
    "network.circuits",
    "network.sites",
    "network.ipam",
    "network.findings",
    "network.compliance_findings",  # alias for network.findings
    "network.projects",
    "network.versions",
    "network.groups",
    "network.cables",
    "network.cross_connects",
    "network.ai_decisions",
    "network.partners",
    "network.agreements_expiring",
]


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row or psycopg2 DictRow to a plain dict."""
    try:
        return dict(row)
    except TypeError:
        return {k: row[k] for k in row.keys()}


def _fetch(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cursor = conn.execute(sql, params)
        return [_row_to_dict(r) for r in cursor.fetchall()]
    except Exception:
        return []


class NDCAdapter(IQEAdapter):
    """IQE adapter for the Network Design Canvas (NDC)."""

    def list_collections(self) -> list[str]:
        return list(_COLLECTIONS)

    def get_collection(self, collection: str, topology_id: str | None = None) -> list[dict]:
        from tools.network.db.init_db import get_connection

        conn = get_connection()
        try:
            return self._query(conn, collection, topology_id)
        finally:
            conn.close()

    def _query(self, conn, collection: str, topology_id: str | None) -> list[dict]:
        tid_filter = "WHERE topology_id = ?" if topology_id else ""
        tid_params: tuple = (topology_id,) if topology_id else ()

        if collection == "network.topologies":
            rows = _fetch(conn, "SELECT * FROM topologies")
        elif collection in ("network.devices",):
            # type_list built from _DEVICE_TYPES — a module-level frozen set of literals, never user input
            type_list = ", ".join(f"'{t}'" for t in _DEVICE_TYPES)
            sql = f"SELECT * FROM nc_objects WHERE object_type IN ({type_list})"  # nosec B608
            if topology_id:
                sql += " AND topology_id = ?"
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.objects":
            # tid_filter is either "" or "WHERE topology_id = ?" — a literal constant, never user input
            sql = f"SELECT * FROM nc_objects {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.circuits":
            sql = f"SELECT * FROM nc_circuits {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.sites":
            rows = _fetch(conn, "SELECT * FROM nc_sites")
        elif collection == "network.ipam":
            sql = f"SELECT * FROM nc_ipam_blocks {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection in ("network.findings", "network.compliance_findings"):
            sql = f"SELECT * FROM nc_compliance_findings {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.projects":
            rows = _fetch(conn, "SELECT * FROM nc_projects")
        elif collection == "network.versions":
            sql = f"SELECT * FROM nc_versions {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.groups":
            sql = f"SELECT * FROM nc_groups {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.cables":
            sql = f"SELECT * FROM nc_cables {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.cross_connects":
            sql = f"SELECT * FROM nc_cross_connects {tid_filter}"  # nosec B608
            rows = _fetch(conn, sql, tid_params)
        elif collection == "network.ai_decisions":
            try:
                from tools.db.storage import get_connection as _main_conn
                with _main_conn() as _c:
                    rows = [dict(r) for r in _c.execute(
                        "SELECT * FROM canvas_ai_decisions WHERE canvas_type='ndc' ORDER BY created_at DESC"
                    ).fetchall()]
            except Exception:
                rows = []
            return rows
        elif collection == "network.partners":
            rows = _fetch(conn, "SELECT * FROM nc_partners ORDER BY name")
        elif collection == "network.agreements_expiring":
            rows = _fetch(
                conn,
                "SELECT id, peer_name, peer_asn, status, contract_end, "
                "CAST((julianday(contract_end) - julianday('now')) AS INTEGER) AS days_remaining, "
                "monthly_cost_usd FROM nc_peering_agreements "
                "WHERE status='operational' AND contract_end != '' AND contract_end <= date('now', '+90 days') "
                "ORDER BY contract_end ASC",
            )
        else:
            raise ValueError(
                f"NDCAdapter: unknown collection {collection!r}. "
                f"Valid: {', '.join(_COLLECTIONS)}"
            )

        # Auto-expand config_json into a 'config' key so predicates like
        # device.config.vendor work without special handling in the interpreter.
        for row in rows:
            if "config_json" in row and row["config_json"]:
                try:
                    row["config"] = json.loads(row["config_json"])
                except (json.JSONDecodeError, TypeError):
                    row["config"] = {}

        return rows


# Register all NDC collections on the module-level executor so the IQE dispatch
# route can use this adapter without instantiating NDCAdapter explicitly.
from tools.iqe.executor import register_collection  # noqa: E402

_ndc_inst = NDCAdapter()


def _make_ndc_fn(coll: str):
    def _fn(conn):  # conn unused — NDCAdapter manages its own connection
        return _ndc_inst.get_collection(coll)
    return _fn


for _c in _COLLECTIONS:
    register_collection(_c, _make_ndc_fn(_c))
