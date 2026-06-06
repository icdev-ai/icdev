# CUI // SP-CTI
"""IQE adapter for Peering Management Canvas (PMC).

Registers 5 collections: pmc.peers, pmc.ix_memberships, pmc.prefixes,
pmc.peering_requests, pmc.route_policies.
"""

from __future__ import annotations

import os


def _pmc_conn(conn):
    """Return PMC connection — use provided conn or open new one."""
    if conn is not None:
        return conn, False
    from tools.pmc_canvas.db.init_db import get_connection
    return get_connection(), True


def _safe_fetch(conn, sql: str, params=()) -> list[dict]:
    """Execute SQL and return list of row dicts, empty list on error."""
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        return []


# ── Collection adapters ────────────────────────────────────────────────────────

def peers_adapter(query: str, conn=None, **_) -> list[dict]:
    """NL→SQL for pmc.peers: active/all peers, by ASN, by status, by type."""
    _conn, owned = _pmc_conn(conn)
    try:
        q = query.lower()
        if any(t in q for t in ("active", "live", "up")):
            sql = "SELECT id,asn,org_name,peer_type,policy,status,traffic_ratio,ipv4_prefix_count,ipv6_prefix_count,rir FROM peering_peers WHERE status='active' ORDER BY org_name"
        elif "transit" in q:
            sql = "SELECT id,asn,org_name,peer_type,policy,status,traffic_ratio FROM peering_peers WHERE peer_type='transit' ORDER BY org_name"
        elif "customer" in q:
            sql = "SELECT id,asn,org_name,peer_type,policy,status,traffic_ratio FROM peering_peers WHERE peer_type='customer' ORDER BY org_name"
        elif "evaluation" in q or "pending" in q:
            sql = "SELECT id,asn,org_name,peer_type,policy,status,noc_email FROM peering_peers WHERE status='evaluation' ORDER BY created_at DESC"
        elif "stale" in q or "old" in q:
            sql = ("SELECT id,asn,org_name,peeringdb_synced_at FROM peering_peers "
                   "WHERE peeringdb_synced_at IS NOT NULL ORDER BY peeringdb_synced_at ASC LIMIT 20")
        else:
            sql = "SELECT id,asn,org_name,peer_type,policy,status,traffic_ratio,rir FROM peering_peers ORDER BY org_name LIMIT 50"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def ix_adapter(query: str, conn=None, **_) -> list[dict]:
    """NL→SQL for pmc.ix_memberships: active IXs, by country, by cost."""
    _conn, owned = _pmc_conn(conn)
    try:
        q = query.lower()
        if "cost" in q or "expensive" in q:
            sql = "SELECT id,ix_name,city,country,our_speed_gbps,monthly_cost_usd,status FROM peering_ix ORDER BY monthly_cost_usd DESC"
        elif "active" in q:
            sql = "SELECT id,ix_name,city,country,our_ipv4,our_ipv6,our_speed_gbps,monthly_cost_usd FROM peering_ix WHERE status='active'"
        else:
            sql = "SELECT id,ix_name,city,country,our_speed_gbps,monthly_cost_usd,status FROM peering_ix ORDER BY ix_name"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def prefixes_adapter(query: str, conn=None, **_) -> list[dict]:
    """NL→SQL for pmc.prefixes: RPKI invalid, unregistered, by peer."""
    _conn, owned = _pmc_conn(conn)
    try:
        q = query.lower()
        if "invalid" in q:
            sql = ("SELECT pp.prefix, pp.origin_asn, pp.rpki_status, pr.asn, pr.org_name "
                   "FROM peering_prefixes pp JOIN peering_peers pr ON pp.peer_id=pr.id "
                   "WHERE pp.rpki_status='invalid' ORDER BY pr.asn")
        elif "not found" in q or "not-found" in q or "no roa" in q:
            sql = ("SELECT pp.prefix, pp.origin_asn, pp.rpki_status, pr.asn, pr.org_name "
                   "FROM peering_prefixes pp JOIN peering_peers pr ON pp.peer_id=pr.id "
                   "WHERE pp.rpki_status='not-found' ORDER BY pr.asn")
        elif "irr" in q or "unregistered" in q:
            sql = ("SELECT pp.prefix, pp.origin_asn, pp.irr_registered, pr.asn, pr.org_name "
                   "FROM peering_prefixes pp JOIN peering_peers pr ON pp.peer_id=pr.id "
                   "WHERE pp.irr_registered=0 OR pp.irr_registered=FALSE ORDER BY pr.asn")
        else:
            sql = ("SELECT pp.prefix, pp.address_family, pp.rpki_status, pp.roa_found, "
                   "pr.asn, pr.org_name FROM peering_prefixes pp "
                   "JOIN peering_peers pr ON pp.peer_id=pr.id ORDER BY pr.asn LIMIT 100")
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def requests_adapter(query: str, conn=None, **_) -> list[dict]:
    """NL→SQL for pmc.peering_requests: pending, sent, by type."""
    _conn, owned = _pmc_conn(conn)
    try:
        q = query.lower()
        if "pending" in q:
            sql = ("SELECT pr.id,pr.request_type,pr.status,pr.contact_method,pr.created_at,"
                   "pp.asn,pp.org_name FROM peering_requests pr "
                   "LEFT JOIN peering_peers pp ON pr.peer_id=pp.id "
                   "WHERE pr.status='pending' ORDER BY pr.created_at DESC")
        elif "accepted" in q:
            sql = ("SELECT pr.id,pr.request_type,pr.status,pr.responded_at,"
                   "pp.asn,pp.org_name FROM peering_requests pr "
                   "LEFT JOIN peering_peers pp ON pr.peer_id=pp.id "
                   "WHERE pr.status='accepted' ORDER BY pr.responded_at DESC")
        else:
            sql = ("SELECT pr.id,pr.request_type,pr.status,pr.created_at,"
                   "pp.asn,pp.org_name FROM peering_requests pr "
                   "LEFT JOIN peering_peers pp ON pr.peer_id=pp.id ORDER BY pr.created_at DESC LIMIT 50")
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def policies_adapter(query: str, conn=None, **_) -> list[dict]:
    """NL→SQL for pmc.route_policies: active import/export policies."""
    _conn, owned = _pmc_conn(conn)
    try:
        q = query.lower()
        if "import" in q:
            sql = "SELECT id,policy_name,our_asn,policy_type,action,priority,active FROM peering_policies WHERE policy_type='import' ORDER BY priority"
        elif "export" in q:
            sql = "SELECT id,policy_name,our_asn,policy_type,action,priority,active FROM peering_policies WHERE policy_type='export' ORDER BY priority"
        elif "active" in q:
            sql = "SELECT id,policy_name,our_asn,policy_type,action,priority FROM peering_policies WHERE active=TRUE OR active=1 ORDER BY priority"
        else:
            sql = "SELECT id,policy_name,our_asn,policy_type,action,priority,active FROM peering_policies ORDER BY policy_type,priority"
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


# ── Registration ────────────────────────────────────────────────────────────────

def register(registry) -> None:
    """Register PMC collections with the IQE registry."""
    if os.environ.get("ICDEV_PMC_ENABLED", "false").lower() not in ("true", "1"):
        return

    registry.register("pmc.peers", peers_adapter, description="BGP peers — ASN, org, policy, status")
    registry.register("pmc.ix_memberships", ix_adapter, description="Internet Exchange memberships and costs")
    registry.register("pmc.prefixes", prefixes_adapter, description="Peer prefixes — RPKI validation, IRR registration")
    registry.register("pmc.peering_requests", requests_adapter, description="Peering session requests and their lifecycle")
    registry.register("pmc.route_policies", policies_adapter, description="BGP route import/export policies")
