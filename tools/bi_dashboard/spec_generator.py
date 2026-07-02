# CUI // SP-CTI
"""NL -> Viz Kernel spec generation for the BI Dashboard canvas.

Given a user's natural-language ask and a real, already-queried dataset
(columns/dimensions/measures/rows from tools.viz.dataset.parse_dataset or an
IQE collection), the LLM chooses ONLY the chart *structure* — kind, chart
type, and which columns map to which axes. It never supplies a number.
Actual values are always computed by tools.viz.dataset.aggregate() over the
real rows, preserving the Viz Kernel's "never fabricates data" guarantee.

Flow: LLM attempt -> (invalid) one retry with the validation error appended
-> (still invalid, or LLM unavailable) deterministic heuristic fallback.
This mirrors the try/LLM-then-template convention already used by
tools.data_canvas.ai_mapper.generate_transforms().
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.viz.dataset import aggregate
from tools.viz.spec import Chart3DSpec, ChartSpec, Series

_HARDPROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "hardprompts" / "bi_dashboard" / "nl_to_chart_spec.md"
)

_VALID_KINDS = {"chart", "chart3d"}
_VALID_2D_TYPES = {"bar", "column", "line", "area", "pie", "donut", "gauge"}
_VALID_3D_TYPES = {"bar3d", "scatter3d", "surface3d"}

_TIME_LIKE_RE = re.compile(r"date|time|month|year|week|day|period|quarter", re.I)


def _load_system_prompt() -> str:
    try:
        return _HARDPROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "Choose a chart structure (kind/chart_type/column mapping) as strict JSON."


def _build_user_prompt(nl_ask: str, dataset: dict, prior_structure: dict | None) -> str:
    parts = [
        f"User request: {nl_ask}",
        f"Columns: {json.dumps(dataset.get('columns', []))}",
        f"Dimensions (categorical): {json.dumps(dataset.get('dimensions', []))}",
        f"Measures (numeric): {json.dumps(dataset.get('measures', []))}",
        f"Sample rows: {json.dumps(dataset.get('rows', [])[:5])}",
    ]
    if prior_structure:
        parts.append(f"Previous chart structure (refine this): {json.dumps(prior_structure)}")
    return "\n".join(parts)


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _validate_structure(structure: Any, dataset: dict) -> str | None:
    """Return an error description if invalid, else None."""
    if not isinstance(structure, dict):
        return "response was not a JSON object"

    kind = structure.get("kind")
    if kind not in _VALID_KINDS:
        return f"kind must be one of {sorted(_VALID_KINDS)}, got {kind!r}"

    chart_type = structure.get("chart_type")
    valid_types = _VALID_2D_TYPES if kind == "chart" else _VALID_3D_TYPES
    if chart_type not in valid_types:
        return f"chart_type must be one of {sorted(valid_types)} for kind={kind!r}, got {chart_type!r}"

    columns = set(dataset.get("columns", []))
    if kind == "chart":
        dimension = structure.get("dimension")
        if chart_type != "gauge" and dimension and dimension not in columns:
            return f"dimension {dimension!r} is not one of the dataset columns"
        measures = structure.get("measures") or []
        if not isinstance(measures, list) or not measures:
            return "measures must be a non-empty list of column names"
        for m in measures:
            if m not in columns:
                return f"measure {m!r} is not one of the dataset columns"
    else:
        for field_name in ("x_field", "y_field", "z_field"):
            value = structure.get(field_name)
            if not value or value not in columns:
                return f"{field_name} must be one of the dataset columns, got {value!r}"

    return None


def _heuristic_structure(dataset: dict) -> dict:
    """Deterministic fallback used when the LLM is unavailable or fails validation twice."""
    dims = list(dataset.get("dimensions", []))
    meas = list(dataset.get("measures", []))
    columns = list(dataset.get("columns", []))

    if len(meas) >= 3:
        return {"kind": "chart3d", "chart_type": "scatter3d",
                "x_field": meas[0], "y_field": meas[1], "z_field": meas[2],
                "title": "", "unit": ""}

    if dims and meas:
        dim = dims[0]
        chart_type = "line" if _TIME_LIKE_RE.search(dim) else "bar"
        return {"kind": "chart", "chart_type": chart_type,
                "dimension": dim, "measures": meas[:3], "title": "", "unit": ""}

    if len(columns) >= 2:
        return {"kind": "chart", "chart_type": "bar",
                "dimension": columns[0], "measures": [columns[1]], "title": "", "unit": ""}

    return {"kind": "chart", "chart_type": "bar", "dimension": "", "measures": [], "title": "", "unit": ""}


def _structure_to_spec(structure: dict, dataset: dict) -> ChartSpec | Chart3DSpec:
    """Populate a spec from a validated structure + the real dataset rows.

    The LLM/heuristic only chose column names — every value here is computed
    from real rows via ``aggregate()``, never supplied by the LLM.
    """
    title = structure.get("title") or ""
    unit = structure.get("unit") or ""
    rows = dataset.get("rows", [])
    columns = dataset.get("columns", [])

    if structure["kind"] == "chart3d":
        chart_type = structure["chart_type"]
        x_field, y_field, z_field = structure["x_field"], structure["y_field"], structure["z_field"]
        xi, yi, zi = columns.index(x_field), columns.index(y_field), columns.index(z_field)

        if chart_type == "bar3d":
            # x/y are categorical (e.g. region/quarter) — aggregate z per (x, y) pair,
            # then index into x_categories/y_categories per the ECharts bar3D contract.
            x_cats = sorted({str(r[xi]) for r in rows if xi < len(r)})
            y_cats = sorted({str(r[yi]) for r in rows if yi < len(r)})
            x_idx = {v: i for i, v in enumerate(x_cats)}
            y_idx = {v: i for i, v in enumerate(y_cats)}
            totals: dict[tuple[int, int], float] = {}
            for row in rows:
                if xi >= len(row) or yi >= len(row) or zi >= len(row):
                    continue
                try:
                    z_val = float(row[zi])
                except (ValueError, TypeError):
                    continue
                key = (x_idx[str(row[xi])], y_idx[str(row[yi])])
                totals[key] = totals.get(key, 0.0) + z_val
            points = [[float(xk), float(yk), v] for (xk, yk), v in sorted(totals.items())]
            return Chart3DSpec(title=title, chart_type=chart_type, points=points,
                               x_categories=x_cats, y_categories=y_cats,
                               x_label=x_field, y_label=y_field, z_label=z_field, unit=unit)

        # scatter3d / surface3d — all three fields are numeric.
        points = []
        for row in rows:
            try:
                points.append([float(row[xi]), float(row[yi]), float(row[zi])])
            except (ValueError, TypeError, IndexError):
                continue
        return Chart3DSpec(title=title, chart_type=chart_type, points=points,
                           x_label=x_field, y_label=y_field, z_label=z_field, unit=unit)

    dimension = structure.get("dimension") or ""
    measures = structure.get("measures") or []
    if not measures:
        return ChartSpec(title=title, chart_type=structure["chart_type"], unit=unit)

    if structure["chart_type"] == "gauge" or not dimension:
        first = measures[0]
        mi = columns.index(first)
        values = [float(r[mi]) for r in rows if mi < len(r)]
        total = sum(values) if values else 0.0
        return ChartSpec(title=title, chart_type=structure["chart_type"],
                         series=[Series(first, [total])], unit=unit)

    categories: list[str] = []
    series: list[Series] = []
    for measure in measures:
        cats, values = aggregate(rows, columns, dimension, measure, "sum")
        if not categories:
            categories = cats
        series.append(Series(measure, values))
    return ChartSpec(title=title, chart_type=structure["chart_type"],
                     categories=categories, series=series, unit=unit)


def generate_spec(
    nl_ask: str,
    dataset: dict,
    prior_structure: dict | None = None,
) -> tuple[ChartSpec | Chart3DSpec, str, dict]:
    """Generate a chart spec from a NL ask over a real, already-queried dataset.

    Returns (spec, method, structure) where ``method`` is one of
    "llm", "llm_retry", or "heuristic", and ``structure`` is the chosen
    column-mapping dict (persisted for the next refinement turn).
    """
    structure: dict | None = None
    method = "heuristic"

    try:
        from tools.llm import get_router  # noqa: PLC0415
        from tools.llm.provider import LLMRequest  # noqa: PLC0415

        router = get_router()
        system_prompt = _load_system_prompt()
        base_prompt = _build_user_prompt(nl_ask, dataset, prior_structure)

        error: str | None = None
        for attempt in range(2):
            prompt = base_prompt if error is None else (
                f"{base_prompt}\n\nYour previous answer was invalid: {error}. "
                "Return corrected JSON only, matching the required shape exactly."
            )
            resp = router.invoke("chart_generation", LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system_prompt,
                max_tokens=400,
                temperature=0.1,
            ))
            candidate = _extract_json(getattr(resp, "content", ""))
            error = _validate_structure(candidate, dataset)
            if error is None:
                structure = candidate
                method = "llm" if attempt == 0 else "llm_retry"
                break
    except Exception:  # noqa: BLE001
        structure = None

    if structure is None:
        structure = _heuristic_structure(dataset)
        method = "heuristic"

    spec = _structure_to_spec(structure, dataset)
    return spec, method, structure
