# [TEMPLATE: CUI // SP-CTI]
"""Model Code Generator -- generate code scaffolding from SysML model elements.

Reads sysml_elements and sysml_relationships from the ICDEV database,
generates code files (classes, modules, state machines, tests), and records
model_code_mappings and digital_thread_links for full traceability.

Usage:
    python tools/mbse/model_code_generator.py --project-id PROJ --output /path --language python
    python tools/mbse/model_code_generator.py --project-id PROJ --output /path --blocks-only
    python tools/mbse/model_code_generator.py --project-id PROJ --output /path --tests-only --json
"""

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
LANG_REGISTRY_PATH = BASE_DIR / "context" / "languages" / "language_registry.json"

# Try to import audit logger for traceability
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None


# ---------------------------------------------------------------------------
# Language registry loader
# ---------------------------------------------------------------------------

def _load_language_registry() -> dict:
    """Load the language registry from context/languages/language_registry.json."""
    if LANG_REGISTRY_PATH.exists():
        with open(LANG_REGISTRY_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"languages": {}}


_LANG_REGISTRY = _load_language_registry()

# File extension mapping per language
_LANGUAGE_EXTENSIONS: Dict[str, str] = {
    "python": ".py",
    "java": ".java",
    "go": ".go",
    "rust": ".rs",
    "csharp": ".cs",
    "typescript": ".ts",
}

# Comment style per language (for CUI headers/footers)
_COMMENT_STYLES: Dict[str, str] = {
    "hash": "#",       # python
    "c-style": "//",   # java, go, rust, csharp, typescript
}


# ---------------------------------------------------------------------------
# CUI header / footer helpers
# ---------------------------------------------------------------------------

def _get_cui_header(language: str) -> str:
    """Return CUI // SP-CTI header in correct comment style for language."""
    lang_info = _LANG_REGISTRY.get("languages", {}).get(language, {})
    style = lang_info.get("cui_comment_style", "hash")
    prefix = _COMMENT_STYLES.get(style, "#")
    return f"{prefix} CUI // SP-CTI"


def _get_cui_footer(language: str) -> str:
    """Return CUI // SP-CTI footer in correct comment style for language."""
    return _get_cui_header(language)


# ---------------------------------------------------------------------------
# Name conversion helpers
# ---------------------------------------------------------------------------

def _to_class_name(name: str) -> str:
    """Convert any name to PascalCase class name.

    Examples:
        'authentication_service' -> 'AuthenticationService'
        'my-block'               -> 'MyBlock'
        'Foo Bar Baz'            -> 'FooBarBaz'
    """
    # Replace non-alphanumeric with spaces, split, capitalise each word
    cleaned = re.sub(r"[^a-zA-Z0-9]", " ", name)
    parts = cleaned.split()
    return "".join(word.capitalize() for word in parts) if parts else "Unnamed"


def _to_snake_case(name: str) -> str:
    """Convert any name to snake_case for files/functions.

    Examples:
        'AuthenticationService' -> 'authentication_service'
        'My Block'              -> 'my_block'
    """
    # Insert underscore before uppercase letters that follow lowercase/digits
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Replace non-alphanumeric with underscore
    s2 = re.sub(r"[^a-zA-Z0-9]", "_", s1)
    # Collapse multiple underscores
    s3 = re.sub(r"_+", "_", s2).strip("_")
    return s3.lower()


def _to_file_name(name: str, language: str) -> str:
    """Convert element name to appropriate file name with extension."""
    ext = _LANGUAGE_EXTENSIONS.get(language, ".py")
    snake = _to_snake_case(name)
    if language == "java":
        # Java uses PascalCase filenames
        return _to_class_name(name) + ext
    return snake + ext


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None) -> sqlite3.Connection:
    """Open a SQLite connection with row_factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_elements(project_id: str, element_type: str, conn: sqlite3.Connection) -> List[dict]:
    """Fetch sysml_elements of a given type for a project."""
    cur = conn.execute(
        "SELECT * FROM sysml_elements WHERE project_id = ? AND element_type = ?",
        (project_id, element_type),
    )
    return [dict(row) for row in cur.fetchall()]


def _fetch_elements_multi(project_id: str, element_types: List[str],
                          conn: sqlite3.Connection) -> List[dict]:
    """Fetch sysml_elements matching any of the given types."""
    placeholders = ",".join("?" for _ in element_types)
    cur = conn.execute(
        f"SELECT * FROM sysml_elements WHERE project_id = ? AND element_type IN ({placeholders})",
        [project_id] + element_types,
    )
    return [dict(row) for row in cur.fetchall()]


def _fetch_children(parent_id: str, conn: sqlite3.Connection) -> List[dict]:
    """Fetch child elements of a given parent."""
    cur = conn.execute(
        "SELECT * FROM sysml_elements WHERE parent_id = ?",
        (parent_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def _fetch_relationships(project_id: str, conn: sqlite3.Connection,
                         source_id: str = None,
                         rel_types: List[str] = None) -> List[dict]:
    """Fetch sysml_relationships with optional filters."""
    query = "SELECT * FROM sysml_relationships WHERE project_id = ?"
    params: list = [project_id]
    if source_id:
        query += " AND source_element_id = ?"
        params.append(source_id)
    if rel_types:
        placeholders = ",".join("?" for _ in rel_types)
        query += f" AND relationship_type IN ({placeholders})"
        params.extend(rel_types)
    cur = conn.execute(query, params)
    return [dict(row) for row in cur.fetchall()]


def _fetch_element_by_id(element_id: str, conn: sqlite3.Connection) -> Optional[dict]:
    """Fetch a single sysml_element by id."""
    cur = conn.execute("SELECT * FROM sysml_elements WHERE id = ?", (element_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _parse_properties(element: dict) -> dict:
    """Parse the JSON properties field of a sysml_element."""
    raw = element.get("properties")
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


# ---------------------------------------------------------------------------
# Recording helpers (model_code_mappings + digital_thread_links)
# ---------------------------------------------------------------------------

def _record_mapping(project_id: str, sysml_element_id: str, code_path: str,
                    code_type: str, code_hash: str, conn: sqlite3.Connection) -> None:
    """Record a model_code_mappings entry."""
    try:
        conn.execute(
            """INSERT OR REPLACE INTO model_code_mappings
               (project_id, sysml_element_id, code_path, code_type,
                mapping_direction, sync_status, model_hash, code_hash)
               VALUES (?, ?, ?, ?, 'model_to_code', 'synced', ?, ?)""",
            (project_id, sysml_element_id, code_path, code_type,
             None, code_hash),
        )
    except sqlite3.Error:
        pass  # Table may not exist in a test harness


def _record_thread_link(project_id: str, sysml_element_id: str,
                        code_path: str, conn: sqlite3.Connection) -> None:
    """Record a digital_thread_links entry (sysml_element -> code_module, implements)."""
    try:
        conn.execute(
            """INSERT OR IGNORE INTO digital_thread_links
               (project_id, source_type, source_id, target_type, target_id,
                link_type, confidence, created_by)
               VALUES (?, 'sysml_element', ?, 'code_module', ?, 'implements', 1.0,
                       'icdev-model-code-generator')""",
            (project_id, sysml_element_id, code_path),
        )
    except sqlite3.Error:
        pass


def _hash_content(content: str) -> str:
    """SHA-256 hash of file content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_file(path: Path, content: str) -> str:
    """Write content to file and return its SHA-256 hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return _hash_content(content)


# ---------------------------------------------------------------------------
# Python code generators
# ---------------------------------------------------------------------------

def _generate_python_class(block: dict, relationships: list, all_blocks: dict) -> str:
    """Generate a Python class from a SysML block with properties, operations, inheritance."""
    props = _parse_properties(block)
    class_name = _to_class_name(block["name"])
    description = block.get("description") or f"SysML Block '{block['name']}'."
    source_file = block.get("source_file", "model.xmi")

    # Determine base classes from generalization relationships
    bases: List[str] = []
    is_interface = block.get("element_type") == "interface_block"
    for rel in relationships:
        if rel["relationship_type"] == "generalization" and rel["source_element_id"] == block["id"]:
            target = all_blocks.get(rel["target_element_id"])
            if target:
                bases.append(_to_class_name(target["name"]))

    if is_interface and not bases:
        bases.append("ABC")

    # Collect value properties, part properties, and operations from properties JSON
    value_props = props.get("value_properties", [])
    part_props = props.get("part_properties", [])
    operations = props.get("operations", [])

    # Also gather part properties from composition relationships
    comp_parts: List[Tuple[str, str]] = []  # (attr_name, class_name)
    for rel in relationships:
        if rel["relationship_type"] == "composition" and rel["source_element_id"] == block["id"]:
            target = all_blocks.get(rel["target_element_id"])
            if target:
                tname = _to_class_name(target["name"])
                attr = _to_snake_case(target["name"])
                comp_parts.append((attr, tname))

    # Build imports
    imports: List[str] = []
    if is_interface:
        imports.append("from abc import ABC, abstractmethod")
    imports.append("from dataclasses import dataclass, field")
    imports.append("from typing import Optional, List")

    # Build class definition
    base_str = f"({', '.join(bases)})" if bases else ""

    lines: List[str] = []
    lines.append(f'"""Module: {_to_snake_case(block["name"])} -- Generated from SysML Block \'{block["name"]}\'."""')
    lines.append("")
    for imp in imports:
        lines.append(imp)
    lines.append("")
    lines.append("")
    lines.append("@dataclass")
    lines.append(f"class {class_name}{base_str}:")
    lines.append(f'    """{class_name} -- SysML Block.')
    lines.append("")
    lines.append(f"    Description: {description}")
    lines.append(f"    Source Model: {source_file} (block: {block['name']})")
    lines.append('    """')

    # Value properties
    if value_props:
        lines.append("")
        lines.append("    # Value Properties")
        for vp in value_props:
            vp_name = _to_snake_case(vp.get("name", "unknown"))
            vp_type = vp.get("type", "str")
            vp_default = vp.get("default")
            type_map = {
                "Integer": "int", "int": "int",
                "Real": "float", "float": "float",
                "Boolean": "bool", "bool": "bool",
                "String": "str", "str": "str",
            }
            py_type = type_map.get(vp_type, "str")
            if vp_default is not None:
                lines.append(f"    {vp_name}: {py_type} = {repr(vp_default)}")
            else:
                defaults = {"int": "0", "float": "0.0", "bool": "False", "str": '""'}
                lines.append(f"    {vp_name}: {py_type} = {defaults.get(py_type, 'None')}")

    # Part properties (from properties JSON)
    if part_props or comp_parts:
        lines.append("")
        lines.append("    # Part Properties")
        seen_parts = set()
        for pp in part_props:
            pp_name = _to_snake_case(pp.get("name", "part"))
            pp_type = _to_class_name(pp.get("type", "object"))
            if pp_name not in seen_parts:
                lines.append(f"    {pp_name}: Optional['{pp_type}'] = None")
                seen_parts.add(pp_name)
        for attr, tname in comp_parts:
            if attr not in seen_parts:
                lines.append(f"    {attr}: Optional['{tname}'] = None")
                seen_parts.add(attr)

    # If no properties at all, add pass
    if not value_props and not part_props and not comp_parts and not operations:
        lines.append("    pass")

    # Operations
    if operations:
        lines.append("")
        lines.append("    # Operations")
        for op in operations:
            op_name = _to_snake_case(op.get("name", "operation"))
            op_return = op.get("return_type", "None")
            op_params = op.get("parameters", [])
            type_map = {
                "Integer": "int", "Real": "float", "Boolean": "bool",
                "String": "str", "void": "None", "": "None",
            }
            py_return = type_map.get(op_return, op_return)

            # Build parameter list
            param_strs = ["self"]
            for p in op_params:
                p_name = _to_snake_case(p.get("name", "arg"))
                p_type = type_map.get(p.get("type", ""), "Any")
                param_strs.append(f"{p_name}: {p_type}")

            params = ", ".join(param_strs)
            is_abstract = is_interface or op.get("abstract", False)

            lines.append("")
            if is_abstract:
                lines.append("    @abstractmethod")
            lines.append(f"    def {op_name}({params}) -> {py_return}:")
            lines.append(f'        """{op_name} -- from SysML operation."""')
            if is_abstract:
                lines.append("        ...")
            else:
                lines.append(f'        raise NotImplementedError("Generated stub -- implement {op_name} logic")')

    content = _get_cui_header("python") + "\n"
    content += "\n".join(lines) + "\n"
    content += "\n" + _get_cui_footer("python") + "\n"
    return content


def _generate_python_module(activity: dict, actions: list) -> str:
    """Generate a Python module from a SysML activity with function stubs."""
    mod_name = _to_snake_case(activity["name"])
    description = activity.get("description") or f"SysML Activity '{activity['name']}'."
    source_file = activity.get("source_file", "model.xmi")

    # Separate actions by type
    regular_actions = []
    decision_nodes = []
    fork_nodes = []
    join_nodes = []
    control_flows = []

    for a in actions:
        etype = a.get("element_type", "action")
        if etype == "control_flow":
            control_flows.append(a)
        elif a.get("stereotype") == "decision" or "decision" in (a.get("name") or "").lower():
            decision_nodes.append(a)
        elif a.get("stereotype") == "fork" or "fork" in (a.get("name") or "").lower():
            fork_nodes.append(a)
        elif a.get("stereotype") == "join" or "join" in (a.get("name") or "").lower():
            join_nodes.append(a)
        elif etype == "action":
            regular_actions.append(a)
        else:
            regular_actions.append(a)

    lines: List[str] = []
    lines.append(f'"""Module: {mod_name} -- Generated from SysML Activity \'{activity["name"]}\'.')
    lines.append("")
    lines.append(f"Description: {description}")
    lines.append(f"Source Model: {source_file} (activity: {activity['name']})")
    lines.append('"""')
    lines.append("")
    lines.append("import logging")
    lines.append("from typing import Any, Dict, Optional")
    lines.append("")
    lines.append('logger = logging.getLogger(__name__)')
    lines.append("")

    # Generate a function stub for each action
    for action in regular_actions:
        fn_name = _to_snake_case(action["name"])
        action_desc = action.get("description") or f"Action '{action['name']}' from SysML activity."
        lines.append("")
        lines.append(f"def {fn_name}(context: Dict[str, Any]) -> Dict[str, Any]:")
        lines.append(f'    """{fn_name} -- from SysML action.')
        lines.append("")
        lines.append(f"    {action_desc}")
        lines.append("")
        lines.append("    Args:")
        lines.append("        context: Execution context dictionary.")
        lines.append("")
        lines.append("    Returns:")
        lines.append("        Updated context dictionary.")
        lines.append('    """')
        lines.append(f'    logger.info("Executing {fn_name}")')
        lines.append(f'    raise NotImplementedError("Generated stub -- implement {fn_name}")')
        lines.append("")

    # Generate orchestrator function
    lines.append("")
    lines.append(f"def run_{mod_name}(context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:")
    lines.append(f'    """Orchestrator for activity \'{activity["name"]}\'.')
    lines.append("")
    lines.append("    Calls action functions in sequence as defined by control flows.")
    lines.append("")
    lines.append("    Args:")
    lines.append("        context: Initial execution context (defaults to empty dict).")
    lines.append("")
    lines.append("    Returns:")
    lines.append("        Final context after all actions complete.")
    lines.append('    """')
    lines.append("    ctx = context or {}")
    lines.append(f'    logger.info("Starting activity: {activity["name"]}")')

    # Decision node stubs
    for dn in decision_nodes:
        dn_name = _to_snake_case(dn["name"])
        lines.append("")
        lines.append(f"    # Decision: {dn['name']}")
        lines.append(f'    if ctx.get("{dn_name}_condition"):')
        lines.append(f"        pass  # TODO: implement true branch for {dn['name']}")
        lines.append("    else:")
        lines.append(f"        pass  # TODO: implement false branch for {dn['name']}")

    # Fork/join stubs
    for fn in fork_nodes:
        lines.append("")
        lines.append(f"    # Fork: {fn['name']} -- parallel execution point")
        lines.append(f"    # TODO: implement parallel branches for {fn['name']}")

    # Call actions in sequence
    if regular_actions:
        lines.append("")
        lines.append("    # Action sequence (from control flows)")
        for action in regular_actions:
            fn_name = _to_snake_case(action["name"])
            lines.append(f"    ctx = {fn_name}(ctx)")

    for jn in join_nodes:
        lines.append("")
        lines.append(f"    # Join: {jn['name']} -- synchronisation point")
        lines.append(f"    # TODO: merge parallel results for {jn['name']}")

    lines.append("")
    lines.append(f'    logger.info("Activity completed: {activity["name"]}")')
    lines.append("    return ctx")
    lines.append("")

    content = _get_cui_header("python") + "\n"
    content += "\n".join(lines) + "\n"
    content += _get_cui_footer("python") + "\n"
    return content


def _generate_python_state_machine(sm: dict, states: list, transitions: list) -> str:
    """Generate Python state machine with enum and transition dict."""
    mod_name = _to_snake_case(sm["name"])
    class_name = _to_class_name(sm["name"])
    description = sm.get("description") or f"SysML StateMachine '{sm['name']}'."
    source_file = sm.get("source_file", "model.xmi")

    lines: List[str] = []
    lines.append(f'"""Module: {mod_name} -- Generated from SysML StateMachine \'{sm["name"]}\'.')
    lines.append("")
    lines.append(f"Description: {description}")
    lines.append(f"Source Model: {source_file} (state_machine: {sm['name']})")
    lines.append('"""')
    lines.append("")
    lines.append("from enum import Enum, auto")
    lines.append("from typing import Any, Callable, Dict, Optional, Tuple")
    lines.append("")
    lines.append("")

    # State enum
    lines.append(f"class {class_name}State(Enum):")
    lines.append(f'    """States for {sm["name"]} state machine."""')
    if states:
        for s in states:
            enum_name = _to_snake_case(s["name"]).upper()
            lines.append(f"    {enum_name} = auto()")
    else:
        lines.append("    INITIAL = auto()")
    lines.append("")
    lines.append("")

    # Transition table
    lines.append("# Transition table: (current_state, event) -> next_state")
    lines.append(f"TRANSITIONS: Dict[Tuple[{class_name}State, str], {class_name}State] = {{")
    if transitions:
        for t in transitions:
            src_state = t.get("source_state", "INITIAL")
            tgt_state = t.get("target_state", "INITIAL")
            event = t.get("event", t.get("name", "event"))
            src_enum = _to_snake_case(src_state).upper()
            tgt_enum = _to_snake_case(tgt_state).upper()
            event_str = _to_snake_case(event)
            lines.append(f'    ({class_name}State.{src_enum}, "{event_str}"): {class_name}State.{tgt_enum},')
    else:
        lines.append("    # No transitions defined yet")
    lines.append("}")
    lines.append("")
    lines.append("")

    # State machine class
    lines.append(f"class {class_name}Machine:")
    lines.append(f'    """{class_name} state machine implementation.')
    lines.append("")
    lines.append("    Manages state transitions based on events.")
    lines.append('    """')
    lines.append("")
    initial_state = _to_snake_case(states[0]["name"]).upper() if states else "INITIAL"
    lines.append(f"    def __init__(self, initial_state: {class_name}State = {class_name}State.{initial_state}) -> None:")
    lines.append('        """Initialize the state machine."""')
    lines.append("        self.state = initial_state")
    lines.append("        self.history: list = [initial_state]")
    lines.append("        self._callbacks: Dict[str, Callable] = {}")
    lines.append("")
    lines.append(f"    def handle_event(self, event: str) -> {class_name}State:")
    lines.append('        """Process an event and transition to the next state.')
    lines.append("")
    lines.append("        Args:")
    lines.append("            event: The event name to process.")
    lines.append("")
    lines.append("        Returns:")
    lines.append("            The new state after transition.")
    lines.append("")
    lines.append("        Raises:")
    lines.append("            ValueError: If no transition exists for (state, event).")
    lines.append('        """')
    lines.append("        key = (self.state, event)")
    lines.append("        if key not in TRANSITIONS:")
    lines.append('            raise ValueError(')
    lines.append('                f"No transition for state={self.state.name}, event={event}"')
    lines.append("            )")
    lines.append("        old_state = self.state")
    lines.append("        self.state = TRANSITIONS[key]")
    lines.append("        self.history.append(self.state)")
    lines.append("        if event in self._callbacks:")
    lines.append("            self._callbacks[event](old_state, self.state)")
    lines.append("        return self.state")
    lines.append("")
    lines.append("    def on(self, event: str, callback: Callable) -> None:")
    lines.append('        """Register a callback for a specific event."""')
    lines.append("        self._callbacks[event] = callback")
    lines.append("")
    lines.append("    @property")
    lines.append(f"    def current_state(self) -> {class_name}State:")
    lines.append('        """Return the current state."""')
    lines.append("        return self.state")
    lines.append("")

    content = _get_cui_header("python") + "\n"
    content += "\n".join(lines) + "\n"
    content += _get_cui_footer("python") + "\n"
    return content


def _generate_python_test(requirement: dict) -> str:
    """Generate a pytest test function from a requirement."""
    req_title = requirement.get("title") or requirement.get("name", "requirement")
    req_desc = requirement.get("description") or req_title
    req_id = requirement.get("doors_id") or requirement.get("xmi_id") or requirement.get("id", "")
    fn_name = _to_snake_case(req_title)
    # Truncate overly long function names
    if len(fn_name) > 80:
        fn_name = fn_name[:80].rstrip("_")

    lines: List[str] = []
    lines.append(f"def test_{fn_name}():")
    lines.append(f'    """Test for requirement: {req_title}')
    lines.append("")
    lines.append(f"    Requirement ID: {req_id}")
    lines.append(f"    Description: {req_desc}")
    lines.append('    """')
    lines.append(f"    # TODO: Implement test for requirement '{req_title}'")
    lines.append(f'    raise NotImplementedError("Test stub -- implement verification for {req_id}")')
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generation functions
# ---------------------------------------------------------------------------

def generate_from_blocks(project_id: str, language: str = "python",
                         output_dir: str = None, db_path=None) -> dict:
    """Generate classes from SysML blocks.

    Returns:
        {"files_generated": int, "files": [...]}
    """
    conn = _get_connection(db_path)
    try:
        blocks = _fetch_elements(project_id, "block", conn)
        iface_blocks = _fetch_elements(project_id, "interface_block", conn)
        all_block_elements = blocks + iface_blocks

        # Build lookup dict by element id
        all_blocks_map: Dict[str, dict] = {b["id"]: b for b in all_block_elements}

        out = Path(output_dir) if output_dir else Path.cwd() / "generated"
        out.mkdir(parents=True, exist_ok=True)

        files_generated: List[dict] = []

        for block in all_block_elements:
            # Fetch relationships for this block
            rels = _fetch_relationships(project_id, conn, source_id=block["id"])
            # Also fetch incoming generalization (where block is target)
            incoming_gen = conn.execute(
                "SELECT * FROM sysml_relationships WHERE project_id = ? AND target_element_id = ?",
                (project_id, block["id"]),
            ).fetchall()
            all_rels = rels + [dict(r) for r in incoming_gen]

            if language == "python":
                content = _generate_python_class(block, all_rels, all_blocks_map)
            else:
                # For non-Python languages, generate a placeholder using the Python
                # generator pattern but with appropriate CUI headers
                content = _generate_python_class(block, all_rels, all_blocks_map)

            filename = _to_file_name(block["name"], language)
            filepath = out / filename
            code_hash = _write_file(filepath, content)

            code_type = "interface" if block["element_type"] == "interface_block" else "class"
            _record_mapping(project_id, block["id"], str(filepath), code_type, code_hash, conn)
            _record_thread_link(project_id, block["id"], str(filepath), conn)

            files_generated.append({
                "path": str(filepath),
                "element_id": block["id"],
                "element_name": block["name"],
                "code_type": code_type,
                "hash": code_hash,
            })

        conn.commit()
        return {"files_generated": len(files_generated), "files": files_generated}
    finally:
        conn.close()


def generate_from_activities(project_id: str, language: str = "python",
                             output_dir: str = None, db_path=None) -> dict:
    """Generate modules from SysML activities.

    Returns:
        {"files_generated": int, "files": [...]}
    """
    conn = _get_connection(db_path)
    try:
        activities = _fetch_elements(project_id, "activity", conn)
        out = Path(output_dir) if output_dir else Path.cwd() / "generated"
        out.mkdir(parents=True, exist_ok=True)

        files_generated: List[dict] = []

        for activity in activities:
            # Fetch child actions, object_nodes, control_flows under this activity
            children = _fetch_children(activity["id"], conn)
            actions = [
                c for c in children
                if c["element_type"] in ("action", "object_node", "control_flow")
            ]

            if language == "python":
                content = _generate_python_module(activity, actions)
            else:
                content = _generate_python_module(activity, actions)

            filename = _to_file_name(activity["name"], language)
            filepath = out / filename
            code_hash = _write_file(filepath, content)

            _record_mapping(project_id, activity["id"], str(filepath), "module", code_hash, conn)
            _record_thread_link(project_id, activity["id"], str(filepath), conn)

            files_generated.append({
                "path": str(filepath),
                "element_id": activity["id"],
                "element_name": activity["name"],
                "code_type": "module",
                "hash": code_hash,
            })

        conn.commit()
        return {"files_generated": len(files_generated), "files": files_generated}
    finally:
        conn.close()


def generate_from_state_machines(project_id: str, language: str = "python",
                                 output_dir: str = None, db_path=None) -> dict:
    """Generate state machine code from SysML state machines.

    Returns:
        {"files_generated": int, "files": [...]}
    """
    conn = _get_connection(db_path)
    try:
        state_machines = _fetch_elements(project_id, "state_machine", conn)
        out = Path(output_dir) if output_dir else Path.cwd() / "generated"
        out.mkdir(parents=True, exist_ok=True)

        files_generated: List[dict] = []

        for sm in state_machines:
            # Fetch child states
            children = _fetch_children(sm["id"], conn)
            states = [c for c in children if c["element_type"] == "state"]

            # Build transitions from relationships or properties
            sm_props = _parse_properties(sm)
            transitions_data = sm_props.get("transitions", [])

            # Also look at relationships for transitions between states
            if not transitions_data and states:
                for state in states:
                    rels = _fetch_relationships(project_id, conn, source_id=state["id"])
                    for rel in rels:
                        target = _fetch_element_by_id(rel["target_element_id"], conn)
                        if target and target["element_type"] == "state":
                            rel_props = {}
                            if rel.get("properties"):
                                try:
                                    rel_props = json.loads(rel["properties"])
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            transitions_data.append({
                                "source_state": state["name"],
                                "target_state": target["name"],
                                "event": rel.get("name") or rel_props.get("trigger", f"to_{_to_snake_case(target['name'])}"),
                                "name": rel.get("name", ""),
                            })

            if language == "python":
                content = _generate_python_state_machine(sm, states, transitions_data)
            else:
                content = _generate_python_state_machine(sm, states, transitions_data)

            filename = _to_file_name(sm["name"], language)
            filepath = out / filename
            code_hash = _write_file(filepath, content)

            _record_mapping(project_id, sm["id"], str(filepath), "module", code_hash, conn)
            _record_thread_link(project_id, sm["id"], str(filepath), conn)

            files_generated.append({
                "path": str(filepath),
                "element_id": sm["id"],
                "element_name": sm["name"],
                "code_type": "module",
                "hash": code_hash,
            })

        conn.commit()
        return {"files_generated": len(files_generated), "files": files_generated}
    finally:
        conn.close()


def generate_tests_from_requirements(project_id: str, output_dir: str = None,
                                     db_path=None) -> dict:
    """Generate test stubs from DOORS requirements and SysML requirements.

    Returns:
        {"files_generated": int, "test_count": int, "files": [...]}
    """
    conn = _get_connection(db_path)
    try:
        # Fetch SysML requirement elements
        sysml_reqs = _fetch_elements(project_id, "requirement", conn)

        # Fetch DOORS requirements
        doors_reqs: List[dict] = []
        try:
            cur = conn.execute(
                "SELECT * FROM doors_requirements WHERE project_id = ?",
                (project_id,),
            )
            doors_reqs = [dict(row) for row in cur.fetchall()]
        except sqlite3.OperationalError:
            pass  # Table may not exist

        all_reqs = sysml_reqs + doors_reqs
        if not all_reqs:
            return {"files_generated": 0, "test_count": 0, "files": []}

        out = Path(output_dir) if output_dir else Path.cwd() / "generated" / "tests"
        out.mkdir(parents=True, exist_ok=True)

        # Group requirements by module if available, else single test file
        test_functions: List[str] = []
        test_count = 0

        for req in all_reqs:
            test_code = _generate_python_test(req)
            test_functions.append(test_code)
            test_count += 1

        # Build the test file
        lines: List[str] = []
        lines.append('"""Auto-generated test stubs from SysML/DOORS requirements.')
        lines.append("")
        lines.append(f"Project: {project_id}")
        lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}Z")
        lines.append(f"Total requirements: {test_count}")
        lines.append('"""')
        lines.append("")
        lines.append("import pytest")
        lines.append("")
        lines.append("")

        for tf in test_functions:
            lines.append(tf)

        content = _get_cui_header("python") + "\n"
        content += "\n".join(lines) + "\n"
        content += _get_cui_footer("python") + "\n"

        filepath = out / f"test_requirements_{_to_snake_case(project_id)}.py"
        code_hash = _write_file(filepath, content)

        files_generated: List[dict] = [{
            "path": str(filepath),
            "test_count": test_count,
            "hash": code_hash,
        }]

        # Record thread links for each requirement -> test file
        for req in all_reqs:
            req_id = req.get("id", "")
            if req_id:
                source_type = "doors_requirement" if req in doors_reqs else "sysml_element"
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO digital_thread_links
                           (project_id, source_type, source_id, target_type, target_id,
                            link_type, confidence, created_by)
                           VALUES (?, ?, ?, 'test_file', ?, 'verifies', 1.0,
                                   'icdev-model-code-generator')""",
                        (project_id, source_type, req_id, str(filepath)),
                    )
                except sqlite3.Error:
                    pass

            # Record model_code_mapping for SysML requirements
            if req in sysml_reqs and req_id:
                _record_mapping(project_id, req_id, str(filepath), "test", code_hash, conn)

        conn.commit()
        return {
            "files_generated": len(files_generated),
            "test_count": test_count,
            "files": files_generated,
        }
    finally:
        conn.close()


def generate_all(project_id: str, language: str = "python",
                 output_dir: str = None, db_path=None) -> dict:
    """Full generation: blocks + activities + state machines + tests.

    Creates model_code_mappings and digital_thread_links entries.
    Returns combined summary.
    """
    out_base = Path(output_dir) if output_dir else Path.cwd() / "generated"
    src_dir = str(out_base / "src")
    test_dir = str(out_base / "tests")

    results: Dict[str, Any] = {
        "project_id": project_id,
        "language": language,
        "output_dir": str(out_base),
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "blocks": {},
        "activities": {},
        "state_machines": {},
        "tests": {},
        "totals": {
            "files_generated": 0,
            "test_count": 0,
        },
    }

    # Generate blocks -> classes
    blocks_result = generate_from_blocks(
        project_id, language=language, output_dir=src_dir, db_path=db_path
    )
    results["blocks"] = blocks_result
    results["totals"]["files_generated"] += blocks_result["files_generated"]

    # Generate activities -> modules
    activities_result = generate_from_activities(
        project_id, language=language, output_dir=src_dir, db_path=db_path
    )
    results["activities"] = activities_result
    results["totals"]["files_generated"] += activities_result["files_generated"]

    # Generate state machines
    sm_result = generate_from_state_machines(
        project_id, language=language, output_dir=src_dir, db_path=db_path
    )
    results["state_machines"] = sm_result
    results["totals"]["files_generated"] += sm_result["files_generated"]

    # Generate test stubs from requirements
    tests_result = generate_tests_from_requirements(
        project_id, output_dir=test_dir, db_path=db_path
    )
    results["tests"] = tests_result
    results["totals"]["files_generated"] += tests_result["files_generated"]
    results["totals"]["test_count"] = tests_result.get("test_count", 0)

    # Log audit event
    _log_audit_event(project_id, results, db_path)

    return results


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _log_audit_event(project_id: str, results: dict, db_path=None) -> None:
    """Log code-from-model generation to the audit trail."""
    total = results["totals"]["files_generated"]
    all_files: List[str] = []
    for section in ("blocks", "activities", "state_machines", "tests"):
        for f in results.get(section, {}).get("files", []):
            all_files.append(f.get("path", ""))

    if audit_log_event:
        try:
            audit_log_event(
                event_type="code_from_model",
                actor="mbse/model_code_generator",
                action=f"Generated {total} file(s) from SysML model for project {project_id}",
                project_id=project_id,
                details={
                    "language": results.get("language", "python"),
                    "blocks": results["blocks"].get("files_generated", 0),
                    "activities": results["activities"].get("files_generated", 0),
                    "state_machines": results["state_machines"].get("files_generated", 0),
                    "tests": results["tests"].get("files_generated", 0),
                    "test_count": results["totals"].get("test_count", 0),
                },
                affected_files=all_files,
                classification="CUI",
                db_path=db_path,
            )
        except Exception:
            pass  # Non-critical; don't fail generation on audit failure
    else:
        # Fallback: direct SQL insert
        try:
            path = db_path or DB_PATH
            conn = sqlite3.connect(str(path))
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, affected_files, classification)
                   VALUES (?, 'code_from_model', 'mbse/model_code_generator', ?, ?, ?, 'CUI')""",
                (
                    project_id,
                    f"Generated {total} file(s) from SysML model",
                    json.dumps({
                        "language": results.get("language", "python"),
                        "files_generated": total,
                    }),
                    json.dumps(all_files),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for model code generation."""
    parser = argparse.ArgumentParser(
        description="Generate code from SysML model elements"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument(
        "--language", default="python",
        choices=["python", "java", "go", "rust", "csharp", "typescript"],
        help="Target language for generated code (default: python)",
    )
    parser.add_argument("--output", required=True, help="Output directory for generated files")
    parser.add_argument("--blocks-only", action="store_true",
                        help="Only generate classes from SysML blocks")
    parser.add_argument("--activities-only", action="store_true",
                        help="Only generate modules from SysML activities")
    parser.add_argument("--tests-only", action="store_true",
                        help="Only generate test stubs from requirements")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--db-path", type=Path, default=None,
                        help="Override database path (default: data/icdev.db)")

    args = parser.parse_args()

    db = args.db_path if args.db_path else None

    if args.blocks_only:
        result = generate_from_blocks(
            project_id=args.project_id,
            language=args.language,
            output_dir=args.output,
            db_path=db,
        )
    elif args.activities_only:
        result = generate_from_activities(
            project_id=args.project_id,
            language=args.language,
            output_dir=args.output,
            db_path=db,
        )
    elif args.tests_only:
        result = generate_tests_from_requirements(
            project_id=args.project_id,
            output_dir=args.output,
            db_path=db,
        )
    else:
        result = generate_all(
            project_id=args.project_id,
            language=args.language,
            output_dir=args.output,
            db_path=db,
        )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        total = result.get("files_generated", result.get("totals", {}).get("files_generated", 0))
        print(f"Model Code Generator -- {total} file(s) generated")
        files = result.get("files", [])
        if not files:
            # Collect files from sub-sections
            for section in ("blocks", "activities", "state_machines", "tests"):
                section_data = result.get(section, {})
                if isinstance(section_data, dict):
                    files.extend(section_data.get("files", []))
        for f in files:
            path = f.get("path", "unknown")
            ctype = f.get("code_type", "")
            ename = f.get("element_name", "")
            print(f"  [{ctype}] {ename} -> {path}")
        test_count = result.get("test_count", result.get("totals", {}).get("test_count", 0))
        if test_count:
            print(f"  Tests: {test_count} test function(s)")


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
