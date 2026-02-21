#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Provenance Tracker — Supply chain provenance for published assets.

Tracks the full provenance chain of marketplace assets: who published what,
when, from where, with what dependencies, signed by whom.

Integrates with the existing artifact signer (tools/saas/artifacts/signer.py)
and supply chain tools (tools/supply_chain/) for comprehensive provenance.

Usage:
    # Record provenance for an asset version
    python tools/marketplace/provenance_tracker.py --record \\
        --asset-id "asset-abc" --version-id "ver-abc" \\
        --publisher-user "john.doe@mil" \\
        --publisher-org "Army PEO EIS" \\
        --source-repo "https://gitlab.mil/project" --json

    # Get provenance chain for an asset
    python tools/marketplace/provenance_tracker.py --get \\
        --asset-id "asset-abc" --json

    # Verify provenance (signature + hash check)
    python tools/marketplace/provenance_tracker.py --verify \\
        --asset-id "asset-abc" --version-id "ver-abc" \\
        --asset-path /path/to/asset --json

    # Generate provenance report
    python tools/marketplace/provenance_tracker.py --report \\
        --asset-id "asset-abc" --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Graceful imports
try:
    from tools.saas.artifacts.signer import verify_signature
    _HAS_SIGNER = True
except ImportError:
    _HAS_SIGNER = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1


def _get_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _gen_id(prefix="prov"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_dir(dir_path):
    """Compute SHA-256 hex digest of a directory."""
    h = hashlib.sha256()
    dir_path = Path(dir_path)
    for fpath in sorted(dir_path.rglob("*")):
        if fpath.is_file():
            h.update(str(fpath.relative_to(dir_path)).encode())
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Provenance operations
# ---------------------------------------------------------------------------

def record_provenance(asset_id, version_id, publisher_user, publisher_org=None,
                      source_repo=None, build_id=None, db_path=None):
    """Record provenance metadata for an asset version.

    Updates the marketplace_versions record with provenance fields
    and records a comprehensive audit trail entry.
    """
    conn = _get_db(db_path)
    try:
        # Get version details
        version = conn.execute(
            "SELECT * FROM marketplace_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if not version:
            raise ValueError(f"Version not found: {version_id}")

        # Get asset details
        asset = conn.execute(
            "SELECT * FROM marketplace_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not asset:
            raise ValueError(f"Asset not found: {asset_id}")

        # Get dependencies
        deps = conn.execute(
            "SELECT depends_on_slug, version_constraint FROM marketplace_dependencies WHERE asset_id = ?",
            (asset_id,),
        ).fetchall()

        # Get scan results
        scans = conn.execute(
            """SELECT gate_name, status, findings_count
               FROM marketplace_scan_results WHERE version_id = ?""",
            (version_id,),
        ).fetchall()

        provenance = {
            "provenance_id": _gen_id("prov"),
            "asset_id": asset_id,
            "asset_name": asset["name"],
            "version_id": version_id,
            "version": version["version"],
            "publisher": {
                "user": publisher_user,
                "org": publisher_org or asset["publisher_org"],
                "tenant_id": asset["publisher_tenant_id"],
            },
            "source": {
                "repo": source_repo,
                "build_id": build_id,
            },
            "integrity": {
                "sha256": version["sha256_hash"],
                "signature": version["signature"],
                "signed_by": version["signed_by"],
            },
            "dependencies": [
                {"slug": d["depends_on_slug"], "constraint": d["version_constraint"]}
                for d in deps
            ],
            "security_scans": [
                {"gate": s["gate_name"], "status": s["status"], "findings": s["findings_count"]}
                for s in scans
            ],
            "classification": asset["classification"],
            "impact_level": asset["impact_level"],
            "recorded_at": _now(),
        }

        # Store provenance in version metadata
        existing_meta = json.loads(version["metadata"]) if version["metadata"] else {}
        existing_meta["provenance"] = provenance
        conn.execute(
            "UPDATE marketplace_versions SET metadata = ? WHERE id = ?",
            (json.dumps(existing_meta), version_id),
        )
        conn.commit()

        if _HAS_AUDIT:
            try:
                audit_log_event(
                    event_type="marketplace_asset_published",
                    actor=publisher_user,
                    action=f"Recorded provenance for {asset['name']} v{version['version']}",
                    details=provenance,
                    db_path=DB_PATH,
                )
            except Exception:
                pass

        return provenance
    finally:
        conn.close()


def get_provenance(asset_id, version_id=None, db_path=None):
    """Get provenance chain for an asset (latest version or specified)."""
    conn = _get_db(db_path)
    try:
        if version_id:
            version = conn.execute(
                "SELECT * FROM marketplace_versions WHERE id = ?", (version_id,)
            ).fetchone()
        else:
            version = conn.execute(
                """SELECT * FROM marketplace_versions
                   WHERE asset_id = ? ORDER BY created_at DESC LIMIT 1""",
                (asset_id,),
            ).fetchone()

        if not version:
            return {"error": "No version found"}

        meta = json.loads(version["metadata"]) if version["metadata"] else {}
        provenance = meta.get("provenance", {})

        # Enrich with current state
        provenance["current_hash"] = version["sha256_hash"]
        provenance["version_status"] = version["status"]

        return provenance
    finally:
        conn.close()


def verify_provenance(asset_id, version_id, asset_path, public_key_path=None, db_path=None):
    """Verify provenance: check hash matches, signature valid."""
    conn = _get_db(db_path)
    try:
        version = conn.execute(
            "SELECT * FROM marketplace_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if not version:
            return {"verified": False, "reason": "Version not found"}

        results = {"checks": {}}

        # Check 1: Hash verification
        current_hash = _sha256_dir(asset_path)
        stored_hash = version["sha256_hash"]
        hash_match = current_hash == stored_hash
        results["checks"]["hash"] = {
            "status": "pass" if hash_match else "fail",
            "stored_hash": stored_hash[:16] + "...",
            "current_hash": current_hash[:16] + "...",
        }

        # Check 2: Signature verification (if available)
        if version["signature"] and _HAS_SIGNER and public_key_path:
            try:
                sig_ok = verify_signature(
                    str(asset_path), version["signature"], public_key_path
                )
                results["checks"]["signature"] = {
                    "status": "pass" if sig_ok else "fail",
                    "signed_by": version["signed_by"],
                }
            except Exception as e:
                results["checks"]["signature"] = {
                    "status": "error",
                    "error": str(e),
                }
        elif version["signature"]:
            results["checks"]["signature"] = {
                "status": "skipped",
                "reason": "No public key provided or signer unavailable",
            }

        # Check 3: Scan results exist
        scans = conn.execute(
            "SELECT COUNT(*) as cnt FROM marketplace_scan_results WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        results["checks"]["scan_records"] = {
            "status": "pass" if scans["cnt"] > 0 else "warning",
            "scan_count": scans["cnt"],
        }

        all_pass = all(
            c.get("status") in ("pass", "skipped")
            for c in results["checks"].values()
        )
        results["verified"] = all_pass
        return results
    finally:
        conn.close()


def generate_report(asset_id, db_path=None):
    """Generate a comprehensive provenance report for an asset."""
    conn = _get_db(db_path)
    try:
        asset = conn.execute(
            "SELECT * FROM marketplace_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if not asset:
            return {"error": "Asset not found"}

        versions = conn.execute(
            "SELECT * FROM marketplace_versions WHERE asset_id = ? ORDER BY created_at",
            (asset_id,),
        ).fetchall()

        version_chain = []
        for v in versions:
            meta = json.loads(v["metadata"]) if v["metadata"] else {}
            version_chain.append({
                "version": v["version"],
                "hash": v["sha256_hash"],
                "signed": bool(v["signature"]),
                "signed_by": v["signed_by"],
                "published_by": v["published_by"],
                "created_at": v["created_at"],
                "provenance": meta.get("provenance"),
            })

        deps = conn.execute(
            "SELECT * FROM marketplace_dependencies WHERE asset_id = ?",
            (asset_id,),
        ).fetchall()

        installations = conn.execute(
            "SELECT tenant_id, status, installed_at FROM marketplace_installations WHERE asset_id = ?",
            (asset_id,),
        ).fetchall()

        return {
            "report_type": "provenance",
            "asset": {
                "id": asset["id"],
                "name": asset["name"],
                "slug": asset["slug"],
                "classification": asset["classification"],
                "impact_level": asset["impact_level"],
                "publisher_org": asset["publisher_org"],
                "catalog_tier": asset["catalog_tier"],
            },
            "version_chain": version_chain,
            "dependencies": [dict(d) for d in deps],
            "installations": [dict(i) for i in installations],
            "generated_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV Marketplace Provenance Tracker")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="Record provenance")
    group.add_argument("--get", action="store_true", help="Get provenance chain")
    group.add_argument("--verify", action="store_true", help="Verify provenance")
    group.add_argument("--report", action="store_true", help="Generate provenance report")

    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--version-id")
    parser.add_argument("--publisher-user")
    parser.add_argument("--publisher-org")
    parser.add_argument("--source-repo")
    parser.add_argument("--build-id")
    parser.add_argument("--asset-path")
    parser.add_argument("--public-key")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.record:
            if not all([args.version_id, args.publisher_user]):
                parser.error("--record requires --version-id, --publisher-user")
            result = record_provenance(
                asset_id=args.asset_id, version_id=args.version_id,
                publisher_user=args.publisher_user,
                publisher_org=args.publisher_org,
                source_repo=args.source_repo,
                build_id=args.build_id, db_path=db_path,
            )
        elif args.get:
            result = get_provenance(args.asset_id, args.version_id, db_path)
        elif args.verify:
            if not all([args.version_id, args.asset_path]):
                parser.error("--verify requires --version-id, --asset-path")
            result = verify_provenance(
                args.asset_id, args.version_id, args.asset_path,
                args.public_key, db_path,
            )
        elif args.report:
            result = generate_report(args.asset_id, db_path)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for k, v in result.items():
                print(f"  {k}: {v}")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
