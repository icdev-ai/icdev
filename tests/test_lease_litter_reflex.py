# CUI // SP-CTI
"""lease_litter_reflex feeds the frozen restore act on a cadence (kpr-stale-04).

On 2026-09-02 an operator cleared 20 leaked kanban:task leases by hand to
unstarve the board, and 92 more sat on done tasks. restore_acts.perform(
"reap_dead_lease") already did exactly that -- prove, audit, apply, confirm --
and nothing ran it on a cadence. These tests pin that the reflex (a) consumes
that act and never grows a reaping rule of its own, (b) bounds and REPORTS,
(c) reads an unreadable store as unmeasurable rather than clean, and (d) is
registered on both sides the daemon needs (REFLEX_NAMES and genesis_config).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import lease_litter_reflex as R  # noqa: E402

PREFIX = "kanban:task:"


class _FakeActs:
    """restore_acts with a scripted verdict per target."""
    LEASE_PREFIX = PREFIX
    REFUSED, WOULD_APPLY, APPLIED = "refused", "would_apply", "applied"
    APPLIED_UNCONFIRMED, UNAUDITED_REFUSED, FAILED = "applied_unconfirmed", "unaudited_refused", "failed"

    def __init__(self, verdicts):
        self.verdicts = verdicts          # target -> (outcome-when-live, reason)
        self.calls = []

    def perform(self, act, target, *, dry_run=False, **_):
        self.calls.append((act, target, dry_run))
        outcome, reason = self.verdicts[target]
        if outcome == self.APPLIED and dry_run:
            outcome = self.WOULD_APPLY
        return {"act": act, "target": target, "outcome": outcome, "reason": reason,
                "confirmed": outcome == self.APPLIED, "audit_id": 7}


class _FakeLeases:
    def __init__(self, targets=(), fail=False):
        self._t, self._fail = list(targets), fail

    def list_leases(self):
        if self._fail:
            raise OSError("lease dir unreadable")
        return [{"resource": PREFIX + t} for t in self._t] + [{"resource": "service:dashboard"}]


def _wire(monkeypatch, leases, acts):
    monkeypatch.setattr(R, "_leases", lambda: leases)
    monkeypatch.setattr(R, "_restore_acts", lambda: acts)
    return acts


# --------------------------------------------------------------------------- #
# 1. it consumes the act, and only the act
# --------------------------------------------------------------------------- #
def test_every_held_task_lease_is_offered_to_the_frozen_act(monkeypatch):
    acts = _wire(monkeypatch, _FakeLeases(["t-1", "t-2"]), _FakeActs({
        "t-1": ("applied", "dead pid, no heartbeat"),
        "t-2": ("refused", "holder pid 36760 is alive"),
    }))
    rep = R.sweep({})
    assert [c[:2] for c in acts.calls] == [("reap_dead_lease", "t-1"), ("reap_dead_lease", "t-2")]
    assert rep["leases_held"] == 2, "service:* leases are not in scope"
    assert rep["reaped"] == 1 and rep["status"] == R.STATUS_FINDINGS
    assert rep["refused_by_reason"] == {"holder pid 36760 is alive": 1}


def test_the_reflex_has_no_reaping_rule_of_its_own():
    """The pid-only reader rem-hyg-15 / autonomy-adm-03 removed must not come
    back wearing a reflex. Every release goes through restore_acts.perform."""
    src = Path(R.__file__).read_text(encoding="utf-8")
    assert ".perform(" in src
    for forbidden in ("release_stale(", ".release(", "holder_is_alive(", "os.remove", "unlink("):
        assert forbidden not in src, f"the reflex reaps on its own via {forbidden!r}"


# --------------------------------------------------------------------------- #
# 2. bounded, and the bound is reported
# --------------------------------------------------------------------------- #
def test_the_bound_defers_and_names_what_it_did_not_do(monkeypatch):
    acts = _wire(monkeypatch, _FakeLeases(["a", "b", "c"]), _FakeActs({
        "a": ("applied", ""), "b": ("applied", ""), "c": ("applied", ""),
    }))
    rep = R.sweep({"max_reaps_per_run": 2})
    assert rep["reaped"] == 2 and rep["deferred"] == 1
    # the third candidate was still PROVEN -- as a dry run -- not skipped
    assert acts.calls[2] == ("reap_dead_lease", "c", True)
    assert rep["status"] == R.STATUS_FINDINGS


def test_dry_run_proves_everything_and_reaps_nothing(monkeypatch):
    acts = _wire(monkeypatch, _FakeLeases(["a"]), _FakeActs({"a": ("applied", "")}))
    rep = R.sweep({"dry_run": True})
    assert acts.calls == [("reap_dead_lease", "a", True)]
    assert rep["reaped"] == 0 and rep["would_reap"] == 1
    assert rep["status"] == R.STATUS_FINDINGS, "a would-reap is a finding, reported as such"


# --------------------------------------------------------------------------- #
# 3. unmeasurable is never clean
# --------------------------------------------------------------------------- #
def test_an_unreadable_store_is_unmeasurable_not_ok(monkeypatch):
    _wire(monkeypatch, _FakeLeases(fail=True), _FakeActs({}))
    rep = R.sweep({})
    assert rep["status"] == R.STATUS_UNMEASURABLE
    assert rep["leases_held"] is None and rep["reaped"] == 0


def test_no_leases_is_a_measured_clean_sweep(monkeypatch):
    _wire(monkeypatch, _FakeLeases([]), _FakeActs({}))
    rep = R.sweep({})
    assert rep["status"] == R.STATUS_OK and rep["leases_held"] == 0


def test_run_never_raises_and_keeps_the_breaker_closed_on_unmeasurable(monkeypatch):
    _wire(monkeypatch, _FakeLeases(fail=True), _FakeActs({}))
    out = R.run({}, None)
    assert out["success"] is True and out["metric_value"] == 0.0
    assert out["status"] == R.STATUS_UNMEASURABLE

    def _boom():
        raise RuntimeError("no such module")

    monkeypatch.setattr(R, "_restore_acts", _boom)
    monkeypatch.setattr(R, "_leases", _boom)
    out = R.run({}, None)
    assert out["success"] is True and out["status"] == R.STATUS_UNMEASURABLE, (
        "an import failure is an unreadable store, not a crash"
    )


# --------------------------------------------------------------------------- #
# 4. registered on both sides, or the daemon never dispatches it
# --------------------------------------------------------------------------- #
def test_registered_in_reflex_names_and_genesis_config():
    from tools.genesis.daemon import REFLEX_NAMES

    assert R.REFLEX_NAME in REFLEX_NAMES
    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    block = cfg["reflexes"][R.REFLEX_NAME]
    assert block["enabled"] is True and block["risk_tier"] == "green"
    assert block["interval_seconds"] == 3600
    assert block["dry_run"] is False


# --------------------------------------------------------------------------- #
# 5. the scheduler is LOUD about not being seen (tools.genesis.kanban_scheduler)
# --------------------------------------------------------------------------- #
def _scheduler_source() -> str:
    import importlib.util

    spec = importlib.util.find_spec("tools.genesis.kanban_scheduler")
    assert spec is not None and spec.origin, "tools.genesis.kanban_scheduler not importable"
    return Path(spec.origin).read_text(encoding="utf-8")


def _retry_source() -> str:
    import importlib.util

    spec = importlib.util.find_spec("tools.coordination.registration_retry")
    assert spec is not None and spec.origin, "tools.coordination.registration_retry not importable"
    return Path(spec.origin).read_text(encoding="utf-8")


def test_registration_failure_is_a_warning_not_a_pass():
    """pid 29880 ran five hours unregistered and nothing said so. The except
    around session_registry.register() used to be a bare `_coord_reg = None`.

    mfx-boot-01 moved the attempt out of kanban_scheduler and into
    RegistrationRetry, so the ONE call site this used to read by text no
    longer exists. The INVARIANT is unchanged and is asserted here at its new
    home -- and BEHAVIOURALLY, not by grepping a function's source, which is
    what made this test break on a refactor that strengthened the thing it
    guards (a failure is now loud on EVERY attempt, not only the last).
    """
    import logging

    from tools.coordination.registration_retry import RegistrationRetry

    log = logging.getLogger("test_registration_failure_is_a_warning_not_a_pass")
    log.propagate = True
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)

    def _always_fails(**_kwargs):
        raise RuntimeError("database is not accepting connections")

    try:
        retry = RegistrationRetry(
            "kanban scheduler", _always_fails, intent="kanban scheduler", log=log,
        )
        # Drive it to exhaustion: every attempt fails, so the last one gives up.
        cycle = 0
        outcomes = []
        while not retry.exhausted and cycle < 10_000:
            if retry.due(cycle):
                outcomes.append(retry.attempt(cycle))
            cycle += 1
    finally:
        log.removeHandler(handler)

    assert retry.registered is False, "a failing register() must never read as registered"
    assert retry.exhausted is True, "it must give up out loud, not retry forever in silence"
    assert outcomes and outcomes[-1] == "exhausted"

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert warnings, "a registration failure was swallowed -- the silent pass is back"
    # EVERY failed attempt is loud, not only the last.
    assert len(warnings) == len(outcomes), (
        f"{len(outcomes)} failed attempts produced {len(warnings)} warnings"
    )
    rendered = [r.getMessage() for r in warnings]
    assert any("registration FAILED" in m for m in rendered), rendered
    assert any("will not heartbeat in agent_sessions" in m for m in rendered), rendered


def test_scheduler_does_not_swallow_a_registration_attempt_error():
    """The scheduler's own wrapper around the retry must warn, never `pass`.

    The retry logs its own failures; this guards the seam AROUND it in
    kanban_scheduler._ensure_registered, which is where a bare `except:
    pass` would put the five-hour silence back.
    """
    src = _scheduler_source()
    i = src.index("def _ensure_registered(")
    block = src[i:i + 900]
    assert ("except Exception:" + chr(10) + "        pass") not in block, "the silent pass is back"
    assert "logger.warning(" in block, block


def test_the_retry_never_reports_registered_without_a_row():
    """`registered` is set from the register() RESULT, never from "it did not raise".

    read_result() is the one place that reads a truthy/`ok` answer out of
    whatever session_registry.register returns; a register() that answers
    "no" must leave the retry unregistered and still retrying.
    """
    from tools.coordination.registration_retry import RegistrationRetry

    def _answers_no(**_kwargs):
        return {"ok": False, "reason": "no row written"}

    retry = RegistrationRetry("kanban scheduler", _answers_no, intent="x")
    assert retry.attempt(0) == "failed"
    assert retry.registered is False
    assert retry.last_reason == "no row written"


def test_heartbeat_failure_is_not_swallowed_silently():
    """The heartbeat try/except must be LOUD on both legs: no heartbeat sent,
    and a heartbeat that raised.

    Anchored on the handler (`except Exception as _hb_exc:`) rather than on a
    fixed byte count from the call. mfx-boot-01 added an `elif` arm between
    the two, which pushed "heartbeat failed" past the old 700-char window --
    a test that goes red because a guarded region GREW is measuring the
    offset, not the invariant.
    """
    src = _scheduler_source()
    i = src.index('_coord_reg.heartbeat(intent=f"kanban scheduler')
    j = src.index("except Exception as _hb_exc:", i)
    block = src[i:j + 500]
    assert ("except Exception:" + chr(10) + "            pass") not in block, "the silent pass is back"
    assert "running UNREGISTERED" in block and "heartbeat failed" in block
