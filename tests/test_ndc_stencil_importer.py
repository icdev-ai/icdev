# CUI // SP-CTI
"""Unit tests for the NDC vendor stencil importer (ndc-qa-02).

Covers ``tools/network/stencil_importer.py``:
  - parse_vssx: successful parse of >=1 shape from a minimal OPC (.vssx) archive;
    malformed/corrupt archive -> graceful empty result (no crash); empty archive.
  - parse_svg_pack: AWS/Azure icon-pack ZIP yields named shapes with base64 icons.
  - parse_cisco_zip: harvests PNG/SVG image assets from a Cisco-style ZIP.
  - _detect_and_parse: dispatch + non-ZIP bytes -> ("unknown", []) graceful path.
  - import_from_bytes/save_library: persists library + shapes via the canvas
    get_connection() (monkeypatched to a temp SQLite DB); rows read back.

In-memory fixtures only — Visio stencils are OOXML/OPC ZIPs, so we synthesize the
minimal XML parts with zipfile + io. No fixture files on disk.
"""

from __future__ import annotations

import base64
import io
import zipfile


from tools.network import stencil_importer as si
from tools.network.stencil_importer import (
    parse_cisco_zip,
    parse_svg_pack,
    parse_vssx,
)


# ── Minimal OPC / vssx fixtures ────────────────────────────────────────────────

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    "</Types>"
)

_MASTERS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Masters xmlns="http://schemas.microsoft.com/office/visio/2012/main">'
    '<Master ID="1" Name="Core Router" NameU="CoreRouter"/>'
    '<Master ID="2" Name="Firewall" NameU="Firewall"/>'
    "</Masters>"
)


def _build_vssx(masters_xml: str = _MASTERS_XML, *, opc: bool = True) -> bytes:
    """Build a minimal .vssx OPC archive with a masters/masters.xml part."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if opc:
            zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("visio/masters/masters.xml", masters_xml)
    return buf.getvalue()


def _build_svg_pack() -> bytes:
    """A ZIP of SVG/PNG icons laid out like an AWS Architecture Icons pack."""
    tiny_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
    tiny_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("AWS-Icons/Compute/Arch_Amazon-EC2_48.svg", tiny_svg)
        zf.writestr("AWS-Icons/Networking/Arch_Amazon-VPC_48.png", tiny_png)
        zf.writestr("AWS-Icons/README.txt", b"not an icon")  # ignored
    return buf.getvalue()


def _build_cisco_zip() -> bytes:
    """A Cisco-style ZIP carrying PNG/SVG image assets (no parseable .vss)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cisco_router-2900.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        zf.writestr("cisco_switch-catalyst.svg", b"<svg/>")
    return buf.getvalue()


# ── parse_vssx: success / corrupt / empty ──────────────────────────────────────

def test_parse_vssx_extracts_shapes():
    shapes = parse_vssx(_build_vssx())
    assert len(shapes) == 2
    names = {s["name"] for s in shapes}
    assert "Core Router" in names
    assert "Firewall" in names
    # Every shape carries the required keys the persistence layer reads.
    for s in shapes:
        assert set(s) >= {"name", "name_u", "category", "icon_data", "icon_type"}
        assert s["icon_type"] in ("png", "svg", "emf", "none")


def test_parse_vssx_corrupt_archive_is_graceful():
    # Not a ZIP at all -> BadZipFile is caught, empty list returned (no raise).
    assert parse_vssx(b"this is not a zip archive") == []
    # Truncated ZIP magic followed by garbage.
    assert parse_vssx(b"PK\x03\x04" + b"\xff" * 32) == []


def test_parse_vssx_empty_archive_yields_no_shapes():
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w"):
        pass
    shapes = parse_vssx(empty.getvalue())
    assert shapes == []  # valid ZIP but no masters.xml -> no shapes, no crash


def test_parse_vssx_malformed_masters_xml_is_graceful():
    # Valid ZIP, present masters.xml, but the XML is broken -> ParseError caught.
    bad = _build_vssx('<Masters xmlns="http://schemas.microsoft.com/office/visio/2012/main"><Master')
    assert parse_vssx(bad) == []


# ── parse_svg_pack (AWS/Azure) ─────────────────────────────────────────────────

def test_parse_svg_pack_extracts_named_icons():
    shapes = parse_svg_pack(_build_svg_pack(), vendor="aws")
    # Only the .svg and .png entries count; README.txt is skipped.
    assert len(shapes) == 2
    by_type = {s["icon_type"] for s in shapes}
    assert by_type == {"svg", "png"}
    for s in shapes:
        # AWS 'Arch_' prefix and size suffix are stripped by _clean_name.
        assert "Arch" not in s["name"]
        assert s["icon_data"]
        # icon_data must be valid base64.
        base64.b64decode(s["icon_data"], validate=True)


def test_parse_svg_pack_corrupt_archive_is_graceful():
    assert parse_svg_pack(b"garbage-not-a-zip", vendor="aws") == []


# ── parse_cisco_zip ────────────────────────────────────────────────────────────

def test_parse_cisco_zip_harvests_image_assets():
    shapes = parse_cisco_zip(_build_cisco_zip())
    assert len(shapes) == 2
    assert all(s["category"] == "Cisco" for s in shapes)
    assert {s["icon_type"] for s in shapes} == {"png", "svg"}


def test_parse_cisco_zip_corrupt_archive_is_graceful():
    assert parse_cisco_zip(b"not a zip") == []


# ── _detect_and_parse dispatch ─────────────────────────────────────────────────

def test_detect_and_parse_non_zip_is_unknown():
    shapes, fmt = si._detect_and_parse(b"%PDF-1.4 not a zip", vendor="custom", filename="x.pdf")
    assert shapes == []
    assert fmt == "unknown"


def test_detect_and_parse_opc_routes_to_vssx():
    shapes, fmt = si._detect_and_parse(_build_vssx(), vendor="juniper", filename="lib.vssx")
    assert fmt == "vssx"
    assert len(shapes) == 2


# ── import_from_bytes / save_library persistence ───────────────────────────────

def _seed_stencil_db(tmp_path, monkeypatch):
    """Point stencil_importer.get_connection at a fresh temp SQLite DB with the
    two stencil tables created; return a factory that yields new connections."""
    import sqlite3

    from tools.db.storage import StorageConnection

    db_file = tmp_path / "nc_stencils.db"

    def _make_conn():
        raw = sqlite3.connect(str(db_file))
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    seed = _make_conn()
    seed.execute(
        "CREATE TABLE nc_stencil_libraries ("
        "id TEXT PRIMARY KEY, vendor TEXT, name TEXT, category TEXT, "
        "source_url TEXT, raw_format TEXT, shape_count INTEGER, imported_at TEXT)"
    )
    seed.execute(
        "CREATE TABLE nc_stencil_shapes ("
        "id TEXT PRIMARY KEY, library_id TEXT, name TEXT, name_u TEXT, "
        "category TEXT, icon_data TEXT, icon_type TEXT, metadata_json TEXT)"
    )
    seed.commit()
    seed.close()

    # stencil_importer binds get_connection at import (shim-aware patch on module).
    monkeypatch.setattr(si, "get_connection", _make_conn)
    return _make_conn


def test_import_from_bytes_persists_library_and_shapes(tmp_path, monkeypatch):
    make_conn = _seed_stencil_db(tmp_path, monkeypatch)

    result = si.import_from_bytes(
        _build_vssx(), filename="juniper.vssx", vendor="juniper", lib_name="Juniper Core"
    )
    assert result["shape_count"] == 2
    assert result["format"] == "vssx"

    check = make_conn()
    lib = check.execute(
        "SELECT vendor, name, shape_count, raw_format FROM nc_stencil_libraries WHERE id=%s",
        (result["library_id"],),
    ).fetchone()
    shape_rows = check.execute(
        "SELECT name FROM nc_stencil_shapes WHERE library_id=%s ORDER BY name",
        (result["library_id"],),
    ).fetchall()
    check.close()

    assert lib is not None
    assert lib["vendor"] == "juniper"
    assert lib["shape_count"] == 2
    assert lib["raw_format"] == "vssx"
    assert [r["name"] for r in shape_rows] == ["Core Router", "Firewall"]


def test_import_from_bytes_non_zip_saves_empty_library(tmp_path, monkeypatch):
    make_conn = _seed_stencil_db(tmp_path, monkeypatch)

    # Garbage bytes must not crash — an empty library is persisted.
    result = si.import_from_bytes(
        b"not a stencil", filename="broken.vssx", vendor="custom", lib_name="Broken"
    )
    assert result["shape_count"] == 0
    assert result["format"] == "unknown"

    check = make_conn()
    lib = check.execute(
        "SELECT shape_count FROM nc_stencil_libraries WHERE id=%s",
        (result["library_id"],),
    ).fetchone()
    check.close()
    assert lib is not None
    assert lib["shape_count"] == 0
