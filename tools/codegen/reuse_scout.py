#!/usr/bin/env python3
# CUI // SP-CTI
"""Reuse Scout — pre-generation reuse + minimal-scope brief.

Before code is generated, this tool tells the generator which existing symbols
to REUSE and which planned symbols actually need to be built. It queries three
existing, deterministic sources (no new infrastructure):

  1. The ICDEV self-awareness knowledge graph (`kg_nodes`, graph
     `kg-icdev-self-awareness`) — module-level component nodes.
  2. The `tools/manifest/` shards — the "grep before you write" registry.
  3. `tools/testing/api_surface_extractor.py` — exact reusable signatures from
     the top candidate modules.

100% deterministic, stdlib-only, air-gap safe: if the KG/DB is unavailable it
degrades to manifest grep + filesystem candidates and never hard-fails.

Adapts graphify's "query the graph to find existing entities before writing new
code" onto ICDEV's existing component graph.

CLI:
    python tools/codegen/reuse_scout.py --intent "open a database connection" --json
    python tools/codegen/reuse_scout.py --intent "parse manifest" --symbols parse_shard,load_index --markdown
    python tools/codegen/reuse_scout.py --spec specs/my_plan.md --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

GRAPH_ID = "kg-icdev-self-awareness"
MANIFEST_DIR = BASE_DIR / "tools" / "manifest"

# Reusable-candidate entity types in the self-awareness graph (module-level).
_REUSABLE_TYPES = {"tool", "reflex", "canvas_module", "goal", "mcp_server"}

# Tokens too generic to carry reuse signal.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with", "by",
    "from", "into", "new", "add", "get", "set", "run", "use", "using", "that",
    "this", "code", "tool", "tools", "module", "function", "create", "build",
    "data", "file", "files", "icdev", "py", "python", "def", "class",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> Set[str]:
    """Lowercase alphanumeric tokens, splitting snake/camel, minus stopwords."""
    if not text:
        return set()
    # Split camelCase into words before lowercasing.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    tokens = {t for t in _TOKEN_RE.findall(spaced.lower()) if len(t) > 2}
    return {t for t in tokens if t not in _STOPWORDS}


def _overlap_score(query: Set[str], candidate: Set[str]) -> float:
    """Token overlap weighted toward shared-token count (deterministic)."""
    if not query or not candidate:
        return 0.0
    shared = query & candidate
    if not shared:
        return 0.0
    union = query | candidate
    return round(len(shared) + len(shared) / len(union), 4)


def _load_kg_nodes() -> List[Dict[str, Any]]:
    """Load self-awareness component nodes. Returns [] if the KG is unavailable."""
    try:
        from tools.db.storage import get_connection
    except Exception:
        return []
    nodes: List[Dict[str, Any]] = []
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT label, entity_type, properties FROM kg_nodes WHERE graph_id = ?",
                (GRAPH_ID,),
            )
            rows = cur.fetchall()
    except Exception:
        return []
    for row in rows:
        d = dict(row)
        try:
            props = json.loads(d.get("properties") or "{}")
        except (TypeError, ValueError):
            props = {}
        nodes.append(
            {
                "label": d.get("label") or "",
                "entity_type": d.get("entity_type") or "",
                "file_path": props.get("file_path") or "",
                "description": props.get("description") or "",
            }
        )
    return nodes


def _signature(func: Dict[str, Any]) -> str:
    """Render a compact `name(p1, p2) -> ret` signature from an extractor entry."""
    params = [p.get("name", "") for p in func.get("parameters", []) if p.get("name")]
    sig = f"{func['name']}({', '.join(params)})"
    ret = func.get("return_type")
    if ret:
        sig += f" -> {ret}"
    return sig


def _module_symbols(file_path: str) -> List[Dict[str, Any]]:
    """Public functions + classes of a module via api_surface_extractor."""
    try:
        from tools.testing.api_surface_extractor import extract_api_surface
    except Exception:
        return []
    surface = extract_api_surface(file_path)
    if "error" in surface:
        return []
    out: List[Dict[str, Any]] = []
    for fn in surface.get("functions", []):
        out.append(
            {
                "name": fn["name"],
                "signature": _signature(fn),
                "doc": fn.get("docstring") or "",
                "kind": "function",
            }
        )
    for cls in surface.get("classes", []):
        out.append(
            {
                "name": cls["name"],
                "signature": f"class {cls['name']}",
                "doc": cls.get("docstring") or "",
                "kind": "class",
            }
        )
    return out


def _search_manifest(query: Set[str], limit: int) -> List[str]:
    """Grep manifest shards for lines matching the intent tokens."""
    if not MANIFEST_DIR.exists():
        return []
    scored: List[Tuple[float, str]] = []
    for shard in sorted(MANIFEST_DIR.glob("*.md")):
        try:
            lines = shard.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines, 1):
            score = _overlap_score(query, _tokenize(line))
            if score > 0:
                scored.append((score, f"{shard.relative_to(BASE_DIR)}:{i}: {line.strip()[:160]}"))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [s for _, s in scored[:limit]]


def scout(
    intent: str,
    symbols: Optional[List[str]] = None,
    limit: int = 8,
    max_modules: int = 5,
) -> Dict[str, Any]:
    """Produce a reuse/scope brief for a generation task.

    Returns a dict with `reuse`, `manifest_hits`, `generate_only`, and `summary`.
    """
    symbols = [s.strip() for s in (symbols or []) if s.strip()]
    query = _tokenize(intent) | {t for s in symbols for t in _tokenize(s)}

    # Rank existing components by token overlap.
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for node in _load_kg_nodes():
        if node["entity_type"] not in _REUSABLE_TYPES:
            continue
        node_tokens = _tokenize(node["label"]) | _tokenize(node["description"])
        score = _overlap_score(query, node_tokens)
        if score > 0:
            candidates.append((score, node))
    candidates.sort(key=lambda t: (-t[0], t[1]["file_path"]))

    reuse: List[Dict[str, Any]] = []
    existing_symbol_names: Set[str] = set()
    for score, node in candidates[:max_modules]:
        fp = node["file_path"]
        syms = _module_symbols(str(BASE_DIR / fp)) if fp.endswith(".py") else []
        for s in syms:
            existing_symbol_names.add(s["name"].lower())
        # Keep the symbols whose name/doc best match the intent (max 6 per module).
        ranked = sorted(
            syms,
            key=lambda s: -_overlap_score(query, _tokenize(s["name"]) | _tokenize(s["doc"])),
        )
        reuse.append(
            {
                "file_path": fp,
                "label": node["label"],
                "entity_type": node["entity_type"],
                "score": score,
                "why": f"matches: {', '.join(sorted(query & (_tokenize(node['label']) | _tokenize(node['description']))))}",
                "symbols": ranked[:6],
            }
        )

    # Planned symbols that already exist (reuse) vs genuinely new (generate).
    generate_only: List[str] = []
    already_exists: List[str] = []
    for sym in symbols:
        if sym.lower() in existing_symbol_names:
            already_exists.append(sym)
        else:
            generate_only.append(sym)

    summary = (
        f"{len(reuse)} reusable module(s); "
        f"{len(already_exists)} planned symbol(s) already exist; "
        f"{len(generate_only)} to build"
    )
    return {
        "intent": intent,
        "planned_symbols": symbols,
        "reuse": reuse,
        "already_exists": already_exists,
        "generate_only": generate_only,
        "manifest_hits": _search_manifest(query, limit),
        "summary": summary,
    }


def format_markdown(brief: Dict[str, Any]) -> str:
    """Render the brief into the slots expected by hardprompts/minimal_generation.md."""
    lines: List[str] = ["## Reuse Scout Brief", "", f"_Intent:_ {brief['intent']}", ""]

    lines.append("REUSE THESE — do not reimplement:")
    if brief["reuse"]:
        for mod in brief["reuse"]:
            lines.append(f"- {mod['file_path']}  ({mod['label']})")
            for s in mod["symbols"]:
                doc = f" — {s['doc']}" if s["doc"] else ""
                lines.append(f"    - {s['signature']}{doc}")
    else:
        lines.append("- (no close matches found in the component graph)")
    lines.append("")

    if brief["already_exists"]:
        lines.append("ALREADY EXISTS (planned symbol found — reuse it):")
        for s in brief["already_exists"]:
            lines.append(f"- {s}")
        lines.append("")

    lines.append("GENERATE ONLY these (no existing match found):")
    if brief["generate_only"]:
        for s in brief["generate_only"]:
            lines.append(f"- {s}")
    else:
        lines.append("- (none specified — pass --symbols to scope generation)")
    lines.append("")

    if brief["manifest_hits"]:
        lines.append("Manifest references (grep before you write):")
        for h in brief["manifest_hits"]:
            lines.append(f"- {h}")
        lines.append("")

    lines.append(f"_Summary:_ {brief['summary']}")
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # robust on Windows cp1252 consoles
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Reuse Scout — pre-generation reuse + scope brief")
    parser.add_argument("--intent", type=str, default="", help="Free-text description of what you intend to build")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated planned function/class names")
    parser.add_argument("--spec", type=str, default="", help="Path to a spec/plan file to use as the intent text")
    parser.add_argument("--limit", type=int, default=8, help="Max manifest hits to return")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--markdown", action="store_true", help="Markdown brief (for prompt injection)")
    args = parser.parse_args()

    intent = args.intent
    if args.spec:
        spec_path = Path(args.spec)
        if not spec_path.is_absolute():
            spec_path = BASE_DIR / spec_path
        if spec_path.exists():
            intent = (intent + "\n" + spec_path.read_text(encoding="utf-8", errors="replace")).strip()

    if not intent and not args.symbols:
        parser.error("provide --intent, --symbols, or --spec")

    symbols = [s for s in args.symbols.split(",") if s.strip()]
    brief = scout(intent, symbols=symbols, limit=args.limit)

    if args.markdown:
        print(format_markdown(brief))
    else:
        print(json.dumps(brief, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
