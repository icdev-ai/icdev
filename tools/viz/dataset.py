# CUI // SP-CTI
"""Dataset ingestion for the Viz Kernel — CSV/JSON/XLSX → typed table.

Turns a user-uploaded CSV, JSON, or XLSX dataset into a normalized table the
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


def _parse_xlsx(file_bytes: bytes | None, path: str | None, name: str) -> dict[str, Any] | None:
    """Parse the first worksheet of an XLSX file into a typed table dict."""
    try:
        import openpyxl
    except ImportError:
        return None

    try:
        source = io.BytesIO(file_bytes) if file_bytes is not None else path
        if source is None:
            return None
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            rows_iter = ws.iter_rows(values_only=True)
            try:
                header = next(rows_iter)
            except StopIteration:
                return None
            columns = [
                (str(c).strip() if c not in (None, "") else f"col{i + 1}")
                for i, c in enumerate(header)
            ][:MAX_COLS]
            rows: list[list] = []
            for i, r in enumerate(rows_iter):
                if i >= MAX_ROWS:
                    break
                row = ["" if v is None else v for v in r][:MAX_COLS]
                if any(str(v).strip() for v in row):
                    rows.append(row)
        finally:
            wb.close()
    except Exception:
        return None

    if not columns or not rows:
        return None
    dims, meas = _infer_types(columns, rows)
    return {"name": name, "columns": columns, "rows": rows,
            "dimensions": dims, "measures": meas}


def parse_dataset(text: str | None = None, path: str | None = None,
                  file_bytes: bytes | None = None, filename: str = "",
                  name: str = "Dataset") -> dict[str, Any] | None:
    """Parse CSV, JSON, or XLSX text/file/bytes into a typed table dict, or None on failure.

    XLSX is routed by extension (``filename`` or ``path``), read via openpyxl
    in read-only/data-only mode. ``.xlsm`` (macro-enabled) workbooks are
    rejected outright — never parsed, macros never executed.
    """
    ext = ""
    src_for_ext = filename or path or ""
    if "." in src_for_ext:
        ext = src_for_ext.rsplit(".", 1)[-1].lower()

    if ext == "xlsm":
        return None
    if ext == "xlsx":
        return _parse_xlsx(file_bytes=file_bytes, path=path if not file_bytes else None, name=name)

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
