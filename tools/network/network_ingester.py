# [CUI // SP-CTI]
"""ICDEV™ Network Infrastructure Intelligence — Unified Diagram Ingestion.

Accepts network diagrams in any supported format (Visio VSDX/VDX, Draw.io,
PDF/image), normalizes to a unified graph model, classifies device types,
and persists to topology DB, device inventory, and knowledge graph.

Usage:
    python tools/network/network_ingester.py --file diagram.vsdx --project-id P1 --json
    python tools/network/network_ingester.py --file network.drawio --name "NYC DC" --json
    python tools/network/network_ingester.py --file scan.pdf --project-id P1 --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = get_logger("icdev.network.network_ingester")

# ── Device type classification ───────────────────────────────────────────────

# Regex patterns mapping labels/types to canonical device types
_DEVICE_TYPE_PATTERNS: list[tuple[str, str]] = [
    # Firewalls (vendor names + ALL naming convention codes: FWLL, FWL, FW)
    (r"(?i)(palo\s*alto|fortinet|fortigate|checkpoint|asa|ftd|firepower|sophos|firewall|nfw|[-_]FWLL?[-_]|[-_]FWL[-_]|[-_]FW[-_]|[-_]FW\d|^FW[-_]|^FWL[-_])", "firewall"),
    # Routers (vendor names + naming convention codes: CORE, RTR, GW)
    (r"(?i)(router|asr|isr|nexus\s*7|cisco\s*[0-9]{4}|juniper\s*(mx|srx)|bgp|ospf|[-_]CORE[-_]|[-_]RTR[-_]|^RTR[-_]|^CORE[-_]|[-_]GW$|^.*-GW$|internet.?gw|gateway)", "router"),
    # Load balancers
    (r"(?i)(load\s*bal|f5|big[-\s]?ip|netscaler|citrix\s*adc|haproxy|nlb|alb|elb|[-_]LB[-_]|[-_]LBR[-_]|^LB[-_])", "load_balancer"),
    # Switches (vendor names + ALL naming convention codes: DIST, ACCS, SWI, SW)
    (r"(?i)(switch|catalyst|nexus\s*[3-9]|arista\s*[0-9]|meraki\s*ms|[-_]DIST[-_]|[-_]ACCS[-_]|[-_]SWI[-_]|[-_]SW[-_]|[-_]SW\d|^DIST[-_]|^ACCS[-_]|^SWI[-_]|^SW[-_])", "switch"),
    # VPN gateways
    (r"(?i)(vpn|ipsec|ssl\s*vpn|anyconnect|wireguard|vpn[-_]gw|[-_]VPNG?[-_]|[-_]VPN[-_]|^VPN[-_])", "vpn_gateway"),
    # Access points / Wireless (WLC, WLAN, AP, WAP)
    (r"(?i)(access\s*point|wifi|wireless|wap|meraki\s*mr|[-_]WLAN[-_]|[-_]WLC[-_]|[-_]AP[-_]|[-_]AP\d|^WLC[-_]|^WLAN[-_]|^AP[-_])", "access_point"),
    # Servers + Security appliances (SRV, SRVR, SIEM, IDS, IPS, NOC)
    (r"(?i)(server|vm|hypervisor|esxi|vcenter|host|dns|dhcp|ntp|syslog|siem|[-_]SRVR?[-_]|[-_]SRV[-_]|[-_]SRV\d|[-_]IDS[-_]|[-_]IPS[-_]|[-_]SIEM[-_]|[-_]NOC[-_]|^SRV[-_]|^SRVR[-_])", "server"),
    # WAN links
    (r"(?i)(wan|mpls|sd[-\s]?wan|dmpvn|internet\s*link|circuit|isp|disa[-_]mpls|[-_]MPLS)", "wan_link"),
    # Cloud services
    (r"(?i)(cloud|aws|azure|gcp|oci|saas|paas|iaas|vpc|vnet|tgw)", "cloud_service"),
]


def _classify_device_type(label: str, raw_type: str = "") -> str:
    """Classify a device label/type string to a canonical device type."""
    combined = f"{label} {raw_type}"
    for pattern, device_type in _DEVICE_TYPE_PATTERNS:
        if re.search(pattern, combined):
            return device_type
    return "unknown"


# ── Graph normalization ──────────────────────────────────────────────────────

_LINK_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)(fiber|smf|mmf|optic)", "fiber"),
    (r"(?i)(vpn|ipsec|tunnel|gre)", "vpn"),
    (r"(?i)(wan|mpls|sd-wan|internet)", "wan"),
    (r"(?i)(wireless|wifi|802\.11)", "wireless"),
    (r"(?i)(eth|gig|10g|100g|copper|cat[56])", "ethernet"),
]


def _classify_link_type(label: str) -> str:
    """Classify a link label to a canonical link type."""
    for pattern, link_type in _LINK_TYPE_PATTERNS:
        if re.search(pattern, label):
            return link_type
    return "ethernet"


def _normalize_graph(raw_graph: dict) -> dict:
    """Normalize a raw graph dict to canonical format with classified types."""
    nodes = []
    for n in raw_graph.get("nodes", []):
        label = n.get("label", n.get("id", "unknown"))
        raw_type = n.get("type", "")
        device_type = _classify_device_type(label, raw_type)
        node = {
            "id": n.get("id", str(uuid.uuid4())[:8]),
            "label": label,
            "type": device_type,
            "x": n.get("x", 0),
            "y": n.get("y", 0),
        }
        if n.get("properties"):
            node["properties"] = n["properties"]
        nodes.append(node)

    edges = []
    for e in raw_graph.get("edges", []):
        label = e.get("label", "")
        edges.append({
            "id": e.get("id", str(uuid.uuid4())[:8]),
            "source": e.get("source", ""),
            "target": e.get("target", ""),
            "label": label,
            "type": _classify_link_type(label),
        })

    return {"nodes": nodes, "edges": edges}


# ── Vision-based extraction (PDF/image) ─────────────────────────────────────

_NETWORK_EXTRACTION_PROMPT = (
    "You are analyzing a network infrastructure diagram. "
    "Extract all network devices and connections. "
    "Respond with EXACTLY this JSON format (no markdown, no extra text):\n"
    '{"devices": [{"name": "string", "type": "router|switch|firewall|'
    'load_balancer|server|access_point|wan_link|vpn_gateway|cloud_service|unknown", '
    '"properties": {}}], '
    '"connections": [{"source": "string (device name)", "target": "string (device name)", '
    '"type": "ethernet|fiber|wan|vpn|wireless|unknown", "label": "string or null", '
    '"bandwidth": "string or null"}], '
    '"confidence": 0.0, "notes": "string"}'
)


def _encode_image(image_path: str) -> tuple[str, str]:
    """Base64-encode an image file. Returns (b64_data, media_type)."""
    import base64

    _MEDIA_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    ext = p.suffix.lower()
    media_type = _MEDIA_TYPES.get(ext)
    if not media_type:
        raise ValueError(f"Unsupported image format: {ext}")
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return b64, media_type


def _extract_from_pdf(file_path: str) -> dict:
    """Extract a topology graph from a PDF.

    Strategy:
    1. **Vector path** (``pdfplumber``): for text-based PDFs exported from
       Visio / drawio / Lucidchart, extract rectangles → nodes and lines
       → edges directly. Zero vision calls, zero OCR, all pages traversed,
       coords preserved. Handles 80%+ of real-world NDC PDFs.
    2. **Vision path**: if the vector path finds no shapes (scanned PDF,
       raster-only export), rasterize every page via ``pypdfium2`` and
       run each through the vision LLM.
    3. **OCR path**: final fallback when vision is unavailable or returns
       empty. Runs on every page, not just page 1.

    Returns a graph dict merged across all pages.
    """
    from tools.network.pdf_import import import_pdf, rasterize_pdf_pages

    # ── 1. Vector path ──
    vector = import_pdf(file_path)
    if vector.get("nodes"):
        logger.info(
            "PDF vector extraction: %d nodes, %d edges across %d page(s)",
            len(vector["nodes"]), len(vector["edges"]), vector.get("_pages", 1),
        )
        return vector

    # ── 2. Vision path (all pages, not just page 1) ──
    merged_nodes: list[dict] = []
    merged_edges: list[dict] = []
    errors: list[str] = list(vector.get("_errors", []))
    dpi = int(os.getenv("ICDEV_PDF_DPI", "200"))
    page_images = rasterize_pdf_pages(file_path, dpi=dpi)

    if page_images:
        for page_idx, png in enumerate(page_images):
            try:
                result = _extract_via_vision(str(png))
            except Exception as e:
                errors.append(f"vision page{page_idx}: {e}")
                continue
            if result.get("error"):
                errors.append(f"vision page{page_idx}: {result['error']}")
                continue
            # Re-ID nodes with page scope and remap edges
            id_map: dict[str, str] = {}
            for n in result.get("nodes", []):
                new_id = f"pdf-vis-p{page_idx}-{n['id']}"
                id_map[n["id"]] = new_id
                n["id"] = new_id
                if page_idx > 0:
                    n["page"] = page_idx
                merged_nodes.append(n)
            for e in result.get("edges", []):
                src = id_map.get(e.get("source", ""))
                dst = id_map.get(e.get("target", ""))
                if src and dst:
                    e["id"] = f"pdf-vis-p{page_idx}-{e['id']}"
                    e["source"] = src
                    e["target"] = dst
                    merged_edges.append(e)
    else:
        # No rasterizer available — let the vision LLM try the raw PDF
        result = _extract_via_vision(file_path)
        if result.get("nodes"):
            merged_nodes.extend(result["nodes"])
            merged_edges.extend(result.get("edges", []))
        elif result.get("error"):
            errors.append(f"vision raw pdf: {result['error']}")

    if merged_nodes:
        out: dict = {"nodes": merged_nodes, "edges": merged_edges,
                     "_pages": len(page_images) or 1}
        if errors:
            out["_errors"] = errors
        return out

    # ── 3. OCR fallback ──
    try:
        from tools.network.ocr_fallback import extract_topology_via_ocr

        ocr_result = extract_topology_via_ocr(file_path)
        if ocr_result.get("nodes"):
            return ocr_result
    except ImportError:
        logger.debug("OCR fallback not available")
    except Exception as e:
        errors.append(f"ocr: {e}")

    return {"nodes": [], "edges": [], "_pages": len(page_images),
            "_errors": errors or ["no extractor produced nodes"]}


def _extract_via_vision(image_path: str) -> dict:
    """Use vision LLM to extract network topology from an image."""
    try:
        b64_data, media_type = _encode_image(image_path)
    except (FileNotFoundError, ValueError) as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        user_content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            },
            {"type": "text", "text": "Extract all network devices and connections from this diagram."},
        ]
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_NETWORK_EXTRACTION_PROMPT,
            max_tokens=4096,
            temperature=0.1,
        )
        response = router.invoke("network_diagram_extraction", request)

        # Parse JSON from response
        content = response.content or ""
        # Try to extract JSON from response (handle markdown fences)
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return {"nodes": [], "edges": [], "error": "No JSON in vision response"}

        data = json.loads(json_match.group())

        # Convert vision format to standard graph format
        nodes = []
        name_to_id: dict[str, str] = {}
        for d in data.get("devices", []):
            nid = str(uuid.uuid4())[:8]
            name = d.get("name", f"device-{nid}")
            name_to_id[name] = nid
            nodes.append({
                "id": nid,
                "label": name,
                "type": d.get("type", "unknown"),
                "x": 0,
                "y": 0,
                "properties": d.get("properties", {}),
            })

        edges = []
        for c in data.get("connections", []):
            src = name_to_id.get(c.get("source", ""), "")
            dst = name_to_id.get(c.get("target", ""), "")
            if src and dst:
                edges.append({
                    "id": str(uuid.uuid4())[:8],
                    "source": src,
                    "target": dst,
                    "label": c.get("label", "") or c.get("bandwidth", "") or "",
                    "type": c.get("type", "ethernet"),
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "confidence": data.get("confidence", 0.0),
            "notes": data.get("notes", ""),
        }
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}


# ── Persistence ──────────────────────────────────────────────────────────────

def _persist_to_topology(graph: dict, name: str, conn) -> str:
    """Write normalized graph to topologies table. Returns topology_id."""
    topology_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json, classification, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (topology_id, name, json.dumps(graph), "CUI // SP-CTI", now, now),
    )
    conn.commit()
    return topology_id


def _persist_to_devices(graph: dict, topology_id: str, conn) -> int:
    """Write device inventory from graph nodes to ni_devices. Returns count."""
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for node in graph.get("nodes", []):
        device_id = str(uuid.uuid4())[:12]
        props = node.get("properties", {})
        conn.execute(
            "INSERT INTO ni_devices (id, topology_id, node_id, label, device_type, "
            "vendor, model, firmware_version, site, properties_json, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                device_id,
                topology_id,
                node["id"],
                node["label"],
                node.get("type", "unknown"),
                props.get("vendor", None),
                props.get("model", None),
                props.get("firmware_version", None),
                props.get("site", None),
                json.dumps(props) if props else "{}",
                now,
                now,
            ),
        )
        count += 1
    conn.commit()
    return count


def _persist_to_knowledge_graph(graph: dict, project_id: str, topology_id: str) -> int:
    """Write graph to knowledge graph (kg_nodes/kg_edges in icdev.db). Returns node count."""
    try:
        from tools.db.storage import get_connection
    except ImportError:
        logger.warning("Cannot persist to knowledge graph — tools.db.storage not available")
        return 0

    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    count = 0

    # Detect backend: Postgres uses %s placeholders, SQLite uses ?
    is_pg = hasattr(conn, "_conn") and type(conn._conn).__module__.startswith("psycopg")
    ph = "%s" if is_pg else "?"
    props_col = "properties" if is_pg else "properties_json"

    try:
        insert_node_sql = (
            f"INSERT{'' if is_pg else ' OR IGNORE'} INTO kg_nodes "
            f"(id, graph_id, entity_type, label, {props_col}, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        if is_pg:
            insert_node_sql += " ON CONFLICT (id) DO NOTHING"

        for node in graph.get("nodes", []):
            kg_node_id = str(uuid.uuid4())
            label = node["label"]
            entity_type = f"network_{node.get('type', 'unknown')}"
            props = json.dumps({"topology_id": topology_id, "node_id": node["id"], **(node.get("properties", {}))})
            conn.execute(insert_node_sql, (kg_node_id, project_id, entity_type, label, props, now))
            count += 1

        # Build node label -> kg_node_id map for edges
        node_id_map: dict[str, str] = {}
        if is_pg:
            lookup_sql = (
                f"SELECT id, {props_col}::json->>'node_id' as node_id "
                f"FROM kg_nodes WHERE graph_id = {ph} AND entity_type LIKE 'network_%%'"
            )
        else:
            lookup_sql = (
                f"SELECT id, json_extract({props_col}, '$.node_id') as node_id "  # pg-ok: SQLite fallback; is_pg branch above uses ::json->>
                f"FROM kg_nodes WHERE graph_id = {ph} AND entity_type LIKE 'network_%'"
            )
        rows = conn.execute(lookup_sql, (project_id,)).fetchall()
        for row in rows:
            nid = row[1] if isinstance(row, tuple) else row.get("node_id")
            kid = row[0] if isinstance(row, tuple) else row.get("id")
            if nid:
                node_id_map[nid] = kid

        insert_edge_sql = (
            f"INSERT{'' if is_pg else ' OR IGNORE'} INTO kg_edges "
            f"(id, graph_id, source_id, target_id, relationship, {props_col}, created_at) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )
        if is_pg:
            insert_edge_sql += " ON CONFLICT (id) DO NOTHING"

        for edge in graph.get("edges", []):
            src_kg = node_id_map.get(edge["source"])
            dst_kg = node_id_map.get(edge["target"])
            if src_kg and dst_kg:
                edge_id = str(uuid.uuid4())
                rel_type = "connects_to"
                props = json.dumps({"link_type": edge.get("type", "ethernet"), "label": edge.get("label", "")})
                conn.execute(insert_edge_sql, (edge_id, project_id, src_kg, dst_kg, rel_type, props, now))

        conn.commit()
    except Exception as e:
        logger.warning("Knowledge graph persistence failed: %s", e)
    finally:
        conn.close()

    return count


# ── Main ingestion pipeline ──────────────────────────────────────────────────

def ingest_diagram(
    file_path: str,
    project_id: str = "default",
    topology_name: str | None = None,
) -> dict[str, Any]:
    """Ingest a network diagram from any supported format.

    Detects format by extension, parses, normalizes device types,
    and persists to topology DB + device inventory + knowledge graph.

    Args:
        file_path: Path to the diagram file.
        project_id: ICDEV project ID for knowledge graph linkage.
        topology_name: Optional name for the topology. Defaults to filename.

    Returns:
        Dict with topology_id, node_count, edge_count, device_count, format, duration_ms.
    """
    start = time.time()
    p = Path(file_path)

    if not p.exists():
        return {"error": f"File not found: {file_path}", "topology_id": None}

    ext = p.suffix.lower()
    name = topology_name or p.stem

    # Supported formats from config
    supported = {".vsdx", ".vdx", ".drawio", ".xml", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
    if ext not in supported:
        return {"error": f"Unsupported format: {ext}. Supported: {', '.join(sorted(supported))}", "topology_id": None}

    # Parse based on format
    raw_graph: dict = {"nodes": [], "edges": []}
    fmt = ext.lstrip(".")
    vision_used = False

    if ext == ".vsdx":
        from tools.network.export_import import import_vsdx
        raw_graph = import_vsdx(file_path)

    elif ext == ".vdx":
        from tools.network.export_import import import_vdx
        with open(file_path, "r", encoding="utf-8") as f:
            raw_graph = import_vdx(f.read())

    elif ext in (".drawio", ".xml"):
        from tools.network.export_import import import_drawio
        with open(file_path, "r", encoding="utf-8") as f:
            raw_graph = import_drawio(f.read())

    elif ext == ".pdf":
        raw_graph = _extract_from_pdf(file_path)
        vision_used = True
        fmt = "pdf_vision"

    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        raw_graph = _extract_via_vision(file_path)
        vision_used = True
        fmt = "image_vision"

        # OCR fallback if vision failed or returned no nodes
        if not raw_graph.get("nodes") or raw_graph.get("error"):
            try:
                from tools.network.ocr_fallback import extract_topology_via_ocr

                ocr_result = extract_topology_via_ocr(file_path)
                if ocr_result.get("nodes"):
                    raw_graph = ocr_result
                    vision_used = False
                    fmt = f"image_ocr_{ocr_result.get('method', 'unknown')}"
            except ImportError:
                logger.debug("OCR fallback not available")

    if raw_graph.get("error"):
        return {
            "error": raw_graph["error"],
            "topology_id": None,
            "format": fmt,
            "duration_ms": int((time.time() - start) * 1000),
        }

    # Normalize
    graph = _normalize_graph(raw_graph)

    if not graph["nodes"]:
        return {
            "error": "No devices found in diagram",
            "topology_id": None,
            "format": fmt,
            "duration_ms": int((time.time() - start) * 1000),
        }

    # Persist
    from tools.network.db.init_db import get_connection

    conn = get_connection()
    try:
        topology_id = _persist_to_topology(graph, name, conn)
        device_count = _persist_to_devices(graph, topology_id, conn)
    finally:
        conn.close()

    # Knowledge graph (best-effort)
    kg_count = _persist_to_knowledge_graph(graph, project_id, topology_id)

    duration_ms = int((time.time() - start) * 1000)

    result = {
        "topology_id": topology_id,
        "topology_name": name,
        "format": fmt,
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "device_count": device_count,
        "kg_nodes_created": kg_count,
        "vision_used": vision_used,
        "ocr_used": not vision_used and fmt.startswith("image_ocr"),
        "confidence": raw_graph.get("confidence"),
        "duration_ms": duration_ms,
    }

    logger.info(
        "Ingested %s: %d nodes, %d edges, %d devices in %dms",
        fmt, result["node_count"], result["edge_count"], device_count, duration_ms,
    )
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Network Diagram Ingester")
    parser.add_argument("--file", required=True, help="Path to diagram file")
    parser.add_argument("--project-id", default="default", help="Project ID")
    parser.add_argument("--name", default=None, help="Topology name (default: filename)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = ingest_diagram(args.file, args.project_id, args.name)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("error"):
            print(f"ERROR: {result['error']}")
            sys.exit(1)
        print(f"Topology:  {result['topology_id']} ({result['topology_name']})")
        print(f"Format:    {result['format']}")
        print(f"Nodes:     {result['node_count']}")
        print(f"Edges:     {result['edge_count']}")
        print(f"Devices:   {result['device_count']}")
        print(f"KG nodes:  {result['kg_nodes_created']}")
        print(f"Duration:  {result['duration_ms']}ms")


if __name__ == "__main__":
    main()
