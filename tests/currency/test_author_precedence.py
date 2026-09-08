# CUI // SP-CTI
"""Author-supplied content as a DECLARED source, ranked top (dwr-ev-01).

The card's DONE, at the store: an author asserts a product is current while the
curated catalog says retired; ``resolve()`` returns the author's assertion
FIRST, the catalog's under ``others``, and ``conflict: True``. Three things are
pinned around that, because each is how the change could quietly be wrong:

* the ranking comes from the ONE resolver, through a declared ``precedence``
  key — no second copy of the rule anywhere;
* every source shipped before this card ranks EXACTLY as it did (the catalog
  still beats a newer, more confident feed), because none of them declares a
  precedence and they all tie on the default;
* the author's row is written under its own source id, so writing it cannot
  touch the catalog's row — disagreement is preserved by construction.

Entities are deliberately not real products: the store must not care what an
entity IS, and a test that named one would be asserting about a vendor.
"""
from __future__ import annotations

import pytest

from tests.currency.test_entity_currency import _conn, _seed_sources, db  # noqa: F401

_PRODUCT = "model-gamma-4400"
_AUTHORITY = "example-corp"
_DOC = "dic_doc_test_author"


def _seed_author(**over):
    from tools.document_intelligence.author_evidence import ensure_table, record_assertions

    conn = _conn()
    ensure_table(conn)
    assertion = {
        "entity_label": _PRODUCT,
        "entity_type": "chassis",
        "namespace": _AUTHORITY,
        "status": "fielded",
        "as_of": "2026-08-01",
        "asserted_by": "network-lead",
    }
    assertion.update(over)
    out = record_assertions(
        conn, doc_id=over.pop("doc_id", _DOC), assertions=[assertion],
        version_id=f"{_DOC}_v1", uploaded_at="2026-09-01T00:00:00+00:00",
    )
    conn.commit()
    conn.close()
    return out


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def test_author_source_is_declared_in_the_idiom_of_the_other_sources():
    from tools.currency.entity_currency import declared_sources, load_config
    from tools.document_intelligence.author_evidence import (
        AUTHOR_STATUSES, SOURCE_ID, SOURCE_KIND, TABLE,
    )

    spec = {s["id"]: s for s in declared_sources()}[SOURCE_ID]
    assert spec["kind"] == SOURCE_KIND
    assert spec["table"] == TABLE
    assert spec["precedence"] == 0
    # NOT authoritative — that word stays the curated catalog's.
    assert not spec.get("authoritative")
    # The author's clock is the store's as_of, never ours.
    assert spec["columns"]["as_of"] == "as_of"
    # `precedence` is applied FIRST, and the three original keys follow in
    # their original order.
    assert load_config()["resolution"]["order"] == [
        "precedence", "authoritative", "confidence", "as_of",
    ]
    # Every word the upload accepts is mapped by the YAML — a word validated
    # at the writer and unmapped at the store would silently read `unknown`.
    mapped = set((spec["verdict"].get("map") or {}))
    assert set(AUTHOR_STATUSES) <= mapped, set(AUTHOR_STATUSES) - mapped


def test_only_the_author_source_declares_a_precedence():
    """The whole point of a DEFAULT: the four sources shipped before this card
    tie on it and fall through to `authoritative` exactly as before."""
    from tools.currency.entity_currency import DEFAULT_PRECEDENCE, _precedence, declared_sources
    from tools.document_intelligence.author_evidence import SOURCE_ID

    by_id = {s["id"]: s for s in declared_sources(enabled_only=False)}
    for sid, spec in by_id.items():
        got = _precedence(by_id, {"source": sid})
        if sid == SOURCE_ID:
            assert got < DEFAULT_PRECEDENCE
        else:
            assert "precedence" not in spec
            assert got == DEFAULT_PRECEDENCE


# ---------------------------------------------------------------------------
# The DONE criterion, at the store
# ---------------------------------------------------------------------------

def test_author_outranks_the_curated_catalog_and_the_catalog_survives(db):  # noqa: F811
    from tools.currency.entity_currency import backfill, query, resolve
    from tools.document_intelligence.author_evidence import SOURCE_ID

    # The curated catalog says RETIRED. Authoritative, confidence 0.95, and
    # its as_of is NEWER than the author's — every key the old policy ranked on
    # favours the catalog.
    _seed_sources(catalog_rows=[("chassis", _AUTHORITY, _PRODUCT, "retired", "")])
    conn = _conn()
    conn.execute(
        "UPDATE docmod_catalog_entries SET updated_at = %s",
        ("2026-08-30T00:00:00+00:00",),
    )
    conn.commit()
    conn.close()
    assert backfill()["errors"] == {}

    # The author says STILL FIELDED as of 2026-08-01.
    out = _seed_author()
    assert out["recorded"] == 1
    assert out["store"]["sources"][SOURCE_ID]["written"] == 1

    view = resolve(_PRODUCT, entity_type="chassis", namespace=_AUTHORITY)
    assert view is not None
    # The author's assertion is ranked FIRST ...
    assert view["source"] == SOURCE_ID
    assert view["verdict"] == "current"
    assert view["source_kind"] == "author_supplied"
    assert view["precedence"] == 0
    assert view["authoritative"] is False
    assert view["as_of"].startswith("2026-08-01")
    # ... the disagreement is VISIBLE ...
    assert view["conflict"] is True
    assert view["sources_consulted"] == sorted([SOURCE_ID, "docmod_catalog_entries"])
    # ... and the catalog's row is STILL THERE, ranked second, with its own
    # verdict, its authority and its rank on it — never deleted, never edited.
    (other,) = view["others"]
    assert other["source"] == "docmod_catalog_entries"
    assert other["verdict"] == "end_of_life"
    assert other["authoritative"] is True
    assert other["rank"] == 1
    rows = query(_PRODUCT, entity_type="chassis", namespace=_AUTHORITY)
    assert {r["source"]: r["verdict"] for r in rows} == {
        SOURCE_ID: "current", "docmod_catalog_entries": "end_of_life",
    }


def test_the_original_sources_rank_exactly_as_before(db):  # noqa: F811
    """Regression: with no author row, the catalog still beats a newer and more
    confident feed. `precedence` ties on the default and `authoritative` decides."""
    from tools.currency.entity_currency import backfill, resolve

    _seed_sources(
        hw_rows=[(_AUTHORITY, _PRODUCT, "2020-01-01", None)],           # feed: end_of_life
        catalog_rows=[("chassis", _AUTHORITY, _PRODUCT, "approved", "")],  # catalog: current
    )
    conn = _conn()
    conn.execute("UPDATE mc_net_eol_data SET synced_at = %s", ("2026-09-01T00:00:00+00:00",))
    conn.commit()
    conn.close()
    assert backfill()["errors"] == {}

    view = resolve(_PRODUCT, namespace=_AUTHORITY)
    assert view["source"] == "docmod_catalog_entries"
    assert view["verdict"] == "current"
    assert view["authoritative"] is True
    assert view["precedence"] == 100
    assert view["conflict"] is True
    assert [o["source"] for o in view["others"]] == ["mc_net_eol_data"]


# ---------------------------------------------------------------------------
# Two clocks, and what the writer refuses
# ---------------------------------------------------------------------------

def test_as_of_is_the_authors_clock_and_a_defaulted_one_says_so(db):  # noqa: F811
    from tools.currency.entity_currency import query
    from tools.document_intelligence.author_evidence import list_assertions

    _seed_author()
    _seed_author(entity_label="model-delta-1", as_of=None, doc_id="dic_doc_other")

    stated = list_assertions(entity_key=_PRODUCT)[0]
    assert stated["as_of"] == "2026-08-01"
    assert stated["as_of_basis"] == "author_stated"
    defaulted = list_assertions(entity_key="model-delta-1")[0]
    assert defaulted["as_of"].startswith("2026-09-01")
    assert defaulted["as_of_basis"] == "upload_time"

    # In the store: as_of is the author's, observed_at is ours, and they differ.
    (row,) = query(_PRODUCT, entity_type="chassis", namespace=_AUTHORITY)
    assert row["as_of"] == "2026-08-01"
    assert row["observed_at"] > "2026-09-01"
    assert row["provenance_table"] == "dic_author_assertions"
    assert row["provenance_id"] == stated["assertion_id"]


def test_a_later_statement_is_the_latest_fact_and_the_earlier_one_is_kept(db):  # noqa: F811
    from tools.currency.entity_currency import resolve
    from tools.document_intelligence.author_evidence import list_assertions

    _seed_author(status="fielded", as_of="2026-03-01", doc_id="dic_doc_march")
    _seed_author(status="retired", as_of="2026-08-01", doc_id="dic_doc_august")

    view = resolve(_PRODUCT, entity_type="chassis", namespace=_AUTHORITY)
    assert view["verdict"] == "end_of_life"
    assert view["as_of"] == "2026-08-01"
    # Both statements remain readable at their origin.
    assert [a["status"] for a in list_assertions(entity_key=_PRODUCT)] == ["retired", "fielded"]


@pytest.mark.parametrize("bad,reason", [
    ({"entity_type": "chassis", "status": "fielded"}, "names no entity"),
    ({"entity_label": "x", "status": "fielded"}, "names no entity_type"),
    ({"entity_label": "x", "entity_type": "chassis", "status": "probably fine"}, "is not one of"),
    ({"entity_label": "x", "entity_type": "chassis", "status": "fielded", "as_of": "last spring"},
     "not an ISO date"),
    ({"entity_label": "x", "entity_type": "chassis", "status": "fielded", "eol_date": "soon"},
     "not an ISO date"),
])
def test_a_malformed_assertion_is_refused_not_stored_as_unknown(bad, reason):
    from tools.document_intelligence.author_evidence import (
        AuthorAssertionError, normalize_assertion, parse_assertions,
    )

    with pytest.raises(AuthorAssertionError, match=reason):
        normalize_assertion(bad)
    # A list is refused WHOLE: recording half of what an author declared,
    # silently, is worse than recording none of it and saying so.
    with pytest.raises(AuthorAssertionError, match=r"author_assertions\[1\]"):
        parse_assertions([
            {"entity_label": "ok", "entity_type": "chassis", "status": "current"}, bad,
        ])


def test_parse_accepts_the_form_fields_short_spellings():
    from tools.document_intelligence.author_evidence import parse_assertions

    (a,) = parse_assertions(
        '[{"entity": "Model Gamma  4400", "type": "Chassis", "vendor": "Example-Corp",'
        ' "status": "In-Use", "version": "v2", "as_of": "2026-08-01T10:00:00Z"}]'
    )
    assert a["entity_label"] == "Model Gamma 4400"
    assert a["entity_key"] == "model gamma 4400"
    assert a["entity_type"] == "chassis"
    assert a["namespace"] == "example-corp"
    assert a["status"] == "in_use"
    assert a["entity_version"] == "v2"
    assert a["as_of"] == "2026-08-01T10:00:00+00:00"
    assert a["as_of_basis"] == "author_stated"
    assert parse_assertions("") == [] and parse_assertions(None) == []


# ---------------------------------------------------------------------------
# No second precedence rule, and no model anywhere
# ---------------------------------------------------------------------------

def test_the_writer_carries_no_precedence_logic_and_no_model():
    """Structural: author_evidence.py neither ranks sources nor calls a model.
    The ranking is the resolver's; the verdict map is the YAML's."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "tools" / "document_intelligence" / "author_evidence.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {n.id.casefold() for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr.casefold() for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    banned = {"llmrouter", "llmrequest", "invoke", "complete", "cortex_ask", "authoritative",
              "_sort_by_policy", "_resolution_view", "resolve"}
    assert not (names & banned), sorted(names & banned)
    imported = {
        (n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    }
    assert not any(m.startswith("tools.llm") or m.startswith("tools.cortex") for m in imported)
