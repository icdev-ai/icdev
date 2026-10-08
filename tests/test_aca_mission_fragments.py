# CUI // SP-CTI
"""Per-mission catalogue fragments (task-det-06d08d9b92).

Mission cards used to append to one shared list in content_loader.py, so every
pair of sibling cards conflicted on the same lines. A mission now ships its
catalogue entry as ``content/<tier>/<slug>/mission.json``.
"""
from __future__ import annotations

import json

from apps.forge_academy.content_loader import (
    BUILTIN_MISSIONS,
    CONTENT_ROOT,
    load_mission_fragments,
)

MIGRATED = {
    "m-aie-01-coding-harnesses",
    "m-aie-02-verification-harnesses",
    "m-aie-03-vibe-vs-engineering",
    "m-aie-04-capstone",
    "m12-model-serving",
    "m13-benchmarks-evals",
    "m-canvas-trio-01-design-canvases",
}


def test_shipped_fragments_load_and_reach_the_catalogue():
    shipped = {m["slug"] for m in load_mission_fragments()}
    assert MIGRATED <= shipped
    catalogued = {m["slug"] for m in BUILTIN_MISSIONS}
    assert shipped <= catalogued


def test_every_shipped_fragment_is_valid():
    paths = sorted(CONTENT_ROOT.rglob("mission.json"))
    assert len(paths) == len(load_mission_fragments()), (
        "a shipped mission.json was skipped as malformed -- see the ERROR log")


#: Entries still written inline in the BUILTIN_MISSIONS literal. May only go
#: DOWN: the first fix held only for the six missions it moved, and a new card
#: appending to the literal again reopens the collision the union rung refused
#: 21 times (task-det-06d08d9b92-r2). Ship a mission.json instead.
INLINE_MISSIONS_MAX = 88


def test_new_missions_ship_as_fragments_not_inline_entries():
    inline = len(BUILTIN_MISSIONS) - len(load_mission_fragments())
    assert inline <= INLINE_MISSIONS_MAX, (
        f"{inline} inline BUILTIN_MISSIONS entries (max {INLINE_MISSIONS_MAX}): add "
        "the new mission as content/<tier>/<slug>/mission.json, not to the list")


def test_catalogue_has_no_duplicate_slugs():
    slugs = [m["slug"] for m in BUILTIN_MISSIONS]
    assert len(slugs) == len(set(slugs))


def test_migrated_entries_keep_their_prereq_chain():
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    assert by_slug["m-aie-02-verification-harnesses"]["prereqs"] == [
        "m-aie-01-coding-harnesses"]
    assert by_slug["m-aie-04-capstone"]["prereqs"] == ["m-aie-03-vibe-vs-engineering"]


def _write(root, folder, payload):
    d = root / "tier1" / folder
    d.mkdir(parents=True)
    (d / "mission.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def test_a_bad_fragment_costs_only_itself(tmp_path):
    _write(tmp_path, "m-good", {"slug": "m-good", "title": "Good", "tier": 1,
                                "order_idx": 2})
    _write(tmp_path, "m-early", {"slug": "m-early", "title": "Early", "tier": 1,
                                 "order_idx": 1})
    _write(tmp_path, "m-broken", "{not json")
    _write(tmp_path, "m-list", [1, 2])
    _write(tmp_path, "m-untitled", {"slug": "m-untitled", "tier": 1})
    _write(tmp_path, "m-folder", {"slug": "m-other", "title": "X", "tier": 1})
    assert [m["slug"] for m in load_mission_fragments(tmp_path)] == ["m-early", "m-good"]


def test_missing_root_is_empty(tmp_path):
    assert load_mission_fragments(tmp_path / "absent") == []
