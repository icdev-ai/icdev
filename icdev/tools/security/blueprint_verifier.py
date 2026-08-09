#!/usr/bin/env python3
# CUI // SP-CTI
"""Blueprint Digest Verifier — NemoClaw-adapted supply chain integrity (D-NC-3).

Computes SHA-256 recursive directory digests for genome artifacts, marketplace
assets, and child app packages.  Verified before absorption, installation, or
propagation.  Follows NemoClaw's supply chain integrity pattern.

Algorithm (NemoClaw):
    1. Walk directory recursively, sorted by relative path.
    2. For each file: hash = SHA-256(relative_path + file_content).
    3. Directory digest = SHA-256(concat of all file hashes).
    4. Compare against stored/published digest.

Key design:
    - Uses stdlib hashlib (air-gap safe, zero deps).
    - Cross-platform path normalization (forward slashes always).
    - Append-only digest log (NIST AU, D6).
    - Supports genome, marketplace_asset, child_app, capability entity types.

CLI:
    python tools/security/blueprint_verifier.py --compute --path /path/to/dir --json
    python tools/security/blueprint_verifier.py --verify --path /path/to/dir --expected <digest> --json
    python tools/security/blueprint_verifier.py --store --entity-type genome --entity-id v1.2.0 --path /path --json
    python tools/security/blueprint_verifier.py --lookup --entity-type genome --entity-id v1.2.0 --json
    python tools/security/blueprint_verifier.py --history --entity-type genome --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

ENTITY_TYPES = ("genome", "marketplace_asset", "child_app", "capability", "propagation")

# Directories and files to skip during digest computation
SKIP_PATTERNS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".tmp",
    ".DS_Store",
    "Thumbs.db",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_path(path: str) -> str:
    """Normalize path to forward slashes for cross-platform consistency."""
    return path.replace("\\", "/")


class BlueprintVerifier:
    """SHA-256 recursive directory digest verification (D-NC-3).

    Follows NemoClaw supply chain integrity pattern:
    immutable, versioned artifacts verified before execution.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._ensure_tables()

    def _get_db(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        conn = self._get_db()
        entity_check = ", ".join(f"'{t}'" for t in ENTITY_TYPES)
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS blueprint_digests (
                id              TEXT PRIMARY KEY,
                entity_type     TEXT NOT NULL CHECK(entity_type IN ({entity_check})),
                entity_id       TEXT NOT NULL,
                digest          TEXT NOT NULL,
                file_count      INTEGER NOT NULL,
                total_bytes     INTEGER NOT NULL,
                directory_path  TEXT,
                computed_at     TEXT NOT NULL,
                verified_at     TEXT,
                verified_by     TEXT,
                verification_result TEXT CHECK(verification_result IN ('pass', 'fail', NULL))
            );
            CREATE INDEX IF NOT EXISTS idx_bd_entity ON blueprint_digests(entity_type, entity_id);
            CREATE INDEX IF NOT EXISTS idx_bd_digest ON blueprint_digests(digest);
            CREATE INDEX IF NOT EXISTS idx_bd_computed ON blueprint_digests(computed_at);
        """)
        conn.commit()
        conn.close()

    def _should_skip(self, name: str) -> bool:
        """Check if a file or directory should be skipped."""
        return name in SKIP_PATTERNS or name.startswith(".")

    def compute_digest(self, directory: Path) -> Dict:
        """Compute SHA-256 recursive directory digest.

        Returns:
            Dict with digest, file_count, total_bytes, file_hashes.
        """
        directory = Path(directory).resolve()
        if not directory.is_dir():
            return {"error": f"Not a directory: {directory}"}

        file_hashes: List[Tuple[str, str, int]] = []
        total_bytes = 0

        # Walk directory recursively, sorted by relative path
        for root, dirs, files in os.walk(str(directory)):
            # Filter out skip patterns from dirs (modifies in-place for os.walk)
            dirs[:] = sorted(d for d in dirs if not self._should_skip(d))
            root_path = Path(root)

            for filename in sorted(files):
                if self._should_skip(filename):
                    continue

                filepath = root_path / filename
                if not filepath.is_file():
                    continue

                # Compute relative path with forward slashes
                rel_path = _normalize_path(str(filepath.relative_to(directory)))

                # Hash = SHA-256(relative_path + file_content)
                hasher = hashlib.sha256()
                hasher.update(rel_path.encode("utf-8"))

                try:
                    file_size = filepath.stat().st_size
                    with open(filepath, "rb") as f:
                        while True:
                            chunk = f.read(65536)  # 64KB chunks
                            if not chunk:
                                break
                            hasher.update(chunk)
                except (OSError, PermissionError) as exc:
                    return {"error": f"Cannot read file {rel_path}: {exc}"}

                file_hash = hasher.hexdigest()
                file_hashes.append((rel_path, file_hash, file_size))
                total_bytes += file_size

        if not file_hashes:
            return {
                "digest": hashlib.sha256(b"").hexdigest(),
                "file_count": 0,
                "total_bytes": 0,
                "file_hashes": [],
                "directory": str(directory),
            }

        # Directory digest = SHA-256(concat of all file hashes)
        dir_hasher = hashlib.sha256()
        for _, fhash, _ in file_hashes:
            dir_hasher.update(fhash.encode("utf-8"))

        return {
            "digest": dir_hasher.hexdigest(),
            "file_count": len(file_hashes),
            "total_bytes": total_bytes,
            "file_hashes": [{"path": p, "hash": h, "size": s} for p, h, s in file_hashes],
            "directory": str(directory),
        }

    def verify_digest(self, directory: Path, expected_digest: str) -> Dict:
        """Verify directory digest against expected value.

        Returns:
            Dict with verified (bool), mismatches if any.
        """
        result = self.compute_digest(directory)
        if "error" in result:
            return result

        actual = result["digest"]
        verified = actual == expected_digest

        response = {
            "verified": verified,
            "expected": expected_digest,
            "actual": actual,
            "file_count": result["file_count"],
            "total_bytes": result["total_bytes"],
            "directory": str(directory),
        }

        if not verified:
            response["mismatches"] = self._find_mismatches(directory, expected_digest)

        return response

    def _find_mismatches(self, directory: Path, expected_digest: str) -> List[Dict]:
        """Attempt to identify which files changed (requires stored file hashes)."""
        conn = self._get_db()
        row = conn.execute(
            """SELECT id FROM blueprint_digests
               WHERE digest = %s ORDER BY computed_at DESC LIMIT 1""",
            (expected_digest,),
        ).fetchone()
        conn.close()

        if not row:
            return [{"message": "No stored file hashes for expected digest — cannot identify specific mismatches"}]

        return [{"message": "Digest mismatch detected — full file-level diff requires stored file_hashes"}]

    def store_digest(self, entity_type: str, entity_id: str, directory: Path, computed_by: str = "") -> Dict:
        """Compute and store a digest for an entity."""
        if entity_type not in ENTITY_TYPES:
            return {"error": f"Invalid entity_type: {entity_type}", "valid_types": list(ENTITY_TYPES)}

        result = self.compute_digest(directory)
        if "error" in result:
            return result

        record_id = str(uuid.uuid4())
        conn = self._get_db()
        conn.execute(
            """INSERT INTO blueprint_digests
               (id, entity_type, entity_id, digest, file_count, total_bytes,
                directory_path, computed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                record_id,
                entity_type,
                entity_id,
                result["digest"],
                result["file_count"],
                result["total_bytes"],
                str(directory),
                _now(),
            ),
        )
        conn.commit()
        conn.close()

        return {
            "stored": True,
            "record_id": record_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "digest": result["digest"],
            "file_count": result["file_count"],
            "total_bytes": result["total_bytes"],
        }

    def verify_and_record(self, entity_type: str, entity_id: str, directory: Path, verified_by: str = "") -> Dict:
        """Verify a directory against the stored digest for an entity."""
        conn = self._get_db()
        row = conn.execute(
            """SELECT digest, id FROM blueprint_digests
               WHERE entity_type = %s AND entity_id = %s
               ORDER BY computed_at DESC LIMIT 1""",
            (entity_type, entity_id),
        ).fetchone()

        if not row:
            conn.close()
            return {"error": "no_stored_digest", "entity_type": entity_type, "entity_id": entity_id}

        expected = row["digest"]
        record_id = row["id"]
        result = self.verify_digest(directory, expected)

        # Record verification result
        conn.execute(
            """UPDATE blueprint_digests
               SET verified_at = %s, verified_by = %s, verification_result = %s
               WHERE id = %s""",
            (_now(), verified_by, "pass" if result.get("verified") else "fail", record_id),
        )
        conn.commit()
        conn.close()

        result["entity_type"] = entity_type
        result["entity_id"] = entity_id
        return result

    def lookup_digest(self, entity_type: str, entity_id: str) -> Dict:
        """Look up the latest stored digest for an entity."""
        conn = self._get_db()
        row = conn.execute(
            """SELECT * FROM blueprint_digests
               WHERE entity_type = %s AND entity_id = %s
               ORDER BY computed_at DESC LIMIT 1""",
            (entity_type, entity_id),
        ).fetchone()
        conn.close()
        if not row:
            return {"found": False, "entity_type": entity_type, "entity_id": entity_id}
        return {"found": True, **dict(row)}

    def get_history(self, entity_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get digest history, optionally filtered by entity type."""
        conn = self._get_db()
        if entity_type:
            rows = conn.execute(
                """SELECT * FROM blueprint_digests
                   WHERE entity_type = %s ORDER BY computed_at DESC LIMIT %s""",
                (entity_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM blueprint_digests
                   ORDER BY computed_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(
        description="Blueprint Digest Verifier — NemoClaw-adapted supply chain integrity (D-NC-3)"
    )
    parser.add_argument("--compute", action="store_true", help="Compute directory digest")
    parser.add_argument("--verify", action="store_true", help="Verify against expected digest")
    parser.add_argument("--store", action="store_true", help="Compute and store digest")
    parser.add_argument("--verify-entity", action="store_true", help="Verify entity against stored digest")
    parser.add_argument("--lookup", action="store_true", help="Look up stored digest")
    parser.add_argument("--history", action="store_true", help="Show digest history")
    parser.add_argument("--path", type=Path, help="Directory path")
    parser.add_argument("--expected", type=str, help="Expected digest for verification")
    parser.add_argument("--entity-type", type=str, choices=list(ENTITY_TYPES), help="Entity type")
    parser.add_argument("--entity-id", type=str, default="", help="Entity identifier")
    parser.add_argument("--verified-by", type=str, default="", help="Verifier identity")
    parser.add_argument("--limit", type=int, default=50, help="History limit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    args = parser.parse_args()

    verifier = BlueprintVerifier(db_path=args.db_path)

    if args.compute:
        result = verifier.compute_digest(args.path)
    elif args.verify:
        result = verifier.verify_digest(args.path, args.expected)
    elif args.store:
        result = verifier.store_digest(args.entity_type, args.entity_id, args.path)
    elif args.verify_entity:
        result = verifier.verify_and_record(args.entity_type, args.entity_id, args.path, verified_by=args.verified_by)
    elif args.lookup:
        result = verifier.lookup_digest(args.entity_type, args.entity_id)
    elif args.history:
        result = verifier.get_history(entity_type=args.entity_type, limit=args.limit)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    main()
