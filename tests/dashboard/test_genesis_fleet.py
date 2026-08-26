# CUI // SP-CTI
"""The /genesis fleet probe is BOUNDED, and a probe that did not answer says so.

qa-fail-1474518ac97ac6a2. ``GET /genesis`` rendered by calling
``_genesis_run(key, ["--status", "--json"])`` for every app in the registry, in
a ``for`` loop, each spawning a fresh Python interpreter with ``timeout=15``.
Eight apps are declared in ``args/genesis_apps.yaml`` and all eight resolve on
the development box, so the page's worst case was 8 x 15 = 120 SECONDS with no
cap of its own -- the route could not answer inside any client's budget, and
nothing anywhere said so.

Measured on an idle machine, each subprocess run alone: 1.29 / 0.08 / 0.74 /
0.65 / 0.35 / 0.35 / 0.51 / 0.07 = 4.05s serial. The nav smoke test gives a
route 10s (``actionTimeout`` in playwright.config.ts) and got
``TimeoutError: apiRequestContext.get`` under a loaded QA sweep. The 10s budget
was the messenger; the unbounded fan-out is the defect.

AND THE PAGE COULD NOT REPORT ITS OWN FAILURE. Three different things all
rendered as one badge:

  * a probe that TIMED OUT       -> ``except Exception`` -> ``_available: False``
    -> OFFLINE, identical to an app that is not on this machine at all;
  * an app not on this machine   -> OFFLINE, correctly;
  * a probe that RAN AND FAILED  -> ``_genesis_run`` returns
    ``{"error": "parse_failed"}`` WITHOUT raising, so the route set
    ``_available: True`` and the template, finding no ``daemon.enabled``, drew
    DISABLED -- a broken probe reading as "installed, switched off". Two of the
    eight (``govchain``, ``icdev-ft``) have no ``daemon.py`` and did exactly
    that.

So ``_probe_state`` is FOUR values that are never merged, because each sends a
reader somewhere different: ``ok`` | ``root_missing`` | ``unmeasured`` |
``error``. ``unmeasured`` is the one that had to exist -- an app whose status
nobody managed to read is not an app that is switched off.
"""

from __future__ import annotations

import time

import pytest

from tools.dashboard.genesis_fleet import (
    ERROR,
    OK,
    ROOT_MISSING,
    UNMEASURED,
    gather_fleet_status,
)


def _apps(*keys, available=True):
    return {
        k: {"key": k, "name": k.upper(), "root": f"/roots/{k}", "available": available}
        for k in keys
    }


class TestBounded:
    def test_probes_run_concurrently_not_serially(self):
        """Eight 0.4s probes must not cost 3.2s. This is the whole defect."""
        apps = _apps(*[f"app{i}" for i in range(8)])

        def probe(key, cfg, timeout):
            time.sleep(0.4)
            return {"daemon": {"enabled": True}}

        started = time.monotonic()
        out = gather_fleet_status(apps, probe, deadline_seconds=10.0)
        elapsed = time.monotonic() - started

        assert len(out) == 8
        assert all(v["_probe_state"] == OK for v in out.values())
        # Serial would be >=3.2s. Allow generous slack for a loaded CI runner
        # and still fail the serial shape by a wide margin.
        assert elapsed < 2.0, f"fan-out took {elapsed:.2f}s -- probes are serial"

    def test_the_page_is_bounded_even_when_every_probe_hangs(self):
        """The route's worst case must be the deadline, never N x per-app timeout."""
        apps = _apps("a", "b", "c", "d")

        def probe(key, cfg, timeout):
            time.sleep(30)
            raise AssertionError("unreachable")

        started = time.monotonic()
        out = gather_fleet_status(apps, probe, deadline_seconds=1.0)
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, f"gather took {elapsed:.2f}s against a 1.0s deadline"
        assert len(out) == 4
        assert all(v["_probe_state"] == UNMEASURED for v in out.values())

    def test_per_probe_timeout_is_the_remaining_budget_never_larger(self):
        """A per-app timeout above the page deadline is incoherent -- one app
        could outlast the page it is rendering."""
        seen: list[float] = []

        def probe(key, cfg, timeout):
            seen.append(timeout)
            return {}

        gather_fleet_status(_apps("a", "b"), probe, deadline_seconds=3.0)

        assert seen, "probe was never called"
        assert all(0 < t <= 3.0 for t in seen), seen


class TestStatesAreNeverMerged:
    def test_a_timed_out_probe_is_unmeasured_not_offline(self):
        apps = _apps("slow")

        def probe(key, cfg, timeout):
            time.sleep(30)

        out = gather_fleet_status(apps, probe, deadline_seconds=0.5)

        assert out["slow"]["_probe_state"] == UNMEASURED
        assert out["slow"]["_available"] is False
        # The distinction the template needs: this app IS on this machine.
        assert out["slow"]["_probe_state"] != ROOT_MISSING

    def test_an_absent_root_is_root_missing_and_is_never_probed(self):
        apps = _apps("gone", available=False)
        calls = []

        def probe(key, cfg, timeout):
            calls.append(key)
            return {}

        out = gather_fleet_status(apps, probe, deadline_seconds=5.0)

        assert out["gone"]["_probe_state"] == ROOT_MISSING
        assert out["gone"]["_available"] is False
        assert calls == [], "an app that is not on this machine must not be probed"

    def test_a_probe_that_answered_with_an_error_is_error_not_ok(self):
        """``_genesis_run`` returns {"error": "parse_failed"} WITHOUT raising. The
        old route set _available=True on it and the page drew DISABLED."""
        apps = _apps("broken")

        def probe(key, cfg, timeout):
            return {"error": "parse_failed", "stderr": "No such file"}

        out = gather_fleet_status(apps, probe, deadline_seconds=5.0)

        assert out["broken"]["_probe_state"] == ERROR
        assert out["broken"]["_available"] is False

    def test_a_probe_that_raised_is_error_not_unmeasured(self):
        """A probe that ran and blew up is a MEASURED failure. Only a probe that
        never came back inside the budget is unmeasured."""
        apps = _apps("boom")

        def probe(key, cfg, timeout):
            raise RuntimeError("daemon exploded")

        out = gather_fleet_status(apps, probe, deadline_seconds=5.0)

        assert out["boom"]["_probe_state"] == ERROR
        assert "daemon exploded" in str(out["boom"].get("error"))

    def test_root_missing_answer_from_the_probe_is_carried_through(self):
        """``_genesis_run`` itself answers {"error": "root_missing"} for an app
        whose root vanished between the registry load and the probe."""
        apps = _apps("raced")

        def probe(key, cfg, timeout):
            return {"error": "root_missing", "app": key}

        out = gather_fleet_status(apps, probe, deadline_seconds=5.0)

        assert out["raced"]["_probe_state"] == ROOT_MISSING


class TestShapeThePageAlreadyConsumes:
    def test_every_app_keeps_its_name_and_key_order(self):
        apps = _apps("one", "two", "three")

        def probe(key, cfg, timeout):
            return {"daemon": {"enabled": False}}

        out = gather_fleet_status(apps, probe, deadline_seconds=5.0)

        assert list(out) == ["one", "two", "three"]
        assert out["two"]["_name"] == "TWO"

    def test_a_successful_probes_payload_is_preserved(self):
        apps = _apps("live")

        def probe(key, cfg, timeout):
            return {"daemon": {"enabled": True}, "reflexes": {"audit": {"enabled": True}}}

        out = gather_fleet_status(apps, probe, deadline_seconds=5.0)

        assert out["live"]["_available"] is True
        assert out["live"]["daemon"]["enabled"] is True
        assert out["live"]["reflexes"]["audit"]["enabled"] is True

    def test_an_empty_registry_is_an_empty_result_not_a_raise(self):
        assert gather_fleet_status({}, lambda k, c, t: {}, deadline_seconds=1.0) == {}


class TestMirrorParity:
    def test_the_module_ships_in_both_trees(self):
        import icdev.tools.dashboard.genesis_fleet as mirrored

        assert mirrored.OK == OK
        assert callable(mirrored.gather_fleet_status)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
# CUI // SP-CTI
