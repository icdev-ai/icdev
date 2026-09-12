# CUI // SP-CTI
"""The standing claim `experiment_loop_measures_a_change` (xrv-lab-01).

The claim guards the defect at the level of the DATA rather than of the function
that was fixed: whatever writes the reflex's count, a keep decision must be
accountable to an experiment whose recorded metric actually moved. That is the
one question an identity baseline can never answer yes to.

The registry's own rule is asserted here too -- `reported` and `derived` must not
share code. If the verifier called what the surface calls it would prove only
that the function is deterministic, which was never in doubt.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from tools.awareness import claims
from tools.awareness.claim_verifier import UNMEASURABLE, verify


CLAIM_ID = "experiment_loop_measures_a_change"


def _claim():
    for candidate in claims.REGISTRY:
        if candidate.claim_id == CLAIM_ID:
            return candidate
    raise AssertionError(f"{CLAIM_ID} is not registered")


# --------------------------------------------------------------------------- #
# 1. Registration
# --------------------------------------------------------------------------- #
class TestRegistration:
    def test_the_claim_is_registered_and_cites_its_incident(self):
        claim = _claim()
        assert claim.tier == "propose", "no tier here may edit the claim it verifies"
        assert claim.incident is not None
        assert "xrv-lab-01" in claim.incident.task_ids
        assert claim.incident.observed_on

    def test_reported_and_derived_are_different_callables(self):
        claim = _claim()
        assert claim.reported is not claim.derived
        assert claim.reported.__code__ is not claim.derived.__code__

    def test_the_derivation_does_not_call_the_reflex_or_the_engine(self):
        """A derivation that re-ran the surface would be one computation trusted
        twice -- the exact shape every claim in this registry was learned from.

        Read STRUCTURALLY, never as text: this function's own docstring names
        ``run_loop`` to explain what it refuses to call, and a substring scan
        would flag the explanation as the offence (the model_id_gate precedent
        -- prose is not a literal).
        """
        source = pathlib.Path(claims.__file__).read_text(encoding="utf-8")
        fn = next(
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef)
            and node.name == "_derived_experiments_that_moved_a_metric"
        )

        touched: set = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Name):
                touched.add(node.id)
            elif isinstance(node, ast.Attribute):
                touched.add(node.attr)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    touched.update(alias.name.split("."))
            elif isinstance(node, ast.ImportFrom):
                touched.update((node.module or "").split("."))
                touched.update(alias.name for alias in node.names)

        for forbidden in ("experiment_engine", "run_loop", "reflexes", "autoresearch",
                          "experiment", "foundry"):
            assert forbidden not in touched, f"the derived side reaches into {forbidden}"

        # Positive control: a scan that found nothing at all would also pass.
        assert "_conn" in touched


# --------------------------------------------------------------------------- #
# 2. The agreement rule
# --------------------------------------------------------------------------- #
class TestAgreementRule:
    @pytest.mark.parametrize(
        "reported, derived, expected",
        [
            (0, 0, True),      # nothing kept, nothing moved
            (2, 5, True),      # kept fewer than moved -- that is what discard means
            (5, 5, True),      # every keep accounted for
            (2, 0, False),     # THE DEFECT: kept 2, nothing moved a metric
            (2, 1, False),     # one keep the results table cannot account for
        ],
    )
    def test_a_keep_needs_a_recorded_change(self, reported, derived, expected):
        assert claims._kept_implies_a_measured_change(reported, derived) is expected

    def test_a_non_numeric_side_never_agrees(self):
        assert claims._kept_implies_a_measured_change("many", 3) is False
        assert claims._kept_implies_a_measured_change(3, None) is False


# --------------------------------------------------------------------------- #
# 3. Verdicts -- the empty case must be UNMEASURABLE, never agreement
# --------------------------------------------------------------------------- #
class TestVerdicts:
    def _run(self, monkeypatch, reported, derived):
        claim = _claim()
        monkeypatch.setattr(claims, "_reported_experiment_kept", lambda: reported)
        monkeypatch.setattr(claims, "_derived_experiments_that_moved_a_metric", lambda: derived)
        # The Claim holds direct references, so rebuild it around the patches.
        from tools.awareness.claim_verifier import Claim

        return verify(Claim(
            claim_id=claim.claim_id, description=claim.description,
            reported=lambda: reported, derived=lambda: derived,
            agree=claim.agree, tier=claim.tier,
        ))

    def test_no_recorded_run_is_unmeasurable(self, monkeypatch):
        assert self._run(monkeypatch, None, 4).verdict == UNMEASURABLE

    def test_no_results_rows_is_unmeasurable(self, monkeypatch):
        assert self._run(monkeypatch, 2, None).verdict == UNMEASURABLE

    def test_a_keep_with_no_recorded_change_disagrees(self, monkeypatch):
        result = self._run(monkeypatch, 2, 0)
        assert result.verdict == "disagrees"

    def test_an_accountable_keep_agrees(self, monkeypatch):
        assert self._run(monkeypatch, 1, 3).verdict == "agrees"

    def test_a_measured_zero_on_both_sides_agrees(self, monkeypatch):
        """0 is a scalar answer, not an empty collection: nothing kept and
        nothing moved is a real, consistent measurement."""
        assert self._run(monkeypatch, 0, 0).verdict == "agrees"


# --------------------------------------------------------------------------- #
# 4. The reported side reads the daemon's record, including the ORANGE nesting
# --------------------------------------------------------------------------- #
class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        self._last = sql
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class TestReportedSide:
    def _read(self, monkeypatch, rows):
        monkeypatch.setattr(claims, "_conn", lambda: _FakeConn(rows))
        return claims._reported_experiment_kept()

    def test_reads_total_kept_from_the_newest_run(self, monkeypatch):
        rows = [
            {"details": json.dumps({"status": "ok", "total_kept": 3})},
            {"details": json.dumps({"status": "ok", "total_kept": 9})},
        ]
        assert self._read(monkeypatch, rows) == 3

    def test_looks_through_the_orange_proposal_wrapper(self, monkeypatch):
        """The daemon nests an ORANGE reflex's own dict one level down."""
        rows = [{"details": json.dumps({
            "status": "proposal_staged",
            "reflex_result": {"status": "ok", "total_kept": 4},
        })}]
        assert self._read(monkeypatch, rows) == 4

    def test_a_refusing_run_falls_through_to_the_durable_record(self, monkeypatch):
        """After xrv-lab-01 a `disabled` / `unmeasurable` run reports total_kept
        None. THIS RUN made no claim -- but the experiment_results rows a
        previous run wrote are still the standing record, so the fallback is
        asked rather than the whole claim going dark."""
        rows = [{"details": json.dumps({"status": "unmeasurable", "total_kept": None})}]
        monkeypatch.setattr(claims, "_kept_from_results", lambda: 0)
        assert self._read(monkeypatch, rows) == 0

    def test_the_fallback_is_none_when_nothing_was_ever_recorded(self, monkeypatch):
        rows = [{"details": json.dumps({"status": "disabled", "total_kept": None})}]
        monkeypatch.setattr(claims, "_kept_from_results", lambda: None)
        assert self._read(monkeypatch, rows) is None

    def test_a_live_run_outranks_the_durable_record(self, monkeypatch):
        """The daemon's own record is asked FIRST."""
        rows = [{"details": json.dumps({"status": "ok", "total_kept": 5})}]
        monkeypatch.setattr(claims, "_kept_from_results", lambda: 99)
        assert self._read(monkeypatch, rows) == 5

    def test_rows_without_a_count_are_skipped_not_read_as_zero(self, monkeypatch):
        rows = [
            {"details": json.dumps({"status": "started"})},
            {"details": json.dumps({"status": "ok", "total_kept": 2})},
        ]
        assert self._read(monkeypatch, rows) == 2

    def test_no_rows_at_all_is_none(self, monkeypatch):
        monkeypatch.setattr(claims, "_kept_from_results", lambda: None)
        assert self._read(monkeypatch, rows=[]) is None

    def test_an_unreadable_audit_is_none(self, monkeypatch):
        class _Boom:
            def execute(self, *a, **k):
                raise RuntimeError("no such table: genesis_audit")

            def close(self):
                pass

        monkeypatch.setattr(claims, "_conn", lambda: _Boom())
        assert claims._reported_experiment_kept() is None



class TestDurableFallback:
    """`experiment_results.decision` is the loop's OWN verdict, kept apart from
    the raw pre/post pair the derived side reads."""

    def _kept(self, monkeypatch, rows, keeps):
        class _Conn:
            def __init__(self):
                self._n = 0

            def execute(self, sql, params=None):
                self._n += 1
                return self

            def fetchone(self):
                return {"c": len(rows) if self._n == 1 else keeps}

            def close(self):
                pass

        monkeypatch.setattr(claims, "_conn", lambda: _Conn())
        return claims._kept_from_results()

    def test_counts_recorded_keeps(self, monkeypatch):
        assert self._kept(monkeypatch, [1, 2, 3], 2) == 2

    def test_a_measured_zero_keeps_is_zero_not_none(self, monkeypatch):
        assert self._kept(monkeypatch, [1, 2, 3], 0) == 0

    def test_an_empty_table_is_none(self, monkeypatch):
        assert self._kept(monkeypatch, [], 0) is None

# --------------------------------------------------------------------------- #
# 5. The derived side counts DISTINCT experiments that moved
# --------------------------------------------------------------------------- #
class _TwoQueryConn:
    """COUNT(*) first, then the rows -- matching the derivation's two reads."""

    def __init__(self, rows):
        self._rows = rows
        self._calls = 0

    def execute(self, sql, params=None):
        self._calls += 1
        self._sql = sql
        return self

    def fetchone(self):
        return {"c": len(self._rows)}

    def fetchall(self):
        return [r for r in self._rows
                if r.get("pre_metric") is not None and r.get("post_metric") is not None]

    def close(self):
        pass


class TestDerivedSide:
    def _derive(self, monkeypatch, rows):
        monkeypatch.setattr(claims, "_conn", lambda: _TwoQueryConn(rows))
        return claims._derived_experiments_that_moved_a_metric()

    def test_counts_distinct_experiments_not_rows(self, monkeypatch):
        """Repetition is not corroboration: two rows for one experiment are one."""
        rows = [
            {"experiment_id": "exp-a", "pre_metric": 0.1, "post_metric": 0.2},
            {"experiment_id": "exp-a", "pre_metric": 0.1, "post_metric": 0.3},
            {"experiment_id": "exp-b", "pre_metric": 0.5, "post_metric": 0.9},
        ]
        assert self._derive(monkeypatch, rows) == 2

    def test_an_identity_baseline_moves_nothing(self, monkeypatch):
        """What the engine produces today: pre == post on every row."""
        rows = [
            {"experiment_id": "exp-a", "pre_metric": 0.42, "post_metric": 0.42},
            {"experiment_id": "exp-b", "pre_metric": 0.7, "post_metric": 0.7},
        ]
        assert self._derive(monkeypatch, rows) == 0

    def test_an_empty_table_is_none_not_zero(self, monkeypatch):
        assert self._derive(monkeypatch, []) is None

    def test_null_metrics_are_not_a_change(self, monkeypatch):
        rows = [{"experiment_id": "exp-a", "pre_metric": None, "post_metric": None}]
        assert self._derive(monkeypatch, rows) == 0
