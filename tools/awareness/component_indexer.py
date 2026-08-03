#!/usr/bin/env python3
# CUI // SP-CTI
"""Component Indexer — Phase 1d.

Deterministic filesystem walker that enumerates ICDEV components
(skills, MCP servers, canvas modules, goals, tools, reflexes) and
writes typed nodes + relationship edges into the `kg_nodes` and
`kg_edges` tables under a dedicated graph `kg-icdev-self-awareness`.

Design:
  * Pure stdlib + optional PyYAML. No LLM, no network. Air-gap safe.
  * Per-node enablement resolved via tools/awareness/enablement.py.
    Disabled nodes are still indexed (so the /components-map UI can
    render them dimmed) but tagged `enabled: false`.
  * Upserts are idempotent — re-running --scan updates existing
    nodes in place via ON CONFLICT DO UPDATE.
  * Stable node IDs derived from `sha256("<entity_type>::<rel_path>")`
    so edges can reference nodes deterministically across runs.
  * Single-file upsert path (`upsert_file`) exposed for the Phase 1e
    post-tool hook to refresh a single file's node after Edit/Write.

CLI:
    python tools/awareness/component_indexer.py --scan --json
    python tools/awareness/component_indexer.py --scan --scope tools/network --json
    python tools/awareness/component_indexer.py --dry-run --json
    python tools/awareness/component_indexer.py --stats --json

See docs/features/internal-awareness-engine.md for the broader plan.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import ast
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = get_logger("component_indexer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.awareness.enablement import (  # noqa: E402
    is_component_enabled,
    load_enablement_flags,
    load_enablement_map,
)

try:
    from tools.db.storage import get_connection  # noqa: E402
except ImportError:
    get_connection = None  # type: ignore[assignment]

GRAPH_ID = "kg-icdev-self-awareness"
GRAPH_NAME = "ICDEV Self-Awareness"
GRAPH_DESCRIPTION = (
    "Internal component graph: skills, MCP servers, canvas modules, "
    "goals, tools, reflexes, dashboard routes, and DB tables. "
    "Populated by tools/awareness/component_indexer.py (Phase 1d)."
)

# Max characters of a description we store on a node — keeps payload
# small but enough for hover tooltips to show meaningful text.
_MAX_DESCRIPTION = 500


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """A single component node destined for kg_nodes."""

    id: str
    label: str
    entity_type: str
    description: str = ""
    file_path: str = ""
    enabled: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_properties_json(self) -> str:
        """Serialize `properties` column payload."""
        payload: Dict[str, Any] = {
            "description": self.description,
            "file_path": self.file_path,
            "enabled": self.enabled,
        }
        payload.update(self.extra)
        return json.dumps(payload, ensure_ascii=False)


@dataclass
class Edge:
    """A typed relationship destined for kg_edges."""

    id: str
    source_id: str
    target_id: str
    relationship: str
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_properties_json(self) -> str:
        return json.dumps(self.properties, ensure_ascii=False)


def _node_id(entity_type: str, stable_key: str) -> str:
    """Deterministic node ID — reruns produce the same ID for the
    same component, so upserts target the same row."""
    h = hashlib.sha256(f"{entity_type}::{stable_key}".encode("utf-8")).hexdigest()
    return f"icdev-{entity_type}-{h[:16]}"


def _edge_id(source_id: str, target_id: str, relationship: str) -> str:
    h = hashlib.sha256(
        f"{source_id}::{relationship}::{target_id}".encode("utf-8")
    ).hexdigest()
    return f"e-{h[:20]}"


def _rel_path(fp: Path, base: Path) -> str:
    try:
        return str(fp.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(fp).replace("\\", "/")


def _truncate(text: str, limit: int = _MAX_DESCRIPTION) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _apply_enablement(node: Node, flags: Dict[str, bool], mapping: List[dict]) -> None:
    """Set node.enabled based on the enablement rules."""
    node.enabled = is_component_enabled(
        node.id,
        {"entity_type": node.entity_type, "file_path": node.file_path},
        flags=flags,
        mapping=mapping,
    )


# ---------------------------------------------------------------------------
# Parsers — one per entity type
# ---------------------------------------------------------------------------


# --- Skills ---------------------------------------------------------------


_SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_yaml_frontmatter_simple(text: str) -> Dict[str, str]:
    """Very small YAML-ish parser for skill/goal frontmatter.
    Handles only flat key: value pairs (no lists, no nesting). Good
    enough for 95% of frontmatter in this repo. Multi-line or complex
    values get truncated to the first line.
    """
    result: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            result[key] = value
    return result


def _parse_skill(skill_md: Path, base: Path) -> Optional[Node]:
    """Parse .agents/skills/<name>/SKILL.md — frontmatter + body intro."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    fm = _parse_yaml_frontmatter_simple("")
    body = text
    m = _SKILL_FRONTMATTER_RE.match(text)
    if m:
        fm = _parse_yaml_frontmatter_simple(m.group(1))
        body = m.group(2)

    rel = _rel_path(skill_md, base)
    skill_dir = skill_md.parent.name
    label = fm.get("name") or skill_dir
    desc = fm.get("description", "")
    if len(desc) < 40 and body:
        # Augment with body intro for hover quality
        body_summary = _truncate(body, 300)
        desc = (desc + " — " + body_summary) if desc else body_summary

    node = Node(
        id=_node_id("skill", rel),
        label=label,
        entity_type="skill",
        description=_truncate(desc),
        file_path=rel,
        extra={
            "skill_dir": skill_dir,
            "model": fm.get("model", ""),
        },
    )
    return node


# --- MCP servers ----------------------------------------------------------


def _parse_mcp_server(server_py: Path, base: Path) -> Optional[Node]:
    """Parse tools/mcp/<name>_server.py — class docstring + tool count."""
    try:
        source = server_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    docstring = ""
    class_name = ""
    try:
        tree = ast.parse(source, filename=str(server_py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                docstring = ast.get_docstring(node) or ""
                break
        if not docstring:
            docstring = ast.get_docstring(tree) or ""
    except SyntaxError:
        pass

    # Rough tool count: count @mcp_tool decorators or register_tool calls
    tool_count = len(re.findall(r"@mcp_tool\b|register_tool\(", source))

    rel = _rel_path(server_py, base)
    server_name = server_py.stem.replace("_server", "")
    label = class_name or server_name or server_py.name

    node = Node(
        id=_node_id("mcp_server", rel),
        label=label,
        entity_type="mcp_server",
        description=_truncate(docstring or f"MCP server ({server_name})"),
        file_path=rel,
        extra={
            "class_name": class_name,
            "tool_count": tool_count,
            "server_short": server_name,
        },
    )
    return node


# --- Canvas modules -------------------------------------------------------


# The set of top-level directories under tools/ that represent canvases.
_CANVAS_DIRS = (
    "boundary_canvas",
    "data_canvas",
    "infra_canvas",
    "migration_canvas",
    "network_canvas",
    "observability_canvas",
    "pipeline_canvas",
    "qdc_canvas",
    "security_canvas",
    "canvas",
)


def _parse_canvas_module(canvas_dir: Path, base: Path) -> Optional[Node]:
    """Parse tools/<x>_canvas/ — uses agent.py / constants.py / blueprint.py
    docstrings as the node description."""
    if not canvas_dir.is_dir():
        return None

    rel = _rel_path(canvas_dir, base)
    description_parts: List[str] = []
    file_count = 0
    blueprint_routes: List[str] = []
    db_tables: List[str] = []

    for py in canvas_dir.glob("**/*.py"):
        file_count += 1
        if py.name == "agent.py" or py.name == "constants.py":
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
                mod_doc = ast.get_docstring(tree)
                if mod_doc:
                    description_parts.append(mod_doc.split("\n")[0])
            except (OSError, SyntaxError):
                pass
        if py.name == "blueprint.py":
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'@bp\.route\([\'"]([^\'"]+)[\'"]', text):
                    blueprint_routes.append(m.group(1))
            except OSError:
                pass
        # CREATE TABLE scans per canvas's db/init_db.py if present
        if py.name == "init_db.py":
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(
                    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-zA-Z_][\w]*)",
                    text,
                    re.IGNORECASE,
                ):
                    db_tables.append(m.group(1))
            except OSError:
                pass

    label = canvas_dir.name
    desc = (
        description_parts[0]
        if description_parts
        else f"ICDEV canvas module ({label})"
    )

    node = Node(
        id=_node_id("canvas_module", rel),
        label=label,
        entity_type="canvas_module",
        description=_truncate(desc),
        file_path=rel,
        extra={
            "file_count": file_count,
            "blueprint_routes": sorted(set(blueprint_routes))[:20],
            "db_tables": sorted(set(db_tables))[:30],
        },
    )
    return node


# --- Goals ----------------------------------------------------------------


def _parse_goal(goal_md: Path, base: Path) -> Optional[Node]:
    """Parse goals/*.md — frontmatter title + body intro."""
    try:
        text = goal_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    fm: Dict[str, str] = {}
    body = text
    m = _SKILL_FRONTMATTER_RE.match(text)
    if m:
        fm = _parse_yaml_frontmatter_simple(m.group(1))
        body = m.group(2)

    rel = _rel_path(goal_md, base)

    # Title: first H1 or frontmatter title or filename
    title = fm.get("title", "")
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
    if not title:
        title = goal_md.stem.replace("_", " ").title()

    # Description: first non-heading paragraph of the body
    desc = ""
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        desc = line
        break
    if not desc:
        desc = _truncate(body, 200)

    node = Node(
        id=_node_id("goal", rel),
        label=title,
        entity_type="goal",
        description=_truncate(desc),
        file_path=rel,
        extra={"phase": fm.get("phase", "")},
    )
    return node


# --- Tools (from tools/manifest.md) --------------------------------------


# Manifest rows look like:
#   | Tool Name | tools/path/to/tool.py | Description | Input | Output |
_MANIFEST_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*(tools/[^|]+?)\s*\|\s*([^|]+?)\s*\|"
)


def _parse_tools_manifest(base: Path) -> List[Node]:
    """Parse tools/manifest.md (and shard files in tools/manifest/) for tool rows."""
    manifest_path = base / "tools" / "manifest.md"
    if not manifest_path.exists():
        return []

    # Collect all files to parse: the index + any shard files
    manifest_files: List[Path] = [manifest_path]
    shard_dir = base / "tools" / "manifest"
    if shard_dir.is_dir():
        manifest_files.extend(sorted(shard_dir.glob("*.md")))

    seen: set = set()
    nodes: List[Node] = []
    for mf in manifest_files:
        try:
            lines = mf.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        current_section = "General"
        for raw in lines:
            if raw.startswith("## "):
                current_section = raw[3:].strip()
                continue
            m = _MANIFEST_ROW_RE.match(raw)
            if not m:
                continue
            name = m.group(1).strip()
            file_rel = m.group(2).strip()
            description = m.group(3).strip()
            # Skip header/separator rows
            if name.lower() in ("tool", "tool name", "----", "---") or "---" in name:
                continue
            if file_rel in seen:
                continue
            seen.add(file_rel)
            node = Node(
                id=_node_id("tool", file_rel),
                label=name,
                entity_type="tool",
                description=_truncate(description),
                file_path=file_rel,
                extra={"category": current_section},
            )
            nodes.append(node)
    return nodes


# --- Reflexes -------------------------------------------------------------


def _parse_reflex(reflex_py: Path, base: Path) -> Optional[Node]:
    """Parse tools/genesis/reflexes/*.py — module docstring + run() signature."""
    if reflex_py.name == "__init__.py":
        return None
    try:
        source = reflex_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    docstring = ""
    has_run = False
    try:
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree) or ""
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "run":
                has_run = True
                break
    except SyntaxError:
        pass

    rel = _rel_path(reflex_py, base)
    label = reflex_py.stem

    node = Node(
        id=_node_id("reflex", rel),
        label=label,
        entity_type="reflex",
        description=_truncate(docstring or f"Genesis reflex ({label})"),
        file_path=rel,
        extra={"has_run": has_run},
    )
    return node


# ---------------------------------------------------------------------------
# Scan orchestration
# ---------------------------------------------------------------------------


def _in_scope(fp: Path, scope: Optional[Path]) -> bool:
    if scope is None:
        return True
    try:
        fp.resolve().relative_to(scope.resolve())
        return True
    except ValueError:
        return False


def collect_nodes(
    base: Path,
    scope: Optional[Path] = None,
) -> List[Node]:
    """Walk the filesystem and produce every typed component node.

    Caller is responsible for persistence — this function is pure
    (no DB writes) so it can be used by both the full --scan path
    and the single-file upsert path.
    """
    nodes: List[Node] = []
    flags = load_enablement_flags()
    mapping = load_enablement_map()

    # Skills
    skills_root = base / ".agents" / "skills"
    if skills_root.exists() and _in_scope(skills_root, scope):
        for skill_md in sorted(skills_root.glob("*/SKILL.md")):
            if scope and not _in_scope(skill_md, scope):
                continue
            n = _parse_skill(skill_md, base)
            if n:
                _apply_enablement(n, flags, mapping)
                nodes.append(n)

    # MCP servers
    mcp_root = base / "tools" / "mcp"
    if mcp_root.exists():
        for server_py in sorted(mcp_root.glob("*_server.py")):
            if scope and not _in_scope(server_py, scope):
                continue
            n = _parse_mcp_server(server_py, base)
            if n:
                _apply_enablement(n, flags, mapping)
                nodes.append(n)

    # Canvas modules
    tools_root = base / "tools"
    for canvas_name in _CANVAS_DIRS:
        canvas_dir = tools_root / canvas_name
        if canvas_dir.exists() and canvas_dir.is_dir():
            if scope and not _in_scope(canvas_dir, scope):
                continue
            n = _parse_canvas_module(canvas_dir, base)
            if n:
                _apply_enablement(n, flags, mapping)
                nodes.append(n)

    # Goals
    goals_root = base / "goals"
    if goals_root.exists() and _in_scope(goals_root, scope):
        for goal_md in sorted(goals_root.glob("*.md")):
            if goal_md.name == "manifest.md":
                continue
            if scope and not _in_scope(goal_md, scope):
                continue
            n = _parse_goal(goal_md, base)
            if n:
                _apply_enablement(n, flags, mapping)
                nodes.append(n)

    # Tools (from manifest)
    if scope is None or _in_scope(base / "tools" / "manifest.md", scope):
        for n in _parse_tools_manifest(base):
            _apply_enablement(n, flags, mapping)
            nodes.append(n)

    # Reflexes
    reflex_root = base / "tools" / "genesis" / "reflexes"
    if reflex_root.exists() and _in_scope(reflex_root, scope):
        for py in sorted(reflex_root.glob("*.py")):
            if scope and not _in_scope(py, scope):
                continue
            n = _parse_reflex(py, base)
            if n:
                _apply_enablement(n, flags, mapping)
                nodes.append(n)

    return nodes


def derive_graph(
    nodes: List[Node], base: Optional[Path] = None
) -> tuple[List[Node], List[Edge]]:
    """Derive the relationship layer for a node list.

    Delegates to ``tools/awareness/edge_deriver.py``, which reads the evidence
    already on disk — AST imports, ``CREATE TABLE`` DDL, ``@bp.route``
    decorators, documented ``python tools/...`` commands and the component
    registry — and returns both the edges and the endpoint nodes those edges
    need (``migration`` / ``db_table`` / ``route`` / ``iqe_collection``).

    Every edge records ``properties.derivation`` and ``properties.mechanical``
    so a consumer can tell a parsed import from an inferred keyword match.

    Imported lazily: edge_deriver imports this module for its Node/Edge types,
    so a module-level import here would be circular.
    """
    try:
        from tools.awareness.edge_deriver import derive as _derive
    except ImportError as exc:  # pragma: no cover -- slim installs
        LOG.warning("edge_deriver unavailable, graph will have no edges: %s", exc)
        return nodes, []
    try:
        return _derive(nodes, base=base or BASE_DIR)
    except Exception as exc:  # pragma: no cover -- never wedge a scan on edges
        LOG.error("edge derivation failed: %s", exc)
        return nodes, []


def derive_edges(nodes: List[Node], base: Optional[Path] = None) -> List[Edge]:
    """Backward-compatible wrapper returning only the edges.

    Callers that also need the derived endpoint nodes (routes, tables,
    migrations, IQE collections) should use ``derive_graph``; edges pointing at
    nodes this function discards will be filtered out at render time.
    """
    _, edges = derive_graph(nodes, base=base)
    return edges


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _upsert_graph_row(conn: Any, node_count: int, edge_count: int) -> None:
    """Ensure kg_graphs has a row for our graph and update counts."""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT id FROM kg_graphs WHERE id = %s", (GRAPH_ID,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO kg_graphs (id, project_id, name, description, "
            "entity_count, edge_count, metadata, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                GRAPH_ID,
                None,
                GRAPH_NAME,
                GRAPH_DESCRIPTION,
                node_count,
                edge_count,
                "{}",
                now,
                now,
            ),
        )
    else:
        conn.execute(
            "UPDATE kg_graphs SET entity_count = %s, edge_count = %s, "
            "updated_at = %s WHERE id = %s",
            (node_count, edge_count, now, GRAPH_ID),
        )


def _upsert_node(conn: Any, node: Node) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO kg_nodes (id, graph_id, label, entity_type, properties, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(id) DO UPDATE SET label = excluded.label, "
        "entity_type = excluded.entity_type, properties = excluded.properties",
        (
            node.id,
            GRAPH_ID,
            node.label,
            node.entity_type,
            node.to_properties_json(),
            now,
        ),
    )


def _upsert_edge(conn: Any, edge: Edge) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO kg_edges (id, graph_id, source_id, target_id, "
        "relationship, weight, properties, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(id) DO UPDATE SET weight = excluded.weight, "
        "properties = excluded.properties",
        (
            edge.id,
            GRAPH_ID,
            edge.source_id,
            edge.target_id,
            edge.relationship,
            edge.weight,
            edge.to_properties_json(),
            now,
        ),
    )


# Rows per transaction. Phase 1a committed per row so one bad row could not
# wedge the rest; that cost one round-trip per row, which was invisible at
# 2,432 nodes / 0 edges and is not once the graph carries five figures of
# edges. Chunking keeps the resilience (a failed chunk is replayed row by row
# so only the genuinely bad row is lost) at ~1/200th the round-trips.
_UPSERT_CHUNK = 200


def _upsert_chunked(conn: Any, items: List[Any], writer: Any, kind: str) -> int:
    """Upsert `items` in chunks; on chunk failure replay it row by row.

    Returns the number of rows that could not be written.
    """
    errors = 0
    for start in range(0, len(items), _UPSERT_CHUNK):
        chunk = items[start:start + _UPSERT_CHUNK]
        try:
            for item in chunk:
                writer(conn, item)
            conn.commit()
            continue
        except Exception as exc:
            LOG.warning(
                "%s chunk %d-%d failed (%s) — retrying row by row",
                kind, start, start + len(chunk), exc,
            )
            try:
                conn.rollback()
            except Exception:
                pass
        for item in chunk:
            try:
                writer(conn, item)
                conn.commit()
            except Exception as exc:
                LOG.error("%s upsert failed (%s): %s", kind, item.id, exc)
                errors += 1
                try:
                    conn.rollback()
                except Exception:
                    pass
    return errors


def persist(nodes: List[Node], edges: List[Edge]) -> Dict[str, Any]:
    """Upsert nodes and edges to kg_nodes/kg_edges.

    Writes in transactions of ``_UPSERT_CHUNK`` rows; a chunk that raises is
    rolled back and replayed one row at a time, so a single bad row is dropped
    rather than taking the whole scan down with it.
    """
    if get_connection is None:
        return {"persisted": False, "error": "get_connection unavailable"}

    conn = get_connection()
    errors = 0

    # Ensure graph row exists FIRST so nodes can be persisted under it
    try:
        _upsert_graph_row(conn, len(nodes), len(edges))
        conn.commit()
    except Exception as exc:
        LOG.error("Failed to upsert kg_graphs row: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass

    errors += _upsert_chunked(conn, nodes, _upsert_node, "Node")
    errors += _upsert_chunked(conn, edges, _upsert_edge, "Edge")

    try:
        _upsert_graph_row(conn, len(nodes), len(edges))
        conn.commit()
    except Exception:
        pass

    try:
        conn.close()
    except Exception:
        pass

    return {"persisted": True, "errors": errors}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prune_stale_nodes(
    base: Optional[Path] = None, dry_run: bool = False
) -> Dict[str, Any]:
    """Reconcile the graph against disk — remove nodes whose backing file is gone.

    The indexer is otherwise additive: ``persist`` only upserts, so when a tool
    is deleted its kg_node lingers forever and every health probe keeps reporting
    it as a ``module_import`` failure. This sweep deletes file-backed nodes whose
    ``file_path`` no longer exists on disk, but ONLY when the node has zero edges
    (so we never orphan a relationship). Each pruned node also has its
    ``awareness_component_health`` snapshots removed so the latest-snapshot-per-node
    view used by self_monitor stops surfacing the dead component.

    Returns {"candidates": n, "pruned": n, "dry_run": bool, "nodes": [...]}.
    """
    base = base or BASE_DIR
    if get_connection is None:
        return {"pruned": 0, "candidates": 0, "error": "get_connection unavailable"}

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, entity_type, properties FROM kg_nodes WHERE graph_id = %s",
            (GRAPH_ID,),
        ).fetchall()
    except Exception as exc:
        LOG.error("prune: kg_nodes read failed: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return {"pruned": 0, "candidates": 0, "error": str(exc)[:200]}

    candidates: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            props = json.loads(d.get("properties") or "{}")
        except Exception:
            props = {}
        fp = props.get("file_path", "")
        if not fp:
            continue  # only file-backed nodes are reconciled against disk
        # Symbol-qualified nodes carry "file.py::symbol" — existence is a
        # property of the FILE, not the qualifier. Strip it before checking,
        # otherwise every function/class node looks like a missing file.
        file_part = fp.split("::", 1)[0]
        if (base / file_part).exists():
            continue  # backing file/dir still present — keep
        # Backing file is gone. Only prune if nothing references this node.
        try:
            ec = conn.execute(
                "SELECT COUNT(*) AS n FROM kg_edges WHERE source_id = %s OR target_id = %s",
                (d["id"], d["id"]),
            ).fetchone()
            if (dict(ec).get("n", 0) if ec else 0) > 0:
                continue  # has edges — skip to avoid orphaning relationships
        except Exception:
            continue  # if we can't verify edges, be conservative and skip
        candidates.append({"id": d["id"], "entity_type": d["entity_type"], "file_path": fp})

    pruned = 0
    if not dry_run:
        for c in candidates:
            try:
                # Snapshots first (best-effort; table may be absent in slim envs)
                try:
                    conn.execute(
                        "DELETE FROM awareness_component_health WHERE node_id = %s",
                        (c["id"],),
                    )
                except Exception:
                    pass
                conn.execute(
                    "DELETE FROM kg_nodes WHERE id = %s AND graph_id = %s",
                    (c["id"], GRAPH_ID),
                )
                conn.commit()
                pruned += 1
            except Exception as exc:
                LOG.error("prune: delete failed for %s: %s", c["id"], exc)
                try:
                    conn.rollback()
                except Exception:
                    pass

    try:
        conn.close()
    except Exception:
        pass

    return {
        "candidates": len(candidates),
        "pruned": pruned,
        "dry_run": dry_run,
        "nodes": candidates[:50],
    }


def scan(
    base: Optional[Path] = None,
    scope: Optional[Path] = None,
    dry_run: bool = False,
    prune: bool = True,
) -> Dict[str, Any]:
    """Full scan entry point — returns summary dict.

    On a full (unscoped) non-dry-run scan, also reconciles the graph against
    disk via ``prune_stale_nodes`` so deleted tools self-clean each cycle.
    Scoped scans never prune (they only see part of the tree).
    """
    base = base or BASE_DIR
    indexed = collect_nodes(base, scope=scope)
    # derive_graph returns `indexed` plus the endpoint nodes the mechanical
    # edges need (route / db_table / migration / iqe_collection); persist both
    # or every edge pointing at one of them would dangle.
    nodes, edges = derive_graph(indexed, base=base)

    summary: Dict[str, Any] = {
        "graph_id": GRAPH_ID,
        "nodes": len(nodes),
        "indexed_nodes": len(indexed),
        "derived_nodes": len(nodes) - len(indexed),
        "edges": len(edges),
        "enabled": sum(1 for n in nodes if n.enabled),
        "disabled": sum(1 for n in nodes if not n.enabled),
        "by_type": {},
    }
    for n in nodes:
        summary["by_type"][n.entity_type] = summary["by_type"].get(n.entity_type, 0) + 1

    if not dry_run:
        summary["persistence"] = persist(nodes, edges)
        # Reconcile only on a full scan — a scoped scan can't tell a deleted
        # node from one simply outside its scope.
        if prune and scope is None:
            summary["prune"] = prune_stale_nodes(base)

    return summary


def upsert_file(path: Path, base: Optional[Path] = None) -> Dict[str, Any]:
    """Refresh a single file's node. Used by the Phase 1e post-tool
    hook so that Edit/Write events update kg_nodes synchronously.

    Returns a summary dict with `node_id` if a matching parser
    handled the file, or `{"skipped": true}` if no parser matched.
    """
    base = base or BASE_DIR
    fp = Path(path).resolve()
    if not fp.exists():
        return {"skipped": True, "reason": "file does not exist"}

    flags = load_enablement_flags()
    mapping = load_enablement_map()

    node: Optional[Node] = None

    # Dispatch to the right parser based on path conventions.
    rel = _rel_path(fp, base)
    if rel.startswith(".agents/skills/") and fp.name == "SKILL.md":
        node = _parse_skill(fp, base)
    elif rel.startswith("tools/mcp/") and fp.name.endswith("_server.py"):
        node = _parse_mcp_server(fp, base)
    elif any(rel.startswith(f"tools/{c}/") for c in _CANVAS_DIRS):
        # Canvas-module files → refresh the parent canvas node
        for c in _CANVAS_DIRS:
            if rel.startswith(f"tools/{c}/") or rel == f"tools/{c}":
                node = _parse_canvas_module(base / "tools" / c, base)
                break
    elif rel.startswith("goals/") and fp.suffix == ".md" and fp.name != "manifest.md":
        node = _parse_goal(fp, base)
    elif rel.startswith("tools/genesis/reflexes/") and fp.suffix == ".py":
        node = _parse_reflex(fp, base)

    if node is None:
        return {"skipped": True, "reason": "no parser for path"}

    _apply_enablement(node, flags, mapping)

    if get_connection is None:
        return {"node_id": node.id, "persisted": False}

    # Single-file fast path: skip the kg_graphs existence check
    # (the graph is created by the full scan; upsert_file should
    # never be the first time kg_nodes gets a row written). One
    # SQL UPSERT + one commit, no extra round-trips.
    conn = get_connection()
    try:
        _upsert_node(conn, node)
        conn.commit()
    except Exception as exc:
        LOG.error("Single-file upsert failed for %s: %s", rel, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"node_id": node.id, "persisted": False, "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "node_id": node.id,
        "persisted": True,
        "entity_type": node.entity_type,
        "enabled": node.enabled,
    }


def get_stats() -> Dict[str, Any]:
    """Return current persisted graph stats."""
    if get_connection is None:
        return {"error": "get_connection unavailable"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kg_nodes WHERE graph_id = %s",
            (GRAPH_ID,),
        ).fetchone()
        node_count = dict(row).get("cnt", 0) if row else 0
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kg_edges WHERE graph_id = %s",
            (GRAPH_ID,),
        ).fetchone()
        edge_count = dict(row).get("cnt", 0) if row else 0
        rows = conn.execute(
            "SELECT entity_type, COUNT(*) AS cnt FROM kg_nodes "
            "WHERE graph_id = %s GROUP BY entity_type ORDER BY cnt DESC",
            (GRAPH_ID,),
        ).fetchall()
        by_type = {dict(r)["entity_type"]: dict(r)["cnt"] for r in rows}
        return {
            "graph_id": GRAPH_ID,
            "nodes": node_count,
            "edges": edge_count,
            "by_type": by_type,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="component_indexer",
        description="Phase 1d — ICDEV component indexer for kg-icdev-self-awareness",
    )
    parser.add_argument("--scan", action="store_true", help="Run a full scan")
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help="Limit scan to a subdirectory (relative to project root)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Collect nodes but do not persist"
    )
    parser.add_argument(
        "--stats", action="store_true", help="Show current graph stats"
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="Reconcile graph against disk: remove file-missing, zero-edge nodes "
             "(+ their health snapshots). Combine with --dry-run to preview.",
    )
    parser.add_argument(
        "--no-prune", action="store_true",
        help="Skip the post-scan reconcile sweep on --scan",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.stats:
        result = get_stats()
    elif args.prune and not args.scan:
        result = prune_stale_nodes(dry_run=args.dry_run)
    elif args.scan or args.dry_run:
        scope = (BASE_DIR / args.scope) if args.scope else None
        result = scan(scope=scope, dry_run=args.dry_run, prune=not args.no_prune)
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for k, v in result.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
