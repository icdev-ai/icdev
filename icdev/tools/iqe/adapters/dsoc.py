# CUI // SP-CTI
"""IQE adapter for DDoS & Security Ops Canvas (DSOC).

Registers 5 collections:
  dsoc.flowspec_rules    — BGP flowspec rules; filter by action, status.
  dsoc.rtbh_entries      — RTBH blackhole entries; filter by status, prefix.
  dsoc.scrubbing_centers — Scrubbing center capacity/utilization; filter by status.
  dsoc.threats           — Threat intelligence feed entries; filter by confidence, type.
  dsoc.mitigations       — Active and historical DDoS mitigations; filter by status, type.

Tables are created by tools/dsoc_canvas/db/init_db.py.
Adapters return [] gracefully if tables are absent.
"""
from __future__ import annotations

import os
import re

from tools.dsoc_canvas.constants import INTENT_RULES


# ── Connection helper ─────────────────────────────────────────────────────────

def _dsoc_conn(conn):
    if conn is not None:
        return conn, False
    from tools.dsoc_canvas.db.init_db import get_connection
    return get_connection(), True


# ── Safe fetch ────────────────────────────────────────────────────────────────

def _safe_fetch(conn, sql: str, params=()) -> list[dict]:
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


# ── Collection adapters ───────────────────────────────────────────────────────

def flowspec_rules_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _dsoc_conn(conn)
    try:
        q = query.lower()
        if "drop" in q:
            sql = (
                "SELECT rule_name, destination_prefix, source_prefix, protocol, "
                "action, community, status, created_at "
                "FROM dsoc_flowspec_rules WHERE action='drop' AND status='active' "
                "ORDER BY created_at DESC"
            )
        elif "rate" in q or "rate-limit" in q or "rate limit" in q:
            sql = (
                "SELECT rule_name, destination_prefix, source_prefix, protocol, "
                "action, community, status, created_at "
                "FROM dsoc_flowspec_rules WHERE action='rate-limit' "
                "ORDER BY status, created_at DESC"
            )
        elif "redirect" in q:
            sql = (
                "SELECT rule_name, destination_prefix, source_prefix, protocol, "
                "action, community, status, created_at "
                "FROM dsoc_flowspec_rules WHERE action='redirect' "
                "ORDER BY status, created_at DESC"
            )
        elif "inactive" in q or "expired" in q or "withdrawn" in q:
            sql = (
                "SELECT rule_name, destination_prefix, action, status, created_at "
                "FROM dsoc_flowspec_rules WHERE status != 'active' "
                "ORDER BY created_at DESC LIMIT 50"
            )
        else:
            sql = (
                "SELECT rule_name, destination_prefix, source_prefix, protocol, "
                "action, community, status, created_at "
                "FROM dsoc_flowspec_rules ORDER BY status, created_at DESC LIMIT 50"
            )
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def rtbh_entries_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _dsoc_conn(conn)
    try:
        q = query.lower()
        if "active" in q:
            sql = (
                "SELECT prefix, trigger_reason, community, nexthop, "
                "auto_withdraw_minutes, created_at "
                "FROM dsoc_rtbh_entries WHERE status='active' "
                "ORDER BY created_at DESC"
            )
        elif "withdrawn" in q or "expired" in q or "removed" in q:
            sql = (
                "SELECT prefix, trigger_reason, community, nexthop, "
                "auto_withdraw_minutes, created_at "
                "FROM dsoc_rtbh_entries WHERE status='withdrawn' "
                "ORDER BY created_at DESC LIMIT 50"
            )
        elif "manual" in q:
            sql = (
                "SELECT prefix, trigger_reason, community, nexthop, status, created_at "
                "FROM dsoc_rtbh_entries WHERE trigger_reason='manual' "
                "ORDER BY status, created_at DESC"
            )
        elif "auto" in q:
            sql = (
                "SELECT prefix, trigger_reason, auto_withdraw_minutes, status, created_at "
                "FROM dsoc_rtbh_entries WHERE auto_withdraw_minutes IS NOT NULL "
                "ORDER BY status, created_at DESC"
            )
        else:
            sql = (
                "SELECT prefix, trigger_reason, community, nexthop, "
                "auto_withdraw_minutes, status, created_at "
                "FROM dsoc_rtbh_entries ORDER BY status, created_at DESC LIMIT 50"
            )
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def scrubbing_centers_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _dsoc_conn(conn)
    try:
        q = query.lower()
        if "high" in q or "overload" in q or "utiliz" in q or "capacity" in q:
            sql = (
                "SELECT name, location, capacity_gbps, current_load_gbps, status "
                "FROM dsoc_scrubbing_centers "
                "WHERE CAST(current_load_gbps AS REAL) / CAST(capacity_gbps AS REAL) > 0.7 "
                "ORDER BY current_load_gbps DESC"
            )
        elif "critical" in q or "saturat" in q:
            sql = (
                "SELECT name, location, capacity_gbps, current_load_gbps, status "
                "FROM dsoc_scrubbing_centers "
                "WHERE CAST(current_load_gbps AS REAL) / CAST(capacity_gbps AS REAL) > 0.85 "
                "ORDER BY current_load_gbps DESC"
            )
        elif "offline" in q or "down" in q or "inactive" in q:
            sql = (
                "SELECT name, location, capacity_gbps, status "
                "FROM dsoc_scrubbing_centers WHERE status != 'active' "
                "ORDER BY name"
            )
        else:
            sql = (
                "SELECT name, location, capacity_gbps, current_load_gbps, status "
                "FROM dsoc_scrubbing_centers ORDER BY status, name LIMIT 50"
            )
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def threats_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _dsoc_conn(conn)
    try:
        q = query.lower()
        if "high confidence" in q or "confident" in q or ">= 80" in q or ">80" in q:
            sql = (
                "SELECT source_prefix, threat_type, confidence_pct, feed_source, "
                "packets_per_sec, bits_per_sec, last_seen "
                "FROM dsoc_threats WHERE confidence_pct >= 80 AND is_active=1 "
                "ORDER BY confidence_pct DESC, last_seen DESC"
            )
        elif "botnet" in q or "c2" in q or "command and control" in q:
            sql = (
                "SELECT source_prefix, threat_type, confidence_pct, feed_source, "
                "last_seen FROM dsoc_threats WHERE threat_type='botnet_c2' "
                "ORDER BY confidence_pct DESC, last_seen DESC"
            )
        elif "scanner" in q or "scan" in q:
            sql = (
                "SELECT source_prefix, threat_type, confidence_pct, feed_source, "
                "last_seen FROM dsoc_threats WHERE threat_type='scanner' "
                "ORDER BY confidence_pct DESC, last_seen DESC"
            )
        elif "amplif" in q:
            sql = (
                "SELECT source_prefix, threat_type, confidence_pct, feed_source, "
                "packets_per_sec, bits_per_sec, last_seen "
                "FROM dsoc_threats WHERE threat_type='amplifier' "
                "ORDER BY confidence_pct DESC, last_seen DESC"
            )
        elif "inactive" in q or "historical" in q:
            sql = (
                "SELECT source_prefix, threat_type, confidence_pct, feed_source, last_seen "
                "FROM dsoc_threats WHERE is_active=0 "
                "ORDER BY last_seen DESC LIMIT 50"
            )
        else:
            sql = (
                "SELECT source_prefix, threat_type, confidence_pct, feed_source, "
                "packets_per_sec, bits_per_sec, last_seen "
                "FROM dsoc_threats ORDER BY confidence_pct DESC, last_seen DESC LIMIT 50"
            )
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


def mitigations_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _dsoc_conn(conn)
    try:
        q = query.lower()
        if "active" in q:
            sql = (
                "SELECT mitigation_number, target_prefix, mitigation_type, "
                "attack_type, peak_traffic_gbps, started_at "
                "FROM dsoc_mitigations WHERE status='active' "
                "ORDER BY started_at DESC"
            )
        elif "complete" in q or "resolved" in q or "finished" in q:
            sql = (
                "SELECT mitigation_number, target_prefix, mitigation_type, "
                "attack_type, peak_traffic_gbps, started_at, ended_at "
                "FROM dsoc_mitigations WHERE status='completed' "
                "ORDER BY ended_at DESC LIMIT 50"
            )
        elif "scrubbing" in q:
            sql = (
                "SELECT mitigation_number, target_prefix, mitigation_type, "
                "attack_type, peak_traffic_gbps, status, started_at "
                "FROM dsoc_mitigations WHERE mitigation_type='scrubbing' "
                "ORDER BY status, created_at DESC"
            )
        elif "flowspec" in q:
            sql = (
                "SELECT mitigation_number, target_prefix, mitigation_type, "
                "attack_type, peak_traffic_gbps, status, started_at "
                "FROM dsoc_mitigations WHERE mitigation_type='flowspec' "
                "ORDER BY status, created_at DESC"
            )
        elif "rtbh" in q or "blackhole" in q:
            sql = (
                "SELECT mitigation_number, target_prefix, mitigation_type, "
                "attack_type, peak_traffic_gbps, status, started_at "
                "FROM dsoc_mitigations WHERE mitigation_type='rtbh' "
                "ORDER BY status, created_at DESC"
            )
        else:
            sql = (
                "SELECT mitigation_number, target_prefix, mitigation_type, "
                "attack_type, peak_traffic_gbps, status, created_at "
                "FROM dsoc_mitigations ORDER BY status, created_at DESC LIMIT 50"
            )
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


# ── Dispatch ──────────────────────────────────────────────────────────────────

def bgp_hijacks_adapter(query: str, conn=None, **_) -> list[dict]:
    _conn, owned = _dsoc_conn(conn)
    try:
        q = query.lower()
        if "high confidence" in q or "critical" in q or ">= 80" in q:
            sql = (
                "SELECT hijack_number, hijack_type, detected_prefix, expected_prefix, "
                "expected_origin_asn, observed_origin_asn, confidence_pct, status, detected_at "
                "FROM dsoc_bgp_hijacks WHERE status IN ('open','under_review') "
                "AND confidence_pct >= 80 ORDER BY confidence_pct DESC, detected_at DESC"
            )
        elif "type_0" in q or "exact" in q:
            sql = (
                "SELECT hijack_number, hijack_type, detected_prefix, expected_prefix, "
                "observed_origin_asn, confidence_pct, status, detected_at "
                "FROM dsoc_bgp_hijacks WHERE hijack_type='type_0' "
                "ORDER BY status, confidence_pct DESC"
            )
        elif "route.?leak" in q or "leak" in q:
            sql = (
                "SELECT hijack_number, hijack_type, detected_prefix, observed_origin_asn, "
                "peer_asn, route_leak_type, confidence_pct, status, detected_at "
                "FROM dsoc_bgp_hijacks WHERE hijack_type='route_leak' "
                "ORDER BY status, confidence_pct DESC"
            )
        elif "resolved" in q or "false" in q or "closed" in q:
            sql = (
                "SELECT hijack_number, hijack_type, detected_prefix, status, detected_at "
                "FROM dsoc_bgp_hijacks WHERE status IN ('resolved','false_positive','mitigated') "
                "ORDER BY detected_at DESC LIMIT 50"
            )
        else:
            sql = (
                "SELECT hijack_number, hijack_type, detected_prefix, expected_prefix, "
                "expected_origin_asn, observed_origin_asn, confidence_pct, status, detected_at "
                "FROM dsoc_bgp_hijacks WHERE status IN ('open','under_review') "
                "ORDER BY confidence_pct DESC, detected_at DESC LIMIT 50"
            )
        return _safe_fetch(_conn, sql)
    finally:
        if owned:
            try:
                _conn.close()
            except Exception:
                pass


_COLLECTION_MAP = {
    "dsoc.flowspec_rules":    flowspec_rules_adapter,
    "dsoc.rtbh_entries":      rtbh_entries_adapter,
    "dsoc.scrubbing_centers": scrubbing_centers_adapter,
    "dsoc.threats":           threats_adapter,
    "dsoc.mitigations":       mitigations_adapter,
    "dsoc.bgp_hijacks":       bgp_hijacks_adapter,
}


def handle_query(conn, query: str, collection: str = "") -> dict:
    """Route a natural-language query to the right DSOC collection adapter.

    Keyword routing is driven by INTENT_RULES from tools.dsoc_canvas.constants.
    Falls back to dsoc.threats when no collection can be inferred.
    """
    if not collection:
        q_lower = query.lower()
        for rule in INTENT_RULES:
            if re.search(rule["pattern"], q_lower):
                collection = rule["collection"]
                break
        if not collection:
            collection = "dsoc.threats"

    fn = _COLLECTION_MAP.get(collection, threats_adapter)
    results = fn(query, conn=conn)
    return {
        "results": results,
        "collection": collection,
        "count": len(results),
        "query": query,
    }


def register(registry: dict) -> None:
    """Register DSOC collections into the global IQE registry.

    Gated on ICDEV_DSOC_ENABLED environment variable.
    """
    if os.environ.get("ICDEV_DSOC_ENABLED", "").lower() not in ("1", "true", "yes"):
        return
    for name, fn in _COLLECTION_MAP.items():
        registry[name] = fn
