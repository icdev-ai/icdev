# CUI // SP-CTI
"""Role matching must compare whole tokens, not substrings.

fa_missions.role_filter is a comma-joined TEXT column with 35 distinct combinations
in production ('swe,swe_arch', 'ai_developer,agent_developer,swe_arch',
'secops_eng,isso,swe_arch,devops', ...). check_cert_eligibility matched it with
`role_filter LIKE '%swe%'`, so 'swe' also matched every 'swe_arch' mission.

Measured against the live catalogue: a learner whose role is 'swe' had a Tier-2
certificate denominator of 37 missions instead of 25 — twelve architect-only
missions counted against a plain SWE, making 100% require work aimed at a different
role. 'swe' -> 'swe_arch' is the only real collision among the 17 role tokens in
use, which is exactly why it survived: it is invisible unless you enumerate them.

list_missions already did this correctly in Python (`role in
role_filter.split(',')`). The two implementations are now one helper, so they cannot
drift apart again.

The completable-mission exclusion is the same fix as aca-ux-04's tier1_complete
gate: nine Tier-2 missions have zero steps, so leaving them in a percentage
denominator makes 100% unreachable by construction.
"""
from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "role_filter,role,expected",
    [
        # The actual production bug.
        ("swe_arch", "swe", False),
        ("swe,swe_arch", "swe", True),
        ("swe_arch", "swe_arch", True),
        # 'all' is a wildcard.
        ("all", "swe", True),
        ("all", "anything", True),
        # Whitespace in the joined column must not defeat the match.
        ("secops_eng, isso , swe_arch", "isso", True),
        ("secops_eng, isso , swe_arch", "swe", False),
        # Other substring shapes that must not match.
        ("ai_developer,agent_developer", "developer", False),
        ("leadership", "lead", False),
        ("dataops", "data", False),
        # Empty / missing filter is treated as open, matching list_missions.
        ("", "swe", True),
        (None, "swe", True),
        # An empty role matches nothing role-specific.
        ("swe", "", False),
    ],
)
def test_role_matches_whole_tokens(role_filter, role, expected):
    from apps.forge_academy.db import role_matches

    assert role_matches(role_filter, role) is expected


def test_list_missions_and_the_cert_gate_use_the_same_helper():
    """Two implementations of one rule is how they drifted in the first place."""
    import inspect

    from apps.forge_academy import db as fadb

    lm = inspect.getsource(fadb.list_missions)
    cert = inspect.getsource(fadb.check_cert_eligibility)
    assert "role_matches" in lm, "list_missions must use the shared helper"
    assert "role_matches" in cert, "the certificate gate must use the shared helper"
    assert "LIKE ?" not in cert or "role_filter LIKE" not in cert, \
        "the substring match must be gone from the certificate gate"


# ---------------------------------------------------------------------------
# The certificate gate, end to end
# ---------------------------------------------------------------------------

@pytest.fixture
def fa_conn(monkeypatch):
    from apps.forge_academy import db as fadb

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fa_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
          display_name TEXT, role TEXT DEFAULT 'swe', xp INTEGER DEFAULT 0,
          level TEXT DEFAULT 'recruit', tier_unlocked INTEGER DEFAULT 1, tenant_id TEXT);
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, tier INTEGER DEFAULT 2, role_filter TEXT DEFAULT 'all',
          mission_type TEXT DEFAULT 'coding', xp_reward INTEGER DEFAULT 200,
          is_active INTEGER DEFAULT 1);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT);
        CREATE TABLE fa_mission_progress (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, mission_id INTEGER, status TEXT DEFAULT 'not_started',
          score INTEGER DEFAULT 0, xp_earned INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
          started_at TEXT, completed_at TEXT, UNIQUE(user_id, mission_id));
        CREATE TABLE fa_certificates (id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER, cert_tier TEXT, cert_label TEXT, token TEXT, issued_at TEXT);
        INSERT INTO fa_users (id, username, display_name, role) VALUES (1,'l','L','swe');
        """
    )
    # Two swe missions (completable), two swe_arch-only, one swe mission with no steps.
    spec = [
        (1, "swe-a", "swe", True),
        (2, "swe-b", "swe", True),
        (3, "arch-a", "swe_arch", True),
        (4, "arch-b", "swe_arch", True),
        (5, "swe-empty", "swe", False),
    ]
    for mid, slug, rf, has_step in spec:
        conn.execute(
            "INSERT INTO fa_missions (id, slug, title, tier, role_filter) VALUES (?,?,?,2,?)",
            (mid, slug, slug, rf),
        )
        if has_step:
            conn.execute(
                "INSERT INTO fa_mission_steps (mission_id, step_num, title) VALUES (?,1,'s')",
                (mid,),
            )
    conn.commit()
    monkeypatch.setattr(fadb, "get_connection", lambda: conn)
    return conn


def _cert(monkeypatch, pct=100):
    from apps.forge_academy import constants

    monkeypatch.setattr(
        constants, "CERT_BY_KEY",
        {"role": {"label": "Role", "requirements": {"role_tier2_pct": pct}}},
        raising=False,
    )


def test_swe_arch_missions_are_not_counted_against_a_plain_swe(fa_conn, monkeypatch):
    """The measured production defect: denominator 37 instead of 25."""
    from apps.forge_academy.db import check_cert_eligibility

    _cert(monkeypatch)
    res = check_cert_eligibility(1, "role")
    gate = next(g for g in res["gates"] if "Role Tier 2" in g["name"])
    # Two completable swe missions; the swe_arch pair and the zero-step one excluded.
    assert "/2 " in gate["detail"] or gate["detail"].startswith("0/2"), gate["detail"]


def test_completing_the_role_missions_satisfies_the_gate(fa_conn, monkeypatch):
    from apps.forge_academy.db import check_cert_eligibility

    _cert(monkeypatch)
    for mid in (1, 2):
        fa_conn.execute(
            "INSERT INTO fa_mission_progress (user_id, mission_id, status, completed_at) "
            "VALUES (1,?, 'completed','now')", (mid,),
        )
    fa_conn.commit()
    res = check_cert_eligibility(1, "role")
    gate = next(g for g in res["gates"] if "Role Tier 2" in g["name"])
    assert gate["met"] is True, (
        f"completing every completable swe mission must satisfy the gate: {gate['detail']}"
    )


def test_a_zero_step_role_mission_does_not_block_the_certificate(fa_conn, monkeypatch):
    """Same unreachable-denominator trap as aca-ux-04's tier1_complete gate."""
    from apps.forge_academy.db import check_cert_eligibility

    _cert(monkeypatch)
    for mid in (1, 2):
        fa_conn.execute(
            "INSERT INTO fa_mission_progress (user_id, mission_id, status, completed_at) "
            "VALUES (1,?, 'completed','now')", (mid,),
        )
    fa_conn.commit()
    gate = next(g for g in check_cert_eligibility(1, "role")["gates"]
                if "Role Tier 2" in g["name"])
    assert gate["met"] is True, "the zero-step swe mission must not sit in the denominator"


def test_a_role_with_no_missions_reports_rather_than_dividing_by_zero(fa_conn, monkeypatch):
    from apps.forge_academy.db import check_cert_eligibility

    _cert(monkeypatch)
    fa_conn.execute("UPDATE fa_users SET role='mleng' WHERE id=1")
    fa_conn.commit()
    res = check_cert_eligibility(1, "role")
    gate = next(g for g in res["gates"] if "Role Tier 2" in g["name"])
    assert gate["met"] is False
    assert gate["detail"], "must explain itself rather than crash"
