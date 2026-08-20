# CUI // SP-CTI
"""The E2E promotion survey must not be able to launder selection bias.

Every test here pins a way the survey could report a reassuring number that is
not a measurement. The defect being guarded is specific and was live: E2E showed
25/25 green while declaring `needs: [test]`, so it only ever ran on branches
whose unit suite had already passed and was SKIPPED on the rest.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.ci.e2e_flake_survey import (  # noqa: E402
    classify_run,
    load_config,
    main,
    summarise,
    survey,
)

CFG = load_config()


def _job(name, conclusion):
    return {"name": name, "conclusion": conclusion}


def _run(jobs, run_id=1):
    return {"databaseId": run_id, "headBranch": "b", "event": "pull_request",
            "createdAt": "2026-08-20T00:00:00Z", "jobs": jobs}


def _sharded(conclusion, n=4):
    """A post-unblock run: shard jobs plus the aggregator."""
    return _run([_job(f"E2E Shard {k} of {n}", conclusion) for k in range(1, n + 1)]
                + [_job("E2E (Playwright)", conclusion)])


def _legacy(conclusion):
    """A pre-unblock run: the single unsharded job."""
    return _run([_job("Test", "success"), _job("E2E (Playwright)", conclusion)])


# --------------------------------------------------------------------------- #
# 1. The defect this tool exists for
# --------------------------------------------------------------------------- #
def test_a_skipped_run_is_never_counted_as_a_pass():
    """THE bug. A skipped job did not run, so it is an absence from the
    DENOMINATOR — the one place an absence is invisible."""
    rows = [classify_run(_sharded("skipped"), CFG) for _ in range(30)]
    block = summarise(rows, "post_unblock", 20)
    assert block["counts"]["skipped"] == 30
    assert block["exercised"] == 0
    assert block["counts"]["success"] == 0
    assert block["state"] == "unmeasurable"


def test_nothing_exercised_reports_none_not_a_zero_flake_rate():
    """`0.0` and `None` justify opposite decisions: 'measured clean' vs 'never
    measured'. A float that reads as a clean bill of health for a check nobody
    ran is how a flaky gate gets promoted."""
    block = summarise([classify_run(_sharded("skipped"), CFG)], "post_unblock", 20)
    assert block["flake_rate"] is None
    assert block["flake_rate_pct"] is None
    assert block["flake_rate"] is not 0.0  # noqa: F632 - identity is the point


def test_a_cancelled_run_is_neither_a_pass_nor_a_failure():
    """A cancelled suite never finished, so it says nothing in either
    direction. Counting it as success inflates reliability; counting it as
    failure blocks a promotion on an infrastructure event."""
    rows = [classify_run(_sharded("cancelled"), CFG) for _ in range(5)]
    block = summarise(rows, "post_unblock", 20)
    assert block["counts"]["cancelled"] == 5
    assert block["counts"]["success"] == 0 and block["counts"]["failure"] == 0
    assert block["exercised"] == 0


# --------------------------------------------------------------------------- #
# 2. The population split is structural, not chronological
# --------------------------------------------------------------------------- #
def test_population_is_read_off_the_run_itself():
    """No cutoff date and no commit sha: a hardcoded boundary silently
    misclassifies every run once somebody rebases or reruns an old branch."""
    assert classify_run(_sharded("success"), CFG)["population"] == "post_unblock"
    assert classify_run(_legacy("success"), CFG)["population"] == "pre_unblock"


def test_the_biased_population_can_never_print_a_quotable_verdict():
    """Left as computed, 27 pre-unblock successes render `state: supported` —
    exactly the number a reader would quote to justify the promotion."""
    report = survey([_legacy("success") for _ in range(40)], CFG)
    assert report["pre_unblock_BIASED"]["state"] == "biased_ineligible"
    assert report["promotion_supported"] is False
    assert report["verdict_state"] == "unmeasurable"


def test_the_verdict_ignores_the_pre_unblock_population_entirely():
    runs = [_legacy("success") for _ in range(50)] + [_sharded("success")]
    report = survey(runs, CFG)
    assert report["post_unblock"]["exercised"] == 1
    assert report["promotion_supported"] is False, (
        "50 biased successes must not carry a promotion"
    )


# --------------------------------------------------------------------------- #
# 3. The promotion rule itself
# --------------------------------------------------------------------------- #
def test_promotion_needs_both_the_volume_and_a_clean_sheet():
    clean_but_thin = survey([_sharded("success") for _ in range(19)], CFG)
    assert clean_but_thin["verdict_state"] == "insufficient"
    assert clean_but_thin["promotion_supported"] is False

    enough = survey([_sharded("success") for _ in range(20)], CFG)
    assert enough["verdict_state"] == "supported"
    assert enough["promotion_supported"] is True


def test_one_failure_blocks_no_matter_how_many_runs_are_averaged_over_it():
    """A failure does not stop being a failure because more runs are added.
    The bar is ZERO, so volume can never dilute it."""
    runs = [_sharded("success") for _ in range(199)] + [_sharded("failure")]
    report = survey(runs, CFG)
    assert report["verdict_state"] == "blocked"
    assert report["promotion_supported"] is False


def test_a_failing_shard_fails_the_run_even_if_the_aggregator_is_missing():
    """A run cancelled before the aggregator started still has shard verdicts."""
    run = _run([_job("E2E Shard 1 of 4", "success"),
                _job("E2E Shard 2 of 4", "failure")])
    assert classify_run(run, CFG)["outcome"] == "failure"


# --------------------------------------------------------------------------- #
# 4. Config and failure modes
# --------------------------------------------------------------------------- #
def test_the_shard_pattern_round_trips_out_of_yaml_and_matches_the_job_name():
    """YAML double quotes would eat the regex escapes and the pattern would
    match nothing — every run silently classified pre_unblock, and the verdict
    permanently `unmeasurable`."""
    raw = yaml.safe_load((ROOT / "args" / "e2e_promotion.yaml").read_text(encoding="utf-8"))
    pattern = raw["shard_job_pattern"]
    assert re.match(pattern, "E2E Shard 3 of 4")
    assert not re.match(pattern, "E2E (Playwright)")


def test_min_runs_matches_the_documented_floor():
    raw = yaml.safe_load((ROOT / "args" / "e2e_promotion.yaml").read_text(encoding="utf-8"))
    assert raw["min_runs"] >= 20, (
        "min_runs may be raised (stronger evidence) but never lowered to reach "
        "`supported` sooner"
    )


def test_a_survey_that_could_not_run_exits_2_not_0(tmp_path):
    """Exit 2, never 0. A survey that could not be produced is not a survey
    that found nothing — 0 would read as 'no flakes'."""
    missing = tmp_path / "nope.json"
    assert main(["--from-json", str(missing)]) == 2


def test_report_only_never_exits_nonzero_on_a_blocked_verdict(tmp_path, capsys):
    """No --gate, by design: a survey shipped with a gate earns a `|| true`."""
    payload = tmp_path / "runs.json"
    payload.write_text(json.dumps([_sharded("failure")]), encoding="utf-8")
    assert main(["--from-json", str(payload), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["promotion_supported"] is False
    assert report["verdict_state"] == "blocked"


def test_no_gate_flag_is_offered():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ci" / "e2e_flake_survey.py"), "--help"],
        capture_output=True, text=True,
    )
    assert "--gate" not in proc.stdout


def test_a_partial_payload_does_not_raise():
    """The forge returns partial job lists for in-flight runs."""
    assert classify_run({}, CFG)["outcome"] == "absent"
    assert classify_run({"jobs": []}, CFG)["outcome"] == "absent"


@pytest.mark.parametrize("conclusion,expected", [
    ("success", "success"),
    ("failure", "failure"),
    ("skipped", "skipped"),
    ("cancelled", "cancelled"),
    ("", "in_progress"),
    (None, "in_progress"),
])
def test_every_conclusion_maps_to_its_own_bucket(conclusion, expected):
    assert classify_run(_sharded(conclusion), CFG)["outcome"] == expected
