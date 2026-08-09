#!/usr/bin/env python3
# CUI // SP-CTI
"""DDC → DataHub one-way sync adapter.

Pushes DDC entities, lineage edges, and classification tags to a running
DataHub instance via the DataHub GMS REST API (v2).

Supported DataHub versions: 0.10.x – 0.14.x
API reference: https://datahubproject.io/docs/api/restli/restli-overview

Entity mapping
--------------
DDC node type         DataHub entity type   DataHub platform
ent-table / ent-view  dataset               ddc_sql
ent-collection        dataset               ddc_nosql
ent-topic             dataset               ddc_kafka
ent-datalake          dataset               ddc_s3
ent-warehouse         dataset               ddc_warehouse
ent-cache             dataset               ddc_cache
ent-queue             dataset               ddc_queue
ent-graph             dataset               ddc_graph
ent-timeseries        dataset               ddc_timeseries
ent-vector            dataset               ddc_vector
ent-file              dataset               ddc_file
flow-*                dataJob               ddc_pipeline
col-pii / col-phi
/ col-cui / col-secret classification tag   globalTag

Lineage edges in dd_lineage → DataHub UpstreamLineage aspect on target dataset.

Usage
-----
    # dry-run (no writes to DataHub)
    python tools/data_canvas/sync/datahub_sync.py --design-id <id> --dry-run --json

    # push single design
    python tools/data_canvas/sync/datahub_sync.py --design-id <id> --json

    # push all designs
    python tools/data_canvas/sync/datahub_sync.py --all --json

    # gate (exit 1 if any push fails)
    python tools/data_canvas/sync/datahub_sync.py --all --gate --json

Configuration (env vars or args/datahub_config.yaml)
-----
    ICDEV_DATAHUB_URL          DataHub GMS base URL  (default: http://localhost:8080)
    ICDEV_DATAHUB_TOKEN        Personal access token (leave empty for local/no-auth)
    ICDEV_DATAHUB_ENV          DataHub fabric env    (default: DEV)
    ICDEV_DATAHUB_TIMEOUT      HTTP timeout seconds  (default: 15)
"""

from __future__ import annotations
import re

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Ensure ICDev root is on sys.path when run directly
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
logger = get_logger("icdev.ddc.datahub")

# ── Paths ─────────────────────────────────────────────────────────────────────
_CONFIG_PATH = _ROOT / "args" / "datahub_config.yaml"

# ── Fabric env labels DataHub recognises ──────────────────────────────────────
_VALID_ENVS = {"DEV", "TEST", "STAGING", "PROD", "CORP"}

# ── DDC node type → DataHub platform slug ────────────────────────────────────
_PLATFORM_MAP: dict[str, str] = {
    "ent-table": "ddc_sql",
    "ent-view": "ddc_sql",
    "ent-collection": "ddc_nosql",
    "ent-topic": "ddc_kafka",
    "ent-datalake": "ddc_s3",
    "ent-warehouse": "ddc_warehouse",
    "ent-cache": "ddc_cache",
    "ent-queue": "ddc_queue",
    "ent-graph": "ddc_graph",
    "ent-timeseries": "ddc_timeseries",
    "ent-vector": "ddc_vector",
    "ent-file": "ddc_file",
    # Data Mesh node types
    "ent-data-product": "ddc_data_product",
    "ent-input-port": "ddc_input_port",
    "ent-output-port": "ddc_output_port",
}

# DDC column types that carry a classification tag
_SENSITIVE_COL_TAGS: dict[str, str] = {
    "col-pii": "DDC_PII",
    "col-phi": "DDC_PHI",
    "col-cui": "DDC_CUI",
    "col-secret": "DDC_SECRET",
    "col-encrypted": "DDC_ENCRYPTED",
}

# flow-* DDC types → DataHub dataJob subtype label
_FLOW_SUBTYPES: dict[str, str] = {
    "flow-etl": "ETL",
    "flow-api": "API",
    "flow-replication": "Replication",
    "flow-cdc": "CDC",
    "flow-backup": "Backup",
    "flow-export": "Export",
    "flow-cross-domain": "CrossDomain",
    "flow-column-lineage": "ColumnLineage",
}


# ── Config loader ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    # Start with hardcoded defaults, then layer YAML on top, then env vars
    # (env vars always win — highest priority).
    defaults: dict = {
        "url": "http://localhost:8080",
        "token": "",
        "env": "DEV",
        "timeout": 15,
        "verify_ssl": True,
    }
    if _CONFIG_PATH.exists():
        try:
            import yaml  # type: ignore[import]
            with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            cfg = data.get("datahub", {})
            if cfg.get("url"):
                defaults["url"] = cfg["url"]
            if cfg.get("token") is not None:
                defaults["token"] = cfg["token"]
            if cfg.get("env"):
                defaults["env"] = cfg["env"]
            if cfg.get("timeout"):
                defaults["timeout"] = int(cfg["timeout"])
        except Exception:
            pass
    # Env vars override YAML and hardcoded defaults
    if os.environ.get("ICDEV_DATAHUB_URL"):
        defaults["url"] = os.environ["ICDEV_DATAHUB_URL"]
    if os.environ.get("ICDEV_DATAHUB_TOKEN") is not None:
        defaults["token"] = os.environ.get("ICDEV_DATAHUB_TOKEN", defaults["token"])
    if os.environ.get("ICDEV_DATAHUB_ENV"):
        defaults["env"] = os.environ["ICDEV_DATAHUB_ENV"]
    if os.environ.get("ICDEV_DATAHUB_TIMEOUT"):
        defaults["timeout"] = int(os.environ["ICDEV_DATAHUB_TIMEOUT"])
    defaults["env"] = defaults["env"].upper()
    if defaults["env"] not in _VALID_ENVS:
        defaults["env"] = "DEV"
    return defaults


# ── URN helpers ───────────────────────────────────────────────────────────────

def _platform_urn(platform: str) -> str:
    return f"urn:li:dataPlatform:{platform}"


def _dataset_urn(platform: str, name: str, env: str) -> str:
    safe = urllib.parse.quote(name, safe="")
    return f"urn:li:dataset:({_platform_urn(platform)},{safe},{env})"


def _datajob_urn(flow_id: str, job_id: str) -> str:
    safe_flow = urllib.parse.quote(flow_id, safe="")
    safe_job = urllib.parse.quote(job_id, safe="")
    return f"urn:li:dataJob:(urn:li:dataFlow:(ddc_pipeline,{safe_flow},DEV),{safe_job})"


def _tag_urn(tag: str) -> str:
    return f"urn:li:tag:{tag}"


def _domain_urn(slug: str) -> str:
    return f"urn:li:domain:{slug}"


def _data_product_urn(platform: str, name: str, env: str) -> str:
    return _dataset_urn(platform, name, env)


# ── DataHub REST client ───────────────────────────────────────────────────────

class DataHubError(Exception):
    """Raised on unrecoverable DataHub API errors."""


class DataHubClient:
    """Thin synchronous client for the DataHub GMS REST API.

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
    ) -> dict | None:
        url = f"{self.base_url}{path}"
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
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise DataHubError(
                f"DataHub {method} {path} → HTTP {exc.code}: {body_text[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DataHubError(f"DataHub connection error ({url}): {exc.reason}") from exc

    # ── Health probe ──────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Return True if DataHub GMS is reachable."""
        try:
            self._request("GET", "/health")
            return True
        except DataHubError:
            return False

    # ── Aspect ingest (MetadataChangeProposal) ────────────────────────────────

    def ingest_aspect(self, entity_urn: str, aspect_name: str, aspect_value: dict) -> None:
        """Send a single MetadataChangeProposal to DataHub GMS."""
        proposal = {
            "proposal": {
                "entityType": _entity_type_from_urn(entity_urn),
                "entityUrn": entity_urn,
                "changeType": "UPSERT",
                "aspectName": aspect_name,
                "aspect": {
                    "contentType": "application/json",
                    "value": json.dumps(aspect_value),
                },
            }
        }
        self._request("POST", "/aspects?action=ingestProposal", body=proposal)

    # ── Platform registration ─────────────────────────────────────────────────

    def ensure_platform(self, platform: str, display_name: str = "") -> None:
        """Upsert a DataPlatform entity so datasets can reference it."""
        urn = _platform_urn(platform)
        aspect = {
            "name": platform,
            "displayName": display_name or platform.replace("_", " ").title(),
            "datasetNameDelimiter": ".",
            "type": "OTHERS",
        }
        self.ingest_aspect(urn, "dataPlatformInfo", aspect)

    # ── Dataset ───────────────────────────────────────────────────────────────

    def upsert_dataset(
        self,
        urn: str,
        name: str,
        description: str,
        platform: str,
        schema_fields: list[dict],
        tags: list[str],
        custom_props: dict[str, str],
    ) -> None:
        """Push dataset entity aspects to DataHub."""
        # Dataset properties
        props = {
            "name": name,
            "description": description,
            "customProperties": custom_props,
        }
        self.ingest_aspect(urn, "datasetProperties", props)

        # Schema metadata
        if schema_fields:
            schema = {
                "schemaName": name,
                "platform": _platform_urn(platform),
                "version": 0,
                "hash": "",
                "platformSchema": {"com.linkedin.schema.OtherSchema": {"rawSchema": ""}},
                "fields": schema_fields,
                "created": {"time": 0, "actor": "urn:li:corpuser:datahub"},
                "lastModified": {"time": 0, "actor": "urn:li:corpuser:datahub"},
            }
            self.ingest_aspect(urn, "schemaMetadata", schema)

        # Global tags
        if tags:
            global_tags = {
                "tags": [
                    {"tag": _tag_urn(t), "attribution": {"actor": "urn:li:corpuser:ddc_sync"}}
                    for t in tags
                ]
            }
            self.ingest_aspect(urn, "globalTags", global_tags)

    # ── DataJob (flow nodes) ──────────────────────────────────────────────────

    def upsert_datajob(
        self,
        urn: str,
        name: str,
        description: str,
        flow_urn: str,
        subtype: str,
        custom_props: dict[str, str],
    ) -> None:
        """Push a dataJob (pipeline step) entity."""
        props = {
            "name": name,
            "description": description,
            "customProperties": custom_props,
            "externalUrl": "",
        }
        self.ingest_aspect(urn, "dataJobInfo", props)

    # ── Lineage ───────────────────────────────────────────────────────────────

    def upsert_upstream_lineage(self, downstream_urn: str, upstream_urns: list[str]) -> None:
        """Set upstream lineage for a dataset."""
        if not upstream_urns:
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        lineage = {
            "upstreams": [
                {
                    "dataset": u,
                    "type": "TRANSFORMED",
                    "auditStamp": {
                        "time": now_ms,
                        "actor": "urn:li:corpuser:ddc_sync",
                    },
                }
                for u in upstream_urns
            ]
        }
        self.ingest_aspect(downstream_urn, "upstreamLineage", lineage)

    # ── Tag ───────────────────────────────────────────────────────────────────

    def ensure_tag(self, tag_name: str, description: str = "") -> None:
        """Upsert a Tag entity."""
        urn = _tag_urn(tag_name)
        props = {"name": tag_name, "description": description}
        self.ingest_aspect(urn, "tagProperties", props)

    def upsert_domain(self, slug: str, name: str, description: str,
                      parent_urn: str | None = None) -> None:
        """Push a DataHub domain entity (org-level grouping for data products)."""
        urn = _domain_urn(slug)
        props: dict = {"name": name, "description": description}
        if parent_urn:
            props["parentDomain"] = parent_urn
        self.ingest_aspect(urn, "domainProperties", props)

    def set_domain_membership(self, entity_urn: str, domain_slug: str) -> None:
        """Attach an entity to a DataHub domain via domains aspect."""
        aspect = {"domains": [_domain_urn(domain_slug)]}
        self.ingest_aspect(entity_urn, "domains", aspect)


def _entity_type_from_urn(urn: str) -> str:
    """Derive DataHub entityType from URN prefix."""
    if urn.startswith("urn:li:dataset:"):
        return "dataset"
    if urn.startswith("urn:li:dataJob:"):
        return "dataJob"
    if urn.startswith("urn:li:dataFlow:"):
        return "dataFlow"
    if urn.startswith("urn:li:dataPlatform:"):
        return "dataPlatform"
    if urn.startswith("urn:li:tag:"):
        return "tag"
    if urn.startswith("urn:li:domain:"):
        return "domain"
    return "dataset"


# ── DDC graph parser ──────────────────────────────────────────────────────────

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


def _build_schema_fields(children: list[dict]) -> list[dict]:
    """Convert DDC column child nodes to DataHub SchemaField list."""
    fields = []
    for child in children:
        ctype = _node_type(child)
        if not ctype.startswith("col-"):
            continue
        label = _node_label(child)
        desc = _node_desc(child)
        # Map col type to a native type hint
        native_type = "string"
        if ctype == "col-pk":
            native_type = "long"
        elif ctype in ("col-pii", "col-phi", "col-cui", "col-secret"):
            native_type = "string"
        field = {
            "fieldPath": label,
            "description": desc,
            "nativeDataType": native_type,
            "type": {
                "type": {
                    "com.linkedin.schema.StringType" if native_type == "string"
                    else "com.linkedin.schema.NumberType": {}
                }
            },
            "nullable": ctype not in ("col-pk",),
            "isPartOfKey": ctype == "col-pk",
            "tags": {"tags": []},
        }
        # Attach sensitivity tags inline on the field
        if ctype in _SENSITIVE_COL_TAGS:
            field["tags"]["tags"].append({"tag": _tag_urn(_SENSITIVE_COL_TAGS[ctype])})
        fields.append(field)
    return fields


# ── Main sync logic ───────────────────────────────────────────────────────────

class DDCDataHubSync:
    """Orchestrates DDC → DataHub push for one or more designs."""

    def __init__(self, config: dict | None = None, dry_run: bool = False):
        self.cfg = config or _load_config()
        self.dry_run = dry_run
        self.client = DataHubClient(
            url=self.cfg["url"],
            token=self.cfg.get("token", ""),
            timeout=self.cfg.get("timeout", 15),
        )
        self.env = self.cfg.get("env", "DEV")
        self._synced_platforms: set[str] = set()

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
            if row is None:
                return None
            return dict(row)
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
                "SELECT source_node_id, target_node_id, lineage_type, column_name "
                "FROM dd_lineage WHERE design_id = ?",
                (design_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Push helpers ──────────────────────────────────────────────────────────

    def _ensure_platform(self, platform: str) -> None:
        if platform in self._synced_platforms:
            return
        if not self.dry_run:
            self.client.ensure_platform(platform)
        self._synced_platforms.add(platform)

    def _ensure_tags(self) -> None:
        """Pre-create all DDC sensitivity tags in DataHub."""
        tag_defs = {
            "DDC_PII": "Personally Identifiable Information (NIST SP 800-53 PL-4)",
            "DDC_PHI": "Protected Health Information (HIPAA §164.312)",
            "DDC_CUI": "Controlled Unclassified Information (32 CFR Part 2002)",
            "DDC_SECRET": "NSA Type 1 / SECRET classification",
            "DDC_ENCRYPTED": "Field is encrypted at rest",
            "DDC_PUBLIC": "Public data — no CUI markings",
            "DDC_FOUO": "For Official Use Only (FOUO)",
            "DDC_DATA_PRODUCT": "ICDEV Data Mesh Data Product",
            "DDC_DOMAIN": "ICDEV Data Mesh Domain",
            "DDC_CONTRACT": "ICDEV Data Mesh Contract (ODCS)",
        }
        for tag_name, desc in tag_defs.items():
            if not self.dry_run:
                self.client.ensure_tag(tag_name, desc)

    def _classification_tags(self, classification: str) -> list[str]:
        mapping = {
            "PUBLIC": ["DDC_PUBLIC"],
            "FOUO": ["DDC_FOUO"],
            "CUI": ["DDC_CUI"],
            "SECRET": ["DDC_SECRET"],
            "TS/SCI": ["DDC_SECRET"],
        }
        for key, tags in mapping.items():
            if key in (classification or "").upper():
                return tags
        return []

    def _push_design(self, design: dict) -> dict:
        """Push all nodes + lineage for one design. Returns per-design summary."""
        design_id = design["id"]
        design_name = design["name"]
        graph = _parse_graph(design.get("graph_json", "{}"))
        nodes: list[dict] = graph.get("nodes", [])
        edges: list[dict] = graph.get("edges", [])
        lineage_rows = self._fetch_lineage(design_id)

        # Build a node map for quick lookup
        node_map: dict[str, dict] = {n["id"]: n for n in nodes}

        # Group child nodes by parent
        children_by_parent: dict[str, list[dict]] = {}
        for edge in edges:
            if edge.get("type") == "parent-child" or edge.get("data", {}).get("relation") == "contains":
                src = edge.get("source", "")
                tgt = edge.get("target", "")
                children_by_parent.setdefault(src, []).append(node_map.get(tgt, {}))

        # URN registry so lineage can cross-reference nodes
        node_urn_map: dict[str, str] = {}

        pushed = 0
        errors: list[str] = []

        for node in nodes:
            ntype = _node_type(node)
            nid = node.get("id", "")
            label = _node_label(node)
            desc = _node_desc(node)
            classif = _node_classification(node) or design.get("classification", "")

            if ntype in _PLATFORM_MAP:
                platform = _PLATFORM_MAP[ntype]
                urn = _dataset_urn(platform, f"{design_name}.{label}", self.env)
                node_urn_map[nid] = urn
                self._ensure_platform(platform)
                children = children_by_parent.get(nid, [])
                schema_fields = _build_schema_fields(children)
                # Collect any sensitivity tags from child columns
                child_tags: list[str] = []
                for child in children:
                    ct = _node_type(child)
                    if ct in _SENSITIVE_COL_TAGS:
                        child_tags.append(_SENSITIVE_COL_TAGS[ct])
                all_tags = list(set(self._classification_tags(classif) + child_tags))
                custom_props = {
                    "ddc_design_id": design_id,
                    "ddc_node_id": nid,
                    "ddc_node_type": ntype,
                    "ddc_classification": classif,
                }
                logger.debug("Upserting dataset %s", urn)
                if not self.dry_run:
                    try:
                        self.client.upsert_dataset(
                            urn=urn,
                            name=label,
                            description=desc,
                            platform=platform,
                            schema_fields=schema_fields,
                            tags=all_tags,
                            custom_props=custom_props,
                        )
                        pushed += 1
                    except DataHubError as exc:
                        errors.append(str(exc))
                else:
                    pushed += 1

            elif ntype in _FLOW_SUBTYPES:
                flow_id = f"ddc_{design_id}"
                job_id = nid
                urn = _datajob_urn(flow_id, job_id)
                node_urn_map[nid] = urn
                flow_urn = f"urn:li:dataFlow:(ddc_pipeline,{urllib.parse.quote(flow_id, safe='')},DEV)"
                custom_props = {
                    "ddc_design_id": design_id,
                    "ddc_node_id": nid,
                    "ddc_flow_type": ntype,
                    "ddc_subtype": _FLOW_SUBTYPES[ntype],
                }
                logger.debug("Upserting dataJob %s", urn)
                if not self.dry_run:
                    try:
                        self.client.upsert_datajob(
                            urn=urn,
                            name=label,
                            description=desc,
                            flow_urn=flow_urn,
                            subtype=_FLOW_SUBTYPES[ntype],
                            custom_props=custom_props,
                        )
                        pushed += 1
                    except DataHubError as exc:
                        errors.append(str(exc))
                else:
                    pushed += 1

        # ── Data Mesh: push domain/data-product entities ─────────────────────
        for node in nodes:
            ntype = _node_type(node)
            nid = node.get("id", "")
            label = _node_label(node)
            desc = _node_desc(node)
            classif = _node_classification(node) or design.get("classification", "")

            if ntype == "ent-domain":
                slug = re.sub(r"[^a-z0-9_]", "_", label.lower()).strip("_") or "domain"
                urn = _domain_urn(slug)
                node_urn_map[nid] = urn
                if not self.dry_run:
                    try:
                        self.client.upsert_domain(
                            slug=slug, name=label, description=desc or f"DDC Domain: {label}"
                        )
                        pushed += 1
                    except DataHubError as exc:
                        errors.append(str(exc))
                else:
                    pushed += 1

            elif ntype in ("ent-data-product", "ent-contract",
                           "ent-input-port", "ent-output-port"):
                platform = _PLATFORM_MAP.get(ntype, "ddc_data_product")
                urn = _dataset_urn(platform, f"{design_name}.{label}", self.env)
                node_urn_map[nid] = urn
                self._ensure_platform(platform)
                all_tags = list(set(
                    self._classification_tags(classif)
                    + (["DDC_DATA_PRODUCT"] if ntype == "ent-data-product" else [])
                    + (["DDC_CONTRACT"] if ntype == "ent-contract" else [])
                ))
                custom_props = {
                    "ddc_design_id": design_id, "ddc_node_id": nid,
                    "ddc_node_type": ntype, "ddc_classification": classif,
                }
                if not self.dry_run:
                    try:
                        self.client.upsert_dataset(
                            urn=urn, name=label, description=desc,
                            platform=platform, schema_fields=[],
                            tags=all_tags, custom_props=custom_props,
                        )
                        pushed += 1
                    except DataHubError as exc:
                        errors.append(str(exc))
                else:
                    pushed += 1

        # ── Lineage ───────────────────────────────────────────────────────────
        lineage_pushed = 0
        # Group lineage by target node
        upstreams_by_target: dict[str, list[str]] = {}
        for lr in lineage_rows:
            src_id = lr["source_node_id"]
            tgt_id = lr["target_node_id"]
            src_urn = node_urn_map.get(src_id)
            tgt_urn = node_urn_map.get(tgt_id)
            if src_urn and tgt_urn and tgt_urn.startswith("urn:li:dataset:"):
                upstreams_by_target.setdefault(tgt_urn, []).append(src_urn)

        # Also derive lineage from flow edges in the graph
        for edge in edges:
            src_id = edge.get("source", "")
            tgt_id = edge.get("target", "")
            if not src_id or not tgt_id:
                continue
            src_urn = node_urn_map.get(src_id)
            tgt_urn = node_urn_map.get(tgt_id)
            if src_urn and tgt_urn and tgt_urn.startswith("urn:li:dataset:"):
                upstreams_by_target.setdefault(tgt_urn, []).append(src_urn)

        for tgt_urn, up_urns in upstreams_by_target.items():
            logger.debug("Lineage %s ← %s", tgt_urn, up_urns)
            if not self.dry_run:
                try:
                    self.client.upsert_upstream_lineage(tgt_urn, list(set(up_urns)))
                    lineage_pushed += 1
                except DataHubError as exc:
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
        """Sync one DDC design to DataHub."""
        design = self._fetch_design(design_id)
        if design is None:
            return {"status": "error", "message": f"Design {design_id!r} not found"}
        if not self.dry_run:
            self._ensure_tags()
        return self._push_design(design)

    def sync_all(self) -> dict:
        """Sync all DDC designs to DataHub."""
        designs = self._fetch_all_designs()
        if not self.dry_run:
            self._ensure_tags()
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
            "datahub_url": self.cfg["url"],
            "results": results,
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Push DDC entities + lineage to DataHub"
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
        get_logger("icdev.ddc.datahub").setLevel(logging.DEBUG)

    syncer = DDCDataHubSync(dry_run=args.dry_run)

    if not args.dry_run:
        if not syncer.client.ping():
            result = {
                "status": "error",
                "message": f"DataHub GMS not reachable at {syncer.cfg['url']}",
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
