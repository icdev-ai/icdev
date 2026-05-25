
from tools.logging.icdev_logger import get_logger
# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Threat model importers.

Import threat models from external tools into the Security Design Canvas:
  - OWASP Threat Dragon (v2 JSON format)
  - Microsoft Threat Modeling Tool (.tm7 XML format)

Auto-detects format or accepts an explicit format hint.
No external dependencies beyond defusedxml (with stdlib fallback).
"""

import json
import logging
import uuid
from datetime import datetime, timezone

try:
    from defusedxml import ElementTree as ET

    _USING_DEFUSEDXML = True
except ImportError:
    from xml.etree import ElementTree as ET  # nosec B405 — defusedxml preferred but not required

    _USING_DEFUSEDXML = False

logger = get_logger("icdev.security_canvas.importers")

# ── Threat Dragon type → SDC type mapping ─────────────────────────────────────

_TD_TYPE_MAP = {
    "tm.Process": "asset-server",
    "tm.Store": "asset-database",
    "tm.Actor": "asset-client",
    "tm.Boundary": "boundary-network",
    "tm.Flow": "_edge",  # sentinel — handled as edge, not node
}

# ── TMT GenericTypeId pattern → SDC type mapping ─────────────────────────────

_TMT_PATTERNS = [
    ("Process", "asset-server"),
    ("DataStore", "asset-database"),
    ("ExternalInteractor", "asset-client"),
    ("Boundary", "boundary-network"),
]


def _uid() -> str:
    """Generate a short unique id for canvas objects."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Threat Dragon importer ────────────────────────────────────────────────────


def import_threat_dragon(file_content: str) -> dict:
    """Parse an OWASP Threat Dragon v2 JSON file into SDC graph format.

    Returns a dict with design_name, graph (nodes/edges/boundaries),
    threats_imported count, nodes_imported count, and source_format.
    """
    try:
        td = json.loads(file_content)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"error": f"Invalid JSON: {exc}"}

    design_name = "Imported Design"
    summary = td.get("summary") or {}
    if isinstance(summary, dict) and summary.get("title"):
        design_name = summary["title"]

    nodes = []
    edges = []
    boundaries = []
    threats_imported = 0

    detail = td.get("detail") or {}
    diagrams = detail.get("diagrams") or []

    for diagram in diagrams:
        cells = diagram.get("cells") or []
        for cell in cells:
            cell_type = cell.get("type", "")
            data = cell.get("data") or {}
            label = data.get("name") or cell_type
            pos = cell.get("position") or {}
            x = pos.get("x", 100)
            y = pos.get("y", 100)

            sdc_type = _TD_TYPE_MAP.get(cell_type)
            if not sdc_type:
                continue

            # Collect threats attached to this cell
            cell_threats = cell.get("threats") or []
            threat_list = []
            for t in cell_threats:
                threat_list.append(
                    {
                        "title": t.get("title", "Untitled Threat"),
                        "type": t.get("type", ""),
                        "status": t.get("status", "Open"),
                        "severity": t.get("severity", "Medium"),
                        "description": t.get("description", ""),
                    }
                )
                threats_imported += 1

            if sdc_type == "_edge":
                # Flows become edges
                edges.append(
                    {
                        "id": _uid(),
                        "source": cell.get("source", {}).get("id", ""),
                        "target": cell.get("target", {}).get("id", ""),
                        "label": label,
                        "threats": threat_list,
                    }
                )
            elif sdc_type.startswith("boundary-"):
                boundaries.append(
                    {
                        "id": cell.get("id") or _uid(),
                        "type": sdc_type,
                        "label": label,
                        "x": x,
                        "y": y,
                        "width": cell.get("size", {}).get("width", 300),
                        "height": cell.get("size", {}).get("height", 200),
                    }
                )
            else:
                nodes.append(
                    {
                        "id": cell.get("id") or _uid(),
                        "type": sdc_type,
                        "label": label,
                        "x": x,
                        "y": y,
                        "config": {},
                        "threats": threat_list,
                    }
                )

    return {
        "design_name": design_name,
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "boundaries": boundaries,
        },
        "threats_imported": threats_imported,
        "nodes_imported": len(nodes),
        "source_format": "threat-dragon",
    }


# ── Microsoft TMT importer ───────────────────────────────────────────────────


def import_tmt(file_content: str) -> dict:
    """Parse a Microsoft Threat Modeling Tool .tm7 XML file into SDC graph.

    Returns the same structure as import_threat_dragon().
    """
    try:
        root = ET.fromstring(file_content)  # nosec B314 — defusedxml used when available
    except ET.ParseError as exc:
        return {"error": f"Invalid XML: {exc}"}

    design_name = "Imported TMT Model"
    # Try to get model name from top-level element
    name_el = root.find(".//MetaInformation/Name")
    if name_el is not None and name_el.text:
        design_name = name_el.text

    nodes = []
    edges = []
    boundaries = []

    # ── Parse drawing surfaces ────────────────────────────────────────────
    for surface in root.iter("DrawingSurfaceModel"):
        # Borders → nodes / boundaries
        for border in surface.iter("Border"):
            generic_type = ""
            gt_el = border.find("GenericTypeId")
            if gt_el is not None and gt_el.text:
                generic_type = gt_el.text

            label = ""
            name_el2 = border.find("Name")
            if name_el2 is not None and name_el2.text:
                label = name_el2.text

            # Determine SDC type from GenericTypeId
            sdc_type = None
            for pattern, mapped in _TMT_PATTERNS:
                if pattern in generic_type:
                    sdc_type = mapped
                    break
            if not sdc_type:
                sdc_type = "asset-server"  # default fallback

            # Position
            x, y = 100, 100
            x_el = border.find(".//Left")
            y_el = border.find(".//Top")
            if x_el is not None and x_el.text:
                try:
                    x = int(float(x_el.text))
                except ValueError:
                    pass
            if y_el is not None and y_el.text:
                try:
                    y = int(float(y_el.text))
                except ValueError:
                    pass

            obj_id = _uid()
            id_el = border.find("Guid")
            if id_el is not None and id_el.text:
                obj_id = id_el.text

            if sdc_type.startswith("boundary-"):
                w_el = border.find(".//Width")
                h_el = border.find(".//Height")
                w = 300
                h = 200
                if w_el is not None and w_el.text:
                    try:
                        w = int(float(w_el.text))
                    except ValueError:
                        pass
                if h_el is not None and h_el.text:
                    try:
                        h = int(float(h_el.text))
                    except ValueError:
                        pass
                boundaries.append(
                    {
                        "id": obj_id,
                        "type": sdc_type,
                        "label": label or "Trust Boundary",
                        "x": x,
                        "y": y,
                        "width": w,
                        "height": h,
                    }
                )
            else:
                nodes.append(
                    {
                        "id": obj_id,
                        "type": sdc_type,
                        "label": label or sdc_type,
                        "x": x,
                        "y": y,
                        "config": {},
                        "threats": [],
                    }
                )

        # Lines → edges
        for line in surface.iter("Line"):
            src = ""
            tgt = ""
            src_el = line.find("SourceGuid")
            tgt_el = line.find("TargetGuid")
            if src_el is not None and src_el.text:
                src = src_el.text
            if tgt_el is not None and tgt_el.text:
                tgt = tgt_el.text
            label = ""
            name_el3 = line.find("Name")
            if name_el3 is not None and name_el3.text:
                label = name_el3.text
            edges.append(
                {
                    "id": _uid(),
                    "source": src,
                    "target": tgt,
                    "label": label,
                }
            )

    # ── Parse threat instances ────────────────────────────────────────────
    threats_imported = 0
    for ti in root.iter("KeyValueOfstringThreatModelThreatvm4bBhYR"):
        threat_data = {}
        val = ti.find("Value")
        if val is None:
            continue
        tid_el = val.find("ThreatId")
        title_el = val.find("Title")
        cat_el = val.find("Category")
        state_el = val.find("State")
        prio_el = val.find("Priority")

        threat_data = {
            "id": tid_el.text if tid_el is not None and tid_el.text else _uid(),
            "title": title_el.text if title_el is not None and title_el.text else "Untitled",
            "category": cat_el.text if cat_el is not None and cat_el.text else "",
            "state": state_el.text if state_el is not None and state_el.text else "Open",
            "priority": prio_el.text if prio_el is not None and prio_el.text else "Medium",
        }
        threats_imported += 1

        # Attach to first node if possible (TMT threats are model-level)
        if nodes:
            if "threats" not in nodes[0]:
                nodes[0]["threats"] = []
            nodes[0]["threats"].append(
                {
                    "title": threat_data["title"],
                    "type": threat_data["category"],
                    "status": threat_data["state"],
                    "severity": threat_data["priority"],
                    "description": "",
                }
            )

    return {
        "design_name": design_name,
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "boundaries": boundaries,
        },
        "threats_imported": threats_imported,
        "nodes_imported": len(nodes),
        "source_format": "tmt",
    }


# ── Unified import entry point ────────────────────────────────────────────────


def import_threat_model(file_content: str, format_hint: str = "auto") -> dict:
    """Import a threat model from Threat Dragon or TMT format.

    Args:
        file_content: Raw file content (JSON or XML string).
        format_hint: One of "threat-dragon", "tmt", or "auto" (default).

    Returns:
        Import result dict, or {"error": str} on failure.
    """
    if not file_content or not file_content.strip():
        return {"error": "Empty file content"}

    if format_hint == "threat-dragon":
        return import_threat_dragon(file_content)
    elif format_hint == "tmt":
        return import_tmt(file_content)
    elif format_hint == "auto":
        # Auto-detect: try JSON first (Threat Dragon), fall back to XML (TMT)
        stripped = file_content.strip()
        if stripped.startswith("{"):
            result = import_threat_dragon(file_content)
            if "error" not in result:
                return result
        # Try XML
        result = import_tmt(file_content)
        if "error" not in result:
            return result
        return {"error": "Could not detect format — not valid Threat Dragon JSON or TMT XML"}
    else:
        return {"error": f"Unknown format hint: {format_hint}"}
