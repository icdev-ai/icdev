# CUI // SP-CTI
"""Deciding a change without reloading the page (dwr-ws-03).

THE DEFECT. Every decision on a document ended in ``window.location.reload()``
(doc_detail.html) or in ``load()`` -- a full change-set refetch that rebuilds
``railEl.innerHTML`` wholesale (workspace.html, dwr-ws-02). Both throw away
scroll position, the open editor, the selection and every per-card result
message the reviewer just read. Neither is a decision applied in place.

WHAT THIS MODULE PINS, and each one is a thing that would otherwise fail GREEN:

  the counts        ``resolved_pct`` is ``None`` -- never 0.0 and never 100.0 --
                    over an empty denominator, and ``unmeasured`` is never
                    folded into a measured zero. ``args/perfect_score_gate.yaml``
                    is ratcheted to 0, so a ``100.0`` fallback here is a census
                    breach as well as a lie.
  reviewed vs
  resolved          a SUPERSEDED change was retired by a MECHANISM when its
                    anchor went stale. Counting it as reviewed overstates human
                    review coverage -- dwr-ev-03's rule ("``decided_by`` names
                    the MECHANISM ... so a retirement can never be read as
                    somebody's accept-or-reject") one surface up.
  the splice        the accept route returns the section content it RE-READ
                    after the write. The page renders those bytes; it does not
                    predict them. A client that recomputed
                    ``content[:start] + replacement + content[end:]`` would be a
                    second copy of a computation the server already did and
                    confirmed.
  the refusal       a 409 ``anchor_stale`` is a REFUSAL, not an error: the
                    document moved under the reviewer. The page must say so, and
                    must not offer a re-anchor of the proposal itself -- the
                    accept door SUPERSEDES a stale proposal, ``redraft`` then
                    refuses it ``already_decided``, and no re-anchor door exists
                    on this tree (``resolve_passage`` is dwr-anchor-04, still
                    open). Offering one would be a button that cannot work.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tools.document_intelligence import change_set

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "tools/dashboard/templates/document_intelligence/workspace.html"
MIRROR = REPO / "icdev/tools/dashboard/templates/document_intelligence/workspace.html"
BLUEPRINT = REPO / "tools/document_intelligence/blueprint.py"


# ── decision_progress: the denominator, and the two zeroes it refuses ─────────

def _rows(**by_status):
    """A fake store: ``get_pending_suggestions(status=...)`` answers per status."""
    def fake(collection_id=None, canvas_source=None, status="pending"):
        return list(by_status.get(status, []))
    return fake


def _sugg(sid, doc_id="doc-1"):
    return {"suggestion_id": sid, "doc_id": doc_id}


def test_resolved_pct_is_none_over_an_empty_denominator(monkeypatch):
    """No proposal was ever made for this document. That is not 100% resolved
    (nothing was reviewed) and it is not 0% resolved (nothing is outstanding)."""
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(),
    )
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["state"] == "no_changes"
    assert p["total"] == 0
    assert p["resolved_pct"] is None
    assert p["reviewed_pct"] is None


def test_a_measured_zero_is_a_real_zero(monkeypatch):
    """Three proposals, none decided. THAT is 0.0% -- and it must still render
    as a real measurement, not be merged with the empty-denominator None."""
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(pending=[_sugg("a"), _sugg("b"), _sugg("c")]),
    )
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["state"] == "changes"
    assert (p["total"], p["resolved"], p["pending"]) == (3, 0, 3)
    assert p["resolved_pct"] == 0.0


def test_an_unreadable_store_is_unmeasured_with_none_counts_never_zero(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("store down")
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions", boom)
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["state"] == "unmeasured"
    assert p["total"] is None and p["resolved"] is None and p["pending"] is None
    assert p["resolved_pct"] is None and p["reviewed_pct"] is None


def test_a_partial_read_failure_is_unmeasured_not_a_smaller_total(monkeypatch):
    """One status readable and one not is NOT a board with fewer changes on it."""
    def half(collection_id=None, canvas_source=None, status="pending"):
        if status == "rejected":
            raise RuntimeError("rejected read failed")
        return [_sugg("a")] if status == "pending" else []
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions", half)
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["state"] == "unmeasured"
    assert p["total"] is None
    assert "rejected" in p["unreadable_statuses"]


def test_superseded_is_resolved_but_never_reviewed(monkeypatch):
    """A machine retired it when the anchor went stale. Nobody decided anything."""
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(pending=[_sugg("a")], accepted=[_sugg("b")],
              superseded=[_sugg("c"), _sugg("d")]),
    )
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["total"] == 4
    assert p["resolved"] == 3          # accepted + superseded: off the queue
    assert p["reviewed"] == 1          # accepted only: a human decided it
    assert p["superseded"] == 2
    assert p["resolved_pct"] == 75.0
    assert p["reviewed_pct"] == 25.0


def test_a_rate_that_is_not_one_hundred_never_rounds_to_one_hundred(monkeypatch):
    """1999 of 2000 is 99.95%, which rounds to 100.0 at one decimal place -- a
    display reading a perfect score for an imperfect one. 100.0 is reserved for
    a rate that IS 100, so the rate FLOORS."""
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(pending=[_sugg("p")],
              accepted=[_sugg("a%d" % i) for i in range(1999)]),
    )
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["total"] == 2000
    assert p["resolved_pct"] == 99.9
    assert p["resolved_pct"] != 100.0


def test_one_hundred_is_reachable_when_it_is_true(monkeypatch):
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(accepted=[_sugg("a")], rejected=[_sugg("b")]),
    )
    p = change_set.decision_progress(doc_id="doc-1")
    assert p["resolved_pct"] == 100.0 and p["reviewed_pct"] == 100.0
    assert p["pending"] == 0


def test_progress_is_scoped_to_the_document(monkeypatch):
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(pending=[_sugg("a", "doc-1"), _sugg("b", "doc-2")],
              accepted=[_sugg("c", "doc-2")]),
    )
    p = change_set.decision_progress(doc_id="doc-1")
    assert (p["total"], p["pending"], p["resolved"]) == (1, 1, 0)


def test_build_change_set_carries_the_progress_block(monkeypatch):
    """ONE assembler: the rail cannot describe a count the API does not."""
    monkeypatch.setattr(
        "tools.document_intelligence.suggestion_store.get_pending_suggestions",
        _rows(pending=[_sugg("a")], accepted=[_sugg("b")]),
    )
    out = change_set.build_change_set(doc_id="doc-1", verify_anchors=False)
    assert out["progress"]["total"] == 2
    assert out["progress"]["resolved_pct"] == 50.0


def test_no_perfect_score_fallback_in_this_module():
    """The rem-hyg-13 shape, asserted structurally over the source: a ratio with
    a 100.0 or 0.0 fallback arm. args/perfect_score_gate.yaml is ratcheted to 0,
    so there is no grandfathering available here."""
    src = (REPO / "tools/document_intelligence/change_set.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.IfExp):
            for arm in (node.body, node.orelse):
                if isinstance(arm, ast.Constant) and arm.value in (100.0, 0.0):
                    pytest.fail("a percentage with a %r fallback arm" % (arm.value,))


# ── The accept door hands back what it WROTE ──────────────────────────────────

def _func(src: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("no function %s" % name)


def _final_jsonify(fn: ast.FunctionDef) -> ast.Dict:
    """The dict of the LAST ``return jsonify({...})`` in a function body."""
    found = None
    for node in ast.walk(fn):
        if (isinstance(node, ast.Return) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "jsonify"
                and node.value.args and isinstance(node.value.args[0], ast.Dict)):
            found = node.value.args[0]
    assert found is not None, "no return jsonify({...}) in %s" % fn.name
    return found


def test_accept_returns_the_section_content_it_re_read():
    """``after_content`` is the CONFIRMED re-read, and it is what the response
    carries -- not ``new_content``, the value the route predicted before the
    write. The page renders the bytes the document holds."""
    fn = _func(BLUEPRINT.read_text(encoding="utf-8"), "api_suggestion_accept")
    ret = _final_jsonify(fn)
    keys = [k.value for k in ret.keys if isinstance(k, ast.Constant)]
    assert "section_content" in keys, "the accept 200 must carry the section it wrote"
    value = ret.values[keys.index("section_content")]
    assert isinstance(value, ast.Name) and value.id == "after_content", (
        "section_content must be the CONFIRMED re-read (after_content), never the "
        "predicted splice (new_content)")


def test_a_crowdsourced_suggestion_records_the_documents_id():
    """FOUND ON THE WAY, and it made the workspace rail blind to a whole
    canvas_source: ``/api/sections/<id>/suggest`` called ``create_suggestion``
    with no ``doc_id``, so the row carried an empty one -- and
    ``build_change_set`` drops every row whose ``doc_id`` is not the document's.
    A crowdsourced proposal could not appear on the page built to review it.

    The id is DERIVED from the section, never taken from the request body: which
    document a section belongs to is not the caller's to assert."""
    src = BLUEPRINT.read_text(encoding="utf-8")
    fn = _func(src, "api_section_suggest")
    call = None
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "create_suggestion"):
            call = node
    assert call is not None, "the suggest route must go through create_suggestion"
    kw = {k.arg: k.value for k in call.keywords}
    assert "doc_id" in kw, "a crowdsourced suggestion must record its document"
    assert isinstance(kw["doc_id"], ast.Name) and kw["doc_id"].id == "section_doc_id", (
        "doc_id must come from the SECTION row, never from the request")
    # `section_doc_id` is bound from the dic_sections read and from nowhere
    # else, so there is no path by which a request body could supply it.
    binds = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
             and any(getattr(t, "id", "") == "section_doc_id" for t in n.targets)]
    assert binds, "section_doc_id must be assigned in the route"
    for node in binds:
        assert "request" not in ast.dump(node.value), (
            "section_doc_id must never be read from the request")


def test_a_workspace_body_read_route_exists_and_has_no_post_sibling():
    """"Re-read the document" needs a JSON door onto the SAME library function
    the page render calls, or the re-read could describe a different document
    from the one first rendered. It is a GET: this route decides nothing."""
    src = BLUEPRINT.read_text(encoding="utf-8")
    assert "/api/workspace/body" in src
    for methods in re.findall(
            r'route\(\s*"/api/workspace/body"[^)]*methods=\[([^\]]*)\]', src):
        assert "POST" not in methods, "the body re-read must have no POST sibling"


# ── The page: in place, and honest about what it cannot offer ─────────────────

def _template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _executable(src: str) -> str:
    """The template with ``//`` and ``{# #}`` comments removed.

    NAMING the defect is not committing it. A scan of the raw bytes would flag
    the comment that EXPLAINS why this page must never reload -- the same trap
    ``perfect_score_census`` disarmed by parsing to an AST, whose first
    candidate entry was the previous fix's own explanation of itself.
    """
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_no_page_reload_anywhere_on_the_workspace(path):
    """The card's DONE criterion, asserted structurally: three decisions in one
    session with no reload is only provable if the page cannot reload at all."""
    src = _executable(_template(path))
    assert "location.reload" not in src
    assert "location.href" not in src
    assert "location.assign" not in src


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_the_comment_stripper_would_have_caught_a_real_reload(path):
    """A control on the test above. Without it, a predicate that only ever
    passes proves the page is clean and proves nothing about the predicate."""
    src = _template(path) + "\n  window.location.reload();\n"
    assert "location.reload" in _executable(src)


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_a_decision_applies_to_one_card_and_does_not_rebuild_the_rail(path):
    """dwr-ws-02 ended every act in ``load()``, which sets
    ``railEl.innerHTML = ...`` and destroys every other card's DOM -- the open
    editor, the selection, the result message just read."""
    src = _template(path)
    assert "function applyDecision(" in src, "a decision must apply to one card"
    body = src[src.index("function act("):]
    end = body.find("\n  // ", 10)
    body = body[:end] if end > 0 else body
    assert "return load()" not in body and " load();" not in body, (
        "act() must not fall back to a full rebuild of the rail")


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_the_page_never_predicts_the_spliced_content(path):
    """The section text comes from the accept response's ``section_content``. A
    client-side ``slice(0, start) + replacement + slice(end)`` would be a second
    copy of a splice the server already performed and CONFIRMED, and the two
    would disagree the moment anything else touched the section."""
    src = _template(path)
    assert "section_content" in src
    assert not re.search(r"slice\(\s*0\s*,\s*\w*[Ss]tart\s*\)\s*\+", src), (
        "the page must not recompute the splice")


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_anchor_stale_is_a_refusal_not_an_error(path):
    src = _template(path)
    assert "anchor_stale" in src
    assert "moved under" in src, "the page must say the document moved, in words"
    assert "expected_text" in src and "found_text" in src, (
        "the refusal carries both sides; showing neither is a toast")


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_a_refusal_re_reads_the_counts_too(path):
    """A stale accept SUPERSEDES the proposal: it leaves the pending queue
    without a human deciding it, so `resolved` rises while `reviewed` does not.
    FOUND ON THE LIVE RUN: the refusal rendered correctly beside "0 of 3 changes
    resolved" on a board that by then had one resolved."""
    src = _template(path)
    body = src[src.index("function refuseDecision("):]
    body = body[:body.index("\n  // Re-anchor the VIEW")]
    assert "reconcile(" in body, "a refusal must re-read the counts it changed"


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_the_page_offers_no_re_anchor_of_a_superseded_proposal(path):
    """There is no door. The accept route SUPERSEDES a stale proposal and
    ``redraft`` then refuses it ``already_decided``; ``resolve_passage`` is
    dwr-anchor-04 and is not on this tree. What the page offers is a re-read of
    the DOCUMENT -- re-anchoring the VIEW, which is a thing that works."""
    src = _template(path)
    assert "resolve_passage" not in src
    assert "reanchorView" in src


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_a_decided_change_is_never_marked_in_the_document(path):
    """FOUND ON THE LIVE RUN of this card: after an accept, the section rendered
    the NEW text with the OLD span still highlighted. A decided change's offsets
    were measured before the splice, so they address text that has moved -- and
    there is nothing left to apply, so a mark invites a decision that cannot be
    made. ``anchorsFor`` is the one place marks are chosen, so the rule is
    asserted there."""
    src = _template(path)
    body = src[src.index("function anchorsFor("):]
    body = body[:body.index("\n  }") + 4]
    assert "c.status" in body and "'pending'" in body, (
        "anchorsFor must exclude a change that has already been decided")
    # A REJECT writes no content, so nothing else would trigger a redraw of the
    # section whose mark must now come off. Both decision paths redraw it.
    decide = src[src.index("function applyDecision("):]
    decide = decide[:decide.index("\n  // A 409")]
    assert "renderSection(sectionEl(section))" in decide, (
        "a decision that writes nothing must still redraw its section")


@pytest.mark.parametrize("path", [TEMPLATE, MIRROR], ids=["tools", "icdev"])
def test_progress_counts_render_none_as_not_measured(path):
    src = _template(path)
    assert "resolved_pct" in src
    assert not re.search(r"resolved_pct\s*\|\|\s*0", src), (
        "`x || 0` puts a fabricated 0% straight back")


def test_the_two_templates_are_byte_identical():
    assert _template(TEMPLATE) == _template(MIRROR)
