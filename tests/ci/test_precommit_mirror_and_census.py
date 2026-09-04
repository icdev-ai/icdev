#!/usr/bin/env python3
# CUI // SP-CTI
"""Three cheap static checks that ran only on CI now run where the author can act (mfx-ci-01).

THE THREE INCIDENTS, one day, each fixed by hand:
  * rmf-rfp-01 changed tools/db/schema/{pg_consolidated.sql,tables.yaml} and not
    their icdev/ twins; tests/test_mirror_drift_baseline.py went red on `db`
    twenty minutes after the push and a human mirrored 199 files.
  * rmf-wp-02 imported `markdown` inside a bare `except Exception` in
    exporter.py; the undeclared-import census refused it, on CI, after the push.
  * the #2052 squash left tests/e2e/key_pages_smoke.spec.ts unparseable and ALL
    FOUR E2E shards on main failed at collection -- for hours, because E2E is not
    a required check and nothing parses .ts before a push.

WHAT THESE TESTS PIN
  * the pre-commit hook refuses a staged tools/<pkg>/ file whose mirror twin
    differs, names the file and prints the `--fix` command; the mirror-side
    spelling is the same pair; a MISSING twin is a note, not a block; an
    unmirrored package, an excluded extension and a re-export shim never block;
  * the hook refuses a staged .py that imports an undeclared package inside a
    swallowing handler, and passes a declared one and a first-party bare import;
  * a commit touching nothing under tools/ never reaches either check;
  * the REQUIRED `Lint` job parses every Playwright spec with `--list`, after
    `npm ci`, with no neutraliser.

Every hook case runs against a REAL throwaway git repository with a real staged
index, because both checks read the index through the tools CI runs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from tools.dx import mirror_parity
from tools.testing import pre_commit_check

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "icdev-ci.yml"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


CORE = "VALUE = 1\n"
SHIM = (
    "# Backward-compat shim -- canonical module is icdev/tools/widget/shimmed.py\n"
    "from icdev.tools.widget.shimmed import VALUE  # noqa: F401\n"
)
FULL = "VALUE = 1\n\n\ndef helper():\n    return VALUE\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A committed repo: one mirrored package in parity, one shim, one unmirrored package."""
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "gate@example.test")
    _git(root, "config", "user.name", "Gate Test")

    _write(root / "requirements.txt", "pyyaml\n")
    _write(root / "tools" / "widget" / "core.py", CORE)
    _write(root / "icdev" / "tools" / "widget" / "core.py", CORE)
    _write(root / "tools" / "widget" / "notes.md", "# notes\n")
    _write(root / "icdev" / "tools" / "widget" / "notes.md", "# notes\n")
    _write(root / "tools" / "widget" / "shimmed.py", SHIM)
    _write(root / "icdev" / "tools" / "widget" / "shimmed.py", FULL)
    _write(root / "tools" / "widget" / "cui_marker.py", "def mark():\n    return 'CUI'\n")
    _write(root / "icdev" / "tools" / "widget" / "cui_marker.py", "def mark():\n    return 'CUI'\n")
    _write(root / "tools" / "solo" / "only.py", "X = 1\n")
    _write(root / "docs" / "notes.md", "notes\n")

    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    return root


def _status(root: Path) -> list[tuple[str, str]]:
    return pre_commit_check._get_staged_name_status(root)


# --------------------------------------------------------------------------- #
# Scope: only a staged file under tools/<pkg>/ or icdev/tools/<pkg>/ is in play
# --------------------------------------------------------------------------- #
def test_scope_is_empty_for_a_commit_touching_nothing_under_a_tools_package() -> None:
    staged = [("M", "docs/notes.md"), ("A", "tests/test_x.py"), ("M", "tools/top_level.py"),
              ("D", "tools/widget/gone.py")]
    assert pre_commit_check._mirror_scope(staged) == []


def test_scope_takes_both_spellings_and_only_add_modify_rename() -> None:
    staged = [("M", "tools/widget/core.py"), ("A", "icdev/tools/widget/new.py"),
              ("R100", "tools/widget/renamed.py"), ("D", "tools/widget/old.py")]
    assert pre_commit_check._mirror_scope(staged) == [
        "tools/widget/core.py", "icdev/tools/widget/new.py", "tools/widget/renamed.py",
    ]


def test_commit_touching_nothing_under_tools_never_reaches_either_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast path must cost nothing, or the hook gets ripped out."""

    def _boom(*_a, **_k):
        raise AssertionError("a check ran for a commit that stages nothing in scope")

    monkeypatch.setattr(pre_commit_check, "_get_staged_name_status",
                        lambda *_a, **_k: [("M", "docs/notes.md")])
    monkeypatch.setattr(pre_commit_check, "_run_domain_leak_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(pre_commit_check, "_run_mirror_parity", _boom)
    monkeypatch.setattr(pre_commit_check, "_run_undeclared_import_census", _boom)
    assert pre_commit_check.main() == 0


# --------------------------------------------------------------------------- #
# Mirror parity on the staged files
# --------------------------------------------------------------------------- #
def test_baseline_repo_is_in_parity(repo: Path) -> None:
    report = mirror_parity.audit_files(["tools/widget/core.py"], root=repo)
    assert report["in_parity"] == ["widget/core.py"] and report["clean"]


def test_staged_drift_is_refused_and_names_the_fix(repo: Path, capsys) -> None:
    _write(repo / "tools" / "widget" / "core.py", "VALUE = 2\n")
    _git(repo, "add", "tools/widget/core.py")
    files = pre_commit_check._mirror_scope(_status(repo))
    assert files == ["tools/widget/core.py"]

    assert pre_commit_check._run_mirror_parity(files, root=repo) is False
    out = capsys.readouterr().out
    assert "BLOCKED" in out
    assert "tools/widget/core.py  !=  icdev/tools/widget/core.py" in out
    assert "--files tools/widget/core.py --fix" in out
    assert "--paths widget --fix" in out


def test_mirror_side_only_edit_is_the_same_drift(repo: Path) -> None:
    """Both spellings name ONE pair; editing the packaged copy alone is drift too."""
    _write(repo / "icdev" / "tools" / "widget" / "core.py", "VALUE = 3\n")
    _git(repo, "add", "icdev/tools/widget/core.py")
    files = pre_commit_check._mirror_scope(_status(repo))
    assert files == ["icdev/tools/widget/core.py"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_editing_both_sides_identically_passes(repo: Path) -> None:
    _write(repo / "tools" / "widget" / "core.py", "VALUE = 2\n")
    _write(repo / "icdev" / "tools" / "widget" / "core.py", "VALUE = 2\n")
    _git(repo, "add", "-A")
    files = pre_commit_check._mirror_scope(_status(repo))
    assert sorted(files) == ["icdev/tools/widget/core.py", "tools/widget/core.py"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_new_file_without_a_twin_is_a_note_not_a_block(repo: Path, capsys) -> None:
    """missing_from_mirror is ungated in the gate YAML and in CI; the hook agrees."""
    _write(repo / "tools" / "widget" / "brand_new.py", "NEW = 1\n")
    _git(repo, "add", "tools/widget/brand_new.py")
    files = pre_commit_check._mirror_scope(_status(repo))
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True
    out = capsys.readouterr().out
    assert "no icdev/tools/ twin" in out and "widget/brand_new.py" in out
    assert "BLOCKED" not in out


def test_unmirrored_package_never_blocks(repo: Path) -> None:
    _write(repo / "tools" / "solo" / "only.py", "X = 2\n")
    _git(repo, "add", "tools/solo/only.py")
    report = mirror_parity.audit_files(["tools/solo/only.py"], root=repo)
    assert report["not_mirrored"] == ["solo/only.py"] and report["content_drift"] == []
    files = pre_commit_check._mirror_scope(_status(repo))
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_excluded_extension_is_read_from_the_gate_not_hardcoded(repo: Path) -> None:
    gate = yaml.safe_load((REPO_ROOT / "args" / "mirror_parity_gate.yaml").read_text(encoding="utf-8"))
    excluded = {e["ext"] for e in gate["excluded_extensions"]}
    assert ".md" in excluded, "precondition: the gate declares .md excluded"
    assert pre_commit_check._mirror_excluded_extensions() == excluded

    _write(repo / "tools" / "widget" / "notes.md", "# notes, revised\n")
    _git(repo, "add", "tools/widget/notes.md")
    files = pre_commit_check._mirror_scope(_status(repo))
    # The auditor reports the drift; the hook applies the declared policy.
    assert mirror_parity.audit_files(files, root=repo)["content_drift"] == ["widget/notes.md"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_a_reexport_shim_is_never_drift(repo: Path) -> None:
    """The two names resolve to ONE module object -- there is no stale half."""
    assert mirror_parity.is_mirror_shim(repo / "tools" / "widget" / "shimmed.py")
    assert not mirror_parity.is_mirror_shim(repo / "icdev" / "tools" / "widget" / "shimmed.py")
    report = mirror_parity.audit_files(["tools/widget/shimmed.py"], root=repo)
    assert report["shim"] == ["widget/shimmed.py"] and report["content_drift"] == []

    _write(repo / "tools" / "widget" / "shimmed.py", SHIM + "# touched\n")
    _git(repo, "add", "tools/widget/shimmed.py")
    files = pre_commit_check._mirror_scope(_status(repo))
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_the_five_real_shims_and_one_rule() -> None:
    """The coherence gate and the hook share the predicate -- no second copy."""
    from tools.workflow import coherence_checker

    for rel in ("llm/agent_loop.py", "showcase/synthetic_data_engine.py",
                "testing/qa_agent_runner.py", "testing/selector_healer.py", "billing/tier.py"):
        path = REPO_ROOT / "tools" / rel
        assert mirror_parity.is_mirror_shim(path), rel
        assert coherence_checker._is_mirror_shim(path), rel
    assert not mirror_parity.is_mirror_shim(REPO_ROOT / "tools" / "db" / "storage.py")


def test_files_cli_round_trips_and_gates(repo: Path) -> None:
    _write(repo / "tools" / "widget" / "core.py", "VALUE = 9\n")
    proc = subprocess.run(
        ["python", str(REPO_ROOT / "tools" / "dx" / "mirror_parity.py"),
         "--files", "tools/widget/core.py,tools/solo/only.py", "--gate", "--root", str(repo)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 1, proc.stdout
    assert "content-drift:       widget/core.py" in proc.stdout
    proc = subprocess.run(
        ["python", str(REPO_ROOT / "tools" / "dx" / "mirror_parity.py"),
         "--files", "tools/widget/core.py", "--fix", "--gate", "--root", str(repo)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stdout
    assert (repo / "icdev" / "tools" / "widget" / "core.py").read_text(encoding="utf-8") == "VALUE = 9\n"


# --------------------------------------------------------------------------- #
# Undeclared-import census on the staged .py files
# --------------------------------------------------------------------------- #
SWALLOWED = "try:\n    import frobnicate\nexcept Exception:\n    pass\n"


def test_undeclared_import_in_a_swallowing_handler_is_refused(repo: Path, capsys) -> None:
    _write(repo / "tools" / "widget" / "optional.py", SWALLOWED)
    _git(repo, "add", "tools/widget/optional.py")
    assert pre_commit_check._run_undeclared_import_census(root=repo) is False
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "frobnicate" in out and "optional.py" in out


def test_a_declared_package_passes(repo: Path) -> None:
    _write(repo / "requirements.txt", "pyyaml\nfrobnicate\n")
    _write(repo / "tools" / "widget" / "optional.py", SWALLOWED)
    _git(repo, "add", "-A")
    assert pre_commit_check._run_undeclared_import_census(root=repo) is True


def test_a_first_party_bare_import_is_not_a_finding(repo: Path) -> None:
    """`from cui_marker import ...` after a sys.path insert is the repo's own module."""
    _write(repo / "tools" / "widget" / "user.py",
           "try:\n    from cui_marker import mark\nexcept ImportError:\n    mark = None\n")
    _git(repo, "add", "tools/widget/user.py")
    assert pre_commit_check._run_undeclared_import_census(root=repo) is True


def test_a_handler_that_says_it_fired_passes(repo: Path) -> None:
    _write(repo / "tools" / "widget" / "optional.py",
           "import logging\ntry:\n    import frobnicate\nexcept ImportError as exc:\n"
           "    logging.getLogger(__name__).warning('frobnicate missing: %s', exc)\n    frobnicate = None\n")
    _git(repo, "add", "tools/widget/optional.py")
    assert pre_commit_check._run_undeclared_import_census(root=repo) is True


# --------------------------------------------------------------------------- #
# The REQUIRED Lint job parses every Playwright spec
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def lint_steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["lint"]["steps"]


def test_lint_lists_every_playwright_spec_with_no_neutraliser(lint_steps: list[dict]) -> None:
    runs = [str(s.get("run") or "") for s in lint_steps]
    idx = [i for i, r in enumerate(runs) if "playwright test --list" in r]
    assert idx, "the required Lint job must run `npx playwright test --list`"
    step = lint_steps[idx[0]]
    assert "|| true" not in runs[idx[0]]
    assert not step.get("continue-on-error"), "a parse gate behind continue-on-error gates nothing"
    assert step.get("env", {}).get("ICDEV_NO_SERVER") == "1", "--list must never try to start the dashboard"


def test_lint_installs_node_deps_before_listing(lint_steps: list[dict]) -> None:
    order = []
    for s in lint_steps:
        if str(s.get("uses") or "").startswith("actions/setup-node"):
            order.append("setup-node")
        run = str(s.get("run") or "")
        if run.strip().startswith("npm ci"):
            order.append("npm ci")
        if "playwright test --list" in run:
            order.append("list")
    assert order == ["setup-node", "npm ci", "list"], order


def test_lint_never_installs_a_browser(lint_steps: list[dict]) -> None:
    """`--list` needs no browser; installing one here would add minutes to a required job."""
    assert not any("playwright install" in str(s.get("run") or "") for s in lint_steps)
