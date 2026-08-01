#!/usr/bin/env python3
# CUI // SP-CTI
"""ANVIL Navigate → Wiki Query (Karpathy LLM Wiki, Item 3).

During the ANVIL Navigate phase, query the memory wiki for tool-related
entries *before* grepping the tools manifest. Surfacing known tool patterns
and past decisions short-circuits manifest searches and avoids rebuilding
institutional knowledge from scratch each session.

CLI usage:
    python tools/memory/wiki_tool_query.py --query "build a DIC ingest pipeline" [--top-k 5] [--json]

Returns a ranked list of relevant wiki entries with their content snippets,
suitable for inclusion in a Navigate phase context block.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "of", "to", "is", "it", "for", "on", "and", "or",
    "with", "that", "this", "be", "as", "by", "we", "i", "you", "are", "was",
    "use", "can", "how", "what", "when", "where", "new", "from", "add",
})

# Prefer tool-related memory files
_TOOL_PREFIXES = ("project-", "feedback-", "feedback_", "rca-", "arc-")


def _extract_terms(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]{4,}", query.lower()) if t not in _STOP_WORDS]


def _score_file(content: str, terms: list[str]) -> float:
    """BM25-inspired score: term frequency normalized by document length."""
    cl = content.lower()
    tf = sum(cl.count(t) for t in terms)
    if tf == 0:
        return 0.0
    # Longer documents dilute score; shorter excerpts are higher precision
    return tf / (len(content) / 500 + 1)


def _extract_snippet(content: str, terms: list[str], max_len: int = 300) -> str:
    """Return the first relevant paragraph from content."""
    # Strip YAML frontmatter
    body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL).strip()
    # Find the line with the most term hits
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    if not paragraphs:
        return body[:max_len]
    best = max(paragraphs, key=lambda p: sum(p.lower().count(t) for t in terms))
    return best[:max_len]


def wiki_tool_query(
    query: str,
    top_k: int = 5,
    memory_dirs: list[str] | None = None,
) -> list[dict]:
    """Search wiki memory dirs for tool-relevant entries.

    Args:
        query:        Natural language task description (Navigate phase input).
        top_k:        Maximum results to return.
        memory_dirs:  Paths to search; defaults to project + Claude Code auto-memory.

    Returns:
        List of dicts with keys: slug, score, snippet, source_dir.
    """
    if memory_dirs is None:
        from tools.memory.claude_memory_path import claude_memory_dir

        auto = claude_memory_dir(BASE_DIR)
        proj = BASE_DIR / "memory"
        memory_dirs = [str(d) for d in [proj, auto] if Path(d).is_dir()]

    terms = _extract_terms(query)
    if not terms:
        return []

    scored: list[tuple[float, str, str, str]] = []  # (score, slug, snippet, dir)
    for dir_str in memory_dirs:
        d = Path(dir_str)
        if not d.is_dir():
            continue
        for fpath in d.glob("*.md"):
            if fpath.name == "MEMORY.md":
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
                score = _score_file(content, terms)
                if score > 0:
                    snippet = _extract_snippet(content, terms)
                    scored.append((score, fpath.stem, snippet, dir_str))
            except Exception:
                continue

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"slug": slug, "score": round(score, 4), "snippet": snippet, "source_dir": src}
        for score, slug, snippet, src in scored[:top_k]
    ]


def _format_context_block(results: list[dict]) -> str:
    """Format results as a Navigate context block."""
    if not results:
        return "No relevant wiki entries found."
    lines = ["[Navigate Wiki Context]"]
    for r in results:
        lines.append(f"\n## {r['slug']} (score: {r['score']})")
        lines.append(r["snippet"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="ANVIL Navigate wiki query")
    parser.add_argument("--query", required=True, help="Navigate phase task description")
    parser.add_argument("--top-k", type=int, default=5, help="Max results")
    parser.add_argument("--memory-dir", action="append", dest="memory_dirs", help="Extra memory dir(s)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    results = wiki_tool_query(args.query, top_k=args.top_k, memory_dirs=args.memory_dirs)

    if args.json:
        print(json.dumps({"results": results, "count": len(results)}, ensure_ascii=False))
    else:
        print(_format_context_block(results))

    return 0


if __name__ == "__main__":
    sys.exit(main())
