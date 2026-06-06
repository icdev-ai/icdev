# [CUI // SP-CTI]
"""Visio VSDX Export — generates modern Visio format with embedded metadata.

Each canvas node becomes a Visio shape with:
- Shape data properties (hostname, IP, location, rack, circuit, etc.)
- Proper positioning matching canvas coordinates
- Connection lines with protocol labels

Export also generates companion CSV files for Ops teams.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile


# ── VSDX Constants ────────────────────────────────────────────────────────────

_CONTENT_TYPES_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/visio/document.xml"
    ContentType="application/vnd.ms-visio.drawing.main+xml"/>
  <Override PartName="/visio/pages/pages.xml"
    ContentType="application/vnd.ms-visio.pages+xml"/>
  <Override PartName="/visio/pages/page1.xml"
    ContentType="application/vnd.ms-visio.page+xml"/>
</Types>"""

_TOP_RELS_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document"
    Target="visio/document.xml"/>
</Relationships>"""

_DOCUMENT_XML_TPL = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VisioDocument xmlns="http://schemas.microsoft.com/office/visio/2012/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <DocumentProperties>
    <Title>{title}</Title>
    <Creator>ICDEV Network Canvas</Creator>
  </DocumentProperties>
</VisioDocument>"""

_VISIO_RELS_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages"
    Target="pages/pages.xml"/>
</Relationships>"""

_PAGES_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <Page ID="0" Name="Page-1">
    <Rel r:id="rId1"/>
  </Page>
</Pages>"""

_PAGES_RELS_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page"
    Target="page1.xml"/>
</Relationships>"""

# Metadata fields to embed as Visio shape properties
_META_FIELDS = [
    "hostname",
    "ip",
    "model",
    "serial",
    "asset_tag",
    "slot",
    "port",
    "port_type",
    "bandwidth",
    "site",
    "location",
    "rack",
    "vlan",
    "vrf",
    "asn",
    "protocol",
    "mtu",
    "peer_asn",
    "peer_ip",
    "peering_type",
    "project_id",
    "circuit_id",
    "customer_id",
    "ipam_block_id",
    "cable_id",
    "xconn_id",
    "notes",
]


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    text = text.replace("'", "&apos;")
    return text


def _build_shape_xml(shape_id: int, node: dict) -> str:
    """Build a Visio <Shape> XML element for a node."""
    config = node.get("config", {})
    label = _xml_escape(node.get("label", node.get("id", "")))
    # Convert px to inches (96 dpi)
    x_in = round(node.get("x", 0) / 96, 3)
    y_in = round(node.get("y", 0) / 96, 3)
    # Invert Y for Visio coordinate system (origin bottom-left)
    pin_y = round(12 - y_in, 3)  # 12-inch page height

    node_type = node.get("type", "")
    is_zone = node_type in ("draw-rect", "zone", "boundary")
    w = round(node.get("width", 200 if is_zone else 120) / 96, 3)
    h = round(node.get("height", 150 if is_zone else 60) / 96, 3)

    # Shape data properties
    prop_rows = []
    for idx, field in enumerate(_META_FIELDS):
        val = _xml_escape(config.get(field, ""))
        prop_rows.append(
            f'        <Row N="{field}" IX="{idx}"><Cell N="Value" V="{val}"/><Cell N="Label" V="{field}"/></Row>'
        )
    props_section = "\n".join(prop_rows)

    fill_color = _xml_escape(config.get("fill_color", "#16213e"))

    return (
        f'  <Shape ID="{shape_id}" NameU="{label}" Type="Shape">\n'
        f'    <Cell N="PinX" V="{x_in + w / 2}"/>\n'
        f'    <Cell N="PinY" V="{pin_y}"/>\n'
        f'    <Cell N="Width" V="{w}"/>\n'
        f'    <Cell N="Height" V="{h}"/>\n'
        f'    <Cell N="FillForegnd" V="{fill_color}"/>\n'
        f'    <Section N="Property">\n{props_section}\n    </Section>\n'
        f"    <Text>{label}</Text>\n"
        f"  </Shape>"
    )


def _build_connector_xml(shape_id: int, edge: dict, node_id_to_shape: dict) -> str:
    """Build a Visio connector <Shape> XML element for an edge."""
    label = _xml_escape(edge.get("label", ""))
    src_shape = node_id_to_shape.get(edge.get("source", ""), 0)
    dst_shape = node_id_to_shape.get(edge.get("target", ""), 0)
    return (
        f'  <Shape ID="{shape_id}" Type="Shape">\n'
        f'    <Cell N="BeginX" V="0"/>\n'
        f'    <Cell N="BeginY" V="0"/>\n'
        f'    <Cell N="EndX" V="1"/>\n'
        f'    <Cell N="EndY" V="1"/>\n'
        f'    <Cell N="ShapeRouteStyle" V="1"/>\n'
        f'    <Cell N="ConFixedCode" V="6"/>\n'
        f'    <Cell N="BegTrigger" V="2" F="_XFTRIGGER(Sheet.{src_shape}!EventXFMod)"/>\n'
        f'    <Cell N="EndTrigger" V="2" F="_XFTRIGGER(Sheet.{dst_shape}!EventXFMod)"/>\n'
        f"    <Text>{label}</Text>\n"
        f"  </Shape>"
    )


def export_vsdx(topology_name: str, graph_json: dict, enrichment: dict | None = None) -> bytes:
    """Export topology to Visio .vsdx format.

    Args:
        topology_name: Name of the topology.
        graph_json: dict with nodes/edges.
        enrichment: optional dict of node_id -> extra metadata to merge.

    Returns:
        bytes: The .vsdx file content (ZIP archive).
    """
    nodes = graph_json.get("nodes", [])
    edges = graph_json.get("edges", [])

    # Merge enrichment data into node configs
    if enrichment:
        for node in nodes:
            nid = node.get("id", "")
            if nid in enrichment:
                cfg = node.setdefault("config", {})
                cfg.update(enrichment[nid])

    # Build page1.xml with all shapes
    shape_parts = []
    node_id_to_shape = {}
    sid = 1

    for node in nodes:
        node_id_to_shape[node.get("id", "")] = sid
        shape_parts.append(_build_shape_xml(sid, node))
        sid += 1

    for edge in edges:
        shape_parts.append(_build_connector_xml(sid, edge, node_id_to_shape))
        sid += 1

    shapes_xml = "\n".join(shape_parts)
    page1_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">\n'
        f"{shapes_xml}\n"
        "</PageContents>"
    )

    safe_title = _xml_escape(topology_name)
    document_xml = _DOCUMENT_XML_TPL.format(title=safe_title)

    # Assemble ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _TOP_RELS_XML)
        zf.writestr("visio/document.xml", document_xml)
        zf.writestr("visio/_rels/document.xml.rels", _VISIO_RELS_XML)
        zf.writestr("visio/pages/pages.xml", _PAGES_XML)
        zf.writestr("visio/pages/_rels/pages.xml.rels", _PAGES_RELS_XML)
        zf.writestr("visio/pages/page1.xml", page1_xml)

    return buf.getvalue()


# ── CSV Ops Exports ───────────────────────────────────────────────────────────


def _csv_string(rows: list[list[str]], header: list[str]) -> str:
    """Write rows with header to CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def export_ops_csvs(topology_name: str, graph_json: dict) -> dict:
    """Export topology as multiple Ops CSV files.

    Args:
        topology_name: Topology name (used in filenames).
        graph_json: dict with nodes/edges.

    Returns:
        dict of filename -> csv_content_string.
    """
    nodes = graph_json.get("nodes", [])
    edges = graph_json.get("edges", [])
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", topology_name)

    # Build node lookup
    node_map = {}
    for n in nodes:
        node_map[n.get("id", "")] = n

    # 1. Device inventory
    dev_header = [
        "hostname",
        "type",
        "model",
        "serial",
        "asset_tag",
        "ip",
        "site",
        "location",
        "rack",
        "vendor",
    ]
    dev_rows = []
    for n in nodes:
        c = n.get("config", {})
        # Skip zone/boundary types
        if n.get("type", "") in ("draw-rect", "zone", "boundary", "text-annotation"):
            continue
        vendor = ""
        model_val = c.get("model", "")
        if model_val:
            parts = model_val.split(" ", 1)
            vendor = parts[0] if len(parts) > 1 else ""
        dev_rows.append(
            [
                c.get("hostname", n.get("label", "")),
                n.get("type", ""),
                model_val,
                c.get("serial", ""),
                c.get("asset_tag", ""),
                c.get("ip", ""),
                c.get("site", ""),
                c.get("location", ""),
                c.get("rack", ""),
                vendor,
            ]
        )

    # 2. Circuit list (derived from edges with protocol info)
    circ_header = [
        "circuit_id",
        "a_end_device",
        "z_end_device",
        "bandwidth",
        "provider",
        "protocol",
        "status",
    ]
    circ_rows = []
    for e in edges:
        src_node = node_map.get(e.get("source", ""), {})
        dst_node = node_map.get(e.get("target", ""), {})
        src_cfg = src_node.get("config", {})
        dst_cfg = dst_node.get("config", {})
        circ_rows.append(
            [
                e.get("label", e.get("id", "")),
                src_cfg.get("hostname", src_node.get("label", "")),
                dst_cfg.get("hostname", dst_node.get("label", "")),
                src_cfg.get("bandwidth", ""),
                "",  # provider — not stored on edges
                src_cfg.get("protocol", ""),
                "active",
            ]
        )

    # 3. Cable schedule
    cable_header = [
        "cable_id",
        "a_port",
        "z_port",
        "cable_type",
        "length",
        "pathway",
    ]
    cable_rows = []
    for e in edges:
        src_node = node_map.get(e.get("source", ""), {})
        dst_node = node_map.get(e.get("target", ""), {})
        src_cfg = src_node.get("config", {})
        dst_cfg = dst_node.get("config", {})
        a_port = src_cfg.get("port", "")
        z_port = dst_cfg.get("port", "")
        cable_type = src_cfg.get("port_type", dst_cfg.get("port_type", ""))
        cable_rows.append(
            [
                e.get("id", ""),
                f"{src_cfg.get('hostname', src_node.get('label', ''))}:{a_port}",
                f"{dst_cfg.get('hostname', dst_node.get('label', ''))}:{z_port}",
                cable_type,
                "",  # length — not stored in config
                "",  # pathway
            ]
        )

    # 4. IP allocation
    ip_header = [
        "ip",
        "subnet",
        "vlan",
        "vrf",
        "assigned_to_device",
        "site",
    ]
    ip_rows = []
    for n in nodes:
        c = n.get("config", {})
        ip_val = c.get("ip", "")
        if not ip_val:
            continue
        # Split IP/subnet if in CIDR notation
        if "/" in ip_val:
            ip_part, subnet_part = ip_val.split("/", 1)
        else:
            ip_part, subnet_part = ip_val, ""
        ip_rows.append(
            [
                ip_part,
                subnet_part,
                c.get("vlan", ""),
                c.get("vrf", ""),
                c.get("hostname", n.get("label", "")),
                c.get("site", ""),
            ]
        )

    # 5. Peering matrix
    peer_header = [
        "local_device",
        "local_asn",
        "peer_asn",
        "peer_ip",
        "peering_type",
        "protocol",
    ]
    peer_rows = []
    for n in nodes:
        c = n.get("config", {})
        if c.get("peer_asn") or c.get("peer_ip"):
            peer_rows.append(
                [
                    c.get("hostname", n.get("label", "")),
                    c.get("asn", ""),
                    c.get("peer_asn", ""),
                    c.get("peer_ip", ""),
                    c.get("peering_type", ""),
                    c.get("protocol", ""),
                ]
            )

    result = {}
    result[f"{safe}_device_inventory.csv"] = _csv_string(dev_rows, dev_header)
    result[f"{safe}_circuit_list.csv"] = _csv_string(circ_rows, circ_header)
    result[f"{safe}_cable_schedule.csv"] = _csv_string(cable_rows, cable_header)
    result[f"{safe}_ip_allocation.csv"] = _csv_string(ip_rows, ip_header)
    result[f"{safe}_peering_matrix.csv"] = _csv_string(peer_rows, peer_header)

    return result
