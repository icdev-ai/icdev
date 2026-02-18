# CUI // SP-CTI
#!/usr/bin/env python3
"""Bidirectional synchronization engine between SysML models (Cameo XMI) and code.

Since Cameo Systems Modeler is a standalone desktop application with no API in
an air-gapped environment, all synchronization is file-based.  Drift detection
uses SHA-256 hash comparison on both model elements (stored in sysml_elements)
and code files (tracked in model_code_mappings).

Supported workflows:
    - detect-drift:       Compare stored hashes with current file state
    - sync-model-to-code: Push model changes into generated code
    - sync-code-to-model: Parse Python AST and generate XMI fragment for Cameo import
    - resolve-conflict:   Resolve model/code conflicts (keep_model, keep_code, merge)
    - reimport-xmi:       Re-import updated XMI after Cameo edits
    - reimport-reqif:     Re-import updated ReqIF after DOORS edits
    - report:             Generate CUI-marked drift/sync report

CLI usage:
    python tools/mbse/sync_engine.py --project-id proj-123 detect-drift
    python tools/mbse/sync_engine.py --project-id proj-123 sync-model-to-code --language python
    python tools/mbse/sync_engine.py --project-id proj-123 sync-code-to-model --output /tmp/updates.xmi
    python tools/mbse/sync_engine.py --project-id proj-123 resolve-conflict --mapping-id 7 --resolution keep_model
    python tools/mbse/sync_engine.py --project-id proj-123 reimport-xmi --file model_v2.xmi
    python tools/mbse/sync_engine.py --project-id proj-123 reimport-reqif --file reqs_v2.reqif
    python tools/mbse/sync_engine.py --project-id proj-123 report --json

CUI // SP-CTI
"""

import argparse
import ast
import hashlib
import json
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Audit logger — graceful fallback for standalone execution
# ---------------------------------------------------------------------------
try:
    from tools.audit.audit_logger import log_event  # type: ignore
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs) -> int:  # noqa: D103 – stub
        return -1

# ---------------------------------------------------------------------------
# XMI namespace constants (match xmi_parser.py)
# ---------------------------------------------------------------------------
XMI_NS = "http://www.omg.org/spec/XMI/20131001"
UML_NS = "http://www.omg.org/spec/UML/20131001"
SYSML_NS = "http://www.omg.org/spec/SysML/20181001"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    """Current ISO-8601 timestamp."""
    return datetime.now().isoformat()


def _new_id(prefix: str = "sysml") -> str:
    """Generate a prefixed UUID."""
    return f"{prefix}-{uuid.uuid4()}"


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection to the ICDEV database."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file.

    Reads in 64 KiB chunks to handle large files without excessive memory use.
    Returns empty string if the file does not exist.
    """
    fpath = Path(file_path)
    if not fpath.exists():
        return ""
    h = hashlib.sha256()
    with open(fpath, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_hash(text: str) -> str:
    """Return SHA-256 hex digest of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Python AST parsing (code -> model direction)
# ---------------------------------------------------------------------------

def _parse_python_ast(file_path: str) -> dict:
    """Parse a Python file using the ast module.

    Extracts top-level classes (with bases, methods, attributes) and
    top-level functions (with arguments and docstrings).

    Returns::

        {
            "classes": [
                {
                    "name": str,
                    "bases": [str, ...],
                    "methods": [{"name": str, "args": [str], "docstring": str}, ...],
                    "attributes": [str, ...]
                },
                ...
            ],
            "functions": [
                {"name": str, "args": [str, ...], "docstring": str},
                ...
            ],
        }
    """
    fpath = Path(file_path)
    if not fpath.exists():
        return {"classes": [], "functions": []}

    source = fpath.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(fpath))
    except SyntaxError:
        return {"classes": [], "functions": []}

    classes: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(ast.dump(base))
                else:
                    bases.append(ast.dump(base))

            methods: List[Dict[str, Any]] = []
            attributes: List[str] = []

            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                    method_args = []
                    for arg in item.args.args:
                        if arg.arg != "self":
                            method_args.append(arg.arg)
                    method_doc = ast.get_docstring(item) or ""
                    methods.append({
                        "name": item.name,
                        "args": method_args,
                        "docstring": method_doc,
                    })
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            attributes.append(target.id)
                elif isinstance(item, ast.AnnAssign):
                    if isinstance(item.target, ast.Name):
                        attributes.append(item.target.id)

            classes.append({
                "name": node.name,
                "bases": bases,
                "methods": methods,
                "attributes": attributes,
            })

        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            func_args = [arg.arg for arg in node.args.args]
            func_doc = ast.get_docstring(node) or ""
            functions.append({
                "name": node.name,
                "args": func_args,
                "docstring": func_doc,
            })

    return {"classes": classes, "functions": functions}


# ---------------------------------------------------------------------------
# XMI fragment generation (code -> model direction)
# ---------------------------------------------------------------------------

def _generate_xmi_fragment(elements: list) -> str:
    """Generate an XMI 2.5 fragment for import into Cameo Systems Modeler.

    Each element dict must contain at minimum:
        - name (str)
        - element_type (str): 'class', 'function', or 'interface'
        - properties (dict, optional): methods, attributes, args, etc.

    Returns well-formed XML string.
    """
    root = ET.Element("xmi:XMI")
    root.set("xmlns:xmi", XMI_NS)
    root.set("xmlns:uml", UML_NS)
    root.set("xmlns:sysml", SYSML_NS)
    root.set("xmi:version", "2.5")

    model = ET.SubElement(root, "uml:Model")
    model.set("xmi:id", f"model-{uuid.uuid4()}")
    model.set("name", "CodeSyncImport")

    pkg = ET.SubElement(model, "packagedElement")
    pkg.set("xmi:type", "uml:Package")
    pkg.set("xmi:id", f"pkg-{uuid.uuid4()}")
    pkg.set("name", "SyncedFromCode")

    for elem in elements:
        etype = elem.get("element_type", "class")
        name = elem.get("name", "Unknown")
        props = elem.get("properties", {})
        xmi_id = elem.get("xmi_id", f"elem-{uuid.uuid4()}")

        if etype in ("class", "block"):
            pe = ET.SubElement(pkg, "packagedElement")
            pe.set("xmi:type", "uml:Class")
            pe.set("xmi:id", xmi_id)
            pe.set("name", name)

            # Add attributes as ownedAttribute
            for attr_name in props.get("attributes", []):
                oa = ET.SubElement(pe, "ownedAttribute")
                oa.set("xmi:type", "uml:Property")
                oa.set("xmi:id", f"attr-{uuid.uuid4()}")
                oa.set("name", attr_name)
                oa.set("visibility", "public")

            # Add methods as ownedOperation
            for method in props.get("methods", []):
                op = ET.SubElement(pe, "ownedOperation")
                op.set("xmi:type", "uml:Operation")
                op.set("xmi:id", f"op-{uuid.uuid4()}")
                op.set("name", method.get("name", ""))
                op.set("visibility", "public")

                # Parameters
                for arg_name in method.get("args", []):
                    param = ET.SubElement(op, "ownedParameter")
                    param.set("xmi:id", f"param-{uuid.uuid4()}")
                    param.set("name", arg_name)
                    param.set("direction", "in")

                # Docstring as ownedComment
                docstring = method.get("docstring", "")
                if docstring:
                    comment = ET.SubElement(op, "ownedComment")
                    comment.set("xmi:id", f"cmt-{uuid.uuid4()}")
                    body = ET.SubElement(comment, "body")
                    body.text = docstring

            # Bases as generalization
            for base_name in props.get("bases", []):
                gen = ET.SubElement(pe, "generalization")
                gen.set("xmi:type", "uml:Generalization")
                gen.set("xmi:id", f"gen-{uuid.uuid4()}")
                gen.set("general", base_name)

        elif etype == "function":
            # Top-level function as a stereotyped class with a single operation
            pe = ET.SubElement(pkg, "packagedElement")
            pe.set("xmi:type", "uml:Class")
            pe.set("xmi:id", xmi_id)
            pe.set("name", name)

            op = ET.SubElement(pe, "ownedOperation")
            op.set("xmi:type", "uml:Operation")
            op.set("xmi:id", f"op-{uuid.uuid4()}")
            op.set("name", name)
            op.set("visibility", "public")

            for arg_name in props.get("args", []):
                param = ET.SubElement(op, "ownedParameter")
                param.set("xmi:id", f"param-{uuid.uuid4()}")
                param.set("name", arg_name)
                param.set("direction", "in")

            docstring = props.get("docstring", "")
            if docstring:
                comment = ET.SubElement(pe, "ownedComment")
                comment.set("xmi:id", f"cmt-{uuid.uuid4()}")
                body_el = ET.SubElement(comment, "body")
                body_el.text = docstring

        elif etype == "interface":
            pe = ET.SubElement(pkg, "packagedElement")
            pe.set("xmi:type", "uml:Interface")
            pe.set("xmi:id", xmi_id)
            pe.set("name", name)

            for method in props.get("methods", []):
                op = ET.SubElement(pe, "ownedOperation")
                op.set("xmi:type", "uml:Operation")
                op.set("xmi:id", f"op-{uuid.uuid4()}")
                op.set("name", method.get("name", ""))

    ET.indent(ET.ElementTree(root), space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def detect_drift(project_id: str, db_path: Optional[Path] = None) -> dict:
    """Compare current model_code_mappings hashes with actual file hashes.

    For each mapping:
        1. Recompute SHA-256 of the code file (if it exists)
        2. Compare with stored code_hash
        3. Check if model's source_hash has changed (re-read sysml_elements.source_hash)
        4. Determine status: synced, model_ahead, code_ahead, conflict, unknown

    Updates model_code_mappings.sync_status in place.

    Returns::

        {
            "total_mappings": int,
            "synced": int,
            "model_ahead": int,
            "code_ahead": int,
            "conflict": int,
            "unknown": int,
            "missing_files": int,
            "details": [...]
        }
    """
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """SELECT mcm.id, mcm.sysml_element_id, mcm.code_path, mcm.code_type,
                  mcm.model_hash, mcm.code_hash, mcm.sync_status,
                  se.source_hash AS current_model_hash, se.name AS element_name
           FROM model_code_mappings mcm
           LEFT JOIN sysml_elements se ON mcm.sysml_element_id = se.id
           WHERE mcm.project_id = ?""",
        (project_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]

    counts = {
        "total_mappings": len(rows),
        "synced": 0,
        "model_ahead": 0,
        "code_ahead": 0,
        "conflict": 0,
        "unknown": 0,
        "missing_files": 0,
    }
    details: List[Dict[str, Any]] = []

    for row in rows:
        mapping_id = row["id"]
        code_path = row["code_path"]
        stored_code_hash = row["code_hash"] or ""
        stored_model_hash = row["model_hash"] or ""
        current_model_hash = row["current_model_hash"] or ""
        element_name = row["element_name"] or ""

        # Recompute code file hash
        current_code_hash = _compute_file_hash(code_path)
        file_exists = current_code_hash != ""

        if not file_exists:
            new_status = "unknown"
            counts["missing_files"] += 1
            counts["unknown"] += 1
        else:
            code_changed = current_code_hash != stored_code_hash
            model_changed = current_model_hash != stored_model_hash

            if code_changed and model_changed:
                new_status = "conflict"
                counts["conflict"] += 1
            elif model_changed and not code_changed:
                new_status = "model_ahead"
                counts["model_ahead"] += 1
            elif code_changed and not model_changed:
                new_status = "code_ahead"
                counts["code_ahead"] += 1
            else:
                new_status = "synced"
                counts["synced"] += 1

        # Update sync_status in DB
        cursor.execute(
            """UPDATE model_code_mappings
               SET sync_status = ?, last_synced = ?
               WHERE id = ?""",
            (new_status, _ts(), mapping_id),
        )

        details.append({
            "mapping_id": mapping_id,
            "element_name": element_name,
            "code_path": code_path,
            "code_type": row["code_type"],
            "previous_status": row["sync_status"],
            "new_status": new_status,
            "file_exists": file_exists,
            "code_changed": current_code_hash != stored_code_hash if file_exists else None,
            "model_changed": current_model_hash != stored_model_hash,
        })

    conn.commit()
    conn.close()

    counts["details"] = details
    return counts


# ---------------------------------------------------------------------------
# Sync model -> code
# ---------------------------------------------------------------------------

def sync_model_to_code(project_id: str, language: str = "python",
                       db_path: Optional[Path] = None) -> dict:
    """Sync model changes to code.

    Steps:
        1. Re-import latest XMI if a new file is available in model_imports
        2. For each model_ahead mapping: regenerate code for that element
        3. For new elements (in model but no mapping): generate new code files
        4. For deleted elements (mapping exists but element gone): mark as orphaned
        5. Update model_code_mappings hashes and status
        6. Log audit trail

    Returns::

        {
            "files_updated": int,
            "files_created": int,
            "files_orphaned": int,
            "errors": int,
            "error_details": [...]
        }
    """
    conn = _get_connection(db_path)
    cursor = conn.cursor()
    errors: List[str] = []
    files_updated = 0
    files_created = 0
    files_orphaned = 0

    # Step 1: Check for latest XMI import and re-import if newer file exists
    # (Delegated to reimport_xmi if caller has a new file; here we just check
    # the most recent import hash to detect if anything changed.)

    # Step 2: For each model_ahead mapping, regenerate code
    cursor.execute(
        """SELECT mcm.id, mcm.sysml_element_id, mcm.code_path, mcm.code_type,
                  se.name, se.element_type, se.properties, se.description,
                  se.source_hash, se.stereotype
           FROM model_code_mappings mcm
           JOIN sysml_elements se ON mcm.sysml_element_id = se.id
           WHERE mcm.project_id = ? AND mcm.sync_status = 'model_ahead'""",
        (project_id,),
    )
    model_ahead_rows = [dict(r) for r in cursor.fetchall()]

    for row in model_ahead_rows:
        try:
            code_content = _generate_code_from_element(
                name=row["name"],
                element_type=row["element_type"],
                properties=row["properties"],
                description=row["description"] or "",
                stereotype=row["stereotype"] or "",
                language=language,
            )
            code_path = Path(row["code_path"])
            code_path.parent.mkdir(parents=True, exist_ok=True)
            code_path.write_text(code_content, encoding="utf-8")

            new_code_hash = _compute_file_hash(str(code_path))
            cursor.execute(
                """UPDATE model_code_mappings
                   SET sync_status = 'synced', code_hash = ?, model_hash = ?,
                       last_synced = ?
                   WHERE id = ?""",
                (new_code_hash, row["source_hash"], _ts(), row["id"]),
            )
            files_updated += 1
        except Exception as exc:
            errors.append(f"Failed to update {row['code_path']}: {exc}")

    # Step 3: New elements with no mapping — generate new code files
    cursor.execute(
        """SELECT se.id, se.name, se.element_type, se.properties,
                  se.description, se.source_hash, se.stereotype
           FROM sysml_elements se
           WHERE se.project_id = ?
             AND se.id NOT IN (
                 SELECT sysml_element_id FROM model_code_mappings
                 WHERE project_id = ?
             )
             AND se.element_type IN ('block', 'interface_block', 'activity')""",
        (project_id, project_id),
    )
    new_elements = [dict(r) for r in cursor.fetchall()]

    for elem in new_elements:
        try:
            safe_name = elem["name"].lower().replace(" ", "_").replace("-", "_")
            if language == "python":
                code_path = BASE_DIR / "output" / project_id / f"{safe_name}.py"
            else:
                code_path = BASE_DIR / "output" / project_id / f"{safe_name}.{language}"

            code_content = _generate_code_from_element(
                name=elem["name"],
                element_type=elem["element_type"],
                properties=elem["properties"],
                description=elem["description"] or "",
                stereotype=elem["stereotype"] or "",
                language=language,
            )
            code_path.parent.mkdir(parents=True, exist_ok=True)
            code_path.write_text(code_content, encoding="utf-8")

            new_code_hash = _compute_file_hash(str(code_path))
            cursor.execute(
                """INSERT INTO model_code_mappings
                   (project_id, sysml_element_id, code_path, code_type,
                    mapping_direction, sync_status, model_hash, code_hash,
                    last_synced)
                   VALUES (?, ?, ?, ?, 'model_to_code', 'synced', ?, ?, ?)""",
                (
                    project_id,
                    elem["id"],
                    str(code_path),
                    "class" if elem["element_type"] in ("block", "interface_block") else "module",
                    elem["source_hash"],
                    new_code_hash,
                    _ts(),
                ),
            )
            files_created += 1
        except Exception as exc:
            errors.append(f"Failed to create code for '{elem['name']}': {exc}")

    # Step 4: Orphaned mappings — element gone from sysml_elements
    cursor.execute(
        """SELECT mcm.id, mcm.code_path
           FROM model_code_mappings mcm
           WHERE mcm.project_id = ?
             AND mcm.sysml_element_id NOT IN (
                 SELECT id FROM sysml_elements WHERE project_id = ?
             )
             AND mcm.sync_status != 'unknown'""",
        (project_id, project_id),
    )
    orphaned_rows = [dict(r) for r in cursor.fetchall()]

    for row in orphaned_rows:
        cursor.execute(
            """UPDATE model_code_mappings
               SET sync_status = 'unknown', last_synced = ?
               WHERE id = ?""",
            (_ts(), row["id"]),
        )
        files_orphaned += 1

    conn.commit()
    conn.close()

    # Step 6: Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="code_generated",
                actor="icdev-sync-engine",
                action=(
                    f"Model-to-code sync for project {project_id}: "
                    f"{files_updated} updated, {files_created} created, "
                    f"{files_orphaned} orphaned"
                ),
                project_id=project_id,
                details={
                    "files_updated": files_updated,
                    "files_created": files_created,
                    "files_orphaned": files_orphaned,
                    "errors": len(errors),
                    "language": language,
                },
                classification="CUI",
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass

    return {
        "files_updated": files_updated,
        "files_created": files_created,
        "files_orphaned": files_orphaned,
        "errors": len(errors),
        "error_details": errors,
    }


def _generate_code_from_element(name: str, element_type: str, properties: str,
                                description: str, stereotype: str,
                                language: str) -> str:
    """Generate source code from a SysML element definition.

    Currently supports Python only.  Produces a module with CUI markings,
    a class stub with attributes and method stubs extracted from the element's
    properties JSON.
    """
    props = {}
    if properties:
        try:
            props = json.loads(properties) if isinstance(properties, str) else properties
        except (json.JSONDecodeError, TypeError):
            props = {}

    if language != "python":
        # Fallback: produce a commented stub
        lines = [
            "// CUI // SP-CTI",
            f"// Auto-generated from SysML element: {name}",
            f"// Element type: {element_type}",
            f"// Stereotype: {stereotype}",
            f"// Description: {description}",
            "// CUI // SP-CTI",
        ]
        return "\n".join(lines)

    lines = [
        "# CUI // SP-CTI",
        f'"""Auto-generated from SysML {element_type}: {name}.',
        "",
    ]
    if description:
        lines.append(f"{description}")
        lines.append("")
    lines.append(f'Stereotype: {stereotype or "N/A"}')
    lines.append('"""')
    lines.append("")

    # Class name: PascalCase from the element name
    class_name = "".join(word.capitalize() for word in name.replace("-", " ").replace("_", " ").split())

    if element_type in ("block", "interface_block"):
        # Extract attributes from properties
        attributes = props.get("attributes", [])
        ports = props.get("ports", [])
        flow_props = props.get("flow_properties", [])

        lines.append(f"class {class_name}:")
        lines.append(f'    """{description or name}"""')
        lines.append("")

        # __init__ with attributes
        init_attrs = attributes + flow_props
        if init_attrs:
            init_args = ", ".join(
                a.get("name", "unnamed") for a in init_attrs
                if a.get("name")
            )
            lines.append(f"    def __init__(self, {init_args}):")
            for attr in init_attrs:
                attr_name = attr.get("name", "")
                if attr_name:
                    lines.append(f"        self.{attr_name} = {attr_name}")
            lines.append("")
        else:
            lines.append("    def __init__(self):")
            lines.append("        pass")
            lines.append("")

        # Port properties as methods
        for port in ports:
            port_name = port.get("name", "port")
            lines.append(f"    def get_{port_name}(self):")
            lines.append(f'        """Access port: {port_name}."""')
            lines.append("        raise NotImplementedError")
            lines.append("")

    elif element_type == "activity":
        # Activity -> module with functions for each action
        actions = props.get("actions", [])
        if actions:
            for action in actions:
                action_name = action.get("name", "").lower().replace(" ", "_").replace("-", "_")
                if not action_name:
                    continue
                lines.append(f"def {action_name}():")
                lines.append(f'    """{action.get("name", "")} action."""')
                lines.append("    raise NotImplementedError")
                lines.append("")
        else:
            lines.append(f"def execute_{class_name.lower()}():")
            lines.append(f'    """{description or name}"""')
            lines.append("    raise NotImplementedError")
            lines.append("")

    else:
        # Generic stub
        lines.append(f"class {class_name}:")
        lines.append(f'    """{description or name}"""')
        lines.append("")
        lines.append("    def __init__(self):")
        lines.append("        pass")
        lines.append("")

    lines.append("# CUI // SP-CTI")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sync code -> model
# ---------------------------------------------------------------------------

def sync_code_to_model(project_id: str, output_path: str,
                       db_path: Optional[Path] = None) -> dict:
    """Reverse sync: analyze code and generate XMI fragment for Cameo import.

    Steps:
        1. For each code_ahead mapping: parse Python AST to extract class/function info
        2. For new code files (not in any mapping): detect classes/functions
        3. Generate XMI fragment with new/modified elements
        4. Output as .xmi file for manual import into Cameo
        5. Log audit trail

    Returns::

        {
            "xmi_file": str,
            "elements_exported": int,
            "new_elements": int,
            "modified_elements": int,
            "errors": [...]
        }
    """
    conn = _get_connection(db_path)
    cursor = conn.cursor()
    errors: List[str] = []
    elements_for_xmi: List[Dict[str, Any]] = []
    modified_count = 0
    new_count = 0

    # Step 1: Process code_ahead mappings
    cursor.execute(
        """SELECT mcm.id, mcm.sysml_element_id, mcm.code_path, mcm.code_type,
                  se.name, se.xmi_id
           FROM model_code_mappings mcm
           LEFT JOIN sysml_elements se ON mcm.sysml_element_id = se.id
           WHERE mcm.project_id = ? AND mcm.sync_status = 'code_ahead'""",
        (project_id,),
    )
    code_ahead_rows = [dict(r) for r in cursor.fetchall()]

    for row in code_ahead_rows:
        code_path = row["code_path"]
        try:
            parsed = _parse_python_ast(code_path)

            for cls in parsed["classes"]:
                elements_for_xmi.append({
                    "name": cls["name"],
                    "element_type": "class",
                    "xmi_id": row.get("xmi_id") or f"elem-{uuid.uuid4()}",
                    "properties": {
                        "methods": cls["methods"],
                        "attributes": cls["attributes"],
                        "bases": cls["bases"],
                    },
                })
                modified_count += 1

            for func in parsed["functions"]:
                elements_for_xmi.append({
                    "name": func["name"],
                    "element_type": "function",
                    "xmi_id": f"func-{uuid.uuid4()}",
                    "properties": {
                        "args": func["args"],
                        "docstring": func["docstring"],
                    },
                })
                modified_count += 1

            # Update mapping: store the current code hash
            new_hash = _compute_file_hash(code_path)
            cursor.execute(
                """UPDATE model_code_mappings
                   SET code_hash = ?, last_synced = ?
                   WHERE id = ?""",
                (new_hash, _ts(), row["id"]),
            )
        except Exception as exc:
            errors.append(f"Failed to parse {code_path}: {exc}")

    # Step 2: Find unmapped code files in the project output directory
    project_output_dir = BASE_DIR / "output" / project_id
    if project_output_dir.exists():
        cursor.execute(
            "SELECT code_path FROM model_code_mappings WHERE project_id = ?",
            (project_id,),
        )
        mapped_paths = {r["code_path"] for r in cursor.fetchall()}

        for py_file in project_output_dir.rglob("*.py"):
            if str(py_file) in mapped_paths:
                continue
            try:
                parsed = _parse_python_ast(str(py_file))
                for cls in parsed["classes"]:
                    elements_for_xmi.append({
                        "name": cls["name"],
                        "element_type": "class",
                        "xmi_id": f"new-{uuid.uuid4()}",
                        "properties": {
                            "methods": cls["methods"],
                            "attributes": cls["attributes"],
                            "bases": cls["bases"],
                        },
                    })
                    new_count += 1

                for func in parsed["functions"]:
                    elements_for_xmi.append({
                        "name": func["name"],
                        "element_type": "function",
                        "xmi_id": f"new-{uuid.uuid4()}",
                        "properties": {
                            "args": func["args"],
                            "docstring": func["docstring"],
                        },
                    })
                    new_count += 1
            except Exception as exc:
                errors.append(f"Failed to scan {py_file}: {exc}")

    conn.commit()
    conn.close()

    # Step 3-4: Generate XMI fragment and write to file
    xmi_content = _generate_xmi_fragment(elements_for_xmi)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(xmi_content, encoding="utf-8")

    # Step 5: Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="code_generated",
                actor="icdev-sync-engine",
                action=(
                    f"Code-to-model sync for project {project_id}: "
                    f"exported {len(elements_for_xmi)} elements to {output_path}"
                ),
                project_id=project_id,
                details={
                    "xmi_file": str(out_path),
                    "elements_exported": len(elements_for_xmi),
                    "modified_elements": modified_count,
                    "new_elements": new_count,
                },
                affected_files=[str(out_path)],
                classification="CUI",
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass

    return {
        "xmi_file": str(out_path),
        "elements_exported": len(elements_for_xmi),
        "new_elements": new_count,
        "modified_elements": modified_count,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

def resolve_conflict(project_id: str, mapping_id: int, resolution: str,
                     db_path: Optional[Path] = None) -> dict:
    """Resolve a model/code conflict for a specific mapping.

    Args:
        resolution: One of 'keep_model', 'keep_code', or 'merge'.
            - keep_model: Regenerate code from model, update code_hash.
            - keep_code:  Update model_hash to current value (model stale until
                          re-imported). Does NOT modify the model file.
            - merge:      Mark as bidirectional; leave both sides as-is for
                          manual merge.

    Returns::

        {"mapping_id": int, "resolution": str, "status": str}
    """
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """SELECT mcm.*, se.name, se.element_type, se.properties,
                  se.description, se.source_hash, se.stereotype
           FROM model_code_mappings mcm
           LEFT JOIN sysml_elements se ON mcm.sysml_element_id = se.id
           WHERE mcm.id = ? AND mcm.project_id = ?""",
        (mapping_id, project_id),
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        return {
            "mapping_id": mapping_id,
            "resolution": resolution,
            "status": "error",
            "error": f"Mapping #{mapping_id} not found for project {project_id}",
        }

    row = dict(row)
    status = "resolved"

    try:
        if resolution == "keep_model":
            # Regenerate code from model
            code_content = _generate_code_from_element(
                name=row["name"] or "",
                element_type=row["element_type"] or "block",
                properties=row["properties"] or "{}",
                description=row["description"] or "",
                stereotype=row["stereotype"] or "",
                language="python",
            )
            code_path = Path(row["code_path"])
            code_path.parent.mkdir(parents=True, exist_ok=True)
            code_path.write_text(code_content, encoding="utf-8")

            new_code_hash = _compute_file_hash(str(code_path))
            cursor.execute(
                """UPDATE model_code_mappings
                   SET sync_status = 'synced', code_hash = ?,
                       model_hash = ?, mapping_direction = 'model_to_code',
                       last_synced = ?
                   WHERE id = ?""",
                (new_code_hash, row["source_hash"], _ts(), mapping_id),
            )

        elif resolution == "keep_code":
            # Update model_hash to match current source_hash so it looks synced
            # The model side remains stale until manually re-imported
            current_code_hash = _compute_file_hash(row["code_path"])
            cursor.execute(
                """UPDATE model_code_mappings
                   SET sync_status = 'synced', code_hash = ?,
                       model_hash = ?, mapping_direction = 'code_to_model',
                       last_synced = ?
                   WHERE id = ?""",
                (current_code_hash, row["source_hash"], _ts(), mapping_id),
            )

        elif resolution == "merge":
            cursor.execute(
                """UPDATE model_code_mappings
                   SET sync_status = 'synced', mapping_direction = 'bidirectional',
                       last_synced = ?
                   WHERE id = ?""",
                (_ts(), mapping_id),
            )

        else:
            status = "error"

    except Exception as exc:
        status = "error"
        conn.close()
        return {
            "mapping_id": mapping_id,
            "resolution": resolution,
            "status": status,
            "error": str(exc),
        }

    conn.commit()
    conn.close()

    # Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="decision_made",
                actor="icdev-sync-engine",
                action=(
                    f"Conflict resolved for mapping #{mapping_id} in {project_id}: "
                    f"{resolution}"
                ),
                project_id=project_id,
                details={
                    "mapping_id": mapping_id,
                    "resolution": resolution,
                    "code_path": row["code_path"],
                    "element_name": row.get("name", ""),
                },
                classification="CUI",
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass

    return {
        "mapping_id": mapping_id,
        "resolution": resolution,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Re-import XMI
# ---------------------------------------------------------------------------

def reimport_xmi(project_id: str, file_path: str,
                 db_path: Optional[Path] = None) -> dict:
    """Re-import XMI after Cameo updates, merging with existing elements.

    Steps:
        1. Parse new XMI file
        2. Compare with existing sysml_elements by xmi_id
        3. Update changed elements, add new ones, mark deleted ones
        4. Update model_code_mappings for affected elements
        5. Log audit trail

    Returns::

        {"updated": int, "added": int, "deleted": int, "unchanged": int}
    """
    # Lazy import xmi_parser to avoid circular dependencies
    try:
        from tools.mbse.xmi_parser import parse_xmi, _file_hash  # type: ignore
    except ImportError:
        # Fallback: use local file hash
        parse_xmi = None
        _file_hash = _compute_file_hash

    if parse_xmi is None:
        return {
            "updated": 0, "added": 0, "deleted": 0, "unchanged": 0,
            "error": "xmi_parser not available. Ensure tools/mbse/xmi_parser.py exists.",
        }

    # Step 1: Parse new XMI
    try:
        parsed = parse_xmi(file_path)
    except Exception as exc:
        return {
            "updated": 0, "added": 0, "deleted": 0, "unchanged": 0,
            "error": f"XMI parse error: {exc}",
        }

    new_elements = parsed["elements"]
    new_source_hash = parsed["metadata"]["file_hash"]
    timestamp = _ts()

    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # Step 2: Load existing elements by xmi_id
    cursor.execute(
        "SELECT id, xmi_id, source_hash FROM sysml_elements WHERE project_id = ?",
        (project_id,),
    )
    existing = {r["xmi_id"]: dict(r) for r in cursor.fetchall()}

    updated = 0
    added = 0
    deleted = 0
    unchanged = 0

    new_xmi_ids = set()

    # Step 3: Process parsed elements
    for elem in new_elements:
        xmi_id = elem.get("xmi_id", "")
        new_xmi_ids.add(xmi_id)

        if xmi_id in existing:
            # Compare source_hash to detect changes
            old_hash = existing[xmi_id].get("source_hash", "")
            if old_hash != new_source_hash:
                # Element changed — update it
                cursor.execute(
                    """UPDATE sysml_elements
                       SET name = ?, element_type = ?, qualified_name = ?,
                           stereotype = ?, description = ?, properties = ?,
                           diagram_type = ?, source_file = ?,
                           source_hash = ?, updated_at = ?
                       WHERE project_id = ? AND xmi_id = ?""",
                    (
                        elem["name"],
                        elem["element_type"],
                        elem.get("qualified_name", ""),
                        elem.get("stereotype", ""),
                        elem.get("description", ""),
                        elem.get("properties", "{}"),
                        elem.get("diagram_type"),
                        elem.get("source_file", ""),
                        new_source_hash,
                        timestamp,
                        project_id,
                        xmi_id,
                    ),
                )
                # Mark associated model_code_mappings as model_ahead
                elem_db_id = existing[xmi_id]["id"]
                cursor.execute(
                    """UPDATE model_code_mappings
                       SET sync_status = 'model_ahead', model_hash = ?
                       WHERE project_id = ? AND sysml_element_id = ?""",
                    (new_source_hash, project_id, elem_db_id),
                )
                updated += 1
            else:
                unchanged += 1
        else:
            # New element — insert
            elem_id = elem.get("id", _new_id())
            try:
                cursor.execute(
                    """INSERT INTO sysml_elements
                       (id, project_id, xmi_id, element_type, name,
                        qualified_name, parent_id, stereotype, description,
                        properties, diagram_type, source_file, source_hash,
                        imported_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        elem_id,
                        project_id,
                        xmi_id,
                        elem["element_type"],
                        elem["name"],
                        elem.get("qualified_name", ""),
                        elem.get("parent_id"),
                        elem.get("stereotype", ""),
                        elem.get("description", ""),
                        elem.get("properties", "{}"),
                        elem.get("diagram_type"),
                        elem.get("source_file", ""),
                        new_source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                added += 1
            except sqlite3.IntegrityError:
                # Duplicate — treat as update
                unchanged += 1

    # Step 3 (cont.): Mark elements missing from new XMI as deleted
    for xmi_id, existing_elem in existing.items():
        if xmi_id not in new_xmi_ids:
            # Mark as deleted by updating a description note (we do NOT delete)
            cursor.execute(
                """UPDATE sysml_elements
                   SET description = description || ' [DELETED in reimport ' || ? || ']',
                       updated_at = ?
                   WHERE id = ?""",
                (timestamp, timestamp, existing_elem["id"]),
            )
            # Mark associated mappings as unknown
            cursor.execute(
                """UPDATE model_code_mappings
                   SET sync_status = 'unknown'
                   WHERE project_id = ? AND sysml_element_id = ?""",
                (project_id, existing_elem["id"]),
            )
            deleted += 1

    # Step 4: Record in model_imports
    cursor.execute(
        """INSERT INTO model_imports
           (project_id, import_type, source_file, source_hash,
            elements_imported, relationships_imported, errors,
            error_details, status, imported_by, imported_at)
           VALUES (?, 'xmi_reimport', ?, ?, ?, 0, 0, NULL, 'completed',
                   'icdev-sync-engine', ?)""",
        (
            project_id,
            str(Path(file_path).name),
            new_source_hash,
            updated + added,
            timestamp,
        ),
    )

    conn.commit()
    conn.close()

    # Step 5: Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="compliance_check",
                actor="icdev-sync-engine",
                action=(
                    f"XMI reimport for {project_id}: "
                    f"{updated} updated, {added} added, {deleted} deleted, "
                    f"{unchanged} unchanged"
                ),
                project_id=project_id,
                details={
                    "file": file_path,
                    "source_hash": new_source_hash,
                    "updated": updated,
                    "added": added,
                    "deleted": deleted,
                    "unchanged": unchanged,
                },
                affected_files=[file_path],
                classification="CUI",
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass

    return {
        "updated": updated,
        "added": added,
        "deleted": deleted,
        "unchanged": unchanged,
    }


# ---------------------------------------------------------------------------
# Re-import ReqIF
# ---------------------------------------------------------------------------

def reimport_reqif(project_id: str, file_path: str,
                   db_path: Optional[Path] = None) -> dict:
    """Re-import ReqIF after DOORS updates, merging with existing requirements.

    Similar to reimport_xmi but operates on the doors_requirements table.

    Steps:
        1. Parse new ReqIF file
        2. Compare with existing doors_requirements by doors_id
        3. Update changed requirements, add new ones, mark deleted ones
        4. Log audit trail

    Returns::

        {"updated": int, "added": int, "deleted": int, "unchanged": int}
    """
    try:
        from tools.mbse.reqif_parser import parse_reqif, _file_hash, _content_hash as _rp_content_hash  # type: ignore
    except ImportError:
        parse_reqif = None

    if parse_reqif is None:
        return {
            "updated": 0, "added": 0, "deleted": 0, "unchanged": 0,
            "error": "reqif_parser not available. Ensure tools/mbse/reqif_parser.py exists.",
        }

    # Step 1: Parse new ReqIF
    try:
        parsed = parse_reqif(file_path)
    except Exception as exc:
        return {
            "updated": 0, "added": 0, "deleted": 0, "unchanged": 0,
            "error": f"ReqIF parse error: {exc}",
        }

    new_reqs = parsed["requirements"]
    source_hash = _compute_file_hash(file_path)
    timestamp = _ts()

    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # Step 2: Load existing requirements by doors_id
    cursor.execute(
        "SELECT id, doors_id, title, description, priority, requirement_type "
        "FROM doors_requirements WHERE project_id = ?",
        (project_id,),
    )
    existing = {}
    for r in cursor.fetchall():
        row_dict = dict(r)
        existing[row_dict["doors_id"]] = row_dict

    updated = 0
    added = 0
    deleted = 0
    unchanged = 0
    new_doors_ids = set()

    # Step 3: Process parsed requirements
    for req in new_reqs:
        doors_id = req.get("doors_id", "")
        if not doors_id:
            continue
        new_doors_ids.add(doors_id)

        # Build content fingerprint for change detection
        new_content = (
            f"{req.get('title', '')}|{req.get('description', '')}|"
            f"{req.get('priority', '')}|{req.get('requirement_type', '')}"
        )
        new_hash = _content_hash(new_content)

        if doors_id in existing:
            ex = existing[doors_id]
            old_content = (
                f"{ex.get('title', '')}|{ex.get('description', '')}|"
                f"{ex.get('priority', '')}|{ex.get('requirement_type', '')}"
            )
            old_hash = _content_hash(old_content)

            if new_hash != old_hash:
                cursor.execute(
                    """UPDATE doors_requirements
                       SET title = ?, description = ?, priority = ?,
                           requirement_type = ?, source_hash = ?, updated_at = ?
                       WHERE project_id = ? AND doors_id = ?""",
                    (
                        req.get("title", ""),
                        req.get("description", ""),
                        req.get("priority"),
                        req.get("requirement_type"),
                        source_hash,
                        timestamp,
                        project_id,
                        doors_id,
                    ),
                )
                updated += 1
            else:
                unchanged += 1
        else:
            # New requirement — insert
            req_id = f"dreq-{uuid.uuid4()}"
            try:
                cursor.execute(
                    """INSERT INTO doors_requirements
                       (id, project_id, doors_id, title, description,
                        priority, requirement_type, status,
                        source_file, source_hash, imported_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
                    (
                        req_id,
                        project_id,
                        doors_id,
                        req.get("title", ""),
                        req.get("description", ""),
                        req.get("priority"),
                        req.get("requirement_type"),
                        str(file_path),
                        source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                added += 1
            except sqlite3.IntegrityError:
                unchanged += 1

    # Mark requirements not in new file as deleted (soft delete via status)
    for doors_id in existing:
        if doors_id not in new_doors_ids:
            cursor.execute(
                """UPDATE doors_requirements
                   SET status = 'deleted', updated_at = ?
                   WHERE project_id = ? AND doors_id = ?""",
                (timestamp, project_id, doors_id),
            )
            deleted += 1

    # Record in model_imports
    cursor.execute(
        """INSERT INTO model_imports
           (project_id, import_type, source_file, source_hash,
            elements_imported, relationships_imported, errors,
            error_details, status, imported_by, imported_at)
           VALUES (?, 'reqif_reimport', ?, ?, ?, 0, 0, NULL, 'completed',
                   'icdev-sync-engine', ?)""",
        (
            project_id,
            str(Path(file_path).name),
            source_hash,
            updated + added,
            timestamp,
        ),
    )

    conn.commit()
    conn.close()

    # Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="compliance_check",
                actor="icdev-sync-engine",
                action=(
                    f"ReqIF reimport for {project_id}: "
                    f"{updated} updated, {added} added, {deleted} deleted, "
                    f"{unchanged} unchanged"
                ),
                project_id=project_id,
                details={
                    "file": file_path,
                    "source_hash": source_hash,
                    "updated": updated,
                    "added": added,
                    "deleted": deleted,
                    "unchanged": unchanged,
                },
                affected_files=[file_path],
                classification="CUI",
                db_path=Path(db_path) if db_path else None,
            )
        except Exception:
            pass

    return {
        "updated": updated,
        "added": added,
        "deleted": deleted,
        "unchanged": unchanged,
    }


# ---------------------------------------------------------------------------
# Sync report
# ---------------------------------------------------------------------------

def generate_sync_report(project_id: str,
                         db_path: Optional[Path] = None) -> str:
    """Generate a CUI-marked drift/sync report as markdown.

    Includes:
        - Summary statistics (synced, model_ahead, code_ahead, conflict, unknown)
        - Per-mapping details table
        - Conflict resolution guidance
        - Timestamps
    """
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # Fetch all mappings with element names
    cursor.execute(
        """SELECT mcm.id, mcm.sysml_element_id, mcm.code_path, mcm.code_type,
                  mcm.mapping_direction, mcm.sync_status, mcm.last_synced,
                  mcm.model_hash, mcm.code_hash,
                  se.name AS element_name, se.element_type
           FROM model_code_mappings mcm
           LEFT JOIN sysml_elements se ON mcm.sysml_element_id = se.id
           WHERE mcm.project_id = ?
           ORDER BY mcm.sync_status, se.name""",
        (project_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]

    # Fetch recent imports
    cursor.execute(
        """SELECT import_type, source_file, status, imported_at
           FROM model_imports
           WHERE project_id = ?
           ORDER BY imported_at DESC LIMIT 5""",
        (project_id,),
    )
    recent_imports = [dict(r) for r in cursor.fetchall()]

    conn.close()

    # Count statuses
    status_counts: Dict[str, int] = {
        "synced": 0, "model_ahead": 0, "code_ahead": 0,
        "conflict": 0, "unknown": 0,
    }
    for row in rows:
        s = row.get("sync_status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    # Build markdown
    lines = [
        "CUI // SP-CTI",
        "",
        f"# MBSE Sync Report: {project_id}",
        f"**Generated:** {_ts()}",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|--------|-------|",
        f"| Synced | {status_counts['synced']} |",
        f"| Model Ahead | {status_counts['model_ahead']} |",
        f"| Code Ahead | {status_counts['code_ahead']} |",
        f"| Conflict | {status_counts['conflict']} |",
        f"| Unknown | {status_counts['unknown']} |",
        f"| **Total** | **{len(rows)}** |",
        "",
    ]

    if status_counts["conflict"] > 0:
        lines.extend([
            "## Conflicts Requiring Resolution",
            "",
            "| ID | Element | Code Path | Direction |",
            "|----|---------|-----------|-----------|",
        ])
        for row in rows:
            if row["sync_status"] == "conflict":
                lines.append(
                    f"| {row['id']} | {row.get('element_name', 'N/A')} "
                    f"| `{row['code_path']}` | {row['mapping_direction']} |"
                )
        lines.append("")
        lines.extend([
            "**Resolution options:**",
            "- `--resolution keep_model` — Overwrite code with model version",
            "- `--resolution keep_code` — Accept code changes (model stale until reimport)",
            "- `--resolution merge` — Mark bidirectional for manual merge",
            "",
        ])

    if rows:
        lines.extend([
            "## All Mappings",
            "",
            "| ID | Element | Type | Code Path | Status | Last Synced |",
            "|----|---------|------|-----------|--------|-------------|",
        ])
        for row in rows:
            lines.append(
                f"| {row['id']} | {row.get('element_name', 'N/A')} "
                f"| {row['code_type']} | `{row['code_path']}` "
                f"| {row['sync_status']} | {row.get('last_synced', 'N/A')} |"
            )
        lines.append("")

    if recent_imports:
        lines.extend([
            "## Recent Imports",
            "",
            "| Type | File | Status | Date |",
            "|------|------|--------|------|",
        ])
        for imp in recent_imports:
            lines.append(
                f"| {imp['import_type']} | {imp['source_file']} "
                f"| {imp['status']} | {imp['imported_at']} |"
            )
        lines.append("")

    lines.extend([
        "---",
        "CUI // SP-CTI",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Command-line interface for MBSE bidirectional sync engine."""
    parser = argparse.ArgumentParser(
        description="ICDEV MBSE Bidirectional Sync Engine"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="ICDEV project identifier (e.g. proj-123)",
    )

    sub = parser.add_subparsers(dest="command")

    # detect-drift
    sub.add_parser("detect-drift", help="Detect model/code drift")

    # sync model-to-code
    m2c = sub.add_parser("sync-model-to-code",
                         help="Sync model changes to code files")
    m2c.add_argument("--language", default="python",
                     help="Target language (default: python)")

    # sync code-to-model
    c2m = sub.add_parser("sync-code-to-model",
                         help="Sync code changes to XMI fragment for Cameo import")
    c2m.add_argument("--output", required=True,
                     help="Output XMI file path")

    # resolve-conflict
    rc = sub.add_parser("resolve-conflict",
                        help="Resolve a model/code sync conflict")
    rc.add_argument("--mapping-id", type=int, required=True,
                    help="ID from model_code_mappings table")
    rc.add_argument("--resolution", required=True,
                    choices=["keep_model", "keep_code", "merge"],
                    help="Conflict resolution strategy")

    # reimport-xmi
    ri = sub.add_parser("reimport-xmi",
                        help="Re-import XMI after Cameo updates")
    ri.add_argument("--file", required=True,
                    help="Path to updated XMI file")

    # reimport-reqif
    rr = sub.add_parser("reimport-reqif",
                        help="Re-import ReqIF after DOORS updates")
    rr.add_argument("--file", required=True,
                    help="Path to updated ReqIF file")

    # report
    sub.add_parser("report", help="Generate drift/sync report")

    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output results as JSON")
    parser.add_argument("--db-path", type=Path, default=None,
                        help="Override database path (default: data/icdev.db)")

    args = parser.parse_args()
    db = args.db_path

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # ---- detect-drift ----
    if args.command == "detect-drift":
        result = detect_drift(args.project_id, db_path=db)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"Drift Detection: {args.project_id}")
            print(f"  Total mappings: {result['total_mappings']}")
            print(f"  Synced:         {result['synced']}")
            print(f"  Model ahead:    {result['model_ahead']}")
            print(f"  Code ahead:     {result['code_ahead']}")
            print(f"  Conflict:       {result['conflict']}")
            print(f"  Unknown:        {result['unknown']}")
            print(f"  Missing files:  {result['missing_files']}")
            if result.get("details"):
                print("\n  Details:")
                for d in result["details"]:
                    marker = {"synced": "=", "model_ahead": "M>",
                              "code_ahead": "C>", "conflict": "!",
                              "unknown": "?"}.get(d["new_status"], "?")
                    print(f"    [{marker}] {d['element_name']}: {d['code_path']}")
            print("CUI // SP-CTI")

    # ---- sync-model-to-code ----
    elif args.command == "sync-model-to-code":
        result = sync_model_to_code(args.project_id, language=args.language,
                                    db_path=db)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"Model-to-Code Sync: {args.project_id}")
            print(f"  Files updated:  {result['files_updated']}")
            print(f"  Files created:  {result['files_created']}")
            print(f"  Files orphaned: {result['files_orphaned']}")
            print(f"  Errors:         {result['errors']}")
            if result.get("error_details"):
                for err in result["error_details"]:
                    print(f"    - {err}")
            print("CUI // SP-CTI")

    # ---- sync-code-to-model ----
    elif args.command == "sync-code-to-model":
        result = sync_code_to_model(args.project_id, output_path=args.output,
                                    db_path=db)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"Code-to-Model Sync: {args.project_id}")
            print(f"  XMI file:           {result['xmi_file']}")
            print(f"  Elements exported:  {result['elements_exported']}")
            print(f"  Modified elements:  {result['modified_elements']}")
            print(f"  New elements:       {result['new_elements']}")
            if result.get("errors"):
                print(f"  Errors: {len(result['errors'])}")
                for err in result["errors"]:
                    print(f"    - {err}")
            print("CUI // SP-CTI")

    # ---- resolve-conflict ----
    elif args.command == "resolve-conflict":
        result = resolve_conflict(args.project_id, mapping_id=args.mapping_id,
                                  resolution=args.resolution, db_path=db)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"Conflict Resolution: mapping #{result['mapping_id']}")
            print(f"  Resolution: {result['resolution']}")
            print(f"  Status:     {result['status']}")
            if result.get("error"):
                print(f"  Error:      {result['error']}")
            print("CUI // SP-CTI")

    # ---- reimport-xmi ----
    elif args.command == "reimport-xmi":
        file_path = str(Path(args.file).resolve())
        result = reimport_xmi(args.project_id, file_path=file_path, db_path=db)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"XMI Reimport: {args.project_id}")
            print(f"  Updated:   {result['updated']}")
            print(f"  Added:     {result['added']}")
            print(f"  Deleted:   {result['deleted']}")
            print(f"  Unchanged: {result['unchanged']}")
            if result.get("error"):
                print(f"  Error:     {result['error']}")
            print("CUI // SP-CTI")

    # ---- reimport-reqif ----
    elif args.command == "reimport-reqif":
        file_path = str(Path(args.file).resolve())
        result = reimport_reqif(args.project_id, file_path=file_path,
                                db_path=db)
        if args.json_output:
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            print("CUI // SP-CTI")
            print(f"ReqIF Reimport: {args.project_id}")
            print(f"  Updated:   {result['updated']}")
            print(f"  Added:     {result['added']}")
            print(f"  Deleted:   {result['deleted']}")
            print(f"  Unchanged: {result['unchanged']}")
            if result.get("error"):
                print(f"  Error:     {result['error']}")
            print("CUI // SP-CTI")

    # ---- report ----
    elif args.command == "report":
        if args.json_output:
            # For JSON mode, run detect_drift and output the result
            result = detect_drift(args.project_id, db_path=db)
            print("CUI // SP-CTI")
            print(json.dumps(result, indent=2, default=str))
            print("CUI // SP-CTI")
        else:
            report = generate_sync_report(args.project_id, db_path=db)
            print(report)

    sys.exit(0)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
