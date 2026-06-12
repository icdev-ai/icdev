# [TEMPLATE: CUI // SP-CTI]
"""IC IE Multi-Agency Data Sharing via Data Fabric.

Deterministic tool for registering, sharing, and revoking access to data
assets across the IC Information Environment with classification-aware
policy enforcement and an append-only audit trail.

NIST 800-53 Controls: AC-3, AC-4, AU-2, AU-3, AU-12, SC-28, SI-12
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from icdev.tools.db.storage import get_connection

_FOREIGN_AGENCY_PREFIXES = ("FVEY_", "NATO_", "FOREIGN_")

_DEFAULT_DB_PATH = str(Path.cwd() / "data" / "ic_ie_fabric.db")


class DataFabricService:
    """Multi-agency data sharing service backed by SQLite or PostgreSQL."""

    def __init__(self, backend: str = "sqlite", db_path: str = None):
        self.backend = backend
        if db_path is None and backend == "sqlite":
            db_path = _DEFAULT_DB_PATH
        self._conn = get_connection(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS fabric_assets (
            id TEXT PRIMARY KEY,
            classification TEXT NOT NULL,
            owner_agency TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS fabric_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id TEXT NOT NULL,
            target_agency TEXT NOT NULL,
            status TEXT NOT NULL,
            justification TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS fabric_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            target_agency TEXT,
            justification TEXT,
            timestamp TEXT
        );
        """
        self._conn.executescript(ddl)
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_asset(self, row) -> dict | None:
        if row is None:
            return None
        metadata = row["metadata"]
        return {
            "id": row["id"],
            "classification": row["classification"],
            "owner_agency": row["owner_agency"],
            "metadata": json.loads(metadata) if metadata else {},
            "created_at": row["created_at"],
        }

    def _row_to_audit(self, row) -> dict:
        return {
            "id": row["id"],
            "event_type": row["event_type"],
            "asset_id": row["asset_id"],
            "target_agency": row["target_agency"],
            "justification": row["justification"],
            "timestamp": row["timestamp"],
        }

    def _is_foreign(self, agency: str) -> bool:
        return any(agency.startswith(p) for p in _FOREIGN_AGENCY_PREFIXES)

    def _is_noforn(self, classification: str) -> bool:
        return "//NOFORN" in classification or "NOFORN" in classification

    def _insert_audit(
        self,
        event_type: str,
        asset_id: str,
        target_agency: str | None = None,
        justification: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO fabric_audit (event_type, asset_id, target_agency, justification, timestamp)"
            " VALUES (?, ?, ?, ?, ?)",
            (event_type, asset_id, target_agency, justification, self._now()),
        )
        self._conn.commit()

    def register_asset(
        self,
        classification: str,
        owner_agency: str,
        metadata: dict,
        asset_id: str | None = None,
    ) -> dict:
        aid = asset_id if asset_id is not None else f"fabric-{uuid.uuid4().hex}"
        self._conn.execute(
            "INSERT INTO fabric_assets (id, classification, owner_agency, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (aid, classification, owner_agency, json.dumps(metadata), self._now()),
        )
        self._conn.commit()
        return self._row_to_asset(
            self._conn.execute("SELECT * FROM fabric_assets WHERE id = ?", (aid,)).fetchone()
        )

    def get_asset(self, asset_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM fabric_assets WHERE id = ?", (asset_id,)
        ).fetchone()
        return self._row_to_asset(row)

    def share_asset(
        self, asset_id: str, target_agency: str, justification: str
    ) -> dict:
        asset = self.get_asset(asset_id)
        if asset is None:
            result = {
                "status": "denied",
                "denial_reason": "Asset not found",
                "asset_id": asset_id,
                "target_agency": target_agency,
            }
            self._insert_audit("share_denied", asset_id, target_agency, justification)
            return result

        if self._is_noforn(asset["classification"]) and self._is_foreign(target_agency):
            result = {
                "status": "denied",
                "denial_reason": "Asset marked NOFORN — sharing with foreign partner denied",
                "asset_id": asset_id,
                "target_agency": target_agency,
            }
            self._insert_audit("share_denied", asset_id, target_agency, justification)
            return result

        self._conn.execute(
            "INSERT INTO fabric_shares (asset_id, target_agency, status, justification, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (asset_id, target_agency, "active", justification, self._now()),
        )
        self._conn.commit()
        self._insert_audit("share_created", asset_id, target_agency, justification)
        return {
            "status": "active",
            "asset_id": asset_id,
            "target_agency": target_agency,
        }

    def get_asset_for_agency(self, asset_id: str, agency: str) -> dict | None:
        asset = self.get_asset(asset_id)
        if asset is None:
            return None
        if asset["owner_agency"] == agency:
            return asset
        row = self._conn.execute(
            "SELECT * FROM fabric_shares WHERE asset_id = ? AND target_agency = ? AND status = ?",
            (asset_id, agency, "active"),
        ).fetchone()
        return asset if row is not None else None

    def query_shared_assets(self, agency: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT a.* FROM fabric_assets a"
            " JOIN fabric_shares s ON a.id = s.asset_id"
            " WHERE s.target_agency = ? AND s.status = ?"
            " UNION"
            " SELECT * FROM fabric_assets WHERE owner_agency = ?",
            (agency, "active", agency),
        ).fetchall()
        seen = set()
        results = []
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"])
                results.append(self._row_to_asset(row))
        return results

    def revoke_share(self, asset_id: str, target_agency: str) -> dict:
        cursor = self._conn.execute(
            "SELECT * FROM fabric_shares WHERE asset_id = ? AND target_agency = ? AND status = ?",
            (asset_id, target_agency, "active"),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("No active share found")

        self._conn.execute(
            "UPDATE fabric_shares SET status = ? WHERE id = ?",
            ("revoked", row["id"]),
        )
        self._conn.commit()
        self._insert_audit("share_revoked", asset_id, target_agency)
        return {"status": "revoked"}

    def get_audit_entries(self, event_type: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM fabric_audit WHERE event_type = ? ORDER BY timestamp DESC",
            (event_type,),
        ).fetchall()
        return [self._row_to_audit(r) for r in rows]
