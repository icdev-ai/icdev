# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Capability extractor (Phase 1).

Turns the boolean "is this script safe?" AST scan from
``tools/marketplace/openclaw_bridge.analyze_script_safety`` into a **normalized
behavioral capability manifest**: instead of a yes/no verdict, every interesting
call site becomes a structured record describing *what the code can do* and the
*literal it does it to*. That manifest is the input to SIPA's downstream intent
reconciliation (capability-vs-RTM in Mode A, capability-vs-claim in Mode B).

Phase 1 detectors (Python ``ast`` only — never imports or executes the target):

  * ``network_egress`` — ``socket`` (socket/create_connection/create_server),
    ``http.client`` (HTTP[S]Connection), ``urllib.request`` (urlopen/Request/
    urlretrieve), ``requests`` and ``httpx`` HTTP verbs. Evidence captures the
    target host / URL literal when it is a constant.
  * ``filesystem`` — builtin ``open``/``io.open`` (with mode), pathlib ``Path``
    read/write methods (``read_text``/``write_text``/``read_bytes``/
    ``write_bytes``/``touch``), ``shutil`` copy/move/tree ops, and mutating
    ``os.*`` file ops (remove/rename/mkdir/...). Evidence captures the path
    literal + access mode.
  * ``process_exec`` — ``subprocess.*`` (run/call/Popen/...), ``os.system``/
    ``os.popen``/``os.exec*``/``os.spawn*``/``os.posix_spawn*``,
    ``multiprocessing`` Process/Pool, ``pty.spawn``. Evidence captures the
    command literal + whether ``shell=True``.

Each record is ``{file_path, function_name, capability_type, evidence, line_start,
line_end, risk_weight}`` and is persisted **append-only** to
``integrity_capabilities`` via the same RLS-aware path (``get_connection`` +
``tenant_id``/``classification`` stamping) the scanner adapters use, so the
capability writer and the finding writer can never drift.

Import aliasing is resolved (``import requests as rq`` -> ``rq.get`` is recognized
as ``requests.get``; ``from urllib.request import urlopen`` -> a bare ``urlopen``
is recognized as ``urllib.request.urlopen``) so renaming an import cannot hide a
capability. Future phases add the remaining ``CAPABILITY_TYPES`` (dynamic_code,
crypto, env_secret, serialization, obfuscation).
"""
from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from tools.integrity.constants import RISK_WEIGHTS_CAPABILITY
from tools.integrity.db.init_db import init_db

# Reuse ingest's context/backend helpers so the capability INSERT and the
# tenant/classification stamping match the finding writer exactly.
from tools.integrity.ingest import _backend_of, _caller_context

logger = logging.getLogger("icdev.integrity.capability_extractor")

# Directories never worth walking when a directory tree is scanned.
_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp", ".mypy_cache"}
_MAX_FILE_BYTES = 5_000_000  # skip files larger than 5 MB (generated/vendored blobs)


# --------------------------------------------------------------------------- #
# Detector tables — canonical (alias-resolved) call name -> capability_type
# --------------------------------------------------------------------------- #
# Exact canonical names that are network egress on their own.
_NETWORK_EXACT = {
    "socket.socket",
    "socket.create_connection",
    "socket.create_server",
    "urllib.request.urlopen",
    "urllib.request.Request",
    "urllib.request.urlretrieve",
    "http.client.HTTPConnection",
    "http.client.HTTPSConnection",
}
# Modules whose HTTP-verb / client attrs are network egress (requests/httpx).
_NETWORK_HTTP_MODULES = {"requests", "httpx"}
_NETWORK_HTTP_ATTRS = {
    "get", "post", "put", "delete", "patch", "head", "options", "request",
    "Session", "Client", "AsyncClient", "stream",
}

# Builtin / io open.
_FS_OPEN = {"open", "io.open"}
# pathlib.Path methods that are unambiguously a filesystem read/write. Generic
# ``.write``/``.read`` are intentionally excluded — they collide with sockets,
# StringIO, etc. and would flood the manifest with false positives.
_FS_PATH_READ = {"read_text", "read_bytes"}
_FS_PATH_WRITE = {"write_text", "write_bytes", "touch"}
_FS_PATH_METHODS = _FS_PATH_READ | _FS_PATH_WRITE
# shutil filesystem operations (all mutate or copy bytes on disk).
_FS_SHUTIL_ATTRS = {
    "copy", "copy2", "copyfile", "copyfileobj", "copytree", "copymode",
    "copystat", "move", "rmtree", "make_archive", "unpack_archive",
}
# Mutating os.* file operations (path-taking; all write/destroy on disk).
_FS_OS_EXACT = {
    "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.mkdir",
    "os.makedirs", "os.rename", "os.renames", "os.replace", "os.truncate",
    "os.chmod", "os.chown", "os.link", "os.symlink",
}

# subprocess attrs that spawn a child process.
_PROC_SUBPROCESS_ATTRS = {
    "run", "call", "check_call", "check_output", "Popen",
    "getoutput", "getstatusoutput",
}
# Exact os.* process-spawning names not covered by the os.exec*/os.spawn* prefixes.
_PROC_OS_EXACT = {"os.system", "os.popen", "os.posix_spawn", "os.posix_spawnp"}
_PROC_MULTIPROCESSING = {"multiprocessing.Process", "multiprocessing.Pool"}


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _literal(node: Optional[ast.AST]) -> Any:
    """Return a constant's Python value, else ``None`` (f-strings/names/etc.)."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _dotted(func: ast.AST) -> Optional[str]:
    """Flatten an attribute chain (``a.b.c``) to a dotted string, else ``None``.

    Returns ``None`` for calls whose receiver is not a plain name chain (e.g. a
    call result ``foo().bar`` or a subscript ``d['k'].bar``) — those cannot be
    resolved to a canonical API without data-flow analysis.
    """
    parts: list[str] = []
    cur: ast.AST = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _canonical_name(func: ast.AST, aliases: dict[str, str]) -> Optional[str]:
    """Resolve a call's dotted name through the import-alias map.

    Only the *first* segment is rewritten (``rq.get`` -> ``requests.get`` when
    ``import requests as rq``; bare ``urlopen`` -> ``urllib.request.urlopen`` when
    ``from urllib.request import urlopen``), so deeper attribute access is
    preserved verbatim.
    """
    dotted = _dotted(func)
    if not dotted:
        return None
    segs = dotted.split(".")
    first = aliases.get(segs[0])
    if first is None:
        return dotted
    rest = segs[1:]
    return ".".join([first, *rest]) if rest else first


def _build_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local import bindings to their canonical module/attribute path.

    ``import socket`` -> {socket: socket}; ``import requests as rq`` ->
    {rq: requests}; ``import urllib.request`` -> {urllib: urllib} (the top
    package is what gets bound); ``from urllib.request import urlopen as uo`` ->
    {uo: urllib.request.urlopen}.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    # `import a.b.c` binds the top package `a`; canonical is `a`.
                    top = a.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — cannot resolve to a stdlib path
                continue
            module = node.module or ""
            for a in node.names:
                local = a.asname or a.name
                aliases[local] = f"{module}.{a.name}" if module else a.name
    return aliases


# --------------------------------------------------------------------------- #
# Per-capability evidence extraction
# --------------------------------------------------------------------------- #
def _first_arg(node: ast.Call) -> Optional[ast.AST]:
    return node.args[0] if node.args else None


def _network_evidence(name: str, node: ast.Call) -> dict:
    """Capture the target host / URL literal for a network egress call."""
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["literal"] = lit
        if "://" in lit:
            ev["url"] = lit
        else:
            ev["host"] = lit
    elif isinstance(arg, (ast.Tuple, ast.List)) and arg.elts:
        # socket.create_connection((host, port)) / socket.create_server(addr).
        host = _literal(arg.elts[0])
        if isinstance(host, str):
            ev["host"] = host
            ev["literal"] = host
    for kw in node.keywords:
        if kw.arg in ("url", "host"):
            v = _literal(kw.value)
            if isinstance(v, str):
                ev[kw.arg] = v
                ev.setdefault("literal", v)
    return ev


def _filesystem_evidence(name: str, node: ast.Call, path_attr: Optional[str]) -> dict:
    """Capture the path literal + access mode for a filesystem call.

    ``path_attr`` is the pathlib method name when the call is a ``Path`` method
    (its mode is implied by the method); otherwise mode comes from ``open``'s
    second argument, or defaults to ``w`` for the mutating shutil/os ops.
    """
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["path"] = lit
        ev["literal"] = lit
    # second positional (often a dst path for shutil.copy/move) is useful context.
    if len(node.args) >= 2:
        second = _literal(node.args[1])
        if isinstance(second, str):
            ev["dest"] = second

    if path_attr in _FS_PATH_WRITE:
        ev["mode"] = "w"
    elif path_attr in _FS_PATH_READ:
        ev["mode"] = "r"
    elif name in _FS_OPEN:
        ev["mode"] = _open_mode(node)
    else:
        # shutil.* / os.* mutating ops.
        ev["mode"] = "w"
    return ev


def _open_mode(node: ast.Call) -> str:
    """Resolve the access mode of an ``open()`` call (positional or ``mode=``)."""
    if len(node.args) >= 2:
        m = _literal(node.args[1])
        if isinstance(m, str):
            return m
    for kw in node.keywords:
        if kw.arg == "mode":
            m = _literal(kw.value)
            if isinstance(m, str):
                return m
    return "r"


def _process_evidence(name: str, node: ast.Call) -> dict:
    """Capture the command literal + shell flag for a process-spawn call."""
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["command"] = lit
        ev["literal"] = lit
    elif isinstance(arg, (ast.List, ast.Tuple)):
        parts = [_literal(e) for e in arg.elts]
        strs = [p for p in parts if isinstance(p, str)]
        if strs:
            ev["command"] = " ".join(strs)
            ev["literal"] = strs
    for kw in node.keywords:
        if kw.arg == "shell" and _literal(kw.value) is True:
            ev["shell"] = True
    return ev


# --------------------------------------------------------------------------- #
# Detection — canonical name + call node -> (capability_type, evidence) | None
# --------------------------------------------------------------------------- #
def _classify(name: Optional[str], node: ast.Call) -> Optional[tuple[str, dict]]:
    """Map a resolved call to a capability record, or ``None`` if uninteresting."""
    # The immediate attribute name is available even when the receiver is not a
    # plain name chain (e.g. ``Path('/x').read_text()`` — receiver is a Call, so
    # ``name`` is None). pathlib's distinctive read/write methods are matched on
    # this attr so a chained construction can't hide a filesystem capability.
    attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
    if attr in _FS_PATH_METHODS:
        return "filesystem", _filesystem_evidence(attr, node, attr)

    if not name:
        return None
    head = name.split(".")[0]
    tail = name.rsplit(".", 1)[-1]

    # --- network egress ---------------------------------------------------- #
    if name in _NETWORK_EXACT:
        return "network_egress", _network_evidence(name, node)
    if head in _NETWORK_HTTP_MODULES and tail in _NETWORK_HTTP_ATTRS:
        return "network_egress", _network_evidence(name, node)

    # --- process execution ------------------------------------------------- #
    if head == "subprocess" and tail in _PROC_SUBPROCESS_ATTRS:
        return "process_exec", _process_evidence(name, node)
    if name in _PROC_OS_EXACT or name.startswith("os.exec") or name.startswith("os.spawn"):
        return "process_exec", _process_evidence(name, node)
    if name in _PROC_MULTIPROCESSING:
        return "process_exec", _process_evidence(name, node)
    if name == "pty.spawn":
        return "process_exec", _process_evidence(name, node)

    # --- filesystem -------------------------------------------------------- #
    if name in _FS_OPEN:
        return "filesystem", _filesystem_evidence(name, node, None)
    if head == "shutil" and tail in _FS_SHUTIL_ATTRS:
        return "filesystem", _filesystem_evidence(name, node, None)
    if name in _FS_OS_EXACT:
        return "filesystem", _filesystem_evidence(name, node, None)

    return None


# --------------------------------------------------------------------------- #
# AST visitor — tracks the enclosing function for each capability call
# --------------------------------------------------------------------------- #
class _CapabilityVisitor(ast.NodeVisitor):
    """Walk a module, emitting a capability record per matching call site.

    Maintains a stack of enclosing function names so each record carries the
    innermost ``function_name`` (``None`` for module-level code).
    """

    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self._func_stack: list[str] = []
        self.records: list[dict] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # async defs tracked identically

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _canonical_name(node.func, self.aliases)
        hit = _classify(name, node)
        if hit:
            cap_type, evidence = hit
            self.records.append(
                {
                    "function_name": self._func_stack[-1] if self._func_stack else None,
                    "capability_type": cap_type,
                    "evidence": evidence,
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", node.lineno),
                    "risk_weight": RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0),
                }
            )
        self.generic_visit(node)


# --------------------------------------------------------------------------- #
# Public extraction API
# --------------------------------------------------------------------------- #
def _rel(path: Path, root: Path) -> str:
    """Path relative to the scan root (portable); original string on cross-drive."""
    try:
        return os.path.relpath(path, root)
    except (ValueError, OSError):
        return str(path)


def _extract_file(file_path: Path, root: Path) -> list[dict]:
    """Extract capability records from a single ``.py`` file.

    A read error or syntax error yields ``[]`` (a file we cannot parse statically
    contributes no capabilities) — never an exception that aborts a tree scan.
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        logger.warning("capability_extractor: cannot read %s: %s", file_path, exc)
        return []
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        logger.warning("capability_extractor: cannot parse %s: %s", file_path, exc)
        return []

    visitor = _CapabilityVisitor(_build_aliases(tree))
    visitor.visit(tree)

    rel = _rel(file_path, root)
    for rec in visitor.records:
        rec["file_path"] = rel
    return visitor.records


def _iter_py_files(root: Path):
    """Yield ``.py``/``.pyw`` files under ``root``, skipping vendored/cache dirs."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            if fn.endswith((".py", ".pyw")):
                fp = Path(dirpath) / fn
                try:
                    if fp.stat().st_size > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield fp


def extract(path: str | os.PathLike) -> list[dict]:
    """Extract the Phase-1 capability manifest for a file or directory tree.

    Args:
        path: a single ``.py`` file or a directory to walk recursively.

    Returns:
        A list of capability records, each
        ``{file_path, function_name, capability_type, evidence, line_start,
        line_end, risk_weight}``. ``file_path`` is relative to ``path`` (for a
        directory) or to the file's parent (for a single file). ``evidence`` is a
        plain dict (JSON-serializable) — it is ``json.dumps``-encoded only at the
        persistence boundary.
    """
    root = Path(path)
    records: list[dict] = []
    if root.is_dir():
        for fp in _iter_py_files(root):
            records.extend(_extract_file(fp, root))
    elif root.is_file():
        records.extend(_extract_file(root, root.parent))
    else:
        logger.warning("capability_extractor: path does not exist: %s", root)
    return records


# --------------------------------------------------------------------------- #
# Persistence — append-only to integrity_capabilities
# --------------------------------------------------------------------------- #
_CAPABILITY_SQL = (
    "INSERT INTO integrity_capabilities "
    "(assessment_id, file_path, function_name, capability_type, evidence, "
    "line_start, line_end, risk_weight, tenant_id, classification) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_capability(conn: Any, params: tuple) -> int:
    """Insert one append-only ``integrity_capabilities`` row; return its PK."""
    if _backend_of(conn) == "postgresql":
        cur = conn.execute(_CAPABILITY_SQL.replace("?", "%s") + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(_CAPABILITY_SQL, params)
    conn.commit()
    return int(cur.lastrowid or 0)


def _persist(conn: Any, assessment_id: int, records: list[dict]) -> list[int]:
    """Append every capability record to ``integrity_capabilities``; return ids."""
    tenant_id, classification, _ = _caller_context()
    ids: list[int] = []
    for rec in records:
        cap_id = _insert_capability(
            conn,
            (
                assessment_id,
                rec["file_path"],
                rec.get("function_name"),
                rec["capability_type"],
                json.dumps(rec["evidence"]),
                rec.get("line_start"),
                rec.get("line_end"),
                rec.get("risk_weight", 0.0),
                tenant_id,
                classification,
            ),
        )
        ids.append(cap_id)
    return ids


def extract_and_persist(assessment_id: int, path: str | os.PathLike, conn: Any = None) -> dict:
    """Extract the capability manifest for ``path`` and persist it append-only.

    Opens an RLS-aware connection when ``conn`` is ``None`` (closing it on exit);
    ``init_db`` is invoked idempotently so this can run standalone before any
    assessment's tables exist. Returns a summary
    ``{"assessment_id", "capabilities_persisted", "capability_ids", "by_type"}``.
    """
    records = extract(path)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        ids = _persist(conn, assessment_id, records)
    finally:
        if own_conn:
            conn.close()

    by_type: dict[str, int] = {}
    for rec in records:
        by_type[rec["capability_type"]] = by_type.get(rec["capability_type"], 0) + 1

    return {
        "assessment_id": assessment_id,
        "capabilities_persisted": len(ids),
        "capability_ids": ids,
        "by_type": by_type,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SIPA capability extractor — Python AST -> integrity_capabilities "
        "(network_egress / filesystem / process_exec)"
    )
    parser.add_argument("--path", required=True, help="file or directory to scan")
    parser.add_argument(
        "--assessment-id",
        type=int,
        default=None,
        help="integrity_assessments.id to attach + persist capabilities to "
        "(omit to print the manifest without persisting)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    if args.assessment_id is not None:
        result = extract_and_persist(args.assessment_id, args.path)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"SIPA capabilities — assessment {result['assessment_id']}")
            for cap_type, n in sorted(result["by_type"].items()):
                print(f"  {cap_type}: {n}")
            print(f"  total: {result['capabilities_persisted']} capability record(s) persisted")
        return

    records = extract(args.path)
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        print(f"SIPA capability manifest — {args.path}")
        for rec in records:
            loc = f"{rec['file_path']}:{rec['line_start']}"
            fn = rec["function_name"] or "<module>"
            print(f"  [{rec['capability_type']}] {loc} in {fn}() — {rec['evidence'].get('api', '')}")
        print(f"  total: {len(records)} capability record(s)")


if __name__ == "__main__":
    main()
