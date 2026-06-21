# CUI // SP-CTI
"""Tests for the adaptive Pulse-relevance threshold in R7 Draft (aiify-opp-5400).

Covers ``_compute_pulse_relevance_threshold`` which replaces the hardcoded 0.1
keyword-overlap cutoff with an ``anomaly_detection`` paradigm: the cutoff is
derived from the P25 of historical content_reuse relevance scores, clamped to a
hard floor, with a static fallback when disabled or under-sampled.
"""

import pytest

from tools.proposal_genesis.reflexes import draft as draft_mod
from tools.proposal_genesis.reflexes.draft import (
    _PULSE_RELEVANCE_THRESHOLD,
    _compute_pulse_relevance_threshold,
)


class _FakeRow(dict):
    """Row that supports both ``row["k"]`` and ``row[i]`` like sqlite3.Row."""

    def __init__(self, ordered):
        super().__init__(ordered)
        self._vals = list(ordered.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return super().__getitem__(key)


class _FakeConn:
    def __init__(self, row, raise_on_percentile=False):
        self._row = row
        self._raise_on_percentile = raise_on_percentile

    def execute(self, sql, params=None):
        if self._raise_on_percentile and "PERCENTILE_CONT" in sql:
            raise RuntimeError("no PERCENTILE_CONT on sqlite")

        class _Cur:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        return _Cur(self._row)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _restore_conn(monkeypatch):
    yield


def _patch_conn(monkeypatch, conn):
    monkeypatch.setattr(draft_mod, "get_connection", lambda: conn)


def test_disabled_returns_fallback(monkeypatch):
    # Should not even touch the DB when disabled.
    def _boom():
        raise AssertionError("get_connection must not be called when disabled")

    monkeypatch.setattr(draft_mod, "get_connection", _boom)
    assert _compute_pulse_relevance_threshold({"enabled": False}) == _PULSE_RELEVANCE_THRESHOLD


def test_under_sampled_returns_fallback(monkeypatch):
    # n below min_samples -> static fallback.
    row = _FakeRow({"p25": 0.42, "n": 3})
    _patch_conn(monkeypatch, _FakeConn(row))
    cfg = {"enabled": True, "min_samples": 15}
    assert _compute_pulse_relevance_threshold(cfg) == _PULSE_RELEVANCE_THRESHOLD


def test_adaptive_p25_above_floor(monkeypatch):
    # Enough samples and P25 above the floor -> adaptive cutoff used.
    row = _FakeRow({"p25": 0.37, "n": 40})
    _patch_conn(monkeypatch, _FakeConn(row))
    cfg = {"enabled": True, "min_samples": 15, "adaptive_bounds": {"relevance_floor": 0.1}}
    assert _compute_pulse_relevance_threshold(cfg) == pytest.approx(0.37)


def test_adaptive_clamped_to_floor(monkeypatch):
    # P25 below the floor -> clamp up to the floor.
    row = _FakeRow({"p25": 0.02, "n": 40})
    _patch_conn(monkeypatch, _FakeConn(row))
    cfg = {"enabled": True, "min_samples": 15, "adaptive_bounds": {"relevance_floor": 0.1}}
    assert _compute_pulse_relevance_threshold(cfg) == pytest.approx(0.1)


def test_sqlite_mean_fallback_path(monkeypatch):
    # PERCENTILE_CONT raises (SQLite) -> mean*0.5 proxy, clamped to floor.
    row = _FakeRow({"mean_r": 0.5, "n": 40})
    _patch_conn(monkeypatch, _FakeConn(row, raise_on_percentile=True))
    cfg = {"enabled": True, "min_samples": 15, "adaptive_bounds": {"relevance_floor": 0.1}}
    # mean 0.5 * 0.5 = 0.25, above floor 0.1
    assert _compute_pulse_relevance_threshold(cfg) == pytest.approx(0.25)


def test_db_error_returns_fallback(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(draft_mod, "get_connection", _boom)
    assert _compute_pulse_relevance_threshold({"enabled": True}) == _PULSE_RELEVANCE_THRESHOLD
