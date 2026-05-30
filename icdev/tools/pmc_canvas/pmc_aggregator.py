# CUI // SP-CTI
"""PMC aggregator — combines peer, IX, RPKI, and request data for dashboard overview."""

from __future__ import annotations

from typing import Any


def get_pmc_overview(conn) -> dict[str, Any]:
    """Return PMC dashboard overview dict.

    Queries:
    - Peer counts by status
    - IX memberships active/pending
    - RPKI coverage summary
    - Pending peering requests
    - Total active prefixes
    """
    try:
        return _query_overview(conn)
    except Exception as exc:
        return {"error": str(exc), "peers": {}, "ix": {}, "rpki": {}, "requests": {}}


def _query_overview(conn) -> dict[str, Any]:
    _q = _make_query(conn)

    # ── Peer stats ────────────────────────────────────────────────────────────
    peer_rows = _q(
        "SELECT status, COUNT(*) as cnt FROM peering_peers GROUP BY status"
    )
    peer_counts: dict[str, int] = {}
    total_peers = 0
    for row in peer_rows:
        s = str(row[0] if not hasattr(row, "keys") else row["status"])
        c = int(row[1] if not hasattr(row, "keys") else row["cnt"])
        peer_counts[s] = c
        total_peers += c

    # ── IX stats ──────────────────────────────────────────────────────────────
    ix_rows = _q("SELECT status, COUNT(*) as cnt FROM peering_ix GROUP BY status")
    ix_counts: dict[str, int] = {}
    for row in ix_rows:
        s = str(row[0] if not hasattr(row, "keys") else row["status"])
        c = int(row[1] if not hasattr(row, "keys") else row["cnt"])
        ix_counts[s] = c

    # ── RPKI summary ──────────────────────────────────────────────────────────
    rpki_rows = _q(
        "SELECT rpki_status, COUNT(*) as cnt FROM peering_prefixes GROUP BY rpki_status"
    )
    rpki_counts: dict[str, int] = {"valid": 0, "invalid": 0, "not_found": 0, "unknown": 0}
    total_pfx = 0
    for row in rpki_rows:
        s = str(row[0] if not hasattr(row, "keys") else row["rpki_status"])
        c = int(row[1] if not hasattr(row, "keys") else row["cnt"])
        key = s.replace("-", "_")
        rpki_counts[key] = rpki_counts.get(key, 0) + c
        total_pfx += c

    rpki_coverage = (
        round(rpki_counts["valid"] / total_pfx * 100, 1) if total_pfx > 0 else 0.0
    )

    # ── Pending requests ──────────────────────────────────────────────────────
    req_rows = _q(
        "SELECT COUNT(*) as cnt FROM peering_requests WHERE status IN ('pending','sent')"
    )
    pending_requests = int(
        req_rows[0][0] if req_rows and not hasattr(req_rows[0], "keys")
        else (req_rows[0]["cnt"] if req_rows else 0)
    )

    # ── Active peer sample ────────────────────────────────────────────────────
    active_rows = _q(
        "SELECT asn, org_name, traffic_ratio FROM peering_peers "
        "WHERE status='active' ORDER BY traffic_ratio DESC LIMIT 5"
    )
    top_peers = []
    for row in active_rows:
        if hasattr(row, "keys"):
            top_peers.append(dict(row))
        else:
            top_peers.append({"asn": row[0], "org_name": row[1], "traffic_ratio": row[2]})

    return {
        "peers": {
            "total": total_peers,
            "active": peer_counts.get("active", 0),
            "evaluation": peer_counts.get("evaluation", 0),
            "requested": peer_counts.get("requested", 0),
            "suspended": peer_counts.get("suspended", 0),
            "by_status": peer_counts,
        },
        "ix": {
            "active": ix_counts.get("active", 0),
            "pending": ix_counts.get("pending", 0),
            "total": sum(ix_counts.values()),
        },
        "rpki": {
            "total_prefixes": total_pfx,
            "valid": rpki_counts["valid"],
            "invalid": rpki_counts["invalid"],
            "not_found": rpki_counts.get("not_found", 0),
            "coverage_pct": rpki_coverage,
            "has_invalids": rpki_counts["invalid"] > 0,
        },
        "requests": {
            "pending": pending_requests,
        },
        "top_peers": top_peers,
    }


def _make_query(conn):
    """Return a query function that handles PG/SQLite differences."""
    def _q(sql: str, params=()) -> list:
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()
        except Exception:
            return []
    return _q
