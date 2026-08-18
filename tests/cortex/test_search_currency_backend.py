# CUI // SP-CTI
"""Tests for the Cortex ``currency`` backend adapter (cef-bck-01).

The adapter answers "is this entity still current?" over two lanes:

* the ASSERTION lane — ``tools/currency/entity_currency.py``, which already
  aggregates the curated catalog and both EOL feeds and resolves them under
  the declared authority policy;
* the LEARNER lane — ``docmod_defacto_standards``, the de-facto standards the
  inventory feeds learned, which is corroboration and tie-breaker only.

Both lanes are monkeypatched at the module attribute the adapter resolves at
call time (shim-aware: importlib + setattr), so these tests exercise the
adapter's normalization, banding and error annotation rather than the DB.

The assertion that matters most is the error one: a dead backing table must
produce ``BackendResults`` carrying ``.errors``, never an empty success — the
two are byte-identical to every caller that only reads the list.
"""
from __future__ import annotations

import importlib

import pytest
import yaml

from tools.cortex import search_service
from tools.cortex.schemas import CORTEX_BACKENDS


# ---------------------------------------------------------------------------
# Fixture shapes captured from the real store / learner returns
# ---------------------------------------------------------------------------

def _curated_view() -> dict:
    """One entity_currency.search() view whose winner is the curated catalog."""
    return {
        "entity_key": "tls 1.1",
        "entity_type": "protocol",
        "namespace": "",
        "entity_version": "",
        "entity_label": "TLS 1.1",
        "verdict": "deprecated",
        "superseded_by": "tls 1.3",
        "source": "docmod_catalog_entries",
        "authoritative": True,
        "as_of": "2026-07-10T16:43:30+00:00",
        "confidence": 0.95,
        "eol_date": None,
        "eos_date": None,
        "classification": "CUI",
        "provenance": {
            "table": "docmod_catalog_entries",
            "id": "cat-tls-11",
            "record_id": "ec-abc123",
        },
        "conflict": False,
        "sources_consulted": ["docmod_catalog_entries"],
        "others": [],
        "match": 1.0,
    }


def _feed_view() -> dict:
    """A non-authoritative external EOL feed assertion."""
    view = _curated_view()
    view.update(
        entity_key="openssl",
        entity_type="software_release",
        entity_label="openssl",
        entity_version="1.1.1",
        verdict="end_of_life",
        source="docmod_eol_products",
        authoritative=False,
        confidence=0.8,
        eol_date="2023-09-11",
        provenance={
            "table": "docmod_eol_products",
            "id": "eol-openssl-111",
            "record_id": "ec-def456",
        },
        sources_consulted=["docmod_eol_products"],
    )
    return view


def _learner_row() -> dict:
    """One docmod_defacto_standards row as defacto_learner.search() returns it."""
    return {
        "id": "df-1",
        "domain": "network_hardware",
        "category": "firewall",
        "vendor": "acme",
        "product": "TLS 1.1 terminator",
        "version": "9",
        "deploy_count": 240,
        "weighted_score": 188.4,
        "share_pct": 97.5,
        "computed_at": "2026-08-17T00:00:00+00:00",
        "source_feed": "ni_devices",
        "evidence_kind": "inventory",
        "precedence": 10,
        "match": 1.0,
    }


@pytest.fixture()
def lanes(monkeypatch):
    """Patch both lanes; each test sets ``lanes['store']`` / ``lanes['learner']``.

    A lane value that is an exception instance is RAISED, which is how a dead
    backing table is simulated.
    """
    state: dict = {"store": [], "learner": []}
    store = importlib.import_module("tools.currency.entity_currency")
    learner = importlib.import_module("tools.doc_modernization.defacto_learner")

    def _lane(key):
        def _search(*args, **kwargs):
            value = state[key]
            if isinstance(value, Exception):
                raise value
            return value
        return _search

    monkeypatch.setattr(store, "search", _lane("store"))
    monkeypatch.setattr(learner, "search", _lane("learner"))
    return state


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_currency_is_a_registered_backend():
    assert "currency" in CORTEX_BACKENDS
    assert "currency" in search_service.BACKEND_ADAPTERS
    assert search_service.BACKEND_ADAPTERS["currency"] is search_service.search_currency
    assert "currency" in search_service.CORTEX_STRATEGIES


def test_cortex_config_declares_weight_timeout_and_fan_out():
    """Registration point 5 — the three config keys, in the shipped YAML."""
    from tools.cortex.config import resolve_cortex_config_path

    cfg = yaml.safe_load(
        resolve_cortex_config_path().read_text(encoding="utf-8")
    )["search"]
    assert "currency" in cfg["strategy_weights"]
    assert "currency" in cfg["timeouts"]
    assert "currency" in cfg["fan_out"]["backends"]


# ---------------------------------------------------------------------------
# Contract: cited results for a known-deprecated entity
# ---------------------------------------------------------------------------

def test_deprecated_entity_returns_a_cited_result(lanes):
    lanes["store"] = [_curated_view()]

    results = search_service.search_currency("is TLS 1.1 still current?", top_k=5)

    assert len(results) == 1
    hit = results[0]
    assert hit.backend == "currency"
    assert 0.0 <= hit.score <= 1.0
    # Mandatory citation, pointing at the row the verdict came from.
    assert hit.citation.source_id
    assert hit.citation.source_table == "docmod_catalog_entries"
    assert hit.citation.source_type == "currency_assertion"
    assert "deprecated" in hit.content.lower()
    # Native scores preserved verbatim.
    assert hit.raw_scores["confidence"] == 0.95
    assert hit.metadata["verdict"] == "deprecated"
    assert hit.metadata["authoritative"] is True
    assert hit.metadata["lane"] == "assertion"
    assert not getattr(results, "errors", [])


def test_score_is_clamped_for_an_absurd_native_confidence(lanes):
    view = _curated_view()
    view["confidence"] = 42.0
    lanes["store"] = [view]

    hit = search_service.search_currency("tls", top_k=5)[0]

    assert 0.0 <= hit.score <= 1.0
    assert hit.raw_scores["confidence"] == 42.0  # verbatim, not clamped


# ---------------------------------------------------------------------------
# Authority ordering
# ---------------------------------------------------------------------------

def test_curated_catalog_outranks_feed_and_learner(lanes):
    lanes["store"] = [_feed_view(), _curated_view()]
    lanes["learner"] = [_learner_row()]

    results = search_service.search_currency("tls 1.1 openssl", top_k=10)

    order = [r.metadata.get("source") or r.metadata.get("source_feed") for r in results]
    assert order.index("docmod_catalog_entries") < order.index("docmod_eol_products")
    assert order.index("docmod_eol_products") < order.index("ni_devices")
    assert [r.raw_scores["band"] for r in results] == ["curated", "feed", "learner"]


def test_learner_hit_reports_its_evidence_class(lanes):
    lanes["learner"] = [_learner_row()]

    hit = search_service.search_currency("tls", top_k=5)[0]

    assert hit.metadata["lane"] == "learner"
    assert hit.metadata["evidence_kind"] == "inventory"
    assert hit.metadata["source_feed"] == "ni_devices"
    assert hit.raw_scores["share_pct"] == 97.5
    assert hit.citation.source_table == "docmod_defacto_standards"


def test_conflict_between_sources_is_reported_not_squashed(lanes):
    view = _curated_view()
    view["conflict"] = True
    view["sources_consulted"] = ["docmod_catalog_entries", "docmod_eol_products"]
    view["others"] = [
        {"source": "docmod_eol_products", "verdict": "end_of_life",
         "confidence": 0.8, "as_of": "2026-08-01T00:00:00+00:00"}
    ]
    lanes["store"] = [view]

    hit = search_service.search_currency("tls 1.1", top_k=5)[0]

    assert hit.metadata["conflict"] is True
    assert [o["source"] for o in hit.metadata["others"]] == ["docmod_eol_products"]
    assert "disagree" in hit.content.lower()


# ---------------------------------------------------------------------------
# A dead backing table is a FAILURE, never an empty success
# ---------------------------------------------------------------------------

def test_dead_store_table_annotates_errors(lanes):
    lanes["store"] = RuntimeError('relation "entity_currency" does not exist')

    results = search_service.search_currency("tls 1.1", top_k=5)

    assert list(results) == []
    errors = getattr(results, "errors", [])
    assert [e["backend"] for e in errors] == ["currency"]
    assert errors[0]["stage"] == "store"
    assert "entity_currency" in errors[0]["message"]


def test_dead_learner_table_still_returns_store_hits_with_errors(lanes):
    lanes["store"] = [_curated_view()]
    lanes["learner"] = RuntimeError('relation "docmod_defacto_standards" does not exist')

    results = search_service.search_currency("tls 1.1", top_k=5)

    assert len(results) == 1  # partial results beat no results
    errors = getattr(results, "errors", [])
    assert [e["stage"] for e in errors] == ["corroboration"]


def test_empty_corpus_is_not_an_error(lanes):
    results = search_service.search_currency("nothing matches this", top_k=5)

    assert list(results) == []
    assert list(getattr(results, "errors", [])) == []


def test_adapter_never_raises(lanes):
    lanes["store"] = ValueError("store down")
    lanes["learner"] = ValueError("learner down")

    results = search_service.search_currency("tls", top_k=5)

    assert isinstance(results, search_service.BackendResults)
    assert len(getattr(results, "errors", [])) == 2


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def test_currency_route_label_selects_the_currency_backend():
    route = search_service.classify_route("is the Catalyst 6500 end-of-life?")

    assert route["label"] == "currency"
    assert route["backends"] == ["currency"]
    assert search_service.ROUTE_LABEL_BACKENDS["currency"] == ["currency"]


# ---------------------------------------------------------------------------
# Store-level: the read that must NOT swallow its own failure
# ---------------------------------------------------------------------------

def test_entity_currency_search_propagates_a_dead_table():
    """``query()`` swallows and returns []; ``search()`` must not.

    The adapter's only way to tell "the backend died" from "the corpus matched
    nothing" is the exception, so a swallowing search() would silently defeat
    the whole errors annotation above.
    """
    from tools.currency import entity_currency

    class _DeadConn:
        def execute(self, *a, **k):
            raise RuntimeError('relation "entity_currency" does not exist')

        def rollback(self):
            pass

        def close(self):
            pass

    with pytest.raises(RuntimeError):
        entity_currency.search("tls", conn=_DeadConn())


def test_entity_currency_search_terms_drops_noise_words():
    from tools.currency import entity_currency

    terms = entity_currency.search_terms("Is the TLS 1.1 protocol still current?")

    assert "tls" in terms
    assert "1.1" in terms
    assert "is" not in terms
    assert "the" not in terms
