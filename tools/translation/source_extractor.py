#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D -- Authorized DoD Personnel Only
# POC: ICDEV System Administrator
"""Source Extractor — ICDEV Cross-Language Translation (Phase 43, D243)

Phase 1 of the 5-phase hybrid translation pipeline.
Parses source code into a language-agnostic Intermediate Representation (IR)
as JSON. Uses Python's ast module for Python, regex-based parsing for
Java, Go, Rust, C#, and TypeScript (D13 — air-gap safe, zero deps).

Each IR unit represents a function, class, interface, or enum with:
  - name, kind, signature, source code, dependencies, line range
  - detected idioms, complexity metrics, concurrency patterns

Post-order dependency graph traversal (D244) — leaf nodes translated first.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import textwrap
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

CUI_BANNER = "CUI // SP-CTI"

SUPPORTED_LANGUAGES = {
    "python": [".py"],
    "java": [".java"],
    "javascript": [".js", ".jsx"],
    "typescript": [".ts", ".tsx"],
    "go": [".go"],
    "rust": [".rs"],
    "csharp": [".cs"],
}

EXCLUDE_DIRS = {
    "__pycache__", ".git", "node_modules", "target", "bin", "obj",
    ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".tmp", "vendor",
}

EXCLUDE_FILES = {"__init__.py", "setup.py", "conftest.py"}

# ---------------------------------------------------------------------------
# Python extractor (ast-based)
# ---------------------------------------------------------------------------


def _extract_python(source_code, file_path):
    """Extract IR units from Python source using AST."""
    units = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return [{
            "kind": "error",
            "name": str(file_path),
            "error": f"SyntaxError: {e}",
            "line_start": getattr(e, "lineno", 0),
        }]

    lines = source_code.split("\n")
    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            unit = _python_function_to_ir(node, lines, source_code)
            unit["file_path"] = str(file_path)
            units.append(unit)
        elif isinstance(node, ast.ClassDef):
            unit = _python_class_to_ir(node, lines, source_code)
            unit["file_path"] = str(file_path)
            units.append(unit)

    return units, imports


def _python_function_to_ir(node, lines, full_source):
    """Convert a Python function AST node to IR unit."""
    params = []
    for arg in node.args.args:
        param = {"name": arg.arg, "type": None}
        if arg.annotation:
            param["type"] = ast.get_source_segment(full_source, arg.annotation)
        params.append(param)

    return_type = None
    if node.returns:
        return_type = ast.get_source_segment(full_source, node.returns)

    line_start = node.lineno
    line_end = node.end_lineno or node.lineno
    source_lines = lines[line_start - 1: line_end]
    source_text = "\n".join(source_lines)

    # Detect idioms
    idioms = _detect_python_idioms(source_text)

    # Complexity (rough McCabe: count branches)
    complexity = _count_branches(source_text)

    is_async = isinstance(node, ast.AsyncFunctionDef)

    return {
        "kind": "function",
        "name": node.name,
        "is_async": is_async,
        "parameters": params,
        "return_type": return_type,
        "line_start": line_start,
        "line_end": line_end,
        "line_count": line_end - line_start + 1,
        "source_code": source_text,
        "source_hash": hashlib.sha256(source_text.encode()).hexdigest()[:16],
        "idioms": idioms,
        "complexity": complexity,
        "dependencies": [],
        "decorators": [
            ast.get_source_segment(full_source, d) or ""
            for d in node.decorator_list
        ],
    }


def _python_class_to_ir(node, lines, full_source):
    """Convert a Python class AST node to IR unit."""
    line_start = node.lineno
    line_end = node.end_lineno or node.lineno
    source_lines = lines[line_start - 1: line_end]
    source_text = "\n".join(source_lines)

    bases = []
    for base in node.bases:
        base_name = ast.get_source_segment(full_source, base)
        if base_name:
            bases.append(base_name)

    methods = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append({
                "name": item.name,
                "is_async": isinstance(item, ast.AsyncFunctionDef),
                "line_start": item.lineno,
                "line_end": item.end_lineno or item.lineno,
            })

    return {
        "kind": "class",
        "name": node.name,
        "bases": bases,
        "methods": methods,
        "method_count": len(methods),
        "line_start": line_start,
        "line_end": line_end,
        "line_count": line_end - line_start + 1,
        "source_code": source_text,
        "source_hash": hashlib.sha256(source_text.encode()).hexdigest()[:16],
        "idioms": _detect_python_idioms(source_text),
        "complexity": _count_branches(source_text),
        "dependencies": [],
        "decorators": [
            ast.get_source_segment(full_source, d) or ""
            for d in node.decorator_list
        ],
    }


def _detect_python_idioms(source_text):
    """Detect Python-specific idioms in source code."""
    idioms = []
    if re.search(r"\[.*\bfor\b.*\bin\b.*\]", source_text):
        idioms.append("list_comprehension")
    if re.search(r"\{.*:\s*.*\bfor\b.*\bin\b.*\}", source_text):
        idioms.append("dict_comprehension")
    if re.search(r"\bwith\b\s+\w+.*\bas\b", source_text):
        idioms.append("context_manager")
    if re.search(r"\byield\b", source_text):
        idioms.append("generator")
    if re.search(r"@\w+", source_text):
        idioms.append("decorator")
    if re.search(r"\basync\s+def\b", source_text):
        idioms.append("async_function")
    if re.search(r"\bawait\b", source_text):
        idioms.append("await")
    if re.search(r"\blambda\b", source_text):
        idioms.append("lambda")
    if re.search(r"\*args|\*\*kwargs", source_text):
        idioms.append("variadic_args")
    if re.search(r"@property", source_text):
        idioms.append("property")
    if re.search(r"@staticmethod|@classmethod", source_text):
        idioms.append("static_or_class_method")
    return idioms


# ---------------------------------------------------------------------------
# Regex-based extractors (Java, Go, Rust, C#, TypeScript)
# ---------------------------------------------------------------------------


def _extract_regex(source_code, file_path, language):
    """Regex-based IR extraction for non-Python languages (D13)."""
    units = []
    imports = []
    lines = source_code.split("\n")

    # Import extraction
    import_patterns = {
        "java": r"^\s*import\s+([\w.]+(?:\.\*)?)\s*;",
        "go": r'^\s*"([\w/.-]+)"',
        "rust": r"^\s*use\s+([\w:]+(?:::\*)?)\s*;",
        "csharp": r"^\s*using\s+([\w.]+)\s*;",
        "javascript": r'(?:import\s+.*\s+from\s+["\']([^"\']+)["\']|require\(["\']([^"\']+)["\']\))',
        "typescript": r'(?:import\s+.*\s+from\s+["\']([^"\']+)["\']|require\(["\']([^"\']+)["\']\))',
    }
    imp_pat = import_patterns.get(language, "")
    if imp_pat:
        for line in lines:
            m = re.match(imp_pat, line)
            if m:
                imports.append(m.group(1) or (m.group(2) if m.lastindex > 1 else m.group(1)))

    # Function/method extraction
    func_patterns = {
        "java": r"^\s*(?:public|private|protected|static|final|abstract|synchronized|\s)*\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
        "go": r"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(([^)]*)\)\s*(?:\(([^)]*)\)|(\w+))?\s*\{",
        "rust": r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)\s*(?:->\s*([^\{]+))?\s*\{",
        "csharp": r"^\s*(?:public|private|protected|internal|static|virtual|override|abstract|async|\s)*\s+(\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)\s*\{",
        "javascript": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
        "typescript": r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)(?:\s*:\s*([^\{]+))?\s*\{",
    }

    # Class/struct/interface extraction
    class_patterns = {
        "java": r"^\s*(?:public|private|protected|abstract|final|\s)*\s*(?:class|interface|enum)\s+(\w+)(?:<[^>]+>)?(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?\s*\{",
        "go": r"^\s*type\s+(\w+)\s+struct\s*\{",
        "rust": r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)(?:<[^>]+>)?\s*(?:where[^{]*)?\{",
        "csharp": r"^\s*(?:public|private|protected|internal|abstract|sealed|static|\s)*\s*(?:class|interface|struct|enum)\s+(\w+)(?:<[^>]+>)?(?:\s*:\s*([\w,\s<>]+))?\s*\{",
        "javascript": r"^\s*(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?\s*\{",
        "typescript": r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)(?:<[^>]+>)?(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s<>]+))?\s*\{",
    }

    func_pat = func_patterns.get(language, "")
    class_pat = class_patterns.get(language, "")

    # Extract functions
    if func_pat:
        for i, line in enumerate(lines):
            m = re.match(func_pat, line)
            if m:
                groups = m.groups()
                func_name = groups[1] if language in ("java", "csharp") else groups[0]
                end_line = _find_block_end(lines, i)
                source_text = "\n".join(lines[i:end_line + 1])
                units.append({
                    "kind": "function",
                    "name": func_name,
                    "is_async": "async" in line,
                    "parameters": _parse_params_regex(groups),
                    "return_type": _parse_return_type(groups, language),
                    "line_start": i + 1,
                    "line_end": end_line + 1,
                    "line_count": end_line - i + 1,
                    "source_code": source_text,
                    "source_hash": hashlib.sha256(source_text.encode()).hexdigest()[:16],
                    "idioms": _detect_language_idioms(source_text, language),
                    "complexity": _count_branches(source_text),
                    "dependencies": [],
                    "file_path": str(file_path),
                })

    # Extract classes/structs
    if class_pat:
        for i, line in enumerate(lines):
            m = re.match(class_pat, line)
            if m:
                class_name = m.group(1)
                end_line = _find_block_end(lines, i)
                source_text = "\n".join(lines[i:end_line + 1])
                units.append({
                    "kind": "class",
                    "name": class_name,
                    "bases": [b.strip() for b in (m.group(2) or "").split(",") if b.strip()] if m.lastindex >= 2 else [],
                    "methods": [],
                    "method_count": source_text.count("func ") + source_text.count("def ") + source_text.count("fn "),
                    "line_start": i + 1,
                    "line_end": end_line + 1,
                    "line_count": end_line - i + 1,
                    "source_code": source_text,
                    "source_hash": hashlib.sha256(source_text.encode()).hexdigest()[:16],
                    "idioms": _detect_language_idioms(source_text, language),
                    "complexity": _count_branches(source_text),
                    "dependencies": [],
                    "file_path": str(file_path),
                })

    return units, imports


def _find_block_end(lines, start_idx):
    """Find the end of a brace-delimited block."""
    depth = 0
    for i in range(start_idx, len(lines)):
        line = lines[i]
        # Simple brace counting (doesn't handle strings/comments perfectly)
        depth += line.count("{") - line.count("}")
        if depth <= 0 and i > start_idx:
            return i
    return min(start_idx + 50, len(lines) - 1)  # Fallback: max 50 lines


def _parse_params_regex(groups):
    """Parse parameters from regex groups (simplified)."""
    # Return raw param string as single entry for now
    for g in groups:
        if g and "," in str(g):
            return [{"name": p.strip(), "type": None} for p in g.split(",") if p.strip()]
    return []


def _parse_return_type(groups, language):
    """Extract return type from regex groups."""
    if language in ("go", "rust", "typescript") and len(groups) >= 3:
        return groups[2].strip() if groups[2] else None
    if language in ("java", "csharp") and len(groups) >= 1:
        return groups[0]
    return None


def _detect_language_idioms(source_text, language):
    """Detect language-specific idioms."""
    idioms = []
    if language == "java":
        if re.search(r"\.stream\(\)", source_text):
            idioms.append("stream_api")
        if re.search(r"Optional<", source_text):
            idioms.append("optional")
        if re.search(r"@Override", source_text):
            idioms.append("override")
    elif language == "go":
        if re.search(r"\bgo\s+\w+\(", source_text):
            idioms.append("goroutine")
        if re.search(r"\bmake\(chan\b|<-", source_text):
            idioms.append("channel")
        if re.search(r"\bdefer\b", source_text):
            idioms.append("defer")
        if re.search(r"err\s*!=\s*nil", source_text):
            idioms.append("error_handling")
    elif language == "rust":
        if re.search(r"\.unwrap\(\)|\.expect\(", source_text):
            idioms.append("unwrap")
        if re.search(r"Option<|Result<", source_text):
            idioms.append("option_result")
        if re.search(r"\bmatch\b", source_text):
            idioms.append("pattern_matching")
        if re.search(r"impl\s+\w+\s+for", source_text):
            idioms.append("trait_impl")
    elif language == "csharp":
        if re.search(r"\.Where\(|\.Select\(", source_text):
            idioms.append("linq")
        if re.search(r"\basync\b.*Task", source_text):
            idioms.append("async_task")
        if re.search(r"\bvar\b", source_text):
            idioms.append("var_inference")
    return idioms


def _count_branches(source_text):
    """Count approximate cyclomatic complexity."""
    branch_keywords = [
        r"\bif\b", r"\belif\b", r"\belse\s+if\b", r"\bfor\b",
        r"\bwhile\b", r"\bcase\b", r"\bcatch\b", r"\bexcept\b",
        r"\b\?\b", r"\?\?", r"&&", r"\|\|",
    ]
    count = 1  # Base complexity
    for kw in branch_keywords:
        count += len(re.findall(kw, source_text))
    return count


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------


def extract_source(source_path, language, max_file_size=500000, exclude_tests=False):
    """Extract IR from all source files in a directory.

    Returns dict with:
        units, imports, file_count, total_lines, total_units, language
    """
    source_path = Path(source_path)
    language = language.lower()

    if language not in SUPPORTED_LANGUAGES:
        return {"error": f"Unsupported language: {language}", "supported": list(SUPPORTED_LANGUAGES.keys())}

    extensions = SUPPORTED_LANGUAGES[language]
    all_units = []
    all_imports = set()
    file_count = 0
    total_lines = 0

    if source_path.is_file():
        files = [source_path]
    else:
        files = []
        for ext in extensions:
            files.extend(source_path.rglob(f"*{ext}"))

    for fpath in sorted(files):
        # Skip excluded directories
        if any(excluded in fpath.parts for excluded in EXCLUDE_DIRS):
            continue
        if fpath.name in EXCLUDE_FILES:
            continue
        if exclude_tests and ("test" in fpath.name.lower() or "test" in str(fpath.parent).lower()):
            continue

        # Skip large files
        try:
            size = fpath.stat().st_size
        except OSError:
            continue
        if size > max_file_size:
            continue

        try:
            source_code = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        file_count += 1
        total_lines += source_code.count("\n") + 1

        rel_path = fpath.relative_to(source_path) if source_path.is_dir() else fpath.name

        if language == "python":
            result = _extract_python(source_code, rel_path)
            if isinstance(result, tuple):
                units, imports = result
            else:
                units, imports = result, []
        else:
            units, imports = _extract_regex(source_code, rel_path, language)

        all_units.extend(units)
        all_imports.update(imports)

    return {
        "language": language,
        "source_path": str(source_path),
        "file_count": file_count,
        "total_lines": total_lines,
        "total_units": len(all_units),
        "units": all_units,
        "imports": sorted(all_imports),
    }


def build_dependency_graph(units):
    """Build dependency graph from IR units for post-order traversal (D244).

    Returns list of units in translation order (leaf-first).
    """
    # Build name -> unit index map
    name_map = {}
    for i, unit in enumerate(units):
        name_map[unit["name"]] = i

    # Build adjacency list (unit depends on which other units)
    deps = {i: set() for i in range(len(units))}
    for i, unit in enumerate(units):
        source = unit.get("source_code", "")
        for name, idx in name_map.items():
            if idx != i and re.search(rf"\b{re.escape(name)}\b", source):
                deps[i].add(idx)

    # Topological sort (post-order: leaves first)
    visited = set()
    order = []

    def dfs(node):
        if node in visited:
            return
        visited.add(node)
        for dep in deps[node]:
            dfs(dep)
        order.append(node)

    for i in range(len(units)):
        dfs(i)

    return [units[i] for i in order]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{CUI_BANNER}\n"
            "ICDEV Source Extractor — Phase 1: AST/Regex -> IR JSON (D243)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
            Examples:
              python tools/translation/source_extractor.py \\
                --source-path /path/to/src --language python \\
                --output-ir ir.json --project-id "proj-123" --json

            {CUI_BANNER}
        """),
    )
    parser.add_argument("--source-path", required=True, help="Source code directory or file")
    parser.add_argument("--language", required=True, choices=list(SUPPORTED_LANGUAGES.keys()),
                        help="Source language")
    parser.add_argument("--output-ir", help="Output IR JSON file path")
    parser.add_argument("--project-id", default="", help="Project ID for audit trail")
    parser.add_argument("--exclude-tests", action="store_true", help="Exclude test files")
    parser.add_argument("--max-file-size", type=int, default=500000, help="Max file size in bytes")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    source_path = Path(args.source_path).resolve()
    if not source_path.exists():
        print(f"[ERROR] Source path does not exist: {source_path}", file=sys.stderr)
        sys.exit(1)

    result = extract_source(
        source_path,
        args.language,
        max_file_size=args.max_file_size,
        exclude_tests=args.exclude_tests,
    )

    # Build dependency order
    if "units" in result and result["units"]:
        ordered_units = build_dependency_graph(result["units"])
        result["units"] = ordered_units
        result["dependency_order"] = [u["name"] for u in ordered_units]

    # Write IR to file
    if args.output_ir:
        output_path = Path(args.output_ir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        result["output_ir_path"] = str(output_path)

    # Audit trail
    if args.project_id:
        try:
            sys.path.insert(0, str(BASE_DIR))
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="translation.extract",
                actor="source-extractor",
                action=f"Extracted {result.get('total_units', 0)} units from {args.language} source",
                project_id=args.project_id,
                details={
                    "language": args.language,
                    "file_count": result.get("file_count", 0),
                    "total_units": result.get("total_units", 0),
                    "total_lines": result.get("total_lines", 0),
                },
                classification="CUI",
            )
        except Exception:
            pass  # Audit trail is best-effort

    if args.json_output:
        # Don't include full source code in CLI output (too large)
        summary = dict(result)
        if "units" in summary:
            summary["units"] = [
                {k: v for k, v in u.items() if k != "source_code"}
                for u in summary["units"]
            ]
        print(json.dumps(summary, indent=2))
    else:
        print(f"[INFO] Extracted {result.get('total_units', 0)} units from "
              f"{result.get('file_count', 0)} {args.language} files "
              f"({result.get('total_lines', 0)} lines)")
        if args.output_ir:
            print(f"[INFO] IR written to: {args.output_ir}")


if __name__ == "__main__":
    main()
