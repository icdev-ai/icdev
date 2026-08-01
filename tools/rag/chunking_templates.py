#!/usr/bin/env python3
# CUI // SP-CTI
"""Document-type chunking templates for RAG ingestion (oss-chunk-01).

Loads ``args/chunking_templates.yaml`` and turns a template definition into
concrete text segments. ``tools/rag/chunker.py`` wraps those segments into
``VectorChunk`` objects; this module owns config + boundary detection only, so
there is no import cycle.

Two rules the rest of the pipeline depends on:

1. **Dispatch is explicit.** A caller passes ``template="oscal_catalog"``, or
   the ``chunking`` key on a ``tools/rag/source_registry.py`` entry names one.
   Nothing here inspects content and picks a template on its own.
2. **Auto-detection is a suggestion.** :func:`suggest_template` scores content
   against each template's ``detect`` patterns and returns a ranked answer for
   an operator to accept or ignore. It is never called during ingestion.

Usage:
    python tools/rag/chunking_templates.py --list --json
    python tools/rag/chunking_templates.py --show oscal_catalog --json
    python tools/rag/chunking_templates.py --suggest path/to/doc.txt --json
    python tools/rag/chunking_templates.py --preview path/to/doc.txt \
        --template stig_checklist --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _resolve_config_path() -> Path:
    """Locate args/chunking_templates.yaml from either package root.

    This module ships in both ``tools/rag/`` and ``icdev/tools/rag/``. The
    canonical config lives at the repo root ``args/``; an installed ``icdev``
    package carries its own ``icdev/args/`` payload. Check the package-local
    path first, then the repo root, so one YAML serves both copies instead of
    a mirror that silently drifts.
    """
    for base in (BASE_DIR, BASE_DIR.parent):
        candidate = base / "args" / "chunking_templates.yaml"
        if candidate.exists():
            return candidate
    return BASE_DIR / "args" / "chunking_templates.yaml"


CONFIG_PATH = _resolve_config_path()

# Name used when a caller passes no template and the config is unreadable.
FALLBACK_TEMPLATE = "general"

# Strategies this module knows how to segment. A template naming anything else
# is treated as sliding_window (the safe, historical behaviour).
STRATEGY_SLIDING = "sliding_window"
STRATEGY_STRUCTURAL = "structural"
STRATEGY_ROW_GROUPS = "row_groups"

# Fallbacks for row_groups templates that omit them.
_DEFAULT_HEADER_LINES = 1
_DEFAULT_ROWS_PER_GROUP = 25

# Built-in config used only when args/chunking_templates.yaml is missing or
# unparseable. Keeps ingestion working (as sliding_window) instead of raising.
_BUILTIN_CONFIG: Dict[str, Any] = {
    "default_template": FALLBACK_TEMPLATE,
    "templates": {
        FALLBACK_TEMPLATE: {
            "description": "Sliding window with overlap (built-in fallback).",
            "strategy": STRATEGY_SLIDING,
        }
    },
}

_config_cache: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(refresh: bool = False) -> Dict[str, Any]:
    """Load and cache args/chunking_templates.yaml.

    Args:
        refresh: Re-read from disk instead of using the cached copy.

    Returns:
        Config dict with at least ``default_template`` and ``templates``.
        Falls back to a built-in single-template config on any read error —
        chunking must never fail because config is absent.
    """
    global _config_cache
    if _config_cache is not None and not refresh:
        return _config_cache

    cfg: Dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            import yaml

            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}

    templates = cfg.get("templates")
    if not isinstance(templates, dict) or not templates:
        cfg = dict(_BUILTIN_CONFIG)
    else:
        cfg.setdefault("default_template", FALLBACK_TEMPLATE)
        if cfg["default_template"] not in templates:
            cfg["default_template"] = FALLBACK_TEMPLATE
        templates.setdefault(
            FALLBACK_TEMPLATE, dict(_BUILTIN_CONFIG["templates"][FALLBACK_TEMPLATE])
        )

    _config_cache = cfg
    return cfg


def default_template_name() -> str:
    """Name of the template used when a caller supplies none."""
    return load_config().get("default_template", FALLBACK_TEMPLATE)


def list_templates() -> List[Dict[str, Any]]:
    """Summarise every registered template (for CLI / operator surfaces)."""
    cfg = load_config()
    out: List[Dict[str, Any]] = []
    for name, tmpl in sorted((cfg.get("templates") or {}).items()):
        tmpl = tmpl or {}
        out.append(
            {
                "name": name,
                "strategy": tmpl.get("strategy", STRATEGY_SLIDING),
                "oversize_policy": tmpl.get("oversize_policy", "split"),
                "max_chunk_tokens": tmpl.get("max_chunk_tokens"),
                "boundary_patterns": len(tmpl.get("boundary_patterns") or []),
                "is_default": name == cfg.get("default_template"),
                "description": " ".join((tmpl.get("description") or "").split()),
            }
        )
    return out


def get_template(name: Optional[str]) -> Tuple[str, Dict[str, Any], str]:
    """Resolve a template name to its definition.

    Args:
        name: Requested template name. ``None``/empty resolves to the default.

    Returns:
        ``(effective_name, template_dict, fallback_reason)``. ``fallback_reason``
        is empty when the requested template was found, otherwise it says why
        the default was substituted — recorded on chunks so a typo in the source
        registry is visible in the audit trail rather than silently ignored.
    """
    cfg = load_config()
    templates: Dict[str, Any] = cfg.get("templates") or {}
    default_name = cfg.get("default_template", FALLBACK_TEMPLATE)

    if not name:
        return default_name, dict(templates.get(default_name) or {}), ""

    tmpl = templates.get(name)
    if tmpl is None:
        return (
            default_name,
            dict(templates.get(default_name) or {}),
            f"unknown_template:{name}",
        )
    return name, dict(tmpl or {}), ""


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """One structural unit of a document (a control, rule, clause, slide...)."""

    text: str
    label: str = ""  # first line, truncated — for audit/preview only


@dataclass
class SegmentResult:
    """Segments plus why they came out that way."""

    segments: List[Segment] = field(default_factory=list)
    strategy: str = STRATEGY_SLIDING
    boundaries_found: int = 0
    # Non-empty when the caller should fall back to the sliding window.
    fallback_reason: str = ""
    # Patterns that failed to compile, so bad config is visible not silent.
    pattern_errors: List[str] = field(default_factory=list)


def _compile_patterns(patterns: List[str], errors: List[str]) -> List[re.Pattern]:
    compiled: List[re.Pattern] = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat, re.MULTILINE))
        except re.error as e:
            errors.append(f"{pat}: {e}")
    return compiled


def _label_for(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first[:80]


def _split_structural(content: str, tmpl: Dict[str, Any]) -> SegmentResult:
    """Start a new segment at every boundary match."""
    result = SegmentResult(strategy=STRATEGY_STRUCTURAL)
    patterns = _compile_patterns(
        list(tmpl.get("boundary_patterns") or []), result.pattern_errors
    )
    if not patterns:
        result.fallback_reason = "no_usable_boundary_patterns"
        return result

    starts = set()
    for pat in patterns:
        for m in pat.finditer(content):
            # A zero-width match cannot anchor a segment; skip it so we never
            # emit an empty slice or loop on the same offset.
            if m.end() > m.start():
                starts.add(m.start())

    result.boundaries_found = len(starts)
    if not starts:
        result.fallback_reason = "no_boundaries_matched"
        return result

    offsets = sorted(starts)
    # Preamble (front matter before the first boundary) is its own segment.
    if offsets[0] > 0:
        offsets.insert(0, 0)
    offsets.append(len(content))

    for i in range(len(offsets) - 1):
        piece = content[offsets[i] : offsets[i + 1]].strip()
        if piece:
            result.segments.append(Segment(text=piece, label=_label_for(piece)))

    if not result.segments:
        result.fallback_reason = "no_content_after_split"
    return result


def _split_row_groups(content: str, tmpl: Dict[str, Any]) -> SegmentResult:
    """Group body rows into fixed-size chunks, repeating the header on each."""
    result = SegmentResult(strategy=STRATEGY_ROW_GROUPS)

    header_lines = max(0, int(tmpl.get("header_lines", _DEFAULT_HEADER_LINES) or 0))
    rows_per_group = max(1, int(tmpl.get("rows_per_group", _DEFAULT_ROWS_PER_GROUP) or 1))

    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines:
        result.fallback_reason = "no_rows"
        return result

    header = lines[:header_lines]
    body = lines[header_lines:]
    if not body:
        # Header-only content: emit it as a single segment rather than nothing.
        text = "\n".join(header).strip()
        if text:
            result.segments.append(Segment(text=text, label=_label_for(text)))
        else:
            result.fallback_reason = "no_rows"
        return result

    result.boundaries_found = len(body)
    for i in range(0, len(body), rows_per_group):
        group = body[i : i + rows_per_group]
        text = "\n".join(header + group).strip()
        if text:
            result.segments.append(
                Segment(text=text, label=f"rows {i + 1}-{i + len(group)}")
            )
    return result


def split_content(content: str, tmpl: Dict[str, Any]) -> SegmentResult:
    """Segment ``content`` per the template's strategy.

    Sliding-window templates return no segments and a ``fallback_reason`` of
    ``sliding_window`` — the caller (chunker) owns that path.
    """
    strategy = (tmpl or {}).get("strategy", STRATEGY_SLIDING)

    if strategy == STRATEGY_STRUCTURAL:
        return _split_structural(content, tmpl)
    if strategy == STRATEGY_ROW_GROUPS:
        return _split_row_groups(content, tmpl)

    return SegmentResult(strategy=STRATEGY_SLIDING, fallback_reason=STRATEGY_SLIDING)


# ---------------------------------------------------------------------------
# Auto-detection — SUGGESTION ONLY, never applied automatically
# ---------------------------------------------------------------------------

# Hits above this per pattern stop adding to the score, so one repeated marker
# in a huge file cannot outvote several distinct markers in a small one.
_MAX_HITS_PER_PATTERN = 5


def suggest_template(content: str, top_n: int = 3) -> Dict[str, Any]:
    """Score content against every template's ``detect`` patterns.

    This is advisory. Callers MUST surface the result to an operator and let
    them pass an explicit ``template=`` — nothing in the ingestion path calls
    this function.

    Args:
        content: Raw document text.
        top_n: How many ranked candidates to return.

    Returns:
        ``{"suggested": name, "confidence": float, "reason": str,
        "candidates": [...], "advisory": True}``. ``suggested`` is the default
        template when nothing scores above its ``min_score``.
    """
    cfg = load_config()
    default_name = cfg.get("default_template", FALLBACK_TEMPLATE)
    scored: List[Dict[str, Any]] = []

    for name, tmpl in (cfg.get("templates") or {}).items():
        detect = (tmpl or {}).get("detect") or {}
        patterns = list(detect.get("patterns") or [])
        if not patterns:
            continue
        min_score = float(detect.get("min_score", 1))

        score = 0.0
        matched: List[str] = []
        for pat in patterns:
            try:
                hits = len(re.findall(pat, content, re.MULTILINE))
            except re.error:
                continue
            if hits:
                score += min(hits, _MAX_HITS_PER_PATTERN)
                matched.append(pat)

        if score >= min_score:
            scored.append(
                {
                    "template": name,
                    "score": round(score, 2),
                    "min_score": min_score,
                    "patterns_matched": len(matched),
                    "patterns_total": len(patterns),
                }
            )

    scored.sort(key=lambda c: (-c["score"], c["template"]))

    if not scored:
        return {
            "suggested": default_name,
            "confidence": 0.0,
            "reason": "no template scored above its min_score",
            "candidates": [],
            "advisory": True,
        }

    best = scored[0]
    runner_up = scored[1]["score"] if len(scored) > 1 else 0.0
    # Confidence blends "did it match a lot" with "did it beat the runner-up".
    margin = (best["score"] - runner_up) / best["score"] if best["score"] else 0.0
    strength = min(1.0, best["score"] / (2 * _MAX_HITS_PER_PATTERN))
    confidence = round(min(1.0, 0.5 * strength + 0.5 * margin), 2)

    return {
        "suggested": best["template"],
        "confidence": confidence,
        "reason": (
            f"{best['patterns_matched']}/{best['patterns_total']} detect patterns "
            f"matched (score {best['score']}, min {best['min_score']})"
        ),
        "candidates": scored[:top_n],
        "advisory": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_preview(path: Path, template: Optional[str]) -> Dict[str, Any]:
    from tools.rag.chunker import chunk_content

    content = path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_content(content, source_type="preview", template=template)
    return {
        "file": str(path),
        "template": chunks[0].metadata.get("chunking_template") if chunks else template,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "index": c.chunk_index,
                "chars": len(c.content),
                "label": c.metadata.get("chunking_label", ""),
                "oversize": c.metadata.get("chunking_oversize", False),
                "preview": c.content[:160].replace("\n", " "),
            }
            for c in chunks
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG chunking templates")
    parser.add_argument("--list", action="store_true", help="List all templates")
    parser.add_argument("--show", metavar="NAME", help="Show one template definition")
    parser.add_argument(
        "--suggest", metavar="FILE", help="Suggest a template for a file (advisory)"
    )
    parser.add_argument(
        "--preview", metavar="FILE", help="Show the chunks a template would produce"
    )
    parser.add_argument("--template", help="Template to use with --preview")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result: Dict[str, Any]

    if args.list:
        result = {"default_template": default_template_name(), "templates": list_templates()}
    elif args.show:
        name, tmpl, fallback = get_template(args.show)
        result = {"requested": args.show, "resolved": name, "template": tmpl}
        if fallback:
            result["fallback_reason"] = fallback
    elif args.suggest:
        path = Path(args.suggest)
        if not path.exists():
            print(json.dumps({"error": f"File not found: {path}"}))
            return 1
        result = suggest_template(path.read_text(encoding="utf-8", errors="replace"))
        result["file"] = str(path)
        result["note"] = (
            "Advisory only. Pass the chosen name explicitly via template= or the "
            "source registry 'chunking' key."
        )
    elif args.preview:
        path = Path(args.preview)
        if not path.exists():
            print(json.dumps({"error": f"File not found: {path}"}))
            return 1
        result = _cmd_preview(path, args.template)
    else:
        parser.print_help()
        return 1

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        for line in json.dumps(result, indent=2, default=str).splitlines():
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
