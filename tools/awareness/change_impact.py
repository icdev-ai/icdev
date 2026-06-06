#!/usr/bin/env python3
# CUI // SP-CTI
"""Change Impact (PR blast radius) — graphify-style PR-impact via the call graph.

Given the files changed in a PR/branch, traverses the module call graph
(tools/awareness/callflow.py) to compute:
  * blast radius — modules that transitively CALL the changed modules (who
    breaks if you change this),
  * downstream — modules the changed code depends on,
  * communities — connected clusters within the impacted subgraph (graphify's
    community grouping, via union-find), and
  * routes affected — impacted modules that carry Flask routes.

Deterministic, stdlib-only, air-gap safe. Computes in-memory (no DB needed).

CLI:
    python tools/awareness/change_impact.py --changed-files "tools/db/storage.py" --json
    python tools/awareness/change_impact.py --changed-files "a.py,b.py" --markdown
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.awareness.callflow import build_call_graph  # noqa: E402


def _to_module_rel(raw: str) -> Optional[str]:
    """Normalize a changed-file path to a repo-relative posix module path."""
    p = Path(raw)
    if not p.is_absolute():
        p = BASE_DIR / p
    try:
        rel = p.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return None
    if rel.startswith("tools/") and rel.endswith(".py"):
        return rel
    return None


def _reachable(seeds: Set[str], adj: Dict[str, List[str]]) -> Set[str]:
    """BFS closure over an adjacency map, excluding the seeds themselves."""
    seen: Set[str] = set()
    queue = deque(seeds)
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen - seeds


def _communities(nodes: Set[str], edges: List[List[str]]) -> List[List[str]]:
    """Connected components (union-find) over the subgraph induced by `nodes`."""
    parent: Dict[str, str] = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for src, dst in edges:
        if src in parent and dst in parent:
            union(src, dst)

    groups: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return sorted([sorted(g) for g in groups.values()], key=lambda g: (-len(g), g[0]))


def _is_route_module(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name == "blueprint.py" or name == "app.py" or "/api/" in rel


def compute_impact(changed_files: List[str], scope: Optional[str] = None) -> Dict[str, Any]:
    """Compute the PR blast radius for a set of changed files."""
    changed = {m for m in (_to_module_rel(f) for f in changed_files) if m}

    graph = build_call_graph(scope)
    module_edges = graph["module_edges"]

    forward: Dict[str, List[str]] = defaultdict(list)   # caller -> callees
    reverse: Dict[str, List[str]] = defaultdict(list)   # callee -> callers
    for src, dst in module_edges:
        forward[src].append(dst)
        reverse[dst].append(src)

    if not changed:
        return {
            "changed": [],
            "blast_radius": [],
            "downstream": [],
            "communities": [],
            "routes_affected": [],
            "summary": "No changed files resolved to tools/*.py modules",
        }

    blast = _reachable(changed, reverse)        # who calls the changed modules (transitively)
    downstream = _reachable(changed, forward)   # what the changed modules call

    impacted = changed | blast
    sub_edges = [e for e in module_edges if e[0] in impacted and e[1] in impacted]
    communities = [c for c in _communities(impacted, sub_edges) if len(c) > 1]

    routes_affected = sorted(m for m in impacted if _is_route_module(m))

    return {
        "changed": sorted(changed),
        "blast_radius": sorted(blast),
        "downstream": sorted(downstream),
        "communities": communities,
        "routes_affected": routes_affected,
        "summary": (
            f"{len(changed)} changed module(s) → {len(blast)} upstream caller(s) impacted; "
            f"{len(downstream)} downstream dependency module(s); "
            f"{len(routes_affected)} route module(s) affected"
        ),
    }


def format_markdown(impact: Dict[str, Any]) -> str:
    lines = ["## PR Change Impact", "", f"_{impact['summary']}_", ""]
    lines.append(f"**Changed ({len(impact['changed'])}):**")
    lines += [f"- {m}" for m in impact["changed"]] or ["- (none)"]
    lines.append("")
    lines.append(f"**Blast radius — upstream callers ({len(impact['blast_radius'])}):**")
    lines += [f"- {m}" for m in impact["blast_radius"]] or ["- (none)"]
    lines.append("")
    if impact["routes_affected"]:
        lines.append(f"**Routes affected ({len(impact['routes_affected'])}):**")
        lines += [f"- {m}" for m in impact["routes_affected"]]
        lines.append("")
    if impact["communities"]:
        lines.append(f"**Impacted communities ({len(impact['communities'])}):**")
        for i, c in enumerate(impact["communities"], 1):
            lines.append(f"- cluster {i} ({len(c)}): {', '.join(c)}")
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="PR Change Impact (blast radius) via call graph")
    parser.add_argument("--changed-files", type=str, required=True, help="Comma-separated changed file paths")
    parser.add_argument("--scope", type=str, default="", help="Limit graph build to a subdir (faster)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--markdown", action="store_true", help="Markdown summary")
    args = parser.parse_args()

    files = [f for f in args.changed_files.split(",") if f.strip()]
    impact = compute_impact(files, scope=args.scope or None)

    if args.markdown:
        print(format_markdown(impact))
    else:
        import json

        print(json.dumps(impact, indent=2))


if __name__ == "__main__":
    main()
