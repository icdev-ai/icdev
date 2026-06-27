#!/usr/bin/env python3
# CUI // SP-CTI
"""GovChain Asset Ledger — government asset tokenization state machine (D-GC-6).

Stores government assets (property, IT equipment, vehicles, comms) in SQLite
with their full lifecycle history. Each state transition is cryptographically
hashed and anchored to the GovChain blockchain via ChainAnchor.

Assets stored in SQLite; blockchain holds integrity proofs only (hybrid model).
See args/asset_tokenization_config.yaml for asset types and state transitions.

Usage:
    python tools/blockchain/asset_ledger.py --register --asset-type it_equipment \\
        --serial "SN-001" --custodian "alice@gov.mil" --json

    python tools/blockchain/asset_ledger.py --transition <asset-id> operational --json

    python tools/blockchain/asset_ledger.py --get <asset-id> --json

    python tools/blockchain/asset_ledger.py --list --asset-type it_equipment --json
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Schema DDL — created on first use (idempotent)
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS govchain_assets (
    id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL,
    serial_number TEXT,
    nsn TEXT,
    state TEXT NOT NULL,
    custodian TEXT,
    location TEXT,
    metadata TEXT DEFAULT '{}',
    token_hash TEXT,
    blockchain_registry_id TEXT,
    blockchain_tx_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS govchain_asset_transitions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT,
    reason TEXT,
    token_hash TEXT,
    blockchain_registry_id TEXT,
    blockchain_tx_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _ensure_schema(conn) -> None:
    conn.executescript(_DDL)
    conn.commit()


def _get_db(db_path: Path = None):
    conn = get_connection(db_path=str(db_path or DB_PATH))
    _ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_CONFIG: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    try:
        import yaml

        with open(BASE_DIR / "args" / "asset_tokenization_config.yaml", "r", encoding="utf-8") as f:
            _CONFIG = yaml.safe_load(f) or {}
    except Exception:
        _CONFIG = {}
    return _CONFIG


def _valid_transitions(from_state: str) -> List[str]:
    cfg = _load_config()
    return cfg.get("state_transitions", {}).get(from_state, [])


def _asset_type_config(asset_type: str) -> Dict[str, Any]:
    cfg = _load_config()
    return cfg.get("asset_types", {}).get(asset_type, {})


def _known_asset_types() -> List[str]:
    cfg = _load_config()
    return list(cfg.get("asset_types", {}).keys())


# ---------------------------------------------------------------------------
# Token hash computation
# ---------------------------------------------------------------------------


def _compute_token_hash(asset_id: str, asset_type: str, serial_number: str, state: str, custodian: str, timestamp: str) -> str:
    canonical = "|".join([
        asset_id or "",
        asset_type or "",
        serial_number or "",
        state or "",
        custodian or "",
        timestamp or "",
    ])
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_asset(
    asset_type: str,
    serial_number: str = "",
    nsn: str = "",
    custodian: str = "",
    location: str = "",
    metadata: dict = None,
    classification: str = "CUI",
    db_path: Path = None,
) -> Dict[str, Any]:
    """Register a new government asset on the ledger.

    The asset starts in the 'procured' state per the state machine.
    A token hash is computed and anchored to GovChain via ChainAnchor.

    Returns:
        dict with asset_id, initial_state, token_hash, and anchor status.
    """
    type_cfg = _asset_type_config(asset_type)
    if not type_cfg:
        known = _known_asset_types()
        return {"ok": False, "error": f"Unknown asset_type '{asset_type}'. Known: {known}"}

    lifecycle_states = type_cfg.get("lifecycle_states", ["procured"])
    initial_state = lifecycle_states[0] if lifecycle_states else "procured"

    asset_id = f"asset-{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc).isoformat()
    token_hash = _compute_token_hash(asset_id, asset_type, serial_number, initial_state, custodian, now)

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO govchain_assets
               (id, asset_type, serial_number, nsn, state, custodian, location, metadata,
                token_hash, classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                asset_id,
                asset_type,
                serial_number,
                nsn,
                initial_state,
                custodian,
                location,
                json.dumps(metadata or {}),
                token_hash,
                classification,
                now,
                now,
            ),
        )

        transition_id = f"txn-{uuid.uuid4().hex[:16]}"
        conn.execute(
            """INSERT INTO govchain_asset_transitions
               (id, asset_id, from_state, to_state, actor, reason, token_hash, classification, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (transition_id, asset_id, None, initial_state, custodian, "Initial registration", token_hash, classification, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Anchor token hash to GovChain
    reg_id = None
    anchor_status = "skipped"
    try:
        from tools.provenance.registry import register_citation
        from tools.blockchain.chain_anchor import ChainAnchor

        reg_id = register_citation(
            citation_type="asset_token",
            source_table="govchain_assets",
            source_record_id=asset_id,
            source_hash=token_hash,
            source_doc=f"{asset_type}:{serial_number or asset_id}",
            classification=classification,
            db_path=db_path,
        )
        if reg_id:
            anchor_result = ChainAnchor(db_path=db_path).anchor_provenance([reg_id])
            anchor_status = anchor_result.get("status", "unknown")

            # Back-fill registry_id + tx_id into asset row
            conn2 = _get_db(db_path)
            try:
                conn2.execute(
                    "UPDATE govchain_assets SET blockchain_registry_id=%s, blockchain_tx_id=%s WHERE id=%s",
                    (reg_id, anchor_result.get("tx_id"), asset_id),
                )
                conn2.commit()
            finally:
                conn2.close()
    except Exception:
        pass

    return {
        "ok": True,
        "asset_id": asset_id,
        "asset_type": asset_type,
        "state": initial_state,
        "serial_number": serial_number,
        "custodian": custodian,
        "token_hash": token_hash,
        "blockchain_registry_id": reg_id,
        "blockchain_anchor_status": anchor_status,
        "created_at": now,
    }


def transition_state(
    asset_id: str,
    new_state: str,
    actor: str = "",
    reason: str = "",
    db_path: Path = None,
) -> Dict[str, Any]:
    """Transition an asset to a new lifecycle state.

    Validates the transition against the state machine, computes a new token hash,
    records the transition history, and anchors the hash to GovChain.

    Returns:
        dict with asset_id, from_state, to_state, token_hash, and anchor status.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, asset_type, serial_number, state, custodian, classification FROM govchain_assets WHERE id=%s",
            (asset_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {"ok": False, "error": f"Asset {asset_id} not found"}

    from_state = row["state"]
    valid = _valid_transitions(from_state)
    if new_state not in valid:
        return {
            "ok": False,
            "error": f"Invalid transition '{from_state}' → '{new_state}'. Valid: {valid}",
            "from_state": from_state,
            "valid_transitions": valid,
        }

    now = datetime.now(timezone.utc).isoformat()
    custodian = actor or row["custodian"] or ""
    token_hash = _compute_token_hash(asset_id, row["asset_type"], row["serial_number"] or "", new_state, custodian, now)
    classification = row["classification"] or "CUI"

    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE govchain_assets SET state=%s, token_hash=%s, updated_at=%s WHERE id=%s",
            (new_state, token_hash, now, asset_id),
        )
        transition_id = f"txn-{uuid.uuid4().hex[:16]}"
        conn.execute(
            """INSERT INTO govchain_asset_transitions
               (id, asset_id, from_state, to_state, actor, reason, token_hash, classification, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (transition_id, asset_id, from_state, new_state, actor, reason, token_hash, classification, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Anchor new token hash
    reg_id = None
    anchor_status = "skipped"
    try:
        from tools.provenance.registry import register_citation
        from tools.blockchain.chain_anchor import ChainAnchor

        reg_id = register_citation(
            citation_type="asset_token",
            source_table="govchain_asset_transitions",
            source_record_id=transition_id,
            source_hash=token_hash,
            source_doc=f"{row['asset_type']}:{asset_id} {from_state}→{new_state}",
            classification=classification,
            db_path=db_path,
        )
        if reg_id:
            anchor_result = ChainAnchor(db_path=db_path).anchor_provenance([reg_id])
            anchor_status = anchor_result.get("status", "unknown")

            conn2 = _get_db(db_path)
            try:
                conn2.execute(
                    "UPDATE govchain_asset_transitions SET blockchain_registry_id=%s, blockchain_tx_id=%s WHERE id=%s",
                    (reg_id, anchor_result.get("tx_id"), transition_id),
                )
                conn2.commit()
            finally:
                conn2.close()
    except Exception:
        pass

    return {
        "ok": True,
        "asset_id": asset_id,
        "transition_id": transition_id,
        "from_state": from_state,
        "to_state": new_state,
        "token_hash": token_hash,
        "blockchain_registry_id": reg_id,
        "blockchain_anchor_status": anchor_status,
        "transitioned_at": now,
    }


def get_asset(asset_id: str, db_path: Path = None) -> Optional[Dict[str, Any]]:
    """Return the current state of an asset plus its full transition history."""
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM govchain_assets WHERE id=%s", (asset_id,)).fetchone()
        if not row:
            return None

        history = conn.execute(
            "SELECT * FROM govchain_asset_transitions WHERE asset_id=%s ORDER BY created_at ASC",
            (asset_id,),
        ).fetchall()

        return {
            **dict(row),
            "metadata": json.loads(row["metadata"] or "{}"),
            "history": [dict(h) for h in history],
        }
    finally:
        conn.close()


def list_assets(
    asset_type: str = "",
    state: str = "",
    limit: int = 100,
    db_path: Path = None,
) -> List[Dict[str, Any]]:
    """List assets with optional type/state filters."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM govchain_assets WHERE 1=1"
        params: List[Any] = []
        if asset_type:
            query += " AND asset_type=?"
            params.append(asset_type)
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [{**dict(r), "metadata": json.loads(r["metadata"] or "{}")} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="GovChain Asset Ledger (D-GC-6)")
    sub = parser.add_subparsers(dest="cmd")

    # --register
    reg_p = sub.add_parser("register", help="Register a new asset")
    reg_p.add_argument("--asset-type", required=True, choices=_known_asset_types() or ["government_property", "it_equipment", "vehicle", "communications_equipment"])
    reg_p.add_argument("--serial", default="", help="Serial number")
    reg_p.add_argument("--nsn", default="", help="National Stock Number")
    reg_p.add_argument("--custodian", default="", help="Asset custodian (email or name)")
    reg_p.add_argument("--location", default="", help="Physical location")

    # --transition
    txn_p = sub.add_parser("transition", help="Transition asset state")
    txn_p.add_argument("asset_id", help="Asset ID")
    txn_p.add_argument("new_state", help="New lifecycle state")
    txn_p.add_argument("--actor", default="", help="Who triggered the transition")
    txn_p.add_argument("--reason", default="", help="Reason for transition")

    # --get
    get_p = sub.add_parser("get", help="Get asset details")
    get_p.add_argument("asset_id", help="Asset ID")

    # --list
    list_p = sub.add_parser("list", help="List assets")
    list_p.add_argument("--asset-type", default="", help="Filter by asset type")
    list_p.add_argument("--state", default="", help="Filter by state")
    list_p.add_argument("--limit", type=int, default=50)

    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    use_json = args.json

    if args.cmd == "register":
        result = register_asset(
            asset_type=args.asset_type,
            serial_number=args.serial,
            nsn=args.nsn,
            custodian=args.custodian,
            location=args.location,
        )
        print(json.dumps(result, indent=2) if use_json else result)

    elif args.cmd == "transition":
        result = transition_state(
            asset_id=args.asset_id,
            new_state=args.new_state,
            actor=getattr(args, "actor", ""),
            reason=getattr(args, "reason", ""),
        )
        print(json.dumps(result, indent=2) if use_json else result)

    elif args.cmd == "get":
        result = get_asset(args.asset_id)
        if result is None:
            print(json.dumps({"error": f"Asset {args.asset_id} not found"}) if use_json else f"Not found: {args.asset_id}")
        else:
            print(json.dumps(result, indent=2, default=str) if use_json else result)

    elif args.cmd == "list":
        assets = list_assets(asset_type=getattr(args, "asset_type", ""), state=getattr(args, "state", ""), limit=args.limit)
        print(json.dumps(assets, indent=2, default=str) if use_json else assets)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
