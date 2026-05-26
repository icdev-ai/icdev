# CUI // SP-CTI — ICDEV Data Design Canvas Apache Iceberg DDL Exporter
"""Apache Iceberg DDL exporter for DDC graphs.

Given a DDC design, generates Apache Iceberg CREATE TABLE statements for:
  ent-datalake  → one table per medallion zone (landing/raw/curated/consumption)
  ent-warehouse → OLAP Iceberg tables (consumption-tuned properties)
  ent-table     → standard Iceberg migration DDL with column lineage

One SQL file per catalog target:
  glue.sql      — AWS Glue Data Catalog (GovCloud-compatible)
  biglake.sql   — GCP BigLake / BigQuery Metastore
  synapse.sql   — Azure Synapse Analytics (Iceberg over ADLS Gen2)
  nessie.sql    — Nessie REST Catalog (OSS, air-gap safe)
  duckdb.sql    — DuckDB local dev (INSTALL iceberg; LOAD iceberg)

Stdlib only (no network/DB calls).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

# Entity types that become Iceberg tables
_ICEBERG_ENTITY_TYPES = {
    "ent-table", "ent-view", "ent-collection",
    "ent-warehouse", "ent-datalake",
}

# DDC col type → Iceberg SQL type
_COL_ICEBERG_TYPE: dict[str, str] = {
    "col-pk":        "BIGINT",
    "col-fk":        "BIGINT",
    "col-data":      "STRING",
    "col-pii":       "STRING",
    "col-phi":       "STRING",
    "col-cui":       "STRING",
    "col-secret":    "STRING",
    "col-encrypted": "BINARY",
    "col-audit":     "TIMESTAMP",
}

_COL_COMMENT: dict[str, str] = {
    "col-pk":        "PRIMARY KEY",
    "col-fk":        "FOREIGN KEY",
    "col-data":      "data column",
    "col-pii":       "PII — masked in consumption zone",
    "col-phi":       "PHI — masked in consumption zone",
    "col-cui":       "CUI — encrypted at rest",
    "col-secret":    "SECRET — need-to-know access",
    "col-encrypted": "pre-encrypted blob",
    "col-audit":     "audit timestamp",
}

# Iceberg catalog prefix per target
_CATALOG_PREFIX: dict[str, str] = {
    "glue":    "glue_catalog",
    "biglake": "biglake_catalog",
    "synapse": "synapse_catalog",
    "nessie":  "nessie",
    "duckdb":  "iceberg_catalog",
}

# Iceberg TBLPROPERTIES tuned per medallion zone
_ZONE_PROPS: dict[str, dict] = {
    "landing": {
        "write.format.default":            "parquet",
        "write.parquet.compression-codec": "snappy",
        "write.delete.mode":               "copy-on-write",
        "gc.enabled":                      "true",
    },
    "raw": {
        "write.format.default":                    "parquet",
        "write.parquet.compression-codec":         "zstd",
        "write.delete.mode":                       "copy-on-write",
        "history.expire.min-snapshots-to-keep":    "3",
    },
    "curated": {
        "write.format.default":                    "parquet",
        "write.parquet.compression-codec":         "zstd",
        "write.delete.mode":                       "merge-on-read",
        "history.expire.min-snapshots-to-keep":    "5",
        "write.metadata.metrics.default":          "full",
    },
    "consumption": {
        "write.format.default":                    "parquet",
        "write.parquet.compression-codec":         "zstd",
        "write.delete.mode":                       "merge-on-read",
        "history.expire.min-snapshots-to-keep":    "10",
        "write.metadata.metrics.default":          "full",
        "read.split.target-size":                  "134217728",
    },
    "default": {
        "write.format.default":                    "parquet",
        "write.parquet.compression-codec":         "zstd",
        "write.delete.mode":                       "merge-on-read",
        "history.expire.min-snapshots-to-keep":    "5",
    },
}

_ZONE_PARTITION: dict[str, str] = {
    "landing":     "",
    "raw":         "days(created_at)",
    "curated":     "days(created_at)",
    "consumption": "months(created_at)",
}


def _parse_graph(graph_json) -> tuple[list, list, list]:
    if isinstance(graph_json, str):
        try:
            g = json.loads(graph_json)
        except json.JSONDecodeError:
            return [], [], []
    else:
        g = graph_json or {}
    return g.get("nodes", []), g.get("edges", []), g.get("boundaries", [])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", text.lower().strip()).strip("_") or "entity"


def _node_label(node: dict) -> str:
    return node.get("label") or node.get("id", "unknown")


def _entity_columns(entity_id: str, edges: list, nodes_by_id: dict, ntypes: dict) -> list[dict]:
    cols = []
    for edge in edges:
        src, tgt = edge.get("source", ""), edge.get("target", "")
        if src == entity_id and ntypes.get(tgt, "").startswith("col-"):
            cols.append(nodes_by_id[tgt])
        elif tgt == entity_id and ntypes.get(src, "").startswith("col-"):
            cols.append(nodes_by_id[src])
    return cols


def _build_column_ddl(cols: list, indent: str = "  ") -> list[str]:
    lines = []
    for col in cols:
        col_type = col.get("type", "col-data")
        col_name = _slug(_node_label(col))
        sql_type = _COL_ICEBERG_TYPE.get(col_type, "STRING")
        comment = _COL_COMMENT.get(col_type, "")
        nullable = "NOT NULL" if col_type == "col-pk" else ""
        parts = [f"{indent}{col_name}  {sql_type}"]
        if nullable:
            parts.append(f"  {nullable}")
        if comment:
            parts.append(f"  COMMENT '{comment}'")
        lines.append("".join(parts))
    if not lines:
        lines.append(f"{indent}id          BIGINT  NOT NULL  COMMENT 'synthetic surrogate key'")
        lines.append(f"{indent}created_at  TIMESTAMP         COMMENT 'audit timestamp'")
    return lines


def _build_tblproperties(props: dict) -> str:
    pairs = ",\n    ".join(f"'{k}' = '{v}'" for k, v in props.items())
    return f"TBLPROPERTIES (\n    {pairs}\n)"


def _create_table(
    catalog_prefix: str,
    database: str,
    table_name: str,
    col_lines: list[str],
    partition_by: str,
    props: dict,
    comment: str,
) -> str:
    cols_str = ",\n".join(col_lines)
    props_str = _build_tblproperties(props)
    partition_clause = f"PARTITIONED BY ({partition_by})\n" if partition_by else ""
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog_prefix}.{database}.{table_name} (\n"
        f"{cols_str}\n"
        f")\n"
        f"USING iceberg\n"
        f"{partition_clause}"
        f"COMMENT '{comment}'\n"
        f"{props_str};\n"
    )


_CATALOG_BOOTSTRAP: dict[str, str] = {
    "glue": (
        "-- AWS Glue Data Catalog — Spark config required:\n"
        "--   spark.sql.catalog.glue_catalog = org.apache.iceberg.spark.SparkCatalog\n"
        "--   spark.sql.catalog.glue_catalog.catalog-impl = org.apache.iceberg.aws.glue.GlueCatalog\n"
        "--   spark.sql.catalog.glue_catalog.io-impl = org.apache.iceberg.aws.s3.S3FileIO\n\n"
    ),
    "biglake": (
        "-- GCP BigLake / BigQuery Metastore — Spark config required:\n"
        "--   spark.sql.catalog.biglake_catalog = org.apache.iceberg.spark.SparkCatalog\n"
        "--   spark.sql.catalog.biglake_catalog.catalog-impl = org.apache.iceberg.gcp.biglake.BigLakeCatalog\n"
        "--   spark.sql.catalog.biglake_catalog.gcp_project = <your-project>\n\n"
    ),
    "synapse": (
        "-- Azure Synapse Analytics (Iceberg over ADLS Gen2) — Spark config:\n"
        "--   spark.sql.catalog.synapse_catalog = org.apache.iceberg.spark.SparkCatalog\n"
        "--   spark.sql.catalog.synapse_catalog.type = hadoop\n"
        "--   spark.sql.catalog.synapse_catalog.warehouse = abfss://curated@<acct>.dfs.core.windows.net\n\n"
    ),
    "nessie": (
        "-- Nessie REST Catalog (OSS, air-gap safe) — Spark config:\n"
        "--   spark.sql.catalog.nessie.uri = http://localhost:19120/api/v1\n"
        "--   spark.sql.catalog.nessie.type = nessie\n"
        "--   spark.sql.catalog.nessie.ref = main\n\n"
    ),
    "duckdb": (
        "-- DuckDB local dev\n"
        "INSTALL iceberg;\n"
        "LOAD iceberg;\n"
        "CREATE SCHEMA IF NOT EXISTS iceberg_catalog.raw;\n"
        "CREATE SCHEMA IF NOT EXISTS iceberg_catalog.curated;\n"
        "CREATE SCHEMA IF NOT EXISTS iceberg_catalog.consumption;\n\n"
    ),
}


def export_iceberg(
    name: str,
    graph_json,
    design_id: str | None = None,
    classification: str = "CUI",
) -> dict[str, bytes]:
    """Export DDC graph as Apache Iceberg DDL, one SQL file per catalog target.

    Parameters
    ----------
    name : str
        Design name.
    graph_json : dict | str
        DDC graph JSON.
    design_id : str, optional
        Design UUID.
    classification : str
        Classification marking.

    Returns
    -------
    dict[str, bytes]
        Filename → bytes. Keys: "glue.sql", "biglake.sql", "synapse.sql",
        "nessie.sql", "duckdb.sql".
    """
    nodes, edges, _boundaries = _parse_graph(graph_json)
    ntypes = {n["id"]: n.get("type", "") for n in nodes}
    nodes_by_id = {n["id"]: n for n in nodes}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    did = design_id or "unknown"
    design_slug = _slug(name)

    header = (
        f"-- CUI // SP-CTI\n"
        f"-- Apache Iceberg DDL — ICDEV™ DDC Iceberg Exporter\n"
        f"-- Design: {name} ({did})\n"
        f"-- Classification: {classification}\n"
        f"-- Generated: {ts}\n"
        f"-- DO NOT EDIT — regenerate via DDC → Export → Iceberg DDL\n\n"
    )

    # sql_parts[catalog_name] = list of SQL strings
    sql_parts: dict[str, list[str]] = {
        cat: [header, bootstrap]
        for cat, bootstrap in _CATALOG_BOOTSTRAP.items()
    }

    for node in nodes:
        ntype = ntypes.get(node["id"], "")
        if ntype not in _ICEBERG_ENTITY_TYPES:
            continue

        label = _node_label(node)
        table_slug = _slug(label)
        cols = _entity_columns(node["id"], edges, nodes_by_id, ntypes)
        col_lines = _build_column_ddl(cols)

        if ntype == "ent-datalake":
            for zone_id in ("landing", "raw", "curated", "consumption"):
                zone_table = f"{table_slug}_{zone_id}"
                partition_by = _ZONE_PARTITION[zone_id]
                props = _ZONE_PROPS[zone_id]
                db = "raw" if zone_id == "landing" else zone_id
                comment = f"{label} — {zone_id} zone — {classification}"

                for cat_name, cat_prefix in _CATALOG_PREFIX.items():
                    ddl = _create_table(
                        cat_prefix, db, zone_table,
                        col_lines, partition_by, props, comment,
                    )
                    sql_parts[cat_name].append(
                        f"-- === {label} / {zone_id} zone ===\n{ddl}\n"
                    )

        elif ntype == "ent-warehouse":
            props = _ZONE_PROPS["consumption"]
            comment = f"{label} — warehouse — {classification}"
            for cat_name, cat_prefix in _CATALOG_PREFIX.items():
                ddl = _create_table(
                    cat_prefix, design_slug, table_slug,
                    col_lines, "", props, comment,
                )
                sql_parts[cat_name].append(f"-- === {label} (warehouse) ===\n{ddl}\n")

        else:
            props = _ZONE_PROPS["default"]
            comment = f"{label} — {ntype} — {classification}"
            has_audit_col = any(c.get("type") == "col-audit" for c in cols)
            partition_by = "days(created_at)" if has_audit_col else ""
            for cat_name, cat_prefix in _CATALOG_PREFIX.items():
                ddl = _create_table(
                    cat_prefix, design_slug, table_slug,
                    col_lines, partition_by, props, comment,
                )
                sql_parts[cat_name].append(f"-- === {label} ({ntype}) ===\n{ddl}\n")

    return {f"{cat}.sql": "".join(parts).encode("utf-8") for cat, parts in sql_parts.items()}
