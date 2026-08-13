# CUI // SP-CTI
"""Tests for the ``graph_json`` type guard (NDC ``/network/`` 500 regression).

``topologies.graph_json`` is a TEXT column, so ``json.loads`` returns whatever
JSON type the column happens to hold. A DOUBLE-ENCODED blob — the string
``'"{\\"nodes\\":[]}"'`` — decodes *successfully* to a ``str``, not a dict. The
NDC index then did ``g.get("nodes", [])`` and the whole request died with
``AttributeError: 'str' object has no attribute 'get'``.

Wrapping the decode in ``try/except`` did not help: the decode never raised.
The RESULT is what needed the type check.

Impact was not one row: ``nc_index`` renders every topology in a single loop, so
one malformed row (written 2026-07-18, found by the E2E sweep 2026-08-12) made
``GET /network/`` return 500 for ~4 weeks while ``route_smoke.py --all`` stayed
green — its 89 checks cover ``/network/ask`` and ``/network/diagram-analysis``
but not the ``/network/`` index itself.
"""

import json

import pytest

from tools.network.blueprint_helpers import parse_graph_json

EMPTY = {"nodes": [], "edges": []}


def test_double_encoded_blob_yields_empty_graph_not_a_str():
    """The exact shape that took /network/ down: json.loads → str, not dict."""
    raw = json.dumps(json.dumps({"nodes": [{"id": "r1"}], "edges": []}))
    # Precondition: a plain decode really does return a str here.
    assert isinstance(json.loads(raw), str)

    graph = parse_graph_json(raw)

    assert isinstance(graph, dict)
    # The caller's very next move — this is what used to raise AttributeError.
    assert graph.get("nodes", []) == []


@pytest.mark.parametrize(
    "raw",
    [
        '"just a string"',  # JSON string
        "[]",  # JSON array
        "[{\"id\": \"n1\"}]",  # array of nodes (plausible mis-write)
        "123",  # JSON number
        "true",  # JSON bool
        "null",  # JSON null -> None, .get would also raise
    ],
)
def test_non_dict_json_types_coerce_to_empty_graph(raw):
    assert parse_graph_json(raw) == EMPTY


@pytest.mark.parametrize("raw", ["", None, "not json at all", "{unclosed", b"\xff\xfe"])
def test_empty_and_undecodable_input_coerces_to_empty_graph(raw):
    """Undecodable input must not raise either — same contract as before."""
    assert parse_graph_json(raw) == EMPTY


def test_well_formed_graph_is_returned_unchanged():
    """The guard must not disturb the normal path."""
    graph = {"nodes": [{"id": "core1"}, {"id": "core2"}], "edges": [{"a": "core1"}]}

    parsed = parse_graph_json(json.dumps(graph))

    assert parsed == graph
    assert len(parsed.get("nodes", [])) == 2
    assert len(parsed.get("edges", [])) == 1


def test_dict_without_node_keys_is_preserved():
    """A dict is a dict — the guard only rejects non-dicts, it does not reshape."""
    assert parse_graph_json('{"zones": [{"id": "z1"}]}') == {"zones": [{"id": "z1"}]}
