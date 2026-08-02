#!/usr/bin/env python3
# CUI // SP-CTI
"""Edge Deriver — mechanical dependency edges for `kg-icdev-self-awareness`.

`component_indexer.py` enumerates ~2,400 typed component NODES but shipped
with exactly one edge heuristic (canvas title keyword → goal), so the graph
was a bag of nodes with no relationships. A node bag cannot answer the one
question an internal developer platform exists to answer: *what breaks if
this changes*.

This module derives the relationships from evidence that is already on disk.
The design rule is **mechanical over similar**: an `import` statement is a
fact, a `CREATE TABLE` is a fact, a `@bp.route(...)` is a fact — a title that
shares a word with another title is a guess. Every edge therefore records the
method it came from in `properties.derivation`, plus `properties.mechanical`
so a consumer can weight or filter the guesses out entirely.

Derivations
-----------
=============================  ===========================  ==============
derivation                     relationship                 mechanical
=============================  ===========================  ==============
python_import_ast              imports                      yes
documented_command             invokes                      yes
ddl_create_table               creates_table                yes
sql_table_reference            uses_table                   yes
flask_route_decorator          serves_route                 yes
component_registry_iqe         provides_collection          yes
component_registry_depends_on  depends_on                   yes
title_keyword_match            referenced_by_goal           NO (heuristic)
=============================  ===========================  ==============

Four node types are contributed alongside the edges so the mechanical
relationships have somewhere to land: ``migration``, ``db_table``, ``route``
and ``iqe_collection``. They are file-backed (``properties.file_path`` points
at the migration / blueprint / adapter that defines them) so
``component_indexer.prune_stale_nodes`` reconciles them against disk like any
other node.

Config: ``args/awareness_config.yaml`` → ``edges`` section. Every derivation
can be switched off individually and the per-module fan-out is capped, so a
pathological file cannot flood the graph.

CLI::

    python tools/awareness/edge_deriver.py --derive --json
    python tools/awareness/edge_deriver.py --derive --dry-run --json
    python tools/awareness/edge_deriver.py --dependents tools/db/storage.py --json
    python tools/awareness/edge_deriver.py --dependents tools/db/storage.py --depth 2
    python tools/awareness/edge_deriver.py --dependencies tools/awareness/health_prober.py
    python tools/awareness/edge_deriver.py --stats --json

See docs/features/internal-awareness-engine.md.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from tools.logging.icdev_logger import get_logger

LOG = get_logger("edge_deriver")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.awareness.component_indexer import (  # noqa: E402
    GRAPH_ID,
    Edge,
    Node,
    _edge_id,
    _node_id,
    _truncate,
)

try:
    from tools.db.storage import get_connection  # noqa: E402
except ImportError:  # pragma: no cover -- slim/air-gap installs
    get_connection = None  # type: ignore[assignment]

try:
    import yaml  # noqa: E402
except ImportError:  # pragma: no cover -- PyYAML is optional
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Derivation registry
# ---------------------------------------------------------------------------

# `weight` doubles as a confidence score: 1.0 = the evidence *is* the
# relationship (an import statement), <1.0 = the evidence implies it.
# `mechanical` is the honest flag — False means "inferred from similarity".
DERIVATIONS: Dict[str, Dict[str, Any]] = {
    "python_import_ast": {
        "mechanical": True,
        "weight": 1.0,
        "evidence_kind": "import statement",
    },
    "documented_command": {
        "mechanical": True,
        "weight": 0.9,
        "evidence_kind": "documented `python tools/...` invocation",
    },
    "ddl_create_table": {
        "mechanical": True,
        "weight": 1.0,
        "evidence_kind": "CREATE TABLE statement",
    },
    "sql_table_reference": {
        "mechanical": True,
        "weight": 0.8,
        "evidence_kind": "SQL referencing a known table",
    },
    "flask_route_decorator": {
        "mechanical": True,
        "weight": 1.0,
        "evidence_kind": "@bp.route decorator",
    },
    "component_registry_iqe": {
        "mechanical": True,
        "weight": 1.0,
        "evidence_kind": "component_registry.yaml iqe binding",
    },
    "component_registry_module": {
        "mechanical": True,
        "weight": 1.0,
        "evidence_kind": "component_registry.yaml module declaration",
    },
    "component_registry_depends_on": {
        "mechanical": True,
        "weight": 1.0,
        "evidence_kind": "component_registry.yaml depends_on",
    },
    "title_keyword_match": {
        "mechanical": False,
        "weight": 0.4,
        "evidence_kind": "shared keyword between titles",
    },
}

_DEFAULT_LIMITS: Dict[str, int] = {
    "max_imports_per_module": 60,
    "max_tables_per_module": 15,
    "max_routes_per_module": 80,
    "max_commands_per_document": 40,
}

_CONFIG_PATH = BASE_DIR / "args" / "awareness_config.yaml"


def load_edge_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the ``edges`` section of args/awareness_config.yaml.

    Missing file / missing section / no PyYAML all degrade to "everything on
    with default limits" — the deriver must never go silent just because the
    config is absent in a slim checkout.
    """
    enabled = {k: True for k in DERIVATIONS}
    cfg: Dict[str, Any] = {
        "enabled": True,
        "derivations": enabled,
        "limits": dict(_DEFAULT_LIMITS),
    }
    cfg_path = path or _CONFIG_PATH
    if yaml is None or not cfg_path.exists():
        return cfg
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover -- malformed yaml
        LOG.warning("edge config read failed (%s): %s", cfg_path, exc)
        return cfg
    section = raw.get("edges") or {}
    if not isinstance(section, dict):
        return cfg
    if "enabled" in section:
        cfg["enabled"] = bool(section["enabled"])
    for key, value in (section.get("derivations") or {}).items():
        if key in enabled:
            enabled[key] = bool(value)
    for key, value in (section.get("limits") or {}).items():
        if key in cfg["limits"]:
            try:
                cfg["limits"][key] = max(0, int(value))
            except (TypeError, ValueError):
                pass
    return cfg


def _mk_edge(
    source: Node,
    target: Node,
    relationship: str,
    derivation: str,
    evidence: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Edge:
    """Build an Edge that carries its own provenance."""
    meta = DERIVATIONS.get(derivation) or {"mechanical": False, "weight": 0.5}
    props: Dict[str, Any] = {
        "derivation": derivation,
        "mechanical": bool(meta["mechanical"]),
        "evidence": _truncate(evidence, 200),
    }
    if extra:
        props.update(extra)
    return Edge(
        id=_edge_id(source.id, target.id, relationship),
        source_id=source.id,
        target_id=target.id,
        relationship=relationship,
        weight=float(meta["weight"]),
        properties=props,
    )


def _norm(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip().lstrip("./")


# ---------------------------------------------------------------------------
# Node index — resolve an import / path / module name back to a graph node
# ---------------------------------------------------------------------------


class NodeIndex:
    """Lookup structures over a node list, built once per derive run."""

    def __init__(self, nodes: Iterable[Node]) -> None:
        self.nodes: List[Node] = list(nodes)
        self.by_id: Dict[str, Node] = {}
        self.by_path: Dict[str, Node] = {}
        self.by_module: Dict[str, Node] = {}
        self.canvas_by_dir: Dict[str, Node] = {}

        for n in self.nodes:
            self.by_id.setdefault(n.id, n)
            path = _norm(n.file_path)
            if not path:
                continue
            self.by_path.setdefault(path, n)
            if n.entity_type == "canvas_module":
                self.canvas_by_dir.setdefault(path, n)
            if path.endswith(".py"):
                module = path[:-3].replace("/", ".")
                if module.endswith(".__init__"):
                    module = module[: -len(".__init__")]
                self.by_module.setdefault(module, n)

    # -- registration (for nodes this module contributes) -------------------

    def add(self, node: Node) -> Node:
        """Register a derived node; returns the canonical instance."""
        existing = self.by_id.get(node.id)
        if existing is not None:
            return existing
        self.by_id[node.id] = node
        self.nodes.append(node)
        return node

    # -- resolution ---------------------------------------------------------

    def resolve_path(self, rel_path: str) -> Optional[Node]:
        """Map a repo-relative path to its node, falling back to the owning canvas."""
        path = _norm(rel_path)
        if not path:
            return None
        node = self.by_path.get(path)
        if node is not None:
            return node
        for canvas_dir, canvas_node in self.canvas_by_dir.items():
            if path.startswith(canvas_dir + "/"):
                return canvas_node
        return None

    def resolve_module(self, dotted: str) -> Optional[Node]:
        """Map a dotted module name (``tools.x.y`` / ``icdev.tools.x.y``) to a node.

        Walks up the dotted path so ``from tools.db.storage import get_connection``
        still resolves when only ``tools.db.storage`` is a node, and so
        ``tools.network_canvas.blueprint`` falls back to the canvas node.
        """
        name = (dotted or "").strip()
        if name.startswith("icdev."):
            name = name[len("icdev."):]
        if not name.startswith("tools."):
            return None
        parts = name.split(".")
        while len(parts) > 1:
            node = self.by_module.get(".".join(parts))
            if node is not None:
                return node
            node = self.canvas_by_dir.get("/".join(parts))
            if node is not None:
                return node
            parts.pop()
        return None


# ---------------------------------------------------------------------------
# Derived node factories
# ---------------------------------------------------------------------------


def _migration_node(rel_path: str, label: str) -> Node:
    return Node(
        id=_node_id("migration", rel_path),
        label=label,
        entity_type="migration",
        description=f"Database migration {label}",
        file_path=rel_path,
        extra={"kind": "migration"},
    )


def _db_table_node(table: str, defined_in: str) -> Node:
    return Node(
        id=_node_id("db_table", table),
        label=table,
        entity_type="db_table",
        description=f"Database table `{table}` (defined in {defined_in})",
        file_path=defined_in,
        extra={"table": table, "defined_in": defined_in},
    )


def _route_node(route_path: str, defined_in: str) -> Node:
    return Node(
        id=_node_id("route", route_path),
        label=route_path,
        entity_type="route",
        description=f"HTTP route {route_path} (served by {defined_in})",
        file_path=defined_in,
        extra={"route": route_path, "defined_in": defined_in},
    )


def _component_node(entry: Dict[str, Any]) -> Node:
    key = entry["key"]
    return Node(
        id=_node_id("component", key),
        label=entry.get("display_name") or key,
        entity_type="component",
        description=_truncate(
            entry.get("description") or f"Registered {entry.get('kind', 'component')} `{key}`"
        ),
        file_path=_REGISTRY_PATH_REL,
        extra={
            "component_key": key,
            "kind": entry.get("kind", ""),
            "env_flag": entry.get("env_flag", ""),
            "url_prefix": entry.get("url_prefix", ""),
            "module": entry.get("module") or "",
        },
    )


def _iqe_collection_node(collection: str, adapter_path: str, component_key: str) -> Node:
    return Node(
        id=_node_id("iqe_collection", collection),
        label=collection,
        entity_type="iqe_collection",
        description=f"IQE collection `{collection}` registered by {component_key}",
        file_path=adapter_path,
        extra={"collection": collection, "component": component_key},
    )


# ---------------------------------------------------------------------------
# Deriver 1 + 4 + 5: single pass over Python sources
#   imports, uses_table, serves_route
# ---------------------------------------------------------------------------

_PY_SOURCE_TYPES = ("tool", "reflex", "mcp_server")

_ROUTE_RE = re.compile(r"@\w+\.route\(\s*[\"']([^\"']+)[\"']")

# Table references in SQL string literals. Deliberately anchored on the SQL
# keyword so a bare identifier never matches; the captured name is then
# validated against the set of tables we actually found DDL for, which is what
# keeps this from degenerating into "any word after FROM".
_SQL_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|INSERT\s+INTO|INTO|UPDATE|DELETE\s+FROM)\s+"
    r"[\"'`\[]?([a-zA-Z_][A-Za-z0-9_]*)[\"'`\]]?",
    re.IGNORECASE,
)


def _iter_imported_modules(tree: ast.AST) -> Iterable[Tuple[str, int]]:
    """Yield (dotted_module, lineno) for every import in an AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative import — resolved by the walk-up below anyway
            if not node.module:
                continue
            # `from tools.db import storage` should bind tools.db.storage, not
            # just tools.db — emit the specific form first so the more precise
            # node wins, then the package form as a fallback.
            for alias in node.names:
                yield f"{node.module}.{alias.name}", node.lineno
            yield node.module, node.lineno


def derive_from_python_sources(
    index: NodeIndex,
    base: Path,
    cfg: Dict[str, Any],
    known_tables: Dict[str, Node],
) -> List[Edge]:
    """Parse every file-backed Python node once and emit its mechanical edges.

    Reads each source file a single time and runs three extractors over it:
    AST imports (→ ``imports``), route decorators (→ ``serves_route``, creating
    ``route`` nodes) and SQL table references (→ ``uses_table``, matched against
    tables that have real DDL).
    """
    on = cfg["derivations"]
    limits = cfg["limits"]
    want_imports = on.get("python_import_ast", True)
    want_routes = on.get("flask_route_decorator", True)
    want_tables = on.get("sql_table_reference", True) and bool(known_tables)
    if not (want_imports or want_routes or want_tables):
        return []

    edges: List[Edge] = []
    seen: Set[str] = set()
    truncated: Dict[str, int] = {"imports": 0, "routes": 0, "tables": 0}

    def _emit(edge: Edge) -> None:
        if edge.id in seen:
            return
        seen.add(edge.id)
        edges.append(edge)

    # Snapshot: derived nodes get appended to the index during the loop.
    source_nodes = [
        n
        for n in list(index.nodes)
        if n.entity_type in _PY_SOURCE_TYPES and _norm(n.file_path).endswith(".py")
    ]

    for node in source_nodes:
        rel = _norm(node.file_path)
        abs_path = base / rel
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # manifest row points at a file that is not on disk

        if want_imports:
            try:
                tree = ast.parse(source, filename=rel)
            except (SyntaxError, ValueError):
                tree = None
            if tree is not None:
                count = 0
                for dotted, lineno in _iter_imported_modules(tree):
                    target = index.resolve_module(dotted)
                    if target is None or target.id == node.id:
                        continue
                    if count >= limits["max_imports_per_module"]:
                        truncated["imports"] += 1
                        break
                    edge = _mk_edge(
                        node,
                        target,
                        "imports",
                        "python_import_ast",
                        f"{rel}:{lineno} imports {dotted}",
                        extra={"module": dotted},
                    )
                    if edge.id not in seen:
                        count += 1
                    _emit(edge)

        if want_routes:
            count = 0
            for match in _ROUTE_RE.finditer(source):
                route_path = match.group(1).strip()
                if not route_path.startswith("/"):
                    continue
                if count >= limits["max_routes_per_module"]:
                    truncated["routes"] += 1
                    break
                count += 1
                route_node = index.add(_route_node(route_path, rel))
                _emit(
                    _mk_edge(
                        node,
                        route_node,
                        "serves_route",
                        "flask_route_decorator",
                        f"{rel} declares @route({route_path!r})",
                    )
                )

        if want_tables:
            hits: List[str] = []
            for match in _SQL_REF_RE.finditer(source):
                table = match.group(1)
                if table not in known_tables or table in hits:
                    continue
                hits.append(table)
                if len(hits) >= limits["max_tables_per_module"]:
                    truncated["tables"] += 1
                    break
            for table in hits:
                table_node = known_tables[table]
                if table_node.id == node.id:
                    continue
                _emit(
                    _mk_edge(
                        node,
                        table_node,
                        "uses_table",
                        "sql_table_reference",
                        f"{rel} references table `{table}` in SQL",
                        extra={"table": table},
                    )
                )

    for kind, count in truncated.items():
        if count:
            LOG.info(
                "edge_deriver: %d module(s) hit the per-module %s cap — "
                "additional edges were dropped",
                count,
                kind,
            )
    return edges


# ---------------------------------------------------------------------------
# Deriver 3: DDL — migrations and init scripts that CREATE TABLE
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?([a-zA-Z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

# Identifiers that a loose CREATE TABLE match can produce but that are never
# real table names in this repo.
_TABLE_NAME_DENYLIST = {"table", "exists", "temp", "temporary", "if", "not"}


def _ddl_sources(base: Path) -> List[Tuple[Path, str, Optional[str]]]:
    """Return (abs_path, rel_path, migration_dir_rel) for every DDL-bearing file."""
    out: List[Tuple[Path, str, Optional[str]]] = []

    migrations_dir = base / "tools" / "db" / "migrations"
    if migrations_dir.is_dir():
        for entry in sorted(migrations_dir.iterdir()):
            if entry.name.startswith("__"):
                continue
            if entry.is_dir():
                mig_rel = _norm(str(entry.relative_to(base)))
                for child in sorted(entry.rglob("*")):
                    if child.is_file() and child.suffix.lower() in (".sql", ".py"):
                        out.append((child, _norm(str(child.relative_to(base))), mig_rel))
            elif entry.is_file() and entry.suffix.lower() in (".sql", ".py"):
                rel = _norm(str(entry.relative_to(base)))
                out.append((entry, rel, rel))

    init_db = base / "tools" / "db" / "init_icdev_db.py"
    if init_db.is_file():
        out.append((init_db, _norm(str(init_db.relative_to(base))), None))

    tools_dir = base / "tools"
    if tools_dir.is_dir():
        for init_file in sorted(tools_dir.glob("*/db/init_db.py")):
            out.append((init_file, _norm(str(init_file.relative_to(base))), None))

    return out


def derive_db_tables(
    index: NodeIndex, base: Path, cfg: Dict[str, Any]
) -> Tuple[Dict[str, Node], List[Edge]]:
    """Create ``migration`` + ``db_table`` nodes and the ``creates_table`` edges.

    The owner of a table is whoever holds its DDL: a migration node for
    anything under ``tools/db/migrations/``, otherwise the graph node backing
    the init script (``tools/db/init_icdev_db.py``, a canvas ``db/init_db.py``).
    First DDL wins, so a table is attributed to the migration that introduced
    it rather than to whichever file re-declares it with IF NOT EXISTS.
    """
    known: Dict[str, Node] = {}
    edges: List[Edge] = []
    if not cfg["derivations"].get("ddl_create_table", True):
        return known, edges

    seen_edge_ids: Set[str] = set()

    for abs_path, rel_path, migration_rel in _ddl_sources(base):
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tables = []
        for match in _CREATE_TABLE_RE.finditer(source):
            name = match.group(1)
            if name.lower() in _TABLE_NAME_DENYLIST:
                continue
            if name not in tables:
                tables.append(name)
        if not tables:
            continue

        if migration_rel is not None:
            owner = index.add(_migration_node(migration_rel, Path(migration_rel).name))
        else:
            owner = index.resolve_path(rel_path)
            if owner is None:
                # No graph node backs this init script — still record the
                # tables so `uses_table` can resolve them, just without an
                # ownership edge.
                for table in tables:
                    if table not in known:
                        known[table] = index.add(_db_table_node(table, rel_path))
                continue

        for table in tables:
            if table not in known:
                known[table] = index.add(_db_table_node(table, rel_path))
            table_node = known[table]
            edge = _mk_edge(
                owner,
                table_node,
                "creates_table",
                "ddl_create_table",
                f"{rel_path} declares CREATE TABLE {table}",
                extra={"table": table},
            )
            if edge.id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge.id)
            edges.append(edge)

    return known, edges


# ---------------------------------------------------------------------------
# Deriver 2: documented commands in goals and skills
# ---------------------------------------------------------------------------

_COMMAND_RE = re.compile(
    r"python[0-9.]*\s+(?:-m\s+(?P<mod>tools(?:\.[A-Za-z0-9_]+)+)"
    r"|(?P<path>tools/[A-Za-z0-9_./-]+\.py))"
)

_DOC_SOURCE_TYPES = ("goal", "skill")


def derive_documented_commands(
    index: NodeIndex, base: Path, cfg: Dict[str, Any]
) -> List[Edge]:
    """Goal / skill markdown that documents ``python tools/x.py`` → ``invokes``.

    A documented command is a fact about the document, not a similarity guess:
    if the goal says to run the tool, the goal depends on the tool.
    """
    if not cfg["derivations"].get("documented_command", True):
        return []

    limit = cfg["limits"]["max_commands_per_document"]
    edges: List[Edge] = []
    seen: Set[str] = set()

    for node in list(index.nodes):
        if node.entity_type not in _DOC_SOURCE_TYPES:
            continue
        rel = _norm(node.file_path)
        try:
            text = (base / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count = 0
        for match in _COMMAND_RE.finditer(text):
            dotted = match.group("mod")
            path = match.group("path")
            target = (
                index.resolve_module(dotted)
                if dotted
                else index.resolve_path(path or "")
            )
            if target is None or target.id == node.id:
                continue
            if count >= limit:
                break
            edge = _mk_edge(
                node,
                target,
                "invokes",
                "documented_command",
                f"{rel} documents `{match.group(0)}`",
            )
            if edge.id in seen:
                continue
            seen.add(edge.id)
            count += 1
            edges.append(edge)

    return edges


# ---------------------------------------------------------------------------
# Deriver 6 + 7: args/component_registry.yaml
# ---------------------------------------------------------------------------

_REGISTRY_PATH_REL = "args/component_registry.yaml"


def _load_registry(base: Path) -> List[Dict[str, Any]]:
    if yaml is None:
        return []
    path = base / _REGISTRY_PATH_REL
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except Exception as exc:  # pragma: no cover -- malformed yaml
        LOG.warning("component registry read failed: %s", exc)
        return []
    components = raw.get("components") or []
    return [c for c in components if isinstance(c, dict) and c.get("key")]


def _implementation_nodes(entry: Dict[str, Any], index: NodeIndex) -> List[Tuple[Node, str]]:
    """Graph nodes that implement a registry component, with the field that named them.

    A component declares its code location as a dotted module. Most of those
    modules (``tools.network.blueprint``, ``tools.iqe.adapters.ndc``) are not
    themselves manifest tools, which is why the component gets its own node and
    this only adds an ``implemented_by`` edge when the module really is in the
    graph.
    """
    out: List[Tuple[Node, str]] = []
    seen: Set[str] = set()
    iqe = entry.get("iqe") if isinstance(entry.get("iqe"), dict) else {}
    candidates = [
        (entry.get("module"), "module"),
        ((iqe or {}).get("adapter_module"), "iqe.adapter_module"),
    ]
    for dotted, field in candidates:
        if not isinstance(dotted, str) or not dotted:
            continue
        node = index.resolve_module(dotted)
        if node is not None and node.id not in seen:
            seen.add(node.id)
            out.append((node, f"{field}={dotted}"))
    key = entry.get("key") or ""
    for candidate in (f"tools/{key}_canvas", f"tools/{key}"):
        node = index.canvas_by_dir.get(candidate)
        if node is not None and node.id not in seen:
            seen.add(node.id)
            out.append((node, f"canvas directory {candidate}"))
    return out


def derive_from_registry(
    index: NodeIndex, base: Path, cfg: Dict[str, Any]
) -> List[Edge]:
    """Turn args/component_registry.yaml into graph structure.

    Every registered component becomes a ``component`` node — the registry is
    the declared inventory of the platform, so a component that has no code
    module in the graph should still be visible and still carry its declared
    prerequisites. From there:

      * ``component -> tool/canvas_module``  ``implemented_by``
      * ``component -> iqe_collection``      ``provides_collection``
      * ``component -> component``           ``depends_on``
    """
    on = cfg["derivations"]
    want_iqe = on.get("component_registry_iqe", True)
    want_deps = on.get("component_registry_depends_on", True)
    want_module = on.get("component_registry_module", True)
    if not (want_iqe or want_deps or want_module):
        return []

    entries = _load_registry(base)
    if not entries:
        return []

    edges: List[Edge] = []
    seen: Set[str] = set()
    by_key: Dict[str, Dict[str, Any]] = {e["key"]: e for e in entries}
    node_by_key: Dict[str, Node] = {}

    def _component(entry: Dict[str, Any]) -> Node:
        key = entry["key"]
        if key not in node_by_key:
            node_by_key[key] = index.add(_component_node(entry))
        return node_by_key[key]

    def _emit(edge: Edge) -> None:
        if edge.id in seen:
            return
        seen.add(edge.id)
        edges.append(edge)

    for entry in entries:
        component = _component(entry)

        if want_module:
            for impl, evidence in _implementation_nodes(entry, index):
                if impl.id == component.id:
                    continue
                _emit(
                    _mk_edge(
                        component,
                        impl,
                        "implemented_by",
                        "component_registry_module",
                        f"{_REGISTRY_PATH_REL}: {entry['key']} {evidence}",
                        extra={"component": entry["key"]},
                    )
                )

        if want_iqe:
            iqe = entry.get("iqe") or {}
            collections = iqe.get("collections") or [] if isinstance(iqe, dict) else []
            adapter = (iqe.get("adapter_module") or "") if isinstance(iqe, dict) else ""
            adapter_path = (
                adapter.replace(".", "/") + ".py" if adapter else _REGISTRY_PATH_REL
            )
            for collection in collections:
                if not isinstance(collection, str) or not collection:
                    continue
                coll_node = index.add(
                    _iqe_collection_node(collection, adapter_path, entry["key"])
                )
                _emit(
                    _mk_edge(
                        component,
                        coll_node,
                        "provides_collection",
                        "component_registry_iqe",
                        f"{_REGISTRY_PATH_REL}: {entry['key']}.iqe.collections",
                        extra={"component": entry["key"]},
                    )
                )

    if want_deps:
        for entry in entries:
            depends_on = entry.get("depends_on") or []
            if isinstance(depends_on, str):
                depends_on = [depends_on]
            for dep_key in depends_on:
                dep_entry = by_key.get(dep_key)
                if dep_entry is None:
                    continue
                source = _component(entry)
                target = _component(dep_entry)
                if source.id == target.id:
                    continue
                _emit(
                    _mk_edge(
                        source,
                        target,
                        "depends_on",
                        "component_registry_depends_on",
                        f"{_REGISTRY_PATH_REL}: {entry['key']}.depends_on[{dep_key}]",
                        extra={"component": entry["key"], "depends_on": dep_key},
                    )
                )

    return edges


# ---------------------------------------------------------------------------
# Deriver 8: the legacy keyword heuristic (kept, but honestly labelled)
# ---------------------------------------------------------------------------


def derive_title_keyword_matches(index: NodeIndex, cfg: Dict[str, Any]) -> List[Edge]:
    """canvas_module → goal when the goal title contains the canvas name.

    This is the one derivation that is NOT mechanical: a shared word is a
    guess. It is kept because it is occasionally the only link between a canvas
    and its workflow doc, but it is weighted 0.4 and flagged
    ``mechanical: false`` so consumers can drop it.
    """
    if not cfg["derivations"].get("title_keyword_match", True):
        return []

    edges: List[Edge] = []
    seen: Set[str] = set()
    canvases = [n for n in index.nodes if n.entity_type == "canvas_module"]
    goals = [n for n in index.nodes if n.entity_type == "goal"]
    for canvas in canvases:
        name = canvas.label.lower()
        keyword = name.replace("_", " ")
        for goal in goals:
            title = goal.label.lower()
            if keyword not in title and name not in title:
                continue
            edge = _mk_edge(
                canvas,
                goal,
                "referenced_by_goal",
                "title_keyword_match",
                f"goal title {goal.label!r} contains {canvas.label!r}",
            )
            if edge.id in seen:
                continue
            seen.add(edge.id)
            edges.append(edge)
    return edges


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def derive(
    nodes: List[Node],
    base: Optional[Path] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Node], List[Edge]]:
    """Run every enabled deriver.

    Returns ``(all_nodes, edges)`` where ``all_nodes`` is the input list plus
    the ``migration`` / ``db_table`` / ``route`` / ``iqe_collection`` nodes the
    mechanical edges need as endpoints. Duplicate edges (same source, target
    and relationship) are collapsed, keeping the highest-weight derivation.
    """
    base = base or BASE_DIR
    cfg = cfg or load_edge_config()
    index = NodeIndex(nodes)

    if not cfg.get("enabled", True):
        return index.nodes, []

    collected: List[Edge] = []

    # DDL first — `uses_table` needs the set of tables that really exist.
    known_tables, ddl_edges = derive_db_tables(index, base, cfg)
    collected.extend(ddl_edges)
    collected.extend(derive_from_python_sources(index, base, cfg, known_tables))
    collected.extend(derive_documented_commands(index, base, cfg))
    collected.extend(derive_from_registry(index, base, cfg))
    collected.extend(derive_title_keyword_matches(index, cfg))

    best: Dict[str, Edge] = {}
    for edge in collected:
        current = best.get(edge.id)
        if current is None or edge.weight > current.weight:
            best[edge.id] = edge

    # Drop edges whose endpoints somehow left the index (defensive).
    valid = {n.id for n in index.nodes}
    edges = [
        e for e in best.values() if e.source_id in valid and e.target_id in valid
    ]
    return index.nodes, edges


def derivation_summary(edges: Iterable[Edge]) -> Dict[str, int]:
    """Count edges per derivation method."""
    counts: Dict[str, int] = {}
    for edge in edges:
        key = str(edge.properties.get("derivation", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def run_derive(
    base: Optional[Path] = None, dry_run: bool = False
) -> Dict[str, Any]:
    """Collect nodes, derive edges, and (unless dry-run) persist the whole graph."""
    from tools.awareness import component_indexer as ci

    base = base or BASE_DIR
    nodes = ci.collect_nodes(base)
    all_nodes, edges = derive(nodes, base=base)

    by_type: Dict[str, int] = {}
    for node in all_nodes:
        by_type[node.entity_type] = by_type.get(node.entity_type, 0) + 1

    summary: Dict[str, Any] = {
        "graph_id": GRAPH_ID,
        "nodes": len(all_nodes),
        "indexed_nodes": len(nodes),
        "derived_nodes": len(all_nodes) - len(nodes),
        "edges": len(edges),
        "by_type": by_type,
        "by_derivation": derivation_summary(edges),
        "by_relationship": _relationship_summary(edges),
        "dry_run": dry_run,
    }
    if not dry_run:
        summary["persistence"] = ci.persist(all_nodes, edges)
    return summary


def _relationship_summary(edges: Iterable[Edge]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for edge in edges:
        counts[edge.relationship] = counts.get(edge.relationship, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# Query API — "what breaks if this changes"
# ---------------------------------------------------------------------------


def _load_graph_rows(conn: Any) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Read every node + edge of the self-awareness graph.

    JSON payloads are parsed in Python, never in SQL — `properties` is a plain
    TEXT/JSON column and PostgreSQL is the primary backend, so `json_extract`
    style filtering would be a portability trap.
    """
    node_rows = conn.execute(
        "SELECT id, label, entity_type, properties FROM kg_nodes WHERE graph_id = %s",
        (GRAPH_ID,),
    ).fetchall()
    nodes: Dict[str, Dict[str, Any]] = {}
    for row in node_rows:
        rec = dict(row)
        raw = rec.get("properties")
        try:
            props = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            props = {}
        nodes[rec["id"]] = {
            "id": rec["id"],
            "label": rec.get("label") or rec["id"],
            "entity_type": rec.get("entity_type") or "other",
            "file_path": props.get("file_path", ""),
            "enabled": props.get("enabled", True),
        }

    edge_rows = conn.execute(
        "SELECT id, source_id, target_id, relationship, weight, properties "
        "FROM kg_edges WHERE graph_id = %s",
        (GRAPH_ID,),
    ).fetchall()
    edges: List[Dict[str, Any]] = []
    for row in edge_rows:
        rec = dict(row)
        raw = rec.get("properties")
        try:
            props = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            props = {}
        edges.append(
            {
                "id": rec["id"],
                "source_id": rec["source_id"],
                "target_id": rec["target_id"],
                "relationship": rec.get("relationship") or "related",
                "weight": float(rec.get("weight") or 1.0),
                "derivation": props.get("derivation", "unknown"),
                "mechanical": bool(props.get("mechanical", False)),
                "evidence": props.get("evidence", ""),
            }
        )
    return nodes, edges


def resolve_reference(
    ref: str, nodes: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Resolve a node id, repo-relative file path, or label to one node."""
    if ref in nodes:
        return nodes[ref]
    target = _norm(ref)
    for node in nodes.values():
        if _norm(node["file_path"]) == target:
            return node
    lowered = ref.strip().lower()
    for node in nodes.values():
        if node["label"].lower() == lowered:
            return node
    for node in nodes.values():
        if _norm(node["file_path"]).endswith("/" + target):
            return node
    return None


def _traverse(
    start_id: str,
    edges: List[Dict[str, Any]],
    nodes: Dict[str, Dict[str, Any]],
    depth: int,
    inbound: bool,
    mechanical_only: bool,
) -> List[Dict[str, Any]]:
    """Breadth-first walk of the graph, recording the hop distance."""
    adjacency: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        if mechanical_only and not edge["mechanical"]:
            continue
        key = edge["target_id"] if inbound else edge["source_id"]
        adjacency.setdefault(key, []).append(edge)

    results: List[Dict[str, Any]] = []
    seen: Set[str] = {start_id}
    queue: deque = deque([(start_id, 0)])
    while queue:
        current, hops = queue.popleft()
        if hops >= depth:
            continue
        for edge in adjacency.get(current, []):
            other_id = edge["source_id"] if inbound else edge["target_id"]
            if other_id in seen:
                continue
            other = nodes.get(other_id)
            if other is None:
                continue
            seen.add(other_id)
            results.append(
                {
                    **other,
                    "depth": hops + 1,
                    "relationship": edge["relationship"],
                    "derivation": edge["derivation"],
                    "mechanical": edge["mechanical"],
                    "evidence": edge["evidence"],
                    "weight": edge["weight"],
                    "via": current,
                }
            )
            queue.append((other_id, hops + 1))
    results.sort(key=lambda r: (r["depth"], -r["weight"], r["label"]))
    return results


def _query(
    ref: str,
    depth: int,
    inbound: bool,
    mechanical_only: bool,
    conn: Any = None,
) -> Dict[str, Any]:
    if conn is None:
        if get_connection is None:
            return {"error": "get_connection unavailable", "results": []}
        conn = get_connection()
        owned = True
    else:
        owned = False
    try:
        nodes, edges = _load_graph_rows(conn)
    except Exception as exc:
        LOG.error("graph read failed: %s", exc)
        return {"error": str(exc)[:200], "results": []}
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass

    node = resolve_reference(ref, nodes)
    if node is None:
        return {"error": f"no node matches {ref!r}", "ref": ref, "results": []}

    depth = max(1, int(depth))
    results = _traverse(node["id"], edges, nodes, depth, inbound, mechanical_only)
    direct = [r for r in results if r["depth"] == 1]
    return {
        "graph_id": GRAPH_ID,
        "node": node,
        "direction": "dependents" if inbound else "dependencies",
        "depth": depth,
        "mechanical_only": mechanical_only,
        "direct_count": len(direct),
        "total_count": len(results),
        "direct": direct,
        "results": results,
    }


def get_dependents(
    ref: str,
    depth: int = 1,
    mechanical_only: bool = False,
    conn: Any = None,
) -> Dict[str, Any]:
    """Who depends on this component — the blast radius of changing it.

    ``depth=1`` gives direct dependents; higher depths walk transitively.
    Set ``mechanical_only=True`` to exclude similarity-inferred edges.
    """
    return _query(ref, depth, inbound=True, mechanical_only=mechanical_only, conn=conn)


def get_dependencies(
    ref: str,
    depth: int = 1,
    mechanical_only: bool = False,
    conn: Any = None,
) -> Dict[str, Any]:
    """What this component depends on — the inverse of ``get_dependents``."""
    return _query(ref, depth, inbound=False, mechanical_only=mechanical_only, conn=conn)


def get_edge_stats(conn: Any = None) -> Dict[str, Any]:
    """Persisted edge counts, broken down by derivation and relationship."""
    if conn is None:
        if get_connection is None:
            return {"error": "get_connection unavailable"}
        conn = get_connection()
        owned = True
    else:
        owned = False
    try:
        _, edges = _load_graph_rows(conn)
    except Exception as exc:
        return {"error": str(exc)[:200]}
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass

    by_derivation: Dict[str, int] = {}
    by_relationship: Dict[str, int] = {}
    mechanical = 0
    for edge in edges:
        by_derivation[edge["derivation"]] = by_derivation.get(edge["derivation"], 0) + 1
        by_relationship[edge["relationship"]] = (
            by_relationship.get(edge["relationship"], 0) + 1
        )
        if edge["mechanical"]:
            mechanical += 1
    return {
        "graph_id": GRAPH_ID,
        "edges": len(edges),
        "mechanical": mechanical,
        "inferred": len(edges) - mechanical,
        "by_derivation": dict(sorted(by_derivation.items(), key=lambda kv: -kv[1])),
        "by_relationship": dict(sorted(by_relationship.items(), key=lambda kv: -kv[1])),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_query(result: Dict[str, Any]) -> None:
    if result.get("error"):
        print(f"  error: {result['error']}")
        return
    node = result["node"]
    print(f"  {result['direction']} of {node['label']} ({node['entity_type']})")
    print(f"  file: {node['file_path'] or '(none)'}")
    print(f"  direct: {result['direct_count']}   total(depth<={result['depth']}): "
          f"{result['total_count']}")
    for row in result["results"][:50]:
        flag = "" if row["mechanical"] else " ~guess"
        print(
            f"    [{row['depth']}] {row['relationship']:<20} {row['label']}"
            f"  ({row['entity_type']}, via {row['derivation']}{flag})"
        )
    if result["total_count"] > 50:
        print(f"    ... {result['total_count'] - 50} more")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="edge_deriver",
        description="Derive and query mechanical dependency edges for "
                    "kg-icdev-self-awareness",
    )
    parser.add_argument(
        "--derive", action="store_true",
        help="Collect nodes, derive edges, and persist the graph",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --derive: compute but do not persist",
    )
    parser.add_argument(
        "--dependents", metavar="REF",
        help="Show what depends on REF (node id, file path, or label)",
    )
    parser.add_argument(
        "--dependencies", metavar="REF",
        help="Show what REF depends on",
    )
    parser.add_argument(
        "--depth", type=int, default=1,
        help="Traversal depth for --dependents/--dependencies (default 1)",
    )
    parser.add_argument(
        "--mechanical-only", action="store_true",
        help="Exclude similarity-inferred edges from the traversal",
    )
    parser.add_argument("--stats", action="store_true", help="Persisted edge stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.derive:
        result: Dict[str, Any] = run_derive(dry_run=args.dry_run)
    elif args.dependents:
        result = get_dependents(
            args.dependents, depth=args.depth, mechanical_only=args.mechanical_only
        )
    elif args.dependencies:
        result = get_dependencies(
            args.dependencies, depth=args.depth, mechanical_only=args.mechanical_only
        )
    elif args.stats:
        result = get_edge_stats()
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif args.dependents or args.dependencies:
        _print_query(result)
    else:
        for key, value in result.items():
            print(f"  {key}: {value}")
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
