#!/usr/bin/env python3
# CUI // SP-CTI
"""DDC ← OpenMetadata lineage import adapter.

Pulls table/column-level lineage FROM a running OpenMetadata instance INTO
DDC's data classification graph as dd_lineage records.

This is the reverse direction of openmetadata_sync.py, which pushes DDC → OM.
Every imported lineage edge receives a CUI classification overlay by default,
enforcing the policy that classification must be tracked on every data flow.

External nodes ingested from OpenMetadata are added to the DDC design graph
with IDs of the form ``ext:om:<entityId>``.

Usage
-----
    # import lineage for one entity
    python tools/data_canvas/sync/openmetadata_import.py \\
        --design-id <id> --entity service.db.schema.table --json

    # import all table lineage (paginated)
    python tools/data_canvas/sync/openmetadata_import.py \\
        --design-id <id> --all --json

    # auto-create design + import all
    python tools/data_canvas/sync/openmetadata_import.py \\
        --create-design "OM Lineage" --all --json

    # dry-run (parse only, no DB writes)
    python tools/data_canvas/sync/openmetadata_import.py \\
        --design-id <id> --all --dry-run --json

    # gate: exit 1 if any CAT1 CUI policy violation
    python tools/data_canvas/sync/openmetadata_import.py \\
        --design-id <id> --all --gate --json

Configuration (env vars or args/openmetadata_config.yaml)
-----
    ICDEV_OM_URL            OpenMetadata base URL (default: http://localhost:8585)
    ICDEV_OM_TOKEN          JWT bearer token
    ICDEV_OM_TIMEOUT        HTTP timeout seconds (default: 15)
    ICDEV_OM_CLASSIFICATION Default CUI overlay classification (default: CUI)
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
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = get_logger("icdev.ddc.om_import")

_CONFIG_PATH = _ROOT / "args" / "openmetadata_config.yaml"

# ── OM entity type → DDC node type ────────────────────────────────────────────

_OM_ENTITY_MAP: dict[str, str] = {
    "table": "ent-table",
    "databaseschema": "ent-table",
    "database": "ent-table",
    "view": "ent-view",
    "materialized_view": "ent-view",
    "query": "ent-view",
    "topic": "ent-topic",
    "container": "ent-datalake",
    "pipeline": "flow-etl",
    "mlmodel": "ent-table",
    "dashboard": "ent-view",
    "chart": "ent-view",
}

# ── OM tag FQN fragments → DDC classification ────────────────────────────────

_OM_TAG_CLASSIF_MAP: list[tuple[str, str]] = [
    ("tssci", "TS/SCI"),
    ("secret", "SECRET"),
    ("cui", "CUI"),
    ("fouo", "FOUO"),
    ("public", "PUBLIC"),
    ("pii", "CUI"),
    ("phi", "CUI"),
]


# ── Config loader ──────────────────────────────────────────────────────────────

def _load_config() -> dict:
    defaults: dict = {
        "url": "http://localhost:8585",
        "token": "",
        "timeout": 15,
        "default_classification": "CUI",
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
                defaults["token"] = cfg.get("token", "")
            if cfg.get("timeout"):
                defaults["timeout"] = int(cfg["timeout"])
            if cfg.get("default_classification"):
                defaults["default_classification"] = cfg["default_classification"]
        except Exception:
            pass
    for env_key, cfg_key in (
        ("ICDEV_OM_URL", "url"),
        ("ICDEV_OM_TOKEN", "token"),
        ("ICDEV_OM_CLASSIFICATION", "default_classification"),
    ):
        val = os.environ.get(env_key)
        if val is not None:
            defaults[cfg_key] = val
    if os.environ.get("ICDEV_OM_TIMEOUT"):
        defaults["timeout"] = int(os.environ["ICDEV_OM_TIMEOUT"])
    return defaults


# ── REST client (import-oriented) ─────────────────────────────────────────────

class OMImportError(Exception):
    """Raised on unrecoverable OpenMetadata API errors during import."""


class OpenMetadataImportClient:
    """Read-only OM REST client for lineage import.

    Uses only stdlib urllib — no external dependencies.
    Only GET calls are made (this is an import adapter, not an export adapter).
    """

    def __init__(self, url: str, token: str = "", timeout: int = 15):
        self.base_url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        *,
        ignore_404: bool = False,
    ) -> dict | list | None:
        url = f"{self.base_url}/api/v1{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — URL built from admin-configured base_url (https only in production); not user-controlled at runtime
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            if ignore_404 and exc.code == 404:
                return None
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise OMImportError(
                f"OM {method} {path} → HTTP {exc.code}: {body_text[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OMImportError(
                f"OM connection error ({url}): {exc.reason}"
            ) from exc

    def ping(self) -> bool:
        try:
            self._request("GET", "/system/version")
            return True
        except OMImportError:
            return False

    def get_lineage_for_entity(
        self,
        entity_type: str,
        fqn: str,
        upstream_depth: int = 3,
        downstream_depth: int = 3,
    ) -> dict:
        """Fetch the lineage graph for a single entity FQN.

        Returns: {entity, nodes: [...], edges: [{fromEntity, toEntity, lineageDetails}]}
        """
        encoded_fqn = urllib.parse.quote(fqn, safe="")
        result = self._request(
            "GET",
            f"/lineage/{entity_type}/name/{encoded_fqn}",
            params={
                "upstreamDepth": upstream_depth,
                "downstreamDepth": downstream_depth,
            },
            ignore_404=True,
        )
        return result if isinstance(result, dict) else {}

    def get_tables(self, limit: int = 100, offset: int = 0) -> dict:
        """Return a page of tables."""
        result = self._request(
            "GET",
            "/tables",
            params={"limit": limit, "offset": offset, "include": "non-deleted"},
        )
        return result if isinstance(result, dict) else {"data": [], "paging": {}}

    def get_all_tables(self, page_size: int = 100) -> list[dict]:
        """Paginate through all tables."""
        tables: list[dict] = []
        offset = 0
        while True:
            page = self.get_tables(limit=page_size, offset=offset)
            batch = page.get("data", [])
            tables.extend(batch)
            after = (page.get("paging") or {}).get("after")
            if not after or not batch:
                break
            offset += len(batch)
            if offset > 10_000:  # safety cap
                break
        return tables

    def get_pipelines(self, limit: int = 100, offset: int = 0) -> dict:
        result = self._request(
            "GET",
            "/pipelines",
            params={"limit": limit, "offset": offset, "include": "non-deleted"},
        )
        return result if isinstance(result, dict) else {"data": [], "paging": {}}


# ── Mapping helpers ────────────────────────────────────────────────────────────

def _om_entity_to_ddc_type(entity_type: str) -> str:
    return _OM_ENTITY_MAP.get((entity_type or "").lower(), "ent-table")


def _infer_classification_from_tags(tags: list[dict], default: str) -> str:
    """Extract classification from OM entity tags."""
    for tag in tags:
        fqn = (tag.get("tagFQN") or "").lower()
        for fragment, level in _OM_TAG_CLASSIF_MAP:
            if fragment in fqn:
                return level
    return default


def _ext_node_id(entity_id: str) -> str:
    return f"ext:om:{entity_id}"


def _col_lineage_type_from_function(function_desc: str | None) -> str:
    """Map an OM SQL function description to a DDC lineage type."""
    if not function_desc:
        return "col-passthrough"
    lower = (function_desc or "").lower()
    if any(k in lower for k in ("sum", "avg", "count", "min", "max", "group")):
        return "col-aggregate"
    if any(k in lower for k in ("join", "merge")):
        return "col-join"
    if any(k in lower for k in ("filter", "where", "having")):
        return "col-filter"
    if any(k in lower for k in ("union", "intersect", "except")):
        return "col-union"
    if any(k in lower for k in ("cast", "convert", "to_")):
        return "col-cast"
    if any(k in lower for k in ("concat", "derive", "compute", "coalesce", "case")):
        return "col-derive"
    return "col-passthrough"


# ── Main importer ──────────────────────────────────────────────────────────────

class DDCOpenMetadataImporter:
    """Imports OpenMetadata lineage into DDC as dd_lineage records.

    Direction: OM → DDC (read from OM, write to DDC).
    The counterpart openmetadata_sync.py pushes DDC → OM.
    """

    def __init__(self, config: dict | None = None, dry_run: bool = False):
        self.cfg = config or _load_config()
        self.dry_run = dry_run
        self.client = OpenMetadataImportClient(
            url=self.cfg["url"],
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
                (design_id, name, "Lineage imported from OpenMetadata", classification),
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
                    "description": "Imported from OpenMetadata",
                    "classification": classification,
                    "source": "openmetadata",
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
                "Skipping invalid edge %s→%s: %s",
                source_node_id,
                target_node_id,
                validation["errors"],
            )
            return False

        if self.dry_run:
            return True

        conn = self._get_conn()
        try:
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
                    str(uuid.uuid4()),
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
            graph = json.loads(
                (row["graph_json"] if row else None)
                or '{"nodes":[],"edges":[],"boundaries":[]}'
            )
        finally:
            conn.close()

        return generate_contract_assertions(lineage_records, graph)

    # ── Single-entity import ───────────────────────────────────────────────────

    def import_entity(
        self,
        design_id: str,
        fqn: str,
        entity_type: str = "table",
        upstream_depth: int = 3,
        downstream_depth: int = 3,
    ) -> dict:
        """Import lineage for one OpenMetadata entity (by FQN).

        Returns a summary dict.
        """
        default_classif = self._default_classification
        imported_nodes = 0
        imported_edges = 0
        skipped_edges = 0
        errors: list[str] = []

        try:
            lineage_graph = self.client.get_lineage_for_entity(
                entity_type, fqn, upstream_depth, downstream_depth
            )
        except OMImportError as exc:
            return {"status": "error", "message": str(exc)}

        if not lineage_graph:
            return {
                "status": "not_found",
                "message": f"No lineage found for {entity_type}/{fqn}",
                "design_id": design_id,
            }

        # Index all nodes returned (entity + nodes array)
        om_nodes: dict[str, dict] = {}
        root_entity = lineage_graph.get("entity") or {}
        if root_entity.get("id"):
            om_nodes[root_entity["id"]] = root_entity

        for node in lineage_graph.get("nodes", []):
            nid = node.get("id")
            if nid:
                om_nodes[nid] = node

        # Upsert all nodes into DDC graph
        for nid, node in om_nodes.items():
            name = node.get("name") or node.get("displayName") or node.get("fullyQualifiedName") or nid
            etype = node.get("type") or entity_type
            ddc_type = _om_entity_to_ddc_type(etype)
            tags = node.get("tags") or []
            classif = _infer_classification_from_tags(tags, default_classif)
            self._upsert_node_in_graph(design_id, _ext_node_id(nid), name, ddc_type, classif)
            imported_nodes += 1

        # Process lineage edges
        for edge in lineage_graph.get("edges", []):
            from_entity = edge.get("fromEntity") or {}
            to_entity = edge.get("toEntity") or {}
            from_id = from_entity.get("id") or from_entity.get("fqn", "")
            to_id = to_entity.get("id") or to_entity.get("fqn", "")

            if not from_id or not to_id or from_id == to_id:
                skipped_edges += 1
                continue

            src_node_id = _ext_node_id(from_id)
            tgt_node_id = _ext_node_id(to_id)

            # Determine classification for the edge
            src_node = om_nodes.get(from_id, {})
            src_classif = _infer_classification_from_tags(
                src_node.get("tags") or [], default_classif
            )

            lineage_details = edge.get("lineageDetails") or {}
            columns_lineage: list[dict] = lineage_details.get("columnsLineage") or []

            if columns_lineage:
                # Column-level lineage: one dd_lineage record per column mapping
                for col_mapping in columns_lineage:
                    to_col_fqn = col_mapping.get("toColumn") or ""
                    from_col_fqns: list[str] = col_mapping.get("fromColumns") or []

                    # Extract bare column name from FQN (last segment after last dot)
                    to_col = to_col_fqn.rsplit(".", 1)[-1] if to_col_fqn else ""

                    # Map SQL function hint to lineage type
                    sql_query = (lineage_details.get("sqlQuery") or "").lower()
                    lt = _col_lineage_type_from_function(sql_query)

                    for from_col_fqn in from_col_fqns:
                        from_col = from_col_fqn.rsplit(".", 1)[-1] if from_col_fqn else ""
                        # Use the target column name as the canonical column_name for the record
                        col_name = to_col or from_col
                        inserted = self._insert_lineage(
                            design_id=design_id,
                            source_node_id=src_node_id,
                            target_node_id=tgt_node_id,
                            column_name=col_name,
                            lineage_type=lt,
                            classification=src_classif,
                            transform_desc=f"{from_col} → {to_col}" if (from_col and to_col) else "",
                        )
                        if inserted:
                            imported_edges += 1
                        else:
                            skipped_edges += 1
            else:
                # Table-level lineage (no column detail)
                pipeline = lineage_details.get("pipeline") or {}
                transform = pipeline.get("name") or pipeline.get("fullyQualifiedName") or ""
                inserted = self._insert_lineage(
                    design_id=design_id,
                    source_node_id=src_node_id,
                    target_node_id=tgt_node_id,
                    column_name="",
                    lineage_type="flow-column-lineage",
                    classification=src_classif,
                    transform_desc=transform,
                )
                if inserted:
                    imported_edges += 1
                else:
                    skipped_edges += 1

        return {
            "status": "ok",
            "design_id": design_id,
            "entity_fqn": fqn,
            "entity_type": entity_type,
            "nodes_imported": imported_nodes,
            "lineage_edges_imported": imported_edges,
            "lineage_edges_skipped": skipped_edges,
            "errors": errors,
        }

    # ── Bulk import ────────────────────────────────────────────────────────────

    def import_all(
        self,
        design_id: str,
        upstream_depth: int = 3,
        downstream_depth: int = 3,
    ) -> dict:
        """Import lineage for all tables from OpenMetadata.

        Returns an aggregated summary with contract assertion results.
        """
        logger.info("Fetching all tables from OpenMetadata…")
        try:
            tables = self.client.get_all_tables()
        except OMImportError as exc:
            return {"status": "error", "message": str(exc)}

        logger.info("Found %d tables — importing lineage…", len(tables))

        total_nodes = 0
        total_edges = 0
        total_skipped = 0
        errors: list[str] = []
        per_entity: list[dict] = []

        for table in tables:
            fqn = table.get("fullyQualifiedName") or table.get("name") or ""
            if not fqn:
                continue
            try:
                r = self.import_entity(
                    design_id, fqn, "table", upstream_depth, downstream_depth
                )
                total_nodes += r.get("nodes_imported", 0)
                total_edges += r.get("lineage_edges_imported", 0)
                total_skipped += r.get("lineage_edges_skipped", 0)
                errors.extend(r.get("errors", []))
                per_entity.append({"fqn": fqn, "edges": r.get("lineage_edges_imported", 0)})
            except Exception as exc:
                errors.append(f"{fqn}: {exc}")

        # Run contract assertions after all imports
        assertions = self._run_assertions(design_id) if not self.dry_run else []
        cat1_violations = [a for a in assertions if not a["passed"] and a.get("severity") == "CAT1"]

        return {
            "status": (
                "ok"
                if not cat1_violations and not errors
                else "violations"
                if cat1_violations
                else "partial"
            ),
            "design_id": design_id,
            "source": "openmetadata_api",
            "openmetadata_url": self.cfg["url"],
            "dry_run": self.dry_run,
            "tables_processed": len(tables),
            "nodes_imported": total_nodes,
            "lineage_edges_imported": total_edges,
            "lineage_edges_skipped": total_skipped,
            "errors": errors[:50],  # cap error list for readability
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
        description="Import OpenMetadata lineage into DDC with CUI classification overlay"
    )
    target_grp = parser.add_mutually_exclusive_group(required=True)
    target_grp.add_argument("--design-id", help="Import into existing DDC design by ID")
    target_grp.add_argument(
        "--create-design", metavar="NAME", help="Auto-create a new DDC design"
    )

    source_grp = parser.add_mutually_exclusive_group()
    source_grp.add_argument(
        "--entity", metavar="FQN",
        help="Import lineage for one entity (e.g. service.db.schema.table)"
    )
    source_grp.add_argument("--all", action="store_true", help="Import lineage for all tables")

    parser.add_argument(
        "--entity-type", default="table",
        help="OM entity type for --entity (default: table)"
    )
    parser.add_argument(
        "--upstream-depth", type=int, default=3,
        help="Upstream hops to fetch (default: 3)"
    )
    parser.add_argument(
        "--downstream-depth", type=int, default=3,
        help="Downstream hops to fetch (default: 3)"
    )
    parser.add_argument(
        "--classification", default="",
        help="Override default CUI overlay classification"
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if any CAT1 violations")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        get_logger("icdev.ddc.om_import").setLevel(logging.DEBUG)

    cfg = _load_config()
    if args.classification:
        cfg["default_classification"] = args.classification

    importer = DDCOpenMetadataImporter(config=cfg, dry_run=args.dry_run)

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

    # Connectivity check (skip for dry-run)
    if not args.dry_run:
        if not importer.client.ping():
            result = {
                "status": "error",
                "message": f"OpenMetadata not reachable at {cfg['url']}",
            }
            if args.json_out:
                print(json.dumps(result, indent=2))
            else:
                print(f"ERROR: {result['message']}")
            sys.exit(1)

    # Import
    if args.entity:
        result = importer.import_entity(
            design_id,
            args.entity,
            args.entity_type,
            args.upstream_depth,
            args.downstream_depth,
        )
        # Run assertions after single-entity import too
        if not args.dry_run:
            assertions = importer._run_assertions(design_id)
            cat1 = [a for a in assertions if not a["passed"] and a.get("severity") == "CAT1"]
            result["assertions_run"] = len(assertions)
            result["cat1_violations"] = len(cat1)
            result["violation_details"] = [
                {"id": a["assertion_id"], "type": a["type"], "desc": a["description"]}
                for a in cat1
            ]
            if cat1:
                result["status"] = "violations"
    elif args.all:
        result = importer.import_all(
            design_id, args.upstream_depth, args.downstream_depth
        )
    else:
        parser.print_help()
        sys.exit(1)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        status = result.get("status", "?")
        tables = result.get("tables_processed", 1)
        print(
            f"[{status.upper()}] design={result.get('design_id', '?')} | "
            f"tables={tables} | "
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
