# CUI // SP-CTI
"""Generated runtime output must not be tracked.

The canvas `*_generator` / `*_scanner` tools write into `data/studio_artifacts/`
on every run — IaC bundles, `iac_report_<hash>.md`, `deploy_<hash>/`. Those files
are untracked and sit inside the working tree, so they are indistinguishable from
intentional content to anything that stages broadly:

  * `git add -A` sweeps them into whatever commit is in progress. This happened
    twice in one session — 33 artifact files into a hardening PR, and a
    2,292-line CRLF rewrite of args/projects.yaml into another.
  * The auto-commit hook (`ICDEV_AUTO_COMMIT`) does the same unattended, which is
    how 73 of them reached main in the first place.

The .gitignore rule is the fix; this is the gate that keeps it true. A single
`git add -f` or a new artifact directory would otherwise put us straight back.
"""
import subprocess

import pytest

#: Directories whose contents are produced by a tool run, not authored.
GENERATED_DIRS = (
    "data/studio_artifacts",
    "data/alerts",
)


def _tracked(path: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", path],
        capture_output=True, text=True, check=False,
    )
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _git_available() -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, check=False,
    ).returncode == 0


@pytest.mark.parametrize("path", GENERATED_DIRS)
def test_generated_output_is_not_tracked(path):
    if not _git_available():  # pragma: no cover
        pytest.skip("not a git checkout")
    tracked = _tracked(path)
    assert not tracked, (
        f"{len(tracked)} generated file(s) under {path} are tracked — they are "
        "tool output, and committing them means every run dirties the working "
        "tree and pollutes unrelated commits:\n  "
        + "\n  ".join(tracked[:10])
        + ("\n  ..." if len(tracked) > 10 else "")
        + f"\n\nFix: git rm -r --cached {path}  (the files stay on disk)."
    )


@pytest.mark.parametrize("path", GENERATED_DIRS)
def test_generated_output_is_actually_ignored(path):
    """The rule must exist, not just happen to match nothing today."""
    if not _git_available():  # pragma: no cover
        pytest.skip("not a git checkout")
    probe = f"{path}/__gate_probe__/x.md"
    result = subprocess.run(
        ["git", "check-ignore", "-v", probe],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"{path} is not covered by .gitignore, so a tool run leaves untracked "
        "files that `git add -A` will sweep into the next commit"
    )
