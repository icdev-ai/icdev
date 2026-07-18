# CUI // SP-CTI
"""Uploaded-file ingestion for the BI Dashboard canvas.

Bridges an uploaded CSV/XLSX/JSON file through tools.viz.dataset.parse_dataset
(bounded to MAX_ROWS/MAX_COLS, macro-enabled .xlsm rejected outright) and
persists the resulting typed table into bi_data_sources so it becomes
queryable via the bi.uploaded_datasets IQE collection.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from tools.bi_dashboard.db.init_db import get_connection
from tools.viz.dataset import parse_dataset


def ingest_upload(filename: str, content_bytes: bytes | None = None, text: str | None = None,
                  name: str = "", tenant_id: str = "default",
                  classification: str = "CUI") -> dict[str, Any]:
    """Parse an uploaded file and store it as a bi_data_sources row.

    Returns {"success": True, "id": ..., "dataset": {...}} or
    {"success": False, "error": ...} — never raises.
    """
    is_binary = filename.lower().endswith((".xlsx", ".xlsm"))
    if is_binary:
        dataset = parse_dataset(file_bytes=content_bytes, filename=filename, name=name or filename)
    else:
        raw_text = text if text is not None else (content_bytes.decode("utf-8", errors="replace")
                                                   if content_bytes else "")
        dataset = parse_dataset(text=raw_text, filename=filename, name=name or filename)

    if dataset is None:
        return {"success": False, "error": "Could not parse a tabular dataset from this file "
                                            "(unsupported format, empty, or a macro-enabled .xlsm)."}

    source_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO bi_data_sources "
        "(id, name, source_type, columns_json, dimensions_json, measures_json, rows_json, "
        "row_count, tenant_id, classification) VALUES (%s, %s, 'upload', %s, %s, %s, %s, %s, %s, %s)",
        (
            source_id, dataset["name"],
            json.dumps(dataset["columns"]), json.dumps(dataset["dimensions"]),
            json.dumps(dataset["measures"]), json.dumps(dataset["rows"]),
            len(dataset["rows"]), tenant_id, classification,
        ),
    )
    conn.commit()
    conn.close()
    return {"success": True, "id": source_id, "dataset": dataset}


def get_dataset(source_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """Return the parsed {columns, dimensions, measures, rows} dict for a stored source.

    When ``tenant_id`` is provided the lookup is scoped to that tenant so a
    caller in one tenant cannot read another tenant's uploaded data.
    """
    conn = get_connection()
    sql = (
        "SELECT name, columns_json, dimensions_json, measures_json, rows_json "
        "FROM bi_data_sources WHERE id = %s"
    )
    params: list[Any] = [source_id]
    if tenant_id is not None:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    name, columns_json, dims_json, meas_json, rows_json = row
    return {
        "name": name,
        "columns": json.loads(columns_json or "[]"),
        "dimensions": json.loads(dims_json or "[]"),
        "measures": json.loads(meas_json or "[]"),
        "rows": json.loads(rows_json or "[]"),
    }


def list_datasets(tenant_id: str = "default", limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.execute(
        "SELECT id, name, source_type, row_count, created_at FROM bi_data_sources "
        "WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
        (tenant_id, limit),
    )
    cols = ["id", "name", "source_type", "row_count", "created_at"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows
