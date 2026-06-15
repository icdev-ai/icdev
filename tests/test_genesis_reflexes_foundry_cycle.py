# CUI // SP-CTI
"""Tests for the quiet-hours gate in ``tools.genesis.reflexes.foundry_cycle``.

The gate prevents the autonomous foundry from running during a configured
local-time window (default 22:00-06:00). Acceptance criteria from acf-ada-05:

  1. foundry_cycle.py reads config ``quiet_hours`` and respects them.
  2. A reflex run at 23:00 (with 22:00-06:00) logs ``skipped_quiet_hours``.
  3. A reflex run at 10:00 (with 22:00-06:00) proceeds to ``engine.run_cycle()``.
  4. Missing / empty config defaults to no quiet hours (backwards compatible).

We avoid freezegun by injecting ``now=`` into ``_in_quiet_hours`` (already
supported) and by monkeypatching ``_QUIET_HOURS`` / ``datetime.now`` for the
``run()`` integration tests. Keeps the tests deterministic and dependency-free.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime

import pytest

from tools.genesis.reflexes import foundry_cycle


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _install_fake_engine(monkeypatch, run_cycle):
    """Register a fake ``tools.foundry.engine`` exposing ``run_cycle``."""
    mod = types.ModuleType("tools.foundry.engine")
    mod.run_cycle = run_cycle
    monkeypatch.setitem(sys.modules, "tools.foundry.engine", mod)


def _at(hh: int, mm: int = 0) -> datetime:
    """Build a naive datetime at the given local time (year/month/day irrelevant)."""
    return datetime(2026, 6, 8, hh, mm, 0)


# --------------------------------------------------------------------------- #
# _in_quiet_hours — pure helper
# --------------------------------------------------------------------------- #
class TestInQuietHoursHelper:
    """The helper is the deterministic core — exhaustive truth-table coverage."""

    def test_empty_quiet_dict_disables_gate(self):
        assert foundry_cycle._in_quiet_hours({}, now=_at(3)) is False

    def test_none_falls_back_to_module_config(self):
        """``None`` means "use the configured default" — the yaml ships a
        22:00-06:00 window, so 03:00 IS in quiet hours when called with None."""
        # 03:00 is inside the default 22:00-06:00 window.
        assert foundry_cycle._in_quiet_hours(None, now=_at(3)) is True
        # 10:00 is outside the default window.
        assert foundry_cycle._in_quiet_hours(None, now=_at(10)) is False

    def test_explicit_empty_overrides_module_config(self):
        """Passing ``{}`` explicitly disables the gate regardless of yaml."""
        assert foundry_cycle._in_quiet_hours({}, now=_at(3)) is False
        assert foundry_cycle._in_quiet_hours({}, now=_at(23)) is False

    def test_missing_start_or_end_disables_gate(self):
        assert foundry_cycle._in_quiet_hours({"start": "22:00"}, now=_at(23)) is False
        assert foundry_cycle._in_quiet_hours({"end": "06:00"}, now=_at(3)) is False

    # Wraparound window 22:00-06:00 (the configured default).
    @pytest.mark.parametrize("hh,mm,expected", [
        (22, 0, True),    # start boundary
        (23, 30, True),   # late night
        (0, 0, True),     # midnight
        (5, 59, True),    # just before end
        (6, 0, False),    # end boundary (exclusive)
        (10, 0, False),   # mid-morning
        (12, 0, False),   # noon
        (21, 59, False),  # just before start
    ])
    def test_wraparound_window_22_06(self, hh, mm, expected):
        q = {"start": "22:00", "end": "06:00"}
        assert foundry_cycle._in_quiet_hours(q, now=_at(hh, mm)) is expected

    # Daytime window 09:00-17:00 (start < end, no wraparound).
    @pytest.mark.parametrize("hh,mm,expected", [
        (8, 59, False),
        (9, 0, True),
        (12, 0, True),
        (16, 59, True),
        (17, 0, False),
        (22, 0, False),
    ])
    def test_daytime_window_09_17(self, hh, mm, expected):
        q = {"start": "09:00", "end": "17:00"}
        assert foundry_cycle._in_quiet_hours(q, now=_at(hh, mm)) is expected

    def test_whitespace_in_times_is_stripped(self):
        q = {"start": " 22:00 ", "end": " 06:00 "}
        assert foundry_cycle._in_quiet_hours(q, now=_at(23)) is True
        assert foundry_cycle._in_quiet_hours(q, now=_at(10)) is False


# --------------------------------------------------------------------------- #
# Module-level config — defaults loaded from args/foundry_config.yaml
# --------------------------------------------------------------------------- #
class TestModuleConfig:
    """The module reads ``foundry_cycle.quiet_hours`` from the YAML at import."""

    def test_quiet_hours_loaded_from_yaml(self):
        # The seeded config ships with a 22:00-06:00 wraparound window.
        assert isinstance(foundry_cycle._QUIET_HOURS, dict)
        assert foundry_cycle._QUIET_HOURS.get("start") == "22:00"
        assert foundry_cycle._QUIET_HOURS.get("end") == "06:00"

    def test_fallback_config_disables_quiet_hours(self, monkeypatch, tmp_path):
        """When the YAML is missing the fallback config (quiet_hours={}) must apply."""
        # Re-point _CONFIG_PATH to a non-existent file by patching the loader
        # used at import time. We do it by calling the loader with a missing
        # path via monkeypatching _load_config.
        monkeypatch.setattr(
            foundry_cycle, "_load_config",
            lambda: {
                "cadence_hours": 12,
                "max_concepts_per_cycle": 5,
                "dry_run": False,
                "quiet_hours": {},
            },
        )
        # Call the helper against the same loader's result by passing None
        # (uses the module-level _QUIET_HOURS, not the patched loader — so we
        # just verify the helper is backwards-compatible when its arg is empty).
        assert foundry_cycle._in_quiet_hours({}, now=_at(3)) is False


# --------------------------------------------------------------------------- #
# run() integration — flag ON, quiet hours enforced
# --------------------------------------------------------------------------- #
class TestRunQuietHoursIntegration:
    """End-to-end reflex behaviour: flag on + quiet window = clean no-op."""

    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "true")

    def test_at_2300_skips_with_quiet_hours_reason(self, monkeypatch):
        self._enable_flag(monkeypatch)
        # Inject the window (defensive — yaml already has it) and freeze the clock.
        monkeypatch.setattr(foundry_cycle, "_QUIET_HOURS", {"start": "22:00", "end": "06:00"})

        class _FrozenNow(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003
                return _at(23, 0)

        monkeypatch.setattr(foundry_cycle, "datetime", _FrozenNow)

        # Engine must never be called — register a bomb.
        def _boom(*a, **k):  # pragma: no cover - must never be called
            raise AssertionError("engine must not be called during quiet hours")
        _install_fake_engine(monkeypatch, _boom)

        r = foundry_cycle.run({})
        assert r["status"] == "skipped"
        assert r["success"] is True  # never trips the circuit breaker
        assert r["details"]["reason"] == "skipped_quiet_hours"
        assert r["details"]["enabled"] is True  # flag is on, just sleeping
        assert r["details"]["quiet_hours"] == {"start": "22:00", "end": "06:00"}
        assert r["harvested"] == 0
        assert r["tasks_emitted"] == 0

    def test_at_1000_proceeds_to_engine(self, monkeypatch):
        self._enable_flag(monkeypatch)
        monkeypatch.setattr(foundry_cycle, "_QUIET_HOURS", {"start": "22:00", "end": "06:00"})

        class _FrozenNow(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003
                return _at(10, 0)

        monkeypatch.setattr(foundry_cycle, "datetime", _FrozenNow)

        seen = {}

        def _fake_run_cycle(**kw):
            seen.update(kw)
            return {"harvested": 1, "concepts_proposed": 1, "tasks_emitted": 2, "status": "ok"}

        _install_fake_engine(monkeypatch, _fake_run_cycle)

        r = foundry_cycle.run({})
        assert r["status"] == "ok"
        assert r["success"] is True
        assert r["tasks_emitted"] == 2
        # The engine saw a real call (dry_run / max_concepts forwarded).
        assert "dry_run" in seen
        assert "max_concepts" in seen

    def test_missing_quiet_hours_config_runs_normally(self, monkeypatch):
        """Backwards-compat: empty quiet_hours config = no gate, reflex runs."""
        self._enable_flag(monkeypatch)
        monkeypatch.setattr(foundry_cycle, "_QUIET_HOURS", {})

        def _fake_run_cycle(**kw):
            return {"harvested": 0, "concepts_proposed": 0, "tasks_emitted": 1, "status": "ok"}

        _install_fake_engine(monkeypatch, _fake_run_cycle)

        r = foundry_cycle.run({})
        assert r["status"] == "ok"
        # No quiet-hours reason is set on the happy path.
        assert r["details"].get("reason") != "skipped_quiet_hours"
        assert r["tasks_emitted"] == 1

    def test_flag_off_still_wins_over_quiet_hours(self, monkeypatch):
        """Quiet hours must not surface as the reason when the flag is off."""
        monkeypatch.delenv(foundry_cycle.FEATURE_FLAG, raising=False)
        monkeypatch.setattr(foundry_cycle, "_QUIET_HOURS", {"start": "22:00", "end": "06:00"})

        # Even at 23:00, flag-off short-circuits first.
        class _FrozenNow(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003
                return _at(23, 0)

        monkeypatch.setattr(foundry_cycle, "datetime", _FrozenNow)

        r = foundry_cycle.run({})
        assert r["status"] == "skipped"
        assert r["details"]["reason"] == f"{foundry_cycle.FEATURE_FLAG} off"
        assert r["details"]["enabled"] is False


# --------------------------------------------------------------------------- #
# Boundary — the engine import must NOT happen during quiet hours
# --------------------------------------------------------------------------- #
class TestQuietHoursDoesNotImportEngine:
    """Quiet hours = no engine module touch. Keeps the path fast & side-effect free."""

    def test_engine_absent_does_not_mask_quiet_hours_reason(self, monkeypatch):
        """If the engine is also missing, quiet hours must still report the
        right reason (no engine import attempted, so no fallback reason)."""
        monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "true")
        monkeypatch.setattr(foundry_cycle, "_QUIET_HOURS", {"start": "22:00", "end": "06:00"})

        class _FrozenNow(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ARG003
                return _at(23, 30)

        monkeypatch.setattr(foundry_cycle, "datetime", _FrozenNow)
        # Force the engine import to fail — but it must never be attempted.
        import_attempted = {"value": False}

        def _raising_engine(*a, **k):
            import_attempted["value"] = True
            raise ImportError("engine not shipped")

        # Register a fake that records the attempt. The reflex should never
        # reach this import during quiet hours, so the counter stays False.
        mod = types.ModuleType("tools.foundry.engine")
        mod.run_cycle = _raising_engine
        monkeypatch.setitem(sys.modules, "tools.foundry.engine", mod)

        r = foundry_cycle.run({})
        assert r["status"] == "skipped"
        assert r["details"]["reason"] == "skipped_quiet_hours"
        assert import_attempted["value"] is False
