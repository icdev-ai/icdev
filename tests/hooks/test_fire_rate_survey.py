# CUI // SP-CTI
"""exa-bench-05 — the survey that had to come before enforcement.

Removing ``|| true`` converts nine advisory checks into hard blocks for every
session on the host at once, so the fire rate of each one on real work had to be
measured first. :mod:`tools.hooks.fire_rate_survey` is that measurement, and
these tests pin the two things that decide whether its numbers mean anything:

* it replays a corpus that actually carries the tool-call **operands**, and
* it never *executes* a check that would mutate the repository or the database
  in order to count it.

No host transcripts are read: every test drives a synthetic corpus written to
``tmp_path``, so the numbers here are deterministic.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.hooks import fire_rate_survey as survey  # noqa: E402


def write_transcript(path: Path, calls) -> Path:
    """A Claude Code transcript holding *calls* as ``tool_use`` blocks."""
    lines = []
    for name, tool_input in calls:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": name, "input": tool_input},
            ]},
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def corpus(tmp_path):
    return write_transcript(tmp_path / "session-a.jsonl", [
        ("Bash", {"command": "git status"}),
        ("Bash", {"command": "rm -rf /"}),                       # dangerous_rm
        ("Bash", {"command": "rm -rf .tmp/scratch"}),            # not dangerous
        ("Read", {"file_path": ".env"}),                         # env_file_access
        ("Read", {"file_path": ".env.example"}),                 # not a secret
        ("Bash", {"command": "git commit -m x"}),                # review_loop trigger
    ])


def test_it_reads_tool_calls_with_their_operands(corpus):
    calls = list(survey.iter_tool_calls([corpus]))
    assert len(calls) == 6
    assert calls[1].tool_input["command"] == "rm -rf /"


def test_a_truncated_line_is_skipped_not_raised(tmp_path):
    """Transcripts are appended to live, so the last line is often partial."""
    path = write_transcript(tmp_path / "s.jsonl", [("Bash", {"command": "ls"})])
    path.write_text(path.read_text(encoding="utf-8") + '{"message": {"cont',
                    encoding="utf-8")
    assert len(list(survey.iter_tool_calls([path]))) == 1


def test_counts_come_from_the_real_checks(corpus):
    report = survey.survey(transcripts=[corpus], samples=5)
    by_name = {c["check"]: c for c in report["checks"]}

    assert report["corpus"]["tool_calls"] == 6
    assert by_name["dangerous_rm"]["fired"] == 1          # `/`, not `.tmp/scratch`
    assert by_name["env_file_access"]["fired"] == 1       # `.env`, not `.env.example`
    assert by_name["dangerous_rm"]["fire_rate"] == pytest.approx(1 / 6, abs=1e-6)
    assert by_name["dangerous_rm"]["samples"][0]["operand"] == "rm -rf /"


def test_the_three_unsafe_to_execute_checks_are_reported_as_trigger_only():
    """A count is not a measurement when producing it changes the tree.

    ``review_loop_precommit`` runs ruff and re-stages what it rewrites,
    ``agent_rules`` writes an ``agent_findings`` row per match, and
    ``branch_deletion`` needs refs a months-old transcript no longer has. Each
    is labelled for what it is instead of contributing a number that looks like
    the others.
    """
    modes = {c.name: c.mode for c in survey.build_checks(REPO_ROOT)}
    assert modes["review_loop_precommit"] == "trigger_only"
    assert modes["agent_rules"] == "trigger_only"
    assert modes["branch_deletion"] == "trigger_only"
    # …and branch_deletion becomes measurable when the caller opts in.
    live = {c.name: c.mode for c in survey.build_checks(REPO_ROOT, live_git=True)}
    assert live["branch_deletion"] == "replayed"


def test_agent_rules_is_never_evaluated(corpus):
    """Evaluating a monitor-only rule pack writes evidence. Surveying must not."""
    check = next(c for c in survey.build_checks(REPO_ROOT) if c.name == "agent_rules")
    assert check.predicate is None and check.trigger is None
    assert next(
        c for c in survey.survey(transcripts=[corpus])["checks"]
        if c["check"] == "agent_rules"
    )["fired"] == 0


def test_every_check_the_hook_runs_is_surveyed():
    """A check missing from the survey is a check enabled unmeasured."""
    hook = survey._load_hook_module(REPO_ROOT)
    assert {c.name for c in survey.build_checks(REPO_ROOT)} == set(
        hook.CHECK_KILL_SWITCHES
    )


def test_hook_events_reports_unavailability_rather_than_a_zero():
    """The table cannot drive this replay, and must not read as if it could.

    ``post_tool_use.py`` persists tool-input KEY NAMES, never the values, so a
    survey sourced from ``hook_events`` reports zero fires for every check no
    matter what the sessions did — indistinguishable from "these checks are safe
    to enable".
    """
    result = survey.hook_events_operand_availability(limit=50)
    assert result["usable_as_corpus"] is False
    assert result["reason"]
    if not result["telemetry_available"]:
        # An unreachable database is reported as unmeasurable, not as empty.
        assert result["rows_sampled"] == 0
    else:
        assert result["rows_carrying_an_operand"] == 0


def test_samples_are_truncated(tmp_path):
    """Operands are session content, i.e. potentially CUI."""
    path = write_transcript(tmp_path / "s.jsonl", [
        ("Bash", {"command": "rm -rf /" + "x" * 500}),
    ])
    sample = next(
        c for c in survey.survey(transcripts=[path], samples=1)["checks"]
        if c["check"] == "dangerous_rm"
    )["samples"][0]
    assert len(sample["operand"]) <= survey.SAMPLE_CHARS + 1


def test_gate_exits_nonzero_only_for_replayed_checks(corpus, monkeypatch, capsys):
    monkeypatch.setattr(
        survey, "iter_transcripts", lambda **kwargs: [corpus]
    )
    assert survey.main(["--json", "--gate", "--max-fire-rate", "1.0"]) == 0
    assert survey.main(["--json", "--gate", "--max-fire-rate", "0.0"]) == 1
    assert "dangerous_rm" in capsys.readouterr().err
