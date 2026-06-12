# CUI // SP-CTI — ICDEV Data Design Canvas dbt Exporter
"""dbt (data build tool) project generator for DDC graphs.

Exports a DDC design as:
  sources.yml — dbt source declarations (entity nodes as raw tables)
  models.yml  — dbt staging model declarations with column-level tests

Supported dialects/warehouses (set via DBT_TARGET env var):
  redshift, bigquery, snowflake, duckdb (default)

Specification: https://docs.getdbt.com/reference/project-configs

Stdlib + pyyaml only (no network/DB calls).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# Entity types mapped as dbt sources
_SOURCE_ENTITY_TYPES = {
    "ent-table", "ent-view", "ent-collection",
    "ent-warehouse", "ent-datalake", "ent-file",
    "ent-timeseries", "ent-vector",
}

# Streaming entities → incremental materialization
_INCREMENTAL_TYPES = {"ent-topic", "ent-queue", "ent-cache"}

# DDC col type → dbt test set + meta
_COL_META: dict[str, dict] = {
    "col-pk":        {"tests": ["not_null", "unique"], "sensitivity": "internal"},
    "col-fk":        {"tests": ["not_null"],           "sensitivity": "internal"},
    "col-data":      {"tests": [],                     "sensitivity": "standard"},
    "col-pii":       {"tests": ["not_null"],           "sensitivity": "PII",    "tags": ["pii"]},
    "col-phi":       {"tests": ["not_null"],           "sensitivity": "PHI",    "tags": ["phi"]},
    "col-cui":       {"tests": ["not_null"],           "sensitivity": "CUI",    "tags": ["cui"]},
    "col-secret":    {"tests": ["not_null"],           "sensitivity": "SECRET", "tags": ["secret"]},
    "col-encrypted": {"tests": [],                     "sensitivity": "encrypted"},
    "col-audit":     {"tests": ["not_null"],           "sensitivity": "internal", "tags": ["audit"]},
}

# DDC col type → SQL data type (Iceberg-compatible)
_COL_DTYPE: dict[str, str] = {
    "col-pk":        "bigint",
    "col-fk":        "bigint",
    "col-data":      "varchar",
    "col-pii":       "varchar",
    "col-phi":       "varchar",
    "col-cui":       "varchar",
    "col-secret":    "varchar",
    "col-encrypted": "varbinary",
    "col-audit":     "timestamp",
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


def _build_column_defs(cols: list) -> list[dict]:
    result = []
    for col in cols:
        col_type = col.get("type", "col-data")
        col_name = _slug(_node_label(col))
        spec = _COL_META.get(col_type, _COL_META["col-data"])
        entry: dict[str, Any] = {
            "name": col_name,
            "description": f"{col_type.replace('col-', '').upper()} column",
            "data_type": _COL_DTYPE.get(col_type, "varchar"),
            "meta": {"sensitivity": spec["sensitivity"]},
        }
        if spec.get("tags"):
            entry["meta"]["tags"] = spec["tags"]
        if spec["tests"]:
            entry["tests"] = spec["tests"]
        result.append(entry)
    return result


def _to_yaml(obj: Any) -> str:
    if _HAS_YAML:
        return _yaml.dump(
            obj, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120
        )
    return json.dumps(obj, indent=2, ensure_ascii=False)


def export_dbt(
    name: str,
    graph_json,
    design_id: str | None = None,
    classification: str = "CUI",
) -> tuple[bytes, bytes]:
    """Export DDC graph as dbt sources.yml + models.yml.

    Parameters
    ----------
    name : str
        Human-readable design name.
    graph_json : dict | str
        DDC graph JSON.
    design_id : str, optional
        Design UUID.
    classification : str
        Classification marking (e.g. "CUI", "SECRET").

    Returns
    -------
    tuple[bytes, bytes]
        (sources_yml_bytes, models_yml_bytes) — UTF-8 encoded YAML files.
    """
    nodes, edges, _boundaries = _parse_graph(graph_json)
    ntypes = {n["id"]: n.get("type", "") for n in nodes}
    nodes_by_id = {n["id"]: n for n in nodes}

    design_slug = _slug(name)
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    classification_tag = classification.lower().replace("/", "_").replace(" ", "_")

    source_tables: list[dict] = []
    model_entries: list[dict] = []

    for node in nodes:
        ntype = ntypes.get(node["id"], "")
        if ntype not in (_SOURCE_ENTITY_TYPES | _INCREMENTAL_TYPES):
            continue

        label = _node_label(node)
        table_slug = _slug(label)
        cols = _entity_columns(node["id"], edges, nodes_by_id, ntypes)
        col_defs = _build_column_defs(cols)

        # sources.yml table entry
        src_table: dict[str, Any] = {
            "name": table_slug,
            "description": f"{label} ({ntype}) — classification: {classification}",
        }
        if col_defs:
            src_table["columns"] = [
                {
                    "name": c["name"],
                    "description": c.get("description", ""),
                    **({"tests": c["tests"]} if c.get("tests") else {}),
                }
                for c in col_defs
            ]
        source_tables.append(src_table)

        # models.yml staging model
        materialized = "incremental" if ntype in _INCREMENTAL_TYPES else "table"
        model_tags = ["icdev", "ddc", classification_tag]
        if ntype == "ent-datalake":
            model_tags.append("lakehouse")
        elif ntype == "ent-warehouse":
            model_tags.append("warehouse")

        model_entry: dict[str, Any] = {
            "name": f"stg_{design_slug}__{table_slug}",
            "description": (
                f"Staging model for {label}. "
                f"Source: {design_slug}.{table_slug}. "
                f"Classification: {classification}."
            ),
            "config": {
                "materialized": materialized,
                "tags": model_tags,
                "meta": {
                    "classification": classification,
                    "ddc_design_id": design_id or "",
                    "ddc_entity_type": ntype,
                    "generated_by": "ICDEV DDC dbt Exporter",
                    "generated_at": now_ts,
                },
            },
        }
        if col_defs:
            model_entry["columns"] = col_defs
        model_entries.append(model_entry)

    sources_doc: dict[str, Any] = {
        "version": 2,
        "sources": [
            {
                "name": design_slug,
                "description": (
                    f"Data design '{name}' — ICDEV™ DDC dbt Exporter. "
                    f"Classification: {classification}."
                ),
                "database": "{{ env_var('DBT_DATABASE', 'warehouse') }}",
                "schema": "{{ env_var('DBT_SCHEMA', 'raw') }}",
                "meta": {
                    "classification": classification,
                    "ddc_design_id": design_id or "",
                    "generated_at": now_ts,
                },
                "tables": source_tables,
            }
        ],
    }

    models_doc: dict[str, Any] = {
        "version": 2,
        "models": model_entries,
    }

    header = (
        f"# CUI // SP-CTI\n"
        f"# Generated by ICDEV™ DDC dbt Exporter — {now_ts}\n"
        f"# Design: {design_id or name}\n#\n"
    )

    sources_yml = (header + _to_yaml(sources_doc)).encode("utf-8")
    models_yml = (header + _to_yaml(models_doc)).encode("utf-8")
    return sources_yml, models_yml
