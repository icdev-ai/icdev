# CUI // SP-CTI
"""NDC Visio Stencil Importer.

Parses vendor stencil packages and stores shapes in the network_canvas DB.

Supported formats:
  vssx      — OpenXML stencil (.vssx) — Juniper, modern Cisco
  vss_zip   — ZIP of .vss files (Cisco legacy); extracts any embedded PNG/SVG
  svg_pack  — ZIP of SVG/PNG files — AWS Architecture Icons, Azure Architecture Icons
  vsdx      — Visio Drawing (.vsdx) — imports masters as shapes
  url       — Download from vendor URL then dispatch to above

URL allowlist: only cisco.com, juniper.net, awsstatic.com, aws.amazon.com,
arch-center.azureedge.net, learn.microsoft.com.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import base64
import io
import json
import logging
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen  # nosec B310

from tools.network.db.init_db import get_connection

logger = get_logger("icdev.network.stencil_importer")

_VISIO_NS = "http://schemas.microsoft.com/office/visio/2012/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

ALLOWED_URL_PREFIXES = (
    "https://www.cisco.com/",
    "https://download.juniper.net/",
    "https://www.juniper.net/",
    "https://d1.awsstatic.com/",
    "https://aws.amazon.com/",
    "https://arch-center.azureedge.net/",
    "https://learn.microsoft.com/",
    "https://github.com/",
    "https://raw.githubusercontent.com/",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_url(url: str) -> None:
    if not any(url.startswith(p) for p in ALLOWED_URL_PREFIXES):
        raise ValueError(f"URL not in vendor allowlist: {url[:120]}")


# ── VSSX / VSDX OpenXML parser ────────────────────────────────────────────────

def _rels_map(zf: zipfile.ZipFile, rels_path: str) -> dict[str, str]:
    """Return {rId: target} from a .rels file."""
    result: dict[str, str] = {}
    if rels_path not in zf.namelist():
        return result
    try:
        root = ET.fromstring(zf.read(rels_path).decode("utf-8", errors="replace"))  # nosec B314
        for rel in root:
            rid = rel.get("Id", "")
            target = rel.get("Target", "")
            if rid:
                result[rid] = target
    except ET.ParseError:
        pass
    return result


def _extract_icon(
    master_xml: str,
    zf: zipfile.ZipFile,
    master_path: str,
    media_map: dict[str, bytes],
) -> tuple[Optional[str], str]:
    """Try three strategies to get a base64 icon from a master shape XML."""
    # Strategy 1: ForeignData embedded base64 in XML
    try:
        root = ET.fromstring(master_xml)  # nosec B314
        for shape in root.iter(f"{{{_VISIO_NS}}}Shape"):
            for section in shape.findall(f"{{{_VISIO_NS}}}Section"):
                if section.get("N") == "ForeignData":
                    for row in section.findall(f"{{{_VISIO_NS}}}Row"):
                        cells = {c.get("N"): c.get("V", "") for c in row.findall(f"{{{_VISIO_NS}}}Cell")}
                        raw = cells.get("ForeignData", "")
                        if raw:
                            try:
                                base64.b64decode(raw, validate=True)
                                ftype = cells.get("ForeignType", "Bitmap")
                                icon_type = "svg" if "SVG" in ftype.upper() else "png"
                                return raw, icon_type
                            except Exception:
                                pass
    except ET.ParseError:
        pass

    # Strategy 2: relationship file → media/
    parent_dir = "/".join(master_path.split("/")[:-1])
    fname = master_path.split("/")[-1]
    rels_path = f"{parent_dir}/_rels/{fname}.rels"
    rels = _rels_map(zf, rels_path)
    for target in rels.values():
        target_name = Path(target).name
        if target_name in media_map:
            ext = Path(target_name).suffix.lower()
            icon_type = "svg" if ext == ".svg" else "png"
            return base64.b64encode(media_map[target_name]).decode(), icon_type

    # Strategy 3: any media file (first match)
    if media_map:
        name, raw = next(iter(media_map.items()))
        ext = Path(name).suffix.lower()
        return base64.b64encode(raw).decode(), "svg" if ext == ".svg" else "png"

    return None, "png"


def parse_vssx(file_bytes: bytes) -> list[dict]:
    """Extract shape dicts from a .vssx / .vsdx OpenXML archive."""
    shapes: list[dict] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        logger.warning("parse_vssx: not a valid ZIP/OPC file")
        return shapes

    with zf:
        all_names = zf.namelist()

        # Build media index (all PNG/SVG in archive)
        media_map: dict[str, bytes] = {}
        for n in all_names:
            ext = Path(n).suffix.lower()
            if ext in (".png", ".svg", ".emf") and not n.endswith("/"):
                media_map[Path(n).name] = zf.read(n)

        # Find masters.xml
        masters_xml_path = next(
            (n for n in all_names if n.endswith("masters/masters.xml")), None
        )
        if not masters_xml_path:
            return shapes

        try:
            masters_root = ET.fromstring(
                zf.read(masters_xml_path).decode("utf-8", errors="replace")
            )  # nosec B314
        except ET.ParseError:
            return shapes

        # Relationship map: rId → master file
        rels_for_masters = _rels_map(
            zf, masters_xml_path.replace("masters.xml", "_rels/masters.xml.rels")
        )

        ns = {"v": _VISIO_NS}
        for master_el in masters_root.findall("v:Master", ns):
            master_id = master_el.get("ID", "")
            name_u = master_el.get("NameU") or master_el.get("Name") or f"Shape-{master_id}"
            name = master_el.get("Name") or name_u

            rel_el = master_el.find("v:Rel", ns)
            rid = rel_el.get(f"{{{_REL_NS}}}id", "") if rel_el is not None else ""
            rel_target = rels_for_masters.get(rid, f"master{master_id}.xml")

            master_dir = "/".join(masters_xml_path.split("/")[:-1])
            master_full = f"{master_dir}/{rel_target.lstrip('/')}"

            icon_data, icon_type = None, "png"
            if master_full in all_names:
                master_xml = zf.read(master_full).decode("utf-8", errors="replace")
                icon_data, icon_type = _extract_icon(master_xml, zf, master_full, media_map)

            shapes.append({
                "name": name,
                "name_u": name_u,
                "category": "",
                "icon_data": icon_data,
                "icon_type": icon_type,
            })

    return shapes


# ── Cisco ZIP parser (.vss binary + any embedded images) ──────────────────────

def parse_cisco_zip(zip_bytes: bytes) -> list[dict]:
    """Extract shapes from a Cisco stencil ZIP.

    Cisco ZIPs contain .vss files (OLE binary, hard to parse without win32com),
    but often also include PNG / SVG icons and / or inner .vssx files.
    We harvest whatever image assets we find.
    """
    shapes: list[dict] = []
    try:
        outer = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return shapes

    with outer:
        for entry in outer.namelist():
            ext = Path(entry).suffix.lower()
            stem = Path(entry).stem

            if ext == ".vssx":
                inner_bytes = outer.read(entry)
                parsed = parse_vssx(inner_bytes)
                for s in parsed:
                    s.setdefault("category", "Cisco")
                shapes.extend(parsed)

            elif ext in (".png", ".svg"):
                raw = outer.read(entry)
                name = stem.replace("-", " ").replace("_", " ").title()
                shapes.append({
                    "name": name,
                    "name_u": stem.lower(),
                    "category": "Cisco",
                    "icon_data": base64.b64encode(raw).decode(),
                    "icon_type": "svg" if ext == ".svg" else "png",
                })

    return shapes


# ── SVG / PNG icon-pack parser (AWS, Azure) ───────────────────────────────────

_AWS_PREFIX_RE = re.compile(r"^(Arch|Res|Cat)[-_]", re.IGNORECASE)
_AZURE_NUM_RE = re.compile(r"^\d+[-_]icon[-_](service[-_])?", re.IGNORECASE)
_SIZE_SUFFIX_RE = re.compile(r"[-_]\d+$")


def _clean_name(stem: str, vendor: str) -> str:
    if vendor == "aws":
        stem = _AWS_PREFIX_RE.sub("", stem)
        stem = _SIZE_SUFFIX_RE.sub("", stem)
        stem = stem.replace("-", " ").replace("_", " ")
    elif vendor == "azure":
        stem = _AZURE_NUM_RE.sub("", stem)
        stem = stem.replace("-", " ").replace("_", " ")
    else:
        stem = stem.replace("-", " ").replace("_", " ")
    return stem.strip().title()


def parse_svg_pack(zip_bytes: bytes, vendor: str = "custom") -> list[dict]:
    """Extract shapes from a ZIP of SVG/PNG files (AWS/Azure icon packs)."""
    shapes: list[dict] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return shapes

    with zf:
        for entry in zf.namelist():
            if entry.endswith("/"):
                continue
            ext = Path(entry).suffix.lower()
            if ext not in (".svg", ".png"):
                continue
            if Path(entry).name.startswith("."):
                continue

            stem = Path(entry).stem
            name = _clean_name(stem, vendor)
            if not name:
                continue

            parts = entry.split("/")
            category = parts[-2].replace("-", " ").replace("_", " ").title() if len(parts) >= 2 else vendor.upper()

            raw = zf.read(entry)
            icon_type = "svg" if ext == ".svg" else "png"
            shapes.append({
                "name": name,
                "name_u": stem.lower(),
                "category": category,
                "icon_data": base64.b64encode(raw).decode(),
                "icon_type": icon_type,
            })

    return shapes


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _detect_and_parse(raw: bytes, vendor: str, filename: str = "") -> tuple[list[dict], str]:
    """Dispatch raw bytes to the correct parser; return (shapes, raw_format)."""
    if raw[:4] != b"PK\x03\x04":
        logger.warning("Stencil file is not a ZIP archive")
        return [], "unknown"

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        inner = [n.lower() for n in zf.namelist()]
        is_opc = "[content_types].xml" in inner
        has_vssx = any(n.endswith(".vssx") for n in inner)
        has_vss = any(n.endswith(".vss") for n in inner)
        has_media = any(n.endswith((".svg", ".png")) for n in inner)

    fname_lower = filename.lower()

    if is_opc:
        # The ZIP itself is a .vssx / .vsdx OpenXML container
        return parse_vssx(raw), "vssx"

    if fname_lower.endswith(".vssx") or (has_vssx and not has_vss):
        return parse_vssx(raw), "vssx"

    if vendor == "cisco" or has_vss:
        return parse_cisco_zip(raw), "vss_zip"

    if vendor in ("aws", "azure") or has_media:
        return parse_svg_pack(raw, vendor), "svg_pack"

    # Generic: try vssx, fall back to svg_pack
    shapes = parse_vssx(raw)
    if shapes:
        return shapes, "vssx"
    return parse_svg_pack(raw, vendor), "svg_pack"


# ── DB persistence ────────────────────────────────────────────────────────────

def save_library(
    vendor: str,
    name: str,
    category: str,
    source_url: str,
    shapes: list[dict],
    raw_format: str = "vssx",
) -> str:
    """Persist stencil library + shapes to network_canvas DB; returns library id."""
    lib_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO nc_stencil_libraries "
            "(id, vendor, name, category, source_url, raw_format, shape_count, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lib_id, vendor, name, category, source_url, raw_format, len(shapes), _now()),
        )
        for shape in shapes:
            conn.execute(
                "INSERT INTO nc_stencil_shapes "
                "(id, library_id, name, name_u, category, icon_data, icon_type, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    lib_id,
                    shape.get("name") or "Unknown",
                    shape.get("name_u") or "",
                    shape.get("category") or category,
                    shape.get("icon_data"),
                    shape.get("icon_type") or "png",
                    json.dumps(shape.get("metadata") or {}),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    logger.info("Saved stencil library '%s' vendor=%s shapes=%d", name, vendor, len(shapes))
    return lib_id


# ── Public API ────────────────────────────────────────────────────────────────

def import_from_url(url: str, vendor: str, lib_name: str, category: str = "") -> dict:
    """Download a stencil from a URL, parse it, and save to DB."""
    _validate_url(url)
    req = Request(url, headers={"User-Agent": "ICDEV-NDC-Stencil/1.0"})  # nosec B310
    with urlopen(req, timeout=60) as resp:  # nosec B310
        raw = resp.read()
    filename = Path(url).name
    shapes, raw_format = _detect_and_parse(raw, vendor, filename)
    lib_id = save_library(vendor, lib_name, category or vendor.upper(), url, shapes, raw_format)
    return {"library_id": lib_id, "shape_count": len(shapes), "format": raw_format}


def import_from_bytes(
    file_bytes: bytes,
    filename: str,
    vendor: str,
    lib_name: str,
    category: str = "",
) -> dict:
    """Parse uploaded stencil bytes and save to DB."""
    shapes, raw_format = _detect_and_parse(file_bytes, vendor, filename)
    lib_id = save_library(vendor, lib_name, category or vendor.upper(), f"upload:{filename}", shapes, raw_format)
    return {"library_id": lib_id, "shape_count": len(shapes), "format": raw_format}
