#!/usr/bin/env python3
# CUI // SP-CTI
"""API Surface Extractor — extract public API from Python modules (D-API-1).

Deterministic AST-based tool that extracts the public API surface of any
Python module: functions, classes, dataclass fields, dict constants, imports,
and mock targets.  stdlib ``ast`` only — air-gap safe, zero deps (D13).

Use this BEFORE writing tests to verify field names, function signatures,
return types, and mock paths.  Prevents the systematic class of errors
where tests guess wrong about module internals.

Usage:
    python tools/testing/api_surface_extractor.py --file tools/rag/source_registry.py --json
    python tools/testing/api_surface_extractor.py --file tools/rag/retriever.py --human
    python tools/testing/api_surface_extractor.py --dir tools/rag/ --json
    python tools/testing/api_surface_extractor.py --file tools/rag/retriever.py --mock-targets
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CUI_BANNER = "CUI // SP-CTI"

EXCLUDE_DIRS = {
    "__pycache__", ".git", "node_modules", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".tmp",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _annotation_text(node, source: str) -> Optional[str]:
    """Extract annotation text from an AST node."""
    if node is None:
        return None
    try:
        seg = ast.get_source_segment(source, node)
        if seg:
            return seg
    except Exception:
        pass
    # Fallback: unparse (Python 3.9+)
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _default_repr(node, source: str) -> Optional[str]:
    """Get a string representation of a default value AST node."""
    if node is None:
        return None
    try:
        seg = ast.get_source_segment(source, node)
        if seg:
            return seg
    except Exception:
        pass
    try:
        return ast.unparse(node)
    except Exception:
        return repr(node)


def _decorator_name(node) -> str:
    """Extract decorator name as string."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        parts = []
        n = node
        while isinstance(n, ast.Attribute):
            parts.append(n.attr)
            n = n.value
        if isinstance(n, ast.Name):
            parts.append(n.id)
        return ".".join(reversed(parts))
    elif isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return "unknown"


def _is_upper_name(name: str) -> bool:
    """Check if a name looks like a constant (UPPER_CASE)."""
    return name.isupper() or (name == name.upper() and "_" in name)


def _extract_dict_keys(node) -> List[str]:
    """Extract string keys from an ast.Dict node."""
    keys = []
    if not isinstance(node, ast.Dict):
        return keys
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.append(k.value)
    return keys


def _extract_dict_value_schema(node) -> List[str]:
    """Extract the key schema from the first dict value in a nested dict."""
    if not isinstance(node, ast.Dict) or not node.values:
        return []
    first_val = node.values[0]
    if isinstance(first_val, ast.Dict):
        return _extract_dict_keys(first_val)
    return []


# ---------------------------------------------------------------------------
# Main visitor
# ---------------------------------------------------------------------------

class APISurfaceVisitor(ast.NodeVisitor):
    """Walk a Python AST and collect the public API surface."""

    def __init__(self, source: str):
        self.source = source
        self.imports: List[Dict[str, Any]] = []
        self.constants: List[Dict[str, Any]] = []
        self.dict_constants: List[Dict[str, Any]] = []
        self.functions: List[Dict[str, Any]] = []
        self.classes: List[Dict[str, Any]] = []
        self.all_exports: Optional[List[str]] = None
        self.future_annotations: bool = False

    # -- Imports --

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append({
                "module": alias.name,
                "names": [alias.name.split(".")[-1]],
                "alias": alias.asname,
                "line": node.lineno,
            })
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        if module == "__future__":
            names = [a.name for a in (node.names or [])]
            if "annotations" in names:
                self.future_annotations = True
            return
        names = []
        for alias in (node.names or []):
            names.append(alias.name)
        self.imports.append({
            "module": module,
            "names": names,
            "alias": None,
            "line": node.lineno,
        })
        self.generic_visit(node)

    # -- Assignments (constants, dicts, __all__) --

    def visit_Assign(self, node):
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id

            # __all__
            if name == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                self.all_exports = []
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        self.all_exports.append(elt.value)
                continue

            # Dict constants (UPPER_CASE or common patterns)
            if isinstance(node.value, ast.Dict) and (_is_upper_name(name) or name.endswith("_REGISTRY") or name.endswith("_MAP")):
                keys = _extract_dict_keys(node.value)
                value_schema = _extract_dict_value_schema(node.value)
                self.dict_constants.append({
                    "name": name,
                    "keys": keys,
                    "key_count": len(keys),
                    "value_schema": value_schema if value_schema else None,
                    "line": node.lineno,
                })
                continue

            # Scalar constants
            if _is_upper_name(name) and isinstance(node.value, ast.Constant):
                val = node.value.value
                self.constants.append({
                    "name": name,
                    "type": type(val).__name__ if val is not None else "NoneType",
                    "value": repr(val),
                    "line": node.lineno,
                })

        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if not isinstance(node.target, ast.Name):
            self.generic_visit(node)
            return
        name = node.target.id
        ann = _annotation_text(node.annotation, self.source)

        if node.value and isinstance(node.value, ast.Dict) and (_is_upper_name(name) or name.endswith("_REGISTRY")):
            keys = _extract_dict_keys(node.value)
            value_schema = _extract_dict_value_schema(node.value)
            self.dict_constants.append({
                "name": name,
                "type_annotation": ann,
                "keys": keys,
                "key_count": len(keys),
                "value_schema": value_schema if value_schema else None,
                "line": node.lineno,
            })
        elif _is_upper_name(name) and node.value and isinstance(node.value, ast.Constant):
            val = node.value.value
            self.constants.append({
                "name": name,
                "type": ann or (type(val).__name__ if val is not None else "NoneType"),
                "value": repr(val),
                "line": node.lineno,
            })

        self.generic_visit(node)

    # -- Functions --

    def visit_FunctionDef(self, node):
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node, is_async=True)

    def _visit_function(self, node, is_async: bool):
        # Only top-level functions (direct children of module)
        params = self._extract_params(node.args)
        return_type = _annotation_text(node.returns, self.source)
        decorators = [_decorator_name(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node)

        self.functions.append({
            "name": node.name,
            "parameters": params,
            "return_type": return_type,
            "decorators": decorators,
            "docstring": docstring.split("\n")[0].strip() if docstring else None,
            "is_async": is_async,
            "is_public": not node.name.startswith("_"),
            "line": node.lineno,
        })
        # Do NOT generic_visit — skip nested functions/classes inside functions

    # -- Classes --

    def visit_ClassDef(self, node):
        decorators = [_decorator_name(d) for d in node.decorator_list]
        is_dataclass = any(
            d in ("dataclass", "dataclasses.dataclass") for d in decorators
        )
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(_decorator_name(base))

        is_enum = any(b in ("Enum", "IntEnum", "StrEnum", "enum.Enum") for b in bases)
        is_abc = any(b in ("ABC", "abc.ABC") for b in bases)

        docstring = ast.get_docstring(node)

        # Extract class body
        init_params: List[Dict[str, Any]] = []
        dataclass_fields: List[Dict[str, Any]] = []
        public_methods: List[Dict[str, Any]] = []
        properties: List[str] = []
        class_methods: List[str] = []
        static_methods: List[str] = []
        enum_members: List[Dict[str, Any]] = []

        for item in node.body:
            # Dataclass fields (annotated assignments in class body)
            if is_dataclass and isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                field_info = self._extract_dataclass_field(item)
                dataclass_fields.append(field_info)

            # Enum members
            elif is_enum and isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name) and isinstance(item.value, ast.Constant):
                        enum_members.append({"name": t.id, "value": repr(item.value.value)})

            # Methods
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_decorators = [_decorator_name(d) for d in item.decorator_list]

                if item.name == "__init__":
                    init_params = self._extract_params(item.args, skip_self=True)

                elif not item.name.startswith("_") or item.name.startswith("__") and item.name.endswith("__"):
                    if "property" in method_decorators:
                        properties.append(item.name)
                    elif "classmethod" in method_decorators:
                        class_methods.append(item.name)
                    elif "staticmethod" in method_decorators:
                        static_methods.append(item.name)
                    else:
                        is_abstract = any(
                            d in ("abstractmethod", "abc.abstractmethod")
                            for d in method_decorators
                        )
                        params = self._extract_params(item.args, skip_self=True)
                        return_type = _annotation_text(item.returns, self.source)
                        method_info: Dict[str, Any] = {
                            "name": item.name,
                            "parameters": params,
                            "return_type": return_type,
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        }
                        if is_abstract:
                            method_info["is_abstract"] = True
                        public_methods.append(method_info)

        cls_info: Dict[str, Any] = {
            "name": node.name,
            "bases": bases,
            "decorators": decorators,
            "is_dataclass": is_dataclass,
            "is_abc": is_abc,
            "is_enum": is_enum,
            "docstring": docstring.split("\n")[0].strip() if docstring else None,
            "init_params": init_params,
            "public_methods": public_methods,
            "properties": properties,
            "class_methods": class_methods,
            "static_methods": static_methods,
            "is_public": not node.name.startswith("_"),
            "line": node.lineno,
        }
        if is_dataclass:
            cls_info["dataclass_fields"] = dataclass_fields
        if is_enum:
            cls_info["enum_members"] = enum_members
        self.classes.append(cls_info)
        # Do NOT generic_visit — skip nested definitions

    # -- Parameter extraction --

    def _extract_params(self, args_node, skip_self: bool = False) -> List[Dict[str, Any]]:
        """Extract function/method parameters with types and defaults."""
        params: List[Dict[str, Any]] = []

        all_args = list(args_node.args)
        defaults = list(args_node.defaults)
        # Pad defaults to match args length
        pad = len(all_args) - len(defaults)
        padded_defaults = [None] * pad + defaults

        for i, arg in enumerate(all_args):
            if skip_self and i == 0 and arg.arg in ("self", "cls"):
                continue
            p: Dict[str, Any] = {"name": arg.arg}
            p["type"] = _annotation_text(arg.annotation, self.source)
            p["default"] = _default_repr(padded_defaults[i], self.source)
            params.append(p)

        # *args
        if args_node.vararg:
            p = {"name": f"*{args_node.vararg.arg}"}
            p["type"] = _annotation_text(args_node.vararg.annotation, self.source)
            p["default"] = None
            params.append(p)

        # Keyword-only args
        kw_defaults = args_node.kw_defaults
        for j, kwarg in enumerate(args_node.kwonlyargs):
            p = {"name": kwarg.arg}
            p["type"] = _annotation_text(kwarg.annotation, self.source)
            p["default"] = _default_repr(kw_defaults[j], self.source) if j < len(kw_defaults) else None
            params.append(p)

        # **kwargs
        if args_node.kwarg:
            p = {"name": f"**{args_node.kwarg.arg}"}
            p["type"] = _annotation_text(args_node.kwarg.annotation, self.source)
            p["default"] = None
            params.append(p)

        return params

    # -- Dataclass field extraction --

    def _extract_dataclass_field(self, node: ast.AnnAssign) -> Dict[str, Any]:
        """Extract a dataclass field from an AnnAssign node."""
        name = node.target.id  # type: ignore[union-attr]
        type_ann = _annotation_text(node.annotation, self.source)
        default = None
        default_factory = None

        if node.value is not None:
            # Check for field(default_factory=...)
            if isinstance(node.value, ast.Call):
                func_name = ""
                if isinstance(node.value.func, ast.Name):
                    func_name = node.value.func.id
                elif isinstance(node.value.func, ast.Attribute):
                    func_name = node.value.func.attr
                if func_name == "field":
                    for kw in node.value.keywords:
                        if kw.arg == "default_factory":
                            default_factory = _default_repr(kw.value, self.source)
                        elif kw.arg == "default":
                            default = _default_repr(kw.value, self.source)
                else:
                    default = _default_repr(node.value, self.source)
            else:
                default = _default_repr(node.value, self.source)

        return {
            "name": name,
            "type": type_ann,
            "default": default,
            "default_factory": default_factory,
        }


# ---------------------------------------------------------------------------
# Extraction orchestrators
# ---------------------------------------------------------------------------

def _compute_mock_targets(imports: List[Dict[str, Any]]) -> List[str]:
    """Compute mock.patch target paths from imports."""
    targets = []
    for imp in imports:
        module = imp["module"]
        for name in imp["names"]:
            if name == "*":
                continue
            targets.append(f"{module}.{name}")
    return targets


def _compute_module_path(file_path: str) -> str:
    """Compute Python module path from file path."""
    p = Path(file_path)
    try:
        rel = p.relative_to(BASE_DIR)
    except ValueError:
        rel = p
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def extract_api_surface(
    file_path: str,
    include_private: bool = False,
) -> Dict[str, Any]:
    """Extract the public API surface from a Python file.

    Args:
        file_path: Path to the .py file.
        include_private: Include _prefixed names (default False).

    Returns:
        Dict with module_path, imports, constants, dict_constants,
        functions, classes, all_exports, mock_targets.
    """
    fp = Path(file_path)
    if not fp.exists():
        return {"error": f"File not found: {file_path}"}
    if not fp.suffix == ".py":
        return {"error": f"Not a Python file: {file_path}"}

    source = fp.read_text(encoding="utf-8", errors="replace")

    try:
        tree = ast.parse(source, filename=str(fp))
    except SyntaxError as e:
        return {
            "error": f"SyntaxError: {e}",
            "file_path": str(fp),
            "line": getattr(e, "lineno", 0),
        }

    visitor = APISurfaceVisitor(source)
    # Only visit top-level nodes (not nested)
    for node in ast.iter_child_nodes(tree):
        visitor.visit(node)

    # Filter private names unless requested
    functions = visitor.functions
    classes = visitor.classes
    if not include_private:
        functions = [f for f in functions if f["is_public"]]
        classes = [c for c in classes if c["is_public"]]

    mock_targets = _compute_mock_targets(visitor.imports)

    return {
        "classification": CUI_BANNER,
        "file_path": str(fp),
        "module_path": _compute_module_path(str(fp)),
        "future_annotations": visitor.future_annotations,
        "all_exports": visitor.all_exports,
        "imports": visitor.imports,
        "constants": visitor.constants,
        "dict_constants": visitor.dict_constants,
        "functions": functions,
        "classes": classes,
        "mock_targets": mock_targets,
    }


def extract_directory(
    dir_path: str,
    include_private: bool = False,
) -> Dict[str, Any]:
    """Extract API surfaces from all Python files in a directory.

    Returns:
        Dict with per-file surfaces keyed by relative path.
    """
    dp = Path(dir_path)
    if not dp.is_dir():
        return {"error": f"Not a directory: {dir_path}"}

    results: Dict[str, Any] = {}
    for root, dirs, files in os.walk(dp):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            fpath = Path(root) / fname
            try:
                if fpath.stat().st_size > 1_048_576:  # Skip >1MB files
                    continue
            except OSError:
                continue
            rel = str(fpath.relative_to(dp))
            results[rel] = extract_api_surface(str(fpath), include_private=include_private)

    return {
        "classification": CUI_BANNER,
        "directory": str(dp),
        "file_count": len(results),
        "files": results,
    }


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------

def format_markdown(surface: Dict[str, Any]) -> str:
    """Format API surface as concise human-readable markdown."""
    if "error" in surface:
        return f"ERROR: {surface['error']}\n"

    lines: List[str] = []
    fp = surface.get("file_path", "unknown")
    mp = surface.get("module_path", "")
    lines.append(f"## {fp}")
    lines.append(f"Module: `{mp}`")
    if surface.get("future_annotations"):
        lines.append("Note: `from __future__ import annotations` active")
    if surface.get("all_exports") is not None:
        lines.append(f"__all__: {surface['all_exports']}")
    lines.append("")

    # Imports
    if surface.get("imports"):
        lines.append("### Imports")
        for imp in surface["imports"]:
            names = ", ".join(imp["names"])
            lines.append(f"- `from {imp['module']} import {names}` (line {imp['line']})")
        lines.append("")

    # Constants
    if surface.get("constants"):
        lines.append("### Constants")
        for c in surface["constants"]:
            lines.append(f"- `{c['name']}: {c['type']} = {c['value']}` (line {c['line']})")
        lines.append("")

    # Dict Constants
    if surface.get("dict_constants"):
        lines.append("### Dict Constants")
        for dc in surface["dict_constants"]:
            keys_preview = ", ".join(dc["keys"][:8])
            if dc["key_count"] > 8:
                keys_preview += ", ..."
            lines.append(f"- `{dc['name']}` ({dc['key_count']} keys): {keys_preview}")
            if dc.get("value_schema"):
                schema = ", ".join(dc["value_schema"])
                lines.append(f"  Value schema: {{{schema}}}")
        lines.append("")

    # Functions
    if surface.get("functions"):
        lines.append("### Functions")
        for fn in surface["functions"]:
            params_str = ", ".join(
                _format_param(p) for p in fn["parameters"]
            )
            ret = f" -> {fn['return_type']}" if fn.get("return_type") else ""
            async_prefix = "async " if fn.get("is_async") else ""
            lines.append(f"- `{async_prefix}{fn['name']}({params_str}){ret}` (line {fn['line']})")
            if fn.get("docstring"):
                lines.append(f"  {fn['docstring']}")
        lines.append("")

    # Classes
    if surface.get("classes"):
        lines.append("### Classes")
        for cls in surface["classes"]:
            bases_str = f"({', '.join(cls['bases'])})" if cls["bases"] else ""
            tags = []
            if cls.get("is_dataclass"):
                tags.append("dataclass")
            if cls.get("is_abc"):
                tags.append("ABC")
            if cls.get("is_enum"):
                tags.append("Enum")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"#### {cls['name']}{bases_str}{tag_str} (line {cls['line']})")
            if cls.get("docstring"):
                lines.append(f"  {cls['docstring']}")

            if cls.get("dataclass_fields"):
                lines.append("  Fields:")
                for f in cls["dataclass_fields"]:
                    default_str = ""
                    if f.get("default_factory"):
                        default_str = f" = field(default_factory={f['default_factory']})"
                    elif f.get("default") is not None:
                        default_str = f" = {f['default']}"
                    lines.append(f"  - `{f['name']}: {f['type']}{default_str}`")

            if cls.get("init_params") and not cls.get("is_dataclass"):
                lines.append("  __init__ params:")
                for p in cls["init_params"]:
                    lines.append(f"  - `{_format_param(p)}`")

            if cls.get("enum_members"):
                lines.append("  Members:")
                for m in cls["enum_members"]:
                    lines.append(f"  - `{m['name']} = {m['value']}`")

            if cls.get("public_methods"):
                lines.append("  Methods:")
                for m in cls["public_methods"]:
                    params_str = ", ".join(_format_param(p) for p in m.get("parameters", []))
                    ret = f" -> {m['return_type']}" if m.get("return_type") else ""
                    abstract = " [abstract]" if m.get("is_abstract") else ""
                    lines.append(f"  - `{m['name']}({params_str}){ret}`{abstract}")

            if cls.get("properties"):
                lines.append(f"  Properties: {', '.join(cls['properties'])}")
            if cls.get("class_methods"):
                lines.append(f"  Class methods: {', '.join(cls['class_methods'])}")
            if cls.get("static_methods"):
                lines.append(f"  Static methods: {', '.join(cls['static_methods'])}")
            lines.append("")

    # Mock targets
    if surface.get("mock_targets"):
        lines.append("### Mock Targets")
        for t in surface["mock_targets"]:
            lines.append(f"- `{t}`")
        lines.append("")

    return "\n".join(lines)


def _format_param(p: Dict[str, Any]) -> str:
    """Format a parameter dict as a concise string."""
    s = p["name"]
    if p.get("type"):
        s += f": {p['type']}"
    if p.get("default") is not None:
        s += f" = {p['default']}"
    return s


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract public API surface from Python modules (D-API-1)",
    )
    parser.add_argument("--file", help="Extract from a single Python file")
    parser.add_argument("--dir", help="Extract from all Python files in a directory")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output (default)")
    parser.add_argument("--human", action="store_true",
                        help="Human-readable markdown output")
    parser.add_argument("--mock-targets", action="store_true",
                        help="Only output mock targets")
    parser.add_argument("--include-private", action="store_true",
                        help="Include _prefixed names")

    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.error("Specify --file or --dir")

    if args.file:
        result = extract_api_surface(args.file, include_private=args.include_private)
    else:
        result = extract_directory(args.dir, include_private=args.include_private)

    if args.mock_targets:
        if "files" in result:
            # Directory mode — collect all mock targets
            all_targets: List[str] = []
            for _fp, surface in result.get("files", {}).items():
                all_targets.extend(surface.get("mock_targets", []))
            print(json.dumps({"mock_targets": sorted(set(all_targets))}, indent=2))
        else:
            print(json.dumps({"mock_targets": result.get("mock_targets", [])}, indent=2))
    elif args.human:
        if "files" in result:
            for _fp, surface in result.get("files", {}).items():
                print(format_markdown(surface))
        else:
            print(format_markdown(result))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
