# CUI // SP-CTI
"""FORGE Academy step content: discovery, and the invariant that was missing.

Steps used to be seeded ONLY from the hand-maintained ``BUILTIN_STEPS`` dict. A
mission absent from that dict got zero step rows permanently, and the UI showed
"Content is being authored" even with its markdown committed on disk. 53 of 89
missions had no steps; 43 of those had authored content.

The reverse-direction test (fga-wire-05) is the one that would have caught it:
everything before it asked "does this dict entry have content?", and nothing
asked "does this content have a dict entry?".
"""
from __future__ import annotations

import re

import pytest

from apps.forge_academy.content_loader import (
    BUILTIN_MISSIONS,
    BUILTIN_STEPS,
    CONTENT_ROOT,
    _ONTOLOGY_STEP_RE,
    _step_type_from_class,
    _title_from_body,
    discover_steps,
    steps_for,
)

VALID_STEP_TYPES = {"watch", "configure", "reflect", "coding"}


@pytest.fixture(scope="module")
def discovered() -> dict:
    return discover_steps()


@pytest.fixture(scope="module")
def catalog_slugs() -> set:
    return {m["slug"] for m in BUILTIN_MISSIONS}


# ---------------------------------------------------------------------------
# fga-wire-05 — the invariant that was missing
# ---------------------------------------------------------------------------

def test_every_mission_with_authored_content_has_steps(discovered, catalog_slugs):
    """Reverse direction: content on disk MUST reach the UI.

    Adding dict entries would have cleared the symptom; this asserts the
    mechanism. If someone adds a mission's markdown and nothing else, this fails.
    """
    orphaned = sorted(
        slug for slug in catalog_slugs
        if discovered.get(slug) and not steps_for(slug, discovered)
    )
    assert not orphaned, (
        "these missions have authored content that produces no steps: " f"{orphaned}"
    )


def test_discovery_covers_the_missions_the_dict_missed(discovered, catalog_slugs):
    """The measured improvement, pinned so a regression is visible."""
    before = catalog_slugs & set(BUILTIN_STEPS)
    after = {s for s in catalog_slugs if steps_for(s, discovered)}
    assert after >= before, "discovery must never lose a mission the dict covered"
    assert len(after) - len(before) >= 40, (
        f"expected ~43 missions newly covered, got {len(after) - len(before)}"
    )


def test_a_mission_with_no_content_anywhere_still_reports_none(discovered):
    assert steps_for("no-such-mission-slug", discovered) == []


# ---------------------------------------------------------------------------
# Discovery mechanics
# ---------------------------------------------------------------------------

def test_discovery_finds_every_authored_markdown_file(discovered):
    on_disk = len(list(CONTENT_ROOT.rglob("*.md")))
    found = sum(len(v) for v in discovered.values())
    assert found == on_disk, (
        f"{on_disk} markdown files on disk but {found} steps discovered — a "
        f"layout variant is being skipped"
    )


def test_discovery_keys_on_frontmatter_not_directory_layout(discovered):
    """Content uses three different layouts; all must resolve identically."""
    layouts = {
        "tier1/m01-llm-fundamentals",          # <tier>/<slug>/steps/stepN_x.md
        "m-ace-01-roles-delegation",           # <tier>/<family>/<slug>/steps/...
        "m-leadership-01-ai-roi",              # <tier>/<slug>/step-N.md
    }
    for slug in ("m01-llm-fundamentals", "m-ace-01-roles-delegation",
                 "m-leadership-01-ai-roi"):
        assert discovered.get(slug), f"layout for {slug} was not discovered"
    assert layouts  # documents the three shapes under test


def test_steps_are_ordered_and_numbered_uniquely(discovered):
    for slug, steps in discovered.items():
        nums = [s["step_num"] for s in steps]
        assert nums == sorted(nums), f"{slug} steps are not ordered"
        assert len(nums) == len(set(nums)), f"{slug} has duplicate step numbers"
        assert all(n >= 1 for n in nums), f"{slug} has a non-positive step number"


def test_every_discovered_step_is_renderable(discovered):
    """A step row pointing at a missing file renders an error box to a learner."""
    for slug, steps in discovered.items():
        for st in steps:
            assert st["title"], f"{slug} step {st['step_num']} has no title"
            assert st["step_type"] in VALID_STEP_TYPES
            assert (CONTENT_ROOT / st["content_path"]).is_file(), (
                f"{slug} step {st['step_num']} -> missing {st['content_path']}"
            )


def test_builtin_steps_win_over_discovery(discovered):
    """The dict carries curated titles, xp and starter/test paths markdown lacks."""
    for slug in list(BUILTIN_STEPS)[:5]:
        assert steps_for(slug, discovered) is BUILTIN_STEPS[slug]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("icdev:mission:m01-llm-fundamentals:step:1", ("m01-llm-fundamentals", "1")),
        ("icdev:mission:m-ace-01-roles-delegation:step:12", ("m-ace-01-roles-delegation", "12")),
        ("", None),
        ("icdev:mission:no-step-number", None),
    ],
)
def test_ontology_id_parsing(raw, expected):
    m = _ONTOLOGY_STEP_RE.search(raw)
    actual = (m.group("slug"), m.group("num")) if m else None
    assert actual == expected


@pytest.mark.parametrize(
    "step_class,expected",
    [("icdev:Lesson", "watch"), ("icdev:Assessment", "reflect"),
     ("icdev:configure", "configure"), ("icdev:Lab", "coding"),
     ("icdev:coding", "coding"), ("", "watch"), (None, "watch")],
)
def test_step_class_maps_into_the_ui_vocabulary(step_class, expected):
    assert _step_type_from_class(step_class) == expected
    assert _step_type_from_class(step_class) in VALID_STEP_TYPES


def test_every_step_class_on_disk_is_mapped():
    """An unmapped class silently becomes 'watch' — catch new ones on arrival."""
    seen = set()
    for path in CONTENT_ROOT.rglob("*.md"):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
            if line.startswith("step_class:"):
                seen.add(line.split(":", 1)[1].strip().split(":")[-1].lower())
                break
    from apps.forge_academy.content_loader import _STEP_CLASS_TO_TYPE

    unmapped = sorted(seen - set(_STEP_CLASS_TO_TYPE))
    assert not unmapped, f"step_class values with no explicit mapping: {unmapped}"


def test_title_falls_back_when_a_file_has_no_heading():
    assert _title_from_body("# Real Title\n\nbody", "fallback") == "Real Title"
    assert _title_from_body("no heading here", "Fallback Name") == "Fallback Name"


def test_content_root_has_no_step_file_without_an_ontology_id():
    """Discovery skips unparsable files; none should exist."""
    missing = [
        p.relative_to(CONTENT_ROOT).as_posix()
        for p in CONTENT_ROOT.rglob("*.md")
        if not re.search(r"icdev:mission:[^\s:]+:step:\d+",
                         p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not missing, f"markdown that discovery cannot attach to a mission: {missing}"


# ---------------------------------------------------------------------------
# fga-wire-07 — authored content with no catalog entry at all
# ---------------------------------------------------------------------------

def test_no_authored_mission_is_left_uncatalogued(discovered, catalog_slugs):
    """The second gap, one level up from steps.

    Step discovery only reaches missions the catalog knows about. 37 mission
    directories carried authored steps and appeared in no catalog, so no amount
    of step discovery could surface them — whole track continuations
    (tier3/m-t3-02..07, m-pm-02..06) where content was written and the catalog
    stopped short.
    """
    from apps.forge_academy.content_loader import all_missions

    known = {m["slug"] for m in all_missions(discovered)}
    unreachable = sorted(
        slug for slug in discovered
        if slug not in known and discovered[slug]
    )
    # Anything left must be a deliberate exclusion (a contested track slot),
    # never an oversight.
    from apps.forge_academy.content_loader import _MISSION_SLUG_RE

    for slug in unreachable:
        m = _MISSION_SLUG_RE.match(slug)
        assert m, f"{slug} is unreachable and not an excluded track collision"
        collision = any(
            other != slug
            and (om := _MISSION_SLUG_RE.match(other))
            and (om.group("family"), om.group("num")) == (m.group("family"), m.group("num"))
            for other in catalog_slugs
        )
        assert collision, f"{slug} has content, no catalog entry, and no reason to be skipped"


def test_derived_missions_are_marked_as_derived(discovered):
    """A reviewer must be able to tell a derived entry from a curated one."""
    from apps.forge_academy.content_loader import discover_missions

    derived = discover_missions(discovered)
    assert derived, "expected authored-but-uncatalogued missions"
    assert all("derived" in m["source_credit"] for m in derived)


def test_derived_missions_never_collide_with_a_catalogued_track_slot(discovered, catalog_slugs):
    """Two missions at the same family+number would duplicate a track position."""
    from apps.forge_academy.content_loader import _MISSION_SLUG_RE, discover_missions

    def slot(slug):
        m = _MISSION_SLUG_RE.match(slug)
        return (m.group("family"), m.group("num")) if m else None

    taken = {s for s in (slot(x) for x in catalog_slugs) if s}
    for m in discover_missions(discovered):
        assert slot(m["slug"]) not in taken, (
            f"{m['slug']} would occupy a track slot already held by a catalogued mission"
        )


def test_derived_missions_carry_the_fields_the_insert_requires(discovered):
    from apps.forge_academy.content_loader import discover_missions

    required = ("slug", "title", "tagline", "tier", "topic", "role_filter",
                "mission_type", "xp_reward", "order_idx", "difficulty",
                "estimated_minutes", "prereqs", "source_credit")
    for m in discover_missions(discovered):
        for field in required:
            assert field in m, f"{m['slug']} is missing {field} for the fa_missions insert"
        assert m["tier"] in (1, 2, 3)
        assert m["mission_type"] in VALID_STEP_TYPES


def test_all_missions_is_a_superset_of_the_hand_written_catalog(discovered):
    from apps.forge_academy.content_loader import all_missions

    combined = {m["slug"] for m in all_missions(discovered)}
    assert combined >= {m["slug"] for m in BUILTIN_MISSIONS}
    assert len(combined) == len(all_missions(discovered)), "duplicate slug in the catalog"


def test_every_derived_mission_actually_renders_steps(discovered):
    from apps.forge_academy.content_loader import discover_missions

    for m in discover_missions(discovered):
        steps = steps_for(m["slug"], discovered)
        assert steps, f"{m['slug']} was catalogued but renders no steps"
