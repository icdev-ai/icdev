"""The pytest gate must not strip the inherited PYTHONPATH.

MEASURED 2026-08-30 on icdev_ft branch kanban/ftp-prd-08:

    PYTHONPATH=<IT checkout>  python -m pytest tests/  ->  2678 passed
    PYTHONPATH unset          python -m pytest tests/  ->  22 failed, 588 errors

Every one of those errors is `ModuleNotFoundError: No module named 'tools'`.
`_run_pytest` did `env["PYTHONPATH"] = cwd`, which is correct for an ICDEV[IT]
task -- the worktree IS the repo holding `tools/` -- and silently fatal for an
ICDEV[FT] one, whose `tools` package comes from the IT checkout the operator
carries on PYTHONPATH. The verifier therefore recorded UNIT TESTS FAILED for a
branch whose suite is entirely green, that false verdict failed the enforced
done-gate, and `--set-status <id> done --merge` refused every ICDEV[FT] task.

That is the part worth remembering: a gate that always refuses does not get
fixed, it gets ROUTED AROUND -- and agents fell back to a raw `gh pr merge`,
which runs none of land.py's thirteen checks.
"""
from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

from tools.workflow import validated_commit as vc

MODIFIED = ["tests/test_thing.py"]


def _captured_env(monkeypatch, inherited: str | None) -> dict:
    """Run _run_pytest with subprocess.run stubbed; return the env it built."""
    if inherited is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", inherited)

    seen: dict = {}

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="1 passed", stderr="")

    with patch.object(vc.subprocess, "run", fake_run):
        vc._run_pytest("/the/worktree", MODIFIED, 60.0)
    return seen


def test_the_inherited_pythonpath_survives(monkeypatch):
    """The ICDEV[FT] case: `tools` lives in the IT checkout, not the worktree."""
    it_checkout = os.path.join("C:", os.sep, "AI", "ICDev")
    env = _captured_env(monkeypatch, it_checkout)
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert "/the/worktree" in parts, "the worktree must still be importable"
    assert it_checkout in parts, (
        "the inherited PYTHONPATH was DISCARDED -- this is the defect: every "
        "`import tools.X` in an ICDEV[FT] suite then fails and the verifier "
        "records UNIT TESTS FAILED for a green branch"
    )


def test_the_worktree_comes_first(monkeypatch):
    """The task's own tree must win over anything inherited, so a test imports
    the code under test rather than a same-named module from another checkout."""
    env = _captured_env(monkeypatch, os.path.join("C:", os.sep, "AI", "ICDev"))
    assert env["PYTHONPATH"].split(os.pathsep)[0] == "/the/worktree"


def test_no_inherited_path_is_still_just_the_worktree(monkeypatch):
    """The ICDEV[IT] case, unchanged: no stray separator, no empty entry."""
    env = _captured_env(monkeypatch, None)
    assert env["PYTHONPATH"] == "/the/worktree"


def test_an_empty_inherited_path_does_not_produce_an_empty_entry(monkeypatch):
    """An empty PYTHONPATH entry means "the current directory" to Python, which
    would make the gate import from wherever it happened to be run."""
    env = _captured_env(monkeypatch, "")
    assert env["PYTHONPATH"] == "/the/worktree"
    assert "" not in env["PYTHONPATH"].split(os.pathsep)


def test_the_sqlite_backend_is_still_defaulted(monkeypatch):
    """Unchanged behaviour: the gate must never reach a real database."""
    env = _captured_env(monkeypatch, None)
    assert env["ICDEV_STORAGE_BACKEND"] == "sqlite"


def test_the_icdev_mirror_carries_the_fix_too():
    """The packaged copy must not still overwrite the inherited path.

    In a source checkout `tools.X` and `icdev.tools.X` are ONE module object
    (xit-decl-02's meta-path shim), so importing the second spelling and
    asserting on it would re-test the file the tests above already cover and
    prove nothing about the mirror. The shim deliberately never installs in the
    wheel, where `icdev/tools/workflow/validated_commit.py` IS the file that
    runs -- so the mirror is read as SOURCE here, which is the only derivation
    that can actually see it.

    The two copies were byte-identical before the fix and the fix touched only
    `tools/`, which is exactly the half-live drift args/mirror_parity_gate.yaml
    was written for: it does not ImportError, it just behaves like the old code.
    """
    from icdev.core.paths import repo_root

    mirror = (
        repo_root(__file__) / "icdev" / "tools" / "workflow" / "validated_commit.py"
    )
    assert mirror.exists(), f"the mirrored twin is missing: {mirror}"
    source = mirror.read_text(encoding="utf-8")
    assert 'env["PYTHONPATH"] = cwd' not in source, (
        "the icdev/ mirror still OVERWRITES the inherited PYTHONPATH -- the fix "
        "is only half live, and the packaged copy is the stale half"
    )
    assert 'env.get("PYTHONPATH")' in source, (
        "the icdev/ mirror does not read the inherited PYTHONPATH at all"
    )
