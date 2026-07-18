#!/usr/bin/env python3
# CUI // SP-CTI
"""DDC → OpenMetadata one-way sync adapter.

Pushes DDC entities, lineage edges, and classification tags to a running
OpenMetadata instance via the OpenMetadata REST API v1.

Supported OpenMetadata versions: 1.2.x – 1.6.x
API reference: https://docs.open-metadata.org/developers/apis

Entity mapping
--------------
DDC node type                   OpenMetadata entity  Service type
ent-table / ent-view            table                CustomSQL
ent-collection                  table                CustomNoSQL
ent-topic                       topic                CustomMessaging
ent-datalake / ent-file         container            CustomStorage
ent-warehouse                   table                CustomDWH
ent-cache / ent-queue           table                CustomCache
ent-graph                       table                CustomGraph
ent-timeseries / ent-vector     table                CustomTimeSeries
flow-*                          pipeline             CustomPipeline
col-pii / col-phi / col-cui /
col-secret / col-encrypted      column tag           (DDC classification tag)

Lineage edges in dd_lineage → PUT /api/v1/lineage/table/{id}

Usage
-----
    # dry-run (no writes to OpenMetadata)
    python tools/data_canvas/sync/openmetadata_sync.py --design-id <id> --dry-run --json

    # push single design
    python tools/data_canvas/sync/openmetadata_sync.py --design-id <id> --json

    # push all designs
    python tools/data_canvas/sync/openmetadata_sync.py --all --json

    # gate (exit 1 if any push fails)
    python tools/data_canvas/sync/openmetadata_sync.py --all --gate --json

Configuration (env vars or args/openmetadata_config.yaml)
-----
    ICDEV_OM_URL       OpenMetadata base URL   (default: http://localhost:8585)
    ICDEV_OM_TOKEN     JWT bearer token        (required for non-public instances)
    ICDEV_OM_TIMEOUT   HTTP timeout seconds    (default: 15)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Ensure ICDev root is on sys.path when run directly
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Logging ───────────────────────────────────────────────────────────────────
logger = get_logger("icdev.ddc.openmetadata")

# ── Paths ─────────────────────────────────────────────────────────────────────
_CONFIG_PATH = _ROOT / "args" / "openmetadata_config.yaml"

# ── DDC node type → (OM entity kind, OM service type) ─────────────────────────
_ENTITY_MAP: dict[str, tuple[str, str]] = {
    "ent-table": ("table", "CustomSQL"),
    "ent-view": ("table", "CustomSQL"),
    "ent-collection": ("table", "CustomNoSQL"),
    "ent-topic": ("topic", "CustomMessaging"),
    "ent-datalake": ("container", "CustomStorage"),
    "ent-file": ("container", "CustomStorage"),
    "ent-warehouse": ("table", "CustomDWH"),
    "ent-cache": ("table", "CustomCache"),
    "ent-queue": ("table", "CustomCache"),
    "ent-graph": ("table", "CustomGraph"),
    "ent-timeseries": ("table", "CustomTimeSeries"),
    "ent-vector": ("table", "CustomTimeSeries"),
    # flow-* → pipeline
    "flow-etl": ("pipeline", "CustomPipeline"),
    "flow-api": ("pipeline", "CustomPipeline"),
    "flow-replication": ("pipeline", "CustomPipeline"),
    "flow-cdc": ("pipeline", "CustomPipeline"),
    "flow-backup": ("pipeline", "CustomPipeline"),
    "flow-export": ("pipeline", "CustomPipeline"),
    "flow-cross-domain": ("pipeline", "CustomPipeline"),
    "flow-column-lineage": ("pipeline", "CustomPipeline"),
}

# DDC column type → OpenMetadata tag label
_COL_TAGS: dict[str, str] = {
    "col-pii": "DDC.PII",
    "col-phi": "DDC.PHI",
    "col-cui": "DDC.CUI",
    "col-secret": "DDC.SECRET",
    "col-encrypted": "DDC.Encrypted",
}

# DDC classification → OM tag
_CLASSIF_TAGS: dict[str, str] = {
    "PUBLIC": "DDC.Public",
    "FOUO": "DDC.FOUO",
    "CUI": "DDC.CUI",
    "SECRET": "DDC.Secret",
    "TS/SCI": "DDC.TSSCI",
}

# ── Service slug (slugified, used as OM service name) ────────────────────────
_DDC_SERVICE_PREFIX = "ddc_"


# ── Config loader ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    # Start with hardcoded defaults, then layer YAML on top, then env vars
    # (env vars always win — highest priority).
    defaults: dict = {
        "url": "http://localhost:8585",
        "token": "",
        "timeout": 15,
    }
    if _CONFIG_PATH.exists():
        try:
            import yaml  # type: ignore[import]
            with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            cfg = data.get("openmetadata", {})
            if cfg.get("url"):
                defaults["url"] = cfg["url"]
            if cfg.get("token") is not None:
                defaults["token"] = cfg["token"]
            if cfg.get("timeout"):
                defaults["timeout"] = int(cfg["timeout"])
        except Exception:
            pass
    # Env vars override YAML and hardcoded defaults
    if os.environ.get("ICDEV_OM_URL"):
        defaults["url"] = os.environ["ICDEV_OM_URL"]
    if os.environ.get("ICDEV_OM_TOKEN") is not None:
        defaults["token"] = os.environ.get("ICDEV_OM_TOKEN", defaults["token"])
    if os.environ.get("ICDEV_OM_TIMEOUT"):
        defaults["timeout"] = int(os.environ["ICDEV_OM_TIMEOUT"])
    return defaults


# ── REST client ───────────────────────────────────────────────────────────────

class OpenMetadataError(Exception):
    """Raised on unrecoverable OpenMetadata API errors."""


class OpenMetadataClient:
    """Thin synchronous client for the OpenMetadata REST API v1.

    Uses only stdlib urllib — no external dependencies.
    """

    def __init__(self, url: str, token: str = "", timeout: int = 15):
        self.base_url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ── Low-level HTTP ────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        ignore_404: bool = False,
    ) -> dict | None:
        url = f"{self.base_url}/api/v1{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if raw:
                    return json.loads(raw.decode("utf-8"))
                return None
        except urllib.error.HTTPError as exc:
            if ignore_404 and exc.code == 404:
                return None
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise OpenMetadataError(
                f"OM {method} {path} → HTTP {exc.code}: {body_text[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenMetadataError(
                f"OM connection error ({url}): {exc.reason}"
            ) from exc

    # ── Health ────────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Return True if OpenMetadata API is reachable."""
        try:
            self._request("GET", "/system/version")
            return True
        except OpenMetadataError:
            return False

    # ── Tag category + tags ───────────────────────────────────────────────────

    def ensure_tag_category(self, category: str, description: str = "") -> None:
        """Create tag category if it does not exist."""
        existing = self._request("GET", f"/classifications/name/{category}", ignore_404=True)
        if existing:
            return
        payload = {
            "name": category,
            "description": description or f"DDC {category} classification tags",
            "provider": "user",
            "mutuallyExclusive": False,
        }
        try:
            self._request("POST", "/classifications", body=payload)
        except OpenMetadataError as exc:
            # 409 conflict = already exists — safe to ignore
            if "409" not in str(exc):
                raise

    def ensure_tag(self, fqn: str, description: str = "") -> None:
        """Create tag (category.tagName) if it does not exist.

        fqn examples: "DDC.PII", "DDC.CUI"
        """
        category, _, tag_name = fqn.partition(".")
        if not tag_name:
            return
        self.ensure_tag_category(category, "DDC Data Classification Tags")
        existing = self._request("GET", f"/tags/name/{fqn}", ignore_404=True)
        if existing:
            return
        payload = {
            "name": tag_name,
            "description": description or fqn,
            "classification": {"name": category},
        }
        try:
            self._request("POST", "/tags", body=payload)
        except OpenMetadataError as exc:
            if "409" not in str(exc):
                raise

    # ── Database service (parent for tables) ─────────────────────────────────

    def ensure_database_service(self, service_name: str, service_type: str) -> str:
        """Upsert a DatabaseService entity. Returns its id."""
        existing = self._request(
            "GET", f"/services/databaseServices/name/{service_name}", ignore_404=True
        )
        if existing:
            return existing.get("id", "")

        # Map CustomSQL / CustomDWH / etc. to OM serviceType
        payload = {
            "name": service_name,
            "serviceType": service_type,
            "connection": {
                "config": {
                    "type": service_type,
                    "hostPort": "ddc://localhost",
                }
            },
        }
        result = self._request("POST", "/services/databaseServices", body=payload)
        return (result or {}).get("id", "")

    def ensure_messaging_service(self, service_name: str) -> str:
        """Upsert a MessagingService entity. Returns its id."""
        existing = self._request(
            "GET", f"/services/messagingServices/name/{service_name}", ignore_404=True
        )
        if existing:
            return existing.get("id", "")
        payload = {
            "name": service_name,
            "serviceType": "CustomMessaging",
            "connection": {"config": {"type": "CustomMessaging", "sourcePythonClass": ""}},
        }
        result = self._request("POST", "/services/messagingServices", body=payload)
        return (result or {}).get("id", "")

    def ensure_storage_service(self, service_name: str) -> str:
        """Upsert a StorageService entity. Returns its id."""
        existing = self._request(
            "GET", f"/services/storageServices/name/{service_name}", ignore_404=True
        )
        if existing:
            return existing.get("id", "")
        payload = {
            "name": service_name,
            "serviceType": "CustomStorage",
            "connection": {"config": {"type": "CustomStorage", "sourcePythonClass": ""}},
        }
        result = self._request("POST", "/services/storageServices", body=payload)
        return (result or {}).get("id", "")

    def ensure_pipeline_service(self, service_name: str) -> str:
        """Upsert a PipelineService entity. Returns its id."""
        existing = self._request(
            "GET", f"/services/pipelineServices/name/{service_name}", ignore_404=True
        )
        if existing:
            return existing.get("id", "")
        payload = {
            "name": service_name,
            "serviceType": "CustomPipeline",
            "connection": {"config": {"type": "CustomPipeline", "sourcePythonClass": ""}},
        }
        result = self._request("POST", "/services/pipelineServices", body=payload)
        return (result or {}).get("id", "")

    # ── Database (groups tables within a service) ─────────────────────────────

    def ensure_database(self, service_name: str, db_name: str) -> str:
        """Upsert a Database entity. Returns its FQN."""
        fqn = f"{service_name}.{db_name}"
        existing = self._request("GET", f"/databases/name/{urllib.parse.quote(fqn)}", ignore_404=True)
        if existing:
            return existing.get("id", "")
        payload = {
            "name": db_name,
            "service": {"type": "databaseService", "fullyQualifiedName": service_name},
        }
        result = self._request("POST", "/databases", body=payload)
        return (result or {}).get("id", "")

    def ensure_schema(self, service_name: str, db_name: str, schema_name: str) -> str:
        """Upsert a DatabaseSchema entity. Returns its id."""
        fqn = f"{service_name}.{db_name}.{schema_name}"
        encoded = urllib.parse.quote(fqn, safe="")
        existing = self._request("GET", f"/databaseSchemas/name/{encoded}", ignore_404=True)
        if existing:
            return existing.get("id", "")
        payload = {
            "name": schema_name,
            "database": {"type": "database", "fullyQualifiedName": f"{service_name}.{db_name}"},
        }
        result = self._request("POST", "/databaseSchemas", body=payload)
        return (result or {}).get("id", "")

    # ── Table ─────────────────────────────────────────────────────────────────

    def upsert_table(
        self,
        service_name: str,
        db_name: str,
        schema_name: str,
        table_name: str,
        description: str,
        columns: list[dict],
        tags: list[str],
    ) -> str:
        """Create or update a Table entity. Returns entity id."""
        schema_fqn = f"{service_name}.{db_name}.{schema_name}"
        payload: dict[str, Any] = {
            "name": table_name,
            "description": description,
            "databaseSchema": {"type": "databaseSchema", "fullyQualifiedName": schema_fqn},
            "tableType": "Regular",
        }
        if columns:
            payload["columns"] = columns
        if tags:
            payload["tags"] = [{"tagFQN": t, "source": "Classification", "labelType": "Automated"} for t in tags]

        fqn = f"{schema_fqn}.{table_name}"
        encoded = urllib.parse.quote(fqn, safe="")
        existing = self._request("GET", f"/tables/name/{encoded}", ignore_404=True)
        if existing:
            patch_payload = {
                "description": description,
                "tags": payload.get("tags", []),
            }
            if columns:
                patch_payload["columns"] = columns
            result = self._request(
                "PATCH",
                f"/tables/{existing['id']}",
                body=patch_payload,
            )
            return (existing or {}).get("id", "")
        result = self._request("POST", "/tables", body=payload)
        return (result or {}).get("id", "")

    # ── Topic ─────────────────────────────────────────────────────────────────

    def upsert_topic(
        self,
        service_name: str,
        topic_name: str,
        description: str,
        tags: list[str],
    ) -> str:
        """Create or update a Topic entity. Returns entity id."""
        payload: dict[str, Any] = {
            "name": topic_name,
            "description": description,
            "service": {"type": "messagingService", "fullyQualifiedName": service_name},
            "messageSchema": {"schemaType": "None", "schemaText": ""},
            "partitions": 1,
            "cleanupPolicies": ["delete"],
            "replicationFactor": 1,
        }
        if tags:
            payload["tags"] = [{"tagFQN": t, "source": "Classification", "labelType": "Automated"} for t in tags]

        fqn = f"{service_name}.{topic_name}"
        encoded = urllib.parse.quote(fqn, safe="")
        existing = self._request("GET", f"/topics/name/{encoded}", ignore_404=True)
        if existing:
            return existing.get("id", "")
        result = self._request("POST", "/topics", body=payload)
        return (result or {}).get("id", "")

    # ── Container ─────────────────────────────────────────────────────────────

    def upsert_container(
        self,
        service_name: str,
        container_name: str,
        description: str,
        tags: list[str],
    ) -> str:
        """Create or update a Container entity. Returns entity id."""
        payload: dict[str, Any] = {
            "name": container_name,
            "description": description,
            "service": {"type": "storageService", "fullyQualifiedName": service_name},
            "numberOfObjects": 0,
            "size": 0,
        }
        if tags:
            payload["tags"] = [{"tagFQN": t, "source": "Classification", "labelType": "Automated"} for t in tags]

        fqn = f"{service_name}.{container_name}"
        encoded = urllib.parse.quote(fqn, safe="")
        existing = self._request("GET", f"/containers/name/{encoded}", ignore_404=True)
        if existing:
            return existing.get("id", "")
        result = self._request("POST", "/containers", body=payload)
        return (result or {}).get("id", "")

    # ── Pipeline ──────────────────────────────────────────────────────────────

    def upsert_pipeline(
        self,
        service_name: str,
        pipeline_name: str,
        description: str,
        tags: list[str],
    ) -> str:
        """Create or update a Pipeline entity. Returns entity id."""
        payload: dict[str, Any] = {
            "name": pipeline_name,
            "description": description,
            "service": {"type": "pipelineService", "fullyQualifiedName": service_name},
        }
        if tags:
            payload["tags"] = [{"tagFQN": t, "source": "Classification", "labelType": "Automated"} for t in tags]

        fqn = f"{service_name}.{pipeline_name}"
        encoded = urllib.parse.quote(fqn, safe="")
        existing = self._request("GET", f"/pipelines/name/{encoded}", ignore_404=True)
        if existing:
            return existing.get("id", "")
        result = self._request("POST", "/pipelines", body=payload)
        return (result or {}).get("id", "")

    # ── Lineage ───────────────────────────────────────────────────────────────

    def add_lineage(
        self,
        from_entity: str,
        from_id: str,
        to_entity: str,
        to_id: str,
    ) -> None:
        """Add a lineage edge between two entities."""
        payload = {
            "edge": {
                "fromEntity": {"id": from_id, "type": from_entity},
                "toEntity": {"id": to_id, "type": to_entity},
            }
        }
        self._request("PUT", "/lineage", body=payload)


# ── DDC graph helpers ─────────────────────────────────────────────────────────

def _parse_graph(graph_json: str | dict) -> dict:
    if isinstance(graph_json, str):
        return json.loads(graph_json)
    return graph_json


def _node_label(node: dict) -> str:
    return (
        node.get("data", {}).get("label")
        or node.get("label")
        or node.get("id", "unknown")
    )


def _node_desc(node: dict) -> str:
    return (
        node.get("data", {}).get("description")
        or node.get("description")
        or ""
    )


def _node_type(node: dict) -> str:
    return node.get("data", {}).get("type") or node.get("type", "")


def _node_classification(node: dict) -> str:
    return (
        node.get("data", {}).get("classification")
        or node.get("classification")
        or ""
    )


def _slugify(text: str) -> str:
    """Return a safe identifier string for use as OM names."""
    import re
    return re.sub(r"[^a-zA-Z0-9_\-.]", "_", text)[:128]


def _build_om_columns(children: list[dict]) -> list[dict]:
    """Convert DDC column child nodes to OpenMetadata column spec."""
    columns = []
    for child in children:
        ctype = _node_type(child)
        if not ctype.startswith("col-"):
            continue
        label = _node_label(child)
        desc = _node_desc(child)
        col: dict[str, Any] = {
            "name": label,
            "description": desc,
            "dataType": "VARCHAR",
            "dataLength": 255,
            "constraint": "NOT NULL" if ctype == "col-pk" else "NULL",
        }
        if ctype == "col-pk":
            col["constraint"] = "PRIMARY_KEY"
            col["dataType"] = "BIGINT"
        elif ctype == "col-fk":
            col["constraint"] = "FOREIGN_KEY"
        if ctype in _COL_TAGS:
            col["tags"] = [
                {
                    "tagFQN": _COL_TAGS[ctype],
                    "source": "Classification",
                    "labelType": "Automated",
                }
            ]
        columns.append(col)
    return columns


def _classification_om_tag(classification: str) -> str | None:
    for key, tag in _CLASSIF_TAGS.items():
        if key in (classification or "").upper():
            return tag
    return None


# ── Main sync logic ───────────────────────────────────────────────────────────

class DDCOpenMetadataSync:
    """Orchestrates DDC → OpenMetadata push for one or more designs."""

    def __init__(self, config: dict | None = None, dry_run: bool = False):
        self.cfg = config or _load_config()
        self.dry_run = dry_run
        self.client = OpenMetadataClient(
            url=self.cfg["url"],
            token=self.cfg.get("token", ""),
            timeout=self.cfg.get("timeout", 15),
        )
        # Cache: node_id → (om_entity_kind, om_entity_id)
        self._node_registry: dict[str, tuple[str, str]] = {}

    # ── DB access ─────────────────────────────────────────────────────────────

    def _get_conn(self):
        from tools.data_canvas.db.init_db import get_connection
        return get_connection()

    def _fetch_design(self, design_id: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT id, name, description, graph_json, classification "
                "FROM data_designs WHERE id = ?",
                (design_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _fetch_all_designs(self) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT id, name, description, graph_json, classification "
                "FROM data_designs ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fetch_lineage(self, design_id: str) -> list[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT source_node_id, target_node_id, lineage_type "
                "FROM dd_lineage WHERE design_id = ?",
                (design_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Tag bootstrap ─────────────────────────────────────────────────────────

    def _ensure_all_tags(self) -> None:
        """Pre-create all DDC tags in OpenMetadata."""
        tag_defs = {
            "DDC.PII": "Personally Identifiable Information (NIST SP 800-53 PL-4)",
            "DDC.PHI": "Protected Health Information (HIPAA §164.312)",
            "DDC.CUI": "Controlled Unclassified Information (32 CFR Part 2002)",
            "DDC.SECRET": "NSA Type 1 / SECRET classification",
            "DDC.Encrypted": "Field is encrypted at rest",
            "DDC.Public": "Public data — no CUI markings",
            "DDC.FOUO": "For Official Use Only",
            "DDC.TSSCI": "TOP SECRET // SCI",
        }
        for fqn, desc in tag_defs.items():
            self.client.ensure_tag(fqn, desc)

    # ── Service registry (cached per sync run) ────────────────────────────────

    _svc_cache: dict[str, str] = {}

    def _ensure_service(self, service_type: str, om_kind: str, svc_slug: str) -> str:
        key = f"{om_kind}:{svc_slug}"
        if key in self._svc_cache:
            return self._svc_cache[key]
        if om_kind == "table":
            svc_id = self.client.ensure_database_service(svc_slug, service_type)
        elif om_kind == "topic":
            svc_id = self.client.ensure_messaging_service(svc_slug)
        elif om_kind == "container":
            svc_id = self.client.ensure_storage_service(svc_slug)
        elif om_kind == "pipeline":
            svc_id = self.client.ensure_pipeline_service(svc_slug)
        else:
            svc_id = ""
        self._svc_cache[key] = svc_id
        return svc_id

    # ── Push helpers ──────────────────────────────────────────────────────────

    def _push_design(self, design: dict) -> dict:
        """Push all nodes + lineage for one design."""
        design_id = design["id"]
        design_name = design["name"]
        design_slug = _slugify(design_name)
        graph = _parse_graph(design.get("graph_json", "{}"))
        nodes: list[dict] = graph.get("nodes", [])
        edges: list[dict] = graph.get("edges", [])
        lineage_rows = self._fetch_lineage(design_id)

        node_map: dict[str, dict] = {n["id"]: n for n in nodes}
        children_by_parent: dict[str, list[dict]] = {}
        for edge in edges:
            if (
                edge.get("type") == "parent-child"
                or edge.get("data", {}).get("relation") == "contains"
            ):
                src = edge.get("source", "")
                tgt = edge.get("target", "")
                children_by_parent.setdefault(src, []).append(node_map.get(tgt, {}))

        # Registry: node_id → (om_entity_kind, om_entity_id)
        node_registry: dict[str, tuple[str, str]] = {}

        pushed = 0
        errors: list[str] = []

        # ── Pre-create DB hierarchy for table-kind entities ───────────────────
        # (service → database → schema), one hierarchy per design
        db_name = design_slug
        schema_name = "ddc"

        # ── Iterate nodes ─────────────────────────────────────────────────────
        for node in nodes:
            ntype = _node_type(node)
            nid = node.get("id", "")
            label = _node_label(node)
            desc = _node_desc(node)
            classif = _node_classification(node) or design.get("classification", "")

            if ntype not in _ENTITY_MAP:
                continue

            om_kind, om_svc_type = _ENTITY_MAP[ntype]
            tag_list: list[str] = []
            ct = _classification_om_tag(classif)
            if ct:
                tag_list.append(ct)

            node_slug = _slugify(label)
            entity_id = ""

            try:
                if om_kind == "table":
                    svc_slug = f"{_DDC_SERVICE_PREFIX}{design_slug}_{om_svc_type.lower()}"
                    if not self.dry_run:
                        self._ensure_service(om_svc_type, "table", svc_slug)
                        self.client.ensure_database(svc_slug, db_name)
                        self.client.ensure_schema(svc_slug, db_name, schema_name)
                    children = children_by_parent.get(nid, [])
                    om_cols = _build_om_columns(children)
                    # Collect col-level tags
                    for child in children:
                        ct2 = _node_type(child)
                        if ct2 in _COL_TAGS:
                            tag_list.append(_COL_TAGS[ct2])
                    tag_list = list(set(tag_list))
                    logger.debug("Upserting table %s.%s.%s.%s", svc_slug, db_name, schema_name, node_slug)
                    if not self.dry_run:
                        entity_id = self.client.upsert_table(
                            service_name=svc_slug,
                            db_name=db_name,
                            schema_name=schema_name,
                            table_name=node_slug,
                            description=desc,
                            columns=om_cols,
                            tags=tag_list,
                        )
                    pushed += 1
                    node_registry[nid] = ("table", entity_id)

                elif om_kind == "topic":
                    svc_slug = f"{_DDC_SERVICE_PREFIX}{design_slug}_messaging"
                    if not self.dry_run:
                        self._ensure_service("CustomMessaging", "topic", svc_slug)
                    logger.debug("Upserting topic %s.%s", svc_slug, node_slug)
                    if not self.dry_run:
                        entity_id = self.client.upsert_topic(
                            service_name=svc_slug,
                            topic_name=node_slug,
                            description=desc,
                            tags=tag_list,
                        )
                    pushed += 1
                    node_registry[nid] = ("topic", entity_id)

                elif om_kind == "container":
                    svc_slug = f"{_DDC_SERVICE_PREFIX}{design_slug}_storage"
                    if not self.dry_run:
                        self._ensure_service("CustomStorage", "container", svc_slug)
                    logger.debug("Upserting container %s.%s", svc_slug, node_slug)
                    if not self.dry_run:
                        entity_id = self.client.upsert_container(
                            service_name=svc_slug,
                            container_name=node_slug,
                            description=desc,
                            tags=tag_list,
                        )
                    pushed += 1
                    node_registry[nid] = ("container", entity_id)

                elif om_kind == "pipeline":
                    svc_slug = f"{_DDC_SERVICE_PREFIX}{design_slug}_pipeline"
                    if not self.dry_run:
                        self._ensure_service("CustomPipeline", "pipeline", svc_slug)
                    logger.debug("Upserting pipeline %s.%s", svc_slug, node_slug)
                    if not self.dry_run:
                        entity_id = self.client.upsert_pipeline(
                            service_name=svc_slug,
                            pipeline_name=node_slug,
                            description=desc,
                            tags=tag_list,
                        )
                    pushed += 1
                    node_registry[nid] = ("pipeline", entity_id)

            except OpenMetadataError as exc:
                errors.append(str(exc))

        # ── Lineage ───────────────────────────────────────────────────────────
        lineage_pushed = 0

        # Combine dd_lineage rows + graph edges
        lineage_pairs: list[tuple[str, str]] = [
            (lr["source_node_id"], lr["target_node_id"]) for lr in lineage_rows
        ]
        for edge in edges:
            src_id = edge.get("source", "")
            tgt_id = edge.get("target", "")
            if src_id and tgt_id:
                lineage_pairs.append((src_id, tgt_id))

        seen_pairs: set[tuple[str, str]] = set()
        for src_id, tgt_id in lineage_pairs:
            pair = (src_id, tgt_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            src_reg = node_registry.get(src_id)
            tgt_reg = node_registry.get(tgt_id)
            if not src_reg or not tgt_reg:
                continue
            src_kind, src_entity_id = src_reg
            tgt_kind, tgt_entity_id = tgt_reg
            if not src_entity_id or not tgt_entity_id:
                continue  # dry-run: ids are empty
            logger.debug("Lineage %s(%s) → %s(%s)", src_kind, src_entity_id, tgt_kind, tgt_entity_id)
            if not self.dry_run:
                try:
                    self.client.add_lineage(src_kind, src_entity_id, tgt_kind, tgt_entity_id)
                    lineage_pushed += 1
                except OpenMetadataError as exc:
                    errors.append(str(exc))
            else:
                lineage_pushed += 1

        return {
            "design_id": design_id,
            "design_name": design_name,
            "entities_pushed": pushed,
            "lineage_edges_pushed": lineage_pushed,
            "errors": errors,
            "status": "ok" if not errors else "partial",
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def sync_design(self, design_id: str) -> dict:
        """Sync one DDC design to OpenMetadata."""
        design = self._fetch_design(design_id)
        if design is None:
            return {"status": "error", "message": f"Design {design_id!r} not found"}
        if not self.dry_run:
            self._ensure_all_tags()
        return self._push_design(design)

    def sync_all(self) -> dict:
        """Sync all DDC designs to OpenMetadata."""
        designs = self._fetch_all_designs()
        if not self.dry_run:
            self._ensure_all_tags()
        results = []
        total_entities = 0
        total_lineage = 0
        total_errors = 0
        for design in designs:
            r = self._push_design(design)
            results.append(r)
            total_entities += r.get("entities_pushed", 0)
            total_lineage += r.get("lineage_edges_pushed", 0)
            total_errors += len(r.get("errors", []))
        return {
            "status": "ok" if total_errors == 0 else "partial",
            "designs_synced": len(designs),
            "total_entities_pushed": total_entities,
            "total_lineage_edges_pushed": total_lineage,
            "total_errors": total_errors,
            "dry_run": self.dry_run,
            "openmetadata_url": self.cfg["url"],
            "results": results,
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Push DDC entities + lineage to OpenMetadata"
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--design-id", help="Sync a single design by ID")
    grp.add_argument("--all", action="store_true", help="Sync all designs")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if any errors")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        get_logger("icdev.ddc.openmetadata").setLevel(logging.DEBUG)

    syncer = DDCOpenMetadataSync(dry_run=args.dry_run)

    if not args.dry_run:
        if not syncer.client.ping():
            result = {
                "status": "error",
                "message": f"OpenMetadata not reachable at {syncer.cfg['url']}",
            }
            if args.json_out:
                print(json.dumps(result, indent=2))
            else:
                print(f"ERROR: {result['message']}")
            sys.exit(1)

    if args.all:
        result = syncer.sync_all()
    else:
        result = syncer.sync_design(args.design_id)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status", "?")
        if args.all:
            print(
                f"[{status.upper()}] {result.get('designs_synced', 0)} designs | "
                f"{result.get('total_entities_pushed', 0)} entities | "
                f"{result.get('total_lineage_edges_pushed', 0)} lineage edges | "
                f"errors={result.get('total_errors', 0)}"
            )
        else:
            print(
                f"[{status.upper()}] {result.get('design_name', '')} | "
                f"entities={result.get('entities_pushed', 0)} | "
                f"lineage={result.get('lineage_edges_pushed', 0)} | "
                f"errors={len(result.get('errors', []))}"
            )

    if args.gate and result.get("status") != "ok":
        sys.exit(1)


if __name__ == "__main__":
    _cli()
