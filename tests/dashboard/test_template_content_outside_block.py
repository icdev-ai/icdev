# CUI // SP-CTI
"""Markup outside a block in an extending template is silently discarded.

The HITL Rebase/Requeue/Dismiss buttons on /monitoring did nothing when clicked
— not even the synchronous 'working…' text. The buttons rendered, the container
rendered, the route existed and CSRF was wired correctly. The click handler sat
AFTER `{% endblock %}`, and Jinja drops anything outside a block in a template
that `{% extends %}` a base. The listener was never on the page.

Nothing errors in that situation. The server logs nothing, the browser console
is clean, and the template still 'works'. Only the behaviour is missing, which
is why this needs a test rather than review.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _ROOT / "tools" / "dashboard" / "templates"

_EXTENDS = re.compile(r"{%-?\s*extends\s", re.I)
_BLOCK = re.compile(r"{%-?\s*block\s+\w+", re.I)
_ENDBLOCK = re.compile(r"{%-?\s*endblock")
_MEANINGFUL = re.compile(r"<(script|div|table|button|section|form|span|p|ul)\b", re.I)


def _templates():
    return sorted(_TEMPLATES.rglob("*.html")) if _TEMPLATES.is_dir() else []


def _orphaned_markup(text: str) -> list:
    """Meaningful tags sitting at block depth 0 in an extending template."""
    if not _EXTENDS.search(text):
        return []
    depth, orphans = 0, []
    for lineno, line in enumerate(text.splitlines(), 1):
        opens, closes = len(_BLOCK.findall(line)), len(_ENDBLOCK.findall(line))
        if depth == 0 and not opens and _MEANINGFUL.search(line):
            orphans.append((lineno, line.strip()[:70]))
        depth += opens - closes
        depth = max(0, depth)
    return orphans


def test_no_meaningful_markup_sits_outside_a_block():
    offenders = {}
    for path in _templates():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = _orphaned_markup(text)
        if found:
            offenders[str(path.relative_to(_ROOT))] = found
    assert not offenders, (
        "these templates extend a base but put markup outside every block, so "
        "Jinja discards it and the page silently loses that behaviour:\n"
        + "\n".join(f"  {f}: line {n}: {s}" for f, v in offenders.items()
                    for n, s in v[:3]))


def test_the_monitoring_hitl_handler_is_inside_the_scripts_block():
    """The concrete regression: the handler must precede the final endblock."""
    path = _TEMPLATES / "monitoring" / "overview.html"
    if not path.exists():
        pytest.skip("monitoring/overview.html not present")
    text = path.read_text(encoding="utf-8", errors="replace")
    handler = text.index("firing-alerts')")
    assert handler < text.rindex("{% endblock %}"), (
        "the HITL click handler is after the last endblock — Jinja will discard "
        "it and the Rebase/Requeue/Dismiss buttons will do nothing")
