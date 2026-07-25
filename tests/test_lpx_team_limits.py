# CUI // SP-CTI
"""lpx-teams-01 — per-team RPM/TPM ceilings (competition fairness) tests.

The deny case IS the point: exceeding a team's ceiling must degrade ONLY that
team, never its opponents (see the abac-pip lesson — always test the deny case).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def tl(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    # Deterministic org limits for sizing assertions.
    monkeypatch.setenv("ICDEV_LLM_ORG_RPM", "60")
    monkeypatch.setenv("ICDEV_LLM_ORG_TPM", "100000")
    monkeypatch.setenv("ICDEV_LLM_TEAM_BURST_FACTOR", "1.0")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    mod = importlib.import_module("tools.llm.proxy_team_limits")
    mod._migrated = False
    mod.ensure_schema()
    return mod


def _seed_session(n_teams, max_teams=8):
    """Create a session + n teams; return (session_id, [team_ids])."""
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute(
        "INSERT INTO ttx_sessions (scenario_slug, join_code, max_teams) VALUES (%s, %s, %s)",
        ("scn", f"JOIN{max_teams}{n_teams}", max_teams),
    )
    c.commit()
    sid = dict(c.execute("SELECT session_id FROM ttx_sessions ORDER BY session_id DESC").fetchone())["session_id"]
    tids = []
    for i in range(n_teams):
        c.execute(
            "INSERT INTO ttx_teams (session_id, team_name, join_code) VALUES (%s, %s, %s)",
            (sid, f"team{i}", f"T{sid}-{i}"),
        )
        c.commit()
        tids.append(dict(c.execute("SELECT team_id FROM ttx_teams ORDER BY team_id DESC").fetchone())["team_id"])
    return sid, tids


def test_ceiling_sized_off_actual_team_count(tl):
    # 5 teams, org RPM 60, burst 1.0 -> ceiling 12 (not 60/8 for max_teams=8).
    sid, tids = _seed_session(5, max_teams=8)
    cfg = tl.configure_session_ceilings(sid)
    assert cfg["team_count"] == 5
    assert cfg["rpm_ceiling"] == 12  # 60/5
    assert cfg["tpm_ceiling"] == 20000  # 100000/5
    assert set(cfg["teams_configured"]) == set(tids)


def test_burst_factor_widens_ceiling(tl):
    sid, _ = _seed_session(4)
    cfg = tl.configure_session_ceilings(sid, burst_factor=1.5)
    # 60/4 = 15, *1.5 = 22.5 -> ceil 23
    assert cfg["rpm_ceiling"] == 23


def test_deny_when_team_exceeds_its_rpm_ceiling(tl):
    sid, tids = _seed_session(5)  # ceiling 12 rpm
    tl.configure_session_ceilings(sid)
    victim = tids[0]
    now = 1000.0  # fixed minute window
    allowed = 0
    for _ in range(12):
        r = tl.check_team_rate(sid, victim, now=now)
        assert r["allowed"] is True
        tl.record_team_call(sid, victim, now=now)
        allowed += 1
    # 13th call in the same minute is denied — THE DENY CASE.
    denied = tl.check_team_rate(sid, victim, now=now)
    assert denied["allowed"] is False
    assert denied["action"] == "deny"
    assert "ceiling" in denied["reason"].lower()
    assert allowed == 12


def test_one_team_looping_does_not_degrade_opponents(tl):
    """Fairness: exhausting one team's ceiling leaves every opponent at full share."""
    sid, tids = _seed_session(5)
    tl.configure_session_ceilings(sid)
    hog, opp1, opp2 = tids[0], tids[1], tids[2]
    now = 2000.0
    # Hog burns its entire ceiling this minute.
    for _ in range(20):
        if tl.check_team_rate(sid, hog, now=now)["allowed"]:
            tl.record_team_call(sid, hog, now=now)
    assert tl.check_team_rate(sid, hog, now=now)["allowed"] is False
    # Opponents are completely unaffected — full ceiling available.
    for opp in (opp1, opp2):
        r = tl.check_team_rate(sid, opp, now=now)
        assert r["allowed"] is True
        assert r["rpm_used"] == 0
        assert r["rpm_limit"] == 12


def test_window_resets_next_minute(tl):
    sid, tids = _seed_session(5)
    tl.configure_session_ceilings(sid)
    t = tids[0]
    for _ in range(12):
        tl.record_team_call(sid, t, now=100.0 * 60)  # minute 100
    assert tl.check_team_rate(sid, t, now=100.0 * 60)["allowed"] is False
    # Next minute -> fresh window.
    assert tl.check_team_rate(sid, t, now=101.0 * 60)["allowed"] is True


def test_tpm_ceiling_enforced(tl):
    sid, tids = _seed_session(5)  # tpm ceiling 20000
    tl.configure_session_ceilings(sid)
    t = tids[0]
    now = 3000.0
    tl.record_team_call(sid, t, tokens=19000, now=now)
    # A 2000-token call would exceed 20000 -> deny.
    r = tl.check_team_rate(sid, t, tokens=2000, now=now)
    assert r["allowed"] is False
    assert "TPM" in r["reason"]


def test_unconfigured_session_fails_open(tl):
    sid, tids = _seed_session(3)
    # No configure_session_ceilings called.
    r = tl.check_team_rate(sid, tids[0])
    assert r["allowed"] is True
    assert r["rpm_limit"] is None


def test_facilitator_status_flags_throttled_team(tl):
    sid, tids = _seed_session(5)
    tl.configure_session_ceilings(sid)
    now = 4000.0
    for _ in range(12):
        tl.record_team_call(sid, tids[0], now=now)
    status = tl.team_rate_status(sid, now=now)
    by_team = {s["team_id"]: s for s in status}
    assert by_team[tids[0]]["at_ceiling"] is True
    assert by_team[tids[1]]["at_ceiling"] is False
