#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-54 — Deep-module + friction architecture audit.

Applies Ousterhout's deep-module principle (A Philosophy of Software Design):
a DEEP module has a small public interface hiding a large implementation;
SHALLOW modules expose nearly as much interface as implementation and are
candidates for consolidation or deeper abstraction.

This is a LLM-agnostic deterministic AST pass. For each Python module it
computes:

    depth_ratio = public_interface_surface / implementation_lines

A low ratio (small interface / lots of implementation behind it) = deep (good).
A high ratio (most of the file is interface) = shallow (candidate for refactor).

It also builds the import graph and surfaces tightly-coupled clusters
(modules with >= N bidirectional import edges).

Upstream reference:
  https://github.com/mattpocock/skills/tree/main/improve-codebase-architecture

Usage:
    python tools/analysis/architecture_audit.py --path tools/
    python tools/analysis/architecture_audit.py --path tools/ --format markdown --out reports/arch-audit.md
    python tools/analysis/architecture_audit.py --path tools/security --top 10 --json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class ModuleReport:
    path: str                       # relative path from repo root
    total_lines: int                # non-blank non-comment physical lines
    public_symbols: int             # functions / classes not starting with _
    private_symbols: int            # those starting with _
    implementation_lines: int       # lines inside function / class bodies
    depth_ratio: float              # public_symbols / implementation_lines
    classification: str             # "deep" | "balanced" | "shallow"
    why: str                        # short explanation string


@dataclass
class CouplingCluster:
    modules: list[str]
    edges: int
    reason: str = ""


@dataclass
class AuditReport:
    root: str
    module_count: int
    deep: list[ModuleReport] = field(default_factory=list)
    balanced: list[ModuleReport] = field(default_factory=list)
    shallow: list[ModuleReport] = field(default_factory=list)
    clusters: list[CouplingCluster] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Depth analysis
# ---------------------------------------------------------------------------
def _count_non_blank_lines(src: str) -> int:
    return sum(1 for line in src.splitlines()
               if line.strip() and not line.strip().startswith("#"))


def _analyze_module(path: Path) -> ModuleReport | None:
    """AST pass on a single .py file. Returns None on parse failure."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return None

    total = _count_non_blank_lines(src)
    public = 0
    private = 0
    impl_lines = 0

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                private += 1
            else:
                public += 1
            # Body span
            if node.body and hasattr(node.body[-1], "end_lineno"):
                end = node.body[-1].end_lineno or node.lineno
                impl_lines += max(0, end - node.lineno)
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                private += 1
            else:
                public += 1
            # Count class methods + body as implementation
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if inner.body and hasattr(inner.body[-1], "end_lineno"):
                        end = inner.body[-1].end_lineno or inner.lineno
                        impl_lines += max(0, end - inner.lineno)

    # Depth ratio — guard against zero impl
    denom = max(impl_lines, 1)
    ratio = public / denom

    # Classification heuristics (calibrated for Python stdlib style):
    #   deep:     ratio <= 0.15, impl >= 40 lines
    #   shallow:  ratio >= 0.40 OR (impl < 20 AND public >= 2)
    #   balanced: otherwise
    if impl_lines < 20 and public >= 2:
        cls = "shallow"
        why = f"{public} public symbols in only {impl_lines} impl lines"
    elif ratio >= 0.40 and public >= 2:
        cls = "shallow"
        why = f"ratio={ratio:.2f} — interface nearly as large as body"
    elif ratio <= 0.15 and impl_lines >= 40:
        cls = "deep"
        why = f"ratio={ratio:.2f} — {public} public symbol(s) hide {impl_lines} impl lines"
    else:
        cls = "balanced"
        why = f"ratio={ratio:.2f}"

    try:
        rel_path = str(path.relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        rel_path = str(path).replace("\\", "/")
    return ModuleReport(
        path=rel_path,
        total_lines=total,
        public_symbols=public,
        private_symbols=private,
        implementation_lines=impl_lines,
        depth_ratio=round(ratio, 4),
        classification=cls,
        why=why,
    )


# ---------------------------------------------------------------------------
# Coupling analysis
# ---------------------------------------------------------------------------
def _extract_imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def _mod_key(path: Path) -> str:
    """Package key for a module (e.g. 'tools.rag' for tools/rag/foo.py)."""
    try:
        rel = path.relative_to(BASE_DIR)
    except ValueError:
        rel = path
    parts = rel.parts
    if len(parts) <= 1:
        return parts[0].replace(".py", "")
    # package = first two components joined
    return ".".join(parts[:2]).replace(".py", "")


def _find_coupling_clusters(paths: list[Path], min_edges: int = 3) -> list[CouplingCluster]:
    """Build import graph at package level; find tight mutual-import clusters."""
    edges: dict[tuple[str, str], int] = defaultdict(int)

    for p in paths:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        src_key = _mod_key(p)
        for imp in _extract_imports(tree):
            # Only count intra-project edges
            if imp != "tools":
                continue
            # Try to resolve to package-level key
            # (ast.ImportFrom with tools.xyz.zzz -> "tools.xyz")
        # Second pass — need dotted module
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if not mod.startswith("tools."):
                    continue
                parts = mod.split(".")
                tgt_key = ".".join(parts[:2])
                if tgt_key == src_key:
                    continue
                edges[(src_key, tgt_key)] += 1

    # Mutual edges: (a,b) and (b,a) both present
    clusters: list[CouplingCluster] = []
    seen: set[frozenset] = set()
    for (a, b), count in edges.items():
        if (b, a) not in edges:
            continue
        key = frozenset((a, b))
        if key in seen:
            continue
        mutual = count + edges[(b, a)]
        if mutual >= min_edges:
            seen.add(key)
            clusters.append(CouplingCluster(
                modules=[a, b],
                edges=mutual,
                reason=f"{count} imports {a}->{b}, {edges[(b,a)]} imports {b}->{a}",
            ))

    clusters.sort(key=lambda c: -c.edges)
    return clusters


# ---------------------------------------------------------------------------
# Scan + render
# ---------------------------------------------------------------------------
def scan_path(root: Path, min_edges: int = 3) -> AuditReport:
    paths = sorted(p for p in root.rglob("*.py")
                   if "__pycache__" not in p.parts)
    reports: list[ModuleReport] = []
    for p in paths:
        r = _analyze_module(p)
        if r is not None:
            reports.append(r)

    deep = sorted([r for r in reports if r.classification == "deep"],
                  key=lambda r: r.depth_ratio)
    balanced = sorted([r for r in reports if r.classification == "balanced"],
                      key=lambda r: r.depth_ratio)
    shallow = sorted([r for r in reports if r.classification == "shallow"],
                     key=lambda r: -r.depth_ratio)
    clusters = _find_coupling_clusters(paths, min_edges=min_edges)

    summary = {
        "module_count": len(reports),
        "deep_count": len(deep),
        "balanced_count": len(balanced),
        "shallow_count": len(shallow),
        "cluster_count": len(clusters),
        "avg_depth_ratio": round(sum(r.depth_ratio for r in reports) / max(len(reports), 1), 4),
    }
    return AuditReport(
        root=str(root.relative_to(BASE_DIR)).replace("\\", "/") if root.is_relative_to(BASE_DIR) else str(root),
        module_count=len(reports),
        deep=deep, balanced=balanced, shallow=shallow,
        clusters=clusters, summary=summary,
    )


def render_markdown(report: AuditReport, top: int = 20) -> str:
    lines: list[str] = []
    s = report.summary
    lines.append(f"# Architecture Audit — `{report.root}`")
    lines.append("")
    lines.append("> Deep-module analysis (Ousterhout) + import-coupling friction detector.")
    lines.append("> A DEEP module has a small interface hiding a large implementation.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Modules analyzed: **{s['module_count']}**")
    lines.append(f"- Deep: **{s['deep_count']}** · Balanced: **{s['balanced_count']}** · Shallow: **{s['shallow_count']}**")
    lines.append(f"- Average depth ratio: **{s['avg_depth_ratio']}**")
    lines.append(f"- Coupling clusters: **{s['cluster_count']}**")
    lines.append("")

    lines.append("## Shallow-module candidates (refactor targets)")
    lines.append("")
    if not report.shallow:
        lines.append("_No shallow modules detected._")
    else:
        lines.append("| Module | Public | Impl lines | Depth ratio | Why |")
        lines.append("|---|---:|---:|---:|---|")
        for r in report.shallow[:top]:
            lines.append(f"| `{r.path}` | {r.public_symbols} | {r.implementation_lines} "
                         f"| {r.depth_ratio} | {r.why} |")
    lines.append("")

    lines.append("## Deep modules (healthy examples)")
    lines.append("")
    if not report.deep:
        lines.append("_No deep modules detected._")
    else:
        lines.append("| Module | Public | Impl lines | Depth ratio |")
        lines.append("|---|---:|---:|---:|")
        for r in report.deep[:top]:
            lines.append(f"| `{r.path}` | {r.public_symbols} | {r.implementation_lines} "
                         f"| {r.depth_ratio} |")
    lines.append("")

    lines.append("## Coupling clusters (tightly-coupled packages)")
    lines.append("")
    if not report.clusters:
        lines.append("_No tight clusters found at the configured threshold._")
    else:
        lines.append("| Cluster | Edges | Reason |")
        lines.append("|---|---:|---|")
        for c in report.clusters[:top]:
            lines.append(f"| `{' ↔ '.join(c.modules)}` | {c.edges} | {c.reason} |")
    lines.append("")
    return "\n".join(lines)


def render_json(report: AuditReport, top: int = 20) -> str:
    payload = {
        "root": report.root,
        "summary": report.summary,
        "shallow_top": [asdict(r) for r in report.shallow[:top]],
        "deep_top": [asdict(r) for r in report.deep[:top]],
        "clusters_top": [asdict(c) for c in report.clusters[:top]],
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--path", default="tools",
                   help="Root path to audit (default: tools)")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--top", type=int, default=20,
                   help="Show N shallowest / deepest / clusters in output")
    p.add_argument("--min-edges", type=int, default=3,
                   help="Minimum mutual import count for a coupling cluster")
    p.add_argument("--out", metavar="FILE", help="Write output to file instead of stdout")
    p.add_argument("--json", action="store_true", help="Alias for --format json")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.path).resolve()
    if not root.is_absolute():
        root = BASE_DIR / args.path
    if not root.exists():
        print(f"ERROR: path not found: {root}", file=sys.stderr)
        return 1

    report = scan_path(root, min_edges=args.min_edges)
    fmt = "json" if args.json else args.format
    rendered = render_json(report, args.top) if fmt == "json" \
        else render_markdown(report, args.top)

    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8", newline="")
        print(f"wrote {out_path} ({len(rendered)} bytes)")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
