# CUI // SP-CTI
"""rmf-ui-11: an IQE example chip's onclick survives HTML attribute parsing.

includes/iqe_query_widget.html renders each quick-pick example as

    <button class="iqe-chip" onclick="icdevIQEFill('<wid>',<query as JSON>)">

Flask's ``tojson`` filter returns Markup (so autoescape leaves it alone) and
escapes ``<``, ``>``, ``&`` and ``'`` -- but NOT ``"``. A JSON string always
OPENS with ``"``, so inside a double-quoted attribute the very first character
of the query closed the attribute: the browser saw
``onclick="icdevIQEFill('iqew_compliance',"`` and every click raised
``SyntaxError: Unexpected end of input``. Measured on the migrated Compliance
Hub 2026-09-03 -- all four chips truncated at the comma, including the one
whose query contains no inner quote -- and the partial is included by 336
templates, 128 of which pass examples. Every chip on every one of them had
been dead since the line was written.

The fix is ``tojson | forceescape``: the attribute holds ``&quot;`` and the
browser hands the handler the original JSON. This test renders the partial
through the dashboard's own Jinja environment and reads the attribute back
with html.parser -- the same decode a browser performs -- so it is RED on the
old line (the attribute value stops at the comma) and GREEN on the fixed one.
A source grep for ``forceescape`` would have pinned the spelling, not the
behaviour.
"""
from __future__ import annotations

from html.parser import HTMLParser

import pytest

QUOTED = 'foreach v in compliance.violations where v.status == "open" select v.control_id'
PLAIN = "foreach v in compliance.violations select v.status"


class _Chips(HTMLParser):
    def __init__(self):
        super().__init__()
        self.onclicks: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "button" and "iqe-chip" in (a.get("class") or ""):
            self.onclicks.append(a.get("onclick") or "")


@pytest.fixture
def rendered():
    from tools.dashboard.app import app

    with app.test_request_context("/"):
        return app.jinja_env.get_template("includes/iqe_query_widget.html").render(
            iqe_canvas="chiptest",
            iqe_api_route="/api/chiptest/iqe-query",
            iqe_examples=[
                {"label": "Quoted", "query": QUOTED},
                {"label": "Plain", "query": PLAIN},
            ],
        )


def test_every_chip_onclick_carries_the_whole_call(rendered):
    p = _Chips()
    p.feed(rendered)
    assert len(p.onclicks) == 2, rendered[:600]
    for onclick, query in zip(p.onclicks, (QUOTED, PLAIN)):
        # The decoded attribute is a complete JS call, closed with ')'.
        assert onclick.startswith("icdevIQEFill('iqew_chiptest',"), onclick
        assert onclick.rstrip().endswith(")"), f"attribute truncated: {onclick!r}"
        # And the JSON argument round-trips to the query the template was given.
        import json

        arg = onclick[len("icdevIQEFill('iqew_chiptest',"):].rstrip()[:-1]
        assert json.loads(arg) == query


def test_the_raw_html_holds_no_bare_quote_inside_the_onclick_attribute(rendered):
    """The literal defect: a '"' inside onclick="..." ends the attribute early."""
    chip_lines = [line for line in rendered.splitlines() if '<button class="iqe-chip"' in line]
    assert len(chip_lines) == 2, rendered[:600]
    for line in chip_lines:
        start = line.index('onclick="') + len('onclick="')
        end = line.index('"', start)
        inside = line[start:end]
        assert inside.rstrip().endswith(")"), f"onclick attribute closed early: {inside!r}"
