#!/usr/bin/env python3
"""Every DIC chat surface must send session_id — CUI // SP-CTI.

`blueprint.api_chat` gates conversational memory on::

    if mem_on and session_id:

so a chat surface that omits `session_id` silently gets no memory at all — no
follow-up resolution, no turn recording — while looking like it works.

That is exactly what happened twice: `search.html` was fixed first, and
`notebook.html` was found still broken only after a live probe of the running
dashboard showed the served page lacked the marker. This test enumerates the
surfaces from the templates themselves rather than naming them, so a THIRD chat
surface cannot be added without either sending session_id or failing here.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CHAT_ENDPOINT = "/document-intelligence/api/chat"

# Both the live tree and the packaged mirror must stay correct.
_TEMPLATE_DIRS = [
    _ROOT / "tools" / "dashboard" / "templates" / "document_intelligence",
    _ROOT / "icdev" / "tools" / "dashboard" / "templates" / "document_intelligence",
]


def _chat_surfaces() -> list[pathlib.Path]:
    """Every template that POSTs to the DIC chat endpoint."""
    found: list[pathlib.Path] = []
    for d in _TEMPLATE_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.html")):
            if _CHAT_ENDPOINT in p.read_text(encoding="utf-8", errors="replace"):
                found.append(p)
    return found


def test_chat_surfaces_are_discovered():
    """Guard the guard: if this finds nothing, the test below is vacuous."""
    surfaces = _chat_surfaces()
    assert surfaces, f"no template POSTs to {_CHAT_ENDPOINT} — has the endpoint moved?"
    names = {p.name for p in surfaces}
    # Known at time of writing; a new one is fine, losing both is not.
    assert {"search.html", "notebook.html"} <= names, f"expected both chat surfaces, got {names}"


def _chat_fetch_body(src: str) -> str | None:
    """The JSON.stringify body of the fetch to the CHAT endpoint specifically.

    These templates contain several fetches (search, ingest, ...), so anchoring
    on the endpoint rather than on the first JSON.stringify in the file matters
    — otherwise this test reads a different request's body and reports nonsense.
    """
    anchor = src.find(_CHAT_ENDPOINT)
    if anchor == -1:
        return None
    m = re.search(r"body:\s*JSON\.stringify\((\{.*?\})\)", src[anchor:], re.S)
    return m.group(1) if m else None


@pytest.mark.parametrize("path", _chat_surfaces(), ids=lambda p: f"{p.parts[-4]}/{p.name}")
def test_chat_surface_sends_session_id(path: pathlib.Path):
    src = path.read_text(encoding="utf-8", errors="replace")

    # The fetch body must carry session_id.
    body = _chat_fetch_body(src)
    assert body, f"{path.name}: could not locate the chat fetch body"
    assert "session_id" in body, (
        f"{path.name} POSTs to the chat API without session_id, so "
        "`if mem_on and session_id:` is always False and conversational memory "
        "is silently disabled on this surface."
    )

    # And it must come from a real generator, not a hardcoded or empty literal.
    assert "_dicSessionId()" in body, (
        f"{path.name}: session_id must come from _dicSessionId(), which persists "
        "a stable per-tab id in sessionStorage."
    )
    assert re.search(r"function\s+_dicSessionId\s*\(", src), (
        f"{path.name}: _dicSessionId() is used but not defined in this template."
    )


@pytest.mark.parametrize("path", _chat_surfaces(), ids=lambda p: f"{p.parts[-4]}/{p.name}")
def test_session_id_is_tab_scoped_not_persistent(path: pathlib.Path):
    """sessionStorage, not localStorage: closing the tab ends the conversation."""
    src = path.read_text(encoding="utf-8", errors="replace")
    fn = re.search(r"function\s+_dicSessionId\s*\(\)\s*\{(.*?)\n\}", src, re.S)
    assert fn, f"{path.name}: _dicSessionId body not found"
    assert "sessionStorage" in fn.group(1)
    assert "localStorage" not in fn.group(1), (
        f"{path.name}: localStorage would make one conversation span every tab "
        "and outlive the browser session."
    )
