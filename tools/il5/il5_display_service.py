# CUI // SP-CTI
"""ICDEV™ IL5 Display Service — formats ingested IL5/CUI events for UI rendering.

Transforms raw event dicts (from :mod:`tools.il5.ingestion`) into serialized UI
response payloads that support both tabular and chart display components.

NIST 800-53: AC-3 (access enforcement), AU-3 (content of audit records), SC-28.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from tools.il5.ingestion import (
    IL5_CLASSIFICATION,
    IL5_IMPACT_LEVEL,
    SLA_SECONDS,
)

_SLA_MET_LABEL = "MET"
_SLA_VIOLATED_LABEL = "VIOLATED"
_SLA_UNKNOWN_LABEL = "UNKNOWN"


def _format_row(event: Dict[str, Any]) -> Dict[str, Any]:
    raw_met = event.get("sla_met")
    if raw_met == 1:
        sla_label = _SLA_MET_LABEL
    elif raw_met == 0:
        sla_label = _SLA_VIOLATED_LABEL
    else:
        sla_label = _SLA_UNKNOWN_LABEL

    latency = event.get("display_latency_s")
    return {
        "id": event.get("id", ""),
        "source_id": event.get("source_id", ""),
        "classification": event.get("classification", IL5_CLASSIFICATION),
        "impact_level": event.get("impact_level", IL5_IMPACT_LEVEL),
        "ingested_at": event.get("ingested_at", ""),
        "source_published_at": event.get("source_published_at"),
        "display_latency_s": round(latency, 3) if latency is not None else None,
        "sla_status": sla_label,
        "metadata": event.get("metadata", {}),
    }


def _build_chart_data(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate event latencies into a bar-chart-compatible payload."""
    labels: List[str] = []
    latencies: List[Optional[float]] = []
    colors: List[str] = []

    for ev in events:
        labels.append(ev.get("id", "")[:8])
        lat = ev.get("display_latency_s")
        latencies.append(round(lat, 3) if lat is not None else None)
        if ev.get("sla_met") == 1:
            colors.append("#28a745")   # green — SLA met
        elif ev.get("sla_met") == 0:
            colors.append("#dc3545")   # red — SLA violated
        else:
            colors.append("#6c757d")   # grey — unknown

    return {
        "type": "bar",
        "labels": labels,
        "datasets": [
            {
                "label": f"Display Latency (s) — SLA ≤ {SLA_SECONDS}s",
                "data": latencies,
                "backgroundColor": colors,
                "sla_threshold": SLA_SECONDS,
            }
        ],
    }


def render_il5_ui(
    raw_data: Union[List[Dict[str, Any]], Dict[str, Any]],
    *,
    component: str = "table",
    classification_header: bool = True,
) -> str:
    """Format raw IL5 ingestion data into a serialized UI response string.

    Accepts either a list of event dicts (from :func:`tools.il5.ingestion.get_il5_events`)
    or a single event dict and returns a JSON string suitable for rendering a
    table or chart component in the dashboard.

    Args:
        raw_data: Event list from ``get_il5_events()``, or a single event dict.
        component: ``"table"`` (default) or ``"chart"``.
        classification_header: When True, includes CUI banner in the response.

    Returns:
        JSON-serialized UI response string.
    """
    events: List[Dict[str, Any]] = (
        raw_data if isinstance(raw_data, list) else [raw_data]
    )

    rows = [_format_row(ev) for ev in events]
    sla_met_count = sum(1 for ev in events if ev.get("sla_met") == 1)
    sla_violated_count = sum(1 for ev in events if ev.get("sla_met") == 0)
    known = sla_met_count + sla_violated_count
    # NOT ASSESSED, never 100.0 (rem-hyg-13). `known` counts only the events
    # whose SLA outcome could be decided; zero means nothing in this payload was
    # decidable, which is not the same claim as "everything met its SLA".
    compliance_pct = round((sla_met_count / known) * 100, 2) if known > 0 else None

    payload: Dict[str, Any] = {
        "component": component,
        "classification": IL5_CLASSIFICATION if classification_header else None,
        "impact_level": IL5_IMPACT_LEVEL,
        "sla_threshold_s": SLA_SECONDS,
        "summary": {
            "total": len(events),
            "sla_met": sla_met_count,
            "sla_violated": sla_violated_count,
            "sla_unknown": len(events) - known,
            "compliance_pct": compliance_pct,
        },
    }

    if component == "chart":
        payload["chart"] = _build_chart_data(events)
    else:
        payload["table"] = {
            "columns": [
                "id",
                "source_id",
                "classification",
                "impact_level",
                "ingested_at",
                "source_published_at",
                "display_latency_s",
                "sla_status",
            ],
            "rows": rows,
        }

    return json.dumps(payload, ensure_ascii=False)
