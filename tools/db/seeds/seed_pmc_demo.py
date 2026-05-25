#!/usr/bin/env python3
# CUI // SP-CTI
"""PMC Demo Seed -- populates pmc_canvas.db with realistic peering management demo data.

Tables seeded:
  peering_peers (12), peering_ix (6), peering_prefixes (30),
  peering_requests (8), peering_policies (6)

Usage:
    python tools/db/seeds/seed_pmc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_pmc_demo.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=72)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.pmc_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db(conn)
        return conn
    except Exception:
        db = _ROOT / "data" / "pmc_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _safe_execute(conn, sql, params):
    expected = sql.count("?")
    actual = len(params) if isinstance(params, (list, tuple)) else 1
    if expected != actual:
        raise ValueError(f"Placeholder mismatch: {expected} placeholders but {actual} params")
    conn.execute(sql, params)


def _reset_demo_data(conn) -> None:
    for tbl in ("peering_policies", "peering_requests", "peering_prefixes", "peering_ix", "peering_peers"):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


# Pre-generate UUIDs for cross-references
_PEER_IDS = {f"peer-{i:03d}": _uid() for i in range(12)}
_IX_IDS = {f"ix-{i:03d}": _uid() for i in range(6)}

# Map country code to RIR for CHECK constraint compliance
_COUNTRY_RIR = {
    "US": "ARIN", "CA": "ARIN",
    "JP": "APNIC", "AU": "APNIC", "SG": "APNIC",
    "SE": "RIPE", "GB": "RIPE", "FR": "RIPE", "DE": "RIPE", "NL": "RIPE",
    "BR": "LACNIC", "MX": "LACNIC",
    "ZA": "AFRINIC",
}

_PEERS = [
    (_PEER_IDS["peer-000"], "Akamai Technologies", 32787, "public", "open", "active", 1000, 500, "AKAMAI", "ARIN", "noc@akamai.com"),
    (_PEER_IDS["peer-001"], "Cloudflare Inc", 13335, "public", "open", "active", 2000, 1000, "CLOUDFLARE", "ARIN", "peering@cloudflare.com"),
    (_PEER_IDS["peer-002"], "NTT Communications", 2914, "transit", "selective", "active", 5000, 2000, "NTT", "APNIC", "peering@ntt.net"),
    (_PEER_IDS["peer-003"], "Telia Company", 1299, "transit", "selective", "active", 4500, 1500, "TELIANET", "RIPE", "peering@telia.net"),
    (_PEER_IDS["peer-004"], "Hurricane Electric", 6939, "public", "open", "active", 3000, 1000, "HENET", "ARIN", "peering@he.net"),
    (_PEER_IDS["peer-005"], "GTT Communications", 3257, "transit", "selective", "evaluation", 4000, 1200, "GTT", "ARIN", "peering@gtt.net"),
    (_PEER_IDS["peer-006"], "Cogent Communications", 174, "transit", "no", "suspended", 6000, 2500, "COGENT", "ARIN", "peering@cogentco.com"),
    (_PEER_IDS["peer-007"], "Level 3 (Lumen)", 3356, "transit", "selective", "active", 7000, 3000, "LEVEL3", "ARIN", "peering@level3.com"),
    (_PEER_IDS["peer-008"], "Orange", 5511, "customer", "selective", "active", 2000, 800, "ORANGE", "RIPE", "peering@orange.com"),
    (_PEER_IDS["peer-009"], "Deutsche Telekom", 3320, "customer", "selective", "active", 3500, 1200, "DTAG", "RIPE", "peering@telekom.de"),
    (_PEER_IDS["peer-010"], "RETN", 9002, "public", "open", "requested", 800, 400, "RETN", "RIPE", "peering@retn.net"),
    (_PEER_IDS["peer-011"], "Netflix", 2906, "public", "open", "active", 1200, 600, "NETFLIX", "ARIN", "peering@netflix.com"),
]

_IX = [
    (_IX_IDS["ix-000"], "Equinix Ashburn", "Ashburn", "US", "198.51.100.1", "2001:db8::1", 100.0, 8500.0, "active", 1, "198.51.100.254", "2001:db8::254"),
    (_IX_IDS["ix-001"], "DE-CIX Frankfurt", "Frankfurt", "DE", "203.0.113.1", "2001:db8:de::1", 40.0, 3200.0, "active", 2, "203.0.113.254", "2001:db8:de::254"),
    (_IX_IDS["ix-002"], "LINX London", "London", "GB", "192.0.2.1", "2001:db8:gb::1", 10.0, 4500.0, "active", 3, "192.0.2.254", "2001:db8:gb::254"),
    (_IX_IDS["ix-003"], "AMS-IX", "Amsterdam", "NL", "198.51.100.129", "2001:db8:nl::1", 10.0, 2800.0, "active", 4, "198.51.100.253", "2001:db8:nl::254"),
    (_IX_IDS["ix-004"], "JPIX Tokyo", "Tokyo", "JP", "203.0.113.129", "2001:db8:jp::1", 10.0, 1500.0, "active", 5, "203.0.113.253", "2001:db8:jp::254"),
    (_IX_IDS["ix-005"], "Equinix Dallas", "Dallas", "US", "192.0.2.129", "2001:db8:us::1", 40.0, 6200.0, "pending", 6, "192.0.2.253", "2001:db8:us::254"),
]

_PREFIXES = []
for peer_idx, peer in enumerate(_PEERS):
    peer_id = peer[0]
    v4_count = peer[6] // 500
    v6_count = peer[7] // 500
    for j in range(min(v4_count, 3)):
        _PREFIXES.append({
            "id": _uid(),
            "peer_id": peer_id,
            "prefix": f"192.0.{peer_idx}.{j*64}/26",
            "address_family": "ipv4",
            "max_length": 28,
            "origin_asn": peer[2],
            "rpki_status": random.choice(["valid", "valid", "valid", "not-found", "unknown"]),
            "roa_found": True if random.random() > 0.2 else False,
            "irr_registered": True if random.random() > 0.3 else False,
            "last_validated": _ts(peer_idx * 2 + j),
            "classification": "CUI",
        })
    for j in range(min(v6_count, 2)):
        _PREFIXES.append({
            "id": _uid(),
            "peer_id": peer_id,
            "prefix": f"2001:db8:{peer_idx:04x}::{j*4}/64",
            "address_family": "ipv6",
            "max_length": 68,
            "origin_asn": peer[2],
            "rpki_status": random.choice(["valid", "valid", "not-found", "unknown"]),
            "roa_found": True if random.random() > 0.3 else False,
            "irr_registered": True if random.random() > 0.4 else False,
            "last_validated": _ts(peer_idx * 2 + j + 10),
            "classification": "CUI",
        })

_REQUESTS = []
for i in range(8):
    peer_idx = random.randint(0, len(_PEERS) - 1)
    ix_idx = random.randint(0, len(_IX) - 1)
    _REQUESTS.append({
        "id": _uid(),
        "peer_id": _PEERS[peer_idx][0],
        "request_type": random.choice(["new", "new", "upgrade", "policy_change"]),
        "status": random.choice(["pending", "sent", "accepted", "rejected"]),
        "ix_id": _IX[ix_idx][0],
        "proposed_speed": random.choice(["1G", "10G", "40G", "100G"]),
        "contact_method": random.choice(["email", "peeringdb", "noc"]),
        "contact_address": "peering@example.com",
        "notes": "",
        "sent_at": _ts(i * 5),
        "responded_at": _ts(i * 5 + 48) if random.random() > 0.3 else None,
        "classification": "CUI",
    })

_POLICIES = [
    ("IMPORT-PUBLIC-PEERS", 64512, "import", json.dumps({"asn": [13335, 32787, 6939, 2906]}), "accept", json.dumps({}), 100, True),
    ("IMPORT-TRANSIT", 64512, "import", json.dumps({"asn": [2914, 1299, 3257, 3356]}), "accept", json.dumps({"local_pref": 100}), 200, True),
    ("IMPORT-CUSTOMERS", 64512, "import", json.dumps({"asn": [5511, 3320]}), "accept", json.dumps({"local_pref": 200}), 50, True),
    ("EXPORT-PUBLIC", 64512, "export", json.dumps({"community": "64512:100"}), "accept", json.dumps({}), 100, True),
    ("EXPORT-TRANSIT", 64512, "export", json.dumps({"community": "64512:200"}), "accept", json.dumps({}), 200, True),
    ("REJECT-BOGONS", 64512, "import", json.dumps({"prefix_list": "bogons"}), "reject", json.dumps({}), 10, True),
]


def seed_peers(conn) -> int:
    sql = """INSERT OR IGNORE INTO peering_peers (
        id, asn, org_name, peer_type, policy, status, peeringdb_net_id, peeringdb_synced_at,
        ipv4_prefix_count, ipv6_prefix_count, max_prefixes_v4, max_prefixes_v6, traffic_ratio,
        noc_email, irr_as_set, rir, md5_password, multihop, notes, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _PEERS:
        _safe_execute(conn, sql, (
            row[0], row[2], row[1], row[3], row[4], row[5], None, _ts(count * 3),
            row[6], row[7], 1000, 1000, random.uniform(0.5, 3.0), row[10], row[8], row[9], "", 1, "", "CUI",
            _ts(count * 2), _ts(count * 2),
        ))
        count += 1
    return count


def seed_ix(conn) -> int:
    sql = """INSERT OR IGNORE INTO peering_ix (
        id, ix_name, city, country, our_ipv4, our_ipv6, our_speed_gbps, monthly_cost_usd, status,
        peeringdb_ix_id, ixlan_id, routeserver_v4, routeserver_v6, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _IX:
        _safe_execute(conn, sql, (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
            row[9], row[9], row[10], row[11], "CUI", _ts(count * 4), _ts(count * 4),
        ))
        count += 1
    return count


def seed_prefixes(conn) -> int:
    sql = """INSERT OR IGNORE INTO peering_prefixes (
        id, peer_id, prefix, address_family, max_length, origin_asn, rpki_status, roa_found,
        irr_registered, last_validated, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _PREFIXES:
        _safe_execute(conn, sql, (
            row["id"], row["peer_id"], row["prefix"], row["address_family"], row["max_length"],
            row["origin_asn"], row["rpki_status"], row["roa_found"], row["irr_registered"],
            row["last_validated"], row["classification"], row["last_validated"],
        ))
        count += 1
    return count


def seed_requests(conn) -> int:
    sql = """INSERT OR IGNORE INTO peering_requests (
        id, peer_id, request_type, status, ix_id, proposed_speed, contact_method, contact_address,
        notes, sent_at, responded_at, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _REQUESTS:
        _safe_execute(conn, sql, (
            row["id"], row["peer_id"], row["request_type"], row["status"], row["ix_id"],
            row["proposed_speed"], row["contact_method"], row["contact_address"], row["notes"],
            row["sent_at"], row["responded_at"], row["classification"], row["sent_at"], row["sent_at"],
        ))
        count += 1
    return count


def seed_policies(conn) -> int:
    sql = """INSERT OR IGNORE INTO peering_policies (
        id, policy_name, our_asn, policy_type, match_criteria, action, action_params, priority, active,
        classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _POLICIES:
        _safe_execute(conn, sql, (
            _uid(), row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], "CUI", _ts(count * 2), _ts(count * 2),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in ("peering_peers", "peering_ix", "peering_prefixes", "peering_requests", "peering_policies"):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="PMC Demo Seed")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.verify:
            result = verify(conn)
            print(json.dumps(result, indent=2) if args.json else result)
            return

        if args.reset:
            _reset_demo_data(conn)

        counts = {
            "peering_peers": seed_peers(conn),
            "peering_ix": seed_ix(conn),
            "peering_prefixes": seed_prefixes(conn),
            "peering_requests": seed_requests(conn),
            "peering_policies": seed_policies(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_pmc] Seeded {counts}")
            print(f"[seed_pmc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
