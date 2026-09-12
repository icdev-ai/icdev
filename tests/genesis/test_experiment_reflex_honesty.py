# CUI // SP-CTI
"""The experiment reflex stops reporting a measurement it did not make (xrv-lab-01).

Three things the reflex used to do and must not do again:

  * run the loop although ``args/autoresearch_config.yaml`` declares
    ``enabled: false`` and names an env override nothing read;
  * publish an ``acceptance_rate`` derived from ``run_loop`` results that
    carry ``placeholder_metrics: True`` -- deltas measured against an IDENTITY
    baseline, which is what the engine's own note has said since it shipped;
  * export a GKP from that run, promoting a knowledge packet built on it.

The positive control matters as much as the refusals: a reflex that stopped
running the loop entirely would pass every "did not report a number" assertion,
so a measured run must still report its counts, its rate, and its export.
"""
from __future__ import annotations

import json

import pytest

from tools.genesis.reflexes import experiment as reflex


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
_CONFIG = {
    "domains": ["compliance"],
    "max_experiments_per_run": 3,
    "export_gkp": True,
    "adaptive_threshold": {"enabled": False},
}


def _placeholder_loop(**_kwargs):
    """What run_loop returns TODAY, on every domain, for every run."""
    return {
        "success": True,
        "domain": "compliance",
        "experiments_run": 3,
        "kept": 2,
        "discarded": 1,
        "acceptance_rate": 0.6667,
        "placeholder_metrics": True,
        "heuristic": True,
        "placeholder_note": "identity baseline — deltas are placeholder",
    }


def _measured_loop(**_kwargs):
    """What it would return once the loop applies a real modification."""
    return {
        "success": True,
        "domain": "compliance",
        "experiments_run": 4,
        "kept": 1,
        "discarded": 3,
        "acceptance_rate": 0.25,
        "total_improvement": 0.02,
    }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No signal read hits a database, and no GKP lands in the real tree."""
    monkeypatch.setattr(reflex, "_fetch_high_score_signals", lambda *a, **k: [])
    monkeypatch.setattr(reflex, "_ROOT", tmp_path)
    return tmp_path


def _gkp_files(root):
    return sorted((root / "data" / "genesis" / "exports").glob("gkp-experiment-*.json"))


def _arm_loop(monkeypatch, fn):
    """Bind the loop the reflex imports, and count how often it is CALLED."""
    calls = []

    def _wrapped(**kwargs):
        calls.append(kwargs)
        return fn(**kwargs)

    import tools.autoresearch.experiment_engine as engine

    monkeypatch.setattr(engine, "run_loop", _wrapped)
    return calls


def _arm_switch(monkeypatch, enabled: bool):
    import tools.autoresearch.experiment_engine as engine

    monkeypatch.setattr(
        engine,
        "autoresearch_enabled",
        lambda *a, **k: {
            "enabled": enabled,
            "basis": "config:args/autoresearch_config.yaml",
            "env_var": "ICDEV_AUTORESEARCH_ENABLED",
            "env_value": None,
            "config_enabled": enabled,
        },
    )


# --------------------------------------------------------------------------- #
# 1. The master switch
# --------------------------------------------------------------------------- #
class TestMasterSwitch:
    def test_disabled_does_not_run_the_loop_and_reports_no_counts(self, monkeypatch, _isolate):
        calls = _arm_loop(monkeypatch, _placeholder_loop)
        _arm_switch(monkeypatch, enabled=False)

        result = reflex.run(dict(_CONFIG))

        assert calls == [], "the loop ran although autoresearch is disabled"
        assert result["success"] is True, "an opt-in feature being off is not a failure"
        assert result["metric_value"] is None
        details = result["details"]
        assert details["status"] == reflex.STATUS_DISABLED
        for key in ("total_experiments", "total_kept", "total_discarded", "acceptance_rate"):
            assert details[key] is None, f"{key} reported a count for a run that never happened"
        assert details["gkp_exported"] is False
        assert _gkp_files(_isolate) == []

    def test_env_override_turns_the_declared_off_switch_on(self, monkeypatch):
        """The declaration says enabled:false AND names an override. Both are read."""
        from tools.autoresearch import experiment_engine as engine

        monkeypatch.setattr(engine, "_load_config", lambda: {
            "enabled": False, "env_override": "ICDEV_AUTORESEARCH_ENABLED",
        })

        off = engine.autoresearch_enabled(env={})
        assert off["enabled"] is False
        assert off["basis"] == "config:args/autoresearch_config.yaml"
        assert off["config_enabled"] is False

        on = engine.autoresearch_enabled(env={"ICDEV_AUTORESEARCH_ENABLED": "1"})
        assert on["enabled"] is True
        assert on["basis"] == "env:ICDEV_AUTORESEARCH_ENABLED"
        # The config's own declaration is still reported, not overwritten.
        assert on["config_enabled"] is False

        # And the override works the other way too.
        monkeypatch.setattr(engine, "_load_config", lambda: {"enabled": True})
        assert engine.autoresearch_enabled(
            env={"ICDEV_AUTORESEARCH_ENABLED": "0"}
        )["enabled"] is False

    def test_unreadable_config_fails_closed_and_says_so(self, monkeypatch):
        """'We could not read the switch' is not consent to mutate code nightly."""
        from tools.autoresearch import experiment_engine as engine

        def _boom():
            raise OSError("config gone")

        monkeypatch.setattr(engine, "_load_config", _boom)
        gate = engine.autoresearch_enabled(env={})
        assert gate["enabled"] is False
        assert gate["basis"] == "config_unreadable"
        # None, never False: "declared off" and "could not tell" stay apart.
        assert gate["config_enabled"] is None


# --------------------------------------------------------------------------- #
# 2. Placeholder metrics are UNMEASURABLE
# --------------------------------------------------------------------------- #
class TestPlaceholderMetrics:
    def test_placeholder_run_reports_no_metric_and_exports_no_gkp(self, monkeypatch, _isolate):
        _arm_loop(monkeypatch, _placeholder_loop)
        _arm_switch(monkeypatch, enabled=True)

        result = reflex.run(dict(_CONFIG))

        assert result["success"] is True
        assert result["metric_value"] is None, "an identity baseline produced a metric"
        details = result["details"]
        assert details["status"] == reflex.STATUS_UNMEASURABLE
        assert details["reason"] == "placeholder_metrics"
        assert details["total_kept"] is None
        assert details["total_discarded"] is None
        assert details["acceptance_rate"] is None
        assert details["placeholder_domains"] == ["compliance"]
        assert details["measured_domains"] == []
        # The human-readable note is carried through, not dropped.
        assert "identity baseline" in details["placeholder_note"]

    def test_placeholder_run_writes_no_gkp_file(self, monkeypatch, _isolate):
        _arm_loop(monkeypatch, _placeholder_loop)
        _arm_switch(monkeypatch, enabled=True)

        result = reflex.run(dict(_CONFIG))

        assert result["details"]["gkp_exported"] is False
        assert _gkp_files(_isolate) == [], "a GKP was exported from an identity baseline"

    def test_the_unmeasured_keep_count_is_not_reported_as_kept(self, monkeypatch, _isolate):
        """A placeholder domain's keep decision must not wear a measured key."""
        _arm_loop(monkeypatch, _placeholder_loop)
        _arm_switch(monkeypatch, enabled=True)

        row = reflex.run(dict(_CONFIG))["details"]["domain_results"][0]
        assert row["measured"] is False
        assert "kept" not in row
        assert row["kept_unmeasured"] == 2
        assert row["acceptance_rate"] is None

    def test_a_mixed_run_is_unmeasurable_as_a_whole(self, monkeypatch, _isolate):
        """A total summed across a measured and an unmeasured domain is not a total."""
        def _mixed(domain=None, **_kw):
            return _measured_loop() if domain == "code_quality" else _placeholder_loop()

        _arm_loop(monkeypatch, _mixed)
        _arm_switch(monkeypatch, enabled=True)

        details = reflex.run({**_CONFIG, "domains": ["compliance", "code_quality"]})["details"]
        assert details["status"] == reflex.STATUS_UNMEASURABLE
        assert details["acceptance_rate"] is None
        assert details["placeholder_domains"] == ["compliance"]
        assert details["measured_domains"] == ["code_quality"]


# --------------------------------------------------------------------------- #
# 3. Positive control — a MEASURED run still measures
# --------------------------------------------------------------------------- #
class TestMeasuredRunStillReports:
    def test_measured_run_reports_counts_rate_and_exports(self, monkeypatch, _isolate):
        _arm_loop(monkeypatch, _measured_loop)
        _arm_switch(monkeypatch, enabled=True)

        result = reflex.run(dict(_CONFIG))

        assert result["metric_value"] == pytest.approx(0.25)
        details = result["details"]
        assert details["status"] == reflex.STATUS_OK
        assert details["total_experiments"] == 4
        assert details["total_kept"] == 1
        assert details["total_discarded"] == 3
        assert details["acceptance_rate"] == pytest.approx(0.25)
        assert details["gkp_exported"] is True

        files = _gkp_files(_isolate)
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["total_kept"] == 1
        assert payload["acceptance_rate"] == pytest.approx(0.25)

    def test_a_measured_zero_keep_run_still_reports_a_real_rate(self, monkeypatch, _isolate):
        """0.0 over 4 experiments is a MEASUREMENT and must survive."""
        def _none_kept(**_kw):
            return {**_measured_loop(), "kept": 0, "discarded": 4}

        _arm_loop(monkeypatch, _none_kept)
        _arm_switch(monkeypatch, enabled=True)

        result = reflex.run(dict(_CONFIG))
        assert result["metric_value"] == 0.0
        assert result["details"]["status"] == reflex.STATUS_OK
        assert result["details"]["total_kept"] == 0
        # Nothing kept -> nothing to promote.
        assert result["details"]["gkp_exported"] is False


# --------------------------------------------------------------------------- #
# 4. Empty denominators and dead domains
# --------------------------------------------------------------------------- #
class TestEmptyDenominators:
    def test_zero_experiments_is_unmeasurable_not_a_zero_rate(self, monkeypatch, _isolate):
        """`kept / max(run, 1)` answered 0.0 for a loop that ran nothing at all."""
        _arm_loop(monkeypatch, lambda **_kw: {
            "success": True, "domain": "compliance", "experiments_run": 0,
            "kept": 0, "discarded": 0, "reason": "no_hypotheses_generated",
        })
        _arm_switch(monkeypatch, enabled=True)

        result = reflex.run(dict(_CONFIG))
        assert result["metric_value"] is None
        assert result["details"]["status"] == reflex.STATUS_UNMEASURABLE
        assert result["details"]["reason"] == "no_experiments_run"
        assert result["details"]["acceptance_rate"] is None

    def test_every_domain_failing_is_unmeasurable_not_zero_percent(self, monkeypatch, _isolate):
        def _raise(**_kw):
            raise RuntimeError("no program config for domain")

        _arm_loop(monkeypatch, _raise)
        _arm_switch(monkeypatch, enabled=True)

        result = reflex.run(dict(_CONFIG))
        assert result["metric_value"] is None
        assert result["details"]["status"] == reflex.STATUS_UNMEASURABLE
        assert result["details"]["reason"] == "no_measured_domain"
        assert result["details"]["failed_domains"] == ["compliance"]

    def test_rate_is_none_over_an_empty_denominator(self):
        assert reflex._rate(0, 0) is None
        assert reflex._rate(0, 4) == 0.0
        assert reflex._rate(2, 4) == 0.5


# --------------------------------------------------------------------------- #
# 5. The daemon seams that used to coerce the refusal back to a number
# --------------------------------------------------------------------------- #
class TestDaemonPreservesAnUnmeasuredMetric:
    def test_evaluate_metric_does_not_fail_an_unmeasured_run(self):
        """None must not be scored a threshold miss -- three of those trip the breaker."""
        from tools.daemon.base import evaluate_metric

        cfg = {"name": "experiments_accepted_pct", "threshold": 0, "operator": "gte"}
        assert evaluate_metric(cfg, None) is True
        # And a MEASURED value is still compared.
        assert evaluate_metric({"threshold": 1, "operator": "gte"}, 0.0) is False

    def test_orange_path_stages_no_proposal_for_a_refusal(self):
        """There is nothing to propose from a run that did not run."""
        from tools.genesis.daemon import GenesisDaemon

        assert "disabled" in GenesisDaemon._NO_PROPOSAL_STATUSES
        assert "unmeasurable" in GenesisDaemon._NO_PROPOSAL_STATUSES
        # `ok` must never be in the set, or a measured run would stop staging.
        assert "ok" not in GenesisDaemon._NO_PROPOSAL_STATUSES
