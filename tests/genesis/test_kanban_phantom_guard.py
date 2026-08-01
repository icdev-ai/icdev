# CUI // SP-CTI
"""OPT-76: phantom-completion guard in tools/genesis/reflexes/kanban.py.

Tests the new _extract_claimed_file_paths / _verify_claimed_files_exist /
_verify_task_completed logic — specifically the phantom detection that
rejects agent output claiming to write files that don't exist on disk.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban


# ────────────────────────────────────────────────────────────────────────────
# _extract_claimed_file_paths
# ────────────────────────────────────────────────────────────────────────────


def test_extract_simple_path():
    text = "I created tools/llm/eval_runner.py with 400 lines."
    paths = kanban._extract_claimed_file_paths(text)
    assert "tools/llm/eval_runner.py" in paths


def test_extract_backtick_wrapped_path():
    text = "Modified `args/llm_eval_config.yaml` to add defaults."
    paths = kanban._extract_claimed_file_paths(text)
    assert "args/llm_eval_config.yaml" in paths


def test_extract_multiple_paths():
    text = """
    New files:
    - tools/llm/eval_runner.py (400 LOC)
    - tests/llm/test_eval_runner.py (200 LOC)
    - args/llm_evals/code_generation_baseline.yaml

    Updated:
    - tools/manifest.md — added entry
    """
    paths = kanban._extract_claimed_file_paths(text)
    assert "tools/llm/eval_runner.py" in paths
    assert "tests/llm/test_eval_runner.py" in paths
    assert "args/llm_evals/code_generation_baseline.yaml" in paths
    assert "tools/manifest.md" in paths


def test_extract_deduplicates():
    text = "Created tools/foo.py. Then I modified tools/foo.py again."
    paths = kanban._extract_claimed_file_paths(text)
    assert paths.count("tools/foo.py") == 1


def test_extract_skips_globs():
    text = "Scanned tools/*/blueprint.py for routes."
    paths = kanban._extract_claimed_file_paths(text)
    assert not any("*" in p for p in paths)


def test_extract_skips_non_whitelisted_dirs():
    text = "I updated README.md and src/main.c and my/random/file.py"
    paths = kanban._extract_claimed_file_paths(text)
    # README.md is top-level — not in our repo-path whitelist
    # src/main.c, my/random/... don't start with tools|tests|args|...
    assert "README.md" not in paths
    assert not any("src/" in p for p in paths)


def test_extract_empty_text():
    assert kanban._extract_claimed_file_paths("") == []
    assert kanban._extract_claimed_file_paths(None) == []


def test_extract_respects_max():
    # Generate 100 distinct paths in output
    text = "\n".join(f"created tools/foo/file_{i}.py" for i in range(100))
    paths = kanban._extract_claimed_file_paths(text, max_paths=10)
    assert len(paths) == 10


# ────────────────────────────────────────────────────────────────────────────
# _verify_claimed_files_exist
# ────────────────────────────────────────────────────────────────────────────


def test_verify_all_exist(tmp_path):
    # Create two real files under tmp_path
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "a.py").write_text("pass")
    (tmp_path / "tools" / "b.py").write_text("pass")

    existing, claimed, missing = kanban._verify_claimed_files_exist(
        ["tools/a.py", "tools/b.py"], tmp_path
    )
    assert existing == 2
    assert claimed == 2
    assert missing == []


def test_verify_none_exist(tmp_path, monkeypatch):
    # Keep the test network-free and deterministic: neither file is merged.
    monkeypatch.setattr(kanban, "_fetch_origin_main_quiet", lambda: None)
    monkeypatch.setattr(kanban, "_path_in_origin_main", lambda rel: False)
    existing, claimed, missing = kanban._verify_claimed_files_exist(
        ["tools/nope1.py", "tools/nope2.py"], tmp_path
    )
    assert existing == 0
    assert claimed == 2
    assert len(missing) == 2


def test_verify_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(kanban, "_fetch_origin_main_quiet", lambda: None)
    monkeypatch.setattr(kanban, "_path_in_origin_main", lambda rel: False)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "exists.py").write_text("pass")

    existing, claimed, missing = kanban._verify_claimed_files_exist(
        ["tools/exists.py", "tools/missing.py"], tmp_path
    )
    assert existing == 1
    assert claimed == 2
    assert missing == ["tools/missing.py"]


# ────────────────────────────────────────────────────────────────────────────
# origin/main fallback — merged work whose worktree is gone and whose shared
# checkout is stale must still count as existing.
# ────────────────────────────────────────────────────────────────────────────


def test_verify_missing_on_disk_but_in_origin_main_counts_as_existing(
    tmp_path, monkeypatch
):
    """A path absent from a stale on-disk checkout but present in origin/main
    is counted as existing (merged work, worktree removed)."""
    fetched = {"n": 0}
    monkeypatch.setattr(
        kanban, "_fetch_origin_main_quiet",
        lambda: fetched.__setitem__("n", fetched["n"] + 1),
    )
    # Only the merged path is in origin/main; the truly-missing one is not.
    monkeypatch.setattr(
        kanban, "_path_in_origin_main",
        lambda rel: rel == "tools/merged_but_worktree_gone.py",
    )

    existing, claimed, missing = kanban._verify_claimed_files_exist(
        ["tools/merged_but_worktree_gone.py", "tools/truly_absent.py"], tmp_path
    )
    assert claimed == 2
    assert existing == 1, "merged path should be counted via origin/main"
    assert missing == ["tools/truly_absent.py"]
    assert fetched["n"] == 1, "exactly one best-effort fetch when something missing"


def test_verify_hot_path_skips_fetch_when_all_on_disk(tmp_path, monkeypatch):
    """When every claimed path exists on disk, no git fetch is attempted —
    the hot path must stay network-free."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "a.py").write_text("pass")
    (tmp_path / "tools" / "b.py").write_text("pass")

    fetched = {"n": 0}
    monkeypatch.setattr(
        kanban, "_fetch_origin_main_quiet",
        lambda: fetched.__setitem__("n", fetched["n"] + 1),
    )

    existing, claimed, missing = kanban._verify_claimed_files_exist(
        ["tools/a.py", "tools/b.py"], tmp_path
    )
    assert (existing, claimed, missing) == (2, 2, [])
    assert fetched["n"] == 0, "no fetch when everything is already on disk"


def test_path_in_origin_main_normalizes_backslashes(monkeypatch):
    """Windows backslash paths become forward-slash git pathspecs."""
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        r = type("R", (), {})()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert kanban._path_in_origin_main("tools\\kanban\\gates.py") is True
    assert captured["args"][:3] == ["git", "cat-file", "-e"]
    assert captured["args"][3] == "origin/main:tools/kanban/gates.py"


def test_path_in_origin_main_returncode_maps_to_bool(monkeypatch):
    import subprocess

    def make_run(rc):
        def _run(args, **kwargs):
            r = type("R", (), {})()
            r.returncode = rc
            r.stdout = ""
            r.stderr = ""
            return r
        return _run

    monkeypatch.setattr(subprocess, "run", make_run(0))
    assert kanban._path_in_origin_main("tools/x.py") is True

    monkeypatch.setattr(subprocess, "run", make_run(1))
    assert kanban._path_in_origin_main("tools/x.py") is False


def test_path_in_origin_main_swallows_subprocess_error(monkeypatch):
    import subprocess

    def boom(args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", boom)
    assert kanban._path_in_origin_main("tools/x.py") is False


def test_verify_empty_list():
    existing, claimed, missing = kanban._verify_claimed_files_exist([], ".")
    assert existing == 0
    assert claimed == 0
    assert missing == []


# ────────────────────────────────────────────────────────────────────────────
# _verify_task_completed — phantom detection (the main OPT-76 guarantee)
# ────────────────────────────────────────────────────────────────────────────


def test_verify_task_phantom_completion_rejected():
    """Agent output claims 5 files but none exist → PHANTOM, reject."""
    phantom_output = """
    The task is done. Here's what was built:

    **New files:**
    - tools/phantom_demo/never_real_abc.py (400 LOC)
    - tests/phantom_demo/test_never_real_abc.py (200 LOC)
    - args/phantom_demo_never_real.yaml
    - docs/features/phantom-demo-never-real.md

    All tests pass. Ruff clean.
    """ * 3  # repeat to ensure len > 200

    verified, reason = kanban._verify_task_completed("task-phantom", phantom_output)
    assert verified is False
    assert "PHANTOM COMPLETION" in reason


def test_verify_task_real_completion_passes(tmp_path, monkeypatch):
    """Agent output claims files that actually exist → accept."""
    # Create a real file under tmp_path so the path check passes
    tools_dir = tmp_path / "tools" / "foo"
    tools_dir.mkdir(parents=True)
    (tools_dir / "real_file.py").write_text("# pass")

    # Point _worktrees so work_dir_for_check resolves to tmp_path
    monkeypatch.setattr(kanban, "_worktrees", {"task-real": str(tmp_path)})
    # Also override BASE_DIR for the fallback check
    monkeypatch.setattr(kanban, "BASE_DIR", tmp_path)

    real_output = """
    Completed the task. Created tools/foo/real_file.py with the expected
    content. The file is on disk and the module imports correctly. Also
    verified with grep and tests.
    """ * 3

    # Patch subprocess.run for the git check to return 'no commits' so we
    # exercise the dirty-working-tree + claimed-path fallback instead.
    import subprocess
    def fake_run(args, **kwargs):
        result = type("R", (), {})()
        result.stdout = ""  # no git output
        result.stderr = ""
        result.returncode = 0
        return result
    monkeypatch.setattr(subprocess, "run", fake_run)

    verified, reason = kanban._verify_task_completed("task-real", real_output)
    # Either the git check path succeeds (dirty/empty) or the claimed-path
    # path succeeds. Both are acceptable — what matters is NOT phantom.
    # The file exists → phantom check passes → we proceed to git check.
    # Git returns empty → we fail at "No git commits..." UNLESS dirty.
    # For this test the key assertion is that it's NOT rejected as phantom.
    if not verified:
        assert "PHANTOM COMPLETION" not in reason, (
            f"should not be phantom; rejected for other reason: {reason}"
        )


def test_verify_task_short_output_rejected():
    """Output too short → reject (existing behavior preserved)."""
    verified, reason = kanban._verify_task_completed("t1", "done")
    assert verified is False
    assert "too short" in reason.lower()


def test_verify_task_failure_marker_rejected():
    """Agent says 'I cannot do that' → reject."""
    output = "I cannot access the filesystem. " * 30
    verified, reason = kanban._verify_task_completed("t1", output)
    assert verified is False
    assert "I cannot".lower() in reason.lower() or "failure indicator" in reason.lower()


def test_verify_task_no_file_evidence_rejected():
    """Output has no file-change keywords → reject."""
    output = (
        "Hello there. This is a very long response that talks about nothing "
        "in particular and certainly contains no mention of any programming "
        "construct or version control system or any relevant ICDEV thing. "
    ) * 5
    verified, reason = kanban._verify_task_completed("t1", output)
    assert verified is False
    # Either the file-evidence check (for known file-creation task types) or the
    # git-commit check (for unknown task IDs with no DB entry) can trigger here.
    assert (
        "No evidence" in reason
        or "no evidence" in reason
        or "No git commits" in reason
    )


def test_verify_task_phantom_guard_runs_before_git():
    """Phantom detection must run BEFORE git check, so hallucinated
    file paths are caught even if git commands would have returned
    success."""
    phantom_output = (
        "Created tools/llm/fake_file_xyz123.py and tests/llm/test_fake.py. "
        "All checks pass, tests green. "
    ) * 5

    calls = []
    def fake_run(args, **kwargs):
        calls.append(list(args))
        result = type("R", (), {})()
        # Phantom paths are NOT in origin/main → cat-file existence probe fails.
        if len(args) >= 2 and args[1] == "cat-file":
            result.stdout = ""
            result.returncode = 1
        else:
            # A git-commit check, if reached, WOULD wrongly report commits —
            # it must never run because the phantom guard short-circuits first.
            result.stdout = "abc1234 phantom commit\n"
            result.returncode = 0
        result.stderr = ""
        return result

    with patch("subprocess.run", side_effect=fake_run):
        verified, reason = kanban._verify_task_completed("t-phantom2", phantom_output)

    assert verified is False
    assert "PHANTOM COMPLETION" in reason
    # The git COMMIT check (git log) must never run — the phantom guard rejects
    # first. (The guard itself may run fetch + cat-file to consult origin/main.)
    assert not any(len(a) >= 2 and a[1] == "log" for a in calls), (
        "phantom guard should short-circuit before the git commit check"
    )
