# CUI // SP-CTI
"""xbm-wake-01: the Genesis circuit breaker must not be a permanent latch.

Why this exists — measured, not hypothetical:

    genesis_audit, reflex_name='scout'
      2026-06-12 .. 2026-06-23   8x genesis.reflex.completed   scouted=16 failed=0
      2026-06-24                 genesis.reflex.failed         scouted=0  failed=16
      2026-06-27                 genesis.reflex.failed         scouted=0  failed=16
      2026-06-28                 genesis.circuit_breaker.tripped  scouted=0 failed=16
      (nothing, ever again)

Three transient total-loss GitHub API outages tripped the breaker.  Nothing
closed it, so a healthy CORE reflex stayed dormant for five weeks.  The state
row compounded it: ``last_error`` read ``metric_threshold_not_met`` (the old
catch-all default) and ``last_metric_value`` still showed ``16.0`` from the last
*success*, because ``record_failure`` never wrote the field.

These tests pin the three fixes: half-open recovery after a cooldown, a state
row that tells the truth on failure, and a success that closes the breaker.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml

from tools.daemon.base import ReflexStateBase, classify_failure, utcnow, utcnow_iso

_CFG_PATH = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"


class _FakeState(ReflexStateBase):
    """ReflexStateBase with load() stubbed — exercises breaker logic, no DB."""

    state_table = "genesis_reflex_state"

    def __init__(self, row):
        super().__init__("scout", {})
        self._row = row

    def load(self):
        return self._row


def _open_row(tripped_minutes_ago: float):
    tripped = utcnow() - timedelta(minutes=tripped_minutes_ago)
    return {
        "reflex_name": "scout",
        "enabled": 1,
        "circuit_breaker_open": 1,
        "circuit_breaker_tripped_at": tripped.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consecutive_failures": 3,
        "last_error": "reflex_reported_failure: briefs_generated=0.0",
    }


# ---------------------------------------------------------------------------
# Half-open recovery
# ---------------------------------------------------------------------------
def test_closed_breaker_never_blocks():
    state = _FakeState({"circuit_breaker_open": 0})
    assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 60}) is False


def test_latches_forever_without_auto_reenable():
    """auto_reenable:false keeps the old hard-latch — proposal_genesis relies on it."""
    state = _FakeState(_open_row(tripped_minutes_ago=60 * 24 * 365))
    assert state.is_circuit_open({"auto_reenable": False, "cooldown_minutes": 60}) is True


def test_blocks_during_cooldown_window():
    state = _FakeState(_open_row(tripped_minutes_ago=10))
    assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 60}) is True


def test_half_opens_after_cooldown_elapses():
    """This is the regression that would have woken scout on 2026-06-28T11:58Z."""
    state = _FakeState(_open_row(tripped_minutes_ago=61))
    assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 60}) is False


def test_five_week_dormancy_would_have_recovered():
    state = _FakeState(_open_row(tripped_minutes_ago=60 * 24 * 36))
    assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 60}) is False


def test_zero_cooldown_means_latch():
    state = _FakeState(_open_row(tripped_minutes_ago=60 * 24))
    assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 0}) is True


def test_missing_or_unparseable_tripped_at_allows_one_probe():
    """Broken bookkeeping must not strand a reflex forever.

    This deliberately REVERSES the original fail-closed choice. Fail-closed
    looks safer, but the failure it protects against does not exist: it was
    guarding against hammering, and a probe here cannot hammer.

    With ``circuit_breaker_tripped_at`` missing or corrupt there is no cooldown
    to measure, so fail-closed means the reflex never runs again — which is
    precisely the permanent dormancy xbm-wake-01 exists to end, now reachable
    through a NULL column instead of a config flag.

    Fail-open self-repairs within a single cycle, because the probe's outcome
    rewrites the state either way:
      * success -> ``record_success`` clears ``circuit_breaker_open``
      * failure -> ``record_failure`` sets ``circuit_breaker_tripped_at = now``
        (the breaker is already open, so failures >= max_consecutive_failures
        and ``tripped`` is True), after which the timestamp parses and the
        exponential backoff below applies as normal.

    So the cost of fail-open is at most one extra run; the cost of fail-closed
    is unbounded. See ``test_a_failed_probe_restores_backoff_after_bad_state``.
    """
    for bad in (None, "", "not-a-timestamp"):
        row = _open_row(tripped_minutes_ago=999)
        row["circuit_breaker_tripped_at"] = bad
        state = _FakeState(row)
        assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 60}) is False, (
            f"tripped_at={bad!r} must allow one self-repairing probe, not strand "
            f"the reflex permanently"
        )


def test_a_failed_probe_restores_backoff_after_bad_state():
    """The self-repair claim above, asserted rather than assumed.

    Once ``record_failure`` has written a real timestamp, the breaker blocks
    again — so the fail-open branch cannot loop.
    """
    row = _open_row(tripped_minutes_ago=999)
    row["circuit_breaker_tripped_at"] = None
    cfg = {"auto_reenable": True, "cooldown_minutes": 60}
    assert _FakeState(row).is_circuit_open(cfg) is False       # probe allowed

    repaired = dict(row)
    repaired["circuit_breaker_tripped_at"] = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _FakeState(repaired).is_circuit_open(cfg) is True   # and immediately blocked again


# ---------------------------------------------------------------------------
# Exponential backoff on repeated failed probes

def test_backoff_doubles_per_failure_beyond_the_trip_threshold():
    cfg = {
        "auto_reenable": True,
        "cooldown_minutes": 60,
        "max_consecutive_failures": 3,
        "max_cooldown_minutes": 1440,
    }
    # 3 failures = just tripped, no extra -> plain 60m window
    for failures, expected_wait in ((3, 60), (4, 120), (5, 240), (6, 480)):
        row = _open_row(tripped_minutes_ago=0)
        row["consecutive_failures"] = failures
        wait = _FakeState(row)._probe_wait_minutes(row, cfg, 60)
        assert wait == expected_wait, f"{failures} failures -> {wait}m, expected {expected_wait}m"


def test_backoff_is_capped_so_a_dead_reflex_still_probes_daily():
    cfg = {
        "auto_reenable": True,
        "cooldown_minutes": 60,
        "max_consecutive_failures": 3,
        "max_cooldown_minutes": 1440,
    }
    row = _open_row(tripped_minutes_ago=0)
    row["consecutive_failures"] = 500          # long-dead reflex
    wait = _FakeState(row)._probe_wait_minutes(row, cfg, 60)
    assert wait == 1440, f"backoff must cap at max_cooldown_minutes, got {wait}"


def test_recovery_case_is_not_slowed_by_backoff():
    """A reflex whose dependency came back must still probe on the FIRST window.

    Backoff keys off failures accrued beyond the trip threshold, so the reflex
    that just tripped waits exactly ``cooldown_minutes`` — the recovery path is
    unaffected by adding backoff.
    """
    cfg = {
        "auto_reenable": True,
        "cooldown_minutes": 60,
        "max_consecutive_failures": 3,
        "max_cooldown_minutes": 1440,
    }
    row = _open_row(tripped_minutes_ago=61)     # one window elapsed, freshly tripped
    assert _FakeState(row).is_circuit_open(cfg) is False


def test_genesis_config_declares_a_backoff_cap():
    with open(_CFG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cb = cfg["trust_kernel"]["circuit_breaker"]
    assert int(cb.get("max_cooldown_minutes", 0)) > 0, (
        "max_cooldown_minutes must be declared, else a permanently dead reflex "
        "probes at the flat cooldown forever"
    )
    assert int(cb["max_cooldown_minutes"]) >= int(cb["cooldown_minutes"]), (
        "a cap below the base cooldown would shorten the wait instead of capping it"
    )


def test_empty_cb_config_preserves_latch():
    """A daemon that passes no breaker config must not silently gain auto-recovery."""
    state = _FakeState(_open_row(tripped_minutes_ago=60 * 24))
    assert state.is_circuit_open({}) is True
    assert state.is_circuit_open(None) is True


# ---------------------------------------------------------------------------
# Honest failure labelling
# ---------------------------------------------------------------------------
def test_scout_all_repos_failed_is_not_labelled_a_threshold_miss():
    """The exact shape scout returns when every repo fails: no 'error' key."""
    details = {"repos_scouted": 0, "repos_failed": 16, "anomalies_detected": 0}
    msg = classify_failure(
        success=False,
        details=details,
        metric_name="briefs_generated",
        metric_value=0.0,
        metric_config={"threshold": 0, "operator": "gte"},
    )
    assert not msg.startswith("metric_threshold_not_met"), (
        "0 >= 0 passes the configured threshold; calling this a threshold miss "
        "is what sent xbm-wake-01 debugging the wrong subsystem"
    )
    assert msg.startswith("reflex_reported_failure")
    assert "briefs_generated" in msg


def test_explicit_error_wins():
    msg = classify_failure(
        success=False,
        details={"error": "No targets in context/genesis/competitors.yaml"},
        metric_name="briefs_generated",
        metric_value=0.0,
        metric_config={},
    )
    assert msg == "No targets in context/genesis/competitors.yaml"


def test_real_threshold_miss_records_the_comparison():
    msg = classify_failure(
        success=True,
        details={},
        metric_name="briefs_generated",
        metric_value=2.0,
        metric_config={"threshold": 10, "operator": "gte"},
    )
    assert msg.startswith("metric_threshold_not_met")
    assert "2.0" in msg and "10" in msg and "gte" in msg


# ---------------------------------------------------------------------------
# State-row honesty — record_failure/record_success SQL
# ---------------------------------------------------------------------------
class _CapturingConn:
    def __init__(self, row):
        self.row = row
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), params))
        return self

    def fetchone(self):
        return self.row

    def commit(self):
        pass

    def close(self):
        pass


def _patch_conn(monkeypatch, conn):
    import tools.daemon.base as base

    monkeypatch.setattr(base, "get_connection", lambda *a, **k: conn)


def test_record_failure_overwrites_the_stale_success_metric(monkeypatch):
    """scout's row showed last_metric_value=16.0 while every failing run scored 0.0."""
    conn = _CapturingConn({"consecutive_failures": 2})
    _patch_conn(monkeypatch, conn)

    state = ReflexStateBase("scout", {})
    tripped = state.record_failure("reflex_reported_failure", {"max_consecutive_failures": 3}, metric_value=0.0)

    assert tripped is True
    update = next(s for s, _ in conn.statements if s.startswith("UPDATE"))
    assert "last_metric_value = %s" in update, "a failed run must record its own metric"
    params = next(p for s, p in conn.statements if s.startswith("UPDATE"))
    assert 0.0 in params, "the failing metric (0.0) must reach the row, not the stale 16.0"


def test_record_success_closes_the_breaker(monkeypatch):
    """Without this a half-open probe succeeds but leaves circuit_breaker_open=1."""
    conn = _CapturingConn({"consecutive_failures": 0})
    _patch_conn(monkeypatch, conn)

    state = ReflexStateBase("scout", {})
    state.record_success(metric_value=16.0)

    update = next(s for s, _ in conn.statements if s.startswith("UPDATE"))
    assert "circuit_breaker_open = 0" in update
    assert "circuit_breaker_tripped_at = NULL" in update


# ---------------------------------------------------------------------------
# Config — the knob must actually be on
# ---------------------------------------------------------------------------
def test_genesis_config_enables_auto_reenable():
    with open(_CFG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cb = cfg["trust_kernel"]["circuit_breaker"]
    assert cb["auto_reenable"] is True, (
        "cooldown_minutes is only honoured when auto_reenable is on; with it off "
        "a transient outage kills a CORE reflex permanently (xbm-wake-01)"
    )
    assert int(cb["cooldown_minutes"]) > 0


def test_utcnow_iso_round_trips_through_the_breaker_parser():
    """tripped_at is written by utcnow_iso(); is_circuit_open must parse that format."""
    row = _open_row(tripped_minutes_ago=0)
    row["circuit_breaker_tripped_at"] = utcnow_iso()
    state = _FakeState(row)
    assert state.is_circuit_open({"auto_reenable": True, "cooldown_minutes": 60}) is True
