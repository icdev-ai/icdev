# CUI // SP-CTI
"""Level-up achievements were designed and never defined.

award_step_xp computed `slug = f"level_{result['level']}"` inside
`if leveled_up:` and then dropped it (ruff F841). The obvious reading is "a
grant call is missing" — it is not. No level_* achievement exists among the 29
defined, and grant_achievement() returns None for an unknown slug, so adding the
call would no-op and change nothing a user could see.

These tests pin the real state so the dead line is not reintroduced as a
"fix", and so that if someone later DOES define level achievements, the
assertion below fails and tells them to wire the grant.
"""
from __future__ import annotations

import inspect

from apps.forge_academy.constants import ACHIEVEMENTS


def _slugs() -> list:
    if isinstance(ACHIEVEMENTS, dict):
        return [str(k) for k in ACHIEVEMENTS]
    return [str(a.get("slug")) for a in ACHIEVEMENTS]


def test_no_level_achievements_are_defined():
    """The premise. If this fails, level achievements now exist — wire the grant."""
    level_slugs = [s for s in _slugs() if s.startswith("level_")]
    assert not level_slugs, (
        "level_* achievements are now defined; award_step_xp should grant them "
        f"instead of reporting leveled_up only: {level_slugs}"
    )


def test_grant_achievement_no_ops_on_an_unknown_slug():
    """Why adding the call would have been theatre."""
    src = inspect.getsource(
        __import__("apps.forge_academy.db", fromlist=["grant_achievement"]).grant_achievement
    )
    assert "if not ach:" in src and "return None" in src


def test_dead_slug_assignment_is_gone():
    """Check CODE, not comments — the explanatory note quotes the removed line."""
    from apps.forge_academy import gamification

    code_lines = [
        ln for ln in inspect.getsource(gamification.award_step_xp).splitlines()
        if not ln.strip().startswith("#")
    ]
    assert not any('slug = f"level_' in ln for ln in code_lines), (
        "the unused assignment is back"
    )


def test_level_up_is_still_reported_to_the_caller():
    """Removing the dead line must not drop the signal the UI consumes."""
    from apps.forge_academy import gamification

    src = inspect.getsource(gamification.award_step_xp)
    assert '"leveled_up": leveled_up' in src
    assert '"new_level": result["level"]' in src


def test_speed_demon_grant_is_untouched():
    """The adjacent working grant is the pattern; it must keep working."""
    from apps.forge_academy import gamification

    src = inspect.getsource(gamification.award_step_xp)
    assert 'grant_achievement(user_id, "speed_demon")' in src
    assert "speed_demon" in _slugs(), "the one achievement this path grants must exist"
