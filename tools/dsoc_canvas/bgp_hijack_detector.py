# CUI // SP-CTI
"""BGP Hijack & Route Leak Detector (artemis-model).

Detects:
  - Prefix hijacking: observed origin ASN doesn't match expected AS (RFC 7908)
  - Route leaks: customer/peer re-announcing transit routes (RFC 7908)
  - RPKI invalids: captured from external validator output

Hijack taxonomy (RFC 7908):
  type_0  — exact prefix, wrong origin ASN
  type_1  — sub-prefix of our prefix, wrong origin ASN
  type_2  — super-prefix of our prefix, wrong origin ASN
  route_leak — prefix reaching unexpected ASes due to policy violation
  rpki_invalid — RPKI invalid prefix observed from a peer
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Optional

CADENCE_HOURS = 1  # used by Genesis reflex bgp_hijack_monitor (tools/genesis/reflexes/bgp_hijack_monitor.py)

HIJACK_TYPES = ["type_0", "type_1", "type_2", "route_leak", "rpki_invalid", "unknown"]
HIJACK_STATUSES = ["open", "under_review", "mitigated", "false_positive", "resolved"]
ROUTE_LEAK_TYPES = [
    "customer_to_customer", "customer_to_peer", "peer_to_peer", "unknown"
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_exec(conn, sql_pg: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
    except Exception:
        conn.execute(sql_pg.replace("%s", "?"), params)


def _safe_fetch(conn, sql_pg: str, params: tuple = ()) -> list[dict]:
    def _rows(cur) -> list[dict]:
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]
    try:
        cur = conn.cursor()
        cur.execute(sql_pg, params)
        return _rows(cur)
    except Exception:
        try:
            cur = conn.cursor()
            cur.execute(sql_pg.replace("%s", "?"), params)
            return _rows(cur)
        except Exception:
            return []


def _prefix_relationship(our_prefix: str, observed_prefix: str) -> str:
    """Return RFC 7908 hijack type based on prefix relationship."""
    try:
        our_net = ipaddress.ip_network(our_prefix, strict=False)
        obs_net = ipaddress.ip_network(observed_prefix, strict=False)
        if our_net == obs_net:
            return "type_0"
        if obs_net.subnet_of(our_net):
            return "type_1"
        if our_net.subnet_of(obs_net):
            return "type_2"
        return "unknown"
    except ValueError:
        return "unknown"


def _generate_hijack_number(conn) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m")
    rows = _safe_fetch(
        conn,
        "SELECT COUNT(*) AS n FROM dsoc_bgp_hijacks WHERE hijack_number LIKE %s",
        (f"HIJACK-{ts}-%",),
    )
    n = (int(rows[0].get("n", 0)) if rows else 0) + 1
    return f"HIJACK-{ts}-{n:04d}"


def record_hijack_event(
    conn,
    *,
    hijack_type: str,
    detected_prefix: str,
    expected_prefix: str,
    expected_origin_asn: Optional[int],
    observed_origin_asn: Optional[int],
    peer_asn: Optional[int] = None,
    route_leak_type: Optional[str] = None,
    confidence_pct: float = 75.0,
    detection_source: str = "internal",
    notes: str = "",
    classification: str = "CUI",
) -> dict:
    """Insert a new hijack/route-leak event. Returns inserted record dict."""
    hijack_num = _generate_hijack_number(conn)
    now = _now()
    _safe_exec(
        conn,
        """INSERT INTO dsoc_bgp_hijacks
           (hijack_number, hijack_type, status, detected_prefix, expected_prefix,
            expected_origin_asn, observed_origin_asn, peer_asn, route_leak_type,
            confidence_pct, detection_source, notes, classification, detected_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            hijack_num, hijack_type, "open", detected_prefix, expected_prefix,
            expected_origin_asn, observed_origin_asn, peer_asn, route_leak_type,
            confidence_pct, detection_source, notes, classification, now, now,
        ),
    )
    conn.commit()
    return {
        "hijack_number": hijack_num,
        "hijack_type": hijack_type,
        "status": "open",
        "detected_prefix": detected_prefix,
        "expected_prefix": expected_prefix,
        "expected_origin_asn": expected_origin_asn,
        "observed_origin_asn": observed_origin_asn,
        "confidence_pct": confidence_pct,
        "detected_at": now,
    }


def detect_prefix_hijack(
    conn,
    *,
    our_prefix: str,
    our_asn: int,
    observed_prefix: str,
    observed_origin_asn: int,
    peer_asn: Optional[int] = None,
    detection_source: str = "internal",
) -> Optional[dict]:
    """Check if observed_prefix/observed_origin_asn constitutes a hijack of our_prefix.

    Returns the new hijack record dict if a hijack is detected, else None.
    Deduplicates: skips if an identical open event already exists.
    """
    if observed_origin_asn == our_asn:
        return None  # our own ASN — not a hijack

    hijack_type = _prefix_relationship(our_prefix, observed_prefix)
    if hijack_type == "unknown":
        return None  # unrelated prefix

    # Dedup: don't open duplicate events for same observed prefix + rogue ASN
    existing = _safe_fetch(
        conn,
        """SELECT id FROM dsoc_bgp_hijacks
           WHERE detected_prefix=%s AND observed_origin_asn=%s AND status='open'""",
        (observed_prefix, observed_origin_asn),
    )
    if existing:
        return None

    confidence = 90.0 if hijack_type == "type_0" else 75.0
    return record_hijack_event(
        conn,
        hijack_type=hijack_type,
        detected_prefix=observed_prefix,
        expected_prefix=our_prefix,
        expected_origin_asn=our_asn,
        observed_origin_asn=observed_origin_asn,
        peer_asn=peer_asn,
        confidence_pct=confidence,
        detection_source=detection_source,
    )


def detect_route_leak(
    conn,
    *,
    prefix: str,
    announcing_asn: int,
    peer_asn: int,
    leak_type: str = "unknown",
    confidence_pct: float = 70.0,
    detection_source: str = "bgp_monitor",
) -> Optional[dict]:
    """Record a route leak event (customer/peer re-announcing transit routes)."""
    existing = _safe_fetch(
        conn,
        """SELECT id FROM dsoc_bgp_hijacks
           WHERE detected_prefix=%s AND observed_origin_asn=%s
           AND hijack_type='route_leak' AND status='open'""",
        (prefix, announcing_asn),
    )
    if existing:
        return None

    return record_hijack_event(
        conn,
        hijack_type="route_leak",
        detected_prefix=prefix,
        expected_prefix=prefix,
        expected_origin_asn=None,
        observed_origin_asn=announcing_asn,
        peer_asn=peer_asn,
        route_leak_type=leak_type,
        confidence_pct=confidence_pct,
        detection_source=detection_source,
        notes=f"Route leak via AS{peer_asn}",
    )


def get_active_hijacks(conn) -> list[dict]:
    """Return all open or under_review hijack events, highest confidence first."""
    return _safe_fetch(
        conn,
        """SELECT id, hijack_number, hijack_type, detected_prefix, expected_prefix,
           expected_origin_asn, observed_origin_asn, peer_asn,
           confidence_pct, status, detection_source, detected_at
           FROM dsoc_bgp_hijacks WHERE status IN ('open','under_review')
           ORDER BY confidence_pct DESC, detected_at DESC""",
    )


def resolve_hijack(conn, hijack_id: int, resolution: str = "resolved") -> dict:
    """Mark a hijack event as resolved (or false_positive / mitigated)."""
    now = _now()
    _safe_exec(
        conn,
        "UPDATE dsoc_bgp_hijacks SET status=%s, resolved_at=%s, updated_at=%s WHERE id=%s",
        (resolution, now, now, hijack_id),
    )
    conn.commit()
    return {"status": resolution, "id": hijack_id, "resolved_at": now}


def get_hijack_summary(conn) -> dict:
    """Return open/high-confidence counts for DSOC overview."""
    open_rows = _safe_fetch(
        conn,
        "SELECT COUNT(*) AS n FROM dsoc_bgp_hijacks WHERE status IN ('open','under_review')",
    )
    hc_rows = _safe_fetch(
        conn,
        """SELECT COUNT(*) AS n FROM dsoc_bgp_hijacks
           WHERE status IN ('open','under_review') AND confidence_pct >= 80""",
    )
    return {
        "open_hijacks": int(open_rows[0].get("n", 0)) if open_rows else 0,
        "high_confidence_hijacks": int(hc_rows[0].get("n", 0)) if hc_rows else 0,
    }
