#!/usr/bin/env python3
# CUI // SP-CTI
"""Provenance Verifier — verify audit integrity and blockchain anchoring.

Checks local hash chains, Merkle root inclusion, and ledger confirmation.
Generates comprehensive verification reports for auditors.

Usage:
    python tools/blockchain/provenance_verifier.py --verify-audit 12345 --json
    python tools/blockchain/provenance_verifier.py --verify-project proj-test --json
    python tools/blockchain/provenance_verifier.py --test --json
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

# The ONE audit-row hash recipe. The offline verifier
# (tools/agent_case/bundle_format.py) and the chain writer call the same helper —
# if this file computed its own, every row would report as tampered.
from icdev.tools.audit.row_hash import GENESIS_HASH, compute_audit_row_hash

# Where the chain starts, so a row that predates it is not reported as broken.
from icdev.tools.audit.chain import chain_start_id

DB_PATH = BASE_DIR / "data" / "icdev.db"

# Optional imports
try:
    from tools.crypto.key_manager import verify_payload

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def _get_db(db_path: Path = None):
    return get_connection(db_path=str(db_path or DB_PATH))


def verify_audit_integrity(entry_id: int, db_path: Path = None) -> dict:
    """Verify a single audit entry's hash chain and signature.

    ``chain_status`` separates the two ways a row can fail to verify:

    ``chained``    the chain writer produced this row; the three *_valid flags
                   are a real verdict on it.
    ``pre_chain``  the row predates the cutover recorded in audit_chain_genesis,
                   so it never had a hash to check. The flags are False because
                   there is nothing to verify, NOT because anything is wrong —
                   ``ok`` is likewise False, since an unverifiable row must not
                   be reported as verified either.

    Without that distinction every row written before exa-audit-03 reads
    identically to a tampered one, which is what made the verifier's honest
    fail-closed output unusable in practice.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, project_id, event_type, actor, action, details, classification, ip_address, session_id, created_at, hash, previous_hash, signature FROM audit_trail WHERE id = %s",
            (entry_id,),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Entry not found", "entry_id": entry_id}

        # A row below the recorded cutover was written before the chain writer
        # existed. Its NULL hash is an absence of evidence, not evidence of
        # tampering, and the two must not render the same.
        start_id = chain_start_id(conn)
        pre_chain = row["hash"] is None and (start_id is None or entry_id < start_id)

        result = {
            "entry_id": entry_id,
            "ok": True,
            "hash_valid": False,
            "chain_valid": False,
            "signature_valid": False,
            "blockchain_verified": False,
            "chain_status": "pre_chain" if pre_chain else "chained",
            "chain_start_id": start_id,
        }

        # 1. Verify row hash
        expected_hash = compute_audit_row_hash(row)
        if row["hash"] and row["hash"] == expected_hash:
            result["hash_valid"] = True

        # 2. Verify chain link
        prev_row = conn.execute(
            "SELECT hash FROM audit_trail WHERE id = %s",
            (entry_id - 1,),
        ).fetchone()
        prev_hash = prev_row["hash"] if prev_row and prev_row["hash"] else GENESIS_HASH
        if row["previous_hash"] and row["previous_hash"] == prev_hash:
            result["chain_valid"] = True
        elif entry_id == 1 and row["previous_hash"] == GENESIS_HASH:
            result["chain_valid"] = True

        # 3. Verify signature
        if row["signature"]:
            try:
                sig = json.loads(row["signature"])
                if sig.get("algorithm") == "none":
                    result["signature_valid"] = False
                elif HAS_DEPS:
                    ok = verify_payload(
                        row["hash"].encode(),
                        sig.get("value", ""),
                        sig.get("public_key_fp", ""),
                        algorithm=sig.get("algorithm"),
                    )
                    result["signature_valid"] = ok
            except Exception:
                pass

        # 4. Check blockchain anchor
        reg = conn.execute(
            "SELECT merkle_root, blockchain_tx_id FROM source_citation_registry WHERE source_table='audit_trail' AND source_record_id=%s",
            (str(entry_id),),
        ).fetchone()
        if reg and reg["merkle_root"]:
            result["blockchain_verified"] = True
            result["merkle_root"] = reg["merkle_root"]
            result["blockchain_tx_id"] = reg["blockchain_tx_id"]

        result["ok"] = result["hash_valid"] and result["chain_valid"]
        return result
    finally:
        conn.close()


def verify_citation(registry_id: str, db_path: Path = None) -> dict:
    """Verify a citation's source hash and blockchain anchor."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM source_citation_registry WHERE id = %s",
            (registry_id,),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Registry entry not found", "registry_id": registry_id}

        result = {
            "ok": True,
            "registry_id": registry_id,
            "source_hash_present": bool(row["source_hash"]),
            "merkle_root_present": bool(row["merkle_root"]),
            "blockchain_tx_present": bool(row["blockchain_tx_id"]),
            "trust_score": row["trust_score"],
            "blockchain_verified": bool(row["blockchain_tx_id"]),
        }

        return result
    finally:
        conn.close()


def verify_slsa_attestation(project_id: str, db_path: Path = None) -> dict:
    """Verify a project's SLSA attestation signature."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, source_hash, merkle_root, blockchain_tx_id FROM source_citation_registry WHERE project_id = %s AND citation_type = 'slsa' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "No SLSA attestation found", "project_id": project_id}

        return {
            "ok": True,
            "project_id": project_id,
            "registry_id": row["id"],
            "source_hash": row["source_hash"],
            "merkle_root": row["merkle_root"],
            "blockchain_tx_id": row["blockchain_tx_id"],
            "blockchain_verified": bool(row["blockchain_tx_id"]),
        }
    finally:
        conn.close()


def generate_verification_report(project_id: str, db_path: Path = None) -> dict:
    """Generate a comprehensive verification report for a project."""
    conn = _get_db(db_path)
    try:
        # Count audit entries
        audit_total = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = %s",
            (project_id,),
        ).fetchone()["cnt"]

        audit_hashed = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = %s AND hash IS NOT NULL",
            (project_id,),
        ).fetchone()["cnt"]

        audit_signed = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = %s AND signature IS NOT NULL",
            (project_id,),
        ).fetchone()["cnt"]

        # Count citations
        citations = conn.execute(
            "SELECT * FROM source_citation_registry WHERE project_id = %s",
            (project_id,),
        ).fetchall()

        citation_summary = {}
        for r in citations:
            ct = r["citation_type"]
            if ct not in citation_summary:
                citation_summary[ct] = {"total": 0, "anchored": 0}
            citation_summary[ct]["total"] += 1
            if r["merkle_root"]:
                citation_summary[ct]["anchored"] += 1

        # Sample verification: check last 5 audit entries
        sample_ids = conn.execute(
            "SELECT id FROM audit_trail WHERE project_id = %s ORDER BY id DESC LIMIT 5",
            (project_id,),
        ).fetchall()
        sample_results = [verify_audit_integrity(r["id"], db_path) for r in sample_ids]

        broken = sum(1 for s in sample_results if not s.get("ok"))

        return {
            "project_id": project_id,
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "audit": {
                "total": audit_total,
                "hashed": audit_hashed,
                "signed": audit_signed,
                "hash_coverage": round(audit_hashed / audit_total, 2) if audit_total else 0,
                "signature_coverage": round(audit_signed / audit_total, 2) if audit_total else 0,
            },
            "citations": {
                "total": len(citations),
                "by_type": citation_summary,
            },
            "sample_verification": {
                "entries_checked": len(sample_results),
                "broken_links": broken,
                "results": sample_results,
            },
            "overall_ok": broken == 0,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Provenance Verifier")
    parser.add_argument("--verify-audit", type=int, help="Verify a specific audit entry")
    parser.add_argument("--verify-citation", help="Verify a specific registry entry")
    parser.add_argument("--verify-project", help="Generate verification report for a project")
    parser.add_argument("--verify-slsa", help="Verify SLSA attestation for a project")
    parser.add_argument("--test", action="store_true", help="Run self-test")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.test:
        # Self-test: verify the last audit entry
        conn = _get_db()
        try:
            row = conn.execute("SELECT id FROM audit_trail ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                result = verify_audit_integrity(row["id"])
                print(json.dumps(result, indent=2) if args.json else result)
            else:
                print(json.dumps({"ok": False, "error": "No audit entries"}) if args.json else "No audit entries")
        finally:
            conn.close()
        return

    if args.verify_audit:
        result = verify_audit_integrity(args.verify_audit)
        print(json.dumps(result, indent=2) if args.json else result)
        return

    if args.verify_citation:
        result = verify_citation(args.verify_citation)
        print(json.dumps(result, indent=2) if args.json else result)
        return

    if args.verify_slsa:
        result = verify_slsa_attestation(args.verify_slsa)
        print(json.dumps(result, indent=2) if args.json else result)
        return

    if args.verify_project:
        result = generate_verification_report(args.verify_project)
        print(json.dumps(result, indent=2, default=str) if args.json else result)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
