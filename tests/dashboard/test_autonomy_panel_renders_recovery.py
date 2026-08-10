# CUI // SP-CTI
"""The Autonomous Recovery panel must render what the API feeds it.

/api/autonomy/status has served `pr_recovery` since the panel started reporting
it — and nothing rendered it. The panel showed "watching" and an empty body
during the exact periods the pipeline was busiest recovering, which made the HITL
alerts look like the only thing the system ever produces when they are the rare
exception. Serving data is not showing it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PANEL = REPO / "tools" / "dashboard" / "templates" / "_autonomy_status.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PANEL.read_text(encoding="utf-8")


def test_the_panel_reads_the_field_the_api_sends(html):
    assert "payload.pr_recovery" in html


def test_it_has_a_row_renderer_and_uses_it(html):
    """A renderer nobody calls is the same as no renderer."""
    assert "function recoveryRowHTML(" in html
    assert "recovery.map(recoveryRowHTML)" in html


def test_recovery_rows_are_escaped(html):
    """Task ids and reasons arrive from a branch name and a PR url — data."""
    body = html[html.index("function recoveryRowHTML("):]
    body = body[:body.index("function triageCardHTML(")]
    assert "escapeHTML(" in body
    assert "innerHTML = " not in body


def test_it_reuses_classes_that_actually_have_css(html):
    """A class with no CSS renders as unstyled text that looks like a broken
    panel — the first version invented .auto-row/.auto-kind/.auto-task."""
    body = html[html.index("function recoveryRowHTML("):]
    body = body[:body.index("function triageCardHTML(")]
    import re
    used = set(re.findall(r'class="(auto-[a-z-]+)"', body))
    defined = set(re.findall(r"\.(auto-[a-z-]+)\s*\{", html))
    assert used <= defined, f"classes with no CSS: {sorted(used - defined)}"


def test_recovery_counts_toward_the_summary_and_the_tag(html):
    """Otherwise the panel says 'watching' while it is actively recovering."""
    assert "auto-recovered (24h)" in html
    assert "'recovering'" in html
