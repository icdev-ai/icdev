# CUI // SP-CTI
#!/usr/bin/env python3
"""Legacy Code Static Analysis Engine for ICDEV DoD Modernization.

Performs comprehensive static analysis of legacy applications written in Python,
Java, and C#. Extracts components (classes, modules, functions), dependencies
(imports, inheritance, injection), API endpoints, and computes software quality
metrics (cyclomatic complexity, tech debt, maintainability index).

Results are stored in the ICDEV operational database for downstream migration
assessment by the 7Rs strategy scorer.

Usage:
    # Register a legacy application
    python tools/modernization/legacy_analyzer.py \\
        --register --project-id proj-123 --name "my-app" --source-path /path/to/src

    # Run full analysis
    python tools/modernization/legacy_analyzer.py \\
        --analyze --project-id proj-123 --app-id lapp-xxxx

    # JSON output
    python tools/modernization/legacy_analyzer.py \\
        --analyze --project-id proj-123 --app-id lapp-xxxx --json

Classification: CUI // SP-CTI
"""

import argparse
import ast
import collections
import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# File extensions mapped to language identifiers
LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".java": "java",
    ".cs": "csharp",
    ".js": "javascript",
    ".ts": "typescript",
    ".rb": "ruby",
    ".go": "golang",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".vb": "vbnet",
    ".sql": "sql",
    ".xml": "xml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".properties": "properties",
    ".jsp": "java",
    ".aspx": "csharp",
    ".cshtml": "csharp",
    ".xaml": "csharp",
}

# Comment patterns per language family
COMMENT_PATTERNS = {
    "hash": {"line": "#"},                       # Python, Ruby, YAML
    "slash": {"line": "//", "block_start": "/*", "block_end": "*/"},  # Java, C#, JS, TS, C/C++, Go
}

LANGUAGE_COMMENT_STYLE = {
    "python": "hash",
    "ruby": "hash",
    "yaml": "hash",
    "java": "slash",
    "csharp": "slash",
    "javascript": "slash",
    "typescript": "slash",
    "golang": "slash",
    "cpp": "slash",
    "c": "slash",
    "vbnet": "hash",  # simplified — VB uses ' but close enough
}

# Productivity rate for tech debt estimation (LOC per hour)
PRODUCTIVITY_RATE = 20.0


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _get_db():
    """Return a sqlite3 connection to the ICDEV operational database.

    The database file must already exist (created by tools/db/init_icdev_db.py).
    Uses row_factory = sqlite3.Row for dict-like access.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"ICDEV database not found at {DB_PATH}. "
            "Run 'python tools/db/init_icdev_db.py' first."
        )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Line counting
# ---------------------------------------------------------------------------

def _count_lines(file_path):
    """Count total, code, comment, and blank lines in a source file.

    Handles:
      - '#' line comments for Python/Ruby/shell
      - '//' line comments for Java/C#/JS/TS/Go/C++
      - '/* ... */' block comments for Java/C#/JS/TS/Go/C++

    Args:
        file_path: Path object or string to the source file.

    Returns:
        dict with keys: total, code, comment, blank
    """
    file_path = Path(file_path)
    ext = file_path.suffix.lower()
    lang = LANGUAGE_EXTENSIONS.get(ext)
    style = LANGUAGE_COMMENT_STYLE.get(lang)

    total = 0
    code = 0
    comment = 0
    blank = 0
    in_block_comment = False

    try:
        with open(str(file_path), "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                total += 1
                stripped = raw_line.strip()

                if not stripped:
                    blank += 1
                    continue

                # Block comment handling (slash-style languages)
                if style == "slash":
                    if in_block_comment:
                        comment += 1
                        if "*/" in stripped:
                            in_block_comment = False
                        continue

                    if stripped.startswith("/*"):
                        comment += 1
                        if "*/" not in stripped or stripped.endswith("/*"):
                            in_block_comment = True
                        continue

                    if stripped.startswith("//"):
                        comment += 1
                        continue

                elif style == "hash":
                    if stripped.startswith("#"):
                        comment += 1
                        continue

                # If we reach here it is a code line (may contain inline comment)
                code += 1

    except (OSError, IOError) as exc:
        print(f"[WARN] Could not read {file_path}: {exc}")
        return {"total": 0, "code": 0, "comment": 0, "blank": 0}

    return {"total": total, "code": code, "comment": comment, "blank": blank}


# ---------------------------------------------------------------------------
# Application registration
# ---------------------------------------------------------------------------

def register_application(project_id, name, source_path, description=None):
    """Register a legacy application in the ICDEV database.

    Walks the source_path directory tree, counts files by extension, computes
    aggregate LOC metrics, detects the primary language, and generates a
    source hash (SHA-256 of all file paths + sizes for change detection).

    Args:
        project_id: Parent ICDEV project ID (must exist in projects table).
        name: Human-readable name for the legacy application.
        source_path: Absolute path to the application source root.
        description: Optional description text.

    Returns:
        dict with the registered application record.
    """
    source_path = Path(source_path).resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(f"Source path does not exist or is not a directory: {source_path}")

    app_id = f"lapp-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Walk source tree and aggregate stats
    ext_counts = collections.Counter()
    loc_total = 0
    loc_code = 0
    loc_comment = 0
    loc_blank = 0
    file_count = 0
    hash_material = hashlib.sha256()

    for root, _dirs, files in os.walk(str(source_path)):
        for fname in files:
            fpath = Path(root) / fname
            ext = fpath.suffix.lower()

            if ext not in LANGUAGE_EXTENSIONS:
                continue

            file_count += 1
            ext_counts[ext] += 1

            # Hash: path relative to source root + file size
            try:
                rel = str(fpath.relative_to(source_path))
                fsize = fpath.stat().st_size
                hash_material.update(f"{rel}:{fsize}\n".encode("utf-8"))
            except (OSError, ValueError):
                pass

            counts = _count_lines(fpath)
            loc_total += counts["total"]
            loc_code += counts["code"]
            loc_comment += counts["comment"]
            loc_blank += counts["blank"]

    # Determine primary language from extension counts
    lang_counts = collections.Counter()
    for ext, cnt in ext_counts.items():
        lang = LANGUAGE_EXTENSIONS.get(ext, "unknown")
        lang_counts[lang] += cnt

    primary_language = "unknown"
    if lang_counts:
        primary_language = lang_counts.most_common(1)[0][0]

    source_hash = hash_material.hexdigest()

    # Insert into database
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO legacy_applications
               (id, project_id, name, description, source_path, primary_language,
                analysis_status, loc_total, loc_code, loc_comment, loc_blank,
                file_count, source_hash, registered_at)
               VALUES (?, ?, ?, ?, ?, ?, 'registered', ?, ?, ?, ?, ?, ?, ?)""",
            (
                app_id, project_id, name, description, str(source_path),
                primary_language, loc_total, loc_code, loc_comment, loc_blank,
                file_count, source_hash, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = {
        "id": app_id,
        "project_id": project_id,
        "name": name,
        "description": description,
        "source_path": str(source_path),
        "primary_language": primary_language,
        "analysis_status": "registered",
        "loc_total": loc_total,
        "loc_code": loc_code,
        "loc_comment": loc_comment,
        "loc_blank": loc_blank,
        "file_count": file_count,
        "source_hash": source_hash,
        "extension_breakdown": dict(ext_counts),
        "language_breakdown": dict(lang_counts),
        "registered_at": now,
    }

    print(f"[INFO] Registered legacy application '{name}' as {app_id}")
    print(f"       Language: {primary_language} | Files: {file_count} | LOC: {loc_total}")
    return result


# ---------------------------------------------------------------------------
# Python analysis (AST-based)
# ---------------------------------------------------------------------------

class _PythonComplexityVisitor(ast.NodeVisitor):
    """Count branching nodes for cyclomatic complexity estimation."""

    def __init__(self):
        self.complexity = 1  # Base complexity

    def visit_If(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        # Each 'and'/'or' adds a branch
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.complexity += 1
        self.generic_visit(node)


def _compute_python_complexity(node):
    """Compute cyclomatic complexity for an AST node (function or class body)."""
    visitor = _PythonComplexityVisitor()
    visitor.visit(node)
    return visitor.complexity


def analyze_python(app_id, source_path):
    """Analyze Python source files using the ast module.

    Extracts:
      - Classes and their methods, decorators, inheritance
      - Top-level functions
      - Import dependencies
      - Flask/Django API endpoints
      - Cyclomatic complexity per component

    Args:
        app_id: Legacy application ID (lapp-xxx).
        source_path: Path to the source root.

    Returns:
        dict with counts of extracted components, dependencies, and APIs.
    """
    source_path = Path(source_path)
    conn = _get_db()

    components_added = 0
    dependencies_added = 0
    apis_added = 0

    try:
        for root, _dirs, files in os.walk(str(source_path)):
            for fname in files:
                if not fname.endswith(".py"):
                    continue

                fpath = Path(root) / fname
                rel_path = str(fpath.relative_to(source_path))

                try:
                    source_code = fpath.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(source_code, filename=str(fpath))
                except SyntaxError as exc:
                    print(f"[WARN] Syntax error in {rel_path}: {exc}")
                    continue
                except Exception as exc:
                    print(f"[WARN] Cannot parse {rel_path}: {exc}")
                    continue

                module_name = rel_path.replace(os.sep, ".").replace("/", ".").rstrip(".py")
                if module_name.endswith(".__init__"):
                    module_name = module_name[:-9]

                line_counts = _count_lines(fpath)

                # ----- Module-level component -----
                module_comp_id = f"lcomp-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT OR IGNORE INTO legacy_components
                       (id, legacy_app_id, name, component_type, file_path,
                        qualified_name, loc, cyclomatic_complexity, properties)
                       VALUES (?, ?, ?, 'module', ?, ?, ?, ?, ?)""",
                    (
                        module_comp_id, app_id, fname, rel_path,
                        module_name, line_counts["code"],
                        _compute_python_complexity(tree),
                        json.dumps({"total_lines": line_counts["total"]}),
                    ),
                )
                components_added += 1

                # ----- Imports -----
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            _insert_dependency(
                                conn, app_id, module_comp_id, None,
                                "import", evidence=f"import {alias.name}"
                            )
                            dependencies_added += 1

                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        names = ", ".join(a.name for a in node.names) if node.names else "*"
                        _insert_dependency(
                            conn, app_id, module_comp_id, None,
                            "import", evidence=f"from {mod} import {names}"
                        )
                        dependencies_added += 1

                # ----- Classes -----
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.ClassDef):
                        class_id = f"lcomp-{uuid.uuid4().hex[:12]}"
                        bases = [_get_name(b) for b in node.bases]
                        decorators = [_get_decorator_name(d) for d in node.decorator_list]

                        class_complexity = _compute_python_complexity(node)
                        class_loc = (node.end_lineno or node.lineno) - node.lineno + 1

                        # Determine component type from decorators
                        comp_type = "class"
                        for dec in decorators:
                            if dec and ("Controller" in dec or "controller" in dec):
                                comp_type = "controller"
                                break

                        props = {
                            "bases": bases,
                            "decorators": decorators,
                            "method_count": sum(
                                1 for n in ast.iter_child_nodes(node)
                                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                            ),
                        }

                        conn.execute(
                            """INSERT OR IGNORE INTO legacy_components
                               (id, legacy_app_id, name, component_type, file_path,
                                qualified_name, parent_component_id, loc,
                                cyclomatic_complexity, properties)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                class_id, app_id, node.name, comp_type, rel_path,
                                f"{module_name}.{node.name}", module_comp_id,
                                class_loc, class_complexity, json.dumps(props),
                            ),
                        )
                        components_added += 1

                        # Inheritance dependencies
                        for base_name in bases:
                            if base_name and base_name not in ("object",):
                                _insert_dependency(
                                    conn, app_id, class_id, None,
                                    "inheritance", evidence=f"extends {base_name}"
                                )
                                dependencies_added += 1

                        # Extract methods inside the class
                        for child in ast.iter_child_nodes(node):
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                [_get_decorator_name(d) for d in child.decorator_list]

                                # Detect Flask/Blueprint route decorators
                                for dec in child.decorator_list:
                                    route_info = _extract_flask_route(dec)
                                    if route_info:
                                        api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                                        conn.execute(
                                            """INSERT OR IGNORE INTO legacy_apis
                                               (id, legacy_app_id, component_id, method,
                                                path, handler_function, parameters)
                                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            (
                                                api_id, app_id, class_id,
                                                route_info["method"], route_info["path"],
                                                f"{node.name}.{child.name}",
                                                json.dumps(route_info.get("params", [])),
                                            ),
                                        )
                                        apis_added += 1

                    # ----- Top-level functions -----
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func_id = f"lcomp-{uuid.uuid4().hex[:12]}"
                        func_complexity = _compute_python_complexity(node)
                        func_loc = (node.end_lineno or node.lineno) - node.lineno + 1
                        decorators = [_get_decorator_name(d) for d in node.decorator_list]

                        conn.execute(
                            """INSERT OR IGNORE INTO legacy_components
                               (id, legacy_app_id, name, component_type, file_path,
                                qualified_name, parent_component_id, loc,
                                cyclomatic_complexity, properties)
                               VALUES (?, ?, ?, 'function', ?, ?, ?, ?, ?, ?)""",
                            (
                                func_id, app_id, node.name, rel_path,
                                f"{module_name}.{node.name}", module_comp_id,
                                func_loc, func_complexity,
                                json.dumps({"decorators": decorators}),
                            ),
                        )
                        components_added += 1

                        # Flask route on top-level function
                        for dec in node.decorator_list:
                            route_info = _extract_flask_route(dec)
                            if route_info:
                                api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                                conn.execute(
                                    """INSERT OR IGNORE INTO legacy_apis
                                       (id, legacy_app_id, component_id, method,
                                        path, handler_function, parameters)
                                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                    (
                                        api_id, app_id, func_id,
                                        route_info["method"], route_info["path"],
                                        node.name,
                                        json.dumps(route_info.get("params", [])),
                                    ),
                                )
                                apis_added += 1

                # ----- Django URL patterns -----
                django_urls = _extract_django_urls(source_code, rel_path)
                for url_info in django_urls:
                    api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                    conn.execute(
                        """INSERT OR IGNORE INTO legacy_apis
                           (id, legacy_app_id, component_id, method, path,
                            handler_function)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            api_id, app_id, module_comp_id,
                            url_info.get("method", "ALL"),
                            url_info["path"],
                            url_info.get("handler", ""),
                        ),
                    )
                    apis_added += 1

        conn.commit()
    finally:
        conn.close()

    print(f"[INFO] Python analysis complete for {app_id}")
    print(f"       Components: {components_added} | Dependencies: {dependencies_added} | APIs: {apis_added}")
    return {
        "components": components_added,
        "dependencies": dependencies_added,
        "apis": apis_added,
    }


def _get_name(node):
    """Extract a name string from an AST node (Name, Attribute, or Constant)."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        value = _get_name(node.value)
        return f"{value}.{node.attr}" if value else node.attr
    elif isinstance(node, ast.Constant):
        return str(node.value)
    return None


def _get_decorator_name(node):
    """Extract the decorator name from an AST decorator node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return _get_name(node)
    elif isinstance(node, ast.Call):
        return _get_decorator_name(node.func)
    return None


def _extract_flask_route(decorator_node):
    """Extract Flask/Blueprint route info from a decorator AST node.

    Handles patterns like:
      @app.route('/path', methods=['GET', 'POST'])
      @blueprint.route('/path')
      @app.get('/path')
      @app.post('/path')

    Returns:
        dict with 'method' and 'path' keys, or None if not a route.
    """
    if not isinstance(decorator_node, ast.Call):
        return None

    func = decorator_node.func
    func_name = _get_name(func)
    if not func_name:
        return None

    # Detect @app.route(...) or @bp.route(...)
    parts = func_name.split(".")
    if len(parts) < 2:
        return None

    method_name = parts[-1]

    # Simple HTTP method shortcuts: @app.get, @app.post, etc.
    shortcut_methods = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
    if method_name in shortcut_methods:
        path = None
        if decorator_node.args:
            path = _get_name(decorator_node.args[0])
        return {"method": shortcut_methods[method_name], "path": path or "/"}

    if method_name != "route":
        return None

    # Extract path (first positional arg)
    path = "/"
    if decorator_node.args:
        path_val = _get_name(decorator_node.args[0])
        if path_val:
            path = path_val

    # Extract methods keyword argument
    methods = ["ALL"]
    for kw in decorator_node.keywords:
        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
            methods = []
            for elt in kw.value.elts:
                m = _get_name(elt)
                if m:
                    methods.append(m.upper())

    # Return one entry per HTTP method
    return {"method": methods[0] if len(methods) == 1 else "ALL", "path": path, "params": methods}


def _extract_django_urls(source_code, rel_path):
    """Extract Django URL patterns from source code using regex.

    Looks for urlpatterns list entries like:
      path('api/v1/users/', views.user_list, name='user-list')
      url(r'^users/$', views.user_list)

    Returns:
        list of dicts with 'path' and 'handler' keys.
    """
    if "urlpatterns" not in source_code:
        return []

    results = []

    # Match path(...) and url(...) calls
    pattern = re.compile(
        r"""(?:path|url)\s*\(\s*['"]([^'"]+)['"]"""
        r"""\s*,\s*([\w.]+)""",
        re.MULTILINE,
    )
    for match in pattern.finditer(source_code):
        url_path = match.group(1)
        handler = match.group(2)
        results.append({"path": url_path, "handler": handler, "method": "ALL"})

    return results


# ---------------------------------------------------------------------------
# Java analysis (regex-based)
# ---------------------------------------------------------------------------

def analyze_java(app_id, source_path):
    """Analyze Java source files using regex pattern matching.

    Extracts:
      - Package declarations and imports
      - Classes, interfaces, enums with modifiers
      - Methods with visibility modifiers
      - Annotations (@Controller, @Service, @Repository, @Entity, etc.)
      - Spring MVC/Boot API endpoints from @RequestMapping, @GetMapping, etc.
      - Struts Actions from struts-config.xml
      - Spring beans from applicationContext.xml

    Args:
        app_id: Legacy application ID.
        source_path: Path to the source root.

    Returns:
        dict with counts of extracted components, dependencies, and APIs.
    """
    source_path = Path(source_path)
    conn = _get_db()

    components_added = 0
    dependencies_added = 0
    apis_added = 0

    # Regex patterns
    re_package = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
    re_import = re.compile(r"^\s*import\s+(static\s+)?([\w.*]+)\s*;", re.MULTILINE)
    re_class = re.compile(
        r"(?:public|private|protected)?\s*(?:abstract\s+|final\s+|static\s+)*"
        r"(class|interface|enum)\s+(\w+)"
        r"(?:\s+extends\s+(\w+))?"
        r"(?:\s+implements\s+([\w,\s]+))?",
        re.MULTILINE,
    )
    re.compile(
        r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
        r"(?:abstract\s+)?[\w<>\[\],\s?]+\s+(\w+)\s*\(",
        re.MULTILINE,
    )
    re_annotation = re.compile(r"@(\w+)(?:\(([^)]*)\))?", re.MULTILINE)

    # Annotation-to-component-type mapping
    annotation_type_map = {
        "Controller": "controller",
        "RestController": "controller",
        "Service": "service",
        "Repository": "repository",
        "Entity": "entity",
        "Stateless": "ejb",
        "Stateful": "ejb",
        "Singleton": "ejb",
        "MessageDriven": "ejb",
        "WebServlet": "servlet",
        "ManagedBean": "controller",
    }

    # HTTP mapping annotations
    http_mapping_annotations = {
        "RequestMapping": None,  # method extracted from params
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "DeleteMapping": "DELETE",
        "PatchMapping": "PATCH",
    }

    try:
        for root, _dirs, files in os.walk(str(source_path)):
            for fname in files:
                if not fname.endswith(".java"):
                    continue

                fpath = Path(root) / fname
                rel_path = str(fpath.relative_to(source_path))

                try:
                    source_code = fpath.read_text(encoding="utf-8", errors="replace")
                except (OSError, IOError):
                    continue

                line_counts = _count_lines(fpath)

                # Package
                pkg_match = re_package.search(source_code)
                package = pkg_match.group(1) if pkg_match else ""

                # Imports
                import_list = []
                for m in re_import.finditer(source_code):
                    import_list.append(m.group(2))

                # All annotations in the file
                file_annotations = [m.group(1) for m in re_annotation.finditer(source_code)]

                # Class-level request mapping (prefix)
                class_path_prefix = ""
                class_rm = re.search(
                    r"@RequestMapping\s*\(\s*(?:value\s*=\s*)?[\"']([^\"']+)[\"']",
                    source_code,
                )
                if class_rm:
                    class_path_prefix = class_rm.group(1)

                # Classes
                for cm in re_class.finditer(source_code):
                    kind = cm.group(1)       # class, interface, enum
                    class_name = cm.group(2)
                    extends = cm.group(3)
                    implements = cm.group(4)

                    comp_id = f"lcomp-{uuid.uuid4().hex[:12]}"
                    qualified = f"{package}.{class_name}" if package else class_name

                    # Determine component type from annotations
                    comp_type = kind  # default: class, interface, enum
                    for ann in file_annotations:
                        if ann in annotation_type_map:
                            comp_type = annotation_type_map[ann]
                            break

                    props = {
                        "package": package,
                        "extends": extends,
                        "implements": [s.strip() for s in implements.split(",")] if implements else [],
                        "annotations": list(set(file_annotations)),
                        "kind": kind,
                    }

                    conn.execute(
                        """INSERT OR IGNORE INTO legacy_components
                           (id, legacy_app_id, name, component_type, file_path,
                            qualified_name, loc, cyclomatic_complexity, properties)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            comp_id, app_id, class_name, comp_type, rel_path,
                            qualified, line_counts["code"],
                            _estimate_complexity_regex(source_code),
                            json.dumps(props),
                        ),
                    )
                    components_added += 1

                    # Inheritance dependency
                    if extends:
                        _insert_dependency(
                            conn, app_id, comp_id, None,
                            "inheritance", evidence=f"extends {extends}"
                        )
                        dependencies_added += 1

                    # Implements dependency
                    if implements:
                        for iface in implements.split(","):
                            iface = iface.strip()
                            if iface:
                                _insert_dependency(
                                    conn, app_id, comp_id, None,
                                    "inheritance", evidence=f"implements {iface}"
                                )
                                dependencies_added += 1

                    # Import dependencies
                    for imp in import_list:
                        _insert_dependency(
                            conn, app_id, comp_id, None,
                            "import", evidence=f"import {imp}"
                        )
                        dependencies_added += 1

                    # Annotation-based dependencies (EJB injection, Spring autowire)
                    for ann in file_annotations:
                        if ann in ("Inject", "Autowired", "EJB", "Resource"):
                            _insert_dependency(
                                conn, app_id, comp_id, None,
                                "injection", evidence=f"@{ann}"
                            )
                            dependencies_added += 1

                    # HTTP endpoint extraction from annotations
                    for ann_name, default_method in http_mapping_annotations.items():
                        ann_pattern = re.compile(
                            rf"@{ann_name}\s*\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"']"
                            rf"(?:\s*,\s*method\s*=\s*(?:RequestMethod\.)?(\w+))?",
                            re.MULTILINE,
                        )
                        for am in ann_pattern.finditer(source_code):
                            endpoint_path = am.group(1)
                            http_method = am.group(2) or default_method or "ALL"

                            full_path = class_path_prefix.rstrip("/") + "/" + endpoint_path.lstrip("/")
                            full_path = "/" + full_path.strip("/")

                            api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                            conn.execute(
                                """INSERT OR IGNORE INTO legacy_apis
                                   (id, legacy_app_id, component_id, method, path,
                                    handler_function)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (
                                    api_id, app_id, comp_id,
                                    http_method.upper(), full_path,
                                    f"{class_name}.{ann_name}",
                                ),
                            )
                            apis_added += 1

        # Detect Struts actions from struts-config.xml
        struts_apis = _detect_struts_actions(source_path, app_id)
        apis_added += struts_apis

        # Detect Spring beans from applicationContext.xml
        spring_deps = _detect_spring_beans(source_path, app_id, conn)
        dependencies_added += spring_deps

        conn.commit()
    finally:
        conn.close()

    print(f"[INFO] Java analysis complete for {app_id}")
    print(f"       Components: {components_added} | Dependencies: {dependencies_added} | APIs: {apis_added}")
    return {
        "components": components_added,
        "dependencies": dependencies_added,
        "apis": apis_added,
    }


def _estimate_complexity_regex(source_code):
    """Estimate cyclomatic complexity from source code via keyword counting.

    Used for Java/C# where we do not have AST. Counts branching keywords.
    """
    complexity = 1
    branching_keywords = [
        r"\bif\s*\(", r"\belse\s+if\s*\(", r"\bfor\s*\(",
        r"\bwhile\s*\(", r"\bcatch\s*\(", r"\bcase\s+",
        r"\b\?\s*", r"&&", r"\|\|",
    ]
    for pattern in branching_keywords:
        complexity += len(re.findall(pattern, source_code))
    return complexity


def _detect_struts_actions(source_path, app_id):
    """Parse struts-config.xml for Action mappings if present."""
    config_path = source_path / "WEB-INF" / "struts-config.xml"
    if not config_path.exists():
        # Search for it elsewhere
        for root, _dirs, files in os.walk(str(source_path)):
            if "struts-config.xml" in files:
                config_path = Path(root) / "struts-config.xml"
                break
        else:
            return 0

    count = 0
    try:
        content = config_path.read_text(encoding="utf-8", errors="replace")
        # <action path="/login" type="com.example.LoginAction" ...>
        pattern = re.compile(
            r'<action\s+[^>]*path\s*=\s*["\']([^"\']+)["\']'
            r'[^>]*type\s*=\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        conn = _get_db()
        try:
            for m in pattern.finditer(content):
                api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT OR IGNORE INTO legacy_apis
                       (id, legacy_app_id, component_id, method, path, handler_function)
                       VALUES (?, ?, NULL, 'ALL', ?, ?)""",
                    (api_id, app_id, m.group(1), m.group(2)),
                )
                count += 1
            conn.commit()
        finally:
            conn.close()
    except (OSError, IOError):
        pass
    return count


def _detect_spring_beans(source_path, app_id, conn):
    """Parse applicationContext.xml for Spring bean definitions."""
    count = 0
    for xml_name in ("applicationContext.xml", "spring-context.xml", "beans.xml"):
        for root, _dirs, files in os.walk(str(source_path)):
            if xml_name in files:
                xml_path = Path(root) / xml_name
                try:
                    content = xml_path.read_text(encoding="utf-8", errors="replace")
                    # <bean id="userService" class="com.example.UserService" ...>
                    bean_pattern = re.compile(
                        r'<bean\s+[^>]*id\s*=\s*["\']([^"\']+)["\']'
                        r'[^>]*class\s*=\s*["\']([^"\']+)["\']',
                        re.IGNORECASE,
                    )
                    for m in bean_pattern.finditer(content):
                        bean_id_str = m.group(1)
                        m.group(2)
                        # Look for property refs (injection)
                        ref_pattern = re.compile(
                            rf'<bean[^>]*id\s*=\s*["\'{bean_id_str}["\'].*?</bean>',
                            re.DOTALL | re.IGNORECASE,
                        )
                        bean_block = ref_pattern.search(content)
                        if bean_block:
                            for ref in re.findall(r'ref\s*=\s*["\']([^"\']+)["\']', bean_block.group(0)):
                                count += 1
                except (OSError, IOError):
                    pass
    return count


# ---------------------------------------------------------------------------
# C# analysis (regex-based)
# ---------------------------------------------------------------------------

def analyze_csharp(app_id, source_path):
    """Analyze C# source files using regex pattern matching.

    Extracts:
      - Namespaces and using statements
      - Classes, interfaces, structs, enums with modifiers
      - Methods with visibility
      - Attributes ([HttpGet], [HttpPost], [Route], [ApiController], etc.)
      - ASP.NET Web API/MVC endpoints
      - WCF ServiceContract interfaces
      - WebForms code-behind detection

    Args:
        app_id: Legacy application ID.
        source_path: Path to the source root.

    Returns:
        dict with counts of extracted components, dependencies, and APIs.
    """
    source_path = Path(source_path)
    conn = _get_db()

    components_added = 0
    dependencies_added = 0
    apis_added = 0

    re_namespace = re.compile(r"^\s*namespace\s+([\w.]+)", re.MULTILINE)
    re_using = re.compile(r"^\s*using\s+([\w.]+)\s*;", re.MULTILINE)
    re_class = re.compile(
        r"(?:public|private|internal|protected)?\s*"
        r"(?:abstract\s+|sealed\s+|static\s+|partial\s+)*"
        r"(class|interface|struct|enum)\s+(\w+)"
        r"(?:\s*:\s*([\w,\s.]+))?",
        re.MULTILINE,
    )
    re.compile(
        r"(?:public|private|protected|internal)\s+(?:static\s+)?(?:async\s+)?"
        r"(?:virtual\s+)?(?:override\s+)?(?:abstract\s+)?"
        r"[\w<>\[\]?,\s]+\s+(\w+)\s*\(",
        re.MULTILINE,
    )
    re_attribute = re.compile(r"\[(\w+)(?:\(([^]]*)\))?\]", re.MULTILINE)

    # Attribute-to-component-type mapping
    attr_type_map = {
        "ApiController": "controller",
        "Controller": "controller",
        "ServiceContract": "interface",
        "DataContract": "model",
        "Table": "entity",
    }

    # HTTP attribute mapping
    http_attr_map = {
        "HttpGet": "GET",
        "HttpPost": "POST",
        "HttpPut": "PUT",
        "HttpDelete": "DELETE",
        "HttpPatch": "PATCH",
    }

    try:
        for root, _dirs, files in os.walk(str(source_path)):
            for fname in files:
                if not fname.endswith(".cs"):
                    continue

                fpath = Path(root) / fname
                rel_path = str(fpath.relative_to(source_path))

                try:
                    source_code = fpath.read_text(encoding="utf-8", errors="replace")
                except (OSError, IOError):
                    continue

                line_counts = _count_lines(fpath)

                # Namespace
                ns_match = re_namespace.search(source_code)
                namespace = ns_match.group(1) if ns_match else ""

                # Using statements
                usings = [m.group(1) for m in re_using.finditer(source_code)]

                # All attributes in the file
                file_attrs = [m.group(1) for m in re_attribute.finditer(source_code)]

                # Detect WebForms code-behind
                is_webforms = fname.endswith(".aspx.cs") or fname.endswith(".ascx.cs")

                # Class-level Route prefix
                class_route_prefix = ""
                route_match = re.search(
                    r'\[Route\s*\(\s*["\']([^"\']+)["\']\s*\)\]',
                    source_code,
                )
                if route_match:
                    class_route_prefix = route_match.group(1)

                # Classes / interfaces / structs / enums
                for cm in re_class.finditer(source_code):
                    kind = cm.group(1)
                    class_name = cm.group(2)
                    base_types_str = cm.group(3)

                    comp_id = f"lcomp-{uuid.uuid4().hex[:12]}"
                    qualified = f"{namespace}.{class_name}" if namespace else class_name

                    # Determine component type
                    comp_type = kind
                    if is_webforms:
                        comp_type = "view"
                    for attr in file_attrs:
                        if attr in attr_type_map:
                            comp_type = attr_type_map[attr]
                            break

                    base_types = []
                    if base_types_str:
                        base_types = [b.strip() for b in base_types_str.split(",") if b.strip()]

                    props = {
                        "namespace": namespace,
                        "base_types": base_types,
                        "attributes": list(set(file_attrs)),
                        "kind": kind,
                        "is_webforms": is_webforms,
                    }

                    conn.execute(
                        """INSERT OR IGNORE INTO legacy_components
                           (id, legacy_app_id, name, component_type, file_path,
                            qualified_name, loc, cyclomatic_complexity, properties)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            comp_id, app_id, class_name, comp_type, rel_path,
                            qualified, line_counts["code"],
                            _estimate_complexity_regex(source_code),
                            json.dumps(props),
                        ),
                    )
                    components_added += 1

                    # Inheritance / implementation dependencies
                    for base in base_types:
                        _insert_dependency(
                            conn, app_id, comp_id, None,
                            "inheritance", evidence=f"inherits {base}"
                        )
                        dependencies_added += 1

                    # Using dependencies
                    for u in usings:
                        _insert_dependency(
                            conn, app_id, comp_id, None,
                            "import", evidence=f"using {u}"
                        )
                        dependencies_added += 1

                    # Injection via [Inject] or constructor injection
                    for attr in file_attrs:
                        if attr in ("Inject", "Dependency"):
                            _insert_dependency(
                                conn, app_id, comp_id, None,
                                "injection", evidence=f"[{attr}]"
                            )
                            dependencies_added += 1

                    # WCF ServiceContract detection
                    if "ServiceContract" in file_attrs and kind == "interface":
                        # Extract OperationContract methods as API endpoints
                        op_pattern = re.compile(
                            r'\[OperationContract\].*?'
                            r'(?:public|private|protected|internal)?\s*[\w<>\[\]]+\s+(\w+)\s*\(',
                            re.DOTALL,
                        )
                        for op in op_pattern.finditer(source_code):
                            api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                            conn.execute(
                                """INSERT OR IGNORE INTO legacy_apis
                                   (id, legacy_app_id, component_id, method, path,
                                    handler_function)
                                   VALUES (?, ?, ?, 'ALL', ?, ?)""",
                                (api_id, app_id, comp_id, f"/{op.group(1)}", f"{class_name}.{op.group(1)}"),
                            )
                            apis_added += 1

                    # HTTP endpoint extraction from attributes
                    for attr_name, http_method in http_attr_map.items():
                        attr_pattern = re.compile(
                            rf'\[{attr_name}(?:\s*\(\s*["\']([^"\']*)["\'])?\s*\]',
                            re.MULTILINE,
                        )
                        for am in attr_pattern.finditer(source_code):
                            endpoint_path = am.group(1) or ""
                            full_path = class_route_prefix.rstrip("/")
                            if endpoint_path:
                                full_path += "/" + endpoint_path.lstrip("/")
                            if not full_path:
                                full_path = f"/{class_name}"
                            full_path = "/" + full_path.strip("/")

                            api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                            conn.execute(
                                """INSERT OR IGNORE INTO legacy_apis
                                   (id, legacy_app_id, component_id, method, path,
                                    handler_function)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (api_id, app_id, comp_id, http_method, full_path, class_name),
                            )
                            apis_added += 1

                    # Route attribute on methods
                    route_method_pattern = re.compile(
                        r'\[Route\s*\(\s*["\']([^"\']+)["\']\s*\)\]'
                        r'\s*(?:\[Http(\w+)\])?\s*'
                        r'(?:public|private|protected|internal)\s+[\w<>\[\]]+\s+(\w+)\s*\(',
                        re.MULTILINE,
                    )
                    for rm in route_method_pattern.finditer(source_code):
                        r_path = rm.group(1)
                        r_method = rm.group(2) or "ALL"
                        r_handler = rm.group(3)

                        full_path = "/" + class_route_prefix.strip("/")
                        if r_path:
                            full_path += "/" + r_path.lstrip("/")
                        full_path = "/" + full_path.strip("/")

                        api_id = f"lapi-{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            """INSERT OR IGNORE INTO legacy_apis
                               (id, legacy_app_id, component_id, method, path,
                                handler_function)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                api_id, app_id, comp_id,
                                r_method.upper(), full_path,
                                f"{class_name}.{r_handler}",
                            ),
                        )
                        apis_added += 1

        conn.commit()
    finally:
        conn.close()

    print(f"[INFO] C# analysis complete for {app_id}")
    print(f"       Components: {components_added} | Dependencies: {dependencies_added} | APIs: {apis_added}")
    return {
        "components": components_added,
        "dependencies": dependencies_added,
        "apis": apis_added,
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _insert_dependency(conn, app_id, source_comp_id, target_comp_id, dep_type, evidence=None):
    """Insert a dependency record, ignoring duplicates."""
    try:
        conn.execute(
            """INSERT OR IGNORE INTO legacy_dependencies
               (legacy_app_id, source_component_id, target_component_id,
                dependency_type, evidence)
               VALUES (?, ?, ?, ?, ?)""",
            (app_id, source_comp_id, target_comp_id, dep_type, evidence),
        )
    except sqlite3.IntegrityError:
        pass  # Duplicate — ignore


# ---------------------------------------------------------------------------
# Framework and database detection
# ---------------------------------------------------------------------------

def _detect_framework(source_path, language):
    """Identify the application framework from file/code indicators.

    Args:
        source_path: Path to source root.
        language: Primary language string (python, java, csharp).

    Returns:
        Tuple of (framework_name, framework_version_guess) or (None, None).
    """
    source_path = Path(source_path)

    if language == "python":
        # Django: settings.py + urls.py
        has_settings = False
        has_urls = False
        for root, _dirs, files in os.walk(str(source_path)):
            if "settings.py" in files:
                has_settings = True
            if "urls.py" in files:
                has_urls = True
            if has_settings and has_urls:
                return ("Django", _detect_version_from_requirements(source_path, "django"))

        # Flask: app.py (or wsgi.py) with Flask import
        for root, _dirs, files in os.walk(str(source_path)):
            for f in files:
                if f in ("app.py", "wsgi.py", "application.py", "__init__.py"):
                    fpath = Path(root) / f
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")
                        if "from flask" in content.lower() or "import flask" in content.lower():
                            return ("Flask", _detect_version_from_requirements(source_path, "flask"))
                    except (OSError, IOError):
                        pass

        # Pyramid
        setup_py = source_path / "setup.py"
        if setup_py.exists():
            try:
                content = setup_py.read_text(encoding="utf-8", errors="replace")
                if "pyramid" in content.lower():
                    return ("Pyramid", _detect_version_from_requirements(source_path, "pyramid"))
            except (OSError, IOError):
                pass

    elif language == "java":
        # Struts
        for root, _dirs, files in os.walk(str(source_path)):
            if "struts-config.xml" in files:
                return ("Struts", None)

        # EJB
        for root, _dirs, files in os.walk(str(source_path)):
            if "ejb-jar.xml" in files:
                return ("EJB", None)

        # Spring / Spring Boot
        for root, _dirs, files in os.walk(str(source_path)):
            for f in files:
                if f.endswith(".java"):
                    fpath = Path(root) / f
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")
                        if "@SpringBootApplication" in content:
                            return ("Spring Boot", _detect_spring_version(source_path))
                        if "@Controller" in content or "@Service" in content:
                            return ("Spring", _detect_spring_version(source_path))
                    except (OSError, IOError):
                        pass

        # Servlet-only (web.xml)
        for root, _dirs, files in os.walk(str(source_path)):
            if "web.xml" in files:
                return ("Servlet", None)

    elif language == "csharp":
        # Check .csproj for target framework
        for root, _dirs, files in os.walk(str(source_path)):
            for f in files:
                if f.endswith(".csproj"):
                    fpath = Path(root) / f
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")
                        tf_match = re.search(r"<TargetFramework>(.*?)</TargetFramework>", content)
                        fw_version = tf_match.group(1) if tf_match else None

                        if "Microsoft.NET.Sdk.Web" in content:
                            return ("ASP.NET Core", fw_version)
                    except (OSError, IOError):
                        pass

        # WCF
        for root, _dirs, files in os.walk(str(source_path)):
            for f in files:
                if f.endswith(".cs"):
                    try:
                        content = (Path(root) / f).read_text(encoding="utf-8", errors="replace")
                        if "[ServiceContract]" in content:
                            return ("WCF", None)
                    except (OSError, IOError):
                        pass

        # WebForms
        for root, _dirs, files in os.walk(str(source_path)):
            for f in files:
                if f.endswith(".aspx"):
                    return ("WebForms", None)

        # Razor Pages
        for root, _dirs, files in os.walk(str(source_path)):
            for f in files:
                if f.endswith(".cshtml"):
                    try:
                        content = (Path(root) / f).read_text(encoding="utf-8", errors="replace")
                        if "@page" in content:
                            return ("Razor Pages", None)
                    except (OSError, IOError):
                        pass

    return (None, None)


def _detect_version_from_requirements(source_path, package_name):
    """Try to extract a package version from requirements.txt or Pipfile."""
    for req_file in ("requirements.txt", "requirements/base.txt", "requirements/production.txt"):
        req_path = source_path / req_file
        if req_path.exists():
            try:
                for line in req_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip().lower()
                    if line.startswith(package_name.lower()):
                        # e.g., django==3.2.1 or flask>=2.0
                        version_match = re.search(r"[=><]+(.+)", line)
                        if version_match:
                            return version_match.group(1).strip()
            except (OSError, IOError):
                pass
    return None


def _detect_spring_version(source_path):
    """Attempt to detect Spring version from pom.xml or build.gradle."""
    pom = source_path / "pom.xml"
    if pom.exists():
        try:
            content = pom.read_text(encoding="utf-8", errors="replace")
            v = re.search(r"<spring-boot\.version>(.*?)</spring-boot\.version>", content)
            if v:
                return v.group(1)
            v = re.search(r"<version>(.*?)</version>", content)
            if v:
                return v.group(1)
        except (OSError, IOError):
            pass

    gradle = source_path / "build.gradle"
    if gradle.exists():
        try:
            content = gradle.read_text(encoding="utf-8", errors="replace")
            v = re.search(r"springBootVersion\s*=\s*['\"]([^'\"]+)['\"]", content)
            if v:
                return v.group(1)
        except (OSError, IOError):
            pass

    return None


def _detect_database(source_path):
    """Detect database type from configuration files and source code.

    Searches for:
      - Python: Django DATABASES setting, SQLAlchemy URLs, DB driver imports
      - Java: JDBC URLs, Hibernate config, JPA persistence.xml
      - C#: connection strings in web.config/appsettings.json

    Args:
        source_path: Path to source root.

    Returns:
        Database type string (e.g. 'postgresql', 'mysql', 'oracle') or None.
    """
    source_path = Path(source_path)

    db_indicators = {
        "postgresql": [
            "psycopg2", "postgresql", "postgres", "org.postgresql",
            "Npgsql", "5432",
        ],
        "mysql": [
            "pymysql", "mysqlclient", "mysql-connector", "com.mysql",
            "MySql.Data", "3306",
        ],
        "oracle": [
            "cx_Oracle", "oracledb", "oracle.jdbc", "Oracle.ManagedDataAccess",
            "1521",
        ],
        "mssql": [
            "pyodbc", "pymssql", "com.microsoft.sqlserver",
            "System.Data.SqlClient", "SqlConnection", "1433",
        ],
        "sqlite": [
            "sqlite3", "org.sqlite", "Microsoft.Data.Sqlite",
            "System.Data.SQLite",
        ],
        "db2": ["ibm_db", "com.ibm.db2"],
        "h2": ["org.h2"],
        "derby": ["org.apache.derby"],
    }

    # Scan files for DB indicators
    scan_extensions = {".py", ".java", ".cs", ".xml", ".json", ".properties", ".yaml", ".yml", ".cfg", ".ini", ".config"}
    scan_filenames = {
        "settings.py", "database.py", "db.py",
        "application.properties", "application.yml", "application.yaml",
        "persistence.xml", "hibernate.cfg.xml",
        "web.config", "appsettings.json", "appsettings.Development.json",
    }

    for root, _dirs, files in os.walk(str(source_path)):
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in scan_extensions and fname not in scan_filenames:
                continue

            fpath = Path(root) / fname
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace").lower()
            except (OSError, IOError):
                continue

            for db_type, indicators in db_indicators.items():
                for indicator in indicators:
                    if indicator.lower() in content:
                        return db_type

    return None


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------

def _compute_tech_debt(app_id):
    """Estimate technical debt in hours for a legacy application.

    Formula per component:
        debt = (LOC * complexity_factor * coupling_factor) / productivity_rate

    Where:
        complexity_factor: 1.0 (CC<5), 1.5 (5-10), 2.0 (10-20), 3.0 (>20)
        coupling_factor: 1.0 (deps<3), 1.2 (3-6), 1.5 (>6)
        productivity_rate: 20 LOC/hour

    Args:
        app_id: Legacy application ID.

    Returns:
        Total estimated tech debt hours (float).
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT loc, cyclomatic_complexity, dependencies_out
               FROM legacy_components WHERE legacy_app_id = ?""",
            (app_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0.0

    total_debt = 0.0
    for row in rows:
        loc = row["loc"] or 0
        cc = row["cyclomatic_complexity"] or 1
        deps = row["dependencies_out"] or 0

        # Complexity factor
        if cc < 5:
            complexity_factor = 1.0
        elif cc < 10:
            complexity_factor = 1.5
        elif cc < 20:
            complexity_factor = 2.0
        else:
            complexity_factor = 3.0

        # Coupling factor
        if deps < 3:
            coupling_factor = 1.0
        elif deps <= 6:
            coupling_factor = 1.2
        else:
            coupling_factor = 1.5

        total_debt += (loc * complexity_factor * coupling_factor) / PRODUCTIVITY_RATE

    return round(total_debt, 2)


def _compute_maintainability_index(app_id):
    """Compute a Halstead-simplified Maintainability Index (0-100).

    MI = max(0, (171 - 5.2*ln(avg_CC) - 0.23*avg_coupling - 16.2*ln(avg_LOC)) * 100 / 171)

    This is the Microsoft Visual Studio variant of the maintainability index,
    scaled to 0-100 where higher is better.

    Args:
        app_id: Legacy application ID.

    Returns:
        Maintainability index score (float, 0-100).
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT loc, cyclomatic_complexity, coupling_score
               FROM legacy_components WHERE legacy_app_id = ?""",
            (app_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0.0

    total_cc = 0.0
    total_coupling = 0.0
    total_loc = 0.0
    count = 0

    for row in rows:
        loc = row["loc"] or 1
        cc = row["cyclomatic_complexity"] or 1
        coupling = row["coupling_score"] or 0

        total_cc += cc
        total_coupling += coupling
        total_loc += loc
        count += 1

    if count == 0:
        return 0.0

    avg_cc = max(total_cc / count, 1.0)
    avg_coupling = total_coupling / count
    avg_loc = max(total_loc / count, 1.0)

    mi_raw = 171.0 - 5.2 * math.log(avg_cc) - 0.23 * avg_coupling - 16.2 * math.log(avg_loc)
    mi = max(0.0, mi_raw * 100.0 / 171.0)
    return round(min(mi, 100.0), 2)


# ---------------------------------------------------------------------------
# Full analysis orchestrator
# ---------------------------------------------------------------------------

def analyze_full(project_id, app_id, source_path_override=None):
    """Run complete static analysis on a registered legacy application.

    Orchestrates:
      1. Fetch app record from DB
      2. Dispatch to language-specific analyzer
      3. Detect framework and database
      4. Compute tech debt and maintainability index
      5. Update DB with results

    Args:
        project_id: ICDEV project ID.
        app_id: Legacy application ID.
        source_path_override: Optional override for the source path.

    Returns:
        Summary dict with all analysis results.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM legacy_applications WHERE id = ? AND project_id = ?",
            (app_id, project_id),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise ValueError(f"Application {app_id} not found in project {project_id}")

    source_path = Path(source_path_override or row["source_path"])
    language = row["primary_language"]

    if not source_path.is_dir():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")

    print(f"[INFO] Starting full analysis of {row['name']} ({app_id})")
    print(f"       Language: {language} | Source: {source_path}")

    # Mark as analyzing
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE legacy_applications SET analysis_status = 'analyzing' WHERE id = ?",
            (app_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Language-specific analysis
    analysis_result = {"components": 0, "dependencies": 0, "apis": 0}
    try:
        if language == "python":
            analysis_result = analyze_python(app_id, source_path)
        elif language == "java":
            analysis_result = analyze_java(app_id, source_path)
        elif language == "csharp":
            analysis_result = analyze_csharp(app_id, source_path)
        else:
            print(f"[WARN] No analyzer available for language: {language}")
            print("       Supported: python, java, csharp")
    except Exception as exc:
        print(f"[ERROR] Analysis failed: {exc}")
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE legacy_applications SET analysis_status = 'failed' WHERE id = ?",
                (app_id,),
            )
            conn.commit()
        finally:
            conn.close()
        raise

    # Update dependency counts on components
    _update_dependency_counts(app_id)

    # Detect framework and database
    framework, framework_version = _detect_framework(source_path, language)
    db_type = _detect_database(source_path)

    # Compute quality metrics
    tech_debt = _compute_tech_debt(app_id)
    maintainability = _compute_maintainability_index(app_id)

    # Compute average complexity score
    conn = _get_db()
    try:
        avg_row = conn.execute(
            """SELECT AVG(cyclomatic_complexity) as avg_cc
               FROM legacy_components WHERE legacy_app_id = ?""",
            (app_id,),
        ).fetchone()
        avg_complexity = round(avg_row["avg_cc"] or 0.0, 2) if avg_row else 0.0
    finally:
        conn.close()

    # Update the application record
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_db()
    try:
        conn.execute(
            """UPDATE legacy_applications
               SET framework = ?, framework_version = ?,
                   tech_debt_hours = ?, maintainability_index = ?,
                   complexity_score = ?, analysis_status = 'analyzed',
                   analyzed_at = ?
               WHERE id = ?""",
            (
                framework, framework_version,
                tech_debt, maintainability,
                avg_complexity, now, app_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    summary = {
        "app_id": app_id,
        "project_id": project_id,
        "name": row["name"],
        "language": language,
        "framework": framework,
        "framework_version": framework_version,
        "database_detected": db_type,
        "analysis_status": "analyzed",
        "components_found": analysis_result["components"],
        "dependencies_found": analysis_result["dependencies"],
        "apis_found": analysis_result["apis"],
        "loc_total": row["loc_total"],
        "loc_code": row["loc_code"],
        "file_count": row["file_count"],
        "complexity_score": avg_complexity,
        "tech_debt_hours": tech_debt,
        "maintainability_index": maintainability,
        "analyzed_at": now,
    }

    print(f"[INFO] Analysis complete for {row['name']}")
    print(f"       Framework: {framework or 'unknown'} {framework_version or ''}")
    print(f"       Database: {db_type or 'not detected'}")
    print(f"       Components: {analysis_result['components']} | "
          f"Dependencies: {analysis_result['dependencies']} | "
          f"APIs: {analysis_result['apis']}")
    print(f"       Complexity: {avg_complexity} | Tech Debt: {tech_debt}h | "
          f"Maintainability: {maintainability}")

    return summary


def _update_dependency_counts(app_id):
    """Update dependencies_in and dependencies_out counts on all components."""
    conn = _get_db()
    try:
        # Count outgoing dependencies per component
        out_counts = conn.execute(
            """SELECT source_component_id, COUNT(*) as cnt
               FROM legacy_dependencies
               WHERE legacy_app_id = ? AND source_component_id IS NOT NULL
               GROUP BY source_component_id""",
            (app_id,),
        ).fetchall()

        for row in out_counts:
            conn.execute(
                "UPDATE legacy_components SET dependencies_out = ? WHERE id = ?",
                (row["cnt"], row["source_component_id"]),
            )

        # Count incoming dependencies per component
        in_counts = conn.execute(
            """SELECT target_component_id, COUNT(*) as cnt
               FROM legacy_dependencies
               WHERE legacy_app_id = ? AND target_component_id IS NOT NULL
               GROUP BY target_component_id""",
            (app_id,),
        ).fetchall()

        for row in in_counts:
            conn.execute(
                "UPDATE legacy_components SET dependencies_in = ? WHERE id = ?",
                (row["cnt"], row["target_component_id"]),
            )

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def main():
    """Command-line entry point for legacy code analysis."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI — Legacy Code Static Analysis Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Register a legacy app
  python tools/modernization/legacy_analyzer.py \\
      --register --project-id proj-123 --name "my-legacy-app" \\
      --source-path /opt/legacy/my-app

  # Run full analysis
  python tools/modernization/legacy_analyzer.py \\
      --analyze --project-id proj-123 --app-id lapp-abc123def456

  # Analyze with source path override and JSON output
  python tools/modernization/legacy_analyzer.py \\
      --analyze --project-id proj-123 --app-id lapp-abc123def456 \\
      --source-path /opt/legacy/my-app --json

Classification: CUI // SP-CTI
""",
    )

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--register", action="store_true",
        help="Register a new legacy application for analysis",
    )
    action_group.add_argument(
        "--analyze", action="store_true",
        help="Run full static analysis on a registered application",
    )

    parser.add_argument("--project-id", required=True, help="ICDEV project ID")
    parser.add_argument("--name", help="Application name (required for --register)")
    parser.add_argument("--app-id", help="Legacy application ID (required for --analyze)")
    parser.add_argument("--source-path", help="Path to source code root")
    parser.add_argument("--description", help="Application description (for --register)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output results as JSON")

    args = parser.parse_args()

    try:
        if args.register:
            if not args.name:
                parser.error("--name is required for --register")
            if not args.source_path:
                parser.error("--source-path is required for --register")

            result = register_application(
                project_id=args.project_id,
                name=args.name,
                source_path=args.source_path,
                description=args.description,
            )

            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.analyze:
            if not args.app_id:
                parser.error("--app-id is required for --analyze")

            result = analyze_full(
                project_id=args.project_id,
                app_id=args.app_id,
                source_path_override=args.source_path,
            )

            if args.json_output:
                print(json.dumps(result, indent=2))

    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
    except sqlite3.Error as exc:
        print(f"[ERROR] Database error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
