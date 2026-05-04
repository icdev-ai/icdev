#!/usr/bin/env python3
# CUI // SP-CTI
"""PPSM Extractor — canvas-aware Port/Protocol/Service Matrix generator.

Derives a normalized matrix from a TFW session's topology graph. Column set
is determined by canvas type via canvas_registry.CANVAS_AVAILABLE_ARTIFACTS:

  ndc  -> Port Protocol Service Matrix
          (port / protocol / service / direction /
           source_zone / destination_zone / classification)
  sdc  -> API Surface Matrix
          (endpoint / method / auth / upstream / downstream / sla)
  eda  -> Event Catalog Matrix
          (topic / schema_ref / producer / consumer / retention / ordering)

Public surface:
  generate_ppsm(session_id, canvas_type) -> list[dict]

Data source (in priority order):
  1. nc_simulation_sessions.metadata["refined_graph_json"]  (diagram_refiner output)
  2. topologies.graph_json  (via nc_simulation_sessions.topology_id)
  3. Empty graph  (returns [])
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.canvas.canvas_registry import CANVAS_AVAILABLE_ARTIFACTS, get_display_name
from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_PROTO_RE = re.compile(r"\b(TCP|UDP|ICMP|GRE|ESP|AH|SCTP)\b", re.I)
_PORT_WITH_PROTO_RE = re.compile(r"(?:TCP|UDP|ICMP|GRE|ESP|AH|SCTP)[\s:/-]*(\d+)", re.I)
_PORT_BARE_RE = re.compile(r":(\d{1,5})\b|^(\d{1,5})$")
_HTTP_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", re.I)
_ENDPOINT_RE = re.compile(r"(/[^\s,]+)")
_AUTH_RE = re.compile(r"\b(OAuth2?|mTLS|JWT|OIDC|SAML|API[.\s-]?Key|Basic|Bearer)\b", re.I)
_SLA_RE = re.compile(r"(\d+)\s*ms\b|SLA\s*[=:]\s*([^\s,]+)", re.I)
_TOPIC_RE = re.compile(
    r"topic[:\s]+([^\s,]+)"
    r"|([a-z][a-z0-9_.-]*\.(?:events?|messages?|commands?|notifications?|updates?))",
    re.I,
)
_RETENTION_RE = re.compile(r"(\d+)\s*(d(?:ays?)?|h(?:ours?)?|w(?:eeks?)?|m(?:onths?)?)\b", re.I)
_ORDERING_RE = re.compile(r"\b(ordered|unordered|fifo|partitioned|sequential|strict)\b", re.I)

# Aliases: non-core canvas types map to their nearest extractor
_CANVAS_ALIASES: dict[str, str] = {
    "bdc": "sdc",
    "idc": "ndc",
    "odc": "ndc",
    "ddc": "eda",
    "pdc": "eda",
    "qdc": "ndc",
    "mdc": "ndc",
}


def _resolve(canvas_type: str) -> str:
    ct = canvas_type.lower()
    return _CANVAS_ALIASES.get(ct, ct)


# ---------------------------------------------------------------------------
# NDC — Port Protocol Service Matrix
# ---------------------------------------------------------------------------

def _extract_port(label: str) -> str:
    m = _PORT_WITH_PROTO_RE.search(label)
    if m:
        return m.group(1)
    m = _PORT_BARE_RE.search(label)
    if m:
        return m.group(1) or m.group(2)
    return "443"


def _extract_protocol(label: str) -> str:
    m = _PROTO_RE.search(label)
    return m.group(1).upper() if m else "TCP"


def _extract_ndc_rows(nodes: list[dict], edges: list[dict]) -> list[dict]:
    node_map = {n["id"]: n for n in nodes}
    rows: list[dict] = []
    for edge in edges:
        label = edge.get("label", "")
        src = node_map.get(edge.get("source", ""), {})
        dst = node_map.get(edge.get("target", ""), {})
        etype = edge.get("type", "arrow")
        direction = "bidirectional" if any(t in etype for t in ("open", "both")) else "outbound"
        rows.append({
            "port": _extract_port(label),
            "protocol": _extract_protocol(label),
            "service": dst.get("label") or label or "unknown",
            "direction": direction,
            "source_zone": src.get("zone") or src.get("label") or "unknown",
            "destination_zone": dst.get("zone") or dst.get("label") or "unknown",
            "classification": "CUI",
        })
    if not rows:
        for n in nodes:
            label = n.get("label", "")
            rows.append({
                "port": "443",
                "protocol": "TCP",
                "service": label or "unknown",
                "direction": "outbound",
                "source_zone": n.get("zone") or "unknown",
                "destination_zone": "unknown",
                "classification": "CUI",
            })
    return rows


# ---------------------------------------------------------------------------
# SDC — API Surface Matrix
# ---------------------------------------------------------------------------

def _extract_sdc_rows(nodes: list[dict], edges: list[dict]) -> list[dict]:
    node_map = {n["id"]: n for n in nodes}
    rows: list[dict] = []
    for edge in edges:
        label = edge.get("label", "")
        src = node_map.get(edge.get("source", ""), {})
        dst = node_map.get(edge.get("target", ""), {})
        dst_slug = (dst.get("label") or "api").lower().replace(" ", "-")

        method_m = _HTTP_METHOD_RE.search(label)
        method = method_m.group(1).upper() if method_m else "GET"

        ep_m = _ENDPOINT_RE.search(label)
        endpoint = ep_m.group(1) if ep_m else f"/{dst_slug}"

        auth_m = _AUTH_RE.search(label)
        auth = auth_m.group(1) if auth_m else "mTLS"

        sla_m = _SLA_RE.search(label)
        if sla_m:
            sla = f"{sla_m.group(1)}ms" if sla_m.group(1) else sla_m.group(2)
        else:
            sla = "200ms"

        rows.append({
            "endpoint": endpoint,
            "method": method,
            "auth": auth,
            "upstream": src.get("label") or edge.get("source") or "unknown",
            "downstream": dst.get("label") or edge.get("target") or "unknown",
            "sla": sla,
        })
    if not rows:
        for n in nodes:
            label = n.get("label", "service")
            rows.append({
                "endpoint": f"/{label.lower().replace(' ', '-')}",
                "method": "GET",
                "auth": "mTLS",
                "upstream": "client",
                "downstream": label,
                "sla": "200ms",
            })
    return rows


# ---------------------------------------------------------------------------
# EDA — Event Catalog Matrix
# ---------------------------------------------------------------------------

_RETENTION_UNIT_MAP = {"d": "d", "h": "h", "w": "w", "m": "m"}


def _extract_eda_rows(nodes: list[dict], edges: list[dict]) -> list[dict]:
    node_map = {n["id"]: n for n in nodes}
    rows: list[dict] = []
    for edge in edges:
        label = edge.get("label", "")
        src = node_map.get(edge.get("source", ""), {})
        dst = node_map.get(edge.get("target", ""), {})

        topic_m = _TOPIC_RE.search(label)
        if topic_m:
            topic = topic_m.group(1) or topic_m.group(2)
        else:
            src_slug = (src.get("label") or "producer").lower().replace(" ", "_")
            topic = label.strip() or f"{src_slug}.events"

        base_name = topic.rsplit(".", 1)[-1]
        schema_ref = f"schemas/{base_name}.avsc"

        ret_m = _RETENTION_RE.search(label)
        if ret_m:
            unit = _RETENTION_UNIT_MAP.get(ret_m.group(2)[0].lower(), "d")
            retention = f"{ret_m.group(1)}{unit}"
        else:
            retention = "7d"

        ord_m = _ORDERING_RE.search(label)
        ordering = ord_m.group(1).lower() if ord_m else "ordered"

        rows.append({
            "topic": topic,
            "schema_ref": schema_ref,
            "producer": src.get("label") or edge.get("source") or "unknown",
            "consumer": dst.get("label") or edge.get("target") or "unknown",
            "retention": retention,
            "ordering": ordering,
        })
    if not rows:
        for n in nodes:
            label = n.get("label", "service")
            slug = label.lower().replace(" ", "_")
            rows.append({
                "topic": f"{slug}.events",
                "schema_ref": f"schemas/{slug}.avsc",
                "producer": label,
                "consumer": "unknown",
                "retention": "7d",
                "ordering": "ordered",
            })
    return rows


# ---------------------------------------------------------------------------
# Extraction dispatch
# ---------------------------------------------------------------------------

_EXTRACTORS: dict[str, Any] = {
    "ndc": _extract_ndc_rows,
    "sdc": _extract_sdc_rows,
    "eda": _extract_eda_rows,
}

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_session(conn, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, canvas_type, topology_id, mode, metadata "
        "FROM nc_simulation_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"nc_simulation_sessions: session not found: {session_id}")
    return dict(row)


def _load_graph_json(conn, session: dict[str, Any]) -> dict[str, Any]:
    """Resolve graph_json: refined_graph_json in metadata > topologies table."""
    meta_raw = session.get("metadata") or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}

    if "refined_graph_json" in meta:
        return meta["refined_graph_json"]

    topology_id = session.get("topology_id")
    if topology_id:
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = ?",
            (topology_id,),
        ).fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except json.JSONDecodeError:
                pass

    return {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_ppsm(session_id: str, canvas_type: str) -> list[dict]:
    """Generate the PPSM matrix for a TFW session.

    Args:
        session_id:  nc_simulation_sessions.id
        canvas_type: ndc | sdc | eda (or any registered alias)

    Returns:
        list[dict] — matrix rows; column keys are those listed in
        canvas_registry.CANVAS_AVAILABLE_ARTIFACTS[resolved_canvas_type]["columns"].

    Raises:
        ValueError: session not found or unsupported canvas type.
    """
    resolved = _resolve(canvas_type)
    extractor = _EXTRACTORS.get(resolved)
    if extractor is None:
        supported = list(_EXTRACTORS)
        raise ValueError(
            f"No PPSM extractor for canvas type '{canvas_type}' "
            f"(resolved: '{resolved}'). Supported: {supported}"
        )

    conn = get_connection()
    try:
        session = _load_session(conn, session_id)
        graph = _load_graph_json(conn, session)
    finally:
        conn.close()

    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])
    return extractor(nodes, edges)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a canvas-aware PPSM matrix from a TFW session.",
    )
    p.add_argument("--session-id", required=True, help="nc_simulation_sessions.id")
    p.add_argument(
        "--canvas-type",
        required=True,
        choices=list(_EXTRACTORS) + list(_CANVAS_ALIASES),
        help="Canvas type (ndc | sdc | eda or alias)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    p.add_argument("--list-columns", action="store_true", help="Print column spec and exit")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if args.list_columns:
        resolved = _resolve(args.canvas_type)
        spec = CANVAS_AVAILABLE_ARTIFACTS.get(resolved, {})
        if args.json:
            print(json.dumps(spec, indent=2))
        else:
            name = spec.get("artifact_name", resolved.upper())
            cols = spec.get("columns", [])
            print(f"{name}: {', '.join(cols)}")
        return

    rows = generate_ppsm(args.session_id, args.canvas_type)

    if args.json:
        resolved = _resolve(args.canvas_type)
        spec = CANVAS_AVAILABLE_ARTIFACTS.get(resolved, {})
        print(
            json.dumps(
                {
                    "session_id": args.session_id,
                    "canvas_type": args.canvas_type,
                    "artifact_name": spec.get("artifact_name", "PPSM"),
                    "columns": spec.get("columns", []),
                    "rows": rows,
                    "count": len(rows),
                },
                indent=2,
            )
        )
    else:
        display = get_display_name(args.canvas_type)
        resolved = _resolve(args.canvas_type)
        spec = CANVAS_AVAILABLE_ARTIFACTS.get(resolved, {})
        name = spec.get("artifact_name", "PPSM")
        cols = spec.get("columns", [])
        print(f"[{display}] {name} — {len(rows)} row(s)")
        if cols and rows:
            header = " | ".join(f"{c:<20}" for c in cols)
            print(header)
            print("-" * len(header))
            for row in rows:
                print(" | ".join(f"{str(row.get(c, '')):<20}" for c in cols))


if __name__ == "__main__":
    main()
