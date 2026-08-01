# CUI // SP-CTI
"""Tests for the two-pass fit_markdown content filter (`oss-filter-01`).

Success criteria under test:
  * >= 50% token reduction across a fixed 20-page corpus at equal-or-better
    answer quality (the answer sentence must survive the filter).
  * Byte-identical output for identical input.
  * Relevance selection, not positional truncation — answers buried past the
    7000-character cut `url_analyzer` uses today are still returned.
"""

from __future__ import annotations

import copy
import re

import pytest

from tests.fixtures.page_extract_corpus import CHROME_MARKERS, CORPUS
from tools.http import page_extract

# Character-per-token proxy; the assertion is on relative reduction, so the
# exact divisor cancels out.
_URL_ANALYZER_CUT = 7000


def _tokens(text: str) -> int:
    return len(text.split())


@pytest.fixture(scope="module")
def cfg() -> dict:
    return page_extract.load_config()


# ── determinism ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("slug,query,answer,html", CORPUS, ids=[c[0] for c in CORPUS])
def test_output_is_byte_identical_for_identical_input(slug, query, answer, html):
    first = page_extract.extract(html, query=query)
    second = page_extract.extract(html, query=query)
    assert first == second
    assert first["fit_markdown"].encode("utf-8") == second["fit_markdown"].encode("utf-8")


def test_no_query_path_is_also_deterministic():
    html = CORPUS[0][3]
    assert page_extract.extract(html) == page_extract.extract(html)


# ── success criterion: token reduction at equal-or-better answer quality ──────
def test_corpus_token_reduction_at_least_50_percent_with_answers_preserved(cfg):
    raw_total = 0
    fit_total = 0
    misses = []
    for slug, query, answer, html in CORPUS:
        result = page_extract.extract(html, query=query, config=copy.deepcopy(cfg))
        raw_total += _tokens(result["raw_text"])
        fit_total += _tokens(result["fit_markdown"])
        if answer.lower() not in result["fit_markdown"].lower():
            misses.append(slug)

    reduction = 1.0 - (fit_total / raw_total)
    assert not misses, f"answer lost by the filter on: {misses}"
    assert reduction >= 0.50, f"only {reduction:.1%} token reduction (need >= 50%)"


def test_relevance_beats_positional_truncation():
    """The defect this replaces: url_analyzer cuts at a fixed offset."""
    long_pages = [c for c in CORPUS if len(c[3]) > _URL_ANALYZER_CUT]
    assert long_pages, "corpus must contain pages longer than the positional cut"

    rescued = 0
    for slug, query, answer, html in long_pages:
        result = page_extract.extract(html, query=query)
        assert answer.lower() in result["fit_markdown"].lower(), slug
        if answer.lower() not in html[:_URL_ANALYZER_CUT].lower():
            rescued += 1
    assert rescued, "no corpus page buries its answer past the positional cut"


# ── pass 1: pruning ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("slug,query,answer,html", CORPUS, ids=[c[0] for c in CORPUS])
def test_chrome_and_scripts_never_reach_the_output(slug, query, answer, html):
    markdown = page_extract.extract(html)["fit_markdown"]
    for marker in CHROME_MARKERS:
        assert marker not in markdown, f"{slug}: chrome leaked -> {marker!r}"


def test_dropped_blocks_record_why():
    result = page_extract.extract(CORPUS[0][3])
    assert result["dropped_blocks"], "pruning must be auditable"
    for record in result["dropped_blocks"]:
        assert record["stage"] in ("prune", "bm25")
        assert record["reason"]
        assert "tag" in record and "score" in record


def test_thresholds_come_from_args_not_code(cfg):
    """Raising the threshold must change the outcome — proves args are load-bearing."""
    permissive = copy.deepcopy(cfg)
    permissive["pruning"]["threshold"] = 0.0
    permissive["pruning"]["min_word_threshold"] = 0
    strict = copy.deepcopy(cfg)
    strict["pruning"]["threshold"] = 0.99

    html = CORPUS[0][3]
    loose = page_extract.extract(html, config=permissive)["fit_markdown"]
    tight = page_extract.extract(html, config=strict)["fit_markdown"]
    assert len(loose) > len(tight)


def test_fixed_threshold_type_ignores_dynamic_adjustments(cfg):
    fixed = copy.deepcopy(cfg)
    fixed["pruning"]["threshold_type"] = "fixed"
    result = page_extract.extract(CORPUS[0][3], config=fixed)
    assert "type=fixed" in result["reason"]


# ── pass 2: BM25 ──────────────────────────────────────────────────────────────
def test_bm25_pass_is_skipped_without_a_query():
    result = page_extract.extract(CORPUS[2][3])
    assert "pass 2 skipped" in result["reason"]
    assert all(d["stage"] == "prune" for d in result["dropped_blocks"])


def test_bm25_narrows_the_output_versus_no_query():
    slug, query, answer, html = CORPUS[0]
    unfiltered = page_extract.extract(html)["fit_markdown"]
    filtered = page_extract.extract(html, query=query)["fit_markdown"]
    assert len(filtered) < len(unfiltered)
    assert answer.lower() in filtered.lower()


def test_bm25_min_blocks_floor_is_honoured(cfg):
    """A query that matches nothing still returns the configured floor."""
    tuned = copy.deepcopy(cfg)
    tuned["bm25"]["min_blocks"] = 2
    tuned["bm25"]["threshold"] = 999.0
    result = page_extract.extract(
        CORPUS[2][3], query="zzzz qqqq unrelated gibberish", config=tuned
    )
    assert result["fit_markdown"].strip()


def test_fallback_bm25_matches_rank_bm25_ranking():
    """The pure-Python fallback must rank identically to rank_bm25."""
    rank_bm25 = pytest.importorskip("rank_bm25")
    corpus = [
        ["rotate", "signing", "key", "worker"],
        ["billing", "invoice", "tenant"],
        ["rotate", "key", "grace", "period", "key"],
    ]
    query = ["rotate", "key"]
    reference = rank_bm25.BM25Okapi(corpus, k1=1.5, b=0.75).get_scores(query)
    fallback = page_extract._FallbackBM25(corpus, 1.5, 0.75).get_scores(query)
    assert [round(s, 6) for s in reference] == [round(s, 6) for s in fallback]


# ── markdown fidelity ─────────────────────────────────────────────────────────
def test_headings_lists_and_tables_survive_as_markdown():
    markdown = page_extract.extract(CORPUS[2][3])["fit_markdown"]  # rate-limits page
    assert "# API Reference" in markdown
    assert "## Rate limits" in markdown
    assert "| Tier | Requests/min | Burst |" in markdown
    assert "| --- | --- | --- |" in markdown
    assert "| Enterprise | 6000 | 12000 |" in markdown

    sso = page_extract.extract(CORPUS[4][3])["fit_markdown"]
    assert "1. Register the service provider" in sso

    install = page_extract.extract(CORPUS[5][3])["fit_markdown"]
    assert "```" in install and "pipx install icdev" in install


def test_links_are_reference_style_and_urls_stay_out_of_the_prose():
    result = page_extract.extract(CORPUS[0][3])
    body, _, refs = result["fit_markdown"].partition("## References")
    assert "[JWKS endpoint][1]" in body
    assert "https://" not in body, "raw URLs must not inline into the LLM context"
    assert "[1]: https://jwks.example.gov/keys" in refs
    assert result["links"] == [
        {"index": 1, "url": "https://jwks.example.gov/keys", "text": "JWKS endpoint"}
    ]


def test_reference_indices_are_contiguous_and_ordered():
    html = (
        "<html><body><article><h1>Refs</h1>"
        "<p>See <a href='https://a.example/one'>alpha</a> and also the very much "
        "longer discussion over at <a href='https://b.example/two'>beta</a> which "
        "covers the remaining cases in detail.</p></article></body></html>"
    )
    result = page_extract.extract(html)
    assert [item["index"] for item in result["links"]] == [1, 2]
    assert re.search(r"\[alpha\]\[1\].*\[beta\]\[2\]", result["fit_markdown"], re.S)


def test_title_falls_back_to_h1_when_absent():
    html = "<html><body><article><h1>Fallback Title</h1><p>" + ("word " * 40) + "</p></article></body></html>"
    assert page_extract.extract(html)["title"] == "Fallback Title"


# ── robustness against hostile / malformed input ──────────────────────────────
@pytest.mark.parametrize(
    "html",
    [
        "",
        "not html at all",
        "<html><body><div><p>unclosed paragraph<div></body>",
        "</div></p></body></html>",
        "<html><body>" + "<div>" * 200 + "deep" + "</div>" * 200 + "</body></html>",
    ],
)
def test_malformed_input_does_not_raise(html):
    result = page_extract.extract(html, query="anything")
    assert set(result) == {
        "title",
        "raw_text",
        "fit_markdown",
        "links",
        "dropped_blocks",
        "reason",
    }


def test_javascript_hrefs_are_not_emitted_as_links():
    html = (
        "<html><body><article><h1>H</h1><p>Click "
        "<a href='javascript:alert(1)'>here</a> to continue reading about the "
        "configuration options available in this release of the platform.</p>"
        "</article></body></html>"
    )
    result = page_extract.extract(html)
    assert result["links"] == []
    assert "javascript:" not in result["fit_markdown"]
    assert "here" in result["fit_markdown"]
