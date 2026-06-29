# CUI // SP-CTI
"""Tests for gamification.py GameDay XP functions — award_gameday_xp + get_gameday_seed_bonus."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_db(monkeypatch, xp_return=None, achievement_exists=False):
    """Patch DB calls in gamification module to avoid real DB."""
    import apps.forge_academy.gamification as gam

    def fake_update_user_xp(user_id, xp):
        return {"xp": (xp_return or 0) + xp, "level": "agent"}

    def fake_grant_achievement(user_id, slug):
        return slug

    def fake_get_connection():
        class FakeRow:
            def __getitem__(self, idx):
                if idx == 0:
                    return 0 if not achievement_exists else 1
                return xp_return or 0

        class FakeCursor:
            def fetchone(self_):
                return FakeRow()
            def execute(self_, *a, **kw):
                return self_

        class FakeConn:
            def execute(self_, *a, **kw):
                return FakeCursor()
            def commit(self_):
                pass

        return FakeConn()

    monkeypatch.setattr(gam, "update_user_xp", fake_update_user_xp)
    monkeypatch.setattr(gam, "grant_achievement", fake_grant_achievement)

    import tools.db.storage as storage
    monkeypatch.setattr(storage, "get_connection", fake_get_connection)


# ---------------------------------------------------------------------------
# award_gameday_xp
# ---------------------------------------------------------------------------

class TestAwardGamedayXP:
    def _fn(self):
        from apps.forge_academy.gamification import award_gameday_xp
        return award_gameday_xp

    def test_returns_correct_keys(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=5, total_participants=100)
        assert "xp_awarded" in result
        assert "achievements_unlocked" in result
        assert "gameday_rank" in result

    def test_top_10_earns_600_xp(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=3, total_participants=100)
        assert result["xp_awarded"] == 600  # 100 base + 500 top-10

    def test_top_50pct_earns_300_xp(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=25, total_participants=100)
        assert result["xp_awarded"] == 300  # 100 base + 200 top-50%

    def test_outside_top_50pct_earns_100_xp(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=80, total_participants=100)
        assert result["xp_awarded"] == 100  # baseline only

    def test_rank_stored_in_result(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=42, total_participants=100)
        assert result["gameday_rank"] == 42

    def test_gameday_champion_in_achievements_for_top_10(self, monkeypatch):
        _stub_db(monkeypatch, achievement_exists=False)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=1, total_participants=100)
        assert "gameday_champion" in result["achievements_unlocked"]

    def test_achievements_list_type(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=50, total_participants=100)
        assert isinstance(result["achievements_unlocked"], list)

    def test_zero_participants_baseline_only(self, monkeypatch):
        _stub_db(monkeypatch)
        fn = self._fn()
        result = fn(user_id=1, tournament_id="t-001", final_rank=1, total_participants=0)
        # Rank 1 is top-10 regardless
        assert result["xp_awarded"] == 600


# ---------------------------------------------------------------------------
# get_gameday_seed_bonus
# ---------------------------------------------------------------------------

class TestGetGamedaySeedBonus:
    def _fn(self):
        from apps.forge_academy.gamification import get_gameday_seed_bonus
        return get_gameday_seed_bonus

    def _patch_xp(self, monkeypatch, xp_value):
        import sys

        class FakeRow:
            def __getitem__(self_, idx):
                return xp_value
            def __bool__(self_):
                return True

        class FakeCursor:
            def fetchone(self_):
                return FakeRow()
            def execute(self_, *a, **kw):
                return self_

        class FakeConn:
            def execute(self_, *a, **kw):
                return FakeCursor()

        # Patch via sys.modules — the shim creates two distinct module objects;
        # from-imports resolve through sys.modules, not the object returned by "import".
        for key in ("tools.db.storage", "icdev.tools.db.storage"):
            if key in sys.modules:
                monkeypatch.setattr(sys.modules[key], "get_connection", lambda: FakeConn())

    def test_zero_xp_returns_zero_bonus(self, monkeypatch):
        self._patch_xp(monkeypatch, 0)
        fn = self._fn()
        assert fn(user_id=1) == 0.0

    def test_5000_xp_returns_quarter_bonus(self, monkeypatch):
        self._patch_xp(monkeypatch, 5000)
        fn = self._fn()
        result = fn(user_id=1)
        assert result == pytest.approx(0.25)

    def test_bonus_never_exceeds_0_25(self, monkeypatch):
        self._patch_xp(monkeypatch, 100_000)
        fn = self._fn()
        assert fn(user_id=1) <= 0.25

    def test_partial_xp_returns_partial_bonus(self, monkeypatch):
        self._patch_xp(monkeypatch, 2500)
        fn = self._fn()
        result = fn(user_id=1)
        assert 0.0 < result < 0.25

    def test_db_error_returns_zero(self, monkeypatch):
        import tools.db.storage as storage
        monkeypatch.setattr(storage, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        fn = self._fn()
        assert fn(user_id=99) == 0.0

    def test_missing_user_returns_zero(self, monkeypatch):
        import tools.db.storage as storage

        class FakeCursor:
            def fetchone(self_):
                return None
            def execute(self_, *a, **kw):
                return self_

        class FakeConn:
            def execute(self_, *a, **kw):
                return FakeCursor()

        monkeypatch.setattr(storage, "get_connection", lambda: FakeConn())
        fn = self._fn()
        assert fn(user_id=999) == 0.0
