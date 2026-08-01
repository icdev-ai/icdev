# CUI // SP-CTI
"""Neutralise attacker-controlled text from remote MCP servers.

A remote MCP server supplies its own tool names and descriptions, and those
descriptions are injected into an agent's prompt so it knows what each tool
does. That is untrusted text entering the reasoning loop of an agent holding
real credentials — and it lands there whether or not the tool is ever called,
so a hostile server gets a prompt-injection attempt for free just by being
listed.

This module does not try to detect malice. Detection is a losing game against
an adversary who can rewrite their description after reading the filter. It
does two narrower things that hold regardless of phrasing:

* strips the structural devices injection relies on — fake role turns, fenced
  blocks, comment/tag syntax that could close the surrounding prompt context;
* frames whatever survives as an untrusted quotation with an explicit label, so
  the model sees third-party marketing copy rather than an instruction from its
  operator.

The real defences are structural and live elsewhere: namespacing (a remote tool
can never be confused for an ICDEV one), the per-server allowlist (an
unlisted tool is never surfaced at all), and the classification ceiling (what
may be sent out). This is defence in depth, not the primary control.
"""
from __future__ import annotations

import re

#: Hard cap. A tool description is a sentence or two; anything longer is either
#: padding or a payload, and truncation costs nothing real.
MAX_DESCRIPTION_CHARS = 400
MAX_NAME_CHARS = 64

#: Structural devices used to break out of a surrounding prompt context. These
#: are removed, not flagged: there is no legitimate reason for a tool
#: description to contain a chat-role turn or an HTML comment.
_STRUCTURAL = (
    # Fake conversation turns.
    re.compile(r"^\s*(system|assistant|user|human|developer)\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"<\|.*?\|>", re.DOTALL),
    re.compile(r"\[/?INST\]", re.IGNORECASE),
    re.compile(r"</?(system|assistant|user|human)>", re.IGNORECASE),
    # Comment and markup syntax that could close or open a block.
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"```"),
    re.compile(r"^\s*---\s*$", re.MULTILINE),
)

#: Collapsed to whitespace so an instruction loses its imperative shape without
#: the description becoming unreadable. Deliberately short: this is a speed
#: bump, and a long blocklist would imply a completeness it cannot have.
_IMPERATIVE = re.compile(
    r"\b(ignore|disregard|override|forget)\s+(all\s+|any\s+|the\s+|your\s+|previous\s+|prior\s+|above\s+)*"
    r"(instruction|instructions|prompt|prompts|rule|rules|context|directive|directives)\b",
    re.IGNORECASE,
)


def sanitize_tool_name(raw: str) -> str:
    """Reduce a remote tool name to a safe identifier.

    Restricted to ``[a-z0-9_]`` so a remote name cannot carry punctuation,
    whitespace or namespace separators that might confuse a downstream
    dispatcher into resolving it against a different registry.
    """
    name = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower())
    return name.strip("_")[:MAX_NAME_CHARS]


def sanitize_description(raw: str, *, server: str = "") -> str:
    """Return *raw* stripped of structural injection devices and framed.

    The framing matters as much as the stripping: the model is told, in the
    text itself, that what follows is a third-party claim rather than an
    operator instruction.
    """
    text = str(raw or "").strip()
    if not text:
        return ""

    for pattern in _STRUCTURAL:
        text = pattern.sub(" ", text)
    text = _IMPERATIVE.sub(" ", text)

    # Collapse whitespace so removed content leaves no suspicious gaps and a
    # multi-line payload cannot masquerade as document structure.
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip() + "…"

    if not text:
        return ""

    label = f"untrusted description from external MCP server {server!r}" if server else "untrusted external description"
    return f"[{label}] {text}"


def is_suspicious(raw: str) -> bool:
    """True when *raw* contained something worth logging.

    Reported, never used to block: a server whose description trips this is
    still usable with the text stripped, and refusing on a pattern match would
    make the filter itself the thing an attacker probes.
    """
    text = str(raw or "")
    if _IMPERATIVE.search(text):
        return True
    return any(pattern.search(text) for pattern in _STRUCTURAL)
