# CUI // SP-CTI
"""sdt-vocab-01 — the deck type the system writes must be one the schema accepts.

``tools/slides/blueprint.py`` persists ``deck_type='template_fill'`` for every
deck built by filling an uploaded .pptx. ``template_fill`` was not in
``DECK_TYPES``, and ``CHECK_DECK_TYPE`` derived from ``DECK_TYPES``, so the
template-fill route could not persist a deck against a correctly-created
PostgreSQL schema at all. It only appeared to work on databases whose
``slides_decks`` predated the constraint — which is why a clean checkout
surfaced it and a long-lived one did not. Confirmed on the live database before
the fix: ``chk_deck_type`` listed eight types and this was not one of them.

The obvious repair — append it to ``DECK_TYPES`` — is the one to guard against,
because ``DECK_TYPES`` is *also* handed to the index page and the new-deck wizard
as the user-facing picker. A template-fill deck is produced by uploading a file,
never chosen from a menu, so that repair fixes the constraint by putting a
non-choice in front of the user.

Both directions are therefore asserted. A test that only checked persistence
would pass on the naive fix and lock it in, which is the specific failure this
file exists to prevent.

Deliberately free of the Flask app: this is a statement about the vocabulary and
the schema, and routing it through the dashboard would couple it to auth and to
the ``ICDEV_SLIDES_ENABLED`` toggle — see ``sdt-auth-01`` for that separate mess.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.slides.constants import (  # noqa: E402
    CHECK_DECK_TYPE,
    DECK_TYPES,
    PERSISTED_DECK_TYPES,
    SYSTEM_DECK_TYPES,
)

TEMPLATE_FILL = "template_fill"


# ---------------------------------------------------------------------------
# Direction 1 — the schema accepts what the route writes
# ---------------------------------------------------------------------------

def test_the_constraint_admits_the_deck_type_the_route_persists():
    assert TEMPLATE_FILL in CHECK_DECK_TYPE


def test_a_template_fill_deck_persists_against_the_current_schema():
    """The end state, not the constant: build the CHECK and insert through it.

    Asserting on ``CHECK_DECK_TYPE`` alone would still pass if the string stopped
    being the thing the table is actually built with.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"CREATE TABLE slides_decks ("
        f"  deck_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        f"  title TEXT NOT NULL,"
        f"  deck_type TEXT NOT NULL DEFAULT 'executive_overview' CHECK({CHECK_DECK_TYPE})"
        f")"
    )
    conn.execute(
        "INSERT INTO slides_decks (title, deck_type) VALUES (?, ?)",
        ("Filled from a customer template", TEMPLATE_FILL),
    )
    conn.commit()
    stored = conn.execute("SELECT deck_type FROM slides_decks").fetchone()[0]
    assert stored == TEMPLATE_FILL
    conn.close()


def test_an_invented_deck_type_is_still_refused():
    """The constraint must still constrain — widening is not removing."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"CREATE TABLE slides_decks ("
        f"  deck_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        f"  title TEXT NOT NULL,"
        f"  deck_type TEXT NOT NULL CHECK({CHECK_DECK_TYPE})"
        f")"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO slides_decks (title, deck_type) VALUES (?, ?)",
            ("nope", "not_a_real_deck_type"),
        )
    conn.close()


# ---------------------------------------------------------------------------
# Direction 2 — the picker does not offer it
# ---------------------------------------------------------------------------

def test_the_picker_does_not_offer_a_deck_type_nobody_can_choose():
    """Guards the naive fix.

    ``DECK_TYPES`` is passed to ``slides/index.html`` and ``slides/new.html`` as
    the deck-type options. A template-fill deck comes from uploading a .pptx, so
    offering it as a menu item would advertise a path that does not exist.
    """
    assert TEMPLATE_FILL not in DECK_TYPES
    assert TEMPLATE_FILL in SYSTEM_DECK_TYPES


def test_the_routes_hand_the_templates_the_selectable_set_only():
    """Pin the seam, not just the constant.

    The split is only worth anything while the views keep rendering
    ``DECK_TYPES``; a later edit pointing them at ``PERSISTED_DECK_TYPES`` would
    restore the bug with every constant still looking correct.
    """
    import inspect

    from tools.slides import blueprint

    for view in (blueprint.index, blueprint.new_deck):
        src = inspect.getsource(view)
        assert "deck_types=DECK_TYPES" in src, (
            f"{view.__name__} must render the selectable set, not the persisted one"
        )
        assert "PERSISTED_DECK_TYPES" not in src


# ---------------------------------------------------------------------------
# The two vocabularies relate the way the schema assumes
# ---------------------------------------------------------------------------

def test_persisted_is_selectable_plus_system_with_no_overlap():
    assert PERSISTED_DECK_TYPES == DECK_TYPES + SYSTEM_DECK_TYPES
    assert not set(DECK_TYPES) & set(SYSTEM_DECK_TYPES), (
        "a deck type is either offered or system-only, never both"
    )
    assert len(set(PERSISTED_DECK_TYPES)) == len(PERSISTED_DECK_TYPES)


def test_the_check_is_derived_and_not_hand_written():
    """Every persisted type must appear, so nobody can hand-edit the string."""
    for deck_type in PERSISTED_DECK_TYPES:
        assert f"'{deck_type}'" in CHECK_DECK_TYPE


def test_the_packaged_mirror_agrees():
    """A constants file correct in only one tree is correct for half the installs."""
    icdev_constants = ROOT / "icdev" / "tools" / "slides" / "constants.py"
    if not icdev_constants.is_file():
        pytest.skip("icdev/ package mirror not present")
    text = icdev_constants.read_text(encoding="utf-8")
    assert "SYSTEM_DECK_TYPES" in text
    assert "PERSISTED_DECK_TYPES" in text
    assert f'"{TEMPLATE_FILL}"' in text
