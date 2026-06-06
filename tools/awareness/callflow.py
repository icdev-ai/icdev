#!/usr/bin/env python3
# CUI // SP-CTI
"""Call-Flow Graph — function/module call graph over the ICDEV tools tree.

Adapts graphify's call-flow export onto ICDEV's existing self-awareness graph.
Deterministic, stdlib-only, air-gap safe.

What it does:
  * Builds a symbol index (top-level function/class name -> defining module),
    keeping only UNAMBIGUOUS names to avoid false edges.
  * Resolves direct calls (`foo(...)` where `foo` is a known imported/defined
    symbol) into caller_module -> callee_module edges. (Attribute calls like
    `obj.method()` are intentionally skipped — they need type info and would
    create false edges.)
  * Exports a standalone call-flow HTML adjacency view.
  * Optionally persists module-level `calls` edges into the existing
    `kg_edges` table (graph `kg-icdev-self-awareness`) for modules already
    indexed by component_indexer — enriches the /components-map graph.

CLI:
    python tools/awareness/callflow.py --json
    python tools/awareness/callflow.py --export-html .tmp/callflow.html
    python tools/awareness/callflow.py --scan            # persist module edges
    python tools/awareness/callflow.py --scope tools/codegen --json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Names too common to resolve to a single definition meaningfully.
_SKIP_NAMES = {"main", "run", "__init__", "setup", "teardown"}


def _iter_py_files(scope: Optional[str]) -> List[Path]:
    root = BASE_DIR / "tools"
    if scope:
        root = BASE_DIR / scope
    if root.is_file() and root.suffix == ".py":
        return [root]
    return sorted(root.rglob("*.py"))


def _rel(path: Path) -> str:
    return path.relative_to(BASE_DIR).as_posix() if path.is_relative_to(BASE_DIR) else str(path)


def build_symbol_index(scope: Optional[str] = None) -> Dict[str, str]:
    """Map top-level function/class name -> module rel path, for UNAMBIGUOUS names.

    A name defined in more than one module is dropped (ambiguous → no reliable
    resolution). Names in _SKIP_NAMES are always dropped.
    """
    name_to_modules: Dict[str, Set[str]] = {}
    for py in _iter_py_files(scope):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        rel = _rel(py)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in _SKIP_NAMES or node.name.startswith("_"):
                    continue
                name_to_modules.setdefault(node.name, set()).add(rel)
    return {name: next(iter(mods)) for name, mods in name_to_modules.items() if len(mods) == 1}


def build_call_graph(scope: Optional[str] = None, index: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build function- and module-level call edges over the scoped tree.

    Returns a dict: {functions, edges, module_edges}.
      functions:    {"module::func": {"module","line"}}
      edges:        [{"caller_module","caller_func","callee_module","callee_func"}]
      module_edges: sorted list of [src_module, dst_module]
    """
    # Index across the WHOLE tools tree so calls resolve even when scope is narrow.
    symbol_index = index if index is not None else build_symbol_index(None)

    functions: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, str]] = []
    module_edges: Set[Tuple[str, str]] = set()

    for py in _iter_py_files(scope):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        caller_module = _rel(py)
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller_func = node.name
            functions[f"{caller_module}::{caller_func}"] = {"module": caller_module, "line": node.lineno}
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                callee_name = sub.func.id if isinstance(sub.func, ast.Name) else None
                if not callee_name:
                    continue
                callee_module = symbol_index.get(callee_name)
                if not callee_module or callee_module == caller_module:
                    continue
                edges.append(
                    {
                        "caller_module": caller_module,
                        "caller_func": caller_func,
                        "callee_module": callee_module,
                        "callee_func": callee_name,
                    }
                )
                module_edges.add((caller_module, callee_module))

    return {
        "functions": functions,
        "edges": edges,
        "module_edges": sorted([list(e) for e in module_edges]),
    }


def export_html(graph: Dict[str, Any], out_path: Path) -> Path:
    """Write a standalone, dependency-free call-flow adjacency HTML view."""
    from collections import defaultdict

    adj: Dict[str, List[str]] = defaultdict(list)
    for src, dst in graph["module_edges"]:
        adj[src].append(dst)

    rows: List[str] = []
    for src in sorted(adj):
        targets = "".join(f"<li>{dst}</li>" for dst in sorted(adj[src]))
        rows.append(f"<section><h3>{src}</h3><ul>{targets}</ul></section>")

    body = "\n".join(rows) or "<p>No call edges found.</p>"
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>ICDEV Call-Flow</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;background:#0d1117;color:#c9d1d9}"
        "h1{color:#58a6ff}section{margin:0 0 1rem;padding:.5rem 1rem;border-left:3px solid #30363d}"
        "h3{margin:.2rem 0;color:#79c0ff}ul{margin:.2rem 0}li{color:#8b949e}</style></head><body>"
        f"<h1>ICDEV Call-Flow Graph</h1><p>{len(graph['module_edges'])} module edges, "
        f"{len(graph['functions'])} functions, {len(graph['edges'])} call edges.</p>"
        f"{body}</body></html>"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def persist_module_edges(graph: Dict[str, Any]) -> int:
    """Upsert module-level `calls` edges into kg_edges for already-indexed modules.

    Only emits edges where BOTH endpoints already have a node in kg_nodes
    (consistent graph). Returns the number of edges upserted; 0 if the KG/DB
    is unavailable.
    """
    try:
        from tools.awareness.component_indexer import GRAPH_ID, _node_id
        from tools.db.storage import get_connection
    except Exception:
        return 0

    import hashlib

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM kg_nodes WHERE graph_id = ?", (GRAPH_ID,))
            known_ids = {dict(r)["id"] for r in cur.fetchall()}

            count = 0
            for src, dst in graph["module_edges"]:
                src_id = _node_id("tool", src)
                dst_id = _node_id("tool", dst)
                if src_id not in known_ids or dst_id not in known_ids:
                    continue
                edge_id = "callflow-" + hashlib.sha256(f"{src_id}->{dst_id}".encode()).hexdigest()[:16]
                cur.execute(
                    "INSERT INTO kg_edges (id, graph_id, source_id, target_id, relationship, weight, properties) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET weight = excluded.weight",
                    (edge_id, GRAPH_ID, src_id, dst_id, "calls", 1.0, "{}"),
                )
                count += 1
            conn.commit()
            return count
    except Exception:
        return 0


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Call-Flow Graph builder/exporter")
    parser.add_argument("--scope", type=str, default="", help="Limit to a subdir/file under the repo (e.g. tools/codegen)")
    parser.add_argument("--json", action="store_true", help="Print the graph as JSON")
    parser.add_argument("--export-html", type=str, default="", help="Write call-flow HTML to this path")
    parser.add_argument("--scan", action="store_true", help="Persist module-level calls edges to kg_edges")
    parser.add_argument("--stats", action="store_true", help="Print summary counts only")
    args = parser.parse_args()

    scope = args.scope or None
    graph = build_call_graph(scope)

    if args.export_html:
        out = Path(args.export_html)
        if not out.is_absolute():
            out = BASE_DIR / out
        export_html(graph, out)
        print(f"Call-flow HTML written: {out}")

    if args.scan:
        n = persist_module_edges(graph)
        print(f"Persisted {n} module-level 'calls' edge(s) to kg_edges")

    if args.stats and not args.json:
        print(
            json.dumps(
                {
                    "functions": len(graph["functions"]),
                    "call_edges": len(graph["edges"]),
                    "module_edges": len(graph["module_edges"]),
                },
                indent=2,
            )
        )
    if args.json:
        print(json.dumps(graph, indent=2))


if __name__ == "__main__":
    main()
