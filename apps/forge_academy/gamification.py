from __future__ import annotations
# CUI // SP-CTI
"""FORGE Academy gamification engine — XP, level-ups, achievements."""

import json
import logging
from datetime import datetime, timezone

from .constants import (
    ACHIEVEMENTS, XP_MULT_FIRST_TRY_NO_HINTS, XP_MULT_WITH_HINTS,
    XP_HINT_PENALTY, XP_SPEED_BONUS,
    XP_SPEED_BONUS_THRESHOLD_S, XP_DAILY_LOGIN_BASE, XP_STREAK_BONUS_PER_DAY,
    xp_to_next_level,
)
from . import db as _fadb
from .db import (
    update_user_xp, grant_achievement, get_user_achievements, get_user,
)

_log = logging.getLogger(__name__)


def projected_step_xp(base_xp: int, hints_used: int = 0,
                      step_type: str = "coding") -> int:
    """What a step will pay at `hints_used` hints. No side effects.

    aca-ux-02: the hint button was labelled "-10 XP" in two templates while the
    real first-hint cost on a 50 XP step was 48 (75 -> 27), because taking a hint
    both forfeits the 1.5x no-hints multiplier and applies 0.75x plus a per-hint
    penalty. Exposing the projection from the same code path award_step_xp uses is
    what stops the quoted price from drifting from the charged price again — do not
    reintroduce a hardcoded number in a template.
    """
    if step_type == "guided":
        return int(base_xp)
    if hints_used == 0:
        return int(base_xp * XP_MULT_FIRST_TRY_NO_HINTS)
    return max(0, int(base_xp * XP_MULT_WITH_HINTS) - hints_used * XP_HINT_PENALTY)


def award_step_xp(user_id: int, base_xp: int, hints_used: int = 0,
                  elapsed_seconds: int = None, step_type: str = "coding") -> dict:
    """Compute and award XP for a completed step. Returns event dict."""
    xp = projected_step_xp(base_xp, hints_used=hints_used, step_type=step_type)

    speed_bonus = 0
    if elapsed_seconds and elapsed_seconds <= XP_SPEED_BONUS_THRESHOLD_S and step_type == "coding":
        speed_bonus = XP_SPEED_BONUS
        xp += speed_bonus

    before = get_user(user_id)
    old_level = before["level"] if before else "recruit"
    result = update_user_xp(user_id, xp)
    leveled_up = result["level"] != old_level

    achievements = []
    # NOTE: a `slug = f"level_{result['level']}"` was computed here and never
    # used — ruff F841. Do not "fix" it by passing that slug to
    # grant_achievement(): none of the 29 defined achievements is a level_*
    # entry, and grant_achievement() returns None for an unknown slug, so the
    # call would no-op and the visible behaviour would be identical. The real
    # gap is that per-level achievements were designed and never defined.
    # Defining them (names, xp_bonus, rarity) is a content decision, so the dead
    # assignment is removed rather than dressed up as a working feature.
    # `leveled_up` is still reported in the return value, which is what the UI
    # actually consumes.
    if speed_bonus:
        ach = grant_achievement(user_id, "speed_demon")
        if ach:
            achievements.append(ach)

    return {
        "xp_earned": xp,
        "speed_bonus": speed_bonus,
        "hints_used": hints_used,
        "leveled_up": leveled_up,
        "new_level": result["level"],
        "new_xp": result["xp"],
        "achievements": achievements,
    }


def award_mission_xp(user_id: int, mission_xp: int, perfect: bool = False) -> dict:
    """Award XP on mission completion. perfect=True means no hints, all steps first try."""
    xp = int(mission_xp * 1.5) if perfect else mission_xp
    result = update_user_xp(user_id, xp)
    return {"xp_earned": xp, "perfect": perfect, "new_xp": result["xp"],
            "new_level": result["level"]}


def check_mission_achievements(user_id: int, mission_slug: str,
                                hints_total: int = 0,
                                aadc_score: int = 0) -> list[dict]:
    """Check and grant any mission-completion achievements."""
    unlocked = []
    earned_slugs = {a["slug"] for a in get_user_achievements(user_id)}

    for a in ACHIEVEMENTS:
        if a["slug"] in earned_slugs:
            continue
        c = json.loads(a["criteria_json"])
        ctype = c.get("type")

        if ctype == "mission_complete" and c.get("mission_slug") == mission_slug:
            granted = grant_achievement(user_id, a["slug"])
            if granted:
                update_user_xp(user_id, a["xp_bonus"])
                unlocked.append({**granted, "bonus_xp": a["xp_bonus"]})

        elif ctype == "aadc_owasp_clean" and aadc_score >= 100:
            granted = grant_achievement(user_id, a["slug"])
            if granted:
                update_user_xp(user_id, a["xp_bonus"])
                unlocked.append({**granted, "bonus_xp": a["xp_bonus"]})

        elif ctype == "aadc_score_gte" and aadc_score >= c.get("threshold", 80):
            granted = grant_achievement(user_id, a["slug"])
            if granted:
                update_user_xp(user_id, a["xp_bonus"])
                unlocked.append({**granted, "bonus_xp": a["xp_bonus"]})

    if hints_total == 0 and "no_hints_needed" not in earned_slugs:
        granted = grant_achievement(user_id, "no_hints_needed")
        if granted:
            update_user_xp(user_id, 200)
            unlocked.append({**granted, "bonus_xp": 200})

    return unlocked


def check_step_achievements(user_id: int, steps_completed: int) -> list[dict]:
    """Check step-count achievements (first spark, etc.)."""
    unlocked = []
    earned_slugs = {a["slug"] for a in get_user_achievements(user_id)}
    if steps_completed >= 1 and "first_spark" not in earned_slugs:
        granted = grant_achievement(user_id, "first_spark")
        if granted:
            update_user_xp(user_id, 50)
            unlocked.append({**granted, "bonus_xp": 50})
    return unlocked


def award_daily_login(user_id: int) -> dict | None:
    """Award daily login XP + streak bonus. Returns award dict or None if already awarded."""
    conn = _fadb.get_connection()
    today = datetime.now(timezone.utc).date().isoformat()
    existing = conn.execute(
        "SELECT id FROM fa_daily_logins WHERE user_id=? AND login_date=?",
        (user_id, today),
    ).fetchone()
    if existing:
        return None
    user = get_user(user_id)
    # aca-hyg-04: `.get("streak_days", 1)` looked like it defaulted to 1, but the key
    # always exists and the column defaults to 0 — so a learner on day one got
    # min(0,7)*10 = 0 bonus. Logging in IS day one, so floor at 1.
    stored = int((user or {}).get("streak_days") or 0)
    streak = max(1, stored)
    bonus = min(streak, 7) * XP_STREAK_BONUS_PER_DAY
    xp = XP_DAILY_LOGIN_BASE + bonus
    conn.execute(
        "INSERT INTO fa_daily_logins (user_id,login_date,xp_awarded) VALUES (?,?,?)",
        (user_id, today, xp),
    )
    conn.commit()
    update_user_xp(user_id, xp)
    return {"xp": xp, "streak": streak, "bonus": bonus}


def award_gameday_xp(user_id: int, tournament_id: str, final_rank: int, total_participants: int) -> dict:
    """Award XP and achievements based on GameDay tournament performance.

    Rank bonuses:
    - Top 10: 500 XP + gameday_champion badge
    - Top 50%: 200 XP
    - Participated: 100 XP baseline + arena_gladiator on first participation

    Returns: {"xp_awarded": int, "achievements_unlocked": list[str], "gameday_rank": int}
    """
    xp = 100  # participation baseline
    achievements: list[str] = []

    if final_rank <= 10:
        xp += 500
        achievements.append("gameday_champion")
    elif total_participants > 0 and final_rank <= total_participants * 0.5:
        xp += 200

    # First GameDay participation → arena_gladiator
    try:
        conn = _fadb.get_connection()
        prev = conn.execute(
            "SELECT COUNT(*) FROM fa_user_achievements WHERE user_id = ? AND achievement_slug = 'arena_gladiator'",
            (user_id,),
        ).fetchone()
        if prev and prev[0] == 0:
            achievements.append("arena_gladiator")
    except Exception:
        pass

    try:
        update_user_xp(user_id, xp)
    except Exception:
        pass

    unlocked = []
    for slug in achievements:
        try:
            granted = grant_achievement(user_id, slug)
            if granted:
                unlocked.append(slug)
        except Exception:
            pass

    return {
        "xp_awarded": xp,
        "achievements_unlocked": unlocked,
        "gameday_rank": final_rank,
    }


def get_gameday_seed_bonus(user_id: int) -> float:
    """Return a GameDay team seed bonus (0.0–0.25) based on Academy XP.

    L4+ (5000+ XP) earns the maximum bonus of 0.25.
    Used by GameDay team_runner to give higher-XP learners slightly better starting stats.
    """
    # aca-hyg-01: this had three faults that combined into dead code. It imported
    # get_connection from `icdev.tools.db.storage` while every other query in this
    # module uses `tools.db.storage` — a different module object under the shim, so
    # a test patching one never affected the other. It passed a literal `%s`
    # placeholder where the rest of the app uses `?` and lets the storage layer
    # translate. And the whole body sat under `except Exception: pass`, so either
    # fault silently returned 0.0 and the bonus was never applied to anyone.
    from tools.db.storage import get_connection

    try:
        row = get_connection().execute(
            "SELECT xp FROM fa_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    except Exception:
        # fga-fix-05 precedent: a swallowed warning never reaches a health check.
        _log.exception("gameday seed bonus lookup failed for user %s", user_id)
        return 0.0
    if not row:
        return 0.0
    xp = row[0] or 0
    return min(0.25, xp / 20000.0)


def get_user_stats(user_id: int) -> dict:
    """Full stats for the hub page — XP, level progress, achievements, streak."""
    user = get_user(user_id)
    if not user:
        return {}
    progress = xp_to_next_level(user["xp"])
    achievements = get_user_achievements(user_id)
    return {
        "user": user,
        "level_progress": progress,
        "achievements": achievements,
        "achievement_count": len(achievements),
    }
