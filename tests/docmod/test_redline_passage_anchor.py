# CUI // SP-CTI
"""dwr-anchor-04 — the redline drafter sees the sentence it is rewriting.

Before this card the drafter was handed ``finding["entity_label"]`` — a bare
token like ``TLS 1.1`` — as the text to rewrite, and stored that token as the
suggestion's ``current_content``. The model wrote a replacement passage having
never seen the surrounding prose, and no surface could render an honest
before/after because the "before" was two words.

What is proven here:

* ``widen_to_passage`` is pure and always returns a span CONTAINING the match,
  so the entity can never be sliced in half by a boundary rule;
* ``resolve_passage`` records the basis it can PROVE against the live section —
  ``exact`` when the offsets verify, ``relocated`` when the passage is found
  exactly once, ``unanchored`` when it is found nowhere, found twice, or the
  heading names no single section;
* ``draft_redline`` writes the PASSAGE as ``current_content`` and an anchor that
  slices the section verbatim, and REFUSES to write a suggestion it could not
  anchor — without spending an LLM call on it;
* TRUST gate 2 is tested in BOTH directions. Its rule — nothing in the output
  that was not in the input or the candidate list — does not move; the INPUT
  did, so a product verbatim in the shown passage is admitted, a product the
  passage never mentioned is still a hard block, and swapping one product for
  another is still a hard block.

The database is the per-module SQLite file tests/docmod/conftest.py points
``tools.db.storage`` at, so the drafter's own ``get_connection`` is exercised.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.doc_modernization import redline_drafter as rd  # noqa: E402
from tools.document_intelligence import suggestion_store as store  # noqa: E402

# One paragraph carrying the entity, one that does not, and a heading-ish line
# with no sentence terminator at all.
SECTION_TEXT = (
    "Transport Profile\n\n"
    "The enclave shall use TLS 1.1 for traffic between sites. "
    "Catalyst 6500 aggregation switches terminate those tunnels.\n\n"
    "Waivers are recorded in the annex."
)
ENTITY = "TLS 1.1"
SENTENCE = "The enclave shall use TLS 1.1 for traffic between sites."

EVIDENCE = [{"source": "rule:crypto-tls-02",
             "detail": "TLS 1.1 is superseded; use TLS 1.2 or higher.",
             "date": "2021-03-25"}]


# ── schema ────────────────────────────────────────────────────────────────────

def _reset_db():
    """A throwaway schema carrying exactly the columns the drafter reads and
    writes. Declared here rather than assumed: a fixture that assumes its
    table's shape is how dwr-anchor-02's suite went red on a column nobody
    added."""
    from tools.db.storage import get_connection
    with get_connection() as conn:
        store._ensure_tables(conn)
        conn.execute("DELETE FROM dic_suggestions")
        for ddl in (
            """CREATE TABLE IF NOT EXISTS dic_sections (
                   section_id TEXT PRIMARY KEY, version_id TEXT, heading TEXT,
                   content TEXT, status TEXT, origin TEXT, created_at TEXT)""",
            """CREATE TABLE IF NOT EXISTS rag_chunks (
                   id TEXT PRIMARY KEY, content TEXT)""",
            """CREATE TABLE IF NOT EXISTS dic_chunk_links (
                   link_id TEXT PRIMARY KEY, version_id TEXT, rag_chunk_id TEXT,
                   page INTEGER, section TEXT)""",
            """CREATE TABLE IF NOT EXISTS docmod_findings (
                   finding_id TEXT PRIMARY KEY, run_id TEXT, doc_id TEXT,
                   version_id TEXT, chunk_link_id TEXT, section_heading TEXT,
                   page INTEGER, pack_id TEXT, entity_label TEXT,
                   entity_type TEXT, finding_type TEXT,
                   -- MIRROR THE MIGRATED SCHEMA. Without this CHECK the fixture's
                   -- `CREATE TABLE IF NOT EXISTS` silently wins on any host where the
                   -- real table is absent, so an invalid verdict passes locally and
                   -- fails only in CI -- which is exactly how 'superseded' got here.
                   currency_verdict TEXT CHECK (currency_verdict IN
                       ('current','deprecated','eol','retired','divergent','unknown')),
                   severity TEXT, rationale TEXT, evidence_json TEXT,
                   recommended_replacement TEXT, replacement_evidence_json TEXT,
                   confidence REAL, state TEXT, supersedes_id TEXT,
                   redline_suggestion_id TEXT, dedupe_key TEXT, created_at TEXT,
                   tenant_id TEXT, classification TEXT,
                   anchor_start INTEGER, anchor_end INTEGER, anchor_text TEXT)""",
        ):
            conn.execute(ddl)
        for table in ("dic_sections", "rag_chunks", "dic_chunk_links", "docmod_findings"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


def _section(section_id="sec-1", version_id="ver-1", heading="Transport Profile",
             content=SECTION_TEXT):
    from tools.db.storage import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, heading, content, "
            "status, origin, created_at) VALUES (%s,%s,%s,%s,'approved','human','t')",
            (section_id, version_id, heading, content),
        )
        conn.commit()
    return section_id


def _chunk(link_id="lnk-1", version_id="ver-1", content=None, section="Transport Profile"):
    from tools.db.storage import get_connection
    with get_connection() as conn:
        conn.execute("INSERT INTO rag_chunks (id, content) VALUES (%s,%s)",
                     (f"rc-{link_id}", content))
        conn.execute(
            "INSERT INTO dic_chunk_links (link_id, version_id, rag_chunk_id, page, section) "
            "VALUES (%s,%s,%s,1,%s)",
            (link_id, version_id, f"rc-{link_id}", section),
        )
        conn.commit()
    return link_id


def _finding(finding_id="fnd-1", *, chunk_link_id=None, text_for_span=SECTION_TEXT,
             entity=ENTITY, heading="Transport Profile", version_id="ver-1",
             span=None, anchor_text=None, replacement="TLS 1.2 or higher",
             state="open"):
    """Insert one open finding whose span is the entity's real offsets in
    ``text_for_span`` — the chunk-local convention dwr-anchor-02 persists."""
    from tools.db.storage import get_connection
    if span is None:
        start = text_for_span.index(entity)
        span = (start, start + len(entity))
    if anchor_text is None and span[0] is not None:
        anchor_text = text_for_span[span[0]:span[1]]
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO docmod_findings
               (finding_id, run_id, doc_id, version_id, chunk_link_id, section_heading,
                page, pack_id, entity_label, entity_type, finding_type, currency_verdict,
                severity, rationale, evidence_json, recommended_replacement,
                replacement_evidence_json, confidence, state, dedupe_key, created_at,
                tenant_id, classification, anchor_start, anchor_end, anchor_text)
               VALUES (%s,'run-1','doc-1',%s,%s,%s,1,'crypto_protocols',%s,'protocol',
                       'superseded','retired','high','TLS 1.1 is superseded.',
                       %s,%s,'[]',0.9,%s,%s,'t','','CUI',%s,%s,%s)""",
            (finding_id, version_id, chunk_link_id, heading, entity,
             json.dumps(EVIDENCE), replacement, state, f"dk-{finding_id}",
             span[0], span[1], anchor_text),
        )
        conn.commit()
    return finding_id


def _suggestions():
    from tools.db.storage import get_connection
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM dic_suggestions ORDER BY created_at").fetchall()]


@pytest.fixture(autouse=True)
def _clean():
    _reset_db()
    yield


# ── widen_to_passage: pure ────────────────────────────────────────────────────

class TestWidenToPassage:
    def test_a_match_widens_to_its_sentence(self):
        s = SECTION_TEXT.index(ENTITY)
        a, b = rd.widen_to_passage(SECTION_TEXT, s, s + len(ENTITY))
        assert SECTION_TEXT[a:b] == SENTENCE

    def test_the_passage_always_contains_the_match(self):
        # Every entity occurrence, every paragraph — a boundary rule that can
        # slice the finding's own words in half puts the highlight on the
        # wrong text, which is the failure mode `finding_anchor` already refuses.
        text = SECTION_TEXT
        for needle in (ENTITY, "Catalyst 6500", "Waivers", "Transport Profile"):
            s = text.index(needle)
            a, b = rd.widen_to_passage(text, s, s + len(needle))
            assert a <= s and b >= s + len(needle)
            assert needle in text[a:b]

    def test_a_version_dot_is_not_a_sentence_boundary(self):
        # "TLS 1.1" carries a period that is NOT followed by whitespace, so it
        # must not end a sentence — otherwise the passage stops mid-entity.
        text = "Use TLS 1.1 today. Then stop."
        a, b = rd.widen_to_passage(text, text.index("TLS"), text.index("TLS") + 7)
        assert text[a:b] == "Use TLS 1.1 today."

    def test_a_paragraph_with_no_terminator_widens_to_the_paragraph(self):
        s = SECTION_TEXT.index("Transport Profile")
        a, b = rd.widen_to_passage(SECTION_TEXT, s, s + len("Transport"))
        assert SECTION_TEXT[a:b] == "Transport Profile"

    def test_the_passage_never_crosses_a_blank_line(self):
        s = SECTION_TEXT.index("Waivers")
        a, b = rd.widen_to_passage(SECTION_TEXT, s, s + len("Waivers"))
        assert SECTION_TEXT[a:b] == "Waivers are recorded in the annex."
        assert "Catalyst" not in SECTION_TEXT[a:b]

    def test_whitespace_is_trimmed_but_never_past_the_match(self):
        text = "   \n  spaced  \n   "
        s = text.index("spaced")
        a, b = rd.widen_to_passage(text, s, s + len("spaced"))
        assert text[a:b] == "spaced"

    def test_an_empty_text_is_an_empty_span(self):
        assert rd.widen_to_passage("", 0, 0) == (0, 0)


# ── resolve_passage: the basis it can PROVE ───────────────────────────────────

class TestResolvePassage:
    def _resolve(self, finding_id):
        from tools.db.storage import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM docmod_findings WHERE finding_id=%s", (finding_id,)
            ).fetchone()
            return rd.resolve_passage(conn, dict(row))

    def test_section_fallback_offsets_verify_and_are_exact(self):
        # No chunk link: the scan ran over the section itself, so the finding's
        # chunk-local offsets ARE section offsets and the span verifies.
        _section()
        _finding(chunk_link_id=None)
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "exact"
        assert a.passage == SENTENCE
        assert a.section_id == "sec-1"
        assert a.section_content[a.anchor_start:a.anchor_end] == SENTENCE

    def test_a_chunk_that_is_not_the_section_relocates_when_found_once(self):
        # The rag chunk is a fragment of the section, so the chunk-local offsets
        # do NOT verify against the section — but the passage occurs once, so
        # str.find recovers it and the basis is honest about being a guess.
        chunk = "The enclave shall use TLS 1.1 for traffic between sites. Catalyst 6500 aggregation switches terminate those tunnels."
        _section()
        _chunk(content=chunk)
        _finding(chunk_link_id="lnk-1", text_for_span=chunk)
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "relocated"
        assert a.passage == SENTENCE
        assert a.section_content[a.anchor_start:a.anchor_end] == SENTENCE

    def test_a_passage_occurring_twice_stays_unanchored(self):
        doubled = SECTION_TEXT + "\n\n" + SENTENCE
        chunk = SENTENCE
        _section(content=doubled)
        _chunk(content=chunk)
        _finding(chunk_link_id="lnk-1", text_for_span=chunk)
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "unanchored"
        assert a.anchor_start is None and a.anchor_end is None
        assert "ambiguous" in a.reason

    def test_a_heading_naming_two_sections_resolves_nothing(self):
        _section("sec-1")
        _section("sec-2")
        _finding(chunk_link_id=None)
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "unanchored"
        assert a.section_id is None
        assert "no single dic_sections row" in a.reason

    def test_a_heading_matching_no_section_resolves_nothing(self):
        _section(heading="Somewhere Else")
        _chunk(content=SECTION_TEXT)
        _finding(chunk_link_id="lnk-1")
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "unanchored"
        assert a.section_id is None

    def test_a_finding_with_no_span_is_unanchored(self):
        _section()
        _finding(chunk_link_id=None, span=(None, None), anchor_text=None)
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "unanchored"
        assert "no span" in a.reason

    def test_a_chunk_that_moved_under_the_span_is_unanchored(self):
        # The offsets still fit, but they no longer slice what was recorded —
        # the document changed. Widening from there would highlight the wrong
        # words, so nothing is anchored.
        _section()
        _finding(chunk_link_id=None, anchor_text="SSHv1")
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "unanchored"
        assert "moved under the span" in a.reason

    def test_an_unreadable_chunk_is_unanchored_not_empty(self):
        _section()
        _finding(chunk_link_id="lnk-missing")
        a = self._resolve("fnd-1")
        assert a.anchor_basis == "unanchored"
        assert "could not be read" in a.reason


# ── draft_redline: what it writes, and what it refuses to write ───────────────

DRAFT = ("The enclave shall use TLS 1.2 or higher for traffic between sites "
         "[source: rule:crypto-tls-02].")

# A sentence naming a SECOND product, so gate 2 can be tested in both directions.
TWO_PRODUCT_SECTION = (
    "Transport Profile\n\n"
    "Catalyst 6500 aggregation switches terminate TLS 1.1 tunnels between sites.\n\n"
    "Waivers are recorded in the annex."
)
TWO_PRODUCT_SENTENCE = (
    "Catalyst 6500 aggregation switches terminate TLS 1.1 tunnels between sites.")


class TestDraftRedline:
    def test_current_content_is_the_passage_not_the_entity_label(self, monkeypatch):
        """The card's own acceptance criterion."""
        monkeypatch.setattr(rd, "_invoke_llm", lambda s, u: DRAFT)
        _section()
        _finding(chunk_link_id=None)
        result = rd.draft_redline("fnd-1")
        assert result.status == "drafted", result.reason
        rows = _suggestions()
        assert len(rows) == 1
        assert rows[0]["current_content"] == SENTENCE
        assert rows[0]["current_content"] != ENTITY

    def test_the_model_is_shown_the_passage_and_told_which_item_is_stale(self, monkeypatch):
        seen = {}

        def _capture(system, user):
            seen["user"] = user
            return DRAFT

        monkeypatch.setattr(rd, "_invoke_llm", _capture)
        _section()
        _finding(chunk_link_id=None)
        rd.draft_redline("fnd-1")
        # The whole sentence, not the two-word token it used to be handed.
        assert SENTENCE in seen["user"]
        assert "for traffic between sites" in seen["user"]
        assert "OUT-OF-DATE ITEM" in seen["user"]
        assert ENTITY in seen["user"]               # still named as the stale item

    def test_the_written_anchor_slices_the_section_verbatim(self, monkeypatch):
        monkeypatch.setattr(rd, "_invoke_llm", lambda s, u: DRAFT)
        _section()
        _finding(chunk_link_id=None)
        rd.draft_redline("fnd-1")
        row = _suggestions()[0]
        assert row["anchor_section_id"] == "sec-1"
        assert row["section_id"] == "sec-1"
        assert row["anchor_basis"] in ("exact", "relocated")
        assert row["origin_kind"] == "docmod_redline"
        assert SECTION_TEXT[row["anchor_start"]:row["anchor_end"]] == row["anchor_text"]
        assert row["anchor_text"] == SENTENCE

    def test_an_unanchored_finding_writes_no_suggestion_and_spends_no_token(self, monkeypatch):
        calls = []
        monkeypatch.setattr(rd, "_invoke_llm",
                            lambda s, u: calls.append(1) or DRAFT)
        _section("sec-1")
        _section("sec-2")          # same heading twice — nothing resolves
        _finding(chunk_link_id=None)
        result = rd.draft_redline("fnd-1")
        assert result.status == "unanchored"
        assert result.anchor_basis == "unanchored"
        assert "cannot anchor" in result.reason
        assert _suggestions() == []
        assert calls == [], "an unanchorable finding must not spend an LLM call"

    def test_an_unanchored_finding_stays_open_for_the_next_sweep(self, monkeypatch):
        monkeypatch.setattr(rd, "_invoke_llm", lambda s, u: DRAFT)
        _section("sec-1")
        _section("sec-2")
        _finding(chunk_link_id=None)
        rd.draft_redline("fnd-1")
        from tools.db.storage import get_connection
        with get_connection() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT state FROM docmod_findings").fetchall()]
        assert [r["state"] for r in rows] == ["open"]

    def test_gate_two_blocks_a_product_the_passage_never_mentioned(self, monkeypatch):
        """The invention case, and the one that matters: `Nexus 9000` is in no
        candidate list, is not the stale label, and is nowhere in the passage
        the model was shown. Still a hard block."""
        rogue = ("Use TLS 1.2 or higher [source: rule:crypto-tls-02] on the "
                 "Nexus 9000 aggregation switches.")
        monkeypatch.setattr(rd, "_invoke_llm", lambda s, u: rogue)
        _section()
        _finding(chunk_link_id=None)
        result = rd.draft_redline("fnd-1")
        assert result.status == "blocked"
        assert "outside the candidate list" in result.reason
        assert _suggestions() == []

    def test_gate_two_allows_a_product_verbatim_in_the_passage(self, monkeypatch):
        """The other direction. Widening the input to a real sentence means the
        sentence may name a second product; a faithful rewrite has to KEEP it,
        and blocking that would make every multi-product passage undraftable.
        The token is admitted because it is verbatim in what we handed over —
        not because the gate got softer."""
        faithful = ("Catalyst 6500 aggregation switches terminate TLS 1.2 or "
                    "higher tunnels between sites [source: rule:crypto-tls-02].")
        monkeypatch.setattr(rd, "_invoke_llm", lambda s, u: faithful)
        _section(content=TWO_PRODUCT_SECTION)
        _finding(chunk_link_id=None, text_for_span=TWO_PRODUCT_SECTION)
        result = rd.draft_redline("fnd-1")
        assert result.status == "drafted", result.reason
        assert _suggestions()[0]["current_content"] == TWO_PRODUCT_SENTENCE

    def test_gate_two_blocks_swapping_a_product_the_passage_did_mention(self, monkeypatch):
        """Admitting `Catalyst 6500` because the passage says it must not admit
        a DIFFERENT switch in its place."""
        swapped = ("Catalyst 9300 aggregation switches terminate TLS 1.2 or "
                   "higher tunnels between sites [source: rule:crypto-tls-02].")
        monkeypatch.setattr(rd, "_invoke_llm", lambda s, u: swapped)
        _section(content=TWO_PRODUCT_SECTION)
        _finding(chunk_link_id=None, text_for_span=TWO_PRODUCT_SECTION)
        result = rd.draft_redline("fnd-1")
        assert result.status == "blocked"
        assert _suggestions() == []

    def test_a_hallucinated_citation_is_still_a_hard_block(self, monkeypatch):
        monkeypatch.setattr(
            rd, "_invoke_llm",
            lambda s, u: "Use TLS 1.2 or higher [source: rule:invented-99].")
        _section()
        _finding(chunk_link_id=None)
        result = rd.draft_redline("fnd-1")
        assert result.status == "blocked"
        assert "hallucinated" in result.reason
        assert _suggestions() == []
