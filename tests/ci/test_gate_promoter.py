# CUI // SP-CTI
"""rem-tst-06: promoting an ungated module, without turning `main` red.

`ungated_test_census.py` measures which ungated modules pass and deliberately
promotes nothing. Nothing consumed that measurement, so the snapshot aged —
three days stale when this was written — and the backlog shrank only when a
human hand-moved a file.

THE SAFEGUARD IS THE WHOLE POINT. The census runs each module ALONE. Green-alone
is not green-in-suite, and this repo has the scar twice over: four cortex/dashboard
modules that passed alone and failed in-suite (or the reverse) by registering a
blueprint onto a shared app singleton, and `kpr-watch-03` on 2026-08-19, whose
CI failure looked like flake and was an order dependency.

So the properties pinned here are the ones that stop this tool causing the
outage it exists to avoid: a module is promoted only when green BOTH ways, a
failed batch promotes NOTHING, and the ceiling only ever ratchets DOWN.
"""
from __future__ import annotations

import json

import pytest

from tools.ci import gate_promoter as gp


def _census(rows):
    return {"results": rows}


def _row(f, status="passed", duration=1.0):
    return {"file": f, "status": status, "duration_s": duration}


# ── who is even a candidate ────────────────────────────────────────────────
def test_only_modules_the_census_found_green_are_candidates():
    census = _census([_row("a.py"), _row("b.py", status="failed")])
    assert gp.candidates(census, ["a.py", "b.py"], 10) == ["a.py"]


@pytest.mark.parametrize("status", ["failed", "timeout", "no-tests", "collection-error"])
def test_a_non_passing_status_is_never_promotable(status):
    """`no-tests` matters as much as `failed`: "no tests ran" is not "the tests
    pass", and gating an empty module buys nothing while looking like progress."""
    census = _census([_row("a.py", status=status)])
    assert gp.candidates(census, ["a.py"], 10) == []


def test_a_module_already_gated_is_not_a_candidate():
    """The backlog is the source of truth for "still ungated". A module missing
    from it has already been promoted, and re-promoting it would duplicate the
    entry in two fragments."""
    census = _census([_row("a.py"), _row("b.py")])
    assert gp.candidates(census, ["b.py"], 10) == ["b.py"]


def test_cheapest_first():
    """A promoted module joins EVERY future CI run, so the cheap ones buy the
    most coverage per second of build time."""
    census = _census([_row("slow.py", duration=30.0), _row("fast.py", duration=0.5)])
    assert gp.candidates(census, ["slow.py", "fast.py"], 10) == ["fast.py", "slow.py"]


def test_the_limit_is_respected():
    census = _census([_row(f"t{i}.py", duration=i) for i in range(20)])
    assert len(gp.candidates(census, [f"t{i}.py" for i in range(20)], 5)) == 5


# ── the two-phase verification ─────────────────────────────────────────────
class _Proc:
    def __init__(self, rc):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def _wire(monkeypatch, tmp_path, *, alone_fail=(), suite_rc=0, backlog=("a.py", "b.py")):
    """Stub the pytest runs and point the writable files at tmp_path."""
    calls = {"alone": [], "suite": 0}

    def fake_pytest(files, timeout):
        if len(files) == 1:
            calls["alone"].append(files[0])
            return _Proc(1 if files[0] in alone_fail else 0)
        calls["suite"] += 1
        return _Proc(suite_rc)

    monkeypatch.setattr(gp, "_pytest", fake_pytest)
    monkeypatch.setattr(gp, "backlog_modules", lambda path=None: list(backlog))
    monkeypatch.setattr(gp, "load_census",
                        lambda path=None: _census([_row(m) for m in backlog]))
    import tools.ci.gated_test_list as gtl
    monkeypatch.setattr(gtl, "resolve", lambda name="core", root=None: ["gated_one.py"])
    return calls


def test_a_module_green_both_ways_is_promoted(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    report = gp.promote(limit=2, apply=False)
    assert report["promoted"] == ["a.py", "b.py"]
    assert report["in_suite"]["ok"] is True


def test_a_module_that_fails_ALONE_is_dropped_before_the_suite_run(monkeypatch, tmp_path):
    """Phase 1 is the cheap filter: a module failing alone must never reach the
    expensive in-suite run, and must not drag the batch down with it."""
    calls = _wire(monkeypatch, tmp_path, alone_fail={"a.py"})
    report = gp.promote(limit=2, apply=False)
    assert report["promoted"] == ["b.py"]
    assert [r["file"] for r in report["rejected"]] == ["a.py"]
    assert report["rejected"][0]["phase"] == "alone"


def test_a_FAILED_IN_SUITE_batch_promotes_NOTHING(monkeypatch, tmp_path):
    """THE PROPERTY THAT STOPS THIS TOOL BREAKING MAIN. The failure may be an
    interaction BETWEEN two survivors, so promoting "the innocent ones" could
    ship exactly the interacting pair. Fail closed on the whole batch."""
    _wire(monkeypatch, tmp_path, suite_rc=1)
    report = gp.promote(limit=2, apply=False)
    assert report["promoted"] == []
    assert {r["phase"] for r in report["rejected"]} == {"in_suite"}
    assert "nothing promoted" in report["reason"]


def test_green_alone_is_NOT_sufficient(monkeypatch, tmp_path):
    """Stated as its own test because it is the entire reason this tool is not
    just `census --apply`."""
    _wire(monkeypatch, tmp_path, suite_rc=1)
    assert gp.promote(limit=2, apply=False)["promoted"] == []


def test_an_empty_gated_list_refuses_rather_than_passing(monkeypatch, tmp_path):
    """With nothing to run against, the in-suite phase proves nothing. It must
    refuse, not wave the batch through."""
    _wire(monkeypatch, tmp_path)
    import tools.ci.gated_test_list as gtl
    monkeypatch.setattr(gtl, "resolve", lambda name="core", root=None: [])
    assert gp.promote(limit=2, apply=False)["promoted"] == []


# ── writing, and the ratchet ───────────────────────────────────────────────
def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    wrote = []
    monkeypatch.setattr(gp, "_write_fragment", lambda m: wrote.append(m) or "x")
    report = gp.promote(limit=2, apply=False)
    assert report["applied"] is False
    assert wrote == [], "a dry run must not write"


def test_the_ceiling_only_ever_goes_DOWN(monkeypatch, tmp_path):
    """`backlog_max` is a ratchet in CLAUDE.md's words. A tool that could raise
    it would be the one thing this file must never do."""
    cfg = tmp_path / "gate.yaml"
    cfg.write_text("backlog_max: 100\nother: 1\n", encoding="utf-8")
    monkeypatch.setattr(gp, "GATE_CONFIG", cfg)

    assert gp._ratchet_ceiling(90) == 90
    assert "backlog_max: 90" in cfg.read_text(encoding="utf-8")

    assert gp._ratchet_ceiling(95) is None, "a HIGHER value must be refused"
    assert "backlog_max: 90" in cfg.read_text(encoding="utf-8")


def test_promotion_writes_a_PER_RUN_fragment_not_core_txt(monkeypatch, tmp_path):
    """tsg-policy-03 measured `core.txt` as the largest merge-collision surface
    in the repository — 82.8% of merged kanban PRs touched it. Two runs writing
    two files cannot collide at all."""
    monkeypatch.setattr(gp, "FRAGMENT_DIR", tmp_path)
    rel = gp._write_fragment(["a.py", "b.py"])
    written = list(tmp_path.glob("auto-promote-*.txt"))
    assert len(written) == 1 and "core.txt" not in rel
    body = written[0].read_text(encoding="utf-8")
    assert "a.py" in body and "b.py" in body
    assert "BOTH alone" in body, "the fragment must record WHY it was safe to add"


def test_dropping_from_the_backlog_keeps_everything_else(monkeypatch, tmp_path):
    f = tmp_path / "backlog.txt"
    f.write_text("# comment\na.py\nb.py\nc.py\n", encoding="utf-8")
    monkeypatch.setattr(gp, "BACKLOG", f)
    assert gp._drop_from_backlog(["b.py"]) == 2
    kept = f.read_text(encoding="utf-8")
    assert "a.py" in kept and "c.py" in kept and "b.py" not in kept
    assert "# comment" in kept, "comments must survive the rewrite"


# ── honest absence ─────────────────────────────────────────────────────────
def test_an_unreadable_census_is_UNMEASURABLE_not_empty(monkeypatch, tmp_path):
    """An unreadable measurement is not a measurement of zero. Promoting on one
    would be acting on a file that does not exist."""
    monkeypatch.setattr(gp, "load_census", lambda path=None: {})
    report = gp.promote(limit=2)
    assert report["measurable"] is False
    assert "promoted" not in report


def test_it_is_never_a_gate():
    """Promotion is an improvement. Failing a build because an improvement was
    unavailable would make the improvement unwelcome."""
    import inspect

    src = inspect.getsource(gp.main)
    assert "--gate" not in src
    assert gp.main(["--plan", "--json"]) == 0


def test_the_report_json_round_trips():
    out = gp.plan(limit=1)
    json.dumps(out)


# ── the rendered commit / PR text ──────────────────────────────────────────
def test_the_report_renderer_states_WHY_it_was_safe():
    """The commit message is the only place a future reader learns that both
    verifications ran. A promotion commit that just lists files reads like a
    bulk add, which is the thing CLAUDE.md forbids."""
    from tools.ci import gate_promoter_report as rep

    msg = rep.commit_message({"promoted": ["tests/a.py"], "backlog_size": 10,
                              "backlog_size_after": 9, "backlog_max": 9})
    assert "ALONE" in msg and "IN-SUITE" in msg
    assert "tests/a.py" in msg
    assert "10 -> 9" in msg


def test_the_pr_body_lists_what_was_REJECTED_too():
    """A run that promoted two and rejected eight is a different event from one
    that promoted two out of two, and the PR must not read the same."""
    from tools.ci import gate_promoter_report as rep

    body = rep.pr_body({"promoted": ["tests/a.py"],
                        "rejected": [{"file": "tests/b.py", "phase": "in_suite"}]})
    assert "tests/a.py" in body and "tests/b.py" in body
    assert "in_suite" in body


def test_the_renderer_never_builds_prose_inside_the_workflow():
    """Split out because multi-line prose in a YAML `run:` block made that file
    invalid YAML the first time it was written — a heredoc body at column 0
    terminates the block scalar, and the scanner error surfaces far from the
    mistake. Here it is testable; there it never was."""
    from pathlib import Path

    wf = Path(__file__).resolve().parents[2] / ".github/workflows/gate-promoter.yml"
    text = wf.read_text(encoding="utf-8")
    assert "gate_promoter_report.py" in text
    import yaml
    assert yaml.safe_load(text), "the workflow must remain valid YAML"
