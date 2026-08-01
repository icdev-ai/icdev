# CUI // SP-CTI
"""Minimal, dependency-free allowlist HTML sanitizer for LLM/markdown output.

``bleach``/``nh3`` are not available in this (offline, no-npm) environment, so
this provides a small, auditable allowlist filter for the one job we need:
neutralising HTML produced by ``markdown.markdown()`` from untrusted text
(scan-derived module paths, LLM-drafted PRD prose) before it is injected into
the DOM via ``innerHTML``.

Policy (deny-by-default):
  - Only tags in ``ALLOWED_TAGS`` survive; any other tag's markup is dropped.
  - ``<script>`` / ``<style>`` are dropped *together with their text content*.
  - Attributes are dropped unless explicitly allowlisted per-tag; ``href`` is
    additionally restricted to safe URL schemes (no ``javascript:``).
  - All text nodes are HTML-escaped, so a dropped ``<b onclick=...>`` cannot
    smuggle markup back in.

This is intentionally conservative: it is a security boundary, not a
pretty-printer. When in doubt, it strips.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

# Tags safe to render from markdown output. No form/media/script/iframe/object.
ALLOWED_TAGS = frozenset({
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark", "small", "sup", "sub",
    "code", "pre", "kbd", "samp",
    "blockquote", "span", "div",
    "a",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "col", "colgroup",
})

# Void (self-closing) elements — never emit a separate close tag.
VOID_TAGS = frozenset({"br", "hr", "col"})

# Tags whose *contents* must also be discarded (not just the tag markup).
DROP_CONTENT_TAGS = frozenset({"script", "style"})

# Per-tag attribute allowlist. Everything else (style, on*, id, data-*, class)
# is dropped.
ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
    "td": frozenset({"colspan", "rowspan"}),
    "col": frozenset({"span"}),
    "colgroup": frozenset({"span"}),
}

# href must resolve to a benign scheme. Relative/anchor/mailto/http(s) only.
_SAFE_URL_RE = re.compile(r"^\s*(?:https?:|mailto:|tel:|#|/|\./|\.\./)", re.IGNORECASE)


class _AllowlistSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._suppress_depth = 0  # >0 while inside a drop-content element

    # ── tags ────────────────────────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth:
            return
        if tag not in ALLOWED_TAGS:
            return  # drop markup, keep children/text
        attr_str = self._render_attrs(tag, attrs)
        if tag in VOID_TAGS:
            self._out.append(f"<{tag}{attr_str}/>")
        else:
            self._out.append(f"<{tag}{attr_str}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS or self._suppress_depth:
            return
        if tag not in ALLOWED_TAGS:
            return
        attr_str = self._render_attrs(tag, attrs)
        self._out.append(f"<{tag}{attr_str}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            if self._suppress_depth:
                self._suppress_depth -= 1
            return
        if self._suppress_depth:
            return
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self._out.append(f"</{tag}>")

    # ── text ────────────────────────────────────────────────────────────────
    def handle_data(self, data):
        if self._suppress_depth:
            return
        self._out.append(escape(data))

    def handle_entityref(self, name):
        if self._suppress_depth:
            return
        self._out.append(f"&{name};")

    def handle_charref(self, name):
        if self._suppress_depth:
            return
        self._out.append(f"&#{name};")

    # ── attributes ────────────────────────────────────────────────────────────
    @staticmethod
    def _render_attrs(tag, attrs) -> str:
        allowed = ALLOWED_ATTRS.get(tag, frozenset())
        rendered: list[str] = []
        for raw_key, raw_val in attrs:
            key = (raw_key or "").lower()
            if key not in allowed:
                continue
            val = raw_val or ""
            if key == "href" and not _SAFE_URL_RE.match(val):
                continue  # drop javascript:, data:, vbscript:, etc.
            rendered.append(f' {key}="{escape(val, quote=True)}"')
        return "".join(rendered)

    def result(self) -> str:
        return "".join(self._out)


def sanitize_html(html: str) -> str:
    """Return ``html`` with only allowlisted tags/attributes; text escaped.

    Deny-by-default: unknown tags are stripped, ``<script>``/``<style>`` content
    is discarded, unsafe attributes and URL schemes are removed. Safe to inject
    into the DOM via ``innerHTML``.
    """
    if not html:
        return ""
    parser = _AllowlistSanitizer()
    parser.feed(html)
    parser.close()
    return parser.result()
