"""Modernization cards are not filed against a demo corpus (cdh-seed-01).

MEASURED 2026-09-01. Six scheduled `*_modernization` cards were traced to their
findings and their documents. EVERY FINDING WAS CORRECT -- Catalyst 6500 EOL
2018-07-31, Catalyst 3750 EOL 2023-01-31, TLS 1.1 per RFC 8996 / NIST
SP 800-52r2, MD5 per FIPS 180-4, and `unverifiable_evidence` for documents
carrying no chunk links. EVERY DOCUMENT was demo, test or fixture data:

    Politics           the US Constitution and a sample SOP
    test               one document titled `tmp9x41vmaz`, a Python tempfile name
    isp-peering-demo   says so in its own name; auto-registered by migration 268
    col1               one document, 'Legacy Doc', dated 2020-01-01

So the board asked a person to modernize the First Amendment. The cost is not
those six cards -- it is that a queue carrying tasks nobody can act on is one
people learn to skim, and then the real finding in it is the one skimmed past.

A DENYLIST, NOT AN ALLOWLIST, and the direction is the decision. An allowlist
of live collections would mean a NEW collection nobody remembered to declare
produces no cards at all -- silently dropping real findings, which is the worse
failure for a findings queue. A denylist means an undeclared collection still
produces cards: noisier, never silent.

EVERY ENTRY CARRIES A WRITTEN REASON, the same discipline the censuses in this
repo already use. A bare list is a claim nobody checked.

NOT A NAME HEURISTIC. Matching 'demo' or 'test' in a collection name would miss
'Politics' and 'col1' -- two of the four measured -- and would wrongly exclude a
real collection somebody happened to call 'Test Estate'.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "args" / "docmod" / "demo_collections.yaml"


def _load():
    from tools.doc_modernization import card_bridge

    return card_bridge.demo_collections()


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
def test_the_declaration_exists_and_parses():
    assert CONFIG.is_file(), f"{CONFIG} is the declaration this gate reads"
    assert isinstance(yaml.safe_load(CONFIG.read_text(encoding="utf-8")), dict)


def test_every_entry_carries_a_WRITTEN_REASON():
    """A bare list of ids is a claim nobody can check. Each one says why that
    collection is not a live corpus."""
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    entries = raw.get("collections") or {}
    assert entries, "the declaration names no collection"
    for collection_id, reason in entries.items():
        assert isinstance(reason, str) and len(reason.strip()) > 15, (
            f"{collection_id} has no usable reason: {reason!r}")


def test_the_four_measured_collections_are_declared():
    """These are the ones that actually filed cards on 2026-09-01."""
    declared = _load()
    for collection_id in ("Politics", "test", "isp-peering-demo", "col1"):
        assert collection_id in declared, collection_id


def test_an_unreadable_declaration_denies_NOTHING():
    """FAIL OPEN. If the file is missing or malformed the seeder must keep
    filing cards -- a findings queue that goes silent because a config file
    broke is the failure this whole card is about, one level up."""
    from tools.doc_modernization import card_bridge

    assert card_bridge.demo_collections(path=REPO / "nope" / "missing.yaml") == frozenset()


# --------------------------------------------------------------------------- #
# what it does to the seeder
# --------------------------------------------------------------------------- #
class _Row(dict):
    def keys(self):          # sqlite3.Row-ish, enough for dict(row)
        return super().keys()


def test_a_document_in_a_DECLARED_DEMO_collection_is_skipped():
    from tools.doc_modernization import card_bridge

    assert card_bridge._is_demo_document("isp-peering-demo") is True
    assert card_bridge._is_demo_document("Politics") is True


def test_a_document_in_ANY_OTHER_collection_is_NOT_skipped():
    """The half a one-sided test would miss. A gate that skipped everything
    would pass the test above and file nothing at all."""
    from tools.doc_modernization import card_bridge

    assert card_bridge._is_demo_document("live-estate-2026") is False
    assert card_bridge._is_demo_document("") is False
    assert card_bridge._is_demo_document(None) is False


def test_a_collection_merely_NAMED_like_a_test_is_not_excluded():
    """Not a name heuristic: 'Test Estate' is a plausible real collection, and
    only a declaration may exclude one."""
    from tools.doc_modernization import card_bridge

    assert card_bridge._is_demo_document("Test Estate") is False
    assert card_bridge._is_demo_document("demo-but-real") is False


def test_the_seeder_asks_the_gate_at_all():
    """Pins the wiring. The gate existing and nothing calling it is the defect
    this repo files most often."""
    import inspect

    from tools.doc_modernization import card_bridge

    source = inspect.getsource(card_bridge.emit_rollups)
    assert "_is_demo_document" in source


def test_skipped_demo_documents_are_COUNTED_not_silent():
    """A seeder that quietly drops documents cannot be told apart from one that
    found nothing. The count is reported on the result."""
    import inspect

    from tools.doc_modernization import card_bridge

    source = inspect.getsource(card_bridge.emit_rollups)
    assert "demo_skipped" in source
