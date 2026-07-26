# CUI // SP-CTI
"""Two-pass deterministic content filter for untrusted third-party HTML.

Adapted from Crawl4AI's ``fit_markdown`` idea, reimplemented as pure
deterministic Python with **no new runtime dependencies**: stdlib
``html.parser`` plus the ``rank_bm25`` already pinned in ``requirements.txt``.
No bs4, lxml, html2text, markdownify or trafilatura.

Why this exists
---------------
``tools/chat_router/url_analyzer.py`` truncates fetched pages positionally
(``_MAX_CONTENT = 7000`` characters) regardless of where the answer actually
lives on the page.  A long article's conclusion, a spec's normative section, or
a changelog entry below the fold is simply cut off.  This module replaces
positional truncation with relevance selection:

    Pass 1 — pruning
        Block-level scoring on text density, link density, word count, tag
        semantics and id/class hints.  Nav bars, cookie banners, sidebars,
        share widgets and link farms score low and are dropped whole.

    Pass 2 — BM25
        When the caller supplies a ``query``, the surviving blocks are ranked
        against it and only the relevant ones are kept.  Position on the page
        is irrelevant; relevance is what survives.

Every threshold lives in ``args/page_extract.yaml`` (FORGE args layer), never
in this file.

Output is markdown that preserves headings, lists and tables, with
**reference-style** link citations so raw URLs never inline into the LLM
context window.

Security posture: this module ingests untrusted third-party HTML.  See
``docs/security/sandbox-coverage.md`` (Gap 37) for the recorded decision — the
parser never executes, resolves or fetches anything; ``script``, ``style``,
``iframe``, ``svg`` and form controls are dropped subtrees.

Usage::

    from tools.http.page_extract import extract

    result = extract(html, query="how do I rotate the signing key?")
    result["fit_markdown"]     # relevance-filtered markdown
    result["links"]            # [{"index": 1, "url": ..., "text": ...}, ...]
    result["dropped_blocks"]   # audit trail of what was removed and why
    result["reason"]           # human-readable summary of both passes

CLI::

    python tools/http/page_extract.py --file page.html --query "signing key" --json
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import pathlib
import re
import sys
from html.parser import HTMLParser
from typing import Any, Optional

try:  # pragma: no cover - pyyaml is a core ICDEV dependency
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]

__all__ = ["extract", "load_config", "ARGS_FILENAME"]

ARGS_FILENAME = "page_extract.yaml"

# ── Structural vocabulary (HTML facts, not tunable thresholds) ────────────────
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_LEAF_BLOCK_TAGS = frozenset(
    set(_HEADING_TAGS)
    | {"p", "ul", "ol", "dl", "pre", "blockquote", "table", "figure", "hr"}
)
_INLINE_TAGS = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del",
        "dfn", "em", "i", "img", "ins", "kbd", "mark", "q", "s", "samp",
        "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr",
    }
)
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PLACEHOLDER_RE = re.compile(r"\x00L(\d+):(\d+)\x00")


# ── Config (FORGE args layer) ─────────────────────────────────────────────────
def _find_args_file() -> pathlib.Path:
    """Walk up from this file until an ``args/page_extract.yaml`` is found.

    Works from both the canonical ``icdev/tools/`` package and the root
    ``tools/`` shim, and from git worktrees.
    """
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "args" / ARGS_FILENAME
        if candidate.exists():
            return candidate
    raise RuntimeError(
        f"args/{ARGS_FILENAME} not found above {here} — page_extract keeps all "
        "thresholds in the FORGE args layer and refuses to invent defaults."
    )


@functools.lru_cache(maxsize=4)
def _load_config_cached(path_str: str) -> dict:
    if _yaml is None:  # pragma: no cover - core dependency
        raise RuntimeError("pyyaml is required to read args/" + ARGS_FILENAME)
    with open(path_str, encoding="utf-8") as fh:
        cfg = _yaml.safe_load(fh) or {}
    if not isinstance(cfg, dict):
        raise RuntimeError(f"args/{ARGS_FILENAME} must be a mapping")
    return cfg


def load_config(path: Optional[str] = None) -> dict:
    """Return the parsed ``args/page_extract.yaml`` mapping."""
    return _load_config_cached(str(path) if path else str(_find_args_file()))


# ── Lightweight DOM ───────────────────────────────────────────────────────────
class _El:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: Optional["_El"]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    """Builds a minimal element tree; dropped subtrees never enter it."""

    def __init__(self, drop_tags: frozenset) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _El("[document]", {}, None)
        self.cur = self.root
        self.title_parts: list[str] = []
        self._in_title = False
        self._drop = drop_tags
        self._drop_depth = 0

    # -- helpers
    def _open(self, tag: str, attrs: list) -> _El:
        node = _El(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        return node

    # -- HTMLParser hooks
    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._drop_depth:
            if tag in self._drop:
                self._drop_depth += 1
            return
        if tag in self._drop:
            self._drop_depth = 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _VOID_TAGS:
            self._open(tag, attrs)
            return
        self.cur = self._open(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if self._drop_depth or tag in self._drop or tag == "title":
            return
        self._open(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._drop_depth:
            if tag in self._drop:
                self._drop_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _VOID_TAGS:
            return
        node: Optional[_El] = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is None or node.parent is None:
            return  # stray close tag — ignore, keep the tree well-formed
        self.cur = node.parent

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._drop_depth or not data.strip():
            return
        self.cur.children.append(data)


def _find_tag(node: _El, tag: str) -> Optional[_El]:
    for child in node.children:
        if isinstance(child, _El):
            if child.tag == tag:
                return child
            found = _find_tag(child, tag)
            if found is not None:
                return found
    return None


# ── Metrics ───────────────────────────────────────────────────────────────────
def _measure(node: _El, cache: dict) -> tuple[int, int, int, int]:
    """Return ``(text_len, link_text_len, tag_count, word_count)`` for a subtree."""
    key = id(node)
    hit = cache.get(key)
    if hit is not None:
        return hit
    text_len = link_len = word_count = 0
    tag_count = 1
    for child in node.children:
        if isinstance(child, str):
            collapsed = _WS_RE.sub(" ", child).strip()
            text_len += len(collapsed)
            word_count += len(collapsed.split()) if collapsed else 0
        else:
            c_text, c_link, c_tags, c_words = _measure(child, cache)
            text_len += c_text
            tag_count += c_tags
            word_count += c_words
            link_len += c_text if child.tag == "a" else c_link
    result = (text_len, link_len, tag_count, word_count)
    cache[key] = result
    return result


def _class_signal(node: _El, positives: tuple, negatives: tuple) -> float:
    """1.0 for content-ish id/class names, 0.0 for chrome, 0.5 when neutral."""
    blob = " ".join(
        [str(node.attrs.get("id", "")), str(node.attrs.get("class", "")),
         str(node.attrs.get("role", ""))]
    ).lower()
    if not blob.strip():
        return 0.5
    negative = any(hint in blob for hint in negatives)
    positive = any(hint in blob for hint in positives)
    if positive and not negative:
        return 1.0
    if negative and not positive:
        return 0.0
    return 0.5


def _score_block(
    node: _El, cache: dict, page_text_len: int, prune_cfg: dict
) -> tuple[float, float, dict]:
    """Return ``(composite_score, effective_threshold, metrics)``."""
    text_len, link_len, tag_count, word_count = _measure(node, cache)
    weights = prune_cfg["weights"]
    tag_weights = prune_cfg["tag_weights"]
    tag_weight = float(tag_weights.get(node.tag, tag_weights["default"]))

    text_density = text_len / float(tag_count)
    density_norm = min(1.0, text_density / float(prune_cfg["text_density_saturation"]))
    link_density = (link_len / float(text_len)) if text_len else 1.0
    text_ratio = (text_len / float(page_text_len)) if page_text_len else 0.0
    class_signal = _class_signal(
        node,
        tuple(prune_cfg["positive_class_hints"]),
        tuple(prune_cfg["negative_class_hints"]),
    )

    parts = (
        (float(weights["text_density"]), density_norm),
        (float(weights["link_density"]), 1.0 - link_density),
        (float(weights["tag_weight"]), tag_weight),
        (float(weights["class_weight"]), class_signal),
        (float(weights["text_ratio"]), min(1.0, text_ratio * 2.0)),
    )
    total_weight = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / total_weight if total_weight else 0.0

    threshold = float(prune_cfg["threshold"])
    if str(prune_cfg["threshold_type"]).lower() == "dynamic":
        adj = prune_cfg["dynamic_adjust"]
        # A semantic tag only earns its bonus when the id/class signal is not
        # actively hostile — otherwise <section class="comments"> rides in on
        # <section>'s reputation.
        if tag_weight >= float(adj["high_value_cutoff"]) and class_signal > 0.0:
            threshold += float(adj["high_value_tag_bonus"])
        if class_signal <= 0.0:
            threshold += float(adj.get("negative_class_penalty", 0.0))
        if tag_weight <= float(adj["low_value_cutoff"]):
            threshold += float(adj["low_value_tag_penalty"])
        if word_count >= int(adj["long_text_words"]):
            threshold += float(adj["long_text_bonus"])
        if word_count <= int(adj["short_text_words"]):
            threshold += float(adj["short_text_penalty"])

    metrics = {
        "text_len": text_len,
        "word_count": word_count,
        "tag_count": tag_count,
        "link_density": round(link_density, 4),
        "text_density": round(text_density, 4),
    }
    return round(score, 6), round(threshold, 6), metrics


# ── Markdown rendering ────────────────────────────────────────────────────────
class _Renderer:
    """Renders one block to markdown, buffering link refs as placeholders."""

    def __init__(self, block_index: int, md_cfg: dict) -> None:
        self.block_index = block_index
        self.cfg = md_cfg
        self.links: list[tuple[str, str]] = []

    def _ref(self, url: str, text: str) -> str:
        self.links.append((url, text))
        return f"\x00L{self.block_index}:{len(self.links) - 1}\x00"

    def inline(self, node) -> str:
        if isinstance(node, str):
            return _WS_RE.sub(" ", node)
        tag = node.tag
        if tag == "br":
            return "\n"
        if tag == "img":
            if not self.cfg.get("include_images"):
                return ""
            alt = _WS_RE.sub(" ", str(node.attrs.get("alt", ""))).strip() or "image"
            src = str(node.attrs.get("src", "")).strip()
            return f"![{alt}]{self._ref(src, alt)}" if src else f"![{alt}]"
        inner = "".join(self.inline(child) for child in node.children)
        if tag == "a":
            href = str(node.attrs.get("href", "")).strip()
            label = inner.strip() or href
            if not href or href.startswith(("javascript:", "#")):
                return label
            if not self.cfg.get("reference_links", True):
                return f"[{label}]({href})"
            return f"[{label}]{self._ref(href, label)}"
        if tag in ("strong", "b"):
            return f"**{inner.strip()}**" if inner.strip() else ""
        if tag in ("em", "i"):
            return f"*{inner.strip()}*" if inner.strip() else ""
        if tag in ("code", "kbd", "samp", "var"):
            return f"`{inner.strip()}`" if inner.strip() else ""
        if tag in ("del", "s"):
            return f"~~{inner.strip()}~~" if inner.strip() else ""
        return inner

    def _inline_text(self, node: _El) -> str:
        return _WS_RE.sub(" ", "".join(self.inline(c) for c in node.children)).strip()

    def _list(self, node: _El, depth: int = 0) -> str:
        ordered = node.tag == "ol"
        lines: list[str] = []
        counter = 0
        for child in node.children:
            if not isinstance(child, _El) or child.tag != "li":
                continue
            counter += 1
            nested: list[str] = []
            own: list[str] = []
            for grand in child.children:
                if isinstance(grand, _El) and grand.tag in ("ul", "ol"):
                    nested.append(self._list(grand, depth + 1))
                else:
                    own.append(self.inline(grand))
            text = _WS_RE.sub(" ", "".join(own)).strip()
            marker = f"{counter}." if ordered else "-"
            indent = "  " * depth
            if text:
                lines.append(f"{indent}{marker} {text}")
            for block in nested:
                if block:
                    lines.append(block)
        return "\n".join(lines)

    def _rows(self, node: _El, out: list) -> None:
        for child in node.children:
            if not isinstance(child, _El):
                continue
            if child.tag == "tr":
                out.append(child)
            else:
                self._rows(child, out)

    def _table(self, node: _El) -> str:
        rows: list[_El] = []
        self._rows(node, rows)
        if not rows:
            return ""
        grid: list[list[str]] = []
        for row in rows:
            cells = [
                self._inline_text(c).replace("|", "\\|").replace("\n", " ")
                for c in row.children
                if isinstance(c, _El) and c.tag in ("td", "th")
            ]
            if cells:
                grid.append(cells)
        if not grid:
            return ""
        width = max(len(r) for r in grid)
        grid = [r + [""] * (width - len(r)) for r in grid]
        lines = ["| " + " | ".join(grid[0]) + " |",
                 "|" + "|".join([" --- "] * width) + "|"]
        lines.extend("| " + " | ".join(r) + " |" for r in grid[1:])
        return "\n".join(lines)

    def block(self, node: _El) -> str:
        tag = node.tag
        if tag in _HEADING_TAGS:
            text = self._inline_text(node)
            return f"{'#' * int(tag[1])} {text}" if text else ""
        if tag in ("ul", "ol"):
            return self._list(node)
        if tag == "dl":
            lines = []
            for child in node.children:
                if not isinstance(child, _El):
                    continue
                text = self._inline_text(child)
                if not text:
                    continue
                lines.append(f"**{text}**" if child.tag == "dt" else f": {text}")
            return "\n".join(lines)
        if tag == "pre":
            raw = "".join(_raw_text(c) for c in node.children).strip("\n")
            return f"```\n{raw}\n```" if raw.strip() else ""
        if tag == "blockquote":
            body = "\n".join(
                self.block(c) if isinstance(c, _El) and c.tag in _LEAF_BLOCK_TAGS
                else _WS_RE.sub(" ", self.inline(c)).strip()
                for c in node.children
            ).strip()
            return "\n".join(f"> {line}" for line in body.splitlines() if line.strip())
        if tag == "hr":
            return "---"
        if tag == "table":
            return self._table(node)
        return self._inline_text(node)


def _raw_text(node) -> str:
    """Verbatim text of a subtree (whitespace preserved) — used for <pre>."""
    if isinstance(node, str):
        return node
    if node.tag == "br":
        return "\n"
    return "".join(_raw_text(c) for c in node.children)


def _plain_text(node) -> str:
    if isinstance(node, str):
        return _WS_RE.sub(" ", node)
    return " ".join(
        part for part in (_plain_text(c) for c in node.children) if part
    )


# ── BM25 ──────────────────────────────────────────────────────────────────────
def _stem(token: str, suffixes: tuple, min_stem: int) -> str:
    """Deterministic suffix stripping — the cheapest fix for "reporting" vs "report".

    Rules live in ``args/page_extract.yaml``; this is not a Porter stemmer and
    is not trying to be one.  Longest matching suffix wins, so ordering in the
    args file does not change the result.
    """
    best = token
    for suffix in suffixes:
        if len(token) - len(suffix) >= min_stem and token.endswith(suffix):
            candidate = token[: -len(suffix)]
            if len(candidate) < len(best):
                best = candidate
    return best


def _tokenize(
    text: str,
    stopwords: frozenset,
    min_len: int,
    suffixes: tuple = (),
    min_stem: int = 0,
) -> list[str]:
    tokens = [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) >= min_len and tok not in stopwords
    ]
    if not suffixes:
        return tokens
    return [_stem(tok, suffixes, min_stem) for tok in tokens]


class _FallbackBM25:
    """Okapi BM25 matching ``rank_bm25.BM25Okapi`` semantics.

    Only used when ``rank_bm25`` is not importable, so output stays identical
    across environments instead of silently degrading to no filtering.
    """

    _EPSILON = 0.25

    def __init__(self, corpus: list[list[str]], k1: float, b: float) -> None:
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.doc_len) / self.corpus_size) if self.corpus_size else 0.0
        self.doc_freqs: list[dict] = []
        df: dict = {}
        for doc in corpus:
            freqs: dict = {}
            for token in doc:
                freqs[token] = freqs.get(token, 0) + 1
            self.doc_freqs.append(freqs)
            for token in freqs:
                df[token] = df.get(token, 0) + 1
        self.idf: dict = {}
        negatives: list[str] = []
        idf_sum = 0.0
        for token, freq in df.items():
            idf = math.log(self.corpus_size - freq + 0.5) - math.log(freq + 0.5)
            self.idf[token] = idf
            idf_sum += idf
            if idf < 0:
                negatives.append(token)
        avg_idf = (idf_sum / len(self.idf)) if self.idf else 0.0
        for token in negatives:
            self.idf[token] = self._EPSILON * avg_idf

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        for i, freqs in enumerate(self.doc_freqs):
            norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl) \
                if self.avgdl else self.k1
            total = 0.0
            for token in query:
                tf = freqs.get(token, 0)
                if not tf:
                    continue
                total += self.idf.get(token, 0.0) * (tf * (self.k1 + 1)) / (tf + norm)
            scores[i] = total
        return scores


def _build_bm25(corpus: list[list[str]], k1: float, b: float):
    try:  # rank_bm25 is already pinned in requirements.txt
        from rank_bm25 import BM25Okapi  # type: ignore
    except ImportError:
        return _FallbackBM25(corpus, k1, b)
    return BM25Okapi(corpus, k1=k1, b=b)


def _preview(text: str, limit: Optional[int]) -> str:
    text = _WS_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


# ── Public API ────────────────────────────────────────────────────────────────
def extract(
    html: str,
    *,
    query: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict[str, Any]:
    """Two-pass extraction of readable, query-relevant markdown from raw HTML.

    Args:
        html: raw HTML source (untrusted third-party content).
        query: optional caller question.  When present, pass 2 ranks the
            surviving blocks with BM25 and keeps only the relevant ones.
        config: optional pre-loaded ``args/page_extract.yaml`` mapping
            (tests / callers that want to override thresholds explicitly).

    Returns:
        ``{"title", "raw_text", "fit_markdown", "links", "dropped_blocks",
        "reason"}``.  Deterministic: identical input yields byte-identical
        output.
    """
    cfg = config if config is not None else load_config()
    prune_cfg = cfg["pruning"]
    bm25_cfg = cfg["bm25"]
    md_cfg = cfg.get("markdown", {})
    preview_chars = int(md_cfg.get("dropped_preview_chars", 0)) or None

    builder = _TreeBuilder(frozenset(prune_cfg["drop_tags"]))
    builder.feed(html or "")
    builder.close()

    title = _WS_RE.sub(" ", "".join(builder.title_parts)).strip()
    body = _find_tag(builder.root, "body") or builder.root
    if not title:
        first_h1 = _find_tag(body, "h1")
        if first_h1 is not None:
            title = _WS_RE.sub(" ", _plain_text(first_h1)).strip()

    raw_text = _WS_RE.sub(" ", _plain_text(body)).strip()

    cache: dict = {}
    page_text_len = _measure(body, cache)[0]

    kept, dropped = _run_pass1(body, cache, page_text_len, prune_cfg, preview_chars)

    pruned_count = len(dropped)

    # -- render each surviving block, buffering link refs as placeholders
    rendered: list[dict] = []
    for index, node in enumerate(kept):
        renderer = _Renderer(index, md_cfg)
        text = renderer.block(node).strip()
        if not text:
            continue
        max_chars = int(md_cfg.get("max_block_chars", 0) or 0)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        rendered.append(
            {
                "tag": node.tag,
                "markdown": text,
                "links": renderer.links,
                "is_heading": node.tag in _HEADING_TAGS,
                "level": int(node.tag[1]) if node.tag in _HEADING_TAGS else 99,
            }
        )

    # -- Pass 2: BM25 relevance
    selected = rendered
    bm25_note = "pass 2 skipped (no query)"
    if query and query.strip() and rendered:
        selected, bm25_dropped, bm25_note = _run_pass2(
            rendered, query, bm25_cfg, preview_chars
        )
        dropped.extend(bm25_dropped)

    # -- assign reference-link indices in document order
    links: list[dict] = []
    url_to_index: dict[str, int] = {}
    parts: list[str] = []
    for block in selected:
        def _sub(match: "re.Match[str]", block=block) -> str:
            url, label = block["links"][int(match.group(2))]
            existing = url_to_index.get(url)
            if existing is None:
                existing = len(links) + 1
                url_to_index[url] = existing
                links.append({"index": existing, "url": url, "text": label})
            return f"[{existing}]"

        parts.append(_PLACEHOLDER_RE.sub(_sub, block["markdown"]))

    fit_markdown = "\n\n".join(parts).strip()
    if links and md_cfg.get("reference_links", True):
        heading = str(md_cfg.get("reference_heading", "## References"))
        refs = "\n".join(f"[{item['index']}]: {item['url']}" for item in links)
        fit_markdown = f"{fit_markdown}\n\n{heading}\n\n{refs}".strip()

    reason = (
        f"pass 1 pruned {pruned_count} block(s) "
        f"(threshold={prune_cfg['threshold']}, type={prune_cfg['threshold_type']}, "
        f"min_words={prune_cfg['min_word_threshold']}); {bm25_note}; "
        f"kept {len(selected)}/{len(rendered)} rendered block(s)"
    )

    return {
        "title": title,
        "raw_text": raw_text,
        "fit_markdown": fit_markdown,
        "links": links,
        "dropped_blocks": dropped,
        "reason": reason,
    }


def _run_pass1(
    body: _El,
    cache: dict,
    page_text_len: int,
    prune_cfg: dict,
    preview_chars: Optional[int],
) -> tuple[list[_El], list[dict]]:
    """Pass 1 — structural pruning.  Returns ``(kept_blocks, dropped_records)``."""
    kept: list[_El] = []
    dropped: list[dict] = []
    min_words = int(prune_cfg["min_word_threshold"])
    exempt = frozenset(prune_cfg.get("min_word_exempt_tags") or ())

    def record(node: _El, score: float, threshold: float, metrics: dict, why: str) -> None:
        dropped.append(
            {
                "stage": "prune",
                "tag": node.tag,
                "score": score,
                "threshold": threshold,
                "words": metrics["word_count"],
                "link_density": metrics["link_density"],
                "reason": why,
                "preview": _preview(_plain_text(node), preview_chars),
            }
        )

    def flush(pending: list, parent: _El) -> None:
        if not pending:
            return
        synthetic = _El("p", {}, parent)
        synthetic.children = list(pending)
        pending.clear()
        text = _WS_RE.sub(" ", _plain_text(synthetic)).strip()
        if not text:
            return
        if min_words and len(text.split()) < min_words:
            dropped.append(
                {
                    "stage": "prune",
                    "tag": "p",
                    "score": 0.0,
                    "threshold": float(prune_cfg["threshold"]),
                    "words": len(text.split()),
                    "link_density": 0.0,
                    "reason": "below min_word_threshold",
                    "preview": _preview(text, preview_chars),
                }
            )
            return
        kept.append(synthetic)

    def walk(node: _El) -> None:
        pending: list = []
        for child in node.children:
            if isinstance(child, str) or (
                isinstance(child, _El) and child.tag in _INLINE_TAGS
            ):
                pending.append(child)
                continue
            flush(pending, node)
            if not isinstance(child, _El):
                continue
            if child.tag in _HEADING_TAGS:
                kept.append(child)  # headings are structure — never pruned
                continue
            score, threshold, metrics = _score_block(
                child, cache, page_text_len, prune_cfg
            )
            if min_words and metrics["word_count"] < min_words and child.tag not in exempt:
                record(child, score, threshold, metrics, "below min_word_threshold")
                continue
            if score < threshold:
                record(child, score, threshold, metrics, "below prune threshold")
                continue
            if child.tag in _LEAF_BLOCK_TAGS:
                kept.append(child)
            else:
                walk(child)
        flush(pending, node)

    walk(body)
    return kept, dropped


def _run_pass2(
    rendered: list[dict],
    query: str,
    bm25_cfg: dict,
    preview_chars: Optional[int],
) -> tuple[list[dict], list[dict], str]:
    """Pass 2 — BM25 relevance ranking.  Replaces positional truncation."""
    stopwords = frozenset(bm25_cfg.get("stopwords") or ())
    min_token_len = int(bm25_cfg.get("min_token_length", 1))
    suffixes = tuple(bm25_cfg.get("stem_suffixes") or ())
    min_stem = int(bm25_cfg.get("stem_min_stem_length", 0))
    query_tokens = _tokenize(query, stopwords, min_token_len, suffixes, min_stem)
    if not query_tokens:
        return rendered, [], "pass 2 skipped (query has no scorable tokens)"

    corpus = [
        _tokenize(b["markdown"], stopwords, min_token_len, suffixes, min_stem)
        for b in rendered
    ]
    bm25 = _build_bm25(corpus, float(bm25_cfg["k1"]), float(bm25_cfg["b"]))
    scores = [float(s) for s in bm25.get_scores(query_tokens)]

    threshold = float(bm25_cfg["threshold"])
    min_blocks = int(bm25_cfg.get("min_blocks", 0))
    max_blocks = int(bm25_cfg.get("max_blocks", 0) or 0)

    keep = {i for i, s in enumerate(scores) if s >= threshold}
    # Rank ties broken by document order so output stays byte-identical.
    ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    if max_blocks and len(keep) > max_blocks:
        keep = set(sorted(keep, key=lambda i: (-scores[i], i))[:max_blocks])

    # A heading that matches the query pulls in its section body. Sections
    # answer questions; a matched heading with its paragraph stripped away does
    # not. Only threshold-qualified headings propagate — backfilled ones do not.
    if bm25_cfg.get("section_propagation", True):
        max_section = int(bm25_cfg.get("max_section_blocks", 0) or 0)
        for i in sorted(keep):
            if not rendered[i]["is_heading"]:
                continue
            level = rendered[i]["level"]
            taken = 0
            for j in range(i + 1, len(rendered)):
                if rendered[j]["is_heading"] and rendered[j]["level"] <= level:
                    break
                if max_section and taken >= max_section:
                    break
                keep.add(j)
                taken += 1

    for i in ranked:
        if len(keep) >= min_blocks:
            break
        keep.add(i)

    if bm25_cfg.get("keep_headings", True):
        for i in sorted(keep):
            j = i - 1
            while j >= 0 and rendered[j]["is_heading"]:
                keep.add(j)
                j -= 1

    selected = [rendered[i] for i in sorted(keep)]
    dropped = [
        {
            "stage": "bm25",
            "tag": rendered[i]["tag"],
            "score": round(scores[i], 6),
            "threshold": threshold,
            "words": len(corpus[i]),
            "link_density": 0.0,
            "reason": "below bm25 threshold for query",
            "preview": _preview(rendered[i]["markdown"], preview_chars),
        }
        for i in range(len(rendered))
        if i not in keep
    ]
    note = (
        f"pass 2 bm25 kept {len(selected)}/{len(rendered)} block(s) "
        f"(threshold={threshold}, query_tokens={len(query_tokens)})"
    )
    return selected, dropped, note


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Two-pass (prune + BM25) HTML → fit_markdown content filter."
    )
    parser.add_argument("--file", help="path to an HTML file (default: stdin)")
    parser.add_argument("--query", default=None, help="query for the BM25 pass")
    parser.add_argument("--json", action="store_true", help="emit the full payload")
    args = parser.parse_args(argv)

    if args.file:
        html = pathlib.Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        html = sys.stdin.read()

    result = extract(html, query=args.query)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(result["fit_markdown"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
