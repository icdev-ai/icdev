# CUI // SP-CTI
"""The catalogue must describe itself truthfully (aca-hon-02, -03, -04).

Three defects, all in how fa_missions rows get their user-visible fields:

  * derived missions had title and tagline INVERTED. discover_missions set
    title=_humanise_slug(slug) and tagline=<first step's title>, so 35 of 124
    missions showed a mechanical card title ('Ciso Capstone', 'Chromadb Rag',
    'Aiml Governance', 'Advanced Rag') while the good human title sat in the
    tagline ('CISO Capstone - Configure Full AI Governance Posture Dashboard').
  * m11-multimodal's tagline was the literal string 'CUI // SP-CTI'. Its content
    file opens with the classification banner as a markdown heading, and
    _title_from_body returns the first '# ' heading it finds — so the marker became
    the step title and then the mission tagline, rendered on the card.
  * mission_type was taken from the FIRST step alone, and for hand-written entries
    was simply declared. 34 missions advertised 'coding' with no coding step; #1013
    reduced that to 13 by wiring real coding steps, and the remaining 13 are still
    lying to the learner on the hub and browser badges.

m11-multimodal also duplicates the catalogued m-t1-11-multimodal as a subject.
test_derived_missions_never_collide_with_a_catalogued_track_slot passes because it
keys on track slot (m11 vs m-t1-11 differ), not on what the mission is about.
"""
from __future__ import annotations

import pytest

from apps.forge_academy import content_loader as cl

_MARKERS = ("CUI", "SP-CTI", "SECRET", "NOFORN")


# ---------------------------------------------------------------------------
# aca-hon-02 — classification markers must never become content
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body,expected",
    [
        # The m11-multimodal shape: banner heading first, real heading after.
        ("# CUI // SP-CTI\n\n# Multimodal AI\n\nbody", "Multimodal AI"),
        ("# CUI // SP-CTI\n\nprose only", "fallback"),
        ("# SECRET\n\n# Real Title", "Real Title"),
        # Unmarked files keep working exactly as before.
        ("# Real Title\n\nbody", "Real Title"),
        ("no heading here", "fallback"),
    ],
)
def test_title_from_body_skips_classification_headings(body, expected):
    assert cl._title_from_body(body, "fallback") == expected


def test_no_discovered_step_title_is_a_classification_marker():
    """Against the real content tree, not a fixture."""
    offenders = []
    for slug, steps in cl.discover_steps().items():
        for st in steps:
            title = (st.get("title") or "").upper()
            if any(m in title for m in _MARKERS):
                offenders.append((slug, st["step_num"], st["title"]))
    assert not offenders, f"classification marker used as a step title: {offenders[:5]}"


def test_no_derived_mission_carries_a_classification_marker():
    offenders = [
        (m["slug"], m.get("title"), m.get("tagline"))
        for m in cl.discover_missions()
        if any(mk in f"{m.get('title')} {m.get('tagline')}".upper() for mk in _MARKERS)
    ]
    assert not offenders, f"marker leaked into a mission card: {offenders[:5]}"


# ---------------------------------------------------------------------------
# aca-hon-02 — title and tagline the right way round
# ---------------------------------------------------------------------------

def test_derived_titles_come_from_the_authored_heading():
    """The real invariant: use what the author wrote, not a slug transformation.

    Asserting `title != _humanise_slug(slug)` would be wrong — 'Canvas Selection' is
    a perfectly good authored title that happens to coincide with its slug. What
    matters is the SOURCE: when the first step has an authored title, that is the
    mission title.
    """
    steps_by_slug = cl.discover_steps()
    bad = []
    for m in cl.discover_missions():
        authored = ((steps_by_slug.get(m["slug"]) or [{}])[0].get("title") or "").strip()
        if authored and (m.get("title") or "").strip() != authored:
            bad.append((m["slug"], m.get("title"), authored))
    assert not bad, (
        f"{len(bad)} derived missions ignore the authored title: {bad[:6]}"
    )


def test_the_mechanical_slug_form_is_only_a_fallback():
    """_humanise_slug should only be reached when nothing was authored."""
    import inspect

    src = inspect.getsource(cl.discover_missions)
    assert 'title = human_title or _humanise_slug(slug)' in src, (
        "the authored title must take precedence over the slug transformation"
    )


def test_the_human_title_is_not_stranded_in_the_tagline():
    """It used to be: tagline held 'CISO Capstone - Configure...' while title said 'Ciso Capstone'."""
    for m in cl.discover_missions():
        title, tagline = (m.get("title") or ""), (m.get("tagline") or "")
        if tagline and title:
            assert not (len(tagline) > len(title) * 2 and title.lower() in tagline.lower()), (
                f"{m['slug']}: the tagline still carries the real title "
                f"(title={title!r}, tagline={tagline!r})"
            )


# ---------------------------------------------------------------------------
# aca-hon-04 — mission_type must reflect the steps
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "types,expected",
    [
        (["coding"], "coding"),
        (["watch", "coding", "watch"], "coding"),   # any coding step -> coding
        (["watch", "watch", "reflect"], "watch"),   # otherwise the most common
        (["configure", "configure", "watch"], "configure"),
        ([], "watch"),
    ],
)
def test_mission_type_is_derived_from_the_whole_step_list(types, expected):
    steps = [{"step_type": t} for t in types]
    assert cl.mission_type_from_steps(steps) == expected


def test_derived_missions_use_the_whole_step_list_not_just_the_first():
    """It read first.get('step_type'), so a watch intro hid a coding exercise."""
    import inspect

    src = inspect.getsource(cl.discover_missions)
    assert "mission_type_from_steps" in src


def test_no_derived_mission_claims_coding_without_a_coding_step():
    bad = [
        m["slug"] for m in cl.discover_missions()
        if m.get("mission_type") == "coding"
        and not any(s.get("step_type") == "coding"
                    for s in (cl.discover_steps().get(m["slug"]) or []))
    ]
    assert not bad, f"derived missions falsely labelled coding: {bad[:6]}"


def test_a_reconcile_pass_exists_for_already_seeded_mission_types():
    """The 13 remaining offenders are stored rows; discovery alone cannot fix them.

    Same lesson as aca-hon-05: a fix that only touches the insert path is inert on a
    database that is already seeded.
    """
    assert callable(getattr(cl, "reconcile_mission_types", None))


def test_reconcile_mission_types_corrects_a_stored_row():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY, slug TEXT, mission_type TEXT,
          is_active INTEGER DEFAULT 1);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY, mission_id INTEGER,
          step_type TEXT);
        INSERT INTO fa_missions (id, slug, mission_type) VALUES (1, 'm-a', 'coding');
        INSERT INTO fa_mission_steps (mission_id, step_type) VALUES (1, 'watch');
        INSERT INTO fa_mission_steps (mission_id, step_type) VALUES (1, 'watch');
        INSERT INTO fa_missions (id, slug, mission_type) VALUES (2, 'm-b', 'watch');
        INSERT INTO fa_mission_steps (mission_id, step_type) VALUES (2, 'coding');
        """
    )
    conn.commit()
    changed = cl.reconcile_mission_types(conn)
    assert changed == 2
    rows = {r["slug"]: r["mission_type"] for r in conn.execute(
        "SELECT slug, mission_type FROM fa_missions").fetchall()}
    assert rows["m-a"] == "watch", "a coding claim with no coding step must be corrected"
    assert rows["m-b"] == "coding", "a real coding step must be advertised"

    # Idempotent, and it must persist (the aca-hon-05 commit lesson).
    assert cl.reconcile_mission_types(conn) == 0


# ---------------------------------------------------------------------------
# aca-hon-03 — do not derive a mission that duplicates a catalogued subject
# ---------------------------------------------------------------------------

def test_a_derived_mission_does_not_duplicate_a_catalogued_title():
    from apps.forge_academy.content_loader import BUILTIN_MISSIONS

    catalogued = {(m.get("title") or "").strip().lower() for m in BUILTIN_MISSIONS}
    dupes = [
        m["slug"] for m in cl.discover_missions()
        if (m.get("title") or "").strip().lower() in catalogued
    ]
    assert not dupes, (
        f"derived missions duplicating a catalogued subject: {dupes} — the track-slot "
        "check does not catch these because the slugs differ"
    )


def test_m11_multimodal_is_no_longer_derived_alongside_the_catalogued_one():
    slugs = {m["slug"] for m in cl.discover_missions()}
    assert "m11-multimodal" not in slugs, (
        "m11-multimodal duplicates the catalogued m-t1-11-multimodal"
    )
