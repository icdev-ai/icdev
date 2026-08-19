# CUI // SP-CTI
"""cef-ui-02 — what /document-intelligence/explorer actually PUTS ON SCREEN.

The store tests prove the finding survives the request. These prove the page
renders BOTH claims with their sources and as-of dates, that the gap list is
browsable and filterable, and that nothing on the page resolves the
disagreement to one side.

Rendered through a bare Jinja environment with a stub ``base.html`` rather than
through the Flask app: importing ``tools.dashboard.app`` mounts ~200 blueprints
and is the wrong dependency for a template assertion. The template file under
test is the real one, loaded from disk.
"""
from __future__ import annotations

import re
from pathlib import Path

# Imported outright, not through importorskip: Jinja2 is a hard dependency of
# this project (Flask renders every dashboard page with it), so a skip here
# would satisfy the CI coverage claim while asserting nothing.
import jinja2
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "tools" / "dashboard" / "templates"
TEMPLATE = "document_intelligence/explorer.html"

_STUBS = {
    "base.html": "{% block content %}{% endblock %}",
    "includes/iqe_query_widget.html": "",
}


def _render(**context) -> str:
    env = jinja2.Environment(
        # The stubs go FIRST so they SHADOW the real base.html (which needs a
        # Flask app context for url_for). explorer.html itself is not stubbed,
        # so it still loads from disk — the file under test is the real one.
        loader=jinja2.ChoiceLoader([
            jinja2.DictLoader(_STUBS),
            jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        ]),
        autoescape=True,
    )
    context.setdefault("findings", [])
    context.setdefault("themes", [])
    return env.get_template(TEMPLATE).render(**context)


def _stat_value(html: str, element_id: str) -> str:
    """The number (or em dash) the stat tile actually renders."""
    match = re.search(r'<div id="' + element_id + r'"[^>]*>([^<]*)<', html)
    assert match, "stat tile " + element_id + " is not on the page"
    return match.group(1).strip()


def _conflict():
    """The motivating case: the curated catalog against a 2019 runbook."""
    return {
        "entity_key": "tls 1.1", "entity_label": "TLS 1.1",
        "conflict_kind": "status", "values": ["current", "deprecated"],
        "cross_backend": True, "seen_count": 3,
        "subject_entity": "TLS 1.1", "subject_verdict": "deprecated",
        "backends": ["currency", "rag", "dic"],
        "sides": [
            {"backend": "currency", "backends": ["currency"], "source": "nist",
             "source_id": "ec-42", "source_table": "entity_currency",
             "as_of": "2026-01-14", "status": "deprecated",
             "raw_status": "withdrawn", "authoritative": True,
             "confidence": 0.95, "extraction": "structured",
             "snippet": "NIST SP 800-52r2 withdraws TLS 1.1."},
            {"backend": "rag", "backends": ["rag", "dic"],
             "source": "Enclave Interconnect Runbook", "source_id": "chunk-9001",
             "source_table": "rag_chunks", "as_of": "2019-05-02",
             "status": "current", "raw_status": "remains approved",
             "authoritative": False, "confidence": 0.4,
             "extraction": "text_pattern",
             "snippet": "TLS 1.1 remains approved for legacy interconnects."},
        ],
        "citations": [{"source_id": "ec-42"}],
        "uncited_sides": [{"source": "vendor-feed", "backend": "currency",
                           "status": "superseded", "reason": "no_row_id"}],
        "reasons": [], "backends_failed": [],
    }


def _gap(label="Catalyst 4500-X", reasons=("no_claim",), failed=()):
    return {
        "entity_key": label.lower(), "entity_label": label,
        "reasons": list(reasons), "backends_failed": list(failed),
        "backends": ["currency", "rag", "dic", "graph", "kb"],
        "citations": [{"source_id": "chunk-7"}], "citation_basis": "evidence",
        "seen_count": 1, "conflict_kind": "", "values": [], "sides": [],
    }


def _cortex(conflicts=(), gaps=(), state="findings", stats=None):
    """``stats`` overrides the COUNTS; the lists are what gets rendered.

    Kept apart on purpose: the two empty states this page exists to distinguish
    are exactly the ones where the counts are NOT ``len(list)``.
    """
    base = {"state": state, "conflicts": len(conflicts), "gaps": len(gaps),
            "resolutions": 12, "clean_resolutions": 4, "cross_backend": 1,
            "last_run_at": "2026-08-19T00:00:00+00:00", "detail": ""}
    base.update(stats or {})
    return {
        "conflicts": list(conflicts), "gaps": list(gaps), "stats": base,
        "filters": {"reasons": ["no_claim", "no_evidence"],
                    "backends": ["currency", "dic", "rag"], "kinds": ["status"]},
        "actionable": {"TLS 1.1": 4},
    }


# ---------------------------------------------------------------------------
# A conflict renders BOTH claims with their sources and as-of dates
# ---------------------------------------------------------------------------
class TestConflictRendersBothSides:
    @pytest.fixture
    def html(self):
        return _render(cortex=_cortex(conflicts=[_conflict()]))

    def test_both_claimed_statuses_appear(self, html):
        assert "deprecated" in html
        assert "current" in html

    def test_both_sources_appear(self, html):
        assert "nist" in html
        assert "Enclave Interconnect Runbook" in html

    def test_both_as_of_dates_appear(self, html):
        # The as-of is what makes a 2019 runbook contradicting a 2026 catalog
        # legible as staleness rather than as an unexplained disagreement.
        assert "2026-01-14" in html
        assert "2019-05-02" in html

    def test_each_side_names_the_row_it_came_from(self, html):
        assert "entity_currency" in html and "ec-42" in html
        assert "rag_chunks" in html and "chunk-9001" in html

    def test_the_extraction_lane_is_visible_per_side(self, html):
        assert "structured" in html and "text_pattern" in html

    def test_two_side_panels_are_rendered(self, html):
        assert html.count('class="conflict-side"') == 2

    def test_an_uncitable_side_is_shown_with_its_reason(self, html):
        assert "vendor-feed" in html and "no_row_id" in html


# ---------------------------------------------------------------------------
# Nothing silently resolves the conflict to one side
# ---------------------------------------------------------------------------
class TestNoSilentResolution:
    @pytest.fixture
    def html(self):
        return _render(cortex=_cortex(conflicts=[_conflict()]))

    def test_the_page_says_it_is_unresolved_by_design(self, html):
        assert "Unresolved by design" in html
        assert "does not" in html and "pick a winner" in html

    def test_no_winner_vocabulary_is_rendered(self, html):
        # The authoritative side must not be presented as "the answer".
        for banned in ("resolved to", "winning claim", "consensus value",
                       "correct value", "the answer is"):
            assert banned.lower() not in html.lower()

    def test_the_authoritative_flag_is_shown_not_applied(self, html):
        """Authority is REPORTED on the side; it does not evict the other one."""
        assert "authoritative" in html
        assert html.count('class="conflict-side"') == 2

    def test_neither_side_is_hidden(self, html):
        panels = re.findall(r'class="conflict-side"[^>]*style="([^"]*)"', html)
        assert len(panels) == 2
        for style in panels:
            assert "display:none" not in style.replace(" ", "")


# ---------------------------------------------------------------------------
# The gap list is browsable and filterable
# ---------------------------------------------------------------------------
class TestGapListIsBrowsable:
    @pytest.fixture
    def html(self):
        return _render(cortex=_cortex(gaps=[
            _gap("Catalyst 4500-X", ["no_claim"]),
            _gap("Nexus 7000", ["no_evidence"]),
            _gap("IPsec IKEv1", ["no_claim"], failed=["kb"]),
        ]))

    def test_every_gap_is_listed(self, html):
        # Count in the SERVER-RENDERED markup only — the client-side re-render
        # carries the same class name in a JS string literal.
        markup = html.split("function loadGaps")[0]
        assert markup.count('class="cortex-gap"') == 3
        for label in ("Catalyst 4500-X", "Nexus 7000", "IPsec IKEv1"):
            assert label in html

    def test_the_filter_controls_are_present(self, html):
        assert 'id="gap-search"' in html
        assert 'id="gap-reason"' in html
        assert 'id="gap-backend"' in html

    def test_the_filter_vocabulary_is_offered(self, html):
        assert 'value="no_claim"' in html and 'value="no_evidence"' in html
        assert 'value="currency"' in html

    def test_filtering_calls_the_server_side_endpoint(self, html):
        assert "/document-intelligence/api/explorer/cortex-findings" in html

    def test_an_outage_is_rendered_distinctly_from_a_reason(self, html):
        # A dead backend must not read as a statement about the corpus.
        assert "outage:" in html
        assert "kb" in html


# ---------------------------------------------------------------------------
# An empty list has four causes and only one of them is "no conflicts"
# ---------------------------------------------------------------------------
class TestEmptyStateIsHonest:
    def test_unmeasured_prints_no_zero(self):
        html = _render(cortex=_cortex(state="unmeasured", stats={
            "conflicts": None, "gaps": None, "cross_backend": None,
            "resolutions": 0,
        }))
        assert "UNMEASured".upper() in html
        assert "clean bill of health" in html
        # The em dash, not a 0 — a surface that never looked must not print a
        # reassuring number.
        assert '<div id="stat-conflicts"' in html
        assert _stat_value(html, "stat-conflicts") == "—"

    def test_clean_says_detection_ran(self):
        html = _render(cortex=_cortex(state="clean", stats={
            "conflicts": 0, "gaps": 0, "cross_backend": 0,
            "detail": "12 resolution(s) recorded.",
        }))
        assert "MEASURED CLEAN" in html
        assert "detection RAN and found nothing" in html
        assert _stat_value(html, "stat-conflicts") == "0", (
            "a MEASURED zero is a real number and prints as one"
        )

    def test_disabled_says_recording_is_off(self):
        html = _render(cortex=_cortex(state="disabled", stats={
            "conflicts": None, "gaps": None, "cross_backend": None,
        }))
        assert "RECORDING OFF" in html
        assert "persist_findings" in html
        assert "says nothing about whether your sources agree" in html

    def test_the_page_renders_when_cortex_is_absent_entirely(self):
        # The explorer's own KG findings must still render if the store is down.
        html = _render()
        assert "Cross-Source Conflicts" in html
        assert "UNMEASURED" in html


# ---------------------------------------------------------------------------
# The finding stays actionable on DocDrift
# ---------------------------------------------------------------------------
class TestActionableOnDocDrift:
    def test_a_conflict_links_to_docdrift_for_the_entity(self):
        html = _render(cortex=_cortex(conflicts=[_conflict()]))
        assert "/document-intelligence/docdrift?entity=TLS" in html

    def test_the_open_docdrift_finding_count_is_shown(self):
        html = _render(cortex=_cortex(conflicts=[_conflict()]))
        assert "4 finding(s)" in html

    def test_a_gap_links_to_docdrift_too(self):
        html = _render(cortex=_cortex(gaps=[_gap()]))
        assert "/document-intelligence/docdrift?entity=Catalyst" in html


def test_the_template_is_mirrored_byte_for_byte():
    """A canvas template ships in BOTH trees or the packaged app renders stale."""
    canonical = TEMPLATE_DIR / "document_intelligence" / "explorer.html"
    mirrored = (REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates"
                / "document_intelligence" / "explorer.html")
    assert mirrored.exists()
    assert mirrored.read_bytes() == canonical.read_bytes()
