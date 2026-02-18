# CUI // SP-CTI
#!/usr/bin/env python3
"""ReqIF 1.2 parser for IBM DOORS NG requirement exports.

Parses, validates, imports, exports, and diffs ReqIF XML files against the
ICDEV doors_requirements table.  Uses only Python stdlib xml.etree.ElementTree
(no lxml — air-gapped environment).

CUI // SP-CTI
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Audit logger (optional — graceful fallback for standalone use)
# ---------------------------------------------------------------------------
try:
    from tools.audit.audit_logger import log_event
except ImportError:
    def log_event(**kwargs):  # noqa: D103 — stub
        pass

# ---------------------------------------------------------------------------
# ReqIF XML namespaces
# ---------------------------------------------------------------------------
REQIF_NS = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"
XHTML_NS = "http://www.w3.org/1999/xhtml"

NS = {
    "reqif": REQIF_NS,
    "xhtml": XHTML_NS,
}

# Attribute name → standard field mapping for DOORS NG exports
DOORS_ATTR_MAP = {
    "ReqIF.ForeignID": "doors_id",
    "ReqIF.Text": "description",
    "DOORS_Text": "description",
    "ReqIF.Name": "title",
    "DOORS_Priority": "priority",
    "DOORS_ObjectType": "requirement_type",
}

# Normalise DOORS priority values to ICDEV enum
PRIORITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "mandatory": "critical",
    "desirable": "medium",
    "optional": "low",
}

# Normalise DOORS object type values to ICDEV requirement_type enum
REQ_TYPE_MAP = {
    "functional": "functional",
    "non-functional": "non_functional",
    "non_functional": "non_functional",
    "interface": "interface",
    "design": "design",
    "security": "security",
    "performance": "performance",
    "constraint": "constraint",
    "information": "functional",
    "heading": "functional",
}


# ===================================================================
# Helpers
# ===================================================================

def _tag(ns_prefix: str, local: str) -> str:
    """Build a Clark-notation tag string for ElementTree lookups."""
    return f"{{{NS[ns_prefix]}}}{local}"


def _strip_xhtml(element) -> str:
    """Recursively extract plain text from an XHTML element tree.

    Strips all HTML tags and collapses whitespace, returning a clean string
    suitable for the *description* field.
    """
    if element is None:
        return ""
    raw = ET.tostring(element, encoding="unicode", method="text")
    # Collapse runs of whitespace / newlines into a single space
    text = re.sub(r"\s+", " ", raw).strip()
    return text


def _file_hash(file_path: str) -> str:
    """Return the SHA-256 hex digest for a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_hash(text: str) -> str:
    """Return the SHA-256 hex digest for a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_connection(db_path=None):
    """Return a sqlite3 connection with Row factory."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _new_id() -> str:
    """Generate a DOORS-requirement prefixed UUID."""
    return f"dreq-{uuid.uuid4()}"


def _now() -> str:
    """Current UTC timestamp in ISO-8601."""
    return datetime.now().isoformat()


# ===================================================================
# Validation
# ===================================================================

def validate_reqif(file_path: str) -> dict:
    """Validate that *file_path* is a structurally sound ReqIF 1.2 document.

    Returns::

        {
            "valid": bool,
            "errors": [str, ...],
            "spec_count": int,
            "object_count": int,
        }
    """
    errors: list[str] = []
    spec_count = 0
    object_count = 0

    # --- Can we parse the XML at all? ---
    try:
        tree = ET.parse(file_path)
    except ET.ParseError as exc:
        return {"valid": False, "errors": [f"XML parse error: {exc}"],
                "spec_count": 0, "object_count": 0}
    except FileNotFoundError:
        return {"valid": False, "errors": [f"File not found: {file_path}"],
                "spec_count": 0, "object_count": 0}

    root = tree.getroot()

    # --- Root element must be REQ-IF ---
    expected_root = _tag("reqif", "REQ-IF")
    if root.tag != expected_root:
        errors.append(
            f"Root element is '{root.tag}', expected '{expected_root}'"
        )

    # --- CORE-CONTENT must exist ---
    core = root.find(_tag("reqif", "CORE-CONTENT"))
    if core is None:
        errors.append("Missing CORE-CONTENT element")
        return {"valid": False, "errors": errors,
                "spec_count": 0, "object_count": 0}

    content = core.find(_tag("reqif", "REQ-IF-CONTENT"))
    if content is None:
        errors.append("Missing REQ-IF-CONTENT element")
        return {"valid": False, "errors": errors,
                "spec_count": 0, "object_count": 0}

    # --- DATATYPES ---
    datatypes_el = content.find(_tag("reqif", "DATATYPES"))
    if datatypes_el is None:
        errors.append("Missing DATATYPES element")

    # --- SPEC-TYPES ---
    spec_types_el = content.find(_tag("reqif", "SPEC-TYPES"))
    if spec_types_el is None:
        errors.append("Missing SPEC-TYPES element")

    # --- SPEC-OBJECTS ---
    spec_objects_el = content.find(_tag("reqif", "SPEC-OBJECTS"))
    if spec_objects_el is not None:
        object_count = len(spec_objects_el.findall(_tag("reqif", "SPEC-OBJECT")))
    else:
        errors.append("Missing SPEC-OBJECTS element")

    # --- SPECIFICATIONS ---
    specs_el = content.find(_tag("reqif", "SPECIFICATIONS"))
    if specs_el is not None:
        spec_count = len(specs_el.findall(_tag("reqif", "SPECIFICATION")))
    else:
        errors.append("Missing SPECIFICATIONS element")

    # --- SPEC-RELATIONS (optional but note if missing) ---
    relations_el = content.find(_tag("reqif", "SPEC-RELATIONS"))
    if relations_el is None:
        # Not an error — some exports omit relations
        pass

    valid = len(errors) == 0
    return {
        "valid": valid,
        "errors": errors,
        "spec_count": spec_count,
        "object_count": object_count,
    }


# ===================================================================
# Extraction helpers
# ===================================================================

def _extract_datatypes(root, ns: dict) -> dict:
    """Extract ``DATATYPE-DEFINITION-*`` elements from *DATATYPES*.

    Returns ``{identifier: definition_dict}`` for STRING, ENUMERATION,
    INTEGER, DATE, BOOLEAN, and XHTML datatype definitions.
    """
    datatypes: dict = {}
    content = root.find(_tag("reqif", "CORE-CONTENT")).find(
        _tag("reqif", "REQ-IF-CONTENT")
    )
    datatypes_el = content.find(_tag("reqif", "DATATYPES"))
    if datatypes_el is None:
        return datatypes

    # Map of local tag suffix → friendly type name
    type_tags = {
        "DATATYPE-DEFINITION-STRING": "string",
        "DATATYPE-DEFINITION-XHTML": "xhtml",
        "DATATYPE-DEFINITION-ENUMERATION": "enum",
        "DATATYPE-DEFINITION-INTEGER": "integer",
        "DATATYPE-DEFINITION-BOOLEAN": "boolean",
        "DATATYPE-DEFINITION-DATE": "date",
        "DATATYPE-DEFINITION-REAL": "real",
    }

    for suffix, friendly in type_tags.items():
        for elem in datatypes_el.findall(_tag("reqif", suffix)):
            dt_id = elem.get("IDENTIFIER", "")
            entry = {
                "id": dt_id,
                "long_name": elem.get("LONG-NAME", ""),
                "type": friendly,
            }

            # Capture enum values
            if friendly == "enum":
                values = []
                specified_vals = elem.find(_tag("reqif", "SPECIFIED-VALUES"))
                if specified_vals is not None:
                    for ev in specified_vals.findall(
                        _tag("reqif", "ENUM-VALUE")
                    ):
                        values.append({
                            "id": ev.get("IDENTIFIER", ""),
                            "long_name": ev.get("LONG-NAME", ""),
                        })
                entry["values"] = values

            # Capture min/max for integers
            if friendly == "integer":
                entry["min"] = elem.get("MIN")
                entry["max"] = elem.get("MAX")

            datatypes[dt_id] = entry

    return datatypes


def _extract_spec_types(root, ns: dict) -> dict:
    """Extract ``SPEC-OBJECT-TYPE`` definitions and their attribute defs.

    Returns ``{type_identifier: {name, attributes: {attr_id: attr_def}}}``
    """
    spec_types: dict = {}
    content = root.find(_tag("reqif", "CORE-CONTENT")).find(
        _tag("reqif", "REQ-IF-CONTENT")
    )
    spec_types_el = content.find(_tag("reqif", "SPEC-TYPES"))
    if spec_types_el is None:
        return spec_types

    for sot in spec_types_el.findall(_tag("reqif", "SPEC-OBJECT-TYPE")):
        type_id = sot.get("IDENTIFIER", "")
        type_entry = {
            "id": type_id,
            "long_name": sot.get("LONG-NAME", ""),
            "attributes": {},
        }

        spec_attrs = sot.find(_tag("reqif", "SPEC-ATTRIBUTES"))
        if spec_attrs is not None:
            # Walk all ATTRIBUTE-DEFINITION-* children
            attr_tag_suffixes = [
                "ATTRIBUTE-DEFINITION-STRING",
                "ATTRIBUTE-DEFINITION-XHTML",
                "ATTRIBUTE-DEFINITION-ENUMERATION",
                "ATTRIBUTE-DEFINITION-INTEGER",
                "ATTRIBUTE-DEFINITION-BOOLEAN",
                "ATTRIBUTE-DEFINITION-DATE",
                "ATTRIBUTE-DEFINITION-REAL",
            ]
            for suffix in attr_tag_suffixes:
                for ad in spec_attrs.findall(_tag("reqif", suffix)):
                    attr_id = ad.get("IDENTIFIER", "")
                    attr_long = ad.get("LONG-NAME", "")

                    # Resolve the referenced datatype
                    dt_ref = None
                    type_ref_el = ad.find(_tag("reqif", "TYPE"))
                    if type_ref_el is not None:
                        # Child is e.g. DATATYPE-DEFINITION-STRING-REF
                        for child in type_ref_el:
                            dt_ref = (child.text or "").strip()
                            break

                    type_entry["attributes"][attr_id] = {
                        "id": attr_id,
                        "long_name": attr_long,
                        "datatype_ref": dt_ref,
                        "kind": suffix.replace("ATTRIBUTE-DEFINITION-", "").lower(),
                    }

        spec_types[type_id] = type_entry

    # Also extract SPEC-RELATION-TYPE and SPECIFICATION-TYPE for completeness
    for rel_type_tag in ("SPEC-RELATION-TYPE", "SPECIFICATION-TYPE"):
        for srt in spec_types_el.findall(_tag("reqif", rel_type_tag)):
            rt_id = srt.get("IDENTIFIER", "")
            spec_types[rt_id] = {
                "id": rt_id,
                "long_name": srt.get("LONG-NAME", ""),
                "kind": rel_type_tag,
                "attributes": {},
            }

    return spec_types


def _extract_spec_objects(root, ns: dict, datatypes: dict,
                          spec_types: dict) -> list:
    """Extract all ``SPEC-OBJECT`` elements (individual requirements).

    Each requirement's raw attributes are resolved via *datatypes* and
    *spec_types*, then mapped through ``_map_doors_attributes`` to produce
    normalised field names.
    """
    objects: list[dict] = []
    content = root.find(_tag("reqif", "CORE-CONTENT")).find(
        _tag("reqif", "REQ-IF-CONTENT")
    )
    spec_objects_el = content.find(_tag("reqif", "SPEC-OBJECTS"))
    if spec_objects_el is None:
        return objects

    for so in spec_objects_el.findall(_tag("reqif", "SPEC-OBJECT")):
        obj_id = so.get("IDENTIFIER", "")
        obj_long = so.get("LONG-NAME", "")
        last_change = so.get("LAST-CHANGE", "")

        # Determine the SPEC-OBJECT-TYPE reference
        type_ref = None
        type_el = so.find(_tag("reqif", "TYPE"))
        if type_el is not None:
            ref_child = type_el.find(_tag("reqif", "SPEC-OBJECT-TYPE-REF"))
            if ref_child is not None:
                type_ref = (ref_child.text or "").strip()

        # Collect attribute values
        raw_attrs: dict = {}
        values_el = so.find(_tag("reqif", "VALUES"))
        if values_el is not None:
            raw_attrs = _parse_attribute_values(values_el, ns, datatypes,
                                                spec_types, type_ref)

        obj = {
            "reqif_identifier": obj_id,
            "long_name": obj_long,
            "last_change": last_change,
            "type_ref": type_ref,
            "raw_attributes": raw_attrs,
        }

        # Map to standard DOORS fields
        obj = _map_doors_attributes(obj, datatypes, spec_types)
        objects.append(obj)

    return objects


def _parse_attribute_values(values_el, ns: dict, datatypes: dict,
                            spec_types: dict,
                            type_ref: str | None) -> dict:
    """Parse ``VALUES`` children into ``{long_name: value}``."""
    result: dict = {}

    attr_value_tags = {
        "ATTRIBUTE-VALUE-STRING": "string",
        "ATTRIBUTE-VALUE-XHTML": "xhtml",
        "ATTRIBUTE-VALUE-ENUMERATION": "enum",
        "ATTRIBUTE-VALUE-INTEGER": "integer",
        "ATTRIBUTE-VALUE-BOOLEAN": "boolean",
        "ATTRIBUTE-VALUE-DATE": "date",
        "ATTRIBUTE-VALUE-REAL": "real",
    }

    for tag_suffix, kind in attr_value_tags.items():
        for av in values_el.findall(_tag("reqif", tag_suffix)):

            # Resolve the attribute definition to get its LONG-NAME
            attr_long_name = ""
            def_el = av.find(_tag("reqif", "DEFINITION"))
            if def_el is not None:
                for child in def_el:
                    def_ref = (child.text or "").strip()
                    # Look up in the spec_type's attributes
                    if type_ref and type_ref in spec_types:
                        attr_def = spec_types[type_ref]["attributes"].get(
                            def_ref, {}
                        )
                        attr_long_name = attr_def.get("long_name", def_ref)
                    else:
                        attr_long_name = def_ref
                    break

            # Extract the actual value
            value = ""
            if kind == "string":
                value = av.get("THE-VALUE", "")
            elif kind == "xhtml":
                xhtml_content = av.find(_tag("reqif", "THE-VALUE"))
                if xhtml_content is not None:
                    # May contain nested <xhtml:div> etc.
                    value = _strip_xhtml(xhtml_content)
            elif kind == "enum":
                vals_ref_el = av.find(_tag("reqif", "VALUES"))
                if vals_ref_el is not None:
                    enum_refs = []
                    for eref in vals_ref_el.findall(
                        _tag("reqif", "ENUM-VALUE-REF")
                    ):
                        ref_id = (eref.text or "").strip()
                        # Resolve enum value long name from datatypes
                        resolved = ref_id
                        for dt in datatypes.values():
                            if dt.get("type") == "enum":
                                for ev in dt.get("values", []):
                                    if ev["id"] == ref_id:
                                        resolved = ev["long_name"]
                                        break
                        enum_refs.append(resolved)
                    value = ", ".join(enum_refs) if enum_refs else ""
            elif kind == "integer":
                value = av.get("THE-VALUE", "")
            elif kind == "boolean":
                value = av.get("THE-VALUE", "")
            elif kind == "date":
                value = av.get("THE-VALUE", "")
            elif kind == "real":
                value = av.get("THE-VALUE", "")

            if attr_long_name:
                result[attr_long_name] = value

    return result


def _extract_spec_relations(root, ns: dict) -> list:
    """Extract ``SPEC-RELATION`` elements (parent-child, derives, etc.)."""
    relations: list[dict] = []
    content = root.find(_tag("reqif", "CORE-CONTENT")).find(
        _tag("reqif", "REQ-IF-CONTENT")
    )
    relations_el = content.find(_tag("reqif", "SPEC-RELATIONS"))
    if relations_el is None:
        return relations

    for sr in relations_el.findall(_tag("reqif", "SPEC-RELATION")):
        rel_id = sr.get("IDENTIFIER", "")
        long_name = sr.get("LONG-NAME", "")
        last_change = sr.get("LAST-CHANGE", "")

        # Type reference
        type_ref = None
        type_el = sr.find(_tag("reqif", "TYPE"))
        if type_el is not None:
            ref_child = type_el.find(
                _tag("reqif", "SPEC-RELATION-TYPE-REF")
            )
            if ref_child is not None:
                type_ref = (ref_child.text or "").strip()

        # Source
        source_ref = None
        source_el = sr.find(_tag("reqif", "SOURCE"))
        if source_el is not None:
            ref_child = source_el.find(_tag("reqif", "SPEC-OBJECT-REF"))
            if ref_child is not None:
                source_ref = (ref_child.text or "").strip()

        # Target
        target_ref = None
        target_el = sr.find(_tag("reqif", "TARGET"))
        if target_el is not None:
            ref_child = target_el.find(_tag("reqif", "SPEC-OBJECT-REF"))
            if ref_child is not None:
                target_ref = (ref_child.text or "").strip()

        relations.append({
            "reqif_identifier": rel_id,
            "long_name": long_name,
            "last_change": last_change,
            "type_ref": type_ref,
            "source_ref": source_ref,
            "target_ref": target_ref,
        })

    return relations


def _extract_specifications(root, ns: dict) -> list:
    """Extract ``SPECIFICATION`` elements (requirement modules / documents).

    Each specification may contain a hierarchy of ``SPEC-HIERARCHY`` children
    that order the SPEC-OBJECTS into a tree.
    """
    specifications: list[dict] = []
    content = root.find(_tag("reqif", "CORE-CONTENT")).find(
        _tag("reqif", "REQ-IF-CONTENT")
    )
    specs_el = content.find(_tag("reqif", "SPECIFICATIONS"))
    if specs_el is None:
        return specifications

    for spec in specs_el.findall(_tag("reqif", "SPECIFICATION")):
        spec_id = spec.get("IDENTIFIER", "")
        long_name = spec.get("LONG-NAME", "")
        last_change = spec.get("LAST-CHANGE", "")

        # Type reference
        type_ref = None
        type_el = spec.find(_tag("reqif", "TYPE"))
        if type_el is not None:
            ref_child = type_el.find(
                _tag("reqif", "SPECIFICATION-TYPE-REF")
            )
            if ref_child is not None:
                type_ref = (ref_child.text or "").strip()

        # Collect ordered object refs from SPEC-HIERARCHY tree
        hierarchy_refs = []
        children_el = spec.find(_tag("reqif", "CHILDREN"))
        if children_el is not None:
            hierarchy_refs = _walk_hierarchy(children_el, ns)

        specifications.append({
            "reqif_identifier": spec_id,
            "long_name": long_name,
            "last_change": last_change,
            "type_ref": type_ref,
            "hierarchy": hierarchy_refs,
        })

    return specifications


def _walk_hierarchy(parent_el, ns: dict, depth: int = 0) -> list:
    """Recursively walk ``SPEC-HIERARCHY`` elements, returning a flat list
    with depth annotations."""
    items: list[dict] = []
    for sh in parent_el.findall(_tag("reqif", "SPEC-HIERARCHY")):
        sh_id = sh.get("IDENTIFIER", "")
        obj_ref = None
        obj_el = sh.find(_tag("reqif", "OBJECT"))
        if obj_el is not None:
            ref_child = obj_el.find(_tag("reqif", "SPEC-OBJECT-REF"))
            if ref_child is not None:
                obj_ref = (ref_child.text or "").strip()

        items.append({
            "hierarchy_id": sh_id,
            "object_ref": obj_ref,
            "depth": depth,
        })

        # Recurse into nested CHILDREN
        children_el = sh.find(_tag("reqif", "CHILDREN"))
        if children_el is not None:
            items.extend(_walk_hierarchy(children_el, ns, depth + 1))

    return items


# ===================================================================
# Attribute mapping
# ===================================================================

def _map_doors_attributes(spec_object: dict, datatypes: dict,
                          spec_types: dict) -> dict:
    """Map DOORS-specific ReqIF attributes to standard ICDEV fields.

    Mapping rules::

        ReqIF.ForeignID     → doors_id
        ReqIF.Text / DOORS_Text  → description
        ReqIF.Name          → title
        DOORS_Priority      → priority
        DOORS_ObjectType    → requirement_type

    Falls back to ``long_name`` for title and ``reqif_identifier`` for
    doors_id when the canonical attributes are absent.
    """
    raw = spec_object.get("raw_attributes", {})

    # --- doors_id ---
    doors_id = raw.get("ReqIF.ForeignID", "")
    if not doors_id:
        # Some exports use the identifier directly
        doors_id = spec_object.get("reqif_identifier", "")

    # --- title ---
    title = raw.get("ReqIF.Name", "")
    if not title:
        title = spec_object.get("long_name", "")
    if not title:
        title = doors_id  # last resort

    # --- description ---
    description = raw.get("ReqIF.Text", "")
    if not description:
        description = raw.get("DOORS_Text", "")

    # --- priority ---
    priority_raw = raw.get("DOORS_Priority", "").strip().lower()
    priority = PRIORITY_MAP.get(priority_raw, None)

    # --- requirement_type ---
    req_type_raw = raw.get("DOORS_ObjectType", "").strip().lower()
    requirement_type = REQ_TYPE_MAP.get(req_type_raw, None)

    spec_object["doors_id"] = doors_id
    spec_object["title"] = title
    spec_object["description"] = description
    spec_object["priority"] = priority
    spec_object["requirement_type"] = requirement_type

    return spec_object


# ===================================================================
# Full parse
# ===================================================================

def parse_reqif(file_path: str) -> dict:
    """Parse a ReqIF 1.2 XML file.

    Returns::

        {
            "requirements": [mapped spec-object dicts],
            "relations": [spec-relation dicts],
            "metadata": {header info + specification list},
            "datatypes": {id: datatype def},
        }
    """
    tree = ET.parse(file_path)
    root = tree.getroot()

    # Header metadata
    header_el = root.find(_tag("reqif", "THE-HEADER"))
    header = {}
    if header_el is not None:
        rh = header_el.find(_tag("reqif", "REQ-IF-HEADER"))
        if rh is not None:
            header = {
                "identifier": rh.get("IDENTIFIER", ""),
                "title": rh.get("TITLE", ""),
                "creation_time": rh.get("CREATION-TIME", ""),
                "comment": rh.get("COMMENT", ""),
                "repository_id": rh.get("REPOSITORY-ID", ""),
                "req_if_tool_id": rh.get("REQ-IF-TOOL-ID", ""),
                "req_if_version": rh.get("REQ-IF-VERSION", ""),
                "source_tool_id": rh.get("SOURCE-TOOL-ID", ""),
            }

    datatypes = _extract_datatypes(root, NS)
    spec_types = _extract_spec_types(root, NS)
    requirements = _extract_spec_objects(root, NS, datatypes, spec_types)
    relations = _extract_spec_relations(root, NS)
    specifications = _extract_specifications(root, NS)

    metadata = {
        "header": header,
        "specifications": specifications,
        "spec_types_count": len(spec_types),
        "datatype_count": len(datatypes),
        "file": str(file_path),
    }

    return {
        "requirements": requirements,
        "relations": relations,
        "metadata": metadata,
        "datatypes": datatypes,
    }


# ===================================================================
# Import
# ===================================================================

def import_reqif(project_id: str, file_path: str,
                 db_path: str = None) -> dict:
    """Full import pipeline: parse -> validate -> store -> audit.

    Steps:
        1. Validate ReqIF structure
        2. Compute file SHA-256 hash
        3. Parse all requirements and relations
        4. UPSERT into doors_requirements (on project_id + doors_id)
        5. Record in model_imports table (import_type='reqif')
        6. Log audit trail (reqif_imported)
        7. Return summary

    Returns::

        {
            "import_id": int,
            "requirements_imported": int,
            "relations_imported": int,
            "errors": int,
            "status": str,
        }
    """
    timestamp = _now()
    error_details: list[str] = []

    # 1. Validate
    validation = validate_reqif(file_path)
    if not validation["valid"]:
        return {
            "import_id": None,
            "requirements_imported": 0,
            "relations_imported": 0,
            "errors": len(validation["errors"]),
            "status": "failed",
            "validation_errors": validation["errors"],
        }

    # 2. File hash
    source_hash = _file_hash(file_path)

    # 3. Parse
    parsed = parse_reqif(file_path)
    requirements = parsed["requirements"]
    relations = parsed["relations"]
    specifications = parsed["metadata"].get("specifications", [])

    # Build a module-name lookup: reqif_identifier -> specification long_name
    obj_to_module: dict[str, str] = {}
    for spec in specifications:
        module_name = spec.get("long_name", "")
        for hier_item in spec.get("hierarchy", []):
            obj_ref = hier_item.get("object_ref")
            if obj_ref:
                obj_to_module[obj_ref] = module_name

    # Build a parent lookup from hierarchy depth
    obj_to_parent: dict[str, str | None] = {}
    for spec in specifications:
        parent_stack: list[str | None] = [None]
        prev_depth = 0
        for hier_item in spec.get("hierarchy", []):
            obj_ref = hier_item.get("object_ref")
            depth = hier_item.get("depth", 0)
            if depth > prev_depth:
                # Went deeper — previous object is our parent
                pass  # parent_stack already has it
            elif depth < prev_depth:
                # Pop back
                diff = prev_depth - depth
                for _ in range(diff):
                    if len(parent_stack) > 1:
                        parent_stack.pop()
            elif depth == prev_depth and depth > 0:
                # Sibling — pop the last sibling, keep same parent
                if len(parent_stack) > 1:
                    parent_stack.pop()

            parent_id = parent_stack[-1] if parent_stack else None
            if obj_ref:
                obj_to_parent[obj_ref] = parent_id
                parent_stack.append(obj_ref)
            prev_depth = depth

    # 4. Store in DB
    conn = _get_connection(db_path)
    imported_count = 0
    relation_count = 0

    try:
        cursor = conn.cursor()

        for req in requirements:
            try:
                req_id = _new_id()
                doors_id = req.get("doors_id", "")
                if not doors_id:
                    error_details.append(
                        f"Skipped object '{req.get('reqif_identifier')}': "
                        f"no DOORS ID"
                    )
                    continue

                module_name = obj_to_module.get(
                    req.get("reqif_identifier", ""), ""
                )
                parent_reqif_id = obj_to_parent.get(
                    req.get("reqif_identifier"), None
                )
                # Resolve parent's doors_id
                parent_req_db_id = None
                if parent_reqif_id:
                    # Lookup from already-stored rows
                    cursor.execute(
                        "SELECT id FROM doors_requirements "
                        "WHERE project_id = ? AND doors_id = ?",
                        (project_id, parent_reqif_id),
                    )
                    prow = cursor.fetchone()
                    if prow:
                        parent_req_db_id = prow["id"]

                # UPSERT: insert or update on conflict(project_id, doors_id)
                cursor.execute(
                    """INSERT INTO doors_requirements
                       (id, project_id, doors_id, module_name,
                        requirement_type, title, description, priority,
                        status, parent_req_id, source_file, source_hash,
                        imported_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                       ON CONFLICT(project_id, doors_id) DO UPDATE SET
                           module_name = excluded.module_name,
                           requirement_type = excluded.requirement_type,
                           title = excluded.title,
                           description = excluded.description,
                           priority = excluded.priority,
                           parent_req_id = excluded.parent_req_id,
                           source_file = excluded.source_file,
                           source_hash = excluded.source_hash,
                           updated_at = excluded.updated_at
                    """,
                    (
                        req_id,
                        project_id,
                        doors_id,
                        module_name,
                        req.get("requirement_type"),
                        req.get("title", ""),
                        req.get("description", ""),
                        req.get("priority"),
                        parent_req_db_id,
                        str(file_path),
                        source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                imported_count += 1
            except Exception as exc:
                error_details.append(
                    f"Error importing '{req.get('doors_id', '?')}': {exc}"
                )

        # Store relations (informational — linked via reqif identifiers)
        for rel in relations:
            try:
                # Resolve source and target to doors_requirement IDs
                src_ref = rel.get("source_ref", "")
                tgt_ref = rel.get("target_ref", "")

                # Find doors_id for source / target via the parsed objects
                src_doors = None
                tgt_doors = None
                for req in requirements:
                    if req.get("reqif_identifier") == src_ref:
                        src_doors = req.get("doors_id")
                    if req.get("reqif_identifier") == tgt_ref:
                        tgt_doors = req.get("doors_id")

                if src_doors and tgt_doors:
                    # Store as a digital_thread_link
                    cursor.execute(
                        """INSERT OR IGNORE INTO digital_thread_links
                           (project_id, source_type, source_id,
                            target_type, target_id, link_type,
                            confidence, evidence, created_by, created_at)
                           VALUES (?, 'doors_requirement', ?, 'doors_requirement', ?,
                                   'derives_from', 1.0, ?, 'reqif-parser', ?)
                        """,
                        (
                            project_id,
                            src_doors,
                            tgt_doors,
                            f"ReqIF relation {rel.get('reqif_identifier', '')}",
                            timestamp,
                        ),
                    )
                    relation_count += 1
            except Exception as exc:
                error_details.append(f"Error importing relation: {exc}")

        # 5. Record in model_imports
        status = "completed"
        if error_details and imported_count > 0:
            status = "partial"
        elif error_details and imported_count == 0:
            status = "failed"

        cursor.execute(
            """INSERT INTO model_imports
               (project_id, import_type, source_file, source_hash,
                elements_imported, relationships_imported,
                errors, error_details, status, imported_by, imported_at)
               VALUES (?, 'reqif', ?, ?, ?, ?, ?, ?, ?, 'reqif-parser', ?)
            """,
            (
                project_id,
                str(file_path),
                source_hash,
                imported_count,
                relation_count,
                len(error_details),
                json.dumps(error_details) if error_details else None,
                status,
                timestamp,
            ),
        )
        import_id = cursor.lastrowid

        conn.commit()

        # 6. Audit trail
        try:
            log_event(
                event_type="reqif_imported",
                actor="reqif-parser",
                action=(
                    f"Imported {imported_count} requirements, "
                    f"{relation_count} relations from ReqIF"
                ),
                project_id=project_id,
                details={
                    "import_id": import_id,
                    "file": str(file_path),
                    "source_hash": source_hash,
                    "requirements": imported_count,
                    "relations": relation_count,
                    "errors": len(error_details),
                },
                affected_files=[str(file_path)],
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass  # Audit failure should not block import

    except Exception as exc:
        conn.rollback()
        return {
            "import_id": None,
            "requirements_imported": 0,
            "relations_imported": 0,
            "errors": 1,
            "status": "failed",
            "validation_errors": [str(exc)],
        }
    finally:
        conn.close()

    # 7. Summary
    return {
        "import_id": import_id,
        "requirements_imported": imported_count,
        "relations_imported": relation_count,
        "errors": len(error_details),
        "status": status,
    }


# ===================================================================
# Export
# ===================================================================

def export_reqif(project_id: str, output_path: str,
                 db_path: str = None) -> dict:
    """Generate a ReqIF 1.2 XML file from the current ``doors_requirements``
    DB state for *project_id*.

    Produces a valid round-trip document that DOORS NG can re-import.

    Returns ``{"file_path": str, "requirement_count": int}``.
    """
    conn = _get_connection(db_path)
    try:
        cursor = conn.cursor()

        # Fetch requirements
        cursor.execute(
            "SELECT * FROM doors_requirements WHERE project_id = ? "
            "ORDER BY module_name, doors_id",
            (project_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

        if not rows:
            return {"file_path": None, "requirement_count": 0,
                    "error": "No requirements found for project"}

        # Build XML
        root = ET.Element(
            f"{{{REQIF_NS}}}REQ-IF",
            attrib={"xmlns": REQIF_NS},
        )

        # THE-HEADER
        header_wrap = ET.SubElement(root, f"{{{REQIF_NS}}}THE-HEADER")
        header = ET.SubElement(
            header_wrap, f"{{{REQIF_NS}}}REQ-IF-HEADER",
            attrib={
                "IDENTIFIER": f"header-{uuid.uuid4()}",
                "CREATION-TIME": _now(),
                "REQ-IF-TOOL-ID": "ICDEV-ReqIF-Parser",
                "REQ-IF-VERSION": "1.2",
                "SOURCE-TOOL-ID": "ICDEV",
                "TITLE": f"Export for {project_id}",
            },
        )

        # CORE-CONTENT
        core = ET.SubElement(root, f"{{{REQIF_NS}}}CORE-CONTENT")
        content = ET.SubElement(core, f"{{{REQIF_NS}}}REQ-IF-CONTENT")

        # DATATYPES
        datatypes_el = ET.SubElement(content, f"{{{REQIF_NS}}}DATATYPES")
        string_dt_id = f"dt-string-{uuid.uuid4()}"
        ET.SubElement(
            datatypes_el, f"{{{REQIF_NS}}}DATATYPE-DEFINITION-STRING",
            attrib={
                "IDENTIFIER": string_dt_id,
                "LONG-NAME": "String",
                "MAX-LENGTH": "4096",
            },
        )

        # SPEC-TYPES
        spec_types_el = ET.SubElement(content, f"{{{REQIF_NS}}}SPEC-TYPES")
        sot_id = f"sot-doors-req-{uuid.uuid4()}"
        sot = ET.SubElement(
            spec_types_el, f"{{{REQIF_NS}}}SPEC-OBJECT-TYPE",
            attrib={
                "IDENTIFIER": sot_id,
                "LONG-NAME": "DOORS Requirement",
            },
        )

        # Attribute definitions
        spec_attrs = ET.SubElement(sot, f"{{{REQIF_NS}}}SPEC-ATTRIBUTES")
        attr_defs = {}
        for field_name in ("ReqIF.ForeignID", "ReqIF.Name", "ReqIF.Text",
                           "DOORS_Priority", "DOORS_ObjectType"):
            ad_id = f"ad-{field_name}-{uuid.uuid4()}"
            attr_defs[field_name] = ad_id
            ad = ET.SubElement(
                spec_attrs,
                f"{{{REQIF_NS}}}ATTRIBUTE-DEFINITION-STRING",
                attrib={
                    "IDENTIFIER": ad_id,
                    "LONG-NAME": field_name,
                },
            )
            type_ref = ET.SubElement(ad, f"{{{REQIF_NS}}}TYPE")
            ref = ET.SubElement(
                type_ref,
                f"{{{REQIF_NS}}}DATATYPE-DEFINITION-STRING-REF",
            )
            ref.text = string_dt_id

        # SPEC-OBJECTS
        spec_objects_el = ET.SubElement(
            content, f"{{{REQIF_NS}}}SPEC-OBJECTS"
        )
        obj_ids: dict[str, str] = {}  # doors_id -> reqif object identifier

        for row in rows:
            obj_id = f"obj-{uuid.uuid4()}"
            obj_ids[row["doors_id"]] = obj_id

            so = ET.SubElement(
                spec_objects_el, f"{{{REQIF_NS}}}SPEC-OBJECT",
                attrib={
                    "IDENTIFIER": obj_id,
                    "LONG-NAME": row.get("title") or "",
                    "LAST-CHANGE": row.get("updated_at") or _now(),
                },
            )

            # TYPE ref
            type_el = ET.SubElement(so, f"{{{REQIF_NS}}}TYPE")
            tref = ET.SubElement(
                type_el, f"{{{REQIF_NS}}}SPEC-OBJECT-TYPE-REF"
            )
            tref.text = sot_id

            # VALUES
            vals = ET.SubElement(so, f"{{{REQIF_NS}}}VALUES")

            field_mapping = {
                "ReqIF.ForeignID": row.get("doors_id", ""),
                "ReqIF.Name": row.get("title", ""),
                "ReqIF.Text": row.get("description", ""),
                "DOORS_Priority": row.get("priority", "") or "",
                "DOORS_ObjectType": row.get("requirement_type", "") or "",
            }

            for field_name, value in field_mapping.items():
                av = ET.SubElement(
                    vals,
                    f"{{{REQIF_NS}}}ATTRIBUTE-VALUE-STRING",
                    attrib={"THE-VALUE": str(value)},
                )
                def_el = ET.SubElement(av, f"{{{REQIF_NS}}}DEFINITION")
                dref = ET.SubElement(
                    def_el,
                    f"{{{REQIF_NS}}}ATTRIBUTE-DEFINITION-STRING-REF",
                )
                dref.text = attr_defs[field_name]

        # SPEC-RELATIONS (empty — we store relations in digital_thread_links)
        ET.SubElement(content, f"{{{REQIF_NS}}}SPEC-RELATIONS")

        # SPECIFICATIONS — group by module_name
        specs_el = ET.SubElement(content, f"{{{REQIF_NS}}}SPECIFICATIONS")
        modules: dict[str, list[dict]] = {}
        for row in rows:
            mn = row.get("module_name") or "Default Module"
            modules.setdefault(mn, []).append(row)

        for module_name, mod_rows in modules.items():
            spec = ET.SubElement(
                specs_el, f"{{{REQIF_NS}}}SPECIFICATION",
                attrib={
                    "IDENTIFIER": f"spec-{uuid.uuid4()}",
                    "LONG-NAME": module_name,
                    "LAST-CHANGE": _now(),
                },
            )
            children_el = ET.SubElement(spec, f"{{{REQIF_NS}}}CHILDREN")
            for mod_row in mod_rows:
                sh = ET.SubElement(
                    children_el, f"{{{REQIF_NS}}}SPEC-HIERARCHY",
                    attrib={
                        "IDENTIFIER": f"sh-{uuid.uuid4()}",
                    },
                )
                obj_el = ET.SubElement(sh, f"{{{REQIF_NS}}}OBJECT")
                oref = ET.SubElement(
                    obj_el, f"{{{REQIF_NS}}}SPEC-OBJECT-REF"
                )
                oref.text = obj_ids.get(mod_row["doors_id"], "")

        # Write XML
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        tree_out = ET.ElementTree(root)
        ET.indent(tree_out, space="  ")
        tree_out.write(
            str(out_path),
            xml_declaration=True,
            encoding="UTF-8",
        )

    finally:
        conn.close()

    return {
        "file_path": str(out_path),
        "requirement_count": len(rows),
    }


# ===================================================================
# Diff
# ===================================================================

def diff_reqif(project_id: str, new_file: str,
               db_path: str = None) -> dict:
    """Compare current DB state with a new ReqIF file.

    Compares by ``doors_id``:
      - IDs in file but not DB  -> added
      - IDs in both but content hash differs -> modified
      - IDs in DB but not file  -> deleted
      - IDs in both with same hash -> unchanged

    Returns::

        {
            "added": [{"doors_id": ..., "title": ...}, ...],
            "modified": [{"doors_id": ..., "title": ..., "changes": [...]}, ...],
            "deleted": [{"doors_id": ..., "title": ...}, ...],
            "unchanged": int,
        }
    """
    # Parse new file
    parsed = parse_reqif(new_file)
    new_reqs = parsed["requirements"]

    # Build lookup: doors_id -> {title, description hash}
    new_lookup: dict[str, dict] = {}
    for req in new_reqs:
        did = req.get("doors_id", "")
        if not did:
            continue
        content = f"{req.get('title', '')}|{req.get('description', '')}|{req.get('priority', '')}|{req.get('requirement_type', '')}"
        new_lookup[did] = {
            "doors_id": did,
            "title": req.get("title", ""),
            "description": req.get("description", ""),
            "priority": req.get("priority"),
            "requirement_type": req.get("requirement_type"),
            "content_hash": _content_hash(content),
        }

    # Load current DB state
    conn = _get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM doors_requirements WHERE project_id = ?",
            (project_id,),
        )
        db_rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    db_lookup: dict[str, dict] = {}
    for row in db_rows:
        did = row.get("doors_id", "")
        content = f"{row.get('title', '')}|{row.get('description', '')}|{row.get('priority', '')}|{row.get('requirement_type', '')}"
        db_lookup[did] = {
            "doors_id": did,
            "title": row.get("title", ""),
            "description": row.get("description", ""),
            "priority": row.get("priority"),
            "requirement_type": row.get("requirement_type"),
            "content_hash": _content_hash(content),
        }

    # Compare
    added: list[dict] = []
    modified: list[dict] = []
    deleted: list[dict] = []
    unchanged = 0

    new_ids = set(new_lookup.keys())
    db_ids = set(db_lookup.keys())

    for did in new_ids - db_ids:
        added.append({
            "doors_id": did,
            "title": new_lookup[did]["title"],
        })

    for did in db_ids - new_ids:
        deleted.append({
            "doors_id": did,
            "title": db_lookup[did]["title"],
        })

    for did in new_ids & db_ids:
        if new_lookup[did]["content_hash"] != db_lookup[did]["content_hash"]:
            changes = []
            for field in ("title", "description", "priority",
                          "requirement_type"):
                old_val = db_lookup[did].get(field, "")
                new_val = new_lookup[did].get(field, "")
                if old_val != new_val:
                    changes.append({
                        "field": field,
                        "old": old_val,
                        "new": new_val,
                    })
            modified.append({
                "doors_id": did,
                "title": new_lookup[did]["title"],
                "changes": changes,
            })
        else:
            unchanged += 1

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }


# ===================================================================
# Import summary
# ===================================================================

def get_import_summary(import_id: int, db_path: str = None) -> dict:
    """Return import details from the ``model_imports`` table."""
    conn = _get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM model_imports WHERE id = ?", (import_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"error": f"Import ID {import_id} not found"}
        return dict(row)
    finally:
        conn.close()


# ===================================================================
# CLI
# ===================================================================

def main():
    """Command-line interface for ReqIF import / export / diff / validate."""
    parser = argparse.ArgumentParser(
        description="Import/export DOORS NG requirements via ReqIF 1.2"
    )
    parser.add_argument("--project-id", required=True,
                        help="ICDEV project identifier")
    parser.add_argument("--file",
                        help="ReqIF file to import or diff against")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate the ReqIF file structure")
    parser.add_argument("--export",
                        help="Output path for ReqIF export")
    parser.add_argument("--diff", action="store_true",
                        help="Diff file against DB state")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--db-path", type=Path,
                        help="Override database path")
    parser.add_argument("--import-id", type=int,
                        help="Retrieve summary for a previous import")

    args = parser.parse_args()
    db = str(args.db_path) if args.db_path else None

    # --import-id: retrieve summary
    if args.import_id:
        result = get_import_summary(args.import_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for k, v in result.items():
                print(f"  {k}: {v}")
        return

    # --validate-only
    if args.validate_only:
        if not args.file:
            parser.error("--file is required with --validate-only")
        result = validate_reqif(args.file)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "VALID" if result["valid"] else "INVALID"
            print(f"Validation: {status}")
            print(f"  Specifications: {result['spec_count']}")
            print(f"  Objects: {result['object_count']}")
            if result["errors"]:
                print("  Errors:")
                for err in result["errors"]:
                    print(f"    - {err}")
        sys.exit(0 if result["valid"] else 1)

    # --export
    if args.export:
        result = export_reqif(args.project_id, args.export, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("error"):
                print(f"Export failed: {result['error']}")
                sys.exit(1)
            print(f"Exported {result['requirement_count']} requirements")
            print(f"  File: {result['file_path']}")
        return

    # --diff
    if args.diff:
        if not args.file:
            parser.error("--file is required with --diff")
        result = diff_reqif(args.project_id, args.file, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Diff results for {args.project_id}:")
            print(f"  Added:     {len(result['added'])}")
            print(f"  Modified:  {len(result['modified'])}")
            print(f"  Deleted:   {len(result['deleted'])}")
            print(f"  Unchanged: {result['unchanged']}")
            if result["added"]:
                print("\n  New requirements:")
                for item in result["added"]:
                    print(f"    + [{item['doors_id']}] {item['title']}")
            if result["modified"]:
                print("\n  Modified requirements:")
                for item in result["modified"]:
                    fields = ", ".join(
                        c["field"] for c in item.get("changes", [])
                    )
                    print(
                        f"    ~ [{item['doors_id']}] {item['title']} "
                        f"({fields})"
                    )
            if result["deleted"]:
                print("\n  Deleted requirements:")
                for item in result["deleted"]:
                    print(f"    - [{item['doors_id']}] {item['title']}")
        return

    # Default: import
    if not args.file:
        parser.error("--file is required for import")

    result = import_reqif(args.project_id, args.file, db_path=db)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"ReqIF Import: {result['status'].upper()}")
        print(f"  Import ID:    {result.get('import_id')}")
        print(f"  Requirements: {result['requirements_imported']}")
        print(f"  Relations:    {result['relations_imported']}")
        print(f"  Errors:       {result['errors']}")
        if result.get("validation_errors"):
            print("  Validation errors:")
            for err in result["validation_errors"]:
                print(f"    - {err}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
