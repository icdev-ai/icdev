#!/usr/bin/env python3
# CUI // SP-CTI
"""Sanitize -> Lab Mode — clone a production design for safe lab use.

Redaction pipeline:
  1. Strip all secrets (passwords, API keys, certs, tokens, SNMP communities)
  2. Rewrite IP addresses to RFC1918 space (10.0.0.0/8) deterministically
  3. Disable external peering (BGP to internet / non-private peers)
  4. Mark classification=UNCLASSIFIED
  5. Remove PII/CUI annotations
  6. Log every redaction with before/after (audit trail)

The raw secret values are NEVER stored in the redaction log — only the
"REDACTED" marker plus the location context.

CLI:
    python tools/network/lab_clone.py --design-id <id> --json
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.network.db.init_db import DB_PATH  # noqa: E402


# ── Redaction patterns ──────────────────────────────────────────────────────
_SECRET_PATTERNS = [
    (re.compile(r"password\s*[=:]\s*\S+", re.IGNORECASE), "password"),
    (re.compile(r"secret\s*[=:]\s*\S+", re.IGNORECASE), "secret"),
    (re.compile(r"api[_-]?key\s*[=:]\s*\S+", re.IGNORECASE), "api_key"),
    (
        re.compile(
            r"-----BEGIN\s+[A-Z ]+PRIVATE KEY-----[\s\S]+?-----END\s+[A-Z ]+PRIVATE KEY-----"
        ),
        "private_key",
    ),
    (
        re.compile(r"-----BEGIN\s+CERTIFICATE-----[\s\S]+?-----END\s+CERTIFICATE-----"),
        "certificate",
    ),
    (re.compile(r"token\s*[=:]\s*\S+", re.IGNORECASE), "token"),
    # Match optional Cisco type digit: "enable secret [0-9] <value>"
    (re.compile(r"enable\s+secret(?:\s+\d+)?\s+\S+", re.IGNORECASE), "enable_secret"),
    (re.compile(r"\bcommunity\s+\S+", re.IGNORECASE), "snmp_community"),
]

# Match bare IPv4 addresses
_IPV4_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")

# Private ranges that pass through unchanged
_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),  # multicast (leave unchanged)
]

# PII / CUI annotation keys stripped at the JSON level
_PII_KEYS = {
    "ssn",
    "dob",
    "date_of_birth",
    "full_name",
    "home_address",
    "phone",
    "email_personal",
    "pii",
    "cui_marking",
    "owner_pii",
}

# Substrings in edge/link metadata indicating external peering
_EXTERNAL_PEERING_HINTS = (
    "internet",
    "external",
    "ebgp",
    "public_peer",
    "transit",
    "upstream",
)

_REDACTED_TOKEN = "***REDACTED***"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = get_connection(str(DB_PATH))
    return conn


# ── IP rewriting ────────────────────────────────────────────────────────────
def _is_private(ip: ipaddress.IPv4Address) -> bool:
    for net in _PRIVATE_NETS:
        if ip in net:
            return True
    return False


def rewrite_ip_to_rfc1918(ip_str: str) -> str:
    """Map public IP to RFC1918 (10.0.0.0/8) deterministically.

    Uses SHA256(ip_str) to produce the last three octets. The same input
    always yields the same output. Private / special IPs are returned
    unchanged.
    """
    try:
        ip = ipaddress.IPv4Address(ip_str)
    except (ipaddress.AddressValueError, ValueError):
        return ip_str
    if _is_private(ip):
        return ip_str
    digest = hashlib.sha256(ip_str.encode("utf-8")).digest()
    # Use 3 bytes of digest for octets 2/3/4; keep octet 1 = 10
    b2, b3, b4 = digest[0], digest[1], digest[2]
    # Avoid .0 and .255 on last octet to sidestep network/broadcast pitfalls
    if b4 in (0, 255):
        b4 = (b4 ^ 0x7F) or 1
    return f"10.{b2}.{b3}.{b4}"


def redact_text(text: str) -> tuple:
    """Apply secret + IP redaction to arbitrary text.

    Returns:
        (redacted_text, list_of_redactions)

    Each redaction entry: {type, before, after, match_kind}
    The 'before' field for secrets stores only the redaction marker, never
    the raw secret value.
    """
    if not isinstance(text, str) or not text:
        return text, []

    redactions: list[dict] = []
    out = text

    # 1. Secrets (never capture raw value)
    for rx, kind in _SECRET_PATTERNS:
        def _sub(m, _kind=kind):
            redactions.append({
                "type": "secret",
                "match_kind": _kind,
                "before": _REDACTED_TOKEN,
                "after": _REDACTED_TOKEN,
            })
            return f"{_kind} {_REDACTED_TOKEN}"
        out = rx.sub(_sub, out)

    # 2. IP addresses
    def _ip_sub(m):
        original = m.group(1)
        rewritten = rewrite_ip_to_rfc1918(original)
        if rewritten != original:
            redactions.append({
                "type": "ip",
                "before": original,
                "after": rewritten,
            })
        return rewritten

    out = _IPV4_RE.sub(_ip_sub, out)
    return out, redactions


# ── Structured redaction (walk dict/list) ───────────────────────────────────
def _redact_value(value: Any, location: str, log: list) -> Any:
    """Recursively walk a JSON value, redacting in place.

    Appends per-item entries to `log` with {type, before, after, location}.
    """
    if isinstance(value, dict):
        cleaned: dict = {}
        for k, v in value.items():
            key_lc = str(k).lower()
            # Drop PII/CUI annotations entirely
            if key_lc in _PII_KEYS:
                log.append({
                    "type": "annotation",
                    "before": _REDACTED_TOKEN,
                    "after": None,
                    "location": f"{location}.{k}",
                })
                continue
            # Treat any "*secret*"/"*password*"/"*key*" key as sensitive
            if any(tok in key_lc for tok in ("password", "secret", "api_key", "apikey", "token", "private_key", "community")):
                log.append({
                    "type": "secret",
                    "match_kind": key_lc,
                    "before": _REDACTED_TOKEN,
                    "after": _REDACTED_TOKEN,
                    "location": f"{location}.{k}",
                })
                cleaned[k] = _REDACTED_TOKEN
                continue
            cleaned[k] = _redact_value(v, f"{location}.{k}", log)
        return cleaned
    if isinstance(value, list):
        return [_redact_value(item, f"{location}[{i}]", log) for i, item in enumerate(value)]
    if isinstance(value, str):
        new_text, text_reds = redact_text(value)
        for r in text_reds:
            entry = dict(r)
            entry["location"] = location
            log.append(entry)
        return new_text
    return value


def _disable_external_peering(graph: dict, log: list) -> dict:
    """Walk graph edges; flag/disable ones with external peering hints."""
    edges = graph.get("edges") or graph.get("links") or []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        blob = _canonical_json(edge).lower()
        if any(hint in blob for hint in _EXTERNAL_PEERING_HINTS):
            before_enabled = edge.get("enabled", True)
            edge["enabled"] = False
            edge["lab_disabled_reason"] = "external_peering_redacted"
            log.append({
                "type": "peering",
                "before": f"enabled={before_enabled}",
                "after": "enabled=false",
                "location": f"edge[{edge.get('id') or edge.get('source','?')}->{edge.get('target','?')}]",
            })
    return graph


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ── DB helpers ──────────────────────────────────────────────────────────────
def _fetch_design(conn: sqlite3.Connection, design_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, name, description, graph_json, template_id, classification, "
        "created_at, updated_at FROM topologies WHERE id = ?",
        (design_id,),
    ).fetchone()
    return dict(row) if row else None


def _fetch_objects(conn: sqlite3.Connection, design_id: str) -> list:
    rows = conn.execute(
        "SELECT id, topology_id, object_type, label, config_json, pos_x, pos_y "
        "FROM nc_objects WHERE topology_id = ?",
        (design_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_lineage(conn: sqlite3.Connection, parent_design_id: str) -> list:
    """If parent is itself a clone output, walk the lineage chain.

    `parent_design_id` here refers to the topology id being cloned. If that
    topology is the *output* of a prior clone (stored as lab_project_id in
    nc_lab_clones), we extend that clone's lineage.
    """
    row = conn.execute(
        "SELECT clone_id, parent_design_id, lineage FROM nc_lab_clones "
        "WHERE lab_project_id = ? OR clone_id = ?",
        (parent_design_id, parent_design_id),
    ).fetchone()
    if not row:
        return [parent_design_id]
    try:
        parent_lineage = json.loads(row["lineage"]) or []
    except (json.JSONDecodeError, TypeError):
        parent_lineage = []
    return list(parent_lineage) + [parent_design_id]


# ── Main API ────────────────────────────────────────────────────────────────
def sanitize_to_lab(
    design_id: str,
    created_by: str = "",
    lab_backend: str = "gns3",
) -> dict:
    """Clone design + apply full redaction pipeline.

    Returns:
        {clone_id, parent_design_id, redaction_log, classification,
         new_design_id, lineage}
    """
    conn = _connect()
    try:
        design = _fetch_design(conn, design_id)
        if not design:
            raise ValueError(f"design not found: {design_id}")
        objects = _fetch_objects(conn, design_id)

        log: list[dict] = []

        # 1. Redact topology graph
        try:
            graph = json.loads(design.get("graph_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            graph = {}
        graph = _redact_value(graph, "topology.graph", log)
        # 2. Disable external peering
        if isinstance(graph, dict):
            graph = _disable_external_peering(graph, log)

        # 3. Redact name/description
        new_name = (design.get("name") or "design") + " [LAB]"
        redacted_name, name_reds = redact_text(new_name)
        for r in name_reds:
            entry = dict(r)
            entry["location"] = "topology.name"
            log.append(entry)
        redacted_desc, desc_reds = redact_text(design.get("description") or "")
        for r in desc_reds:
            entry = dict(r)
            entry["location"] = "topology.description"
            log.append(entry)

        # 4. Insert cloned topology (new id)
        new_design_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, template_id, classification, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_design_id,
                redacted_name,
                redacted_desc,
                json.dumps(graph),
                design.get("template_id"),
                "UNCLASSIFIED",
                _now(),
                _now(),
            ),
        )

        # 5. Redact + clone each device config
        for obj in objects:
            try:
                cfg = json.loads(obj.get("config_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                cfg = {}
            loc_prefix = f"node_{obj['id']}_config"
            cfg = _redact_value(cfg, loc_prefix, log)
            redacted_label, label_reds = redact_text(obj.get("label") or "")
            for r in label_reds:
                entry = dict(r)
                entry["location"] = f"node_{obj['id']}.label"
                log.append(entry)

            conn.execute(
                "INSERT INTO nc_objects "
                "(id, topology_id, object_type, label, config_json, pos_x, pos_y) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    new_design_id,
                    obj.get("object_type") or "",
                    redacted_label,
                    json.dumps(cfg),
                    float(obj.get("pos_x") or 0),
                    float(obj.get("pos_y") or 0),
                ),
            )

        # 6. Lineage
        lineage = _fetch_lineage(conn, design_id)

        # 7. Insert clone registry row
        clone_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_lab_clones "
            "(clone_id, parent_design_id, lineage, redaction_log, created_by, "
            " created_at, classification, lab_backend, lab_project_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clone_id,
                design_id,
                json.dumps(lineage),
                json.dumps(log),
                created_by,
                _now(),
                "UNCLASSIFIED",
                lab_backend,
                new_design_id,
            ),
        )
        conn.commit()

        return {
            "clone_id": clone_id,
            "parent_design_id": design_id,
            "new_design_id": new_design_id,
            "lineage": lineage,
            "classification": "UNCLASSIFIED",
            "lab_backend": lab_backend,
            "redaction_log": log,
            "redaction_count": len(log),
        }
    finally:
        conn.close()


def _cli() -> int:
    p = argparse.ArgumentParser(description="Sanitize -> Lab Mode design clone")
    p.add_argument("--design-id", required=True, help="Source design id")
    p.add_argument("--created-by", default="", help="User id")
    p.add_argument("--lab-backend", default="gns3", help="gns3 | containerlab | eve-ng")
    p.add_argument("--json", action="store_true", default=True)
    args = p.parse_args()
    try:
        out = sanitize_to_lab(
            args.design_id,
            created_by=args.created_by,
            lab_backend=args.lab_backend,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "result": out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
