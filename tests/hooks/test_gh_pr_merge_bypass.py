# CUI // SP-CTI
"""kpr-rvfy-05 — a raw ``gh pr merge`` on a kanban-linked PR must be refused.

``tools/kanban/cli.py --set-status <id> done --merge`` routes through
``tools/kanban/land.py``, which runs THIRTEEN checks and writes ``done`` only
after the forge CONFIRMS the merge. A raw ``gh pr merge`` runs none of them, and
on 2026-08-29 an operator session landed twelve PRs that way. Branch protection
is not the alternative: it is unavailable on the private repo (403) and it
accepts a repo where no check ever reported — the dead-runner case the door's
``ci_green`` refuses.

THE OTHER HALF IS AS IMPORTANT AS THE REFUSAL. An UNLINKED PR has no task row to
mark ``done`` and no board gate to satisfy — CLAUDE.md's own worktree-first
workflow ends in ``gh pr merge --merge`` on a ``feat/<slug>`` branch — so a check
that refused every merge would refuse routine work, which is the failure mode the
fire-rate survey exists to catch. Both directions are asserted here.

No test in this module may reach the network: a test that shells out to ``gh``
passes on CI and fails under local auth. The forge lookup is either patched or
switched off with ``ICDEV_GH_PR_MERGE_GUARD_OFFLINE=1``.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.airgap import hook_compat
from tools.hooks import shared_checks as sc

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"

#: Captured before the autouse fixture replaces it with a fail-stub, so one test
#: can put the genuine article back and prove the OFFLINE switch on its own.
_REAL_PR_HEAD_BRANCH = sc._pr_head_branch


def _load_hook():
    spec = importlib.util.spec_from_file_location("icdev_hook_gh_merge", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


@pytest.fixture(autouse=True)
def no_forge(monkeypatch):
    """Nothing in this module may call ``gh``. Individual tests opt back in by
    patching ``_pr_head_branch`` with a stub."""
    monkeypatch.setattr(
        sc, "_pr_head_branch",
        lambda *a, **k: pytest.fail("the check reached the forge in a unit test"),
    )


def _check(command, repo_root=None):
    return sc.check_gh_pr_merge_bypass("Bash", {"command": command},
                                       repo_root=repo_root)


def _git_repo(tmp_path: Path, branch: str) -> Path:
    """A throwaway checkout whose HEAD is *branch*."""
    root = tmp_path / branch.replace("/", "_")
    root.mkdir(parents=True)
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=str(root), capture_output=True, text=True, timeout=60,
    )
    run("init", "-q")
    run("config", "user.email", "t@example.test")
    run("config", "user.name", "t")
    (root / "f.txt").write_text("x", encoding="utf-8")
    run("add", "f.txt")
    run("commit", "-qm", "seed")
    run("checkout", "-q", "-b", branch)
    return root


# ── The refusal ───────────────────────────────────────────────────────────


class TestARawMergeOnALinkedPRIsRefused:

    def test_an_explicit_kanban_branch_is_refused(self):
        reason = _check("gh pr merge kanban/kpr-rvfy-05 --squash")
        assert reason and reason.startswith("BLOCKED:")

    def test_the_refusal_names_the_sanctioned_door(self):
        """The acceptance criterion: a refusal that does not say where to go
        instead is an obstacle, not a control."""
        reason = _check("gh pr merge kanban/kpr-rvfy-05 --merge")
        assert "--set-status kpr-rvfy-05 done --merge" in reason
        assert "tools/kanban/cli.py" in reason
        assert "tools/kanban/land.py" in reason

    def test_the_refusal_enumerates_the_thirteen_gates(self):
        reason = _check("gh pr merge kanban/kpr-rvfy-05 --merge")
        assert len(sc.GH_PR_MERGE_DOOR_CHECKS) == 13
        for gate in sc.GH_PR_MERGE_DOOR_CHECKS:
            assert gate in reason, f"the refusal never mentions {gate}"

    def test_the_refusal_names_its_own_kill_switch(self):
        reason = _check("gh pr merge kanban/kpr-rvfy-05 --merge")
        assert sc.GH_PR_MERGE_GUARD_ENV in reason

    def test_a_pr_number_is_refused_when_the_forge_says_kanban(self, monkeypatch):
        """The motivating incident was twelve NUMBERED merges. A number says
        nothing about the branch, so without the lookup the check would miss the
        exact case it was written for."""
        monkeypatch.setattr(sc, "_pr_head_branch",
                            lambda *a, **k: "kanban/ftp-ezb-03")
        reason = _check("gh pr merge 311 --repo icdev-ai/icdev_ft --merge")
        assert reason and "ftp-ezb-03" in reason

    def test_a_pr_url_is_refused_when_the_forge_says_kanban(self, monkeypatch):
        monkeypatch.setattr(sc, "_pr_head_branch",
                            lambda *a, **k: "kanban/autonomy-id-05")
        reason = _check(
            "gh pr merge https://github.com/icdev-ai/icdev/pull/1976 --merge")
        assert reason and "autonomy-id-05" in reason

    def test_no_selector_on_a_kanban_checkout_is_refused(self, tmp_path):
        """`gh pr merge` with no PR argument merges the CURRENT branch's PR."""
        root = _git_repo(tmp_path, "kanban/kpr-rvfy-05")
        assert _check(f'cd "{root}" && gh pr merge --squash --auto')

    def test_it_is_refused_mid_chain_not_only_at_the_start(self):
        reason = _check(
            "git status && gh pr merge kanban/kpr-rvfy-05 --merge && echo done")
        assert reason and "kpr-rvfy-05" in reason


# ── The other half: routine work is not refused ───────────────────────────


class TestAnUnlinkedPRIsStillAllowed:
    """A guard that blocks real work gets disabled, which is worse than none."""

    def test_a_feat_branch_merge_is_allowed(self):
        assert _check("gh pr merge feat/ftp-card --squash") is None

    def test_the_claude_md_worktree_workflow_still_works(self):
        """CLAUDE.md step 7 is literally `gh pr merge --merge` on a `feat/`
        branch. If this check refused that, the documented workflow would be
        unrunnable."""
        assert _check("gh pr merge feat/my-slug --merge") is None

    def test_a_pr_number_on_a_non_kanban_branch_is_allowed(self, monkeypatch):
        monkeypatch.setattr(sc, "_pr_head_branch", lambda *a, **k: "fix/env-log")
        assert _check("gh pr merge 313 --repo icdev-ai/icdev_ft --merge") is None

    def test_no_selector_on_a_non_kanban_checkout_is_allowed(self, tmp_path):
        root = _git_repo(tmp_path, "feat/some-slug")
        assert _check(f'cd "{root}" && gh pr merge --squash') is None

    def test_no_selector_with_an_explicit_repo_never_infers_from_head(
        self, tmp_path
    ):
        """--repo means gh resolves against ANOTHER repository, so this
        checkout's HEAD says nothing about the PR being merged."""
        root = _git_repo(tmp_path, "kanban/kpr-rvfy-05")
        assert _check(
            f'cd "{root}" && gh pr merge --repo other/repo --merge') is None

    def test_other_gh_subcommands_are_untouched(self):
        for command in (
            "gh pr list --state open",
            "gh pr view 1980 --json state",
            "gh pr checks 1977",
            "gh pr create --title x --body y",
            "gh run rerun 12345",
            "git merge origin/main",
        ):
            assert _check(command) is None, f"{command!r} was wrongly refused"


class TestProseIsNotAnInvocation:
    """The measured PreToolUse defect (2,526 refusals, one call in forty) was a
    check reading a heredoc BODY as commands. These are the same shape."""

    def test_an_echo_mentioning_the_command_is_allowed(self):
        assert _check("echo 'do not run gh pr merge kanban/x-01'") is None

    def test_a_pr_body_mentioning_the_command_is_allowed(self):
        assert _check(
            "gh pr create --title t --body 'then gh pr merge kanban/x-01'"
        ) is None

    def test_a_heredoc_data_body_is_allowed(self):
        assert _check(
            'cat > notes.md <<\'EOF\'\ngh pr merge kanban/x-01\nEOF\n') is None

    def test_a_grep_for_the_command_is_allowed(self):
        assert _check("grep -rn 'gh pr merge kanban/x-01' docs/") is None


# ── The parser, on its own ────────────────────────────────────────────────


class TestTheParser:

    def test_a_value_flag_is_not_read_as_the_pr_selector(self):
        """Without the value-flag table, `--body kanban/x-01` would be read as
        the PR being merged — a refusal invented out of a message."""
        found = sc.gh_pr_merge_invocations(
            "gh pr merge --body kanban/x-01 --merge 1980")
        assert found == [{"selector": "1980", "repo": None}]

    def test_repo_is_captured_in_both_spellings(self):
        assert sc.gh_pr_merge_invocations(
            "gh pr merge 5 --repo o/r")[0]["repo"] == "o/r"
        assert sc.gh_pr_merge_invocations(
            "gh pr merge 5 --repo=o/r")[0]["repo"] == "o/r"
        assert sc.gh_pr_merge_invocations(
            "gh pr merge 5 -R o/r")[0]["repo"] == "o/r"

    def test_no_selector_reads_as_none_not_as_a_flag(self):
        assert sc.gh_pr_merge_invocations(
            "gh pr merge --auto --squash") == [{"selector": None, "repo": None}]

    def test_every_invocation_in_a_chain_is_returned(self):
        found = sc.gh_pr_merge_invocations(
            "gh pr merge 1 --merge; gh pr merge 2 --merge")
        assert [f["selector"] for f in found] == ["1", "2"]

    @pytest.mark.parametrize("ref,expected", [
        ("kanban/kpr-rvfy-05", "kpr-rvfy-05"),
        ("origin/kanban/ftl-val-05", "ftl-val-05"),
        ("refs/heads/kanban/x-01", "x-01"),
        ("feat/kanban-ish", None),
        ("kanban", None),
        ("", None),
    ])
    def test_kanban_task_from_ref(self, ref, expected):
        assert sc.kanban_task_from_ref(ref) == expected


# ── Fails open, and can be stood down without a shell operator ────────────


class TestItFailsOpen:
    """A guard that cannot resolve a selector must not be the reason a session
    cannot work. Every unknown allows."""

    def test_an_unreadable_forge_allows(self, monkeypatch):
        monkeypatch.setattr(sc, "_pr_head_branch", lambda *a, **k: None)
        assert _check("gh pr merge 311 --merge") is None

    def test_an_unexpanded_variable_selector_allows(self, monkeypatch):
        monkeypatch.setattr(sc, "_pr_head_branch", lambda *a, **k: None)
        assert _check("for pr in 305 307; do gh pr merge $pr --merge; done") is None

    def test_a_directory_that_does_not_exist_allows(self, tmp_path):
        assert _check(f'cd "{tmp_path / "gone"}" && gh pr merge --merge',
                      repo_root=tmp_path / "gone") is None

    def test_a_non_bash_tool_is_never_examined(self):
        assert sc.check_gh_pr_merge_bypass(
            "Write", {"file_path": "x.md", "content": "gh pr merge kanban/x-01"}
        ) is None


class TestTheKillSwitch:

    def test_it_is_registered_in_check_kill_switches(self):
        """The acceptance criterion. An auditable env var, never a shell
        operator inside a JSON string."""
        assert hook.CHECK_KILL_SWITCHES["gh_pr_merge_bypass"] == (
            sc.GH_PR_MERGE_GUARD_ENV
        )

    def test_setting_it_to_zero_disables_the_check(self, monkeypatch):
        monkeypatch.setenv(sc.GH_PR_MERGE_GUARD_ENV, "0")
        assert _check("gh pr merge kanban/kpr-rvfy-05 --merge") is None
        assert hook.check_enabled("gh_pr_merge_bypass") is False

    def test_the_offline_switch_skips_the_forge_and_keeps_the_rest(
        self, monkeypatch
    ):
        """Offline still refuses what it can prove OFFLINE — a branch literal —
        so switching the lookup off is not switching the check off.

        The REAL ``_pr_head_branch`` is restored here (the autouse fixture
        replaces it with a fail-stub) precisely so the assertion proves the
        env var, not the stub: if the switch were ignored the stub's absence
        would let a real ``gh`` call through and the test would hang or fail.
        """
        monkeypatch.setenv(sc.GH_PR_MERGE_OFFLINE_ENV, "1")
        monkeypatch.setattr(sc, "_pr_head_branch", _REAL_PR_HEAD_BRANCH)
        assert _check("gh pr merge 311 --merge") is None
        assert _check("gh pr merge kanban/kpr-rvfy-05 --merge")


# ── Both guard paths, and the hook end to end ─────────────────────────────


class TestBothPathsRunIt:
    """exa-bench-06: a check wired into one path and missing from the other is
    the regression the parity tests exist for."""

    def test_it_is_declared_in_both_check_lists(self):
        assert "check_gh_pr_merge_bypass" in hook.HOOK_CHECKS
        assert "check_gh_pr_merge_bypass" in hook_compat.HEADLESS_CHECKS

    def test_the_headless_path_refuses_it(self, monkeypatch):
        monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)
        monkeypatch.setenv(sc.GH_PR_MERGE_OFFLINE_ENV, "1")
        result = hook_compat.run_pre_tool_check(
            "Bash", {"command": "gh pr merge kanban/kpr-rvfy-05 --merge"})
        assert result["allowed"] is False
        assert "--set-status kpr-rvfy-05 done --merge" in result["reason"]

    def test_the_headless_path_allows_an_unlinked_merge(self, monkeypatch):
        monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)
        monkeypatch.setenv(sc.GH_PR_MERGE_OFFLINE_ENV, "1")
        result = hook_compat.run_pre_tool_check(
            "Bash", {"command": "gh pr merge feat/ftp-card --merge"})
        assert result["allowed"] is True


def _run_hook(command: str, env_extra: dict | None = None):
    """Drive the hook the way Claude Code does: JSON on stdin, verdict in the
    exit code. 2 = blocked, 0 = allowed."""
    import os
    env = dict(os.environ)
    env[sc.GH_PR_MERGE_OFFLINE_ENV] = "1"   # never reach the network from a test
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120, env=env,
    )
    return proc.returncode, proc.stderr


class TestTheHookEndToEnd:

    def test_a_linked_merge_exits_two(self):
        code, stderr = _run_hook("gh pr merge kanban/kpr-rvfy-05 --merge")
        assert code == 2, f"a raw merge on a kanban PR was ALLOWED: {stderr}"
        assert "--set-status kpr-rvfy-05 done --merge" in stderr

    def test_an_unlinked_merge_exits_zero(self):
        code, stderr = _run_hook("gh pr merge feat/ftp-card --merge")
        assert code == 0, f"an unlinked merge was wrongly BLOCKED: {stderr}"

    def test_the_kill_switch_reaches_the_subprocess(self):
        code, _ = _run_hook("gh pr merge kanban/kpr-rvfy-05 --merge",
                            {sc.GH_PR_MERGE_GUARD_ENV: "0"})
        assert code == 0

    def test_advisory_mode_reports_without_refusing(self):
        code, stderr = _run_hook("gh pr merge kanban/kpr-rvfy-05 --merge",
                                 {"ICDEV_PRETOOLUSE_ENFORCE": "0"})
        assert code == 0
        assert "ADVISORY" in stderr and "kpr-rvfy-05" in stderr


class TestNoShellNeutraliser:
    """The `|| true` failure mode, one guard later. A check that is nominally
    enforcing behind a shell operator is unmeasured, not proven."""

    def test_the_hook_is_not_wrapped_in_a_shell_neutraliser(self):
        text = (REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        settings = json.loads(text)
        # The SAFETY hook only. `coordination.py` is session bookkeeping and
        # refuses nothing, so its `|| true` is not a neutralised guard —
        # asserting over every PreToolUse entry would fail on a correct tree.
        commands = [
            h.get("command", "")
            for group in settings.get("hooks", {}).get("PreToolUse", [])
            for h in group.get("hooks", [])
            if "pre_tool_use.py" in h.get("command", "")
        ]
        assert commands, "the PreToolUse safety hook is not configured at all"
        for command in commands:
            for neutraliser in ("|| true", "|| exit 0", "; true", "|| echo"):
                assert neutraliser not in command, (
                    f"the PreToolUse hook is wrapped in {neutraliser!r}: its "
                    "exit 2 never reaches the caller. Stand a check down with "
                    "its env kill switch, which is auditable."
                )
