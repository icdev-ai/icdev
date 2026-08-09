#!/usr/bin/env python3
# CUI // SP-CTI
"""DDC ← Collibra lineage import adapter.

Pulls table/column-level lineage from Collibra Data Intelligence Cloud
(REST API v2) into DDC's data classification graph as dd_lineage records.

Every imported lineage edge receives a CUI classification overlay by default.
The policy 'SECRET data may not flow into IL4-accessible datasets' is
enforced by generate_contract_assertions() after import.

External nodes ingested from Collibra are added to the DDC design graph
with IDs of the form ``ext:collibra:<assetId>``.

Usage
-----
    # dry-run — parse API, no writes
    python tools/data_canvas/sync/collibra_import.py \\
        --design-id <id> --dry-run --json

    # import into existing design
    python tools/data_canvas/sync/collibra_import.py \\
        --design-id <id> --json

    # auto-create design from Collibra community name
    python tools/data_canvas/sync/collibra_import.py \\
        --create-design "Collibra Import" --json

    # import from saved JSON export (air-gap / offline)
    python tools/data_canvas/sync/collibra_import.py \\
        --design-id <id> --file /path/export.json --json

    # run after import: gate if any CAT1 assertion fails
    python tools/data_canvas/sync/collibra_import.py \\
        --design-id <id> --gate --json

Configuration (env vars or args/collibra_config.yaml)
-----
    ICDEV_COLLIBRA_URL              Collibra base URL (https://tenant.collibra.com)
    ICDEV_COLLIBRA_USERNAME         Service account username (basic auth)
    ICDEV_COLLIBRA_PASSWORD         Service account password (basic auth)
    ICDEV_COLLIBRA_TOKEN            Bearer token (token auth takes precedence)
    ICDEV_COLLIBRA_TIMEOUT          HTTP timeout seconds (default: 15)
    ICDEV_COLLIBRA_COMMUNITY_ID     Community UUID to scope asset search (optional)
    ICDEV_COLLIBRA_CLASSIFICATION   Default CUI overlay classification (default: CUI)
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.ddc.collibra_import")

_CONFIG_PATH = _ROOT / "args" / "collibra_config.yaml"

# ── Collibra asset type names → DDC entity types ──────────────────────────────

_ASSET_TYPE_MAP: dict[str, str] = {
    "Table": "ent-table",
    "View": "ent-view",
    "Schema": "ent-table",          # structural — treat as table placeholder
    "Data Asset": "ent-table",
    "Report": "ent-view",
    "File": "ent-file",
    "Domain": "ent-table",
    "Data Store": "ent-table",
    "Data Domain": "ent-table",
    "Database": "ent-table",
    "Storage Bucket": "ent-datalake",
    "Topic": "ent-topic",
    "Stream": "ent-topic",
    "Collection": "ent-collection",
    "Data Warehouse": "ent-warehouse",
}

# ── Collibra relation name fragments → DDC lineage_type ───────────────────────

_RELATION_LINEAGE_MAP: list[tuple[str, str]] = [
    ("source to target", "col-passthrough"),
    ("data lineage", "col-passthrough"),
    ("targets", "col-passthrough"),
    ("derives", "col-derive"),
    ("joins", "col-join"),
    ("filters", "col-filter"),
    ("aggregates", "col-aggregate"),
    ("union", "col-union"),
    ("cast", "col-cast"),
    ("etl", "flow-column-lineage"),
    ("replication", "flow-column-lineage"),
    ("cdc", "flow-column-lineage"),
]

# ── Classification keyword → DDC classification level ────────────────────────

_CLASSIF_KEYWORD_MAP: list[tuple[str, str]] = [
    ("top secret", "TS/SCI"),
    ("ts/sci", "TS/SCI"),
    ("secret", "SECRET"),
    ("cui", "CUI"),
    ("fouo", "FOUO"),
    ("for official use only", "FOUO"),
    ("public", "PUBLIC"),
    ("unclassified", "PUBLIC"),
]


# ── Config loader ──────────────────────────────────────────────────────────────

def _load_config() -> dict:
    defaults: dict = {
        "url": "",
        "username": "",
        "password": "",
        "token": "",
        "timeout": 15,
        "community_id": "",
        "default_classification": "CUI",
    }
    if _CONFIG_PATH.exists():
        try:
            import yaml  # type: ignore[import]
            with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            cfg = data.get("collibra", {})
            for key in defaults:
                if cfg.get(key) is not None and cfg[key] != "":
                    defaults[key] = cfg[key]
        except Exception:
            pass
    env_map = {
        "ICDEV_COLLIBRA_URL": "url",
        "ICDEV_COLLIBRA_USERNAME": "username",
        "ICDEV_COLLIBRA_PASSWORD": "password",
        "ICDEV_COLLIBRA_TOKEN": "token",
        "ICDEV_COLLIBRA_TIMEOUT": "timeout",
        "ICDEV_COLLIBRA_COMMUNITY_ID": "community_id",
        "ICDEV_COLLIBRA_CLASSIFICATION": "default_classification",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            defaults[cfg_key] = int(val) if cfg_key == "timeout" else val
    return defaults


# ── REST client ────────────────────────────────────────────────────────────────

class CollibraError(Exception):
    """Raised on unrecoverable Collibra API errors."""


class CollibraClient:
    """Thin synchronous client for the Collibra REST API v2.

    Uses only stdlib urllib — no external dependencies.
    Supports both basic auth (username/password) and bearer token auth.
    """

    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        token: str = "",
        timeout: int = 15,
    ):
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout
        self._session_token: str = ""

    # ── Low-level HTTP ─────────────────────────────────────────────────────────

    def _build_auth_header(self) -> str:
        if self._session_token:
            return f"Bearer {self._session_token}"
        if self.token:
            return f"Bearer {self.token}"
        if self.username and self.password:
            creds = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            return f"Basic {creds}"
        return ""

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
        *,
        ignore_404: bool = False,
    ) -> dict | list | None:
        url = f"{self.base_url}/rest/2.0{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        auth = self._build_auth_header()
        if auth:
            headers["Authorization"] = auth
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — URL built from admin-configured base_url (https only in production); not user-controlled at runtime
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            if ignore_404 and exc.code == 404:
                return None
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise CollibraError(
                f"Collibra {method} {path} → HTTP {exc.code}: {body_text[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise CollibraError(
                f"Collibra connection error ({url}): {exc.reason}"
            ) from exc

    # ── Auth ───────────────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """Establish a session using username/password (sets session token)."""
        if not (self.username and self.password):
            return bool(self.token)
        try:
            result = self._request(
                "POST",
                "/auth/sessions",
                body={"username": self.username, "password": self.password},
            )
            if isinstance(result, dict) and result.get("token"):
                self._session_token = result["token"]
            return True
        except CollibraError:
            return False

    # ── Health ─────────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        try:
            self._request("GET", "/ping")
            return True
        except CollibraError:
            return False

    # ── Asset types ────────────────────────────────────────────────────────────

    def get_asset_types(self, name: str = "") -> list[dict]:
        """Return asset types, optionally filtered by name."""
        params: dict[str, Any] = {"limit": 100, "offset": 0}
        if name:
            params["name"] = name
        result = self._request("GET", "/assetTypes", params=params)
        if isinstance(result, dict):
            return result.get("results", [])
        return []

    def find_asset_type_id(self, name: str) -> str | None:
        """Look up the UUID of an asset type by its display name."""
        types = self.get_asset_types(name=name)
        for t in types:
            if t.get("name", "").lower() == name.lower():
                return t.get("id")
        return None

    # ── Assets ─────────────────────────────────────────────────────────────────

    def get_assets(
        self,
        type_ids: list[str] | None = None,
        community_id: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Return paginated assets. Result: {total, results: [...]}."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if type_ids:
            params["typePublicIds"] = ",".join(type_ids)
        if community_id:
            params["communityId"] = community_id
        result = self._request("GET", "/assets", params=params)
        if isinstance(result, dict):
            return result
        return {"total": 0, "results": []}

    def get_all_assets(
        self,
        type_ids: list[str] | None = None,
        community_id: str = "",
        page_size: int = 100,
    ) -> list[dict]:
        """Paginate through all assets matching the criteria."""
        assets: list[dict] = []
        offset = 0
        while True:
            page = self.get_assets(type_ids, community_id, page_size, offset)
            batch = page.get("results", [])
            assets.extend(batch)
            total = page.get("total", 0)
            offset += len(batch)
            if offset >= total or not batch:
                break
        return assets

    # ── Relations ──────────────────────────────────────────────────────────────

    def get_relations(
        self,
        source_id: str = "",
        target_id: str = "",
        relation_type_id: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """Return relations for a source or target asset."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if source_id:
            params["sourceId"] = source_id
        if target_id:
            params["targetId"] = target_id
        if relation_type_id:
            params["typePublicId"] = relation_type_id
        result = self._request("GET", "/relations", params=params)
        if isinstance(result, dict):
            return result
        return {"total": 0, "results": []}

    def get_all_relations_for_asset(
        self, asset_id: str, page_size: int = 100
    ) -> list[dict]:
        """Paginate through all outgoing + incoming relations for an asset."""
        relations: list[dict] = []
        for direction_param, direction_key in [("sourceId", "source"), ("targetId", "target")]:
            offset = 0
            while True:
                params: dict[str, Any] = {direction_param: asset_id, "limit": page_size, "offset": offset}
                result = self._request("GET", "/relations", params=params)
                batch = (result or {}).get("results", []) if isinstance(result, dict) else []
                relations.extend(batch)
                total = (result or {}).get("total", 0) if isinstance(result, dict) else 0
                offset += len(batch)
                if offset >= total or not batch:
                    break
        return relations

    # ── Attributes (for classification tags) ──────────────────────────────────

    def get_attributes(self, asset_id: str) -> list[dict]:
        """Return attributes for an asset (includes classification tags)."""
        result = self._request("GET", "/attributes", params={"assetId": asset_id, "limit": 100})
        if isinstance(result, dict):
            return result.get("results", [])
        return []


# ── Mapping helpers ────────────────────────────────────────────────────────────

def _asset_type_to_ddc(type_name: str) -> str:
    """Map a Collibra asset type name to a DDC entity type."""
    return _ASSET_TYPE_MAP.get(type_name, "ent-table")


def _relation_to_lineage_type(relation_type_name: str) -> str:
    """Infer DDC lineage_type from Collibra relation type name."""
    lower = (relation_type_name or "").lower()
    for fragment, lt in _RELATION_LINEAGE_MAP:
        if fragment in lower:
            return lt
    return "col-passthrough"


def _infer_classification(attributes: list[dict], default: str) -> str:
    """Extract classification level from Collibra attribute values."""
    for attr in attributes:
        val = str(attr.get("value", "") or "").lower()
        type_name = (attr.get("type", {}) or {}).get("name", "").lower()
        if "classification" in type_name or "sensitivity" in type_name:
            for keyword, level in _CLASSIF_KEYWORD_MAP:
                if keyword in val:
                    return level
    return default


def _ext_node_id(asset_id: str) -> str:
    """Canonical external node ID for a Collibra asset."""
    return f"ext:collibra:{asset_id}"


def _slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_\-. ]", "_", text)[:128]


# ── Main importer ──────────────────────────────────────────────────────────────

class DDCCollibraImporter:
    """Imports Collibra lineage into DDC as dd_lineage records.

    Can pull live from the Collibra REST API or parse a pre-exported JSON file.
    """

    def __init__(self, config: dict | None = None, dry_run: bool = False):
        self.cfg = config or _load_config()
        self.dry_run = dry_run
        self.client = CollibraClient(
            url=self.cfg.get("url", ""),
            username=self.cfg.get("username", ""),
            password=self.cfg.get("password", ""),
            token=self.cfg.get("token", ""),
            timeout=self.cfg.get("timeout", 15),
        )
        self._default_classification = self.cfg.get("default_classification", "CUI")

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _get_conn(self):
        from tools.data_canvas.db.init_db import get_connection
        return get_connection()

    def _fetch_design(self, design_id: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT id, name, graph_json, classification FROM data_designs WHERE id = ?",
                (design_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _create_design(self, name: str, classification: str = "CUI") -> str:
        design_id = str(uuid.uuid4())
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO data_designs (id, name, description, classification) VALUES (?, ?, ?, ?)",
                (design_id, name, "Lineage imported from Collibra", classification),
            )
            conn.commit()
        finally:
            conn.close()
        return design_id

    def _upsert_node_in_graph(
        self,
        design_id: str,
        node_id: str,
        label: str,
        node_type: str,
        classification: str,
    ) -> None:
        """Add an external node to the design's graph_json if not already present."""
        if self.dry_run:
            return
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT graph_json FROM data_designs WHERE id = ?", (design_id,)
            ).fetchone()
            if not row:
                return
            graph = json.loads(row["graph_json"] or '{"nodes":[],"edges":[],"boundaries":[]}')
            nodes: list[dict] = graph.get("nodes", [])
            if any(n.get("id") == node_id for n in nodes):
                return
            nodes.append({
                "id": node_id,
                "data": {
                    "type": node_type,
                    "label": label,
                    "description": "Imported from Collibra",
                    "classification": classification,
                    "source": "collibra",
                },
            })
            graph["nodes"] = nodes
            conn.execute(
                "UPDATE data_designs SET graph_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(graph), datetime.now(timezone.utc).isoformat(), design_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_lineage(
        self,
        design_id: str,
        source_node_id: str,
        target_node_id: str,
        column_name: str,
        lineage_type: str,
        classification: str,
        transform_desc: str = "",
    ) -> bool:
        """Insert a dd_lineage record. Returns True if inserted, False if skipped."""
        from tools.data_canvas.lineage import validate_lineage_edge

        edge_data = {
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
            "lineage_type": lineage_type,
            "column_name": column_name,
        }
        validation = validate_lineage_edge(edge_data)
        if not validation["valid"]:
            logger.warning(
                "Skipping invalid lineage edge %s→%s: %s",
                source_node_id,
                target_node_id,
                validation["errors"],
            )
            return False

        if self.dry_run:
            return True

        record_id = str(uuid.uuid4())
        conn = self._get_conn()
        try:
            # Skip if exact duplicate already exists
            existing = conn.execute(
                "SELECT id FROM dd_lineage WHERE design_id=? AND source_node_id=? "
                "AND target_node_id=? AND column_name=?",
                (design_id, source_node_id, target_node_id, column_name),
            ).fetchone()
            if existing:
                return False

            conn.execute(
                "INSERT INTO dd_lineage "
                "(id, design_id, source_node_id, target_node_id, lineage_type, "
                "column_name, transform_desc, classification) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    design_id,
                    source_node_id,
                    target_node_id,
                    lineage_type,
                    column_name,
                    transform_desc,
                    classification,
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _run_assertions(self, design_id: str) -> list[dict]:
        """Run data contract assertions on the imported lineage."""
        from tools.data_canvas.lineage import generate_contract_assertions

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM dd_lineage WHERE design_id = ?", (design_id,)
            ).fetchall()
            lineage_records = [dict(r) for r in rows]

            row = conn.execute(
                "SELECT graph_json FROM data_designs WHERE id = ?", (design_id,)
            ).fetchone()
            graph = json.loads((row["graph_json"] if row else None) or '{"nodes":[],"edges":[],"boundaries":[]}')
        finally:
            conn.close()

        return generate_contract_assertions(lineage_records, graph)

    # ── Live API import ────────────────────────────────────────────────────────

    def import_from_api(self, design_id: str) -> dict:
        """Pull lineage from Collibra REST API and persist to DDC.

        Returns a summary dict with counts and any contract assertion violations.
        """
        community_id = self.cfg.get("community_id", "")
        default_classif = self._default_classification

        # Authenticate (if using session auth)
        if self.client.username and self.client.password and not self.client.token:
            self.client.authenticate()

        # Step 1: Discover asset type IDs for Tables and Views
        type_id_cache: dict[str, str] = {}
        for type_name in ("Table", "View", "Column", "Data Asset"):
            tid = self.client.find_asset_type_id(type_name)
            if tid:
                type_id_cache[type_name] = tid
                logger.debug("Resolved Collibra type '%s' → %s", type_name, tid)

        table_type_ids = [
            v for k, v in type_id_cache.items() if k in ("Table", "View", "Data Asset")
        ]

        # Step 2: Fetch all table-like assets
        logger.info("Fetching Collibra table assets (community=%s)…", community_id or "all")
        table_assets = self.client.get_all_assets(
            type_ids=table_type_ids or None, community_id=community_id
        )
        logger.info("Found %d table assets", len(table_assets))

        # Step 3: Enumerate outgoing relations for each table asset
        imported_nodes = 0
        imported_edges = 0
        skipped_edges = 0
        errors: list[str] = []

        for asset in table_assets:
            asset_id = asset.get("id", "")
            asset_name = asset.get("name", asset_id)
            asset_type_name = (asset.get("type") or {}).get("name", "Table")
            ddc_type = _asset_type_to_ddc(asset_type_name)
            src_node_id = _ext_node_id(asset_id)

            # Infer classification from attributes (cached lazily)
            classif = default_classif

            # Upsert node in DDC graph
            self._upsert_node_in_graph(design_id, src_node_id, asset_name, ddc_type, classif)
            imported_nodes += 1

            # Get relations
            try:
                relations = self.client.get_all_relations_for_asset(asset_id)
            except CollibraError as exc:
                errors.append(f"Relations fetch for {asset_id}: {exc}")
                continue

            for rel in relations:
                rel_type_name = (rel.get("type") or {}).get("name", "")
                lineage_lt = _relation_to_lineage_type(rel_type_name)

                source = rel.get("source") or {}
                target = rel.get("target") or {}

                if not source or not target:
                    continue

                src_id = source.get("id", "")
                tgt_id = target.get("id", "")

                if not src_id or not tgt_id or src_id == tgt_id:
                    continue

                # For column-level relations: extract column name from asset name
                src_type_name = (source.get("type") or {}).get("name", "")
                tgt_type_name = (target.get("type") or {}).get("name", "")

                col_name = ""
                effective_src_id = src_id
                effective_tgt_id = tgt_id

                is_col_source = src_type_name == "Column"
                is_col_target = tgt_type_name == "Column"

                if is_col_source:
                    col_name = source.get("name", "")
                    # Try to find parent table ID — use the Column asset ID as node if not found
                    effective_src_id = src_id  # ext:collibra:<columnId> is the node
                elif is_col_target:
                    col_name = target.get("name", "")
                    effective_tgt_id = tgt_id

                # Upsert target node if it's a known table
                tgt_name = target.get("name", tgt_id)
                tgt_ddc_type = _asset_type_to_ddc(tgt_type_name or "Table")
                self._upsert_node_in_graph(
                    design_id,
                    _ext_node_id(effective_tgt_id),
                    tgt_name,
                    tgt_ddc_type,
                    classif,
                )

                inserted = self._insert_lineage(
                    design_id=design_id,
                    source_node_id=_ext_node_id(effective_src_id),
                    target_node_id=_ext_node_id(effective_tgt_id),
                    column_name=col_name,
                    lineage_type=lineage_lt,
                    classification=classif,
                    transform_desc=rel_type_name,
                )
                if inserted:
                    imported_edges += 1
                else:
                    skipped_edges += 1

        # Step 4: Run contract assertions
        assertions = self._run_assertions(design_id) if not self.dry_run else []
        cat1_violations = [a for a in assertions if not a["passed"] and a.get("severity") == "CAT1"]

        return {
            "status": "ok" if not cat1_violations and not errors else "violations" if cat1_violations else "partial",
            "design_id": design_id,
            "source": "collibra_api",
            "collibra_url": self.cfg.get("url", ""),
            "dry_run": self.dry_run,
            "nodes_imported": imported_nodes,
            "lineage_edges_imported": imported_edges,
            "lineage_edges_skipped": skipped_edges,
            "errors": errors,
            "assertions_run": len(assertions),
            "cat1_violations": len(cat1_violations),
            "violation_details": [
                {"id": a["assertion_id"], "type": a["type"], "desc": a["description"]}
                for a in cat1_violations
            ],
        }

    # ── File import ────────────────────────────────────────────────────────────

    def import_from_file(self, design_id: str, file_path: str) -> dict:
        """Import lineage from a Collibra JSON export file.

        Expected format:
        {
            "assets": [
                {
                    "id": "<uuid>",
                    "name": "<name>",
                    "type": {"name": "Table"},
                    "classification": "CUI"   // optional
                }
            ],
            "relations": [
                {
                    "type": {"name": "Data Lineage"},
                    "source": {"id": "<uuid>", "name": "<name>", "type": {"name": "Table"}},
                    "target": {"id": "<uuid>", "name": "<name>", "type": {"name": "Table"}}
                }
            ]
        }
        """
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "error", "message": f"Cannot read file: {exc}"}

        assets: list[dict] = payload.get("assets", [])
        relations: list[dict] = payload.get("relations", [])
        default_classif = self._default_classification

        asset_map: dict[str, dict] = {a.get("id", ""): a for a in assets if a.get("id")}
        imported_nodes = 0
        imported_edges = 0
        skipped_edges = 0
        errors: list[str] = []

        # Upsert all asset nodes
        for asset in assets:
            asset_id = asset.get("id", "")
            if not asset_id:
                continue
            name = asset.get("name", asset_id)
            type_name = (asset.get("type") or {}).get("name", "Table")
            ddc_type = _asset_type_to_ddc(type_name)

            # Classification from asset field or default
            raw_classif = str(asset.get("classification") or "").lower()
            classif = default_classif
            for keyword, level in _CLASSIF_KEYWORD_MAP:
                if keyword in raw_classif:
                    classif = level
                    break

            self._upsert_node_in_graph(design_id, _ext_node_id(asset_id), name, ddc_type, classif)
            imported_nodes += 1

        # Process relations → lineage records
        for rel in relations:
            rel_type_name = (rel.get("type") or {}).get("name", "")
            lineage_lt = _relation_to_lineage_type(rel_type_name)

            source = rel.get("source") or {}
            target = rel.get("target") or {}
            src_id = source.get("id", "")
            tgt_id = target.get("id", "")

            if not src_id or not tgt_id or src_id == tgt_id:
                skipped_edges += 1
                continue

            col_name = rel.get("column_name", "")
            if not col_name:
                src_type = (source.get("type") or {}).get("name", "")
                tgt_type = (target.get("type") or {}).get("name", "")
                if src_type == "Column":
                    col_name = source.get("name", "")
                elif tgt_type == "Column":
                    col_name = target.get("name", "")

            # Use asset classification if known, else default
            src_asset = asset_map.get(src_id, {})
            raw_classif = str(src_asset.get("classification") or "").lower()
            classif = default_classif
            for keyword, level in _CLASSIF_KEYWORD_MAP:
                if keyword in raw_classif:
                    classif = level
                    break

            # Upsert target node if not already in asset list
            if tgt_id not in asset_map:
                tgt_name = target.get("name", tgt_id)
                tgt_type_name = (target.get("type") or {}).get("name", "Table")
                self._upsert_node_in_graph(
                    design_id,
                    _ext_node_id(tgt_id),
                    tgt_name,
                    _asset_type_to_ddc(tgt_type_name),
                    classif,
                )

            inserted = self._insert_lineage(
                design_id=design_id,
                source_node_id=_ext_node_id(src_id),
                target_node_id=_ext_node_id(tgt_id),
                column_name=col_name,
                lineage_type=lineage_lt,
                classification=classif,
                transform_desc=rel_type_name,
            )
            if inserted:
                imported_edges += 1
            else:
                skipped_edges += 1

        assertions = self._run_assertions(design_id) if not self.dry_run else []
        cat1_violations = [a for a in assertions if not a["passed"] and a.get("severity") == "CAT1"]

        return {
            "status": "ok" if not cat1_violations and not errors else "violations" if cat1_violations else "partial",
            "design_id": design_id,
            "source": "collibra_file",
            "file": file_path,
            "dry_run": self.dry_run,
            "nodes_imported": imported_nodes,
            "lineage_edges_imported": imported_edges,
            "lineage_edges_skipped": skipped_edges,
            "errors": errors,
            "assertions_run": len(assertions),
            "cat1_violations": len(cat1_violations),
            "violation_details": [
                {"id": a["assertion_id"], "type": a["type"], "desc": a["description"]}
                for a in cat1_violations
            ],
        }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Import Collibra lineage into DDC with CUI classification overlay"
    )
    target_grp = parser.add_mutually_exclusive_group(required=True)
    target_grp.add_argument("--design-id", help="Import into existing DDC design by ID")
    target_grp.add_argument(
        "--create-design", metavar="NAME", help="Auto-create a new design with this name"
    )

    source_grp = parser.add_mutually_exclusive_group()
    source_grp.add_argument("--file", metavar="PATH", help="Import from Collibra JSON export file")
    # (default: live API)

    parser.add_argument(
        "--classification",
        default="",
        help="Override default classification for imported edges (e.g. CUI, SECRET)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DDC DB")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if any CAT1 violations found")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        get_logger("icdev.ddc.collibra_import").setLevel(logging.DEBUG)

    cfg = _load_config()
    if args.classification:
        cfg["default_classification"] = args.classification

    importer = DDCCollibraImporter(config=cfg, dry_run=args.dry_run)

    # Resolve design ID
    design_id = args.design_id
    if args.create_design:
        if not args.dry_run:
            design_id = importer._create_design(args.create_design)
        else:
            design_id = f"dry-run-{uuid.uuid4().hex[:8]}"

    if not design_id:
        print("ERROR: --design-id or --create-design required", file=sys.stderr)
        sys.exit(1)

    # Import
    if args.file:
        result = importer.import_from_file(design_id, args.file)
    else:
        if not cfg.get("url"):
            print("ERROR: ICDEV_COLLIBRA_URL not configured", file=sys.stderr)
            sys.exit(1)
        if not args.dry_run:
            ok = importer.client.ping()
            if not ok:
                result = {
                    "status": "error",
                    "message": f"Collibra not reachable at {cfg['url']}",
                }
                if args.json_out:
                    print(json.dumps(result, indent=2))
                else:
                    print(f"ERROR: {result['message']}")
                sys.exit(1)
        result = importer.import_from_api(design_id)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status", "?")
        print(
            f"[{status.upper()}] design={result.get('design_id', '?')} | "
            f"nodes={result.get('nodes_imported', 0)} | "
            f"edges={result.get('lineage_edges_imported', 0)} | "
            f"skipped={result.get('lineage_edges_skipped', 0)} | "
            f"cat1_violations={result.get('cat1_violations', 0)} | "
            f"errors={len(result.get('errors', []))}"
        )

    if args.gate and result.get("cat1_violations", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    _cli()
