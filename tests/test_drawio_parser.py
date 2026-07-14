# CUI // SP-CTI
"""drawio_parser — the three XML shapes that actually arrive.

There was no test file for this parser, which is how it shipped unable to read a
file saved by draw.io. It handled a bare <mxGraphModel> (what its callers passed
it) and returned an empty graph for a real .drawio file (<mxfile> wrapping one
<diagram> per tab) — with no error. A diagram full of components read as "no
components", and nothing anywhere said so.
"""
from __future__ import annotations

from tools.simulation.parsers.drawio_parser import parse_drawio

_CELLS = (
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="n1" value="Core Switch" style="rounded=1;" vertex="1" parent="1"/>'
    '<mxCell id="n2" value="Firewall" style="rounded=0;" vertex="1" parent="1"/>'
    '<mxCell id="e1" edge="1" source="n1" target="n2" parent="1"/>'
)


def _labels(result: dict) -> list[str]:
    return [n["label"] for n in result["nodes"]]


class TestAcceptedShapes:
    def test_bare_mxgraphmodel(self):
        """The existing contract. Every current caller passes this."""
        r = parse_drawio(f"<mxGraphModel><root>{_CELLS}</root></mxGraphModel>")
        assert _labels(r) == ["Core Switch", "Firewall"]
        assert len(r["edges"]) == 1

    def test_bare_root(self):
        r = parse_drawio(f"<root>{_CELLS}</root>")
        assert _labels(r) == ["Core Switch", "Firewall"]

    def test_a_real_drawio_file(self):
        """<mxfile>/<diagram>/<mxGraphModel>/<root> — two levels below find().

        This used to return zero nodes. find("root") searches direct children
        only, so it missed, fell through to a best-effort branch, and
        findall("mxCell") on <mxfile> matched nothing. An empty architecture is
        worse than a crash: it is indistinguishable from a diagram somebody
        genuinely left blank.
        """
        r = parse_drawio(
            f'<mxfile><diagram name="Topology"><mxGraphModel><root>{_CELLS}'
            "</root></mxGraphModel></diagram></mxfile>"
        )
        assert _labels(r) == ["Core Switch", "Firewall"]
        assert len(r["edges"]) == 1

    def test_malformed_xml_reports_the_error(self):
        r = parse_drawio("<mxfile><not closed")
        assert r["nodes"] == []
        assert "parse_error" in r


class TestMultiTabCaveat:
    def test_only_the_first_tab_is_returned(self):
        """Documented, not accidental.

        A .drawio file with several tabs has one <root> per tab, and this parser
        returns a single graph. Collapsing a floor plan, a topology and a rack
        elevation into one node list would throw away which drawing a component
        came from — and the rack elevation is the only one that says how many.
        Callers needing that distinction iterate <diagram> themselves; see
        tools/bom/extract_grid.py::_extract_drawio.
        """
        two_tabs = (
            "<mxfile>"
            '<diagram name="One"><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="a" value="Alpha" vertex="1" parent="1"/>'
            "</root></mxGraphModel></diagram>"
            '<diagram name="Two"><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="b" value="Beta" vertex="1" parent="1"/>'
            "</root></mxGraphModel></diagram>"
            "</mxfile>"
        )
        assert _labels(parse_drawio(two_tabs)) == ["Alpha"]
