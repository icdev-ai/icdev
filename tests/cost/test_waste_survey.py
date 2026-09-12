# CUI // SP-CTI
"""The waste survey's rules, over fixture transcripts (xrv-cost-03).

Every test here builds its own transcript tree and its own ``.claude`` /
``.agents`` tree, so nothing depends on the machine's real session history.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from icdev.tools.cost import waste_survey as ws


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _assistant(blocks):
    return {"type": "assistant", "message": {"content": blocks}}


def _tool_use(name, tool_input):
    return {"type": "tool_use", "name": name, "input": tool_input}


def _user(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def write_transcript(root: Path, project: str, session: str, records) -> Path:
    directory = root / project
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def calls(*pairs):
    """``(tool_name, tool_input)`` pairs, the shape ``one_shot`` consumes."""
    return list(pairs)


# ---------------------------------------------------------------------------
# 1. The retry rule, stated verbatim in the module docstring
# ---------------------------------------------------------------------------


def test_edit_bash_edit_same_file_is_one_retry():
    """A, Bash, A = 1 retry -- something ran between two edits of one file."""
    result = ws.one_shot(calls(
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Bash", {"command": "pytest"}),
        ("Edit", {"file_path": "/repo/a.py"}),
    ))
    assert result["edit_calls"] == 2
    assert result["retries"] == 1
    assert result["one_shot_rate_pct"] == 50.0
    assert dict(result["retried_files"]) == {"/repo/a.py": 1}


def test_edit_bash_different_file_is_zero_retries():
    """A, Bash, B = 0 -- the Bash call separates two unrelated edits."""
    result = ws.one_shot(calls(
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Bash", {"command": "pytest"}),
        ("Edit", {"file_path": "/repo/b.py"}),
    ))
    assert result["edit_calls"] == 2
    assert result["retries"] == 0
    assert result["one_shot_rate_pct"] == 100.0


def test_two_edits_with_nothing_between_is_not_a_retry():
    """A, A = 0 -- two hunks, not two attempts. The rule is narrower than
    'edited twice', which is the whole reason it keys on the Bash call."""
    result = ws.one_shot(calls(
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Edit", {"file_path": "/repo/a.py"}),
    ))
    assert result["edit_calls"] == 2
    assert result["retries"] == 0


def test_a_second_run_re_arms_the_same_file():
    """A, Bash, A, Bash, A = 2 retries -- each run re-arms every edited file."""
    result = ws.one_shot(calls(
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Bash", {"command": "pytest"}),
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Bash", {"command": "pytest"}),
        ("Edit", {"file_path": "/repo/a.py"}),
    ))
    assert result["retries"] == 2
    assert result["edit_calls"] == 3


def test_one_shot_rate_is_none_when_nothing_was_edited():
    result = ws.one_shot(calls(("Bash", {"command": "ls"})))
    assert result["edit_calls"] == 0
    assert result["one_shot_rate_pct"] is None


# ---------------------------------------------------------------------------
# 2. Re-read threshold
# ---------------------------------------------------------------------------


def test_reread_threshold_is_inclusive_and_excludes_below_it():
    session = calls(
        ("Read", {"file_path": "/repo/hot.py"}),
        ("Read", {"file_path": "/repo/hot.py"}),
        ("Read", {"file_path": "/repo/hot.py"}),
        ("Read", {"file_path": "/repo/cool.py"}),
        ("Read", {"file_path": "/repo/cool.py"}),
    )
    assert ws.rereads(session, threshold=3) == {"/repo/hot.py": 3}
    assert ws.rereads(session, threshold=2) == {"/repo/hot.py": 3, "/repo/cool.py": 2}
    assert ws.rereads(session, threshold=4) == {}


def test_rereads_ignore_writes_and_commands():
    session = calls(
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Edit", {"file_path": "/repo/a.py"}),
        ("Bash", {"command": "cat /repo/a.py"}),
    )
    assert ws.rereads(session, threshold=3) == {}


# ---------------------------------------------------------------------------
# 3. Ghost definitions
# ---------------------------------------------------------------------------


@pytest.fixture()
def definition_tree(tmp_path: Path) -> Path:
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / ".claude" / "commands" / "used_cmd.md").write_text("x", encoding="utf-8")
    (tmp_path / ".claude" / "commands" / "ghost_cmd.md").write_text("x", encoding="utf-8")
    (tmp_path / ".agents" / "skills" / "used-skill").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "used-skill" / "SKILL.md").write_text(
        "x", encoding="utf-8")
    (tmp_path / ".agents" / "skills" / "ghost-skill").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "ghost-skill" / "SKILL.md").write_text(
        "x", encoding="utf-8")
    # .claude/agents is deliberately NOT created -- an absent root must read
    # `absent`, never zero ghosts.
    return tmp_path


def test_ghosts_are_named_and_invoked_definitions_are_not(definition_tree):
    from collections import Counter

    report = ws.ghost_definitions(
        definition_tree, Counter({"used_cmd": 1, "used-skill": 4}))
    assert report["kinds"]["command"]["names"] == ["ghost_cmd"]
    assert report["kinds"]["command"]["not_invoked_in_window"] == 1
    assert report["kinds"]["skill"]["names"] == ["ghost-skill"]
    assert report["totals"]["declared"] == 4
    assert report["totals"]["not_invoked_in_window"] == 2
    assert report["totals"]["not_invoked_pct"] == 50.0


def test_an_absent_definition_root_is_absent_not_zero_ghosts(definition_tree):
    from collections import Counter

    report = ws.ghost_definitions(definition_tree, Counter())
    agent = report["kinds"]["agent"]
    assert agent["state"] == "absent"
    # The whole point: None, so no surface can print "0 ghosts" for a tree
    # nobody looked at.
    assert agent["declared"] is None
    assert agent["not_invoked_in_window"] is None
    assert agent["not_invoked_pct"] is None


def test_the_verdict_is_never_the_word_dead(definition_tree):
    from collections import Counter

    report = ws.ghost_definitions(definition_tree, Counter())
    assert "not_invoked_in_window" in report["kinds"]["command"]
    assert "dead" not in json.dumps(report["kinds"])
    assert "NOT dead" in report["caveat"]


def test_all_three_invocation_detectors_fire(tmp_path: Path):
    root = tmp_path / "projects"
    write_transcript(root, "proj", "s1", [
        _assistant([_tool_use("Skill", {"skill": "used-skill"})]),
        _user("<command-name>/used_cmd</command-name>"),
        _user("/typed_cmd please run it"),
        # An unanchored matcher would read this path as an invocation.
        _user("look at tools/ci/gated_test_list.py and /not/at/line/start"),
    ])
    found = ws.invoked_names(sorted(root.glob("*/*.jsonl")))
    assert found["names"]["used-skill"] == 1
    assert found["names"]["used_cmd"] == 1
    assert found["names"]["typed_cmd"] == 1
    assert found["by_detector"] == {
        "tool_use": 1, "command_tag": 1, "slash_line": 1}
    assert "not" not in found["names"]


# ---------------------------------------------------------------------------
# 4. Config bloat
# ---------------------------------------------------------------------------


def test_config_bloat_expands_imports_and_divides_by_the_window(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("a" * 400 + "\n@extra.md\n", encoding="utf-8")
    (tmp_path / "extra.md").write_text("b" * 800, encoding="utf-8")
    report = ws.config_bloat(tmp_path, sessions=10, window_days=5.0)
    assert report["state"] == "measured"
    assert report["token_basis"] == "chars_div_4"
    assert report["approx_tokens_with_imports"] == (
        report["approx_tokens"] + 200)
    assert [i["ref"] for i in report["imports"]] == ["extra.md"]
    assert report["imports"][0]["state"] == "resolved"
    assert report["tokens_loaded_in_window"] == (
        report["approx_tokens_with_imports"] * 10)
    assert report["tokens_loaded_per_day"] == pytest.approx(
        report["tokens_loaded_in_window"] / 5.0)


def test_a_missing_import_is_reported_not_silently_dropped(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("@gone.md\n", encoding="utf-8")
    report = ws.config_bloat(tmp_path, sessions=1, window_days=1.0)
    assert report["imports"] == [
        {"ref": "gone.md", "state": "missing", "bytes": None,
         "approx_tokens": None}]


def test_tokens_per_day_is_none_with_no_sessions(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("x" * 40, encoding="utf-8")
    report = ws.config_bloat(tmp_path, sessions=None, window_days=7.0)
    assert report["tokens_loaded_in_window"] is None
    assert report["tokens_loaded_per_day"] is None
    # The file itself was still measured -- absence of sessions is not
    # absence of a CLAUDE.md.
    assert report["bytes"] == 40


# ---------------------------------------------------------------------------
# 5. MCP, from the transcripts, beside the Studio figure
# ---------------------------------------------------------------------------


def test_split_mcp_name_keeps_hyphenated_servers_and_underscored_tools():
    assert ws.split_mcp_name("mcp__icdev-unified__kanban_get_task") == (
        "icdev-unified", "kanban_get_task")
    assert ws.split_mcp_name("mcp__srv__a__b") == ("srv", "a__b")
    assert ws.split_mcp_name("Bash") is None
    assert ws.split_mcp_name("mcp__onlyserver") is None


def test_two_mcp_calls_are_counted_and_an_unrelated_registry_tool_is_not(
    tmp_path: Path, monkeypatch
):
    """A fixture registry stands in for the 472-entry real one."""
    by_session = {
        "s1": [
            ("mcp__icdev-unified__kanban_get_task", {}),
            ("mcp__icdev-unified__kanban_get_task", {}),
            ("Bash", {"command": "ls"}),
        ]
    }
    monkeypatch.setattr(
        ws, "_registry_names",
        lambda: ["kanban_get_task", "never_called_tool"])
    monkeypatch.setattr(ws, "icdev_mcp_servers", lambda root: ["icdev-unified"])
    report = ws.mcp_usage(by_session, window_days=7.0, studio=False)
    transcript = report["transcript"]
    assert transcript["calls"] == 2
    assert transcript["distinct_tools"] == 1
    assert transcript["by_server"] == {"icdev-unified": 2}
    assert transcript["registry_invoked_from_claude_code"] == 1
    assert transcript["not_invoked_from_claude_code_in_window"] == 1
    assert transcript["not_invoked_names"] == ["never_called_tool"]


def test_an_unreadable_registry_is_none_never_an_empty_declared_set(monkeypatch):
    monkeypatch.setattr(ws, "_registry_names", lambda: None)
    monkeypatch.setattr(ws, "icdev_mcp_servers", lambda root: ["x"])
    report = ws.mcp_usage({"s": [("mcp__x__y", {})]}, window_days=1.0, studio=False)
    transcript = report["transcript"]
    assert transcript["registry_state"] == "unreadable"
    assert transcript["registry_declared"] is None
    # An empty declared list would report every observed tool as fully
    # covered -- the reassurance this module refuses.
    assert transcript["not_invoked_from_claude_code_in_window"] is None


def test_the_studio_figure_is_reported_beside_the_transcript_one_never_merged(
    monkeypatch
):
    monkeypatch.setattr(ws, "_registry_names", lambda: ["t"])
    monkeypatch.setattr(ws, "icdev_mcp_servers", lambda root: ["srv"])
    monkeypatch.setattr(
        ws, "studio_dispatch_only",
        lambda days: {"state": "measured", "declared": 472, "consumed": 4,
                      "inert": 468, "events": 9, "note": "n"})
    report = ws.mcp_usage({"s": [("mcp__srv__t", {})]}, window_days=7.0)
    assert report["transcript"]["registry_invoked_from_claude_code"] == 1
    assert report["studio_dispatch_only"]["consumed"] == 4
    # Two separate sub-reports; nothing sums or reconciles them.
    assert "consumed" not in report["transcript"]
    assert "registry_invoked_from_claude_code" not in report["studio_dispatch_only"]


def test_studio_is_not_consulted_under_no_studio():
    report = ws.mcp_usage({}, window_days=7.0, studio=False)
    assert report["studio_dispatch_only"]["state"] == "not_consulted"
    # not_consulted is its own state and is never 0 consumed.
    assert "consumed" not in report["studio_dispatch_only"]


# ---------------------------------------------------------------------------
# The whole survey
# ---------------------------------------------------------------------------


def test_empty_window_is_unmeasurable_and_every_rate_is_none(tmp_path: Path):
    root = tmp_path / "projects"
    root.mkdir()
    report = ws.survey(root=root, since_days=7.0, repo_root=tmp_path,
                       studio=False)
    assert report["state"] == "unmeasurable"
    assert "unmeasurable_reason" in report
    one = report["one_shot"]
    assert one["edit_calls"] is None
    assert one["retries"] is None
    assert one["one_shot_rate_pct"] is None
    assert report["rereads"]["offenders"] is None
    assert report["config_bloat"]["tokens_loaded_per_day"] is None
    assert report["definitions"]["totals"]["not_invoked_pct"] is None
    # Never 0.0 and never 100.0 -- args/perfect_score_gate.yaml is ratcheted
    # to zero for exactly this.
    for value in (one["one_shot_rate_pct"],
                  report["definitions"]["totals"]["not_invoked_pct"]):
        assert value not in (0.0, 100.0)


def test_survey_end_to_end_over_a_fixture_transcript(tmp_path: Path, monkeypatch):
    root = tmp_path / "projects"
    (tmp_path / "CLAUDE.md").write_text("z" * 4000, encoding="utf-8")
    (tmp_path / ".claude" / "commands").mkdir(parents=True)
    (tmp_path / ".claude" / "commands" / "ghost_cmd.md").write_text(
        "x", encoding="utf-8")
    write_transcript(root, "proj", "s1", [
        _assistant([_tool_use("Edit", {"file_path": "/repo/a.py"})]),
        _assistant([_tool_use("Bash", {"command": "pytest"})]),
        _assistant([_tool_use("Edit", {"file_path": "/repo/a.py"})]),
        _assistant([_tool_use("Read", {"file_path": "/repo/b.py"})]),
        _assistant([_tool_use("Read", {"file_path": "/repo/b.py"})]),
        _assistant([_tool_use("Read", {"file_path": "/repo/b.py"})]),
        _assistant([_tool_use("mcp__icdev-unified__kanban_get_task", {})]),
    ])
    monkeypatch.setattr(ws, "_registry_names",
                        lambda: ["kanban_get_task", "never_called_tool"])
    monkeypatch.setattr(ws, "icdev_mcp_servers", lambda root: ["icdev-unified"])
    report = ws.survey(root=root, since_days=None, repo_root=tmp_path,
                       studio=False)
    assert report["state"] == "measured"
    assert report["window"]["sessions"] == 1
    assert report["one_shot"] == {
        **report["one_shot"],
        "edit_calls": 2, "retries": 1, "one_shot_rate_pct": 50.0,
    }
    assert report["rereads"]["offenders"] == 1
    assert report["rereads"]["top_offenders"][0]["path"] == "/repo/b.py"
    assert report["rereads"]["read_tool_calls"] == 3
    assert report["rereads"]["run_tool_calls"] == 1
    assert report["definitions"]["kinds"]["command"]["names"] == ["ghost_cmd"]
    assert report["config_bloat"]["approx_tokens"] == 1000
    assert report["mcp"]["transcript"]["calls"] == 1
    assert report["mcp"]["transcript"]["not_invoked_names"] == [
        "never_called_tool"]


def test_a_truncated_final_line_is_skipped_not_raised(tmp_path: Path):
    root = tmp_path / "projects"
    directory = root / "proj"
    directory.mkdir(parents=True)
    path = directory / "s1.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(
            _assistant([_tool_use("Edit", {"file_path": "/a.py"})])) + "\n")
        handle.write('{"type": "assistant", "message": {"conte')
    report = ws.survey(root=root, since_days=None, repo_root=tmp_path,
                       studio=False)
    assert report["one_shot"]["edit_calls"] == 1


def test_main_exits_two_when_the_transcript_root_is_absent(tmp_path, capsys):
    code = ws.main(["--root", str(tmp_path / "nope"), "--json", "--no-studio"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    # Exit 2 is "the survey could not be produced", never a clean survey.
    assert payload["state"] == "unmeasurable"


def test_another_servers_identically_named_tool_is_not_credited(monkeypatch):
    """MEASURED, not hypothetical: TOOL_REGISTRY declares browser_click,
    browser_navigate and browser_type, and the Playwright MCP server declares
    tools of exactly those names. Bare-name matching credited Playwright's
    calls to ICDEV's registry and reported 6 registry tools reached from
    Claude Code over 30 days where 3 were."""
    by_session = {"s": [
        ("mcp__playwright__browser_click", {}),
        ("mcp__playwright__browser_navigate", {}),
        ("mcp__icdev-unified__kanban_get_task", {}),
    ]}
    monkeypatch.setattr(
        ws, "_registry_names",
        lambda: ["browser_click", "browser_navigate", "kanban_get_task"])
    monkeypatch.setattr(ws, "icdev_mcp_servers", lambda root: ["icdev-unified"])
    report = ws.mcp_usage(by_session, window_days=30.0, studio=False)["transcript"]
    assert report["server_attribution"] == "server_qualified"
    assert report["registry_invoked_from_claude_code"] == 1
    assert sorted(report["not_invoked_names"]) == [
        "browser_click", "browser_navigate"]
    # The calls are still COUNTED -- they happened. They are simply not
    # attributed to a registry they did not come from.
    assert report["calls"] == 3


def test_an_unreadable_mcp_config_falls_back_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(ws, "_registry_names", lambda: ["browser_click"])
    report = ws.mcp_usage(
        {"s": [("mcp__playwright__browser_click", {})]},
        window_days=30.0, studio=False, repo_root=tmp_path)["transcript"]
    # No .mcp.json under tmp_path: bare matching is used, and the field names
    # it so nobody quotes the inflated figure as the measured one.
    assert report["server_attribution"] == "bare_name_fallback"
    assert report["serving_servers"] is None
    assert report["registry_invoked_from_claude_code"] == 1


def test_icdev_mcp_servers_reads_the_checkouts_own_config(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "icdev-unified": {"command": "python",
                          "args": ["C:/AI/ICDev/tools/mcp/unified_server.py"]},
        "playwright": {"command": "node", "args": ["/npm/@playwright/mcp/cli.js"]},
    }}), encoding="utf-8")
    # Derived from the command line, never a hardcoded server name.
    assert ws.icdev_mcp_servers(tmp_path) == ["icdev-unified"]
    assert ws.icdev_mcp_servers(tmp_path / "nowhere") is None


def test_no_sessions_withholds_the_ghost_verdict_rather_than_scoring_100(
    definition_tree, tmp_path: Path
):
    """A window with NO session must not report 100% of definitions uninvoked.

    CAUGHT LIVE, on this module, by running the CLI against an empty
    transcript root while `--repo-root` still pointed at a real checkout: 93
    declared definitions, 0 invoked, `not_invoked_pct: 100.0`. Nothing could
    have been invoked, because nothing ran -- so that 100.0 is a perfect score
    over a denominator of zero sessions, which is exactly what
    args/perfect_score_gate.yaml is ratcheted to zero for. The 26 tests
    written before it all passed, because each built a tmp_path with no
    definition tree, so `absent` masked the case.
    """
    empty_root = tmp_path / "no_sessions"
    empty_root.mkdir()
    report = ws.survey(root=empty_root, since_days=7.0,
                       repo_root=definition_tree, studio=False)
    assert report["state"] == "unmeasurable"
    defs = report["definitions"]
    assert defs["sessions_observed"] is False
    assert defs["totals"]["not_invoked_pct"] is None
    assert defs["totals"]["not_invoked_in_window"] is None
    command = defs["kinds"]["command"]
    assert command["state"] == "unmeasurable_no_sessions"
    assert command["not_invoked_in_window"] is None
    assert command["not_invoked_pct"] is None
    # What the checkout DECLARES does not depend on anyone having run a
    # session, so it is still reported -- only the verdict is withheld.
    assert command["declared"] == 2


def test_the_ghost_verdict_is_measured_once_a_session_exists(
    definition_tree, tmp_path: Path
):
    """The control for the test above: one session makes it measurable again,
    so the withholding is about ABSENCE of evidence and not a dead branch."""
    root = tmp_path / "projects"
    write_transcript(root, "proj", "s1", [
        _assistant([_tool_use("Skill", {"skill": "used-skill"})]),
    ])
    report = ws.survey(root=root, since_days=None, repo_root=definition_tree,
                       studio=False)
    defs = report["definitions"]
    assert defs["sessions_observed"] is True
    assert defs["kinds"]["command"]["state"] == "declared"
    assert defs["kinds"]["command"]["not_invoked_pct"] == 100.0
    assert defs["kinds"]["skill"]["not_invoked_in_window"] == 1
