#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D -- Authorized DoD Personnel Only
# POC: ICDEV System Administrator
"""
Version Migration Tool — ICDEV DoD Modernization System

Transforms legacy source code to newer language versions using AST-based
analysis and regex transformation rules.  Supports Python 2->3, Java 8->17,
and .NET Framework 4.x -> .NET 8.

All transformations are non-destructive: source files are NEVER modified
in place.  Output always goes to a separate output_path directory.

Rules are loaded from context/modernization/version_upgrade_rules.json.
"""

import argparse
import ast
import collections
import datetime
import difflib
import json
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
RULES_PATH = BASE_DIR / "context" / "modernization" / "version_upgrade_rules.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CUI_BANNER = "CUI // SP-CTI"
SUPPORTED_LANGUAGES = {"python", "java", "csharp", "dotnet"}

# Map CLI language names to the keys used in the rules JSON
_LANG_ALIAS = {
    "csharp": "dotnet",
    "c#": "dotnet",
}


# ====================================================================
# 1. load_upgrade_rules
# ====================================================================
def load_upgrade_rules(language, from_version=None, to_version=None):
    """Load transformation rules from the context JSON file.

    Filters rules to match the requested *language* and version range.
    Returns a list of rule dicts.  If the rules file is missing the
    function returns an empty list so callers can fall back to the
    hard-coded migration functions.

    Parameters
    ----------
    language : str
        One of 'python', 'java', 'dotnet', 'csharp'.
    from_version : str | None
        Source version filter (e.g. '2.x', '8', 'framework-4.x').
    to_version : str | None
        Target version filter (e.g. '3.x', '17', 'net-8').

    Returns
    -------
    list[dict]
        Matching rule objects from the JSON.
    """
    canonical = _LANG_ALIAS.get(language.lower(), language.lower())

    if not RULES_PATH.exists():
        return []

    try:
        with open(RULES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] Could not load rules: {exc}", file=sys.stderr)
        return []

    matched_rules = []
    for entry in data.get("language_versions", []):
        if entry.get("language", "").lower() != canonical:
            continue
        # If caller supplied version filters, honour them
        if from_version and entry.get("source_version") != from_version:
            continue
        if to_version and entry.get("target_version") != to_version:
            continue
        matched_rules.extend(entry.get("rules", []))

    return matched_rules


# ====================================================================
# 5. _apply_transform_rules  (numbered out of order for dependency)
# ====================================================================
def _apply_transform_rules(content, rules):
    """Apply an ordered list of regex substitution rules to *content*.

    Each rule dict must contain at least ``pattern`` and ``replacement``.
    An optional ``flags`` key may hold a string of Python regex flag
    letters (e.g. ``"im"`` for IGNORECASE | MULTILINE).

    Returns
    -------
    tuple[str, list[dict]]
        (transformed_content, list_of_applied_rule_records)
    """
    applied = []

    for rule in rules:
        pattern = rule.get("pattern")
        replacement = rule.get("replacement", "")
        if not pattern:
            continue

        # Build regex flags
        flags = 0
        flag_str = rule.get("flags", "")
        if "i" in flag_str:
            flags |= re.IGNORECASE
        if "m" in flag_str:
            flags |= re.MULTILINE
        if "s" in flag_str:
            flags |= re.DOTALL

        # Convert $1, $2, … placeholders to Python \1, \2, …
        py_replacement = re.sub(r"\$(\d+)", r"\\\1", replacement)

        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            applied.append({
                "rule_id": rule.get("id", "unknown"),
                "error": f"Bad regex: {exc}",
            })
            continue

        new_content, count = compiled.subn(py_replacement, content)
        if count > 0:
            applied.append({
                "rule_id": rule.get("id", "unknown"),
                "description": rule.get("description", ""),
                "matches": count,
                "requires_manual_review": rule.get("requires_manual_review", False),
            })
            content = new_content

    return content, applied


# ====================================================================
# 6. _validate_transforms
# ====================================================================
def _validate_transforms(original_path, transformed_path, language):
    """Run basic syntax validation on a transformed file.

    * Python  -- ``ast.parse()``
    * Java    -- brace balance and semicolons present
    * C#/.NET -- brace balance and semicolons present

    Returns
    -------
    dict
        ``{valid: bool, errors: [str]}``
    """
    errors = []
    canonical = _LANG_ALIAS.get(language.lower(), language.lower())
    transformed_path = Path(transformed_path)

    if not transformed_path.exists():
        return {"valid": False, "errors": [f"File not found: {transformed_path}"]}

    try:
        with open(transformed_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return {"valid": False, "errors": [str(exc)]}

    if canonical == "python":
        try:
            ast.parse(content, filename=str(transformed_path))
        except SyntaxError as exc:
            errors.append(
                f"SyntaxError at line {exc.lineno}: {exc.msg}"
            )

    elif canonical in ("java", "dotnet"):
        # Brace balance check
        open_count = content.count("{")
        close_count = content.count("}")
        if open_count != close_count:
            errors.append(
                f"Brace mismatch: {open_count} opening vs "
                f"{close_count} closing braces"
            )
        # Semicolon sanity — at least one semicolon expected in real code
        if len(content.strip()) > 0 and ";" not in content:
            errors.append("No semicolons found — possible incomplete migration")

    return {"valid": len(errors) == 0, "errors": errors}


# ====================================================================
# Helper — copy tree
# ====================================================================
def _copy_source_tree(source_path, output_path):
    """Copy the full directory tree from *source_path* to *output_path*.

    Creates *output_path* if it does not exist.  Existing files inside
    *output_path* will be overwritten.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    if output_path.exists():
        shutil.rmtree(output_path)

    shutil.copytree(str(source_path), str(output_path))


# ====================================================================
# Helper — collect files by extension
# ====================================================================
def _collect_files(directory, extensions):
    """Yield all files under *directory* whose suffix is in *extensions*."""
    directory = Path(directory)
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            fpath = Path(root) / fname
            if fpath.suffix.lower() in extensions:
                yield fpath


# ====================================================================
# 2. migrate_python2_to_3
# ====================================================================
def migrate_python2_to_3(source_path, output_path):
    """Migrate Python 2 source code to Python 3.

    Uses a hybrid approach: regex transformations for syntactic patterns
    plus special-case logic (e.g. injecting ``from functools import
    reduce``).

    The function first copies the entire *source_path* tree into
    *output_path* then modifies the copies in-place.  The original
    source is NEVER touched.

    Returns
    -------
    dict
        ``{files_processed: int,
           transformations_applied: [{file, line, rule, before, after}],
           errors: [str]}``
    """
    source_path = Path(source_path)
    output_path = Path(output_path)
    _copy_source_tree(source_path, output_path)

    all_transforms = []
    all_errors = []
    files_processed = 0

    # Ordered list of (rule_name, compiled_regex, replacement_func_or_str)
    # Each entry is a tuple: (rule_id, pattern, replacement)
    _PY_RULES = [
        # (a) print >>sys.stderr, "text" must come BEFORE generic print
        (
            "py2to3-print-redirect",
            re.compile(r'^(\s*)print\s*>>\s*(\S+)\s*,\s*(.+)$', re.MULTILINE),
            r'\1print(\3, file=\2)',
        ),
        # (a) print statement -> print function
        (
            "py2to3-print-stmt",
            re.compile(r'^(\s*)print\s+(?![\(])(.+)$', re.MULTILINE),
            r'\1print(\2)',
        ),
        # (c) except Exception, e -> except Exception as e
        (
            "py2to3-except-comma",
            re.compile(r'\bexcept\s+(\w+(?:\.\w+)*)\s*,\s*(\w+)\s*:'),
            r'except \1 as \2:',
        ),
        # (m) raise Exception, "msg" -> raise Exception("msg")
        (
            "py2to3-raise-comma",
            re.compile(r'\braise\s+(\w+(?:\.\w+)*)\s*,\s*(.+)'),
            r'raise \1(\2)',
        ),
        # (n) exec "code" -> exec("code")
        (
            "py2to3-exec-stmt",
            re.compile(r'^(\s*)exec\s+(?![\(])(.+)$', re.MULTILINE),
            r'\1exec(\2)',
        ),
        # (d) unicode(x) -> str(x)
        (
            "py2to3-unicode-call",
            re.compile(r'\bunicode\('),
            'str(',
        ),
        # (d) u"string" -> "string"  (careful with ur"" raw strings)
        (
            "py2to3-unicode-prefix",
            re.compile(r'\bu(["\'])'),
            r'\1',
        ),
        # (e) raw_input -> input
        (
            "py2to3-raw-input",
            re.compile(r'\braw_input\('),
            'input(',
        ),
        # (f) xrange -> range
        (
            "py2to3-xrange",
            re.compile(r'\bxrange\('),
            'range(',
        ),
        # (g) dict.has_key(k) -> k in dict
        (
            "py2to3-has-key",
            re.compile(r'(\w+)\.has_key\(([^)]+)\)'),
            r'\2 in \1',
        ),
        # (h) .iteritems() -> .items()
        (
            "py2to3-iteritems",
            re.compile(r'\.iteritems\(\)'),
            '.items()',
        ),
        # (h) .itervalues() -> .values()
        (
            "py2to3-itervalues",
            re.compile(r'\.itervalues\(\)'),
            '.values()',
        ),
        # (h) .iterkeys() -> .keys()
        (
            "py2to3-iterkeys",
            re.compile(r'\.iterkeys\(\)'),
            '.keys()',
        ),
        # (i) import ConfigParser -> import configparser
        (
            "py2to3-configparser-import",
            re.compile(r'\bimport\s+ConfigParser\b'),
            'import configparser',
        ),
        # (i) from ConfigParser -> from configparser
        (
            "py2to3-configparser-from",
            re.compile(r'\bfrom\s+ConfigParser\b'),
            'from configparser',
        ),
        # (i) ConfigParser. -> configparser. (usage)
        (
            "py2to3-configparser-usage",
            re.compile(r'\bConfigParser\.'),
            'configparser.',
        ),
        # (j) import urllib2 -> import urllib.request
        (
            "py2to3-urllib2-import",
            re.compile(r'\bimport\s+urllib2\b'),
            'import urllib.request',
        ),
        # (j) urllib2.urlopen -> urllib.request.urlopen
        (
            "py2to3-urllib2-usage",
            re.compile(r'\burllib2\.urlopen\b'),
            'urllib.request.urlopen',
        ),
        # (j) urllib2. (other usages)
        (
            "py2to3-urllib2-other",
            re.compile(r'\burllib2\.'),
            'urllib.request.',
        ),
        # (k) import urlparse -> from urllib.parse import urlparse
        (
            "py2to3-urlparse-import",
            re.compile(r'^(\s*)import\s+urlparse\s*$', re.MULTILINE),
            r'\1from urllib.parse import urlparse',
        ),
        # (k) from urlparse import ... keep intact but fix module
        (
            "py2to3-urlparse-from",
            re.compile(r'\bfrom\s+urlparse\s+import\b'),
            'from urllib.parse import',
        ),
        # (l) from StringIO import StringIO -> from io import StringIO
        (
            "py2to3-stringio",
            re.compile(r'\bfrom\s+StringIO\s+import\s+StringIO\b'),
            'from io import StringIO',
        ),
        # (o) long(x) -> int(x)
        (
            "py2to3-long",
            re.compile(r'\blong\('),
            'int(',
        ),
        # (p) basestring -> str
        (
            "py2to3-basestring",
            re.compile(r'\bbasestring\b'),
            'str',
        ),
        # (q) reduce( -> functools.reduce(  (we add import later)
        (
            "py2to3-reduce",
            re.compile(r'(?<!\.)(?<!\w)reduce\('),
            'functools.reduce(',
        ),
    ]

    for py_file in _collect_files(output_path, {".py"}):
        files_processed += 1
        try:
            with open(py_file, "r", encoding="utf-8", errors="replace") as fh:
                original_content = fh.read()
        except OSError as exc:
            all_errors.append(f"Read error {py_file}: {exc}")
            continue

        content = original_content
        original_lines = original_content.splitlines(keepends=True)
        file_transforms = []

        # Apply each rule
        for rule_id, pattern, replacement in _PY_RULES:
            new_content = pattern.sub(replacement, content)
            if new_content != content:
                # Determine which lines changed
                new_lines = new_content.splitlines(keepends=True)
                for idx, (old_ln, new_ln) in enumerate(
                    zip(original_lines, new_lines)
                ):
                    if old_ln != new_ln:
                        file_transforms.append({
                            "file": str(py_file),
                            "line": idx + 1,
                            "rule": rule_id,
                            "before": old_ln.rstrip("\n\r"),
                            "after": new_ln.rstrip("\n\r"),
                        })
                # Lines may have been added/removed; capture extras
                if len(new_lines) > len(original_lines):
                    for idx in range(len(original_lines), len(new_lines)):
                        file_transforms.append({
                            "file": str(py_file),
                            "line": idx + 1,
                            "rule": rule_id,
                            "before": "",
                            "after": new_lines[idx].rstrip("\n\r"),
                        })

                content = new_content
                original_lines = new_content.splitlines(keepends=True)

        # (q) inject 'from functools import reduce' if reduce was transformed
        if "functools.reduce(" in content:
            has_functools_import = bool(
                re.search(
                    r'^\s*(import\s+functools|from\s+functools\s+import)',
                    content,
                    re.MULTILINE,
                )
            )
            if not has_functools_import:
                # Insert after the last top-level import or at the very top
                import_line = "from functools import reduce\n"
                insert_pos = _find_import_insert_position(content)
                content = content[:insert_pos] + import_line + content[insert_pos:]
                # Also replace functools.reduce back to reduce since we imported it
                content = content.replace("functools.reduce(", "reduce(")
                file_transforms.append({
                    "file": str(py_file),
                    "line": 0,
                    "rule": "py2to3-reduce-import-inject",
                    "before": "",
                    "after": import_line.rstrip(),
                })

        # Write back only if changed
        if content != original_content:
            try:
                with open(py_file, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except OSError as exc:
                all_errors.append(f"Write error {py_file}: {exc}")
                continue

        all_transforms.extend(file_transforms)

    return {
        "files_processed": files_processed,
        "transformations_applied": all_transforms,
        "errors": all_errors,
    }


def _find_import_insert_position(content):
    """Find the byte offset just after the last top-level import line.

    If there are no imports, returns 0 (beginning of file), respecting
    shebangs and encoding cookies.
    """
    lines = content.splitlines(keepends=True)
    last_import_end = 0
    offset = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip shebangs / encoding / docstrings / comments at top
        if stripped.startswith(("import ", "from ")) and not stripped.startswith(
            "from __future__"
        ):
            last_import_end = offset + len(line)
        elif stripped.startswith("from __future__"):
            last_import_end = offset + len(line)
        offset += len(line)

    if last_import_end == 0:
        # No imports found — insert after shebang/encoding if present
        offset = 0
        for line in lines[:2]:
            stripped = line.strip()
            if stripped.startswith("#!") or stripped.startswith("# -*-"):
                offset += len(line)
            else:
                break
        return offset

    return last_import_end


# ====================================================================
# 3. migrate_java_version
# ====================================================================
def migrate_java_version(source_path, output_path, from_ver="8", to_ver="17"):
    """Migrate Java source code from one version to another.

    Applies regex-based transformations for Java 8 -> 11 -> 17 patterns
    including Jakarta namespace migration, var inference suggestions, and
    text block / lambda suggestions (as TODO comments).

    Parameters
    ----------
    source_path : str | Path
    output_path : str | Path
    from_ver : str
        Source Java version (default ``'8'``).
    to_ver : str
        Target Java version (default ``'17'``).

    Returns
    -------
    dict
        Same structure as :func:`migrate_python2_to_3`.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)
    _copy_source_tree(source_path, output_path)

    all_transforms = []
    all_errors = []
    files_processed = 0

    # --- rule definitions ------------------------------------------------
    _JAVAX_PACKAGES = [
        "servlet",
        "persistence",
        "inject",
        "ws.rs",
        "annotation",
        "validation",
        "transaction",
        "enterprise",
        "faces",
        "json",
        "mail",
        "security.auth",
        "websocket",
        "xml.bind",
    ]
    javax_pattern = re.compile(
        r'\bjavax\.(' + '|'.join(re.escape(p) for p in _JAVAX_PACKAGES) + r')\b'
    )

    # var suggestion: Type name = new Type(...)  ->  var name = new Type(...)
    var_pattern = re.compile(
        r'^(\s*)'
        r'((?:(?:final|static)\s+)*)'                            # modifiers
        r'(\w+(?:<[^>]+>)?)\s+'                                   # Type<Generic>
        r'(\w+)\s*=\s*new\s+(\w+(?:<[^>]*>)?)\s*\(',             # name = new Type(
        re.MULTILINE,
    )

    # Multiline string concatenation hint
    multiline_concat_pattern = re.compile(
        r'"[^"]*"\s*\+\s*\n\s*"[^"]*"'
    )

    # Anonymous inner class with single method -> lambda hint
    anon_class_pattern = re.compile(
        r'new\s+(\w+)\s*\(\s*\)\s*\{\s*\n'
        r'\s*@?\s*Override\s*\n'
        r'\s*public\s+\w+\s+\w+\s*\([^)]*\)\s*\{',
        re.MULTILINE,
    )

    # Diamond operator: new Type<X,Y>() where LHS already has generics
    diamond_pattern = re.compile(
        r'new\s+(\w+)<([^>]+)>\s*\(\)',
    )

    # .collect(Collectors.toList()) -> .toList()  (Java 16+)
    collectors_tolist_pattern = re.compile(
        r'\.collect\(Collectors\.toList\(\)\)'
    )

    # .collect(Collectors.toUnmodifiableList()) -> .toList()
    collectors_unmod_pattern = re.compile(
        r'\.collect\(Collectors\.toUnmodifiableList\(\)\)'
    )

    for java_file in _collect_files(output_path, {".java"}):
        files_processed += 1
        try:
            with open(java_file, "r", encoding="utf-8", errors="replace") as fh:
                original_content = fh.read()
        except OSError as exc:
            all_errors.append(f"Read error {java_file}: {exc}")
            continue

        content = original_content
        file_transforms = []

        # (a) javax -> jakarta
        new_content = javax_pattern.sub(r'jakarta.\1', content)
        if new_content != content:
            _record_line_diffs(
                content, new_content, java_file, "java-javax-to-jakarta",
                file_transforms,
            )
            content = new_content

        # (b) var suggestions — only when LHS type matches RHS constructor type
        def _var_replace(m):
            indent = m.group(1)
            modifiers = m.group(2).strip()
            lhs_type = m.group(3)
            var_name = m.group(4)
            rhs_type = m.group(5)
            # Only suggest var when base types match and no 'final' or 'static'
            base_lhs = lhs_type.split("<")[0]
            base_rhs = rhs_type.split("<")[0]
            if base_lhs == base_rhs and not modifiers:
                return f"{indent}var {var_name} = new {rhs_type}("
            return m.group(0)

        new_content = var_pattern.sub(_var_replace, content)
        if new_content != content:
            _record_line_diffs(
                content, new_content, java_file, "java-var-inference",
                file_transforms,
            )
            content = new_content

        # (c) Multiline string concat -> text block TODO comment
        for m in multiline_concat_pattern.finditer(content):
            line_no = content[:m.start()].count("\n") + 1
            file_transforms.append({
                "file": str(java_file),
                "line": line_no,
                "rule": "java-text-block-suggestion",
                "before": m.group(0).strip()[:80],
                "after": "// TODO: Consider converting to text block (Java 13+)",
            })
        # Insert TODO comments above each multiline concat
        offset_adjust = 0
        for m in multiline_concat_pattern.finditer(original_content):
            pos = content.find(m.group(0), m.start() + offset_adjust)
            if pos >= 0:
                # Find beginning of this line
                line_start = content.rfind("\n", 0, pos)
                line_start = line_start + 1 if line_start >= 0 else 0
                indent = ""
                for ch in content[line_start:]:
                    if ch in (" ", "\t"):
                        indent += ch
                    else:
                        break
                comment = f"{indent}// TODO: Consider converting to text block (Java 13+)\n"
                content = content[:line_start] + comment + content[line_start:]
                offset_adjust += len(comment)

        # (d) Anonymous inner class -> lambda suggestion (comment only)
        for m in anon_class_pattern.finditer(content):
            line_no = content[:m.start()].count("\n") + 1
            file_transforms.append({
                "file": str(java_file),
                "line": line_no,
                "rule": "java-lambda-suggestion",
                "before": m.group(0).strip()[:80],
                "after": "// TODO: Consider replacing anonymous class with lambda",
            })

        # (e) Collectors.toList() -> .toList()
        new_content = collectors_tolist_pattern.sub('.toList()', content)
        if new_content != content:
            _record_line_diffs(
                content, new_content, java_file, "java-collectors-tolist",
                file_transforms,
            )
            content = new_content

        new_content = collectors_unmod_pattern.sub('.toList()', content)
        if new_content != content:
            _record_line_diffs(
                content, new_content, java_file, "java-collectors-unmod-tolist",
                file_transforms,
            )
            content = new_content

        # Write if changed
        if content != original_content:
            try:
                with open(java_file, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except OSError as exc:
                all_errors.append(f"Write error {java_file}: {exc}")
                continue

        all_transforms.extend(file_transforms)

    return {
        "files_processed": files_processed,
        "transformations_applied": all_transforms,
        "errors": all_errors,
    }


# ====================================================================
# 4. migrate_dotnet_framework
# ====================================================================
def migrate_dotnet_framework(source_path, output_path):
    """Migrate .NET Framework 4.x source to .NET 8.

    Applies regex transformations on ``.cs`` files for namespace and API
    changes, and generates skeleton ``appsettings.json`` / ``Program.cs``
    files where ``web.config`` / ``Global.asax`` are detected.

    Parameters
    ----------
    source_path : str | Path
    output_path : str | Path

    Returns
    -------
    dict
        Same structure as :func:`migrate_python2_to_3`.
    """
    source_path = Path(source_path)
    output_path = Path(output_path)
    _copy_source_tree(source_path, output_path)

    all_transforms = []
    all_errors = []
    files_processed = 0

    # -- C# regex rules ---------------------------------------------------
    _CS_RULES = [
        # (a) System.Web -> Microsoft.AspNetCore.Http (general)
        (
            "dotnet-system-web",
            re.compile(r'\busing\s+System\.Web\b(?!\.)', re.MULTILINE),
            'using Microsoft.AspNetCore.Http',
        ),
        # (b) System.Web.Mvc -> Microsoft.AspNetCore.Mvc
        (
            "dotnet-web-mvc",
            re.compile(r'\busing\s+System\.Web\.Mvc\b'),
            'using Microsoft.AspNetCore.Mvc',
        ),
        # (c) System.Web.Http -> Microsoft.AspNetCore.Mvc
        (
            "dotnet-web-http",
            re.compile(r'\busing\s+System\.Web\.Http\b'),
            'using Microsoft.AspNetCore.Mvc',
        ),
        # System.Web.Security -> Microsoft.AspNetCore.Authentication
        (
            "dotnet-web-security",
            re.compile(r'\busing\s+System\.Web\.Security\b'),
            'using Microsoft.AspNetCore.Authentication',
        ),
        # (d) HttpContext.Current -> // TODO: Inject IHttpContextAccessor
        (
            "dotnet-httpcontext-current",
            re.compile(r'\bHttpContext\.Current\b'),
            '/* TODO: Inject IHttpContextAccessor */ _httpContextAccessor.HttpContext',
        ),
        # (e) ConfigurationManager.AppSettings["key"]
        (
            "dotnet-configmanager",
            re.compile(r'ConfigurationManager\.AppSettings\["([^"]+)"\]'),
            r'_configuration["\1"] /* TODO: Use IConfiguration DI */',
        ),
        # (f) System.Data.SqlClient -> Microsoft.Data.SqlClient
        (
            "dotnet-sqlclient",
            re.compile(r'\busing\s+System\.Data\.SqlClient\b'),
            'using Microsoft.Data.SqlClient',
        ),
        # (g) WebClient -> TODO HttpClient
        (
            "dotnet-webclient",
            re.compile(r'\bnew\s+WebClient\(\)'),
            '/* TODO: Replace WebClient with HttpClient via IHttpClientFactory */ new HttpClient()',
        ),
        # Thread.Abort() removal
        (
            "dotnet-thread-abort",
            re.compile(r'\bThread\.Abort\(\)'),
            '/* REMOVED: Thread.Abort() — use CancellationToken instead */',
        ),
        # ActionResult -> IActionResult
        (
            "dotnet-iactionresult",
            re.compile(r'(\[Http\w+\]\s*\n\s*public\s+)ActionResult\b'),
            r'\1IActionResult',
        ),
    ]

    # Process .cs files
    for cs_file in _collect_files(output_path, {".cs"}):
        files_processed += 1
        try:
            with open(cs_file, "r", encoding="utf-8", errors="replace") as fh:
                original_content = fh.read()
        except OSError as exc:
            all_errors.append(f"Read error {cs_file}: {exc}")
            continue

        content = original_content

        for rule_id, pattern, replacement in _CS_RULES:
            new_content = pattern.sub(replacement, content)
            if new_content != content:
                _record_line_diffs(
                    content, new_content, cs_file, rule_id,
                    all_transforms,
                )
                content = new_content

        if content != original_content:
            try:
                with open(cs_file, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except OSError as exc:
                all_errors.append(f"Write error {cs_file}: {exc}")

    # -- web.config -> appsettings.json skeleton --------------------------
    for config_file in _collect_files(output_path, {".config"}):
        if config_file.name.lower() == "web.config":
            files_processed += 1
            _generate_appsettings_skeleton(config_file, all_transforms, all_errors)

    # -- Global.asax -> Program.cs skeleton --------------------------------
    for asax_file in _collect_files(output_path, {".asax"}):
        if asax_file.name.lower() == "global.asax":
            files_processed += 1
            _generate_program_cs_skeleton(asax_file, all_transforms, all_errors)

    return {
        "files_processed": files_processed,
        "transformations_applied": all_transforms,
        "errors": all_errors,
    }


def _generate_appsettings_skeleton(web_config_path, transforms_list, errors_list):
    """Parse web.config for <appSettings> and produce appsettings.json."""
    web_config_path = Path(web_config_path)
    try:
        with open(web_config_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as exc:
        errors_list.append(f"Read error {web_config_path}: {exc}")
        return

    # Extract key/value pairs from <add key="..." value="..." />
    kv_pattern = re.compile(
        r'<add\s+key="([^"]+)"\s+value="([^"]*)"',
        re.IGNORECASE,
    )
    settings = {}
    for m in kv_pattern.finditer(content):
        settings[m.group(1)] = m.group(2)

    # Extract connection strings
    conn_pattern = re.compile(
        r'<add\s+name="([^"]+)"\s+connectionString="([^"]*)"',
        re.IGNORECASE,
    )
    conn_strings = {}
    for m in conn_pattern.finditer(content):
        conn_strings[m.group(1)] = m.group(2)

    appsettings = {
        "Logging": {
            "LogLevel": {"Default": "Information", "Microsoft.AspNetCore": "Warning"}
        },
        "AllowedHosts": "*",
    }
    if settings:
        appsettings["AppSettings"] = settings
    if conn_strings:
        appsettings["ConnectionStrings"] = conn_strings

    output_json_path = web_config_path.parent / "appsettings.json"
    try:
        with open(output_json_path, "w", encoding="utf-8") as fh:
            json.dump(appsettings, fh, indent=2)
            fh.write("\n")
        transforms_list.append({
            "file": str(output_json_path),
            "line": 0,
            "rule": "dotnet-web-config-to-appsettings",
            "before": f"web.config ({len(settings)} settings, {len(conn_strings)} connection strings)",
            "after": "Generated appsettings.json skeleton",
        })
    except OSError as exc:
        errors_list.append(f"Write error {output_json_path}: {exc}")


def _generate_program_cs_skeleton(global_asax_path, transforms_list, errors_list):
    """Generate a minimal Program.cs skeleton to replace Global.asax."""
    global_asax_path = Path(global_asax_path)
    program_cs_path = global_asax_path.parent / "Program.cs"

    skeleton = textwrap.dedent("""\
        // CUI // SP-CTI
        // MIGRATION: Generated from Global.asax — review and customize
        // Original: {original}

        var builder = WebApplication.CreateBuilder(args);

        // Add services to the container.
        builder.Services.AddControllersWithViews();
        // TODO: Register additional services (DI) that were in Global.asax

        var app = builder.Build();

        // Configure the HTTP request pipeline.
        if (!app.Environment.IsDevelopment())
        {{
            app.UseExceptionHandler("/Home/Error");
            app.UseHsts();
        }}

        app.UseHttpsRedirection();
        app.UseStaticFiles();
        app.UseRouting();
        app.UseAuthorization();

        // TODO: Migrate route configuration from Global.asax Application_Start
        app.MapControllerRoute(
            name: "default",
            pattern: "{{controller=Home}}/{{action=Index}}/{{id?}}");

        app.Run();
    """).format(original=global_asax_path.name)

    try:
        with open(program_cs_path, "w", encoding="utf-8") as fh:
            fh.write(skeleton)
        transforms_list.append({
            "file": str(program_cs_path),
            "line": 0,
            "rule": "dotnet-global-asax-to-program-cs",
            "before": f"Global.asax ({global_asax_path.name})",
            "after": "Generated Program.cs skeleton (minimal pipeline)",
        })
    except OSError as exc:
        errors_list.append(f"Write error {program_cs_path}: {exc}")


# ====================================================================
# Helper — record line-level diffs
# ====================================================================
def _record_line_diffs(old_content, new_content, file_path, rule_id, transforms_list):
    """Compare two content strings line-by-line and record changes."""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    # Use difflib SequenceMatcher for accurate line mapping
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete", "insert"):
            old_chunk = old_lines[i1:i2]
            new_chunk = new_lines[j1:j2]
            max_len = max(len(old_chunk), len(new_chunk))
            for k in range(max_len):
                before = old_chunk[k] if k < len(old_chunk) else ""
                after = new_chunk[k] if k < len(new_chunk) else ""
                line_no = (i1 + k + 1) if k < len(old_chunk) else (j1 + k + 1)
                transforms_list.append({
                    "file": str(file_path),
                    "line": line_no,
                    "rule": rule_id,
                    "before": before,
                    "after": after,
                })


# ====================================================================
# 7. generate_migration_diff
# ====================================================================
def generate_migration_diff(original_path, transformed_path):
    """Generate a unified diff between two files.

    Parameters
    ----------
    original_path : str | Path
    transformed_path : str | Path

    Returns
    -------
    str
        Unified diff output.  Empty string if files are identical.
    """
    original_path = Path(original_path)
    transformed_path = Path(transformed_path)

    try:
        with open(original_path, "r", encoding="utf-8", errors="replace") as fh:
            original_lines = fh.readlines()
    except OSError:
        original_lines = []

    try:
        with open(transformed_path, "r", encoding="utf-8", errors="replace") as fh:
            transformed_lines = fh.readlines()
    except OSError:
        transformed_lines = []

    diff = difflib.unified_diff(
        original_lines,
        transformed_lines,
        fromfile=str(original_path),
        tofile=str(transformed_path),
        lineterm="",
    )
    return "\n".join(diff)


# ====================================================================
# 8. generate_migration_summary
# ====================================================================
def generate_migration_summary(source_path, output_path):
    """Walk both source and output directories and produce a summary.

    Returns
    -------
    dict
        ``{total_files: int, files_changed: int, total_lines_changed: int,
           per_file: [{file: str, lines_added: int, lines_removed: int,
                       lines_modified: int}], timestamp: str}``
    """
    source_path = Path(source_path)
    output_path = Path(output_path)

    per_file = []
    total_lines_changed = 0
    files_changed = 0
    total_files = 0

    for root, _dirs, files in os.walk(output_path):
        for fname in files:
            out_file = Path(root) / fname
            rel = out_file.relative_to(output_path)
            src_file = source_path / rel
            total_files += 1

            if not src_file.exists():
                # New file generated during migration
                try:
                    with open(out_file, "r", encoding="utf-8", errors="replace") as fh:
                        new_line_count = len(fh.readlines())
                except OSError:
                    new_line_count = 0
                per_file.append({
                    "file": str(rel),
                    "status": "new",
                    "lines_added": new_line_count,
                    "lines_removed": 0,
                    "lines_modified": 0,
                })
                total_lines_changed += new_line_count
                files_changed += 1
                continue

            try:
                with open(src_file, "r", encoding="utf-8", errors="replace") as fh:
                    src_lines = fh.readlines()
                with open(out_file, "r", encoding="utf-8", errors="replace") as fh:
                    out_lines = fh.readlines()
            except OSError:
                continue

            if src_lines == out_lines:
                continue

            files_changed += 1
            added = 0
            removed = 0
            modified = 0

            matcher = difflib.SequenceMatcher(None, src_lines, out_lines)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                elif tag == "replace":
                    modified += max(i2 - i1, j2 - j1)
                elif tag == "insert":
                    added += j2 - j1
                elif tag == "delete":
                    removed += i2 - i1

            file_total = added + removed + modified
            total_lines_changed += file_total
            per_file.append({
                "file": str(rel),
                "status": "modified",
                "lines_added": added,
                "lines_removed": removed,
                "lines_modified": modified,
            })

    return {
        "total_files": total_files,
        "files_changed": files_changed,
        "total_lines_changed": total_lines_changed,
        "per_file": per_file,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


# ====================================================================
# Dispatch helper
# ====================================================================
def _dispatch_migration(language, source_path, output_path, from_ver, to_ver):
    """Route to the correct migration function based on *language*."""
    canonical = _LANG_ALIAS.get(language.lower(), language.lower())

    if canonical == "python":
        return migrate_python2_to_3(source_path, output_path)
    elif canonical == "java":
        return migrate_java_version(source_path, output_path, from_ver, to_ver)
    elif canonical == "dotnet":
        return migrate_dotnet_framework(source_path, output_path)
    else:
        return {
            "files_processed": 0,
            "transformations_applied": [],
            "errors": [f"Unsupported language: {language}"],
        }


# ====================================================================
# 9. CLI main
# ====================================================================
def main():
    """CLI entry-point for the version migration tool."""
    parser = argparse.ArgumentParser(
        description=(
            "ICDEV Version Migration Tool — transform legacy code to "
            "newer language versions (CUI // SP-CTI)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s --source ./legacy --output ./migrated --language python --from 2.7 --to 3.11
              %(prog)s --source ./java8app --output ./java17app --language java --from 8 --to 17 --validate
              %(prog)s --source ./netfx --output ./net8 --language csharp --diff --json

            CUI // SP-CTI
        """),
    )
    parser.add_argument(
        "--source", required=True, type=str,
        help="Path to source directory containing legacy code",
    )
    parser.add_argument(
        "--output", required=True, type=str,
        help="Path to output directory (will be created; never overwrites source)",
    )
    parser.add_argument(
        "--language", required=True, type=str,
        choices=["python", "java", "csharp"],
        help="Source code language",
    )
    parser.add_argument(
        "--from", dest="from_ver", type=str, default=None,
        help="Source version (e.g. 2.7, 8, 4.8)",
    )
    parser.add_argument(
        "--to", dest="to_ver", type=str, default=None,
        help="Target version (e.g. 3.11, 17, 8.0)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run syntax validation on migrated files",
    )
    parser.add_argument(
        "--diff", action="store_true",
        help="Print unified diffs for changed files",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    output_path = Path(args.output).resolve()

    # Validate source
    if not source_path.is_dir():
        _error_exit(f"Source directory does not exist: {source_path}")

    # Safety: output must not be same as source
    if source_path == output_path:
        _error_exit("Output directory must differ from source directory")

    # Load external rules (informational; hard-coded rules always apply)
    ext_rules = load_upgrade_rules(
        args.language,
        from_version=args.from_ver,
        to_version=args.to_ver,
    )

    if not args.json_output:
        print(f"{CUI_BANNER}")
        print("=" * 68)
        print("ICDEV Version Migration Tool")
        print(f"  Language : {args.language}")
        print(f"  From     : {args.from_ver or 'auto'}")
        print(f"  To       : {args.to_ver or 'auto'}")
        print(f"  Source   : {source_path}")
        print(f"  Output   : {output_path}")
        print(f"  Rules    : {len(ext_rules)} external rules loaded")
        print("=" * 68)

    # Run migration
    result = _dispatch_migration(
        args.language, source_path, output_path,
        args.from_ver, args.to_ver,
    )

    # Validation pass
    validation_results = {}
    if args.validate:
        canonical = _LANG_ALIAS.get(args.language.lower(), args.language.lower())
        ext_map = {"python": {".py"}, "java": {".java"}, "dotnet": {".cs"}}
        exts = ext_map.get(canonical, set())
        for fpath in _collect_files(output_path, exts):
            rel = fpath.relative_to(output_path)
            src_equiv = source_path / rel
            vr = _validate_transforms(str(src_equiv), str(fpath), args.language)
            if not vr["valid"]:
                validation_results[str(rel)] = vr

        result["validation"] = {
            "files_validated": sum(
                1 for _ in _collect_files(output_path, exts)
            ),
            "files_with_errors": len(validation_results),
            "details": validation_results,
        }

    # Summary
    summary = generate_migration_summary(source_path, output_path)
    result["summary"] = summary

    # Diffs
    if args.diff and not args.json_output:
        print("\n--- DIFFS ---")
        for entry in summary.get("per_file", []):
            if entry.get("status") in ("modified", "new"):
                rel = entry["file"]
                orig = source_path / rel
                transformed = output_path / rel
                if orig.exists():
                    diff_text = generate_migration_diff(orig, transformed)
                    if diff_text:
                        print(diff_text)
                        print()

    # Output
    if args.json_output:
        # Ensure JSON-serializable
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human_report(result, args)

    print(f"\n{CUI_BANNER}")


def _print_human_report(result, args):
    """Print a human-readable migration report to stdout."""
    transforms = result.get("transformations_applied", [])
    errors = result.get("errors", [])
    summary = result.get("summary", {})

    print("\nMigration complete.")
    print(f"  Files processed     : {result.get('files_processed', 0)}")
    print(f"  Transformations     : {len(transforms)}")
    print(f"  Errors              : {len(errors)}")

    if summary:
        print("\nSummary:")
        print(f"  Total files in output  : {summary.get('total_files', 0)}")
        print(f"  Files changed          : {summary.get('files_changed', 0)}")
        print(f"  Total lines changed    : {summary.get('total_lines_changed', 0)}")

    if summary.get("per_file"):
        print("\nPer-file breakdown:")
        for pf in summary["per_file"]:
            status = pf.get("status", "modified")
            added = pf.get("lines_added", 0)
            removed = pf.get("lines_removed", 0)
            modified = pf.get("lines_modified", 0)
            print(
                f"  {pf['file']:50s}  "
                f"[{status}] +{added} -{removed} ~{modified}"
            )

    # Group transformations by rule
    if transforms:
        rule_counts = collections.Counter(t.get("rule", "unknown") for t in transforms)
        print("\nTransformations by rule:")
        for rule, count in rule_counts.most_common():
            print(f"  {rule:45s} : {count}")

    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  [ERROR] {err}")

    # Validation
    validation = result.get("validation")
    if validation:
        print("\nValidation:")
        print(f"  Files validated        : {validation.get('files_validated', 0)}")
        print(f"  Files with errors      : {validation.get('files_with_errors', 0)}")
        if validation.get("details"):
            for fpath, vr in validation["details"].items():
                for verr in vr.get("errors", []):
                    print(f"  [FAIL] {fpath}: {verr}")


def _error_exit(message):
    """Print error and exit with status 1."""
    print(f"[ERROR] {message}", file=sys.stderr)
    sys.exit(1)


# ====================================================================
# Entry point
# ====================================================================
if __name__ == "__main__":
    main()

# CUI // SP-CTI
