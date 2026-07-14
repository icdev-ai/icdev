# CUI // SP-CTI
"""The dashboard's inline JavaScript must actually parse.

A single unescaped apostrophe inside a single-quoted string — ``'the scheduler's mode'`` —
is a syntax error, and a syntax error in an inline <script> kills the WHOLE block. Not the
one function: every function defined in it. The page still renders, every button still
looks right, and every one of them is dead.

That is exactly what shipped in #280: `setBuildModel is not a function`, on a page that
looked completely normal. Python tests pass, ruff passes, CI passes — none of them read
the template's JavaScript.

This runs the inline scripts through node's parser. It does not check behaviour; it checks
that the code the browser is handed is code the browser can run.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = [
    _ROOT / "tools" / "dashboard" / "templates" / "index.html",
]

# Inline <script> blocks with no src=. Jinja blocks inside them are stripped before
# parsing — {{ }} / {% %} are server-side and never reach the browser as JS.
_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node is not installed")
@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda p: p.name)
def test_inline_script_blocks_parse(template: Path):
    html = template.read_text(encoding="utf-8", errors="replace")
    blocks = _SCRIPT.findall(html)
    assert blocks, f"{template.name} has no inline <script> — did the regex drift?"

    for i, block in enumerate(blocks):
        source = _JINJA.sub("0", block)
        if not source.strip():
            continue

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(source)
            path = fh.name

        try:
            result = subprocess.run(
                [_node(), "--check", path],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            Path(path).unlink(missing_ok=True)

        assert result.returncode == 0, (
            f"{template.name} inline <script> #{i} does not parse — a syntax error here "
            f"kills EVERY function in the block, and the page still looks fine:\n"
            f"{result.stderr.strip()[:600]}"
        )


@pytest.mark.skipif(_node() is None, reason="node is not installed")
def test_the_build_switches_are_actually_defined():
    """The specific regression: the toolbar controls call these by name from onclick /
    onchange handlers. If the script block dies, they are undefined and the checkbox and
    the dropdown silently do nothing."""
    html = (_ROOT / "tools" / "dashboard" / "templates" / "index.html").read_text(
        encoding="utf-8", errors="replace")

    for fn in ("setManualBuild", "setBuildModel", "loadBuildMode", "loadBuildModel"):
        assert f"window.{fn} = function" in html, f"{fn} is not defined"
        assert f"{fn}(" in html, f"{fn} is defined but never wired to the toolbar"
