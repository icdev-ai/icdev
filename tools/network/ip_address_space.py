# CUI // SP-CTI
"""Federal Network Peering Agreement — Step 3: IP Address Space and Routing Policy Definition.

Both parties document the IP address space they will announce (including customer
prefixes) and agree on routing policy constraints such as prefix limits, community
strings, and filtering rules.

Workflow: processify-wfl-f77f / step 3
Status lifecycle: draft → submitted → acknowledged → verified | rejected

Usage:
    conn = get_canvas_connection("NC_DB_PATH")
    from tools.network.ip_address_space import create_ip_space_definition, add_prefix
    defn = create_ip_space_definition(conn,
        initiating_party_name="Agency Alpha",
        responding_party_name="Agency Beta",
        peering_request_id="req-123")
    add_prefix(conn, defn["definition_id"], "192.0.2.0/24",
               party_role="initiating", prefix_type="aggregate")
    set_routing_policy(conn, defn["definition_id"],
                       max_prefixes_initiating=100, max_prefixes_responding=50)
    submit_definition(conn, defn["definition_id"])
    acknowledge_definition(conn, defn["definition_id"])
    approve_definition(conn, defn["definition_id"], party_role="initiating")
    approve_definition(conn, defn["definition_id"], party_role="responding")
"""

from __future__ import annotations

import ipaddress
import json
import uuid
from datetime import datetime, timezone
from typing import Any

WORKFLOW_ID = "processify-wfl-f77f"
STEP_NUMBER = 3
STEP_NAME = "ip_address_space"
CLASSIFICATION = "CUI // SP-CTI"

_VALID_STATUSES = ("draft", "submitted", "acknowledged", "verified", "rejected")
_VALID_PREFIX_TYPES = ("aggregate", "customer", "transit", "blackhole")
_VALID_PARTY_ROLES = ("initiating", "responding")
_VALID_FILTER_ACTIONS = ("reject", "warn")

# RFC 5737 / RFC 3849 documentation ranges (not routable — safe defaults)
_DEFAULT_MAX_PREFIXES = 200
_DEFAULT_MIN_LEN_V4 = 8    # minimum acceptable prefix length IPv4
_DEFAULT_MAX_LEN_V4 = 32
_DEFAULT_MIN_LEN_V6 = 16
_DEFAULT_MAX_LEN_V6 = 128

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS federal_ip_space_definitions (
    id                          TEXT PRIMARY KEY,
    workflow_id                 TEXT NOT NULL DEFAULT 'processify-wfl-f77f',
    step                        INTEGER NOT NULL DEFAULT 3,
    peering_request_id          TEXT,
    asn_exchange_id             TEXT,
    initiating_party_name       TEXT NOT NULL,
    initiating_party_org        TEXT DEFAULT '',
    responding_party_name       TEXT NOT NULL,
    responding_party_org        TEXT DEFAULT '',
    prefixes                    TEXT DEFAULT '[]',
    routing_policy              TEXT DEFAULT '{}',
    initiating_approved         INTEGER DEFAULT 0,
    responding_approved         INTEGER DEFAULT 0,
    approval_notes              TEXT DEFAULT '',
    status                      TEXT NOT NULL DEFAULT 'draft'
                                    CHECK(status IN ('draft','submitted','acknowledged','verified','rejected')),
    rejection_reason            TEXT DEFAULT '',
    definition_document         TEXT DEFAULT '',
    classification              TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_federal_ip_space_wf   ON federal_ip_space_definitions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_federal_ip_space_stat ON federal_ip_space_definitions(status);
CREATE INDEX IF NOT EXISTS idx_federal_ip_space_req  ON federal_ip_space_definitions(peering_request_id);
"""

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS federal_ip_space_definitions (
    id                          TEXT PRIMARY KEY,
    workflow_id                 TEXT NOT NULL DEFAULT 'processify-wfl-f77f',
    step                        INTEGER NOT NULL DEFAULT 3,
    peering_request_id          TEXT,
    asn_exchange_id             TEXT,
    initiating_party_name       TEXT NOT NULL,
    initiating_party_org        TEXT DEFAULT '',
    responding_party_name       TEXT NOT NULL,
    responding_party_org        TEXT DEFAULT '',
    prefixes                    TEXT DEFAULT '[]',
    routing_policy              TEXT DEFAULT '{}',
    initiating_approved         INTEGER DEFAULT 0,
    responding_approved         INTEGER DEFAULT 0,
    approval_notes              TEXT DEFAULT '',
    status                      TEXT NOT NULL DEFAULT 'draft'
                                    CHECK(status IN ('draft','submitted','acknowledged','verified','rejected')),
    rejection_reason            TEXT DEFAULT '',
    definition_document         TEXT DEFAULT '',
    classification              TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_federal_ip_space_wf   ON federal_ip_space_definitions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_federal_ip_space_stat ON federal_ip_space_definitions(status);
CREATE INDEX IF NOT EXISTS idx_federal_ip_space_req  ON federal_ip_space_definitions(peering_request_id);
"""


# ── Private helpers ───────────────────────────────────────────────────────────

def _init_schema(conn) -> None:
    try:
        conn.executescript(SCHEMA_PG)
    except Exception:
        try:
            conn.executescript(SCHEMA_SQLITE)
        except Exception:
            pass


def _exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> None:
    try:
        conn.execute(sql_pg, params)
    except Exception:
        conn.execute(sql_sq, params)


def _fetchall(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> list[dict]:
    def _to_dicts(cur) -> list[dict]:
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    try:
        cur = conn.execute(sql_pg, params)
        return _to_dicts(cur)
    except Exception:
        cur = conn.execute(sql_sq, params)
        return _to_dicts(cur)


def _fetchone(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> dict | None:
    rows = _fetchall(conn, sql_pg, sql_sq, params)
    return rows[0] if rows else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(rec: dict) -> dict:
    """Ensure records have a 'definition_id' alias for 'id'."""
    if rec and "id" in rec and "definition_id" not in rec:
        rec["definition_id"] = rec["id"]
    _decode_json_fields(rec)
    return rec


def _decode_json_fields(rec: dict) -> None:
    for field in ("prefixes", "routing_policy"):
        val = rec.get(field)
        if isinstance(val, str):
            try:
                rec[field] = json.loads(val)
            except Exception:
                rec[field] = [] if field == "prefixes" else {}
        elif val is None:
            rec[field] = [] if field == "prefixes" else {}


def _default_routing_policy() -> dict:
    return {
        "max_prefixes_initiating": _DEFAULT_MAX_PREFIXES,
        "max_prefixes_responding": _DEFAULT_MAX_PREFIXES,
        "min_prefix_length_v4": _DEFAULT_MIN_LEN_V4,
        "max_prefix_length_v4": _DEFAULT_MAX_LEN_V4,
        "min_prefix_length_v6": _DEFAULT_MIN_LEN_V6,
        "max_prefix_length_v6": _DEFAULT_MAX_LEN_V6,
        "accepted_communities": [],
        "rejected_communities": [],
        "local_preference": 100,
        "med": 0,
        "no_export": False,
        "prefix_filter_action": "reject",
        "notes": "",
    }


# ── Public: validation ────────────────────────────────────────────────────────

def validate_prefix(prefix: str) -> tuple[bool, str]:
    """Validate an IPv4 or IPv6 CIDR prefix.

    Returns (True, normalized_prefix) on success, (False, error_message) on failure.
    """
    if not prefix or not isinstance(prefix, str):
        return False, "prefix must be a non-empty string"
    try:
        network = ipaddress.ip_network(prefix.strip(), strict=False)
        return True, str(network)
    except ValueError as exc:
        return False, f"invalid CIDR: {exc}"


# ── Public: CRUD ──────────────────────────────────────────────────────────────

def create_ip_space_definition(
    conn,
    initiating_party_name: str,
    responding_party_name: str,
    initiating_party_org: str = "",
    responding_party_org: str = "",
    peering_request_id: str | None = None,
    asn_exchange_id: str | None = None,
    initial_prefixes: list[dict] | None = None,
) -> dict[str, Any]:
    """Create a new IP address space definition record in draft status.

    initial_prefixes: optional list of prefix dicts, each with keys:
        prefix, party_role, prefix_type (aggregate|customer|transit|blackhole),
        description (optional), is_customer_prefix (bool, optional)
    """
    if not initiating_party_name or not initiating_party_name.strip():
        raise ValueError("initiating_party_name is required")
    if not responding_party_name or not responding_party_name.strip():
        raise ValueError("responding_party_name is required")

    definition_id = str(uuid.uuid4())
    now = _now()
    policy = _default_routing_policy()
    prefixes: list[dict] = []

    if initial_prefixes:
        for p in initial_prefixes:
            validated_prefix = _validate_and_build_prefix_entry(p)
            prefixes.append(validated_prefix)

    _init_schema(conn)
    _exec(
        conn,
        """INSERT INTO federal_ip_space_definitions
           (id, workflow_id, step, peering_request_id, asn_exchange_id,
            initiating_party_name, initiating_party_org,
            responding_party_name, responding_party_org,
            prefixes, routing_policy,
            initiating_approved, responding_approved,
            status, classification, created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        """INSERT INTO federal_ip_space_definitions
           (id, workflow_id, step, peering_request_id, asn_exchange_id,
            initiating_party_name, initiating_party_org,
            responding_party_name, responding_party_org,
            prefixes, routing_policy,
            initiating_approved, responding_approved,
            status, classification, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            definition_id, WORKFLOW_ID, STEP_NUMBER,
            peering_request_id, asn_exchange_id,
            initiating_party_name.strip(), initiating_party_org.strip(),
            responding_party_name.strip(), responding_party_org.strip(),
            json.dumps(prefixes), json.dumps(policy),
            0, 0,
            "draft", CLASSIFICATION, now, now,
        ),
    )
    try:
        conn.commit()
    except Exception:
        pass

    return _normalize({
        "id": definition_id,
        "workflow_id": WORKFLOW_ID,
        "step": STEP_NUMBER,
        "peering_request_id": peering_request_id,
        "asn_exchange_id": asn_exchange_id,
        "initiating_party_name": initiating_party_name.strip(),
        "initiating_party_org": initiating_party_org.strip(),
        "responding_party_name": responding_party_name.strip(),
        "responding_party_org": responding_party_org.strip(),
        "prefixes": prefixes,
        "routing_policy": policy,
        "initiating_approved": 0,
        "responding_approved": 0,
        "approval_notes": "",
        "status": "draft",
        "rejection_reason": "",
        "definition_document": "",
        "classification": CLASSIFICATION,
        "created_at": now,
        "updated_at": now,
    })


def get_ip_space_definition(conn, definition_id: str) -> dict[str, Any] | None:
    """Retrieve a definition by ID. Returns None if not found."""
    rec = _fetchone(
        conn,
        "SELECT * FROM federal_ip_space_definitions WHERE id=%s",
        "SELECT * FROM federal_ip_space_definitions WHERE id=?",
        (definition_id,),
    )
    return _normalize(rec) if rec else None


def list_ip_space_definitions(
    conn,
    workflow_id: str | None = None,
    status: str | None = None,
    peering_request_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List definitions with optional filters."""
    filters = []
    params: list = []

    if workflow_id:
        filters.append("workflow_id")
        params.append(workflow_id)
    if status:
        filters.append("status")
        params.append(status)
    if peering_request_id:
        filters.append("peering_request_id")
        params.append(peering_request_id)

    if filters:
        where_pg = " WHERE " + " AND ".join(f"{f}=%s" for f in filters)
        where_sq = " WHERE " + " AND ".join(f"{f}=?" for f in filters)
    else:
        where_pg = where_sq = ""

    order = f" ORDER BY created_at DESC LIMIT {int(limit)}"
    rows = _fetchall(
        conn,
        f"SELECT * FROM federal_ip_space_definitions{where_pg}{order}",
        f"SELECT * FROM federal_ip_space_definitions{where_sq}{order}",
        tuple(params),
    )
    return [_normalize(r) for r in rows]


# ── Public: prefix management ─────────────────────────────────────────────────

def _validate_and_build_prefix_entry(data: dict) -> dict:
    """Validate and normalise a prefix entry dict."""
    prefix = (data.get("prefix") or "").strip()
    if not prefix:
        raise ValueError("prefix is required")
    valid, result = validate_prefix(prefix)
    if not valid:
        raise ValueError(f"Invalid prefix '{prefix}': {result}")
    norm_prefix = result

    party_role = data.get("party_role", "initiating")
    if party_role not in _VALID_PARTY_ROLES:
        raise ValueError(f"party_role must be one of {_VALID_PARTY_ROLES}, got '{party_role}'")

    prefix_type = data.get("prefix_type", "aggregate")
    if prefix_type not in _VALID_PREFIX_TYPES:
        raise ValueError(f"prefix_type must be one of {_VALID_PREFIX_TYPES}, got '{prefix_type}'")

    return {
        "prefix": norm_prefix,
        "party_role": party_role,
        "prefix_type": prefix_type,
        "description": str(data.get("description") or ""),
        "is_customer_prefix": bool(data.get("is_customer_prefix", False)),
    }


def add_prefix(
    conn,
    definition_id: str,
    prefix: str,
    party_role: str = "initiating",
    prefix_type: str = "aggregate",
    description: str = "",
    is_customer_prefix: bool = False,
) -> dict[str, Any]:
    """Append a prefix entry to the definition's prefix list.

    Definition must not be in terminal status (verified | rejected).
    """
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] in ("verified", "rejected"):
        raise ValueError(
            f"Cannot add prefix to definition in terminal status '{rec['status']}'"
        )

    entry = _validate_and_build_prefix_entry({
        "prefix": prefix,
        "party_role": party_role,
        "prefix_type": prefix_type,
        "description": description,
        "is_customer_prefix": is_customer_prefix,
    })

    existing = list(rec.get("prefixes") or [])
    existing.append(entry)

    now = _now()
    _exec(
        conn,
        "UPDATE federal_ip_space_definitions SET prefixes=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_ip_space_definitions SET prefixes=?, updated_at=? WHERE id=?",
        (json.dumps(existing), now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec["prefixes"] = existing
    rec["updated_at"] = now
    return rec


def remove_prefix(
    conn,
    definition_id: str,
    prefix_index: int,
) -> dict[str, Any]:
    """Remove a prefix by its zero-based index in the prefix list.

    Definition must not be in terminal status.
    """
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] in ("verified", "rejected"):
        raise ValueError(
            f"Cannot remove prefix from definition in terminal status '{rec['status']}'"
        )

    existing = list(rec.get("prefixes") or [])
    if prefix_index < 0 or prefix_index >= len(existing):
        raise ValueError(
            f"prefix_index {prefix_index} out of range (0–{len(existing) - 1})"
        )

    existing.pop(prefix_index)
    now = _now()
    _exec(
        conn,
        "UPDATE federal_ip_space_definitions SET prefixes=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_ip_space_definitions SET prefixes=?, updated_at=? WHERE id=?",
        (json.dumps(existing), now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec["prefixes"] = existing
    rec["updated_at"] = now
    return rec


# ── Public: routing policy ────────────────────────────────────────────────────

def set_routing_policy(
    conn,
    definition_id: str,
    max_prefixes_initiating: int | None = None,
    max_prefixes_responding: int | None = None,
    min_prefix_length_v4: int | None = None,
    max_prefix_length_v4: int | None = None,
    min_prefix_length_v6: int | None = None,
    max_prefix_length_v6: int | None = None,
    accepted_communities: list[str] | None = None,
    rejected_communities: list[str] | None = None,
    local_preference: int | None = None,
    med: int | None = None,
    no_export: bool | None = None,
    prefix_filter_action: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Update the routing policy for a definition.

    Only the supplied keyword arguments are updated; others retain their current values.
    Definition must not be in terminal status.
    """
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] in ("verified", "rejected"):
        raise ValueError(
            f"Cannot update routing policy in terminal status '{rec['status']}'"
        )

    policy = dict(rec.get("routing_policy") or _default_routing_policy())

    if max_prefixes_initiating is not None:
        if int(max_prefixes_initiating) < 0:
            raise ValueError("max_prefixes_initiating must be >= 0")
        policy["max_prefixes_initiating"] = int(max_prefixes_initiating)
    if max_prefixes_responding is not None:
        if int(max_prefixes_responding) < 0:
            raise ValueError("max_prefixes_responding must be >= 0")
        policy["max_prefixes_responding"] = int(max_prefixes_responding)
    if min_prefix_length_v4 is not None:
        policy["min_prefix_length_v4"] = int(min_prefix_length_v4)
    if max_prefix_length_v4 is not None:
        policy["max_prefix_length_v4"] = int(max_prefix_length_v4)
    if min_prefix_length_v6 is not None:
        policy["min_prefix_length_v6"] = int(min_prefix_length_v6)
    if max_prefix_length_v6 is not None:
        policy["max_prefix_length_v6"] = int(max_prefix_length_v6)
    if accepted_communities is not None:
        policy["accepted_communities"] = list(accepted_communities)
    if rejected_communities is not None:
        policy["rejected_communities"] = list(rejected_communities)
    if local_preference is not None:
        policy["local_preference"] = int(local_preference)
    if med is not None:
        policy["med"] = int(med)
    if no_export is not None:
        policy["no_export"] = bool(no_export)
    if prefix_filter_action is not None:
        if prefix_filter_action not in _VALID_FILTER_ACTIONS:
            raise ValueError(
                f"prefix_filter_action must be one of {_VALID_FILTER_ACTIONS}"
            )
        policy["prefix_filter_action"] = prefix_filter_action
    if notes is not None:
        policy["notes"] = str(notes)

    now = _now()
    _exec(
        conn,
        "UPDATE federal_ip_space_definitions SET routing_policy=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_ip_space_definitions SET routing_policy=?, updated_at=? WHERE id=?",
        (json.dumps(policy), now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec["routing_policy"] = policy
    rec["updated_at"] = now
    return rec


# ── Public: lifecycle ─────────────────────────────────────────────────────────

def submit_definition(conn, definition_id: str) -> dict[str, Any]:
    """Advance a draft definition to 'submitted' status.

    Requires at least one prefix to be declared.
    """
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] != "draft":
        raise ValueError(
            f"Definition must be in 'draft' status to submit, current: '{rec['status']}'"
        )
    prefixes = rec.get("prefixes") or []
    if not prefixes:
        raise ValueError(
            "At least one prefix must be declared before submitting"
        )

    now = _now()
    _exec(
        conn,
        "UPDATE federal_ip_space_definitions SET status=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_ip_space_definitions SET status=?, updated_at=? WHERE id=?",
        ("submitted", now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec["status"] = "submitted"
    rec["updated_at"] = now
    return rec


def acknowledge_definition(conn, definition_id: str) -> dict[str, Any]:
    """Responding party acknowledges receipt of the submitted definition."""
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] != "submitted":
        raise ValueError(
            f"Definition must be in 'submitted' status to acknowledge, current: '{rec['status']}'"
        )

    now = _now()
    _exec(
        conn,
        "UPDATE federal_ip_space_definitions SET status=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_ip_space_definitions SET status=?, updated_at=? WHERE id=?",
        ("acknowledged", now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec["status"] = "acknowledged"
    rec["updated_at"] = now
    return rec


def approve_definition(
    conn,
    definition_id: str,
    party_role: str,
    notes: str = "",
) -> dict[str, Any]:
    """Record approval from the given party.

    When both parties have approved, status advances to 'verified'.
    Definition must be in 'acknowledged' status.
    """
    if party_role not in _VALID_PARTY_ROLES:
        raise ValueError(f"party_role must be one of {_VALID_PARTY_ROLES}")

    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] != "acknowledged":
        raise ValueError(
            f"Definition must be in 'acknowledged' status to approve, current: '{rec['status']}'"
        )

    col = "initiating_approved" if party_role == "initiating" else "responding_approved"
    init_v = 1 if party_role == "initiating" else rec.get("initiating_approved", 0)
    resp_v = 1 if party_role == "responding" else rec.get("responding_approved", 0)
    new_status = "verified" if (init_v and resp_v) else rec["status"]

    current_notes = rec.get("approval_notes") or ""
    updated_notes = f"{current_notes}\n{party_role}: {notes}".strip() if notes else current_notes

    now = _now()
    _exec(
        conn,
        f"""UPDATE federal_ip_space_definitions
            SET {col}=%s, approval_notes=%s,
                status=%s, updated_at=%s
            WHERE id=%s""",
        f"""UPDATE federal_ip_space_definitions
            SET {col}=?, approval_notes=?,
                status=?, updated_at=?
            WHERE id=?""",
        (1, updated_notes, new_status, now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec[col] = 1
    rec["approval_notes"] = updated_notes
    rec["status"] = new_status
    rec["updated_at"] = now
    return rec


def reject_definition(
    conn,
    definition_id: str,
    reason: str = "",
) -> dict[str, Any]:
    """Reject the definition. Cannot reject a verified or already-rejected record."""
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")
    if rec["status"] in ("verified", "rejected"):
        raise ValueError(
            f"Definition {definition_id} is already in terminal status '{rec['status']}'"
        )

    now = _now()
    _exec(
        conn,
        """UPDATE federal_ip_space_definitions
           SET status=%s, rejection_reason=%s, updated_at=%s
           WHERE id=%s""",
        """UPDATE federal_ip_space_definitions
           SET status=?, rejection_reason=?, updated_at=?
           WHERE id=?""",
        ("rejected", reason, now, definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    rec.update({"status": "rejected", "rejection_reason": reason, "updated_at": now})
    return rec


# ── Public: document generation ───────────────────────────────────────────────

def generate_definition_document(conn, definition_id: str) -> str:
    """Generate a formal CUI-marked IP Address Space Definition document as plain text."""
    rec = get_ip_space_definition(conn, definition_id)
    if not rec:
        raise ValueError(f"Definition {definition_id} not found")

    generated_at = _now()
    init_name = rec.get("initiating_party_name", "")
    init_org = rec.get("initiating_party_org", "")
    resp_name = rec.get("responding_party_name", "")
    resp_org = rec.get("responding_party_org", "")
    status = rec.get("status", "draft").upper()
    peering_req = rec.get("peering_request_id") or "N/A"
    asn_exchange = rec.get("asn_exchange_id") or "N/A"
    prefixes: list[dict] = rec.get("prefixes") or []
    policy: dict = rec.get("routing_policy") or _default_routing_policy()

    init_prefixes = [p for p in prefixes if p.get("party_role") == "initiating"]
    resp_prefixes = [p for p in prefixes if p.get("party_role") == "responding"]

    def _fmt_prefix_list(entries: list[dict]) -> str:
        if not entries:
            return "  (none declared)\n"
        lines = []
        for e in entries:
            tag = "[CUSTOMER]" if e.get("is_customer_prefix") else ""
            lines.append(
                f"  {e['prefix']:30s}  {e.get('prefix_type', 'aggregate'):12s}"
                f"  {tag:10s}  {e.get('description', '')}\n"
            )
        return "".join(lines)

    communities_accepted = ", ".join(policy.get("accepted_communities") or []) or "(none)"
    communities_rejected = ", ".join(policy.get("rejected_communities") or []) or "(none)"
    no_export_str = "YES" if policy.get("no_export") else "NO"
    filter_action = policy.get("prefix_filter_action", "reject").upper()

    init_approved = "YES" if rec.get("initiating_approved") else "PENDING"
    resp_approved = "YES" if rec.get("responding_approved") else "PENDING"

    notes_section = ""
    if rec.get("approval_notes"):
        notes_section = f"\nAPPROVAL NOTES\n--------------\n{rec['approval_notes']}\n"
    rejection_section = ""
    if rec.get("rejection_reason"):
        rejection_section = f"\nREJECTION REASON\n----------------\n{rec['rejection_reason']}\n"

    policy_notes = policy.get("notes") or ""
    policy_notes_section = (
        f"\nROUTING POLICY NOTES\n--------------------\n{policy_notes}\n"
    ) if policy_notes else ""

    doc = f"""// CUI // SP-CTI //
================================================================================
FEDERAL NETWORK PEERING AGREEMENT
IP Address Space and Routing Policy Definition — Step 3
================================================================================
Generated:          {generated_at}
Definition ID:      {definition_id}
Workflow:           {WORKFLOW_ID}
Peering Request ID: {peering_req}
ASN Exchange ID:    {asn_exchange}
Status:             {status}
Classification:     {CLASSIFICATION}

INITIATING PARTY
----------------
Organization Name:  {init_name}
Legal Entity:       {init_org or '(not provided)'}
Approval Status:    {init_approved}

Prefixes Announced by Initiating Party:
  {'PREFIX':30s}  {'TYPE':12s}  {'CUST?':10s}  DESCRIPTION
  {'-'*78}
{_fmt_prefix_list(init_prefixes)}
RESPONDING PARTY
----------------
Organization Name:  {resp_name}
Legal Entity:       {resp_org or '(not provided)'}
Approval Status:    {resp_approved}

Prefixes Announced by Responding Party:
  {'PREFIX':30s}  {'TYPE':12s}  {'CUST?':10s}  DESCRIPTION
  {'-'*78}
{_fmt_prefix_list(resp_prefixes)}
ROUTING POLICY CONSTRAINTS
--------------------------
Max Prefixes (Initiating → Responding):  {policy.get('max_prefixes_initiating', _DEFAULT_MAX_PREFIXES)}
Max Prefixes (Responding → Initiating):  {policy.get('max_prefixes_responding', _DEFAULT_MAX_PREFIXES)}

IPv4 Prefix Length Limits:   /{policy.get('min_prefix_length_v4', _DEFAULT_MIN_LEN_V4)} – /{policy.get('max_prefix_length_v4', _DEFAULT_MAX_LEN_V4)}
IPv6 Prefix Length Limits:   /{policy.get('min_prefix_length_v6', _DEFAULT_MIN_LEN_V6)} – /{policy.get('max_prefix_length_v6', _DEFAULT_MAX_LEN_V6)}

Accepted BGP Communities:    {communities_accepted}
Rejected BGP Communities:    {communities_rejected}
Local Preference:            {policy.get('local_preference', 100)}
MED (Multi-Exit Discriminator): {policy.get('med', 0)}
NO_EXPORT Community Applied: {no_export_str}
Prefix Filter Violation:     {filter_action}

COMPLIANCE REQUIREMENTS
-----------------------
Both parties agree that:
  1. All announced prefixes are owned or legally authorized for announcement.
  2. Route Origin Authorizations (ROAs) are configured in RPKI for all prefixes.
  3. Customer prefixes require written authorization from the originating entity.
  4. Prefix lengths outside the stated limits will be filtered per the policy above.
  5. BGP communities listed as rejected will be stripped at the peering point.
  6. Prefix limits are hard limits; sessions will be torn down if exceeded.
  7. Any addition of new prefixes requires written amendment and re-approval.
  8. All changes are logged to the audit trail per NIST 800-53 AU-2, AU-3.

COMPLIANCE REFERENCES
---------------------
  - NIST SP 800-189: Resilient Interdomain Traffic Exchange
  - NIST SP 800-53: CM-3 (Configuration Change Control), AC-3, AU-2, AU-3
  - DHS CISA BGP Security Guide (2023)
  - ARIN RPKI & ROA Policy
  - Federal BGP Security Guidance (CISA AA23-040A)
  - FISMA, FedRAMP, CMMC 2.0 Level 2
{notes_section}{policy_notes_section}{rejection_section}
================================================================================
// END OF DOCUMENT — CUI // SP-CTI //
================================================================================
"""

    _exec(
        conn,
        "UPDATE federal_ip_space_definitions SET definition_document=%s, updated_at=%s WHERE id=%s",
        "UPDATE federal_ip_space_definitions SET definition_document=?, updated_at=? WHERE id=?",
        (doc, _now(), definition_id),
    )
    try:
        conn.commit()
    except Exception:
        pass

    return doc


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sqlite3

    parser = argparse.ArgumentParser(
        description="Federal Network Peering — IP Address Space Definition (step 3)"
    )
    parser.add_argument("--db", default=":memory:", help="Path to SQLite DB")
    parser.add_argument("--create", action="store_true", help="Create a sample definition")
    parser.add_argument("--list", action="store_true", help="List all definitions")
    parser.add_argument("--definition-id", help="Definition ID for operations")
    parser.add_argument("--add-prefix", help="CIDR prefix to add")
    parser.add_argument("--party-role", default="initiating", choices=["initiating", "responding"])
    parser.add_argument("--prefix-type", default="aggregate", choices=list(_VALID_PREFIX_TYPES))
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--acknowledge", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--reject", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--document", action="store_true", help="Generate document")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)

    result: Any = None

    if args.create:
        result = create_ip_space_definition(
            conn,
            initiating_party_name="Agency Alpha",
            responding_party_name="Agency Beta",
        )
        add_prefix(conn, result["definition_id"], "192.0.2.0/24",
                   party_role="initiating", prefix_type="aggregate",
                   description="Primary aggregate block")
        add_prefix(conn, result["definition_id"], "198.51.100.0/24",
                   party_role="responding", prefix_type="aggregate",
                   description="Responding party aggregate")
        result = get_ip_space_definition(conn, result["definition_id"])
    elif args.list:
        result = list_ip_space_definitions(conn)
    elif args.definition_id:
        did = args.definition_id
        if args.add_prefix:
            result = add_prefix(conn, did, args.add_prefix,
                                party_role=args.party_role,
                                prefix_type=args.prefix_type)
        elif args.submit:
            result = submit_definition(conn, did)
        elif args.acknowledge:
            result = acknowledge_definition(conn, did)
        elif args.approve:
            result = approve_definition(conn, did, party_role=args.party_role)
        elif args.reject:
            result = reject_definition(conn, did, reason=args.reason)
        elif args.document:
            result = generate_definition_document(conn, did)
        else:
            result = get_ip_space_definition(conn, did)

    conn.close()

    if result is not None:
        if args.json and not isinstance(result, str):
            import json as _json
            print(_json.dumps(result, indent=2, default=str))
        else:
            print(result)
