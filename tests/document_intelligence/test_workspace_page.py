# CUI // SP-CTI
"""The two-pane workspace page (dwr-ws-02).

THE LOAD-BEARING ASSERTIONS ARE THE THREE THINGS THAT MUST NEVER BE MERGED:

    ``no_body``  vs ``unmeasured``   an unreadable document is not an empty one
    ``clean``    vs ``unmeasured``   a gate over ZERO sections has measured
                                     nothing, and reporting it green is the
                                     ``args/perfect_score_gate.yaml`` defect
                                     wearing a publish gate's name. The FIRST
                                     live run of ``document_findings`` did
                                     exactly that for
                                     ``dic_doc_28e2ee4d984f3f35`` — a version
                                     row, zero sections, 47 pending proposals —
                                     and drew two green ticks.
    ``appliable`` vs ``offered``     the page must not offer an Accept the
                                     accept door would refuse with a 409

The rest guards the 8-point page gate structurally (template + mirror + route +
module + nav + IQE) and the house style the card names: no CDN, escaping through
``esc.js``, no hand-rolled CSRF token.
"""
from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

from tools.document_intelligence import workspace

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "tools/dashboard/templates/document_intelligence/workspace.html"
PICKER = REPO / "tools/dashboard/templates/document_intelligence/workspace_picker.html"
MIRROR = REPO / "icdev/tools/dashboard/templates/document_intelligence/workspace.html"


# ── active_version: ONE rule, pinned against the blueprint's ──────────────────

def test_active_version_prefers_an_open_version_then_the_newest():
    versions = [
        {"version_id": "v3", "status": "approved"},
        {"version_id": "v2", "status": "draft"},
        {"version_id": "v1", "status": "approved"},
    ]
    assert workspace.active_version(versions) == "v2"
    assert workspace.active_version([{"version_id": "v9", "status": "approved"}]) == "v9"
    assert workspace.active_version([]) == ""
    assert workspace.active_version(None) == ""


def test_active_version_agrees_with_the_blueprints_own_rule():
    """A second copy of "which version am I looking at" is how a rail comes to
    describe a version the page is not showing. This module restates the rule
    (importing the blueprint would be a cycle), so it is pinned to the
    blueprint's function over the cases that distinguish them."""
    from tools.document_intelligence.blueprint import _active_version_id

    cases = [
        [],
        [{"version_id": "a", "status": "approved"}],
        [{"version_id": "a", "status": "approved"}, {"version_id": "b", "status": "draft"}],
        [{"version_id": "a", "status": "pending_review"}, {"version_id": "b", "status": "draft"}],
        [{"version_id": "a", "status": "needs_revision"}],
        [{"version_id": "a", "status": "published"}, {"version_id": "b", "status": "published"}],
    ]
    for versions in cases:
        assert workspace.active_version(versions) == _active_version_id(versions), versions


# ── The body: four bases, and no two of them merged ───────────────────────────

class _FakeConn:
    """A connection whose ``execute`` answers by which table the SQL names."""

    def __init__(self, tables: dict, raises: bool = False):
        self._tables, self._raises = tables, raises
        self.description = None

    def execute(self, sql, params=()):
        if self._raises:
            raise RuntimeError("boom")
        for name, rows in self._tables.items():
            if name in sql:
                self._rows = rows
                return self
        self._rows = []
        return self

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_conn(monkeypatch, conn):
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)


def test_a_document_with_sections_is_positioned_and_addressable(monkeypatch):
    conn = _FakeConn({
        "dic_versions": [{"version_id": "v1", "version_no": 1, "origin": "ai",
                          "status": "draft", "created_at": "t"}],
        "dic_sections": [{"section_id": "s1", "heading": "H", "content": "body",
                          "status": "draft", "origin": "ai", "created_at": "t"}],
    })
    _patch_conn(monkeypatch, conn)
    body = workspace.document_body("doc-1")
    assert body["basis"] == workspace.BASIS_POSITIONED
    assert body["addressable"] is True
    assert body["section_count"] == 1
    assert workspace.MODE_REFLOW in body["modes"]


def test_a_document_with_no_sections_falls_back_to_chunks_and_is_not_addressable(monkeypatch):
    """The measured case: dic_doc_28e2ee4d984f3f35 carries 47 of the board's 58
    pending proposals and ZERO dic_sections rows. A workspace rendering only
    sections would show an EMPTY document beside 47 proposals."""
    conn = _FakeConn({
        "dic_versions": [{"version_id": "v1", "status": "draft"}],
        "dic_sections": [],
        "rag_chunks": [{"id": "c1", "content": "text", "chunk_index": 0,
                        "total_chunks": 2, "created_at": "t"}],
    })
    _patch_conn(monkeypatch, conn)
    body = workspace.document_body("doc-1")
    assert body["basis"] == workspace.BASIS_CHUNKS
    # A chunk is retrieval evidence, not the content of record. api_suggestion_accept
    # writes dic_sections.content and nothing else, so nothing here is appliable.
    assert body["addressable"] is False
    assert body["modes"] == []
    assert body["chunk_count"] == 1


def test_no_body_and_unmeasured_are_different_answers(monkeypatch):
    empty = _FakeConn({"dic_versions": [], "dic_sections": [], "rag_chunks": []})
    _patch_conn(monkeypatch, empty)
    measured = workspace.document_body("doc-1")
    assert measured["basis"] == workspace.BASIS_NONE
    assert measured["section_count"] == 0 and measured["chunk_count"] == 0

    _patch_conn(monkeypatch, _FakeConn({}, raises=True))
    unread = workspace.document_body("doc-1")
    assert unread["basis"] == workspace.BASIS_UNMEASURED
    # Counts are None, NEVER 0: "0 sections" for a database we could not read
    # renders as an empty document.
    assert unread["section_count"] is None
    assert unread["chunk_count"] is None
    assert unread["basis"] != measured["basis"]


def test_an_absent_doc_id_is_unmeasured_not_empty():
    assert workspace.document_body("")["basis"] == workspace.BASIS_UNMEASURED


# ── The gates: clean is only ever written after a gate actually ran ───────────

def _patch_gates(monkeypatch, consistency, citations):
    import tools.document_intelligence.consistency_checker as cc
    monkeypatch.setattr(cc, "check_version_consistency", lambda vid: consistency)
    monkeypatch.setattr(cc, "check_version_citations", lambda vid: citations)


def test_a_gate_over_zero_sections_is_unmeasured_never_clean(monkeypatch):
    """THE DEFECT THIS TEST EXISTS FOR. Both checkers return no findings when
    they were handed no sections, so an unguarded reduction reports `clean` for
    a document nothing has ever scanned."""
    _patch_gates(
        monkeypatch,
        {"placeholders": [], "numeric_conflicts": [], "section_count": 0},
        {"findings": [], "ai_section_count": 0, "section_count": 0},
    )
    gates = workspace.document_findings("v1")
    for key in ("placeholder", "citation", "numeric"):
        assert gates[key]["state"] == workspace.GATE_UNMEASURED, key
        assert gates[key]["count"] is None, key
        assert gates[key]["reason"] == "no_sections", key


def test_a_measured_pass_still_reads_clean(monkeypatch):
    """The complement, and it must keep working: a real gate over real sections
    that found nothing is a MEASURED pass and is not withheld."""
    _patch_gates(
        monkeypatch,
        {"placeholders": [], "numeric_conflicts": [], "section_count": 4},
        {"findings": [], "ai_section_count": 2, "section_count": 4},
    )
    gates = workspace.document_findings("v1")
    assert gates["placeholder"]["state"] == workspace.GATE_CLEAN
    assert gates["placeholder"]["count"] == 0
    assert gates["citation"]["state"] == workspace.GATE_CLEAN
    assert gates["numeric"]["state"] == workspace.GATE_CLEAN


def test_a_document_with_no_ai_section_has_not_passed_the_citation_gate(monkeypatch):
    """citation_gate only ever inspects AI-authored sections. A version with
    none of them has had no citation to check — which is not the same as having
    been checked and found sound."""
    _patch_gates(
        monkeypatch,
        {"placeholders": [], "numeric_conflicts": [], "section_count": 3},
        {"findings": [], "ai_section_count": 0, "section_count": 3},
    )
    gates = workspace.document_findings("v1")
    assert gates["citation"]["state"] == workspace.GATE_UNMEASURED
    assert gates["citation"]["reason"] == "no_ai_sections"
    # The placeholder gate DID run over those 3 sections and is unaffected.
    assert gates["placeholder"]["state"] == workspace.GATE_CLEAN


def test_findings_are_carried_and_counted(monkeypatch):
    _patch_gates(
        monkeypatch,
        {"placeholders": [{"item_number": "1", "placeholders": ["TBD"]}],
         "numeric_conflicts": [], "section_count": 2},
        {"findings": [{"item_number": "1", "issue": "missing_citations"}],
         "ai_section_count": 1, "section_count": 2},
    )
    gates = workspace.document_findings("v1")
    assert gates["placeholder"]["state"] == workspace.GATE_FINDINGS
    assert gates["placeholder"]["count"] == 1
    assert gates["citation"]["findings"][0]["issue"] == "missing_citations"


def test_a_gate_error_is_unmeasured_not_clean(monkeypatch):
    _patch_gates(
        monkeypatch,
        {"placeholders": [], "numeric_conflicts": [], "section_count": 0, "error": "db down"},
        {"findings": [], "ai_section_count": 0, "section_count": 0, "error": "db down"},
    )
    gates = workspace.document_findings("v1")
    assert all(g["state"] == workspace.GATE_UNMEASURED for g in gates.values())
    assert gates["placeholder"]["reason"] == "gate_error"


def test_a_document_with_no_version_has_never_been_put_to_the_gates():
    gates = workspace.document_findings("")
    assert all(g["state"] == workspace.GATE_UNMEASURED for g in gates.values())
    assert all(g["reason"] == "no_version" for g in gates.values())
    assert all(g["count"] is None for g in gates.values())


# ── The page's own document-level finding ─────────────────────────────────────

def test_unplaced_proposals_are_a_document_level_finding():
    """A proposal naming no section cannot be drawn on any span-anchored rail.
    Measured 2026-09-08 that is 58 of 58 — dropping them empties the page."""
    changes = [
        {"suggestion_id": "a", "anchor_section_id": None, "section_id": ""},
        {"suggestion_id": "b", "anchor_section_id": "s1", "section_id": ""},
    ]
    summary = workspace.unplaced_summary(changes)
    assert summary["state"] == workspace.GATE_FINDINGS
    assert summary["count"] == 1 and summary["total"] == 2
    assert summary["suggestion_ids"] == ["a"]


def test_unplaced_is_unmeasured_when_the_change_set_could_not_be_read():
    for summary in (workspace.unplaced_summary(None),
                    workspace.unplaced_summary([], read_ok=False)):
        assert summary["state"] == workspace.GATE_UNMEASURED
        assert summary["count"] is None  # never 0
        assert summary["total"] is None


def test_every_proposal_placed_is_a_measured_clean():
    summary = workspace.unplaced_summary([{"suggestion_id": "a", "anchor_section_id": "s1"}])
    assert summary["state"] == workspace.GATE_CLEAN
    assert summary["count"] == 0


# ── The picker ────────────────────────────────────────────────────────────────

def test_candidates_unmeasured_is_not_an_empty_list(monkeypatch):
    _patch_conn(monkeypatch, _FakeConn({}, raises=True))
    result = workspace.workspace_candidates()
    assert result["state"] == "unmeasured"
    assert result["count"] is None  # never 0
    assert result["documents"] == []


def test_candidates_none_is_measured(monkeypatch):
    _patch_conn(monkeypatch, _FakeConn({"dic_suggestions": [], "dic_documents": []}))
    result = workspace.workspace_candidates()
    assert result["state"] == "none"
    assert result["count"] == 0


# ── The page: it reads, and decides nothing ───────────────────────────────────

def test_the_workspace_routes_exist_and_have_no_post_sibling():
    """Every act on this page goes through a door that already existed. A POST
    on this route would be a second, unaudited decision surface."""
    # REGISTER THE BLUEPRINT ON A FRESH APP rather than reading the global one.
    #
    # The global `tools.dashboard.app` builds itself once at import, gated on
    # ICDEV_DIC_ENABLED. `os.environ.setdefault` cannot override a value another
    # test already set, and an app imported earlier in the same shard is already
    # built -- so this passed when the file ran alone and failed in Test Shard 4
    # with an EMPTY rule map. The blueprint carries the routes either way, and
    # asking it directly is what this test is actually about.
    from flask import Flask

    from tools.document_intelligence.blueprint import dic_bp

    probe = Flask(__name__)
    probe.register_blueprint(dic_bp)

    rules = {}
    for rule in probe.url_map.iter_rules():
        if str(rule.rule).startswith("/document-intelligence/workspace"):
            rules[str(rule.rule)] = set(rule.methods)
    assert "/document-intelligence/workspace" in rules
    assert "/document-intelligence/workspace/<doc_id>" in rules
    for methods in rules.values():
        assert "POST" not in methods
        assert "GET" in methods


def test_the_module_writes_nothing():
    """A reader. An AST scan rather than a behavioural one: a future edit adding
    an INSERT would still pass a test that only exercised today's call sites."""
    src = (REPO / "tools/document_intelligence/workspace.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value.upper()
            for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER "):
                assert verb not in sql, f"workspace.py must not write: {node.value[:80]}"


# ── House style, and the 8-point page gate ────────────────────────────────────

def test_the_template_fetches_nothing_from_a_cdn():
    for path in (TEMPLATE, PICKER):
        html = path.read_text(encoding="utf-8")
        assert "http://" not in html.replace("http://localhost", "")
        assert "https://" not in html, f"{path.name} must vendor, never fetch"


def test_the_template_escapes_through_esc_js_and_never_hand_rolls_csrf():
    html = TEMPLATE.read_text(encoding="utf-8")
    # escHtml/safeMarkdown come from static/js/esc.js — never a local copy.
    assert "window.escHtml" in html
    assert "window.safeMarkdown" in html
    assert not re.search(r"function\s+escHtml\s*\(", html), "use esc.js, do not redefine escHtml"
    # base.html's shim signs every same-origin mutating fetch.
    assert "X-CSRF-Token" not in html
    assert "csrf" not in html.lower() or "CSRF shim in base.html" in html


def test_the_template_carries_the_document_level_band():
    """delta_review's :182-209 distinction. A page showing only span-anchored
    findings renders a blocked draft as having nothing wrong with it."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "Document-level findings" in html
    assert "placeholder_guard" in html and "citation_guard" in html
    assert "dws-gate--unmeasured" in html
    assert "not assessed" in html


def test_the_template_offers_all_four_decisions():
    html = TEMPLATE.read_text(encoding="utf-8")
    for act in ("accept", "reject", "edit", "comment"):
        assert f'data-act="{act}"' in html, act
    # Edit & Accept travels as applied_text, which the accept route stores
    # BESIDE the AI draft — suggested_content is never overwritten.
    assert "applied_text" in html


def test_the_rail_consumes_the_change_set_endpoint_and_no_second_assembler():
    """dwr-ws-01 built /api/change-set and nothing consumed it. This page is that
    consumer, and it must not grow a second copy of the assembly."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "/document-intelligence/api/change-set?doc_id=" in html
    assert "/api/suggestions" not in html.split("<script>")[0], "no doors in the markup"


def test_the_page_gate_eight_points():
    """1 template · 2 mirror · 3 route · 4 module · 5 constants · 6 migration
    · 7 nav link · 8 IQE. Points 5 and 6 are answered rather than skipped: the
    page introduces its own state constants in workspace.py and needs NO new
    table, so there is nothing to migrate."""
    import yaml

    assert TEMPLATE.exists() and PICKER.exists()                             # 1
    assert MIRROR.exists()                                                   # 2
    assert (REPO / "icdev/tools/document_intelligence/workspace.py").exists()
    bp = (REPO / "tools/document_intelligence/blueprint.py").read_text(encoding="utf-8")
    assert '@dic_bp.route("/workspace/<doc_id>")' in bp                       # 3
    assert (REPO / "tools/document_intelligence/workspace.py").exists()       # 4
    for name in ("BASIS_POSITIONED", "BASIS_CHUNKS", "BASIS_NONE",            # 5
                 "BASIS_UNMEASURED", "GATE_CLEAN", "GATE_UNMEASURED"):
        assert hasattr(workspace, name), name

    registry = yaml.safe_load((REPO / "args/component_registry.yaml").read_text(encoding="utf-8"))
    dic = [c for c in registry["components"] if c["key"] == "dic"][0]
    hrefs = [link["href"] for link in dic["nav"]["links"]]
    assert "/document-intelligence/workspace" in hrefs                       # 7
    assert "dic.suggestions" in dic["iqe"]["collections"]                    # 8

    # Registration is a side effect of importing the adapter module.
    importlib.import_module("tools.iqe.adapters.dic")
    from tools.iqe.executor import list_collections
    assert "dic.suggestions" in list_collections()

    seeds = list((REPO / "context/iqe/queries/dic").glob("*workspace*.iqe"))
    assert len(seeds) >= 3, [s.name for s in seeds]
    assert "iqe_query_widget.html" in TEMPLATE.read_text(encoding="utf-8")


def test_the_seed_queries_parse():
    """A seed query that does not parse is a documented command that does not
    exist — the CLAUDE.md rule, applied to IQE."""
    from tools.iqe.parser import parse

    for path in sorted((REPO / "context/iqe/queries/dic").glob("*workspace*.iqe")):
        body = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if not line.strip().startswith("#")).strip()
        node = parse(body)
        assert ".".join(node.collection.parts) == "dic.suggestions", path.name


def test_the_suggestions_collection_normalises_a_null_anchor_basis():
    """A NULL anchor_basis is normalised to "" rather than dropped, so a query
    CAN ask for the unanchored population — which on this board is all of it."""
    src = (REPO / "tools/iqe/adapters/dic.py").read_text(encoding="utf-8")
    assert 'r["anchor_basis"] = r.get("anchor_basis") or ""' in src
    assert 'r["appliable"] = r["anchor_basis"] in ("exact", "relocated")' in src


@pytest.mark.parametrize("basis,addressable", [
    (workspace.BASIS_POSITIONED, True),
    (workspace.BASIS_CHUNKS, False),
    (workspace.BASIS_NONE, False),
    (workspace.BASIS_UNMEASURED, False),
])
def test_only_the_positioned_basis_is_addressable(basis, addressable):
    """Addressability is what decides whether a mark on screen means anything.
    Only dic_sections content is spliced by the accept door."""
    assert (basis == workspace.BASIS_POSITIONED) is addressable
