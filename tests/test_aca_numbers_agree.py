# CUI // SP-CTI
"""aca-numbers-demo — every FORGE Academy number must name its set and agree.

Measured live on 2026-10-05, one learner (swe_arch, 7635 XP of which 7385 was 107
daily logins, 250 earned, 0 missions completed):

  * hub      — headline "Total XP 7635" beside rank "Recruit"
  * board    — WEEKLY tab ranked by the all-time 7635, role shown as "Swe_arch"
  * counts   — health 124, roster 0/122, certificate 0/11, browser "45 available"
  * readiness— "0.0/100 RED" while placing that Recruit at "L4 — AI Expert"

Each test below pins one of those to a single, labelled definition.
"""
from __future__ import annotations

import importlib
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from _academy_conn import academy_conn

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "tools" / "dashboard" / "templates" / "forge_academy"

SCHEMA = """
CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
  display_name TEXT, role TEXT DEFAULT 'swe_arch', xp INTEGER DEFAULT 0,
  level TEXT DEFAULT 'recruit', streak_days INTEGER DEFAULT 0, guild_id INTEGER,
  tier_unlocked INTEGER DEFAULT 1, tenant_id TEXT);
CREATE TABLE fa_guilds (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
CREATE TABLE fa_xp_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  xp_delta INTEGER, reason TEXT, source_type TEXT, source_id INTEGER,
  is_attendance INTEGER DEFAULT 0, verified INTEGER DEFAULT 1, note TEXT,
  created_at TEXT, classification TEXT, tenant_id TEXT);
CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
  title TEXT, tier INTEGER DEFAULT 1, topic TEXT DEFAULT '',
  role_filter TEXT DEFAULT 'all', mission_type TEXT DEFAULT 'coding',
  xp_reward INTEGER DEFAULT 200, order_idx INTEGER DEFAULT 0,
  is_active INTEGER DEFAULT 1, tenant_id TEXT);
CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id INTEGER, step_num INTEGER, title TEXT, step_type TEXT DEFAULT 'watch');
CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, mission_id INTEGER, status TEXT DEFAULT 'not_started',
  score INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, completed_at TEXT,
  UNIQUE(user_id, mission_id));
CREATE TABLE fa_step_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, step_id INTEGER, status TEXT, submission TEXT);
CREATE TABLE fa_instructor_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER);
CREATE TABLE fa_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, cert_tier TEXT);
CREATE TABLE fa_skill_nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, title TEXT);
CREATE TABLE fa_user_skills (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
  skill_id INTEGER);
"""


def _ts(days_ago: float) -> str:
    """Space-form UTC, the spelling SQLite's datetime('now') writes."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _mission(conn, mid, tier, *, active=1, steps=1, role="all", topic=""):
    conn.execute(
        "INSERT INTO fa_missions (id, slug, title, tier, is_active, role_filter, topic) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (mid, f"m{mid}", f"M{mid}", tier, active, role, topic))
    for n in range(steps):
        conn.execute("INSERT INTO fa_mission_steps (mission_id, step_num, title) "
                     "VALUES (%s,%s,'s')", (mid, n + 1))


@pytest.fixture()
def live_shape(monkeypatch):
    """The 2026-10-05 shape, scaled down: catalogued = takeable + retired + stepless."""
    conn = academy_conn()
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO fa_users (id, username, display_name, role, xp) "
                 "VALUES (1, 'admin', 'admin', 'swe_arch', 7635)")
    # 107 daily logins, the most recent inside the week, plus 250 earned 67 days ago.
    for i in range(107):
        conn.execute("INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance, "
                     "created_at) VALUES (1, %s, 'daily_login', 1, %s)",
                     (69 if i < 106 else 7385 - 69 * 106, _ts(i)))
    conn.execute("INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance, "
                 "created_at) VALUES (1, 250, 'step_pass', 0, %s)", (_ts(67),))
    # Tier 1: 3 takeable, 1 stepless (coming soon), 1 retired. Tier 2: 2 takeable,
    # one of them role-limited. Tier 3: 1 takeable, 1 retired.
    _mission(conn, 1, 1)
    _mission(conn, 2, 1)
    _mission(conn, 3, 1)
    _mission(conn, 4, 1, steps=0)
    _mission(conn, 5, 1, active=0)
    _mission(conn, 6, 2, role="swe,swe_arch")
    _mission(conn, 7, 2, role="leadership")
    _mission(conn, 8, 3)
    _mission(conn, 9, 3, active=0)
    conn.commit()
    fadb = importlib.import_module("apps.forge_academy.db")
    monkeypatch.setattr(fadb, "get_connection", lambda *a, **k: conn)
    inst = importlib.import_module("apps.forge_academy.instructor")
    monkeypatch.setattr(inst, "get_connection", lambda *a, **k: conn)
    rep = importlib.import_module("apps.innovation.reporting_engine")
    monkeypatch.setattr(rep, "get_connection", lambda *a, **k: conn)
    return fadb, conn


# ---------------------------------------------------------------------------
# 1. Hub — the headline XP is the XP the rank is computed from
# ---------------------------------------------------------------------------

def test_the_hub_headline_chip_is_the_rank_basis_not_the_running_total():
    src = (TEMPLATES / "page.html").read_text(encoding="utf-8")
    markup = src[src.index("{% block content %}"):]
    chips = markup[markup.index("fa-stats-row"):]
    chips = chips[:chips.index("{% endif %}")]
    first = chips[:chips.index("</div>\n      </div>")]
    assert "level_ctx.earned_xp" in first, "the first stat chip is not the earned figure"
    assert "stats.user.xp" not in chips, "the running total is back in the headline row"
    assert "level_ctx.attendance_xp" in chips and "Attendance XP" in chips


def test_the_hub_rank_and_headline_agree_for_the_live_learner(live_shape):
    bp = importlib.import_module("apps.forge_academy.blueprint")
    ctx = bp._level_ctx({"id": 1, "xp": 7635})
    assert ctx["earned_xp"] == 250
    assert ctx["attendance_xp"] == 7385
    assert ctx["level"]["label"] == "Recruit"


# ---------------------------------------------------------------------------
# 2. Leaderboard — weekly is weekly, and the score is earned XP
# ---------------------------------------------------------------------------

def test_weekly_counts_only_earned_xp_from_the_last_seven_days(live_shape):
    fadb, conn = live_shape
    rows = fadb.get_leaderboard(period="weekly")
    assert rows[0]["score"] == 0, "weekly still counts the all-time total or attendance"
    conn.execute("INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance, "
                 "created_at) VALUES (1, 40, 'step_pass', 0, %s)", (_ts(2),))
    conn.execute("INSERT INTO fa_xp_ledger (user_id, xp_delta, reason, is_attendance, "
                 "created_at) VALUES (1, 30, 'step_pass', 0, %s)", (_ts(9),))
    conn.commit()
    assert fadb.get_leaderboard(period="weekly")[0]["score"] == 40


def test_alltime_ranks_by_earned_xp_never_attendance(live_shape):
    fadb, _ = live_shape
    row = fadb.get_leaderboard(period="alltime")[0]
    assert row["score"] == 250
    assert row["level_label"] == "Recruit"


def test_weekly_parses_every_timestamp_spelling():
    fadb = importlib.import_module("apps.forge_academy.db")
    for text in ("2026-10-06 00:18:50.440097+00", "2026-10-06T00:18:50",
                 "2026-10-06 00:18:50", "2026-10-06T00:18:50Z"):
        ts = fadb._parse_ledger_ts(text)
        assert ts is not None and ts.tzinfo is not None, text
        assert ts.date().isoformat() == "2026-10-06"


def test_ranking_is_by_score_and_carries_role_display_names():
    fadb = importlib.import_module("apps.forge_academy.db")
    users = [
        {"id": 1, "display_name": "a", "role": "swe_arch", "earned_xp_alltime": 250},
        {"id": 2, "display_name": "b", "role": "isso", "earned_xp_alltime": 600},
    ]
    out = fadb.rank_leaderboard(users, {1: 10, 2: 90})
    assert [r["id"] for r in out] == [2, 1]
    assert [r["rank_pos"] for r in out] == [1, 2]
    assert out[1]["role_label"] == "SWE / Architect"
    assert out[0]["level_label"] == "Operative"


def test_the_leaderboard_template_renders_role_labels_and_names_the_period():
    src = (TEMPLATES / "leaderboard.html").read_text(encoding="utf-8")
    assert "| title }}" not in src, "role key is still title-cased ('Swe_arch')"
    assert "row.role_label" in src
    assert "last 7 days" in src and "score_basis" in src


def test_an_unknown_period_is_served_as_alltime_and_says_so():
    bp = importlib.import_module("apps.forge_academy.blueprint")
    assert bp._leaderboard_period("monthly") == "alltime"
    assert bp._leaderboard_period("weekly") == "weekly"
    assert "attendance excluded" in bp.LEADERBOARD_BASIS["weekly"]


# ---------------------------------------------------------------------------
# 3. Mission counts — one definition of "a mission a learner can take"
# ---------------------------------------------------------------------------

def test_the_catalogue_split_reconciles(live_shape):
    fadb, _ = live_shape
    c = fadb.mission_catalogue_counts()
    assert c == {"takeable": 6, "by_tier": {1: 3, 2: 2, 3: 1}, "catalogued": 9,
                 "retired": 2, "coming_soon": 1}
    assert c["takeable"] + c["retired"] + c["coming_soon"] == c["catalogued"]


def test_certificate_tier_progress_and_health_count_the_same_set(live_shape, monkeypatch):
    fadb, _ = live_shape
    assessment = importlib.import_module("apps.forge_academy.assessment")
    monkeypatch.setattr(assessment, "certificate_assessment_score",
                        lambda uid: {"score": 0, "graded_steps": 0})
    gates = fadb.check_cert_eligibility(1, "foundation")["gates"]
    t1 = next(g for g in gates if g["name"] == "Tier 1 Complete")
    assert t1["detail"] == "0/3 Tier 1 missions completed"
    tiers = fadb.tier_progress(1)
    assert tiers[1]["completable"] == fadb.takeable_mission_count(tier=1) == 3
    bp = importlib.import_module("apps.forge_academy.blueprint")
    assert bp._mission_count() == 6


def test_tier3_gate_no_longer_counts_retired_missions(live_shape):
    fadb, conn = live_shape
    conn.execute("INSERT INTO fa_certificates (user_id, cert_tier) VALUES (1, 'practitioner')")
    conn.commit()
    gates = fadb.check_cert_eligibility(1, "expert")["gates"]
    t3 = next(g for g in gates if g["name"] == "Tier 3 Complete")
    assert t3["detail"] == "0/1 Tier 3 missions completed"


def test_the_instructor_roster_uses_the_takeable_denominator(live_shape):
    _, conn = live_shape
    inst = importlib.import_module("apps.forge_academy.instructor")
    assert inst._active_mission_count(conn) == 6


def test_the_browser_header_counts_takeable_and_names_the_track():
    bp = importlib.import_module("apps.forge_academy.blueprint")
    missions = [{"is_available": True}] * 4 + [{"is_available": False}]
    s = bp.browser_listing_summary(missions, "swe_arch")
    assert s == {"takeable": 4, "coming_soon": 1, "track_label": "SWE / Architect"}
    assert bp.browser_listing_summary(missions, None)["track_label"] == ""
    src = (TEMPLATES / "missions.html").read_text(encoding="utf-8")
    assert "role|upper" not in src, "the raw role key is still the track name"
    assert "you can take" in src and "coming soon" in src


# ---------------------------------------------------------------------------
# 4. Org readiness — level and score from the same evidence as rank
# ---------------------------------------------------------------------------

def test_a_learner_with_no_completed_mission_is_l1_whatever_their_xp():
    rep = importlib.import_module("apps.innovation.reporting_engine")
    assert rep.competency_level(7635, 0) == "l1"
    assert rep.competency_level(250, 3) == "l1"
    assert rep.competency_level(5200, 4) == "l4"


def test_no_evidence_is_insufficient_data_not_zero_red():
    rep = importlib.import_module("apps.innovation.reporting_engine")
    comps = {"a": {"value": 0.0, "weight": 0.35}, "b": {"value": None, "weight": 0.65}}
    r = rep.composite_score(comps, evidence_count=0)
    assert r["score"] is None and "no evidence" in r["insufficient_reason"].lower()
    assert r["coverage_pct"] == 35.0
    r = rep.composite_score(comps, evidence_count=2)
    assert r["score"] == 0.0, "unmeasured weight must not dilute a measured score"
    assert rep.composite_score({"b": {"value": None, "weight": 1}}, 5)["score"] is None


def test_the_live_learner_reads_l1_and_insufficient(live_shape):
    rep = importlib.import_module("apps.innovation.reporting_engine")
    r = rep.compute_org_readiness()
    assert r["cohort"]["total_users"] == 1
    assert r["cohort"]["l1"] == 1 and r["cohort"]["l4"] == 0
    assert r["score"] is None
    assert r["tier"] == "insufficient" and r["tier_label"] == "INSUFFICIENT DATA"
    assert r["components"]["leadership_track_completion"]["value"] is None


def test_completed_status_is_counted_as_evidence(live_shape):
    """The engine matched status='complete'; the academy writes 'completed'."""
    _, conn = live_shape
    conn.execute("INSERT INTO fa_mission_progress (user_id, mission_id, status) "
                 "VALUES (1, 6, 'completed')")
    conn.commit()
    rep = importlib.import_module("apps.innovation.reporting_engine")
    r = rep.compute_org_readiness()
    assert r["components"]["tier2_completion_pct"]["value"] == 100.0
    assert r["score"] == 100.0, "only the measured component may carry the score"
    assert r["cohort"]["l1"] == 1, "250 earned XP is L1 even with a completion"


@pytest.mark.parametrize("rel", [
    "forge_academy/page.html", "forge_academy/leaderboard.html",
    "forge_academy/missions.html", "forge_academy/instructor.html",
    "forge_academy/org_readiness.html", "innovation/dashboard.html",
])
def test_the_icdev_mirror_matches(rel):
    a = (ROOT / "tools" / "dashboard" / "templates" / rel).read_text(encoding="utf-8")
    b = (ROOT / "icdev" / "tools" / "dashboard" / "templates" / rel).read_text(encoding="utf-8")
    assert a == b, f"{rel} drifted from its icdev/ mirror"


def test_the_role_certificate_gate_names_the_role_not_its_key(live_shape, monkeypatch):
    fadb, _ = live_shape
    assessment = importlib.import_module("apps.forge_academy.assessment")
    monkeypatch.setattr(assessment, "certificate_assessment_score",
                        lambda uid: {"score": 0, "graded_steps": 0})
    gates = fadb.check_cert_eligibility(1, "foundation")["gates"]
    gate = next(g for g in gates if "Role Tier 2" in g["name"])
    assert gate["name"] == "Role Tier 2 (SWE / Architect)"
    assert gate["detail"] == "0/1 role missions (0%)"
