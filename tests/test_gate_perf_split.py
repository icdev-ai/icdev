# CUI // SP-CTI
"""Tests for the coherence gate/sweep split and the tri-state coherence result.

Regressions these lock down:
  * The per-task gate ran the FULL check tier (328s cold) against a 120s
    subprocess timeout, so it timed out on every task...
  * ...and the timeout was caught and returned as ``True``, recording
    ``coherence_passed=1`` for tasks whose coherence had never run.
  * Baseline comparison was binary ("does main also fail?"), so one unrelated
    pre-existing failure turned the whole gate into an unconditional pass.
  * A CodeLens failure still blocked on the coherence subprocess instead of
    cancelling it.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from tools.workflow import coherence_checker as cc
from tools.workflow import validated_commit as vc


# --------------------------------------------------------------------------
# Tier selection
# --------------------------------------------------------------------------

def test_full_tier_runs_every_registered_check():
    assert set(cc.select_checks("full")) == set(cc.CHECK_REGISTRY)


def test_fast_tier_drops_the_whole_app_heavies():
    fast = set(cc.select_checks("fast"))
    assert fast, "fast tier must not be empty"
    for heavy in cc.HEAVY_CHECKS:
        assert heavy not in fast, f"{heavy} should be deferred to the full sweep"
    # Everything else survives — the split defers three checks, not a category.
    assert fast == set(cc.CHECK_REGISTRY) - set(cc.HEAVY_CHECKS)


def test_fast_tier_readds_a_heavy_when_the_diff_touches_its_trigger():
    fast = set(cc.select_checks("fast", ["tools/dashboard/app.py"]))
    assert "blueprint_imports" in fast
    assert "openapi_parity" in fast
    # An unrelated heavy stays deferred.
    assert "llm_router_api" not in fast


def test_fast_tier_readds_llm_heavy_for_llm_diffs():
    fast = set(cc.select_checks("fast", ["tools/llm/router.py"]))
    assert "llm_router_api" in fast
    assert "blueprint_imports" not in fast


def test_windows_path_separators_still_match_triggers():
    fast = set(cc.select_checks("fast", [r"tools\dashboard\app.py"]))
    assert "blueprint_imports" in fast


# --------------------------------------------------------------------------
# Diff scoping for the file scanners
# --------------------------------------------------------------------------

def test_scan_targets_without_diff_walks_the_tree():
    assert len(cc._scan_targets(None)) > 100


def test_scan_targets_with_diff_returns_only_those_files():
    target = cc.PROJECT_ROOT / "tools" / "workflow" / "validated_commit.py"
    picked = cc._scan_targets([target])
    assert picked == [target]


def test_scan_targets_ignores_paths_outside_the_subtree():
    assert cc._scan_targets([cc.PROJECT_ROOT / "tests" / "conftest.py"]) == []


def test_scan_targets_ignores_non_python_and_missing_files():
    assert cc._scan_targets([cc.PROJECT_ROOT / "tools" / "nope_does_not_exist.py"]) == []
    assert cc._scan_targets([cc.PROJECT_ROOT / "tools" / "manifest.md"]) == []


def test_diff_scoped_provider_bypass_ignores_unrelated_violations():
    """A clean file's diff must not surface violations from elsewhere in tools/."""
    clean = cc.PROJECT_ROOT / "tools" / "workflow" / "pipeline_grader.py"
    result = cc.check_provider_bypass(changed_files=[clean])
    assert result.status == "pass"


# --------------------------------------------------------------------------
# Report parsing — stdout is polluted by import-time banners
# --------------------------------------------------------------------------

_REPORT = {
    "overall_pass": False,
    "total_checks": 2,
    "checks": [
        {"check_id": "manifest", "status": "pass", "message": "ok"},
        {"check_id": "ruff_lint", "status": "fail", "message": "3 issues",
         "missing": ["a.py", "b.py", "c.py", "d.py"], "extra": []},
    ],
}


def test_extract_report_json_skips_leading_banner_noise():
    noisy = "[init_db] Schema created\n[init_db] 15 templates\n" + json.dumps(_REPORT)
    assert vc._extract_report_json(noisy) == _REPORT


def test_extract_report_json_handles_clean_output():
    assert vc._extract_report_json(json.dumps(_REPORT)) == _REPORT


def test_extract_report_json_returns_none_for_garbage():
    assert vc._extract_report_json("not json at all") is None
    assert vc._extract_report_json("") is None


def test_failing_check_ids_picks_only_failures():
    assert vc._failing_check_ids(_REPORT) == {"ruff_lint"}
    assert vc._failing_check_ids(None) == set()


def test_parse_coherence_failures_reports_check_id_through_noise():
    noisy = "[init_db] banner\n" + json.dumps(_REPORT)
    summary = vc._parse_coherence_failures(noisy)
    assert "ruff_lint" in summary
    assert "raw:" not in summary, "must parse, not fall back to the raw tail"


def test_parse_coherence_failures_only_filter():
    body = json.dumps(_REPORT)
    assert vc._parse_coherence_failures(body, only={"manifest"}) == ""
    assert "ruff_lint" in vc._parse_coherence_failures(body, only={"ruff_lint"})


# --------------------------------------------------------------------------
# Command construction
# --------------------------------------------------------------------------

def test_coherence_cmd_passes_the_diff():
    cmd = vc._coherence_cmd("fast", ["tools/a.py", "tools/b.py"])
    assert "--tier" in cmd and "fast" in cmd
    assert cmd[cmd.index("--changed-files") + 1] == "tools/a.py,tools/b.py"


def test_coherence_cmd_degrades_to_full_when_diff_exceeds_argv_budget():
    huge = [f"tools/generated/module_{i:05d}.py" for i in range(500)]
    cmd = vc._coherence_cmd("fast", huge)
    assert "--changed-files" not in cmd
    assert cmd[cmd.index("--tier") + 1] == "full", (
        "an unscoped fast tier would skip the heavies without the diff "
        "evidence that justifies skipping them"
    )


def test_coherence_cmd_without_diff_keeps_requested_tier():
    assert vc._coherence_cmd("fast", None)[-1] == "--gate"
    assert "fast" in vc._coherence_cmd("fast", None)


# --------------------------------------------------------------------------
# Tri-state coherence result
# --------------------------------------------------------------------------

@pytest.fixture
def _fake_run(monkeypatch):
    """Replace the coherence subprocess with a scripted (rc, stdout, status)."""
    calls = []

    def _install(rc, stdout, status):
        def _fake(cmd, cwd, timeout, cancel_event=None):
            calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
            return rc, stdout, status
        monkeypatch.setattr(vc, "_run_cancellable", _fake)
        return calls

    return _install


def test_timeout_is_not_evaluated_never_a_pass(_fake_run, tmp_path):
    _fake_run(None, "", "timeout")
    ok, reason = vc._run_coherence(str(tmp_path), compare_to_main=False)
    assert ok is None, "a timed-out gate must not be recorded as a pass"
    assert "NOT EVALUATED" in reason


def test_cancelled_is_not_evaluated(_fake_run, tmp_path):
    _fake_run(None, "", "cancelled")
    ok, reason = vc._run_coherence(str(tmp_path), compare_to_main=False)
    assert ok is None
    assert "cancelled" in reason.lower()


def test_unparseable_output_is_not_evaluated(_fake_run, tmp_path):
    _fake_run(0, "totally not json", "ok")
    ok, reason = vc._run_coherence(str(tmp_path), compare_to_main=False)
    assert ok is None
    assert "NOT EVALUATED" in reason


def test_clean_report_passes(_fake_run, tmp_path):
    _fake_run(0, json.dumps({"checks": [{"check_id": "manifest", "status": "pass"}]}), "ok")
    ok, _reason = vc._run_coherence(str(tmp_path), compare_to_main=False)
    assert ok is True


def test_preexisting_failure_does_not_block(_fake_run, monkeypatch, tmp_path):
    _fake_run(1, json.dumps(_REPORT), "ok")
    monkeypatch.setattr(vc, "_main_baseline_failures", lambda tier, timeout: {"ruff_lint"})
    ok, reason = vc._run_coherence(str(tmp_path), compare_to_main=True)
    assert ok is True
    assert "pre-existing" in reason


def test_new_failure_blocks_even_when_main_is_already_red(_fake_run, monkeypatch, tmp_path):
    """The old binary compare passed everything once main had ANY failure."""
    _fake_run(1, json.dumps(_REPORT), "ok")
    monkeypatch.setattr(
        vc, "_main_baseline_failures", lambda tier, timeout: {"some_other_check"}
    )
    ok, reason = vc._run_coherence(str(tmp_path), compare_to_main=True)
    assert ok is False
    assert "ruff_lint" in reason
    assert "NEW" in reason


def test_unestablished_baseline_is_not_evaluated(_fake_run, monkeypatch, tmp_path):
    _fake_run(1, json.dumps(_REPORT), "ok")
    monkeypatch.setattr(vc, "_main_baseline_failures", lambda tier, timeout: None)
    ok, reason = vc._run_coherence(str(tmp_path), compare_to_main=True)
    assert ok is None
    assert "NOT EVALUATED" in reason


def test_explicit_tier_overrides_env(_fake_run, monkeypatch, tmp_path):
    calls = _fake_run(0, json.dumps({"checks": []}), "ok")
    monkeypatch.setenv("ICDEV_COHERENCE_GATE_TIER", "fast")
    vc._run_coherence(str(tmp_path), compare_to_main=False, tier="full")
    assert calls[-1]["cmd"][calls[-1]["cmd"].index("--tier") + 1] == "full"


# --------------------------------------------------------------------------
# Cancellation — a cheap failing gate must not wait out the slow one
# --------------------------------------------------------------------------

def test_run_cancellable_kills_on_cancel_event():
    event = threading.Event()
    started = time.monotonic()

    def _cancel_soon():
        time.sleep(0.5)
        event.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    rc, _out, status = vc._run_cancellable(
        ["python", "-c", "import time; time.sleep(30)"],
        cwd=str(vc.BASE_DIR), timeout=30, cancel_event=event,
    )
    elapsed = time.monotonic() - started
    assert status == "cancelled"
    assert rc is None
    assert elapsed < 10, f"cancel must kill the child promptly, took {elapsed:.1f}s"


def test_run_cancellable_enforces_timeout():
    started = time.monotonic()
    rc, _out, status = vc._run_cancellable(
        ["python", "-c", "import time; time.sleep(30)"],
        cwd=str(vc.BASE_DIR), timeout=1,
    )
    assert status == "timeout"
    assert rc is None
    assert time.monotonic() - started < 10


def test_codelens_failure_cancels_coherence_instead_of_blocking(monkeypatch, tmp_path):
    """A fast CodeLens failure used to still wait out the coherence timeout."""
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        vc, "_run_codelens",
        lambda cwd, py, cmp: (False, "ruff found 3 issues", {"ruff_issues": 3}),
    )

    observed = {}

    def _slow_coherence(cwd, compare_to_main=True, changed_files=None,
                        cancel_event=None, timeout=None, tier=None):
        # Simulate a long checker that honours cancellation.
        for _ in range(200):
            if cancel_event is not None and cancel_event.is_set():
                observed["cancelled"] = True
                return None, "coherence cancelled — an earlier gate already failed"
            time.sleep(0.02)
        observed["cancelled"] = False
        return True, "coherence passed"

    monkeypatch.setattr(vc, "_run_coherence", _slow_coherence)

    started = time.monotonic()
    ok, reason, metrics = vc.validate_working_tree(
        str(tmp_path), modified_files=["x.py"], run_e2e=False, run_companion=False,
    )
    elapsed = time.monotonic() - started

    assert ok is False
    assert "ruff" in reason
    assert observed.get("cancelled") is True
    assert elapsed < 3.0, f"should not wait out coherence, took {elapsed:.1f}s"
    assert metrics["codelens_passed"] is False
    assert metrics["coherence_passed"] is None


def test_budget_sec_argument_overrides_the_env_default(monkeypatch, tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(vc, "_run_codelens", lambda *a: (True, "ok", {}))
    monkeypatch.setattr(vc, "_run_coherence", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(vc, "_run_pytest", lambda *a: (None, []))
    _ok, _reason, metrics = vc.validate_working_tree(
        str(tmp_path), modified_files=[], run_e2e=False, run_companion=False,
        budget_sec=42,
    )
    assert metrics["budget_sec"] == 42


# --------------------------------------------------------------------------
# Gate-step tool runner
# --------------------------------------------------------------------------

def test_gate_step_slug_matches_decomposed_children():
    from tools.genesis.reflexes.kanban import _gate_step_slug

    assert _gate_step_slug("efa-F-gate-2-coherence") == "coherence"
    assert _gate_step_slug("efa-F-gate-1-codelens") == "codelens"
    assert _gate_step_slug("efa-F-gate-5-companion") == "companion"
    # Ordinary build tasks must never be routed to the tool runner.
    assert _gate_step_slug("dt-iqe-01") is None
    assert _gate_step_slug("tsr-dash-01-d1") is None
    assert _gate_step_slug("") is None
