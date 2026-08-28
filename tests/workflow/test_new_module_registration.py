# CUI // SP-CTI
"""A NEW tools/ CLI module must actually be registered (wire-reg-01).

THE WRONG DIRECTION. CLAUDE.md's 8-point checklist has three real gates and two that run
backwards: ``check_doc_command_paths`` asks whether a DOCUMENTED command resolves, never whether
a new tool GOT documented, and ``check_mcp_security`` asserts ``gap_handlers.py`` exists, never
that a new tool was registered. Both are fully satisfied by a tree in which nothing was ever
documented. This adds the missing direction.

SURVEYED BEFORE ARMING, over the 389 new ``tools/`` CLI modules added in the last 600 commits
(2026-08-27):

    tools/manifest/ row              284/389  73.0%  -> a gate fires on 27.0%
    commands.md / CLAUDE.md entry    163/389  41.9%  -> a gate fires on 58.1%
    tools/mcp/tool_registry.py        76/389  19.5%  -> a gate fires on 80.5%
    args/security_gates.yaml          73/389  18.8%  -> a gate fires on 81.2%

CLAUDE.md stands a check down at 1.63%, so none of these ships armed. The bottom two can never
be armed AS WRITTEN -- four fifths of tools are legitimately not MCP verbs and have no security
gate -- so they are report-only permanently.
"""
from __future__ import annotations

import importlib
import subprocess

import pytest

#: Bound through importlib, which is the idiom CLAUDE.md prescribes for this repo: a test that
#: patches via the STRING form can otherwise land on a different module object than the one the
#: code under test imported (`tools.x` vs `icdev.tools.x`). Binding the same way the patcher
#: addresses it keeps the two provably identical.
cc = importlib.import_module("tools.workflow.coherence_checker")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A synthetic repo root. Returns a helper that adds one module and registers it or not."""
    (tmp_path / "tools/manifest").mkdir(parents=True)
    (tmp_path / "docs/reference").mkdir(parents=True)
    (tmp_path / "tools/mcp").mkdir(parents=True)
    (tmp_path / "args").mkdir(parents=True)
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "tools/manifest.md").write_text("# index\n", encoding="utf-8")
    (tmp_path / "tools/manifest/things.md").write_text("# things\n", encoding="utf-8")
    (tmp_path / "docs/reference/commands.md").write_text("# commands\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# claude\n", encoding="utf-8")
    (tmp_path / "tools/mcp/tool_registry.py").write_text("TOOL_REGISTRY = {}\n", encoding="utf-8")
    (tmp_path / "args/security_gates.yaml").write_text("gates: {}\n", encoding="utf-8")
    (tmp_path / "tests/conftest.py").write_text("MINIMAL_ICDEV_SCHEMA = ''\n", encoding="utf-8")
    monkeypatch.setattr(cc, "PROJECT_ROOT", tmp_path)

    def _add(rel, *, cli=True, manifest=False, documented=False, creates_table=False):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "import argparse\n" if cli else "VALUE = 1\n"
        if cli:
            body += 'def main():\n    argparse.ArgumentParser()\n'
        if creates_table:
            body += 'SQL = "CREATE TABLE widgets (id int)"\n'
        path.write_text(body, encoding="utf-8")
        if manifest:
            (tmp_path / "tools/manifest/things.md").write_text(
                f"| Thing | {rel} | does a thing |\n", encoding="utf-8"
            )
        if documented:
            (tmp_path / "docs/reference/commands.md").write_text(
                f"python {rel} --json\n", encoding="utf-8"
            )
        monkeypatch.setattr(cc, "_added_tool_modules", lambda *a, **k: [rel])
        return rel

    return _add


# ---------------------------------------------------------------------------
# Registered in the checker at all
# ---------------------------------------------------------------------------


def test_the_check_is_registered():
    """A check nothing dispatches is the declared-but-unconsumed defect, in the check built to
    catch it."""
    assert cc.CHECK_REGISTRY["new_module_registration"] is cc.check_new_module_registration


def test_it_runs_in_the_fast_tier():
    """It is a per-task gate: the whole point is to catch the omission in the PR that makes it,
    not in a nightly sweep after the card closed."""
    assert "new_module_registration" in cc.select_checks("fast", changed_files=[])


# ---------------------------------------------------------------------------
# The two enforceable rungs
# ---------------------------------------------------------------------------


def test_it_fires_on_a_new_cli_tool_with_no_manifest_row(tree, monkeypatch):
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")
    tree("tools/things/doer.py", manifest=False, documented=True)

    result = cc.check_new_module_registration()

    assert result.status == "fail"
    assert any("no row in tools/manifest/" in m for m in result.missing)


def test_it_fires_on_a_new_cli_tool_that_was_never_documented(tree, monkeypatch):
    """The direction check_doc_command_paths does not check."""
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")
    tree("tools/things/doer.py", manifest=True, documented=False)

    result = cc.check_new_module_registration()

    assert result.status == "fail"
    assert any("not documented" in m for m in result.missing)


def test_it_passes_on_a_new_cli_tool_that_is_registered(tree, monkeypatch):
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")
    tree("tools/things/doer.py", manifest=True, documented=True)

    result = cc.check_new_module_registration()

    assert result.status == "pass", result.message
    assert result.missing == []


def test_a_library_is_not_a_cli_and_is_not_asked_for_a_command(tree, monkeypatch):
    """Demanding a commands.md line for a module with no command would be CLAUDE.md's 'never
    document a command whose file does not exist' rule inverted into a demand for a command
    that does not exist."""
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")
    tree("tools/things/lib.py", cli=False, manifest=False, documented=False)

    result = cc.check_new_module_registration()

    assert result.status == "pass"
    assert result.missing == []


# ---------------------------------------------------------------------------
# Points 3, 4, 6, 7 report and never fail -- 80.5% and 81.2% measured
# ---------------------------------------------------------------------------


def test_the_advisory_rungs_never_enter_missing(tree, monkeypatch):
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")
    tree("tools/things/doer.py", manifest=True, documented=True, creates_table=True)

    result = cc.check_new_module_registration()

    assert result.status == "pass", "an advisory rung must never fail a commit"
    assert result.missing == []
    joined = " ".join(result.actual)
    for advisory in ("tool_registry.py", "security_gates.yaml", "conftest.py", "companion.py"):
        assert advisory in joined, f"{advisory} advisory not reported"


def test_only_the_two_surveyed_rungs_are_enforceable():
    """Pinned so a later edit cannot quietly promote an 80%-fire rung to a hard gate."""
    assert cc._ENFORCEABLE_RUNGS == ("manifest", "commands")


# ---------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------


def test_it_ships_unarmed(tree, monkeypatch):
    monkeypatch.delenv(cc.NEW_MODULE_GATE_ENV, raising=False)
    assert cc.NEW_MODULE_GATE_DEFAULT == "report"
    tree("tools/things/doer.py", manifest=False, documented=False)

    result = cc.check_new_module_registration()

    assert result.status == "warn", "58.1% measured -- this cannot ship as a hard gate"
    assert result.missing == [], "report mode must not count anything against the commit"
    assert "no row in tools/manifest/" in result.message, "it must still SAY what is missing"


def test_an_unknown_mode_falls_back_to_the_default_never_to_enforce(monkeypatch):
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "yes-please")
    assert cc._new_module_gate_mode() == cc.NEW_MODULE_GATE_DEFAULT


def test_off_is_honoured(tree, monkeypatch):
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "off")
    tree("tools/things/doer.py", manifest=False, documented=False)

    assert cc.check_new_module_registration().status == "pass"


# ---------------------------------------------------------------------------
# Never a clean bill it did not earn
# ---------------------------------------------------------------------------


def test_an_unreadable_diff_is_warn_not_ok(monkeypatch):
    """A shallow clone has no merge base. 'git could not tell' is not 'nothing was added'."""
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")
    monkeypatch.setattr(cc, "_added_tool_modules", lambda *a, **k: None)

    result = cc.check_new_module_registration()

    assert result.status == "warn"
    assert "not a clean bill" in result.message


def test_a_full_tree_run_never_counts_the_existing_tree_against_the_commit(monkeypatch):
    """105 historical modules lack a docs entry. Re-reporting them to every session is how a
    check gets ignored -- so the scope is what THIS diff ADDS, never the tree.

    Asserted on `missing`, NOT on `status`, and that distinction is the point. CI checks out
    shallow, so `_added_tool_modules` legitimately returns None there and the check reports
    `warn: not a clean bill` -- which is the CORRECT behaviour and was the first version of this
    test's undoing. What must hold on every runner, at every clone depth, is that the existing
    tree never enters `missing`. The measured path is exercised against a real repository in
    `test_added_tool_modules_*` below rather than against whatever history the runner happens
    to have.
    """
    monkeypatch.setenv(cc.NEW_MODULE_GATE_ENV, "enforce")

    result = cc.check_new_module_registration()

    assert result.missing == [], result.message
    assert result.status in ("pass", "warn"), result.message


# ---------------------------------------------------------------------------
# The git seam, against a real repository
# ---------------------------------------------------------------------------


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo with a base commit. The fixture above never exercises git itself."""
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/existing.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "base", cwd=tmp_path)
    _git("branch", "-f", "base", cwd=tmp_path)
    return tmp_path


def test_added_tool_modules_lists_only_what_was_added(repo):
    (repo / "tools/brand_new.py").write_text("import argparse\n", encoding="utf-8")
    (repo / "tools/existing.py").write_text("VALUE = 2\n", encoding="utf-8")  # MODIFIED
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "add one, modify one", cwd=repo)

    added = cc._added_tool_modules("base", root=repo)

    assert added == ["tools/brand_new.py"], (
        "a module that merely gained a line is none of this check's business"
    )


def test_added_tool_modules_is_empty_when_nothing_was_added(repo):
    """Empty is a MEASURED answer and must not be confused with None."""
    assert cc._added_tool_modules("base", root=repo) == []


def test_added_tool_modules_returns_none_for_an_unknown_base(repo):
    """None is 'git could not tell'. Merging it with [] would report a clean bill for a
    repository the check failed to read -- which is exactly what CI's shallow clone would have
    produced."""
    assert cc._added_tool_modules("no-such-ref", root=repo) is None


def test_it_scopes_to_added_files_not_changed_files():
    """A module that merely gained a line is none of this check's business."""
    import inspect

    src = inspect.getsource(cc._added_tool_modules)
    assert "--diff-filter=A" in src


def test_every_status_this_check_returns_is_one_the_report_counts():
    """A status outside the counted vocabulary makes a check VANISH FROM THE TOTALS, silently.

    This check first shipped returning `status="ok"` while `run_all_checks` counts `"pass"`, so
    the report read 50 + 2 + 11 = 63 against total_checks 64 -- a check that ran, produced a
    verdict, and was counted as nothing. It failed green in every per-check invocation and only
    surfaced in the one assertion that reconciles the buckets against the total.
    """
    import ast
    import inspect

    counted = {"pass", "fail", "warn"}
    returned = {
        kw.value.value
        for node in ast.walk(ast.parse(inspect.getsource(cc.check_new_module_registration)))
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "status" and isinstance(kw.value, ast.Constant)
    }
    assert returned, "no literal status= found -- the assertion below would be vacuous"
    assert returned <= counted, f"statuses the report does not count: {sorted(returned - counted)}"
