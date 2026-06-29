#!/usr/bin/env python3
# CUI // SP-CTI
"""CIS Controls v8 Generator — canvas-aware control mapping.

Maps canvas node/edge topology to CIS Controls v8 implementation candidates.
Used by oscal_exporter.py (T16/T18 dependency).

Canvas handling:
  ndc  -> CIS IG1/IG2 controls for network infrastructure
           (CIS 1, 4, 12, 13, 14)
  sdc  -> CIS controls for software / API security
           (CIS 4, 8, 14, 16, 18)
  eda  -> CIS controls for data / event stream security
           (CIS 3, 8, 10, 13)

Public surface:
  get_controls(session_id, canvas_type) -> list[dict]
    Each dict: control_id, title, description, implementation_status, ig_level

Data source:
  1. Canvas topology (nodes/edges) → heuristic control mapping
  2. Hardcoded baseline per canvas type (always included)
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

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# CIS Controls v8 baseline catalog (subset relevant to ICDEV™ canvases)
# ---------------------------------------------------------------------------

_CIS_CATALOG: dict[str, dict] = {
    "CIS-1": {
        "title": "Inventory and Control of Enterprise Assets",
        "ig": "IG1",
        "canvases": {"ndc", "idc"},
    },
    "CIS-2": {
        "title": "Inventory and Control of Software Assets",
        "ig": "IG1",
        "canvases": {"sdc", "bdc"},
    },
    "CIS-3": {
        "title": "Data Protection",
        "ig": "IG1",
        "canvases": {"eda", "ddc", "sdc"},
    },
    "CIS-4": {
        "title": "Secure Configuration of Enterprise Assets and Software",
        "ig": "IG1",
        "canvases": {"ndc", "sdc", "idc", "bdc"},
    },
    "CIS-8": {
        "title": "Audit Log Management",
        "ig": "IG1",
        "canvases": {"ndc", "sdc", "eda", "ddc", "odc"},
    },
    "CIS-10": {
        "title": "Malware Defenses",
        "ig": "IG1",
        "canvases": {"ndc", "eda"},
    },
    "CIS-12": {
        "title": "Network Infrastructure Management",
        "ig": "IG2",
        "canvases": {"ndc", "idc"},
    },
    "CIS-13": {
        "title": "Network Monitoring and Defense",
        "ig": "IG2",
        "canvases": {"ndc", "odc"},
    },
    "CIS-14": {
        "title": "Security Awareness and Skills Training",
        "ig": "IG1",
        "canvases": {"ndc", "sdc", "eda"},
    },
    "CIS-16": {
        "title": "Application Software Security",
        "ig": "IG2",
        "canvases": {"sdc", "bdc"},
    },
    "CIS-18": {
        "title": "Penetration Testing",
        "ig": "IG2",
        "canvases": {"sdc", "ndc"},
    },
}

# ---------------------------------------------------------------------------
# Canvas aliases
# ---------------------------------------------------------------------------

_CANVAS_ALIASES: dict[str, str] = {
    "bdc": "sdc",
    "idc": "ndc",
    "odc": "ndc",
    "ddc": "eda",
    "pdc": "eda",
    "qdc": "ndc",
    "mdc": "ndc",
}

# Keyword → additional control hints from node labels
_LABEL_CONTROL_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bfirewall\b|\bngfw\b|\bwaf\b", re.I), "CIS-13"),
    (re.compile(r"\bvpn\b|\bipsec\b|\bssl\b|\btls\b", re.I), "CIS-12"),
    (re.compile(r"\bdb\b|\bdatabase\b|\bencrypt\b", re.I), "CIS-3"),
    (re.compile(r"\bsiem\b|\blog\b|\bsplunk\b|\belk\b|\bmonitoring\b", re.I), "CIS-8"),
    (re.compile(r"\bantivirus\b|\bav\b|\bedp\b|\bmalware\b", re.I), "CIS-10"),
    (re.compile(r"\bapi.?gate\b|\bapi\b|\bendpoint\b", re.I), "CIS-16"),
    (re.compile(r"\bscan\b|\bpentest\b|\bvulnerability\b", re.I), "CIS-18"),
    (re.compile(r"\bacl\b|\biam\b|\brbac\b|\bpolicy\b", re.I), "CIS-4"),
    (re.compile(r"\bkafka\b|\bsns\b|\bsqs\b|\bqueue\b|\bstream\b", re.I), "CIS-3"),
]


def _resolve(canvas_type: str) -> str:
    ct = canvas_type.lower()
    return _CANVAS_ALIASES.get(ct, ct)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_graph_json(conn, session_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT canvas_type, topology_id, metadata FROM nc_simulation_sessions WHERE id = %s",
        (session_id,),
    ).fetchone()
    if not row:
        return {"nodes": [], "edges": []}
    meta_raw = row[2] or "{}"
    try:
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    except json.JSONDecodeError:
        meta = {}
    if "refined_graph_json" in meta:
        return meta["refined_graph_json"]
    topology_id = row[1]
    if topology_id:
        trow = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = %s", (topology_id,)
        ).fetchone()
        if trow and trow[0]:
            try:
                return json.loads(trow[0]) if isinstance(trow[0], str) else trow[0]
            except json.JSONDecodeError:
                pass
    return {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Control mapping
# ---------------------------------------------------------------------------


def _map_controls(canvas_type: str, graph: dict[str, Any]) -> list[dict]:
    """Return a list of CIS control dicts relevant to the canvas and topology."""
    ct = canvas_type.lower()
    selected: dict[str, dict] = {}

    # Add baseline controls for this canvas type
    for ctrl_id, ctrl in _CIS_CATALOG.items():
        if ct in ctrl["canvases"] or _resolve(ct) in ctrl["canvases"]:
            selected[ctrl_id] = ctrl

    # Add topology-driven hints
    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])
    all_labels = [n.get("label", "") for n in nodes] + [e.get("label", "") for e in edges]
    for label in all_labels:
        for pattern, ctrl_id in _LABEL_CONTROL_HINTS:
            if pattern.search(label) and ctrl_id in _CIS_CATALOG:
                selected[ctrl_id] = _CIS_CATALOG[ctrl_id]

    result: list[dict] = []
    for ctrl_id, ctrl in sorted(selected.items()):
        result.append(
            {
                "control_id": ctrl_id,
                "title": ctrl["title"],
                "description": f"{ctrl['title']} — mapped to {canvas_type.upper()} canvas.",
                "implementation_status": "planned",
                "ig_level": ctrl["ig"],
            }
        )
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_controls(session_id: str, canvas_type: str) -> list[dict]:
    """Return CIS Controls v8 candidates for a TFW session.

    Args:
        session_id:  nc_simulation_sessions.id
        canvas_type: ndc | sdc | eda (or alias)

    Returns:
        list[dict] with keys: control_id, title, description,
        implementation_status, ig_level
    """
    conn = get_connection()
    try:
        graph = _load_graph_json(conn, session_id)
    finally:
        conn.close()
    return _map_controls(canvas_type, graph)


def generate_cis_report(session_id: str, canvas_type: str) -> dict:
    """Full CIS report dict for chat display.

    Returns:
        dict with keys: controls (list), canvas_type, total, markdown (str)
    """
    controls = get_controls(session_id, canvas_type)
    lines = [f"## CIS Controls v8 — {canvas_type.upper()} Mapping\n"]
    for ctrl in controls:
        lines.append(
            f"**{ctrl['control_id']}** [{ctrl['ig_level']}] {ctrl['title']}  \n"
            f"Status: `{ctrl['implementation_status']}`\n"
        )
    return {
        "controls": controls,
        "canvas_type": canvas_type,
        "total": len(controls),
        "markdown": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate CIS Controls v8 mapping for a TFW session.")
    p.add_argument("--session-id", required=True)
    p.add_argument("--canvas-type", required=True)
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    result = generate_cis_report(args.session_id, args.canvas_type)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["markdown"])


if __name__ == "__main__":
    main()
