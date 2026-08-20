# CUI // SP-CTI
"""cef-ui-01 — the DocDrift page must not let four different answers look alike.

The card's thesis, and every test here: "we checked and it is current",
"nothing we have could answer", "we never asked" and "the backend died" are
four separate facts. The bug this suite guards against is any two of them
rendering the same way.

The tests are deliberately about the SEAM and the TEMPLATE, not about Cortex.
``resolve()`` already has its own tests; what was untested is whether the page
in front of it preserves the distinctions it draws.
"""
from __future__ import annotations

import importlib

import pytest

de = importlib.import_module("tools.document_intelligence.docdrift_evidence")


# --------------------------------------------------------------------------- #
# Axis 1 — the deterministic verdict
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "verdict,source,expected",
    [
        ("current", "pack_evaluate", "current"),
        ("deprecated", "pack_evaluate", "deprecated"),
        ("superseded", "pack_evaluate", "superseded"),
        ("unknown", "pack_evaluate", "unknown"),
        # No pack recognised the entity. Still `unknown` — the gap reasons say
        # which kind, and finding_state deliberately does not have to.
        ("unknown", "none", "unknown"),
    ],
)
def test_finding_state_reads_the_verdict_only(verdict, source, expected):
    assert de.finding_state(verdict, source) == expected


def test_an_absent_verdict_is_not_resolved_never_current():
    """THE core assertion of the card.

    An empty verdict means nothing was asked. If this ever returned "current"
    — or anything a template would style green — an unchecked finding would be
    indistinguishable from a checked, healthy one.
    """
    assert de.finding_state("", "none") == "not_resolved"
    assert de.finding_state(None, "none") == "not_resolved"
    assert de.finding_state("", "none") != "current"


def test_an_unrecognised_verdict_degrades_to_unknown_not_to_a_new_state():
    """A token outside RESOLVE_VERDICTS must not reach the template.

    The template styles by state name; an unrecognised one renders unstyled and
    reads to an operator as a new category nobody documented.
    """
    assert de.finding_state("probably fine", "pack_evaluate") == "unknown"


def test_backend_errors_cannot_reach_the_verdict():
    """finding_state does not accept backend_errors, and that is the point.

    A dead retrieval rung is not a currency verdict. The signature is the
    enforcement: there is no parameter through which an outage could arrive.
    """
    import inspect

    params = set(inspect.signature(de.finding_state).parameters)
    assert params == {"verdict", "verdict_source"}


# --------------------------------------------------------------------------- #
# Axis 2 — evidence health, independent of axis 1
# --------------------------------------------------------------------------- #

def test_a_confident_verdict_can_have_a_degraded_sweep():
    """The live case this whole design is built around.

    Measured 2026-08-18: ``TLS 1.1`` resolves ``superseded`` while four of five
    backends time out. The verdict came from a pack reading a rulebook, so it is
    unaffected — but the sweep behind the citations really is degraded, and one
    field cannot say both.
    """
    errors = [
        {"backend": "rag", "stage": "timeout", "message": "timed out after 10.0s"},
        {"backend": "dic", "stage": "timeout", "message": "timed out after 10.0s"},
        {"backend": "graph", "stage": "timeout", "message": "timed out after 8.0s"},
        {"backend": "kb", "stage": "error", "message": 'column "use_count" does not exist'},
    ]
    consulted = ["currency", "rag", "dic", "graph", "kb"]

    assert de.finding_state("superseded", "pack_evaluate") == "superseded"
    assert de.evidence_health(errors, consulted) == "degraded"


def test_every_backend_dead_is_failed_not_degraded():
    consulted = ["currency", "rag"]
    errors = [{"backend": "currency", "stage": "timeout", "message": "x"},
              {"backend": "rag", "stage": "timeout", "message": "y"}]
    assert de.evidence_health(errors, consulted) == "failed"


def test_no_errors_is_ok_and_an_unresolved_finding_is_unmeasured():
    """"Measured and clean" and "never measured" must not both be `ok`."""
    assert de.evidence_health([], ["currency"], resolved=True) == "ok"
    assert de.evidence_health([], [], resolved=False) == "unmeasured"


def test_a_pack_error_cannot_make_retrieval_look_totally_dead():
    """``resolver`` appends pack errors onto backend_errors.

    A pack is not a retrieval rung, so a pack failure must not be able to push
    evidence health to ``failed`` — that would report a corpus outage that did
    not happen.
    """
    consulted = ["currency", "rag"]
    errors = [{"backend": "pack:crypto_protocols", "stage": "evaluate", "message": "boom"}]
    assert de.evidence_health(errors, consulted) == "degraded"


# --------------------------------------------------------------------------- #
# Unknown is a finding with a reason
# --------------------------------------------------------------------------- #

def test_unknown_reasons_are_carried_through_with_labels_never_flattened():
    gaps = [{
        "entity": "(no evidence anchors)",
        "reasons": ["no_pack_matched", "backends_failed"],
        "citation_basis": "retrieval_failed",
    }]
    reasons = de.unknown_reasons(gaps)
    codes = [r["code"] for r in reasons]
    assert codes == ["no_pack_matched", "backends_failed"]
    # Every reason renders as prose, so the page never shows a bare enum.
    assert all(r["label"] and r["label"] != r["code"] for r in reasons)
    # And the two are NOT merged into one "unknown" — they send you to two
    # different fixes.
    assert len(reasons) == 2


def test_the_three_citation_bases_stay_three():
    """An empty citation list on a gap has three causes (cef-rsv-03)."""
    gaps = [
        {"reasons": ["no_evidence"], "citation_basis": "mentioned_not_answered"},
        {"reasons": ["no_evidence"], "citation_basis": "no_retrieval_match"},
        {"reasons": ["backends_failed"], "citation_basis": "retrieval_failed"},
    ]
    codes = [b["code"] for b in de.citation_bases(gaps)]
    assert codes == ["mentioned_not_answered", "no_retrieval_match", "retrieval_failed"]


# --------------------------------------------------------------------------- #
# Axis 3 — the advisory opinion
# --------------------------------------------------------------------------- #

def test_advisory_off_is_not_consulted_never_no_opinion():
    """The shipped default.

    ``sme`` is deliberately absent from ``resolve.backends``, so a default
    deployment's advisory list is structurally always empty. Reporting that as
    "the expert had no concerns" would be a fabrication.
    """
    result = de.advisory_opinion("TLS 1.1", config={"advisory": {"enabled": False}})
    assert result["state"] == "not_consulted"
    assert result["items"] == []
    assert result["state"] != "no_opinion"


def test_a_failing_advisory_rung_is_unavailable_not_no_opinion(monkeypatch):
    """What this deployment returns today.

    ``ensure_sme`` needs one LLM call and the ``generative_intelligence``
    module budget is spent, so the rung errors. An outage is not an absence of
    opinion — ``search_sme``'s own docstring makes the same point.
    """
    import tools.cortex.api as cortex_api
    from tools.cortex.search_service import BackendResults

    monkeypatch.setattr(
        cortex_api, "search",
        lambda *a, **k: BackendResults([], errors=[{
            "backend": "sme", "stage": "ensure_sme",
            "message": "Module 'generative_intelligence' budget exceeded",
        }]),
    )
    result = de.advisory_opinion("TLS 1.1", config={"advisory": {"enabled": True}})
    assert result["state"] == "unavailable"
    assert result["items"] == []
    assert "budget exceeded" in result["errors"][0]["message"]


def test_a_silent_advisory_rung_is_no_opinion(monkeypatch):
    import tools.cortex.api as cortex_api
    from tools.cortex.search_service import BackendResults

    monkeypatch.setattr(cortex_api, "search", lambda *a, **k: BackendResults([], errors=[]))
    result = de.advisory_opinion("TLS 1.1", config={"advisory": {"enabled": True}})
    assert result["state"] == "no_opinion"


def test_an_answering_advisory_rung_is_an_opinion(monkeypatch):
    """The 'when present' half of the acceptance criterion.

    Built from a real ``CortexSearchResult`` marked the way ``search_sme``
    marks one, and accepted through the real ``is_advisory`` predicate — so
    this asserts the actual split, not a stand-in for it.
    """
    import tools.cortex.api as cortex_api
    from tools.cortex.schemas import Citation, CortexSearchResult
    from tools.cortex.search_service import BackendResults, is_advisory

    hit = CortexSearchResult(
        content="TLS 1.1 should be retired ahead of the enclave refresh.",
        backend="sme",
        citation=Citation(source_id="sme:network-security", source_type="sme"),
        metadata={"advisory": True, "role_id": "network-security"},
    )
    assert is_advisory(hit), "fixture must be what the platform calls advisory"

    monkeypatch.setattr(cortex_api, "search", lambda *a, **k: BackendResults([hit], errors=[]))
    result = de.advisory_opinion("TLS 1.1", config={"advisory": {"enabled": True}})
    assert result["state"] == "opinion"
    assert result["items"][0]["text"].startswith("TLS 1.1 should be retired")
    assert result["items"][0]["persona"] == "network-security"


def test_an_advisory_opinion_never_becomes_a_verdict(monkeypatch):
    """The invariant, asserted end to end.

    An expert insisting an entity is deprecated must not move a resolution that
    the packs called ``current``. ``resolver`` already excludes advisory hits;
    this asserts the SEAM in front of it does not put one back.
    """
    import tools.cortex.api as cortex_api
    from tools.cortex.schemas import Citation, CortexResolution, CortexSearchResult
    from tools.cortex.search_service import BackendResults

    resolution = CortexResolution(
        text="NIST SP 800-53 Rev 5: current (policy_refs).",
        entity="NIST SP 800-53 Rev 5",
        verdict="current",
        verdict_source="pack_evaluate",
    )
    opinion = CortexSearchResult(
        content="In my view this is thoroughly deprecated.",
        backend="sme", citation=Citation(source_id="sme:x"),
        metadata={"advisory": True},
    )
    monkeypatch.setattr(cortex_api, "resolve", lambda *a, **k: resolution)
    monkeypatch.setattr(cortex_api, "search", lambda *a, **k: BackendResults([opinion], errors=[]))

    view = de.resolve_finding(
        "NIST SP 800-53 Rev 5", advisory=True, persist=False,
        config={"cortex": {"enabled": True}, "advisory": {"enabled": True}},
    )
    assert view.state == "current"
    assert view.verdict == "current"
    assert view.advisory_state == "opinion"


# --------------------------------------------------------------------------- #
# The page context
# --------------------------------------------------------------------------- #

def test_a_finding_with_no_stored_answer_is_not_resolved(monkeypatch):
    """The page must never render "no answer" as "no problem"."""
    monkeypatch.setattr(de, "latest_resolutions", lambda *a, **k: {})
    ctx = de.attach_resolutions([
        {"source": "docmod.crypto_protocols", "entity": "TLS 1.1", "severity": "high"},
    ])
    resolution = ctx["findings"][0]["resolution"]
    assert resolution["state"] == "not_resolved"
    assert resolution["evidence_health"] == "unmeasured"
    assert resolution["advisory_state"] == "not_consulted"
    assert ctx["summary"]["current"] == 0
    assert ctx["summary"]["not_resolved"] == 1


def test_the_summary_counts_every_state_separately(monkeypatch):
    """No aggregate "N healthy". The counts this page exists to keep apart are
    `current` and `not_resolved`, and summing them is the defect."""
    views = {
        de._entity_key("TLS 1.1"): de.FindingView(
            entity="TLS 1.1", entity_key=de._entity_key("TLS 1.1"),
            state="superseded", evidence_health="degraded"),
        de._entity_key("Nothing"): de.FindingView(
            entity="Nothing", entity_key=de._entity_key("Nothing"),
            state="unknown", evidence_health="ok"),
    }
    monkeypatch.setattr(de, "latest_resolutions", lambda *a, **k: views)
    ctx = de.attach_resolutions([
        {"entity": "TLS 1.1"}, {"entity": "Nothing"}, {"entity": "Never asked"},
    ])
    assert ctx["summary"]["superseded"] == 1
    assert ctx["summary"]["unknown"] == 1
    assert ctx["summary"]["not_resolved"] == 1
    assert ctx["summary"]["current"] == 0
    assert ctx["evidence_health"] == {"ok": 1, "degraded": 1, "failed": 0, "unmeasured": 1}


def test_a_pack_citation_is_not_counted_as_corpus_corroboration():
    """``(no evidence anchors)`` resolves with exactly ONE citation and it is
    the pack's own rationale. Counting it as corroborating evidence would be
    this card's bug expressed as a number."""
    view = de.FindingView(
        entity="(no evidence anchors)",
        citations=[
            {"source_id": "dic_document:cortex.resolve", "source_type": "pack_evidence"},
            {"source_id": "ec-1", "source_type": "currency_assertion"},
        ],
    )
    assert view.citation_count == 2
    assert len(view.corpus_citations) == 1
    assert view.corpus_citations[0]["source_id"] == "ec-1"


# --------------------------------------------------------------------------- #
# Bounds are reported, never silent
# --------------------------------------------------------------------------- #

def test_a_capped_batch_names_what_it_deferred(monkeypatch):
    """A truncated sweep that reported only its successes would read as full
    coverage. ``skipped`` is a list of names, not a count."""
    monkeypatch.setattr(
        de, "resolve_finding",
        lambda entity, **kw: de.FindingView(entity=entity, state="current"),
    )
    result = de.resolve_findings(
        ["a", "b", "c", "d"], config={"cortex": {"enabled": True, "max_resolves_per_batch": 2}},
    )
    assert len(result["resolved"]) == 2
    assert result["skipped"] == ["c", "d"]
    assert result["cap"] == 2
    assert result["requested"] == 4


def test_a_batch_does_not_spend_its_budget_on_the_same_entity_twice(monkeypatch):
    monkeypatch.setattr(
        de, "resolve_finding",
        lambda entity, **kw: de.FindingView(entity=entity, state="current"),
    )
    result = de.resolve_findings(
        ["TLS 1.1", "tls 1.1", "MD5"], config={"cortex": {"enabled": True}},
    )
    assert result["requested"] == 2
    assert result["duplicates_dropped"] == 1


def test_the_toggle_off_yields_not_resolved_never_a_hidden_panel():
    """Off means "we did not ask", stated. It does not mean "nothing to see"."""
    view = de.resolve_finding("TLS 1.1", persist=False, config={"cortex": {"enabled": False}})
    assert view.state == "not_resolved"
    assert view.evidence_health == "unmeasured"
    assert "cortex.enabled is false" in view.advisory["reason"] or "enabled" in view.error


def test_an_unparseable_timestamp_is_stale_not_fresh():
    """A row we cannot date is one we cannot vouch for."""
    assert de._is_stale("not a date", 24) is True
    assert de._is_stale("", 24) is True


# --------------------------------------------------------------------------- #
# The template renders the distinctions the seam draws
# --------------------------------------------------------------------------- #

def _render(currency: dict) -> str:
    from jinja2 import DictLoader, Environment
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / "tools" / "dashboard" / "templates"
                / "document_intelligence" / "docdrift.html").read_text(encoding="utf-8")
    # Render the page body alone — base.html drags in the whole nav and is not
    # what this test is about.
    env = Environment(loader=DictLoader({
        "base.html": "{% block content %}{% endblock %}",
        "includes/iqe_query_widget.html": "",
        "document_intelligence/docdrift.html": template,
    }))
    return env.get_template("document_intelligence/docdrift.html").render(
        drift_events=[], regen_queue=[], ssp_fragments=[], topologies=[],
        baselines_saved=0, currency=currency,
    )


def _currency(*resolutions) -> dict:
    findings = [{"source": "docmod.test", "entity": r["entity"], "severity": "high",
                 "detected_at": "2026-08-18T00:00:00+00:00", "resolution": r}
                for r in resolutions]
    return {
        "findings": findings, "summary": {}, "evidence_health": {}, "advisory": {},
        "enabled": True, "advisory_enabled": False, "total": len(findings),
        "distinct_entities": len(findings), "batch_cap": 8, "stale_after_hours": 24,
    }


def _view(**kw) -> dict:
    return de.FindingView(entity=kw.pop("entity", "X"), **kw).to_dict()


def test_template_renders_current_unknown_and_not_checked_differently():
    """The acceptance criterion, asserted on the rendered HTML.

    Three findings that a naive page would draw identically must produce three
    different state classes and three different colours.
    """
    html = _render(_currency(
        _view(entity="NIST SP 800-53 Rev 5", state="current",
              verdict="current", verdict_source="pack_evaluate", evidence_health="ok"),
        _view(entity="(no evidence anchors)", state="unknown",
              verdict="unknown", verdict_source="pack_evaluate", evidence_health="degraded",
              gaps=[{"reasons": ["backends_failed"], "citation_basis": "retrieval_failed"}],
              backend_errors=[{"backend": "rag", "stage": "timeout", "message": "timed out"}],
              backends_consulted=["currency", "rag"]),
        _view(entity="Never asked", state="not_resolved", evidence_health="unmeasured"),
    ))
    for state in ("current", "unknown", "not_resolved"):
        assert f'dd-state-{state}' in html, f"{state} badge missing"

    # Three distinct foreground colours — the same colour twice would defeat the
    # entire point even with three distinct class names.
    colours = {"current": "#8fd08f", "unknown": "#c0a8f0", "not_resolved": "#8ba0bd"}
    assert len(set(colours.values())) == 3
    for colour in colours.values():
        assert colour in html

    # The unknown carries its REASON, and says in words that it is not a pass.
    assert "backends_failed" in html
    assert "Retrieval broke" in html
    assert "this is a finding, not a pass" in html.lower()

    # "Not checked" says so, in words, rather than being blank.
    assert "not a clean bill of health" in html.lower()


def test_template_renders_a_dead_backend_apart_from_both():
    """A backend error must be its own block, not a flavour of the verdict.

    The live case: `superseded` AND four dead backends at once. Both must be on
    screen, and the page must say the outage did not change the verdict.
    """
    html = _render(_currency(_view(
        entity="TLS 1.1", state="superseded", verdict="superseded",
        verdict_source="pack_evaluate", evidence_health="degraded",
        superseded_by="TLS 1.2 or higher (prefer TLS 1.3)", replacement_source="rulebook",
        backends_consulted=["currency", "rag", "dic", "graph", "kb"],
        backend_errors=[
            {"backend": "rag", "stage": "timeout", "message": "timed out after 10.0s"},
            {"backend": "dic", "stage": "timeout", "message": "timed out after 10.0s"},
        ],
        citations=[{"source_id": "rule:crypto-tls-02", "source_type": "pack_evidence",
                    "source_table": "crypto_protocols", "snippet": "TLS 1.1 is deprecated"}],
    )))
    assert "dd-state-superseded" in html          # the verdict survives...
    assert "dd-health-degraded" in html           # ...and the outage is its own badge
    assert "dd-backend-block" in html
    assert "infrastructure" in html.lower()
    assert "does not change the verdict" in html.lower()
    # And the verdict block is NOT rendered as unknown.
    assert "dd-state-unknown" not in html


def test_template_marks_the_advisory_block_and_keeps_it_subordinate():
    html = _render(_currency(_view(
        entity="TLS 1.1", state="superseded", verdict="superseded",
        verdict_source="pack_evaluate", evidence_health="ok",
        advisory_state="opinion",
        advisory={"state": "opinion", "reason": "an ACE domain expert answered",
                  "items": [{"text": "Retire it this quarter.", "persona": "network-security"}],
                  "errors": []},
    )))
    assert 'data-advisory-state="opinion"' in html
    assert "Advisory" in html
    assert "not evidence" in html
    assert "did not affect the verdict" in html
    assert "Retire it this quarter." in html
    # Subordinate by construction: indented, dashed, and smaller than the
    # verdict headline. Asserting the dashed border and the indent is what
    # stops a later edit promoting it to a peer of the verdict block.
    advisory_block = html[html.index('class="dd-advisory"'):]
    advisory_block = advisory_block[:advisory_block.index("</div>")]
    assert "dashed" in advisory_block
    assert "margin:0 0 4px 22px" in advisory_block


def test_template_states_not_consulted_rather_than_implying_no_concerns():
    html = _render(_currency(_view(
        entity="TLS 1.1", state="superseded", verdict="superseded",
        verdict_source="pack_evaluate", advisory_state="not_consulted",
        advisory={"state": "not_consulted", "items": [], "errors": [],
                  "reason": "the advisory rung was not requested for this finding"},
    )))
    assert 'data-advisory-state="not_consulted"' in html
    assert "Not consulted" in html
    assert 'NOT &#34;the expert had no concerns&#34;' in html or "no concerns" in html


def test_template_distinguishes_an_unavailable_advisory_from_a_silent_one():
    """The state this deployment actually produces today."""
    html = _render(_currency(_view(
        entity="TLS 1.1", state="superseded", verdict="superseded",
        verdict_source="pack_evaluate", advisory_state="unavailable",
        advisory={"state": "unavailable", "items": [],
                  "errors": [{"backend": "sme", "stage": "ensure_sme",
                              "message": "Module 'generative_intelligence' budget exceeded"}],
                  "reason": "the advisory rung was consulted and could not answer"},
    )))
    assert 'data-advisory-state="unavailable"' in html
    assert "Consulted — unavailable" in html
    assert "outage, not an absence of opinion" in html
    assert "budget exceeded" in html
    # And it is NOT the "no opinion" wording. Scoped to the rendered block: the
    # page's JS legitimately declares all four labels so it can repaint a row
    # after a live resolve, and finding one there proves nothing.
    block = html[html.index('class="dd-advisory"'):]
    block = block[:block.index("</div>", block.index("Consulted"))]
    assert "no opinion" not in block


def test_template_keeps_the_iqe_widget_and_the_drift_trigger():
    """The 8-component page gate: a modification must not drop what was there."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for tree in ("tools", "icdev/tools"):
        template = (root / tree / "dashboard" / "templates"
                    / "document_intelligence" / "docdrift.html").read_text(encoding="utf-8")
        assert 'include "includes/iqe_query_widget.html"' in template
        assert 'iqe_canvas = "dic"' in template
        assert "docdrift-run-drift" in template
        assert "docdrift-save-baseline" in template


def test_the_template_is_mirrored_byte_for_byte():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    rel = Path("dashboard/templates/document_intelligence/docdrift.html")
    assert (root / "tools" / rel).read_bytes() == (root / "icdev/tools" / rel).read_bytes()


def test_the_module_is_mirrored_byte_for_byte():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    rel = Path("document_intelligence/docdrift_evidence.py")
    assert (root / "tools" / rel).read_bytes() == (root / "icdev/tools" / rel).read_bytes()


# --------------------------------------------------------------------------- #
# Regressions found by the live Playwright run (cef-ui-01)
# --------------------------------------------------------------------------- #

def test_a_per_call_advisory_optin_is_honoured_when_the_config_default_is_off(monkeypatch):
    """The page's checkbox must actually consult the rung.

    Found live: ``resolve_finding(advisory=True)`` decided to ask, then
    delegated to ``advisory_opinion``, which re-read ``advisory.enabled``,
    found it false, and returned ``not_consulted``. The checkbox looked like a
    control and changed nothing — a declared capability with no consumer,
    wearing a tick box.
    """
    import tools.cortex.api as cortex_api
    from tools.cortex.search_service import BackendResults

    asked = []

    def _search(*a, **k):
        asked.append(k.get("backends"))
        return BackendResults([], errors=[{"backend": "sme", "stage": "x", "message": "y"}])

    monkeypatch.setattr(cortex_api, "search", _search)
    result = de.advisory_opinion(
        "TLS 1.1", consult=True,
        config={"advisory": {"enabled": False}},   # config default OFF
    )
    assert asked == [["sme"]], "the rung was never consulted"
    assert result["state"] == "unavailable"


def test_a_deployment_can_refuse_a_request_level_advisory_override(monkeypatch):
    """`enabled: false` is a default; `allow_request_override: false` is a ban.

    An air-gapped install has to be able to guarantee no model call reaches
    this page whatever a request body says — and the refusal must NAME itself
    rather than looking like "the expert had nothing to say".
    """
    import tools.cortex.api as cortex_api

    def _boom(*a, **k):
        raise AssertionError("the advisory rung must not be consulted")

    monkeypatch.setattr(cortex_api, "search", _boom)
    result = de.advisory_opinion(
        "TLS 1.1", consult=True,
        config={"advisory": {"enabled": False, "allow_request_override": False}},
    )
    assert result["state"] == "not_consulted"
    assert "allow_request_override" in result["reason"]


def test_consult_false_never_asks_even_when_the_config_says_yes(monkeypatch):
    import tools.cortex.api as cortex_api

    def _boom(*a, **k):
        raise AssertionError("the advisory rung must not be consulted")

    monkeypatch.setattr(cortex_api, "search", _boom)
    result = de.advisory_opinion(
        "TLS 1.1", consult=False, config={"advisory": {"enabled": True}})
    assert result["state"] == "not_consulted"


def test_an_advisory_source_id_is_read_off_the_nested_citation():
    """``CortexSearchResult.to_dict()`` nests ``source_id`` under ``citation``.

    Found live: reading it from the top level yielded "" silently, so an
    advisory opinion rendered with no attribution at all.
    """
    from tools.cortex.schemas import Citation, CortexSearchResult

    hit = CortexSearchResult(
        content="opinion text", backend="sme",
        citation=Citation(source_id="sme:network-security"),
        metadata={"advisory": True, "role_id": "network-security"},
    )
    item = de._advisory_item(hit)
    assert item["source_id"] == "sme:network-security"
    assert item["persona"] == "network-security"


def test_each_finding_row_is_paired_with_its_own_detail_row():
    """One entity can occupy several drift rows — TLS 1.1 occupies three live.

    Found live: the template keyed detail panels by ENTITY, so `querySelector`
    matched the first duplicate only. Row 2's "detail" button toggled row 1's
    panel, and a repaint left rows 2 and 3 showing a stale answer beside a
    fresh badge — the page contradicting itself about one entity.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / "tools" / "dashboard" / "templates"
                / "document_intelligence" / "docdrift.html").read_text(encoding="utf-8")
    assert 'data-finding-idx="{{ loop.index0 }}"' in template
    assert 'class="dd-detail-btn" data-finding-idx=' in template
    # And nothing looks a detail panel up by entity any more.
    assert "data-entity-detail=\"' +" not in template
    assert "'[data-entity-detail=\"'" not in template
