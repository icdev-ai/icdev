# CUI // SP-CTI
"""OPT-68: smoke tests for static/js/undo_toast.js and debounce_filter.js.

These are vanilla-JS files served by the Flask static route, so there is
no JS interpreter in CI. The tests validate basic file hygiene:

    1. Files exist and are non-empty.
    2. Files parse as roughly balanced braces/parens (catches obvious
       syntax breakage on copy-paste edits).
    3. Files register their utilities on window.ICDEV.* (the main wiring
       contract other templates rely on).
    4. Files include CUI classification marking and OPT-68 attribution.
"""
from __future__ import annotations

import pathlib


STATIC_JS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tools" / "dashboard" / "static" / "js"
)


def _read(name: str) -> str:
    path = STATIC_JS / name
    assert path.exists(), f"missing static asset: {path}"
    return path.read_text(encoding="utf-8")


def _count_balanced(text: str, opener: str, closer: str) -> int:
    """Return 0 if opener/closer are balanced, else the imbalance."""
    depth = 0
    in_line_comment = False
    in_block_comment = False
    in_single = False
    in_double = False
    in_tpl = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if escape:
            escape = False
            i += 1
            continue
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_single:
            if ch == "\\":
                escape = True
            elif ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_double = False
            i += 1
            continue
        if in_tpl:
            if ch == "\\":
                escape = True
            elif ch == "`":
                in_tpl = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "`":
            in_tpl = True
            i += 1
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth < 0:
                return depth
        i += 1
    return depth


# ────────────────────────────────────────────────────────────────────────────
# undo_toast.js
# ────────────────────────────────────────────────────────────────────────────


def test_undo_toast_file_exists_and_nonempty():
    body = _read("undo_toast.js")
    assert len(body) > 500


def test_undo_toast_is_classification_marked():
    body = _read("undo_toast.js")
    assert "CUI // SP-CTI" in body
    assert "OPT-68" in body


def test_undo_toast_registers_on_icdev_namespace():
    body = _read("undo_toast.js")
    assert "window.ICDEV" in body
    assert "undoToast" in body
    assert "show" in body


def test_undo_toast_exports_expected_public_api():
    body = _read("undo_toast.js")
    # The bind points other templates rely on
    for must in ("undoCallback", "durationMs", "onExpire", "dismiss"):
        assert must in body, f"missing expected option/key: {must}"


def test_undo_toast_braces_balanced():
    body = _read("undo_toast.js")
    assert _count_balanced(body, "{", "}") == 0
    assert _count_balanced(body, "(", ")") == 0
    assert _count_balanced(body, "[", "]") == 0


# ────────────────────────────────────────────────────────────────────────────
# debounce_filter.js
# ────────────────────────────────────────────────────────────────────────────


def test_debounce_filter_file_exists_and_nonempty():
    body = _read("debounce_filter.js")
    assert len(body) > 500


def test_debounce_filter_is_classification_marked():
    body = _read("debounce_filter.js")
    assert "CUI // SP-CTI" in body
    assert "OPT-68" in body


def test_debounce_filter_registers_on_icdev_namespace():
    body = _read("debounce_filter.js")
    assert "window.ICDEV" in body
    assert "debounceFilter" in body
    assert "bind:" in body or "bind: bind" in body


def test_debounce_filter_exports_bind_and_bindForm():
    body = _read("debounce_filter.js")
    assert "function bind(" in body
    assert "function bindForm(" in body
    assert "_debounce" in body


def test_debounce_filter_braces_balanced():
    body = _read("debounce_filter.js")
    assert _count_balanced(body, "{", "}") == 0
    assert _count_balanced(body, "(", ")") == 0
    assert _count_balanced(body, "[", "]") == 0
