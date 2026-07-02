# CUI // SP-CTI
"""Dataset ingestion for the Viz Kernel — CSV/JSON → typed table.

Turns a user-uploaded CSV or JSON dataset into a normalized table the
storytelling layer can build dashboards over:

    {
      "name": str,
      "columns": [col, ...],
      "rows": [[v, ...], ...],
      "dimensions": [categorical col, ...],
      "measures": [numeric col, ...],
    }

Type inference: a column is a *measure* when most of its non-empty values parse
as numbers (and it isn't an obvious id/code); otherwise it's a *dimension*.
Pure stdlib; bounded for safety. Aggregation helper mirrors the client runtime.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

MAX_ROWS = 5000
MAX_COLS = 40


def _is_number(v: Any) -> bool:
    if v is None or v == "":
        return False
    try:
        float(str(v).replace(",", "").replace("$", "").replace("%", ""))
        return True
    except (ValueError, TypeError):
        return False


def _to_number(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", ""))
    except (ValueError, TypeError):
        return 0.0


def _infer_types(columns: list[str], rows: list[list]) -> tuple[list[str], list[str]]:
    dims: list[str] = []
    meas: list[str] = []
    n = len(rows) or 1
    for ci, col in enumerate(columns):
        numeric = 0
        distinct = set()
        for r in rows:
            v = r[ci] if ci < len(r) else ""
            if _is_number(v):
                numeric += 1
            distinct.add(str(v))
        # Measure if ≥70% numeric AND it varies like a quantity (not a small code set
        # masquerading as numeric, e.g. a 0/1 flag is better treated as a dimension).
        if numeric >= 0.7 * n and len(distinct) > 2:
            meas.append(col)
        else:
            dims.append(col)
    return dims, meas


def parse_dataset(text: str | None = None, path: str | None = None,
                  name: str = "Dataset") -> dict[str, Any] | None:
    """Parse CSV or JSON text/file into a typed table dict, or None on failure."""
    raw = text or ""
    if not raw and path:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            return None
    raw = raw.strip()
    if not raw:
        return None

    columns: list[str] = []
    rows: list[list] = []

    # Try JSON first (list-of-objects or {columns, rows}).
    if raw[0] in "[{":
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and "columns" in obj and "rows" in obj:
                columns = [str(c) for c in obj["columns"]][:MAX_COLS]
                rows = [list(r)[:MAX_COLS] for r in obj["rows"][:MAX_ROWS]]
            elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
                cols: list[str] = []
                for rec in obj[:MAX_ROWS]:
                    for k in rec.keys():
                        if k not in cols:
                            cols.append(str(k))
                columns = cols[:MAX_COLS]
                rows = [[rec.get(c, "") for c in columns] for rec in obj[:MAX_ROWS]]
        except (json.JSONDecodeError, ValueError, TypeError):
            columns, rows = [], []

    # Fall back to CSV.
    if not columns:
        try:
            reader = list(csv.reader(io.StringIO(raw)))
        except csv.Error:
            return None
        if not reader:
            return None
        columns = [str(c).strip() for c in reader[0]][:MAX_COLS]
        rows = [r[:MAX_COLS] for r in reader[1:MAX_ROWS + 1] if any(str(c).strip() for c in r)]

    if not columns or not rows:
        return None

    dims, meas = _infer_types(columns, rows)
    return {"name": name, "columns": columns, "rows": rows,
            "dimensions": dims, "measures": meas}


def aggregate(rows: list[list], columns: list[str], dimension: str,
              measure: str | None, agg: str = "sum") -> tuple[list[str], list[float]]:
    """Group ``rows`` by ``dimension`` and aggregate ``measure`` (sum/avg/count).

    Mirrors the client runtime aggregation so server-built snapshots
    (PPTX/insights) match what the browser shows.
    """
    di = columns.index(dimension) if dimension in columns else -1
    mi = columns.index(measure) if (measure and measure in columns) else -1
    if di < 0:
        return [], []
    groups: dict[str, dict[str, float]] = {}
    order: list[str] = []
    for r in rows:
        k = str(r[di]) if di < len(r) else ""
        if k not in groups:
            groups[k] = {"sum": 0.0, "n": 0.0}
            order.append(k)
        groups[k]["n"] += 1
        if mi >= 0 and mi < len(r):
            groups[k]["sum"] += _to_number(r[mi])
    cats, vals = [], []
    for k in order:
        g = groups[k]
        if agg == "avg":
            vals.append(g["sum"] / g["n"] if g["n"] else 0.0)
        elif agg == "count":
            vals.append(g["n"])
        else:
            vals.append(g["sum"])
        cats.append(k)
    return cats, vals
