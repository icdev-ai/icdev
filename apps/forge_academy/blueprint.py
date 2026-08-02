# CUI // SP-CTI
"""FORGE Academy Flask blueprint — all /academy/* and /api/academy/* routes."""

from __future__ import annotations

import secrets

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for

from .auth import require_org_intel
from .constants import ROLES, TECHNICAL_ROLES, LEVELS, xp_to_next_level
from .db import (
    migrate, get_or_create_user, get_user, update_user_role, update_user_display_name, list_missions, get_mission_by_id, get_mission_progress, record_mission_attempt, complete_mission,
    get_step_progress, complete_step, user_progress_summary,
    tier_progress, is_tier_unlocked, resume_target, mission_step_progress,
    mission_prereq_state, earned_xp,
    get_user_achievements, grant_achievement,
    active_challenge_count, create_guild, join_guild, get_guild_stats, get_leaderboard, get_user_skills, unlock_skill,
    check_cert_eligibility, issue_certificate, get_user_certificates,
    verify_certificate_token,
    record_mission_competencies, get_competency_profile, competency_chain_status,
    seed_mission_ontology_mappings, backfill_user_competencies,
)
from .content_loader import get_mission_with_steps, seed_mission_catalog
from .gamification import (
    award_step_xp, award_mission_xp, check_mission_achievements,
    check_step_achievements, award_daily_login, get_user_stats,
)
from .integrations import (
    record_skill_usage, advance_learning_track, patterns_status,
    detect_role_from_answers, create_workflow,
)

bp = Blueprint("forge_academy", __name__)

_initialized = False
import threading as _threading
_init_lock = _threading.Lock()


@bp.app_context_processor
def _inject_fa_nav():
    """Inject FORGE Academy XP/rank badge data into every page template context."""
    try:
        from flask import session
        user_id = session.get("fa_user_id")
        if user_id:
            user = get_user(user_id)
            if user:
                # aca-int-07: through _level_ctx, not xp_to_next_level directly.
                # This is the nav bar's own rank, a second display the page-level fix
                # does not reach — it would have gone on showing the total-based rank
                # site-wide. Defined below; resolved at request time, not import.
                ctx = _level_ctx(user)
                return {
                    "fa_nav_user": user,
                    "fa_nav_level": ctx.get("level", "Recruit"),
                    "fa_nav_xp": user.get("xp", 0),
                    "fa_nav_xp_pct": ctx.get("percent", 0),
                }
    except Exception:
        pass
    return {}


# Health of the one-time init/seed. ``seed_mission_catalog()`` swallows its own
# failures (content_loader.py) and returns, so a silent seed failure would leave
# _initialized=True while serving an EMPTY catalog. We verify a non-empty catalog
# after seeding and, on any failure, log LOUD (error) + record a health flag
# instead of pretending init succeeded. (penta-aca-06)
_INIT_HEALTH: dict = {"initialized": False, "error": None, "mission_count": 0}


def get_init_health() -> dict:
    """Return a copy of the Academy init/seed health flag."""
    return dict(_INIT_HEALTH)


def _mission_count() -> int:
    try:
        from tools.db.storage import get_connection
        row = get_connection().execute(
            "SELECT COUNT(*) FROM fa_missions WHERE is_active=1"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _ensure_init():
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:  # double-checked locking
            return
        import logging
        log = logging.getLogger(__name__)
        try:
            migrate()
            seed_mission_catalog()
            # aca-trn-02: the ontology mapping runs HERE, after the catalog is
            # seeded — inside migrate() it ran against an empty fa_missions on a
            # fresh install and mapped nothing. Missions and steps must carry a
            # competency class before completing them can demonstrate anything,
            # so a mapping failure is an init failure, not a debug log.
            mapping = seed_mission_ontology_mappings()
            _INIT_HEALTH["ontology_mapping"] = mapping
            if mapping.get("errors"):
                raise RuntimeError(
                    "ontology mapping failed: " + "; ".join(mapping["errors"]))
            # Completions that predate a working recorder still deserve a record.
            _INIT_HEALTH["competency_backfill"] = backfill_user_competencies()
            count = _mission_count()
            _INIT_HEALTH["mission_count"] = count
            if count <= 0:
                # seed_mission_catalog() swallows its own exception, so an empty
                # catalog here means seeding failed silently — surface it.
                raise RuntimeError(
                    "mission catalog is empty after seeding — refusing to serve an empty Academy"
                )
        except Exception as exc:
            _INIT_HEALTH["initialized"] = False
            _INIT_HEALTH["error"] = str(exc)
            # Fail loud: error (not warning) with traceback, and DO NOT mark
            # initialized — the next request retries rather than serving empty.
            log.error("FORGE Academy init/seed failed: %s", exc, exc_info=True)
            return
        _INIT_HEALTH["initialized"] = True
        _INIT_HEALTH["error"] = None
        _initialized = True


def _fa_tenant_id() -> str | None:
    try:
        from tools.saas.auth.middleware import get_current_tenant_id
        return get_current_tenant_id()
    except Exception:
        return None


def _fa_email() -> str:
    user = getattr(g, "current_user", None)
    return (user.get("email", "") if user else "") or "guest@system.local"


def _fa_user() -> dict | None:
    email = _fa_email()
    try:
        return get_or_create_user(email, display_name=email.split("@")[0], tenant_id=_fa_tenant_id())
    except Exception:
        return None


def _level_ctx(fa_user: dict) -> dict:
    """Rank progress, computed from EARNED XP.

    aca-int-07: this read fa_user["xp"] — the running total, attendance included —
    and it is the only thing the UI ever consults for rank, in eight places. So
    after migration 316 corrected fa_users.level to 'recruit', every academy page
    went on rendering 'Operative': the stored column the migration fixed is not what
    the profile displays. The learner's total was 1715 of which 1465 was 41 daily
    logins, so the rank on screen was bought by showing up.

    The displayed XP total is deliberately unchanged — attendance is excluded from
    rank, not confiscated.

    aca-ux-07: that decision leaves two different numbers on screen — a total of
    1815 beside "250 XP to Operative" — and nothing said which was which. That is
    not a one-off migration artefact that a dismissible banner could cover: it is
    the permanent steady state for every learner who ever collects a daily login,
    so the split is returned here and labelled in the template instead. See
    docs/features/forge-academy-aca-ux-07-rank-xp-split.md.
    """
    if not fa_user:
        return dict(xp_to_next_level(0), total_xp=0, earned_xp=0, attendance_xp=0)
    total = fa_user.get("xp", 0) or 0
    try:
        xp = earned_xp(fa_user["id"])
    except Exception:
        # Before migration 315 there is no ledger to read. Falling back to the total
        # is the pre-int-07 behaviour, which is wrong but not broken — and it only
        # applies to a database that has not been migrated yet.
        xp = total
    # Derived by subtraction rather than by a second SUM over is_attendance=1, so it
    # cannot disagree with the total the page prints next to it. A negative value
    # would mean the ledger over-counts fa_users.xp; clamp so the UI never asserts
    # a nonsense split, and let the ledger reconciliation surface that separately.
    return dict(xp_to_next_level(xp), total_xp=total, earned_xp=xp,
                attendance_xp=max(0, total - xp))


# ---------------------------------------------------------------------------
# Legacy redirect — the registry historically declared url_prefix /forge-academy
# while every route is hardcoded to /academy, so the derived nav link 404'd.
# The registry now points at /academy; these 301s preserve any stale bookmarks
# to /forge-academy. (penta-aca-01)
# ---------------------------------------------------------------------------

@bp.route("/forge-academy")
@bp.route("/forge-academy/")
def _legacy_prefix_root():
    return redirect("/academy", code=301)


@bp.route("/forge-academy/<path:rest>")
def _legacy_prefix_path(rest):
    return redirect("/academy/" + rest, code=301)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.route("/academy")
def hub():
    _ensure_init()
    fa_user = _fa_user()
    if not fa_user:
        return redirect(url_for("forge_academy.profile"))
    daily = award_daily_login(fa_user["id"])
    stats = get_user_stats(fa_user["id"])
    # Honour ?role= like the browser, leaderboard and leaderboard API already
    # do. The hub was the only page ignoring it, so the "View as:" persona
    # dropdown appeared to do nothing here (fga-fix-06). Falling back to the
    # user's own role keeps the default view unchanged.
    role_filter = request.args.get("role", "")
    effective_role = role_filter or fa_user.get("role")
    missions = list_missions(role=effective_role, tier=None)[:6]
    level_ctx = _level_ctx(fa_user)

    # aca-ux-03: the hub listed the first six missions by order_idx and offered no
    # way back into work already under way. page.html also READ progress_map, which
    # this route never passed — so its `is defined` guard was always false and every
    # card showed "Start" no matter the learner's state.
    resume = resume_target(fa_user["id"])
    mission_ids = [m["id"] for m in missions]
    progress_map = {
        mid: (get_mission_progress(fa_user["id"], mid) or {}).get("status", "pending")
        for mid in mission_ids
    }
    step_progress = mission_step_progress(fa_user["id"], mission_ids)

    return render_template(
        "forge_academy/page.html",
        fa_user=fa_user,
        stats=stats,
        missions=missions,
        resume=resume,
        progress_map=progress_map,
        step_progress=step_progress,
        level_ctx=level_ctx,
        daily_login=daily,
        roles=ROLES,
        role_filter=role_filter,
        # Hide the Arena entry point while no challenge can exist. Seeding
        # filler to populate the page was explicitly rejected — fabricated data
        # presented as real is the failure mode PENTA removed from this surface.
        has_challenges=active_challenge_count() > 0,
    )


@bp.route("/academy/missions")
def missions_browser():
    _ensure_init()
    fa_user = _fa_user()
    tier = request.args.get("tier", type=int)
    topic = request.args.get("topic", "")
    mtype = request.args.get("type", "")
    role_filter = request.args.get("role", "")
    # When filtering by type, show all roles so guided missions are visible to any user.
    # Only narrow by user's role when browsing without a type constraint.
    effective_role = role_filter or (None if mtype else (fa_user.get("role") if fa_user else None))
    all_missions = list_missions(role=effective_role, tier=tier)
    if topic:
        all_missions = [m for m in all_missions if m.get("topic") == topic]
    if mtype:
        all_missions = [m for m in all_missions if m.get("mission_type") == mtype]
    progress_map = {}
    if fa_user:
        for m in all_missions:
            p = get_mission_progress(fa_user["id"], m["id"])
            progress_map[m["id"]] = p["status"] if p else "locked"
    # aca-ux-04: state the lock on the card, before the click. Computed once for all
    # tiers rather than per mission — tier_progress is two queries.
    tier_info = tier_progress(fa_user["id"]) if fa_user else {}
    for m in all_missions:
        m["is_locked"] = bool(
            fa_user and not tier_info.get(int(m.get("tier") or 1), {}).get("unlocked", True)
        )
    # aca-ux-06: 86 missions declare prerequisites and nothing showed them.
    prereq_state = mission_prereq_state(fa_user["id"], all_missions) if fa_user else {}
    return render_template(
        "forge_academy/missions.html",
        fa_user=fa_user,
        missions=all_missions,
        progress_map=progress_map,
        prereq_state=prereq_state,
        tier_info=tier_info,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
        roles=ROLES,
        active_tier=tier,
        active_topic=topic,
        active_type=mtype,
    )


@bp.route("/academy/mission/<slug>")
def mission_runner(slug):
    _ensure_init()
    fa_user = _fa_user()
    if not fa_user:
        return redirect(url_for("forge_academy.profile"))
    mission = get_mission_with_steps(slug)
    if not mission:
        return render_template("forge_academy/missions.html",
                               error=f"Mission '{slug}' not found.",
                               fa_user=fa_user, missions=[], progress_map={},
                               level_ctx=_level_ctx(fa_user), roles=ROLES,
                               active_tier=None, active_topic="", active_type=""), 404
    # aca-int-04: this GET used to record a mission start, bumping `attempts` and
    # forcing status back to 'in_progress' — so merely opening a page counted as
    # an attempt, and revisiting a completed mission withdrew the completion.
    # Reading a page is not progress; progress is recorded when work is submitted.
    # Tests assert no progress-mutating call appears in this function.
    progress = get_mission_progress(fa_user["id"], mission["id"])
    step_states = {}
    for step in mission.get("steps", []):
        sp = get_step_progress(fa_user["id"], step["id"])
        step_states[step["id"]] = sp["status"] if sp else "pending"
    level_ctx = _level_ctx(fa_user)
    from .grading import client_safe_steps

    # aca-ux-04: locked-but-readable. A learner may read a mission from a tier they
    # have not unlocked — curiosity is not something to punish — but submitting it
    # earns nothing (enforced in api_step_submit, not just hidden in the UI).
    tier_info = tier_progress(fa_user["id"])
    mission_tier = int(mission.get("tier") or 1)
    tier_state = tier_info.get(mission_tier, {})
    tier_locked = not tier_state.get("unlocked", True)
    gating_tier = tier_state.get("gating_tier")

    # aca-int-02/03: the template serialises this into page JavaScript. The raw step
    # rows carry the grading test and the answer key, so only the sanitised
    # projection may go into the page.
    # aca-trn-01: user-scoped, because a step with an item bank serves a per-learner,
    # per-attempt draw. Passing the user id here is what opens (or resumes) that
    # attempt; the answer key still never reaches the page.
    steps_client = client_safe_steps(mission.get("steps", []), fa_user["id"])

    # Which steps render the assessment pane instead of their type pane. Derived from
    # steps_client rather than re-queried, so the template's routing decision and the
    # payload it renders from cannot disagree: if open_attempt returned nothing —
    # every item retired, or the table unavailable — the step keeps its type pane
    # rather than showing an empty form no one can submit.
    assessed_step_ids = {
        s["id"] for s in steps_client
        if (s.get("assessment") or {}).get("items")
    }

    return render_template(
        "forge_academy/mission.html",
        fa_user=fa_user,
        mission=mission,
        tier_locked=tier_locked,
        tier_state=tier_state,
        gating_state=tier_info.get(gating_tier, {}) if gating_tier else {},
        steps_client=steps_client,
        assessed_step_ids=assessed_step_ids,
        progress=progress,
        step_states=step_states,
        level_ctx=level_ctx,
        roles=ROLES,
        TECHNICAL_ROLES=TECHNICAL_ROLES,
    )


@bp.route("/academy/skill-tree")
def skill_tree():
    _ensure_init()
    fa_user = _fa_user()
    from .constants import SKILL_NODES
    user_skills = set()
    if fa_user:
        user_skills = get_user_skills(fa_user["id"])
    return render_template(
        "forge_academy/skill_tree.html",
        fa_user=fa_user,
        skill_nodes=SKILL_NODES,
        user_skills=user_skills,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


@bp.route("/academy/guild")
def guild():
    _ensure_init()
    fa_user = _fa_user()
    guild_data = None
    if fa_user and fa_user.get("guild_id"):
        guild_data = get_guild_stats(fa_user["guild_id"], tenant_id=_fa_tenant_id())
    return render_template(
        "forge_academy/guild.html",
        fa_user=fa_user,
        guild=guild_data,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


@bp.route("/academy/leaderboard")
def leaderboard_page():
    _ensure_init()
    fa_user = _fa_user()
    period = request.args.get("period", "weekly")
    role_filter = request.args.get("role", "")
    rows = get_leaderboard(period=period, role=role_filter or None, limit=50, tenant_id=_fa_tenant_id())
    return render_template(
        "forge_academy/leaderboard.html",
        fa_user=fa_user,
        rows=rows,
        period=period,
        role_filter=role_filter,
        roles=ROLES,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


@bp.route("/academy/achievements")
def achievements():
    _ensure_init()
    fa_user = _fa_user()
    from .constants import ACHIEVEMENTS
    earned = {a["slug"]: a for a in (get_user_achievements(fa_user["id"]) if fa_user else [])}
    return render_template(
        "forge_academy/achievements.html",
        fa_user=fa_user,
        all_achievements=ACHIEVEMENTS,
        earned=earned,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


@bp.route("/academy/profile")
def profile():
    _ensure_init()
    fa_user = _fa_user()
    return render_template(
        "forge_academy/profile.html",
        fa_user=fa_user,
        roles=ROLES,
        levels=LEVELS,
        # aca-trn-02: the training record belongs where the learner's identity
        # is, not on a page of its own that nobody would find.
        competency_profile=get_competency_profile(fa_user["id"]) if fa_user else None,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


@bp.route("/academy/arena")
def arena():
    _ensure_init()
    fa_user = _fa_user()
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        challenges = [dict(r) for r in conn.execute(
            "SELECT * FROM fa_challenges WHERE ends_at > datetime('now') ORDER BY starts_at"
        ).fetchall()]
    except Exception:
        challenges = []
    return render_template(
        "forge_academy/arena.html",
        fa_user=fa_user,
        challenges=challenges,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
        TECHNICAL_ROLES=TECHNICAL_ROLES,
    )


@bp.route("/academy/workflow-builder")
def workflow_builder_page():
    _ensure_init()
    fa_user = _fa_user()
    # Distinguish "no patterns configured" from "pattern source unavailable"
    # so the page cannot present a broken dependency as an empty catalogue.
    pattern_state = patterns_status()
    return render_template(
        "forge_academy/workflow_builder.html",
        fa_user=fa_user,
        patterns=pattern_state["patterns"],
        patterns_available=pattern_state["available"],
        patterns_error=pattern_state["error"],
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@bp.route("/api/academy/user/setup", methods=["POST"])
def api_user_setup():
    _ensure_init()
    data = request.get_json(silent=True) or {}
    email = _fa_email()
    display_name = data.get("display_name", email.split("@")[0])
    role = data.get("role", "devops")
    # tenant_id MUST match what _fa_user() reads with, or setup writes a row in
    # one tenant while every page reads another — the saved profile appears to
    # vanish and an orphan row accumulates (fga-fix-03).
    fa_user = get_or_create_user(email, display_name=display_name,
                                 tenant_id=_fa_tenant_id())
    # get_or_create_user only applies display_name on INSERT; persist it
    # explicitly so a returning user's change is not dropped.
    update_user_display_name(fa_user["id"], display_name)
    if role:
        update_user_role(fa_user["id"], role)
    wizard_result = {}
    if data.get("wizard_answers"):
        wizard_result = detect_role_from_answers(data["wizard_answers"])
    return jsonify({"ok": True, "user_id": fa_user["id"], "role": role,
                    "wizard": wizard_result})


@bp.route("/api/academy/progress")
def api_progress():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 404
    summary = user_progress_summary(fa_user["id"])
    stats = get_user_stats(fa_user["id"])
    return jsonify({**summary, "stats": stats})


@bp.route("/api/academy/code/run", methods=["POST"])
def api_code_run():
    """Run a learner's code against the step's OWN stored test.

    aca-int-02: this used to take `test_code` straight from the request body,
    which mission.html helpfully posted back from the step payload — so the
    person being graded supplied the test. The step id is now the only thing the
    caller controls; the test comes from test_code_path on the step row.
    """
    from .grading import run_step_code

    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    step_id = data.get("step_id")
    if not step_id:
        return jsonify({"error": "step_id required", "passed": False,
                        "stdout": "", "stderr": "step_id required"}), 400
    return jsonify(run_step_code(step_id, code))


@bp.route("/api/academy/step/submit", methods=["POST"])
def api_step_submit():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    from .grading import grade_step, mission_is_complete, mission_xp_reward

    data = request.get_json(silent=True) or {}
    step_id = data.get("step_id")
    if not step_id:
        return jsonify({"error": "step_id required"}), 400

    # aca-int-01: the ONLY things the client may influence are which step it is
    # answering and what it submitted. The verdict, the payout and whether the
    # mission is finished are all derived server-side. `passed`, `score`,
    # `base_xp`, `mission_xp`, `mission_complete`, `mission_id`, `step_type` and
    # `skill_tag` used to be read from this body; every one of them was forgeable.
    submission = data.get("submission", "")
    chosen_option = data.get("chosen_option")
    # aca-trn-01: item_key -> DISPLAYED option index. Meaningless without the server's
    # served_json, which is what makes the option order in the DOM useless.
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    elapsed_s = data.get("elapsed_seconds")
    # aca-int-06: the hint count comes from fa_step_progress, where the hint route
    # recorded it. It used to be read from this body, and the browser zeroed its own
    # counter on every step navigation - so hints were laundered by clicking away and
    # back, which also restored the 1.5x "perfect" bonus and no_hints_needed.
    stored_hints_used = int(
        (get_step_progress(fa_user["id"], step_id) or {}).get("hints_used", 0) or 0
    )
    hints_used = max(0, stored_hints_used)

    # aca-trn-01: the attempt limit is checked BEFORE grading. Grading first and
    # discarding the verdict would let a learner burn attempts to enumerate the item
    # bank, which is exactly what a summative cap exists to prevent.
    from .assessment import attempt_state

    gate = attempt_state(fa_user["id"], step_id)
    if not gate["allowed"] and gate["reason"] == "attempts_exhausted":
        return jsonify({
            "ok": True,
            "passed": False,
            "assessed": True,
            "status": "attempts_exhausted",
            "reason": "attempts_exhausted",
            "policy": gate["policy"],
            "attempts_used": gate["attempts_used"],
            "attempts_remaining": 0,
            "max_attempts": gate["max_attempts"],
            "stderr": (
                f"You have used all {gate['max_attempts']} attempts on this "
                "assessment. An instructor can reset it for you."
            ),
        })

    verdict = grade_step(step_id, submission, chosen_option=chosen_option,
                         answers=answers, user_id=fa_user["id"])
    step = verdict.get("step")
    if step is None:
        return jsonify({"error": "unknown step", "passed": False}), 404

    mission_id = step["mission_id"]
    step_type = (step.get("step_type") or "coding").strip().lower()
    passed = verdict["passed"]

    # aca-ux-04: the tier gate is enforced HERE, where credit is granted, not only
    # in the template. A locked mission stays readable and runnable; it just cannot
    # pay out or record completion.
    _m = get_mission_by_id(mission_id)
    if _m and not is_tier_unlocked(fa_user["id"], int(_m.get("tier") or 1)):
        return jsonify({
            "ok": True,
            "passed": False,
            "assessed": verdict["assessed"],
            "status": "locked",
            "reason": "tier_locked",
            "stderr": (
                f"Tier {_m.get('tier')} is not unlocked yet, so this step cannot be "
                "credited. You can keep reading and experimenting."
            ),
        })

    # aca-int-04: submitting work is what puts a mission in progress and what
    # counts as an attempt — not opening its page.
    record_mission_attempt(fa_user["id"], mission_id)
    status = complete_step(fa_user["id"], step_id, submission=submission,
                           passed=passed, hints_used=hints_used,
                           # aca-trn-01: the real percentage. An item-scored step can
                           # now record "67", which no longer rounds to a pass.
                           score=verdict.get("score"))

    resp = {
        "ok": True,
        "passed": passed,
        "assessed": verdict["assessed"],
        "status": status,
        "score": verdict.get("score"),
        "reason": verdict.get("reason", ""),
        "stdout": verdict.get("stdout", ""),
        "stderr": verdict.get("stderr", ""),
        "explanation": verdict.get("explanation", ""),
        "correct_option": verdict.get("correct_option"),
    }
    # Item-scored steps report per-item feedback and the attempt budget. Released
    # only here, with the attempt closed - before that this IS the answer key.
    for key in ("items", "correct", "total", "pass_threshold_pct",
                "attempts_used", "attempts_remaining"):
        if verdict.get(key) is not None:
            resp[key] = verdict[key]
    if not passed:
        # No credit for a failed or ungradeable submission. The learner keeps the
        # feedback and can try again.
        return jsonify(resp)

    summary = user_progress_summary(fa_user["id"])
    xp_event = award_step_xp(fa_user["id"], verdict["xp_base"], hints_used=hints_used,
                              elapsed_seconds=elapsed_s, step_type=step_type,
                              step_id=step_id)
    step_ach = check_step_achievements(fa_user["id"], summary.get("steps_completed", 0))
    xp_event["achievements"] = xp_event.get("achievements", []) + step_ach
    resp["xp_event"] = xp_event

    email = _fa_email()
    skill_tag = (step.get("skill_tag") or "").strip()
    if skill_tag:
        record_skill_usage(email, skill_tag)
        unlock_skill(fa_user["id"], skill_tag)

    # aca-int-01: completion is derived from recorded step progress, not from the
    # client's "this was the last step".
    if mission_is_complete(fa_user["id"], mission_id):
        complete_mission(fa_user["id"], mission_id, score=100)
        mission_xp_event = award_mission_xp(fa_user["id"], mission_xp_reward(mission_id),
                                             perfect=(hints_used == 0),
                                             mission_id=mission_id)
        mission = get_mission_by_id(mission_id)
        mission_slug = (mission or {}).get("slug", "")
        mission_ach = check_mission_achievements(fa_user["id"], mission_slug, hints_used)
        advance_learning_track(email)
        resp["mission_complete"] = True
        resp["mission_xp"] = mission_xp_event
        resp["mission_achievements"] = mission_ach
        # aca-trn-02: recording the competency is part of completing the mission,
        # not a side effect of it. The outcome goes back to the client with the
        # rest of the completion, so a chain that records nothing is visible in
        # the response instead of only in a log nobody reads.
        try:
            competency = record_mission_competencies(
                user_id=fa_user["id"], mission_id=mission_id,
                score=100, hints_used=hints_used)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "competency recording failed for mission %s", mission_id)
            competency = {"recorded": [], "classes": [], "errors": [str(exc)],
                          "unmapped": False}
        resp["competencies"] = competency
        if competency.get("errors") or competency.get("unmapped"):
            import logging
            logging.getLogger(__name__).error(
                "competency chain incomplete for mission %s: unmapped=%s errors=%s",
                mission_id, competency.get("unmapped"), competency.get("errors"))

    return jsonify(resp)


@bp.route("/api/academy/assessment/coverage")
def api_assessment_coverage():
    """How much of the catalogue is actually graded (aca-trn-01).

    Exists so the shortfall is a NUMBER on an endpoint rather than a claim in a
    document. 94% of steps were passive when the assessment model was specified;
    this reports what that figure is today instead of letting the next audit
    rediscover it.
    """
    _ensure_init()
    from .assessment import coverage_report

    return jsonify(coverage_report())


@bp.route("/api/academy/step/design-assess", methods=["POST"])
def api_step_design_assess():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    from .verifier import verify_step
    from .gamification import award_step_xp, check_step_achievements, check_mission_achievements, award_mission_xp
    # record_skill_usage + advance_learning_track live in .integrations (already
    # imported at module top), NOT in .db / a .learning_track module — importing
    # them here 500s the whole route on every real submission (penta-aca-07 fix).
    from .db import complete_step, complete_mission, user_progress_summary, unlock_skill

    data = request.get_json(silent=True) or {}
    step_id = data.get("step_id")
    mission_id = data.get("mission_id")
    design_id = data.get("design_id", "")
    required_checks = data.get("required_checks", [])
    min_score = int(data.get("min_score", 70))
    hints_used = int(data.get("hints_used", 0))
    base_xp = int(data.get("base_xp", 100))

    # verify_step signature is (user_id, step_type, verification_data). penta-fix-02:
    # the first two args were SWAPPED here, and the route read result["evidence"] as
    # a dict though verify_step ALWAYS returns "evidence" as a string and never a
    # "check_results" list — either mistake 500'd the route on every call.
    result = verify_step(fa_user["id"], "aadc_design_compliant", {
        "design_id": design_id,
        "required_checks": required_checks,
        "min_score": min_score,
    })

    # verify_step returns {passed, evidence(str), score?, failed_checks?}. Derive
    # checks_passed from the requested required_checks minus whatever came back
    # failing, preserving the route's list-of-check-ids response contract.
    failed_checks = result.get("failed_checks", [])
    checks_passed = [c for c in required_checks if c not in failed_checks]
    resp = {
        "passed": result.get("passed", False),
        "score": result.get("score", 0),
        "checks_passed": checks_passed,
        "failed_checks": failed_checks,
        "evidence": result.get("evidence", ""),
    }

    if result.get("passed"):
        complete_step(fa_user["id"], step_id, submission=design_id, passed=True, hints_used=hints_used)
        summary = user_progress_summary(fa_user["id"])
        xp_event = award_step_xp(fa_user["id"], base_xp, hints_used=hints_used,
                                  step_type="design", step_id=step_id)
        step_ach = check_step_achievements(fa_user["id"], summary.get("steps_completed", 0))
        xp_event["achievements"] = xp_event.get("achievements", []) + step_ach

        email = _fa_email()
        skill_tag = data.get("skill_tag", "")
        if skill_tag:
            record_skill_usage(email, skill_tag)
            unlock_skill(fa_user["id"], skill_tag)

        resp["xp_event"] = xp_event

        if mission_id and data.get("mission_complete"):
            complete_mission(fa_user["id"], mission_id, score=result.get("score", 100))
            mission_xp_event = award_mission_xp(fa_user["id"], int(data.get("mission_xp", 400)),
                                                mission_id=mission_id,
                                                 perfect=(hints_used == 0))
            mission_ach = check_mission_achievements(
                fa_user["id"], data.get("mission_slug", ""), hints_used,
                aadc_score=result.get("score", 0),
            )
            advance_learning_track(email)
            resp["mission_xp"] = mission_xp_event
            resp["mission_achievements"] = mission_ach

    return jsonify(resp)


@bp.route("/api/academy/step/configure", methods=["POST"])
def api_step_configure():
    from .configurator import dispatch_configure
    data = request.get_json(silent=True) or {}
    result = dispatch_configure(data)
    return jsonify(result)


@bp.route("/api/academy/coach/hint", methods=["POST"])
def api_coach_hint():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    from .ai_coach import get_hint
    data = request.get_json(silent=True) or {}
    context = data.get("context", "")
    question = data.get("question", "")
    mission_slug = data.get("mission_slug", "")
    design_id = data.get("design_id", "")
    debate_mode = data.get("debate_mode", False)
    chain_mode = "cod" if debate_mode else ""
    hint = get_hint(
        question=question,
        context=context,
        mission_slug=mission_slug,
        design_id=design_id,
        chain_mode=chain_mode,
    )
    # _md_to_html allowlist-sanitizes its output (penta-aca-06), so this LLM
    # coach hint is safe to return as HTML — <script>/inline handlers render inert.
    from .content_loader import _md_to_html
    hint_html = _md_to_html(hint)

    # aca-int-06 / aca-ux-02: the hint used to be charged TWICE - an instant XP
    # deduction here, plus the submit path separately applying XP_MULT_WITH_HINTS
    # (0.75 instead of 1.5) minus XP_HINT_PENALTY per hint. On a 50 XP step one hint
    # cost 10 up front and then paid 27 instead of 75: 58 XP total, against a button
    # quoting only the flat penalty. The submit-time multiplier is now the single
    # pricing mechanism - it is what constants.py documents, and it does not charge
    # a learner who reads a hint and never submits.
    #
    # The count is recorded server-side because it used to live only in the browser,
    # where goStep() reset it to 0 on every sidebar click.
    from .db import record_hint
    from .gamification import projected_step_xp
    from .grading import _load_step

    step_id = data.get("step_id")
    hints_used = 0
    projected = None
    step = _load_step(step_id) if step_id else None
    # aca-hyg-04: honour hint_allowed server-side. Otherwise the column is decoration
    # in the same way tier_unlocked was before aca-ux-04 — a flag nothing enforces.
    if step is not None and not _step_allows_hint(step):
        return jsonify({
            "hint": "",
            "error": "hints_not_available",
            "detail": "This step type does not offer hints.",
            "hints_used": 0,
            "projected": None,
        }), 400
    if step is not None:
        hints_used = record_hint(fa_user["id"], step["id"])
        base_xp = int(step.get("xp_partial") or 0)
        step_type = (step.get("step_type") or "coding").strip().lower()
        projected = {
            "xp_if_you_stop_now": projected_step_xp(
                base_xp, hints_used=hints_used, step_type=step_type),
            "xp_without_hints": projected_step_xp(
                base_xp, hints_used=0, step_type=step_type),
            "hints_used": hints_used,
        }

    return jsonify({
        "hint": hint_html,
        "chain_mode": chain_mode,
        "hints_used": hints_used,
        "projected": projected,
    })


@bp.route("/api/academy/guild/create", methods=["POST"])
def api_guild_create():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    description = data.get("description", "")
    if not name:
        return jsonify({"error": "name required"}), 400
    guild = create_guild(name=name, description=description,
                         invite_code=secrets.token_urlsafe(6),
                         created_by=fa_user["id"],
                         tenant_id=_fa_tenant_id())
    # Report the code that was actually STORED, not the one we proposed —
    # create_guild uppercases it to match join_guild's lookup, so echoing the
    # local variable would hand the user an invite that never resolves.
    return jsonify({"ok": True, "guild": guild,
                    "invite_code": guild.get("invite_code")})


@bp.route("/api/academy/guild/join", methods=["POST"])
def api_guild_join():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    data = request.get_json(silent=True) or {}
    invite_code = data.get("invite_code", "").strip()
    result = join_guild(user_id=fa_user["id"], invite_code=invite_code,
                        tenant_id=_fa_tenant_id())
    if result is None:
        # aca-trn-04: this returned `null` with a 200, so an unresolvable invite
        # code was indistinguishable from a successful join to any caller that
        # checked the status.
        return jsonify({"ok": False, "error": "invite code not found"}), 404
    return jsonify(result)


@bp.route("/api/academy/guild/<int:guild_id>")
def api_guild_stats(guild_id):
    # aca-trn-04: scoped to the caller's tenant. The id comes from the URL and
    # this route has no other authorisation, so unscoped it enumerated every
    # learner's display name and XP across every tenant.
    stats = get_guild_stats(guild_id, tenant_id=_fa_tenant_id())
    if stats is None:
        # aca-hyg-03: used to 200 with an empty member list, so a client could not
        # tell a missing guild from an empty one.
        return jsonify({"error": "guild not found", "guild_id": guild_id}), 404
    return jsonify(stats)


# Fields a learner's browser legitimately needs about a mission. Everything else in
# fa_missions is internal bookkeeping (aca-hyg-03).
_LEARNER_MISSION_FIELDS = (
    "id", "slug", "title", "tagline", "tier", "topic", "mission_type",
    "xp_reward", "difficulty", "estimated_minutes", "is_available",
)


def _learner_mission_view(mission: dict) -> dict:
    """Project a mission row down to the fields a client may see."""
    return {k: mission.get(k) for k in _LEARNER_MISSION_FIELDS if k in mission}


def _step_allows_hint(step: dict) -> bool:
    """Whether this step permits a coach hint (aca-hyg-04).

    Prefers the stored hint_allowed column and falls back to the step type, so rows
    seeded before the column meant anything still behave sensibly.
    """
    from .content_loader import hint_allowed_for

    stored = step.get("hint_allowed")
    if stored is not None:
        return bool(stored)
    return hint_allowed_for(step.get("step_type"))


@bp.route("/api/academy/leaderboard")
def api_leaderboard():
    period = request.args.get("period", "weekly")
    role = request.args.get("role")
    rows = get_leaderboard(period=period, role=role, limit=100, tenant_id=_fa_tenant_id())
    return jsonify({"rows": rows, "period": period})


@bp.route("/api/academy/challenge/enter", methods=["POST"])
def api_challenge_enter():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    data = request.get_json(silent=True) or {}
    challenge_id = data.get("challenge_id")
    submission = data.get("submission", "")
    if not challenge_id:
        return jsonify({"error": "challenge_id required"}), 400
    from tools.db.storage import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO fa_challenge_entries "
        "(challenge_id,user_id,submission,score,submitted_at) VALUES (%s,%s,%s,%s,datetime('now'))",
        (challenge_id, fa_user["id"], submission, 0),
    )
    conn.commit()
    return jsonify({"ok": True, "status": "submitted"})


@bp.route("/api/academy/workflow/submit", methods=["POST"])
def api_workflow_submit():
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    data = request.get_json(silent=True) or {}
    name = data.get("name", "My Workflow")
    canvas_json = data.get("canvas", {})
    email = _fa_email()
    result = create_workflow(name=name, canvas_json=canvas_json, user_email=email)
    if result.get("status") == "ok":
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO fa_workflow_submissions "
            "(user_id,design_id,score,ai_feedback,tier,submitted_at) VALUES (%s,%s,%s,%s,%s,datetime('now'))",
            (fa_user["id"], result.get("design_id", ""), 80, result.get("goal_md", "")[:500], 1),
        )
        conn.commit()
        award_step_xp(fa_user["id"], 150, step_type="guided")
        grant_achievement(fa_user["id"], "workflow_author")
    return jsonify(result)


# ---------------------------------------------------------------------------
# Academy Oracle routes  (ep11)
# ---------------------------------------------------------------------------

@bp.route("/academy/oracle")
@require_org_intel  # org-wide predictive intelligence — leadership tier only (penta-aca-06)
def oracle_page():
    _ensure_init()
    # Single shared DB connection for all three reads — each new canvas
    # connection costs ~2s in the dashboard, so opening one instead of three
    # cuts the page from ~6s of connection overhead to ~2s.
    from .oracle.db import page_payload
    data = page_payload(prediction_limit=200)
    return render_template(
        "forge_academy/oracle.html",
        stats=data["stats"],
        predictions=data["predictions"],
        convergence=data["convergence"],
    )


@bp.route("/api/academy/oracle/predictions")
@require_org_intel
def api_oracle_predictions():
    _ensure_init()
    from .oracle.db import list_predictions
    lens_id = request.args.get("lens_id")
    severity = request.args.get("severity")
    outcome = request.args.get("outcome", "pending")
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = list_predictions(lens_id=lens_id, severity=severity, outcome=outcome, limit=limit)
    return jsonify({"predictions": rows, "count": len(rows)})


@bp.route("/api/academy/oracle/summary")
@require_org_intel
def api_oracle_summary():
    _ensure_init()
    from .oracle.db import summary_stats
    return jsonify(summary_stats())


@bp.route("/api/academy/oracle/run", methods=["POST"])
@require_org_intel
def api_oracle_run():
    _ensure_init()
    from .oracle.runner import AcademyOracleRunner
    result = AcademyOracleRunner().run()
    return jsonify({
        "ok": True,
        "persisted": result["persisted_count"],
        "convergence_events": len(result["convergence"]),
        "total_predictions": len(result["predictions"]),
    })


@bp.route("/api/academy/oracle/prediction/<pred_id>/outcome", methods=["POST"])
@require_org_intel
def api_oracle_update_outcome(pred_id: str):
    _ensure_init()
    from .oracle.db import update_prediction_outcome
    data = request.get_json(silent=True) or {}
    outcome = data.get("outcome", "actioned")
    if outcome not in ("actioned", "dismissed", "pending"):
        return jsonify({"error": "invalid outcome"}), 400
    update_prediction_outcome(pred_id, outcome)
    return jsonify({"ok": True})


@bp.route("/academy/patterns")
def pattern_library():
    _ensure_init()
    from .patterns import INJECTION_PATTERNS
    phase_filter = request.args.get("phase", "")
    approach_filter = request.args.get("approach", "")
    patterns = INJECTION_PATTERNS
    if phase_filter:
        patterns = [p for p in patterns if p.get("phase_tag") == phase_filter]
    if approach_filter:
        patterns = [p for p in patterns if approach_filter.lower() in p.get("approach", "").lower()]
    return render_template(
        "forge_academy/pattern_library.html",
        patterns=patterns,
        all_patterns=INJECTION_PATTERNS,
        phase_filter=phase_filter,
        approach_filter=approach_filter,
    )


@bp.route("/academy/patterns/<pattern_id>")
def pattern_detail(pattern_id: str):
    _ensure_init()
    from .patterns import PATTERN_BY_ID, INJECTION_PATTERNS
    pattern = PATTERN_BY_ID.get(pattern_id)
    if not pattern:
        return redirect(url_for("forge_academy.pattern_library"))
    return render_template(
        "forge_academy/pattern_detail.html",
        pattern=pattern,
        all_patterns=INJECTION_PATTERNS,
    )


@bp.route("/academy/org-readiness")
@require_org_intel  # org-wide readiness/cohort intelligence — leadership tier only (penta-aca-06)
def org_readiness_page():
    _ensure_init()
    try:
        from apps.innovation.reporting_engine import compute_org_readiness
        readiness = compute_org_readiness()
    except Exception:
        readiness = {
            "score": 0, "tier": "red", "tier_color": "#FF4444",
            "guidance": "Readiness data unavailable — ensure FORGE IGNITE is enabled.",
            "components": {}, "cohort": {}, "skill_gaps": [],
        }
    return render_template("forge_academy/org_readiness.html", readiness=readiness)


@bp.route("/api/academy/org-readiness")
@require_org_intel
def api_org_readiness():
    _ensure_init()
    try:
        from apps.innovation.reporting_engine import compute_org_readiness
        return jsonify(compute_org_readiness())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Instructor / cohort workflow  (aca-trn-04)
#
# Every route here wears @require_org_intel — the SAME admin/pm/isso gate Org
# Readiness and the Oracle already use. A second RBAC model for instructors was
# explicitly not built: two authorisation systems over one dataset is how a
# surface ends up authorised in one place and open in the other.
# ---------------------------------------------------------------------------

def _instructor_identity() -> tuple[str, str]:
    """(actor, role) for the audit trail. Never anonymous — the gate guarantees a user."""
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        return (user.get("email") or user.get("username") or "unknown",
                str(user.get("role") or ""))
    return (str(getattr(user, "email", "") or getattr(user, "username", "") or "unknown"),
            str(getattr(user, "role", "") or ""))


@bp.route("/academy/instructor")
@require_org_intel
def instructor_page():
    _ensure_init()
    from . import instructor as _inst
    tenant_id = _fa_tenant_id()
    learners = _inst.roster(tenant_id)
    assignments = _inst.list_assignments(tenant_id)
    return render_template(
        "forge_academy/instructor.html",
        learners=learners,
        assignments=assignments,
        missions=list_missions(tier=None),
        roles=ROLES,
        audit=_inst.audit_trail(tenant_id, limit=25),
        verdicts=sorted(_inst.REVIEW_VERDICTS),
    )


@bp.route("/academy/instructor/learner/<int:user_id>")
@require_org_intel
def instructor_learner_page(user_id: int):
    _ensure_init()
    from . import instructor as _inst
    tenant_id = _fa_tenant_id()
    learner = _inst.get_learner(user_id, tenant_id)
    if not learner:
        # Same answer for "not in this tenant" as for "does not exist".
        return redirect(url_for("forge_academy.instructor_page"))
    return render_template(
        "forge_academy/instructor_learner.html",
        learner=learner,
        summary=user_progress_summary(user_id, tenant_id),
        assignments=_inst.list_assignments(tenant_id, user_id=user_id),
        submissions=_inst.learner_submissions(user_id),
        evidence=_inst.learner_evidence(user_id),
        reviews=_inst.learner_reviews(user_id),
        roles=ROLES,
        verdicts=sorted(_inst.REVIEW_VERDICTS),
    )


@bp.route("/api/academy/instructor/roster")
@require_org_intel
def api_instructor_roster():
    _ensure_init()
    from . import instructor as _inst
    return jsonify({"learners": _inst.roster(_fa_tenant_id(),
                                             request.args.get("role") or None)})


@bp.route("/api/academy/instructor/assignments")
@require_org_intel
def api_instructor_assignments():
    _ensure_init()
    from . import instructor as _inst
    user_id = request.args.get("user_id", type=int)
    return jsonify({"assignments": _inst.list_assignments(
        _fa_tenant_id(), user_id=user_id)})


@bp.route("/api/academy/instructor/assign", methods=["POST"])
@require_org_intel
def api_instructor_assign():
    _ensure_init()
    from . import instructor as _inst
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    actor, actor_role = _instructor_identity()
    try:
        assignment = _inst.create_assignment(
            assigned_by=actor,
            actor_role=actor_role,
            assignment_type=data.get("assignment_type", "mission"),
            mission_id=data.get("mission_id"),
            track_key=data.get("track_key"),
            target_type=data.get("target_type", "learner"),
            target_user_id=data.get("target_user_id"),
            target_role=data.get("target_role"),
            due_at=data.get("due_at"),
            note=data.get("note", ""),
            tenant_id=_fa_tenant_id(),
        )
    except _inst.AssignmentError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "assignment": assignment})


@bp.route("/api/academy/instructor/assignment/<int:assignment_id>/cancel", methods=["POST"])
@require_org_intel
def api_instructor_cancel(assignment_id: int):
    _ensure_init()
    from . import instructor as _inst
    actor, actor_role = _instructor_identity()
    ok = _inst.cancel_assignment(assignment_id, actor=actor, actor_role=actor_role,
                                 tenant_id=_fa_tenant_id())
    if not ok:
        return jsonify({"ok": False, "error": "assignment not found"}), 404
    return jsonify({"ok": True, "assignment_id": assignment_id})


@bp.route("/api/academy/instructor/review", methods=["POST"])
@require_org_intel
def api_instructor_review():
    _ensure_init()
    from . import instructor as _inst
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    actor, actor_role = _instructor_identity()
    try:
        result = _inst.record_review(
            user_id=int(data.get("user_id") or 0),
            verdict=data.get("verdict", ""),
            reviewer=actor,
            actor_role=actor_role,
            mission_id=data.get("mission_id"),
            step_id=data.get("step_id"),
            assignment_id=data.get("assignment_id"),
            override_score=data.get("override_score"),
            comment=data.get("comment", ""),
            tenant_id=_fa_tenant_id(),
        )
    except _inst.AssignmentError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "user_id must be a learner id"}), 400
    return jsonify(result)


@bp.route("/api/academy/instructor/audit")
@require_org_intel
def api_instructor_audit():
    _ensure_init()
    from . import instructor as _inst
    limit = min(request.args.get("limit", 50, type=int) or 50, 200)
    return jsonify({"audit": _inst.audit_trail(_fa_tenant_id(), limit=limit)})


@bp.route("/api/academy/health")
def api_academy_health():
    """Surface the Academy init/seed health flag (penta-aca-06).

    Returns 200 when the mission catalog seeded successfully, 503 when init/seed
    failed — so an empty catalog is observable instead of silently served.

    aca-trn-02 adds the competency chain to the same probe. A training record
    that quietly writes nothing looks exactly like one with nothing to say yet,
    which is how it stayed empty: 503 when the chain is stalled (missions have
    been completed and produced no competency at all), and unmapped counts in
    the body either way so partial catalog coverage is visible before a learner
    finishes one of the missions it is missing.
    """
    _ensure_init()
    health = get_init_health()
    chain = competency_chain_status()
    health["competency_chain"] = chain
    ok = health.get("initialized") and not chain.get("stalled") and chain.get("ok", True)
    return jsonify(health), (200 if ok else 503)


@bp.route("/api/academy/competencies")
def api_competencies():
    """The signed-in learner's competency profile."""
    _ensure_init()
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 404
    return jsonify(get_competency_profile(fa_user["id"]))


# ---------------------------------------------------------------------------
# Certification routes  (Phase 5)
# ---------------------------------------------------------------------------

@bp.route("/academy/certificate/<cert_key>")
def certificate_page(cert_key: str):
    _ensure_init()
    from .constants import CERT_BY_KEY, CERT_TIERS
    cert_def = CERT_BY_KEY.get(cert_key)
    if not cert_def:
        return redirect(url_for("forge_academy.hub"))
    fa_user = _fa_user()
    eligibility = check_cert_eligibility(fa_user["id"], cert_key) if fa_user else {"eligible": False, "gates": []}
    existing_cert = None
    if fa_user:
        certs = get_user_certificates(fa_user["id"])
        existing_cert = next((c for c in certs if c["cert_tier"] == cert_key), None)
    return render_template(
        "forge_academy/certificate.html",
        fa_user=fa_user,
        cert_def=cert_def,
        cert_tiers=CERT_TIERS,
        eligibility=eligibility,
        existing_cert=existing_cert,
        level_ctx=_level_ctx(fa_user) if fa_user else {},
    )


@bp.route("/api/academy/certificate/<cert_key>/issue", methods=["POST"])
def api_issue_certificate(cert_key: str):
    _ensure_init()
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 400
    cert = issue_certificate(fa_user["id"], cert_key)
    if not cert:
        eligibility = check_cert_eligibility(fa_user["id"], cert_key)
        return jsonify({"ok": False, "reason": "not eligible", "gates": eligibility.get("gates", [])}), 403
    return jsonify({"ok": True, "cert": dict(cert) if hasattr(cert, "keys") else cert})


@bp.route("/academy/verify/<token>")
def verify_cert(token: str):
    _ensure_init()
    result = verify_certificate_token(token)
    from .constants import CERT_BY_KEY
    cert_def = CERT_BY_KEY.get(result["cert_tier"]) if result else None
    return render_template(
        "forge_academy/cert_verify.html",
        result=result,
        cert_def=cert_def,
    )


@bp.route("/academy/my-certificates")
def my_certificates():
    _ensure_init()
    fa_user = _fa_user()
    if not fa_user:
        return redirect(url_for("forge_academy.profile"))
    from .constants import CERT_TIERS
    certs = get_user_certificates(fa_user["id"])
    cert_map = {c["cert_tier"]: c for c in certs}
    eligibility_map = {ct["key"]: check_cert_eligibility(fa_user["id"], ct["key"]) for ct in CERT_TIERS}
    return render_template(
        "forge_academy/my_certificates.html",
        fa_user=fa_user,
        cert_tiers=CERT_TIERS,
        cert_map=cert_map,
        eligibility_map=eligibility_map,
        level_ctx=_level_ctx(fa_user),
    )


# ---------------------------------------------------------------------------
# Adaptive learning path  (Phase 5)
# ---------------------------------------------------------------------------

def _recommend_next_missions(user_id: int, role: str, limit: int = 3) -> list[dict]:
    """Return up to `limit` missions recommended for the user to attempt next."""
    completed_ids = set()
    try:
        from tools.db.storage import get_connection
        rows = get_connection().execute(
            "SELECT mission_id FROM fa_mission_progress WHERE user_id=%s AND status='completed'",
            (user_id,),
        ).fetchall()
        completed_ids = {r[0] for r in rows}
    except Exception:
        pass

    all_missions = list_missions(role=role or None, tier=None)
    # Prioritise: in-progress first, then by tier ascending, then by title
    in_prog_ids = set()
    try:
        from tools.db.storage import get_connection
        rows = get_connection().execute(
            "SELECT mission_id FROM fa_mission_progress WHERE user_id=%s AND status='in_progress'",
            (user_id,),
        ).fetchall()
        in_prog_ids = {r[0] for r in rows}
    except Exception:
        pass

    candidates = [m for m in all_missions if m["id"] not in completed_ids]

    def _sort_key(m):
        in_prog = 0 if m["id"] in in_prog_ids else 1
        return (in_prog, m.get("tier", 99), m.get("title", ""))

    candidates.sort(key=_sort_key)
    return candidates[:limit]


@bp.route("/api/academy/learning-path")
def api_learning_path():
    _ensure_init()
    fa_user = _fa_user()
    if not fa_user:
        return jsonify({"error": "not configured"}), 404
    role = fa_user.get("role", "")
    limit = min(int(request.args.get("limit", 5)), 10)
    recommendations = _recommend_next_missions(fa_user["id"], role, limit=limit)
    # aca-hyg-03: this used to serialise raw mission rows, leaking
    # domain_classes_json, is_active, order_idx, ontology_id, created_at and the rest
    # of the internal schema to the browser.
    return jsonify({
        "recommendations": [_learner_mission_view(m) for m in recommendations],
        "role": role,
    })


# Alias for app.py _APP_DEFS registration
academy_bp = bp
