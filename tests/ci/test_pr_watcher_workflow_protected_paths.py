# CUI // SP-CTI
"""The Actions auto-merge workflow honours `protected_paths` (mfx-mrg-07).

THE DEFECT. `.github/workflows/pr-watcher.yml` squash-merges any open non-draft
`kanban/*` PR whose required checks are green, every 15 minutes and on every
check_suite completion, as github-actions[bot]. It ran none of land.py's
thirteen checks -- not `_refuse_protected` above all. MEASURED 2026-09-07 over
the last 84 merged PRs (re-derived here, docs/audits/mfx-mrg-07-*.md): ten
touched a protected path, eight merged unattended through this workflow, seven
of those eight edited tools/ci/pr_watcher.py itself. mfx-mrg-04's own PR
(#2143), which built the audited `--protected-ok` door on the premise that a
protected PR needs a human, was merged by this workflow at 05:02; the door had
never been used.

WHAT IS PINNED, and why each half is its own test:

* ONE LIST. The workflow reads `protected_paths` from args/pr_watcher_config.yaml
  and carries NO path literal from that list -- a second copy is the two
  implementations defect mfx-mrg-04 closed in land.py a day earlier.
* ONE RULE. `merge_readiness.protected_hits` IS `protected_paths.protected_hits`
  (identity, not equality), and the workflow runs that module.
* THE BASE BRANCH, NEVER THE HEAD. The config fetch is keyed on `baseRefName`;
  a PR that deletes its own path from the list must not authorise itself.
* THE RULE FROM THE DEFAULT BRANCH. The checkout step is pinned to
  `default_branch` and checks out exactly the rule module.
* SELF-PROTECTION. The workflow and the rule module are on the list.
* BEHAVIOUR. The workflow's own `run:` script is executed under bash against a
  fake `gh`: a protected PR is skipped with its hits named, a clean sibling
  merges, an unreadable config merges NOTHING, a truncated file listing
  merges nothing, and the config was fetched with `ref=<base>`.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import sys
import textwrap

import pytest
import yaml

from tools.ci import merge_readiness as mr
from tools.ci import protected_paths as pp

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "pr-watcher.yml"
CONFIG = ROOT / "args" / "pr_watcher_config.yaml"
RULE = "tools/ci/protected_paths.py"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list:
    return _doc()["jobs"]["auto-merge"]["steps"]


def _run_script() -> str:
    scripts = [s["run"] for s in _steps() if "run" in s]
    assert len(scripts) == 1, "expected exactly one run: step"
    return scripts[0]


def _code_lines(text: str) -> list:
    return [ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _live_list() -> list:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return list(cfg.get("protected_paths") or [])


# ── one list, one rule ─────────────────────────────────────────────────────

def test_the_workflow_reads_the_list_from_the_config_and_never_respells_it():
    script = _run_script()
    code = "\n".join(_code_lines(script))
    env = _steps()[-1].get("env", {})
    referenced = "args/pr_watcher_config.yaml" in code or \
        env.get("PROTECTED_PATHS_CONFIG") == "args/pr_watcher_config.yaml"
    assert referenced, (
        "the workflow must read protected_paths from args/pr_watcher_config.yaml "
        "-- the ONE list -- not carry its own")
    for entry in _live_list():
        if entry == "args/pr_watcher_config.yaml":
            continue                      # the reference to the list itself
        if entry == RULE:
            continue                      # the rule the workflow runs
        assert entry not in code, (
            f"{entry!r} is respelled in the workflow; the list lives in "
            "args/pr_watcher_config.yaml and nowhere else")
    assert RULE in code, "the workflow must run the shared rule module"
    assert "protected_paths" in code


def test_the_ladder_and_the_workflow_share_one_implementation():
    assert mr.protected_hits is pp.protected_hits, (
        "merge_readiness.protected_hits must be a re-export of "
        "protected_paths.protected_hits, never a copy")
    import tools.ci.pr_watcher as pw
    assert pw.protected_hits is pp.protected_hits


def test_the_rule_module_imports_nothing_from_the_tree():
    """It runs from a one-file sparse checkout on a bare runner."""
    import ast
    tree = ast.parse((ROOT / RULE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith(("tools", "icdev")), mod
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(("tools", "icdev")), alias.name


# ── the base branch, never the head; the rule from the default branch ──────

def test_the_config_is_fetched_from_the_base_branch_never_the_head():
    code = _code_lines(_run_script())
    fetch = [ln for ln in code if "contents/" in ln and "pr_watcher_config" in ln
             or ("contents/$PROTECTED_PATHS_CONFIG" in ln)]
    assert fetch, "no gh api contents/ fetch of the config found"
    line = fetch[0]
    assert "?ref=$base" in line or "?ref=${base}" in line, line
    assert "headRef" not in line
    assigns = [ln for ln in code if ln.strip().startswith("base=")]
    assert assigns and "baseRefName" in assigns[0], (
        "the `base` the fetch is keyed on must come from the PR's baseRefName")
    assert not any("headRefOid" in ln or "headRefName" in ln for ln in assigns)


def test_the_rule_is_checked_out_from_the_default_branch():
    checkouts = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout")]
    assert len(checkouts) == 1, "exactly one checkout, of the rule module"
    with_ = checkouts[0]["with"]
    assert "default_branch" in str(with_.get("ref", "")), (
        "the checkout must be pinned to the repository's default branch, "
        "never a PR head")
    assert RULE in str(with_.get("sparse-checkout", ""))


# ── self-protection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [".github/workflows/pr-watcher.yml", RULE])
def test_the_door_and_its_rule_are_on_the_list(path):
    assert path in _live_list(), (
        f"{path} carries the merge decision and must be in protected_paths")


# ── the probe: exit codes ──────────────────────────────────────────────────

CFG = "protected_paths:\n  - tools/ci/pr_watcher.py\n  - tools/kanban\n"


def test_probe_clean_protected_and_prefix_semantics():
    code, hits, _ = pp.probe(CFG, ["README.md", "tools/ci/pr_watcher_helpers.py"])
    assert (code, hits) == (pp.EXIT_CLEAN, [])
    code, hits, _ = pp.probe(CFG, ["tools/kanban/cli.py"])
    assert (code, hits) == (pp.EXIT_PROTECTED, ["tools/kanban"])
    code, hits, _ = pp.probe(CFG, ["tools/ci/pr_watcher.py", "x.md"])
    assert (code, hits) == (pp.EXIT_PROTECTED, ["tools/ci/pr_watcher.py"])


def test_probe_refuses_an_unreadable_config_and_a_truncated_listing():
    code, _, reason = pp.probe("protected_paths: [a, b", ["README.md"])
    assert code == pp.EXIT_UNDECIDABLE and "unreadable" in reason
    code, _, reason = pp.probe("- just\n- a list\n", ["README.md"])
    assert code == pp.EXIT_UNDECIDABLE
    code, _, reason = pp.probe(CFG, ["README.md"], expected_count=3)
    assert code == pp.EXIT_UNDECIDABLE and "truncated" in reason
    code, _, reason = pp.probe(CFG, None)
    assert code == pp.EXIT_UNDECIDABLE and "unavailable" in reason


def test_probe_reports_protection_off_as_off_not_as_clean():
    code, hits, reason = pp.probe("protected_paths: []\n", ["tools/ci/pr_watcher.py"])
    assert (code, hits) == (pp.EXIT_CLEAN, [])
    assert "protection off" in reason
    code, _, reason = pp.probe("other: 1\n", ["tools/ci/pr_watcher.py"])
    assert code == pp.EXIT_CLEAN and "protection off" in reason


def test_the_fallback_reader_agrees_with_pyyaml_on_the_live_config():
    text = CONFIG.read_text(encoding="utf-8")
    assert pp._fallback_block_sequence(text) == pp.load_protected_paths(text) == _live_list()


@pytest.mark.parametrize("text", [
    "protected_paths: [a, b]\n",
    "protected_paths:\n  - a\n  nested: 1\n",
    "protected_paths:\n  - &anchor a\n",
    "protected_paths: yes\n",
    "other: 1\n",
])
def test_the_fallback_reader_refuses_what_it_is_not_sure_of(text):
    with pytest.raises(pp.ConfigUnreadable):
        pp._fallback_block_sequence(text)


def test_the_probe_cli_exit_codes(tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(CFG, encoding="utf-8")
    files = tmp_path / "files"
    files.write_text("tools/ci/pr_watcher.py\nREADME.md\n", encoding="utf-8")
    argv = [sys.executable, str(ROOT / RULE), "--config-file", str(cfg)]
    r = subprocess.run(argv + ["--files-from", str(files)], capture_output=True, text=True)
    assert r.returncode == pp.EXIT_PROTECTED, r.stdout
    assert "tools/ci/pr_watcher.py" in r.stdout
    r = subprocess.run(argv + ["--files", "README.md"], capture_output=True, text=True)
    assert r.returncode == pp.EXIT_CLEAN, r.stdout
    r = subprocess.run(argv + ["--files", "README.md", "--expected-count", "2"],
                       capture_output=True, text=True)
    assert r.returncode == pp.EXIT_UNDECIDABLE, r.stdout
    r = subprocess.run(argv[:2] + ["--config-file", str(tmp_path / "missing.yaml"),
                                   "--files", "README.md"], capture_output=True, text=True)
    assert r.returncode == pp.EXIT_UNDECIDABLE, r.stdout


# ── behaviour: the workflow's own shell against a fake forge ───────────────

FAKE_GH = r'''#!/usr/bin/env bash
# A fake `gh` for the workflow's run: script. Every invocation is appended to
# $GH_LOG; the scenario is described by files under $GH_FIXTURES.
printf '%s\n' "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr list")   cat "$GH_FIXTURES/prs.json" ;;
  "pr checks") echo pass ;;
  "pr view")   cat "$GH_FIXTURES/view-$3.json" ;;
  "pr merge")  echo "merged $3" >> "$GH_MERGES" ;;
  "api "*)
    case "$2" in
      *"/contents/"*) [ -f "$GH_FIXTURES/config.yaml" ] && cat "$GH_FIXTURES/config.yaml" || exit 1 ;;
      *"/pulls/"*"/files"*) n=$(printf '%s' "$2" | sed -E 's#.*/pulls/([0-9]+)/files.*#\1#'); cat "$GH_FIXTURES/files-$n.txt" ;;
      *) exit 1 ;;
    esac ;;
  *) exit 1 ;;
esac
'''

VIEW = '{"mergeable":"MERGEABLE","baseRefName":"main","changedFiles":%d}'


def _bash() -> str:
    for cand in ("bash", "C:/Program Files/Git/bin/bash.exe"):
        found = shutil.which(cand) or (cand if pathlib.Path(cand).exists() else None)
        if found:
            return found
    raise AssertionError("bash is required to execute the workflow's run: script")


def _drive(tmp_path, *, prs, files, config, changed=None):
    """Run the workflow's run: script with a fake gh. Returns (merges, log, output)."""
    assert shutil.which("jq"), "jq is required: the workflow's script pipes gh through it"
    binp = tmp_path / "bin"
    binp.mkdir()
    gh = binp / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    py3 = binp / "python3"
    py3.write_text('#!/usr/bin/env bash\nexec "$REAL_PYTHON" "$@"\n', encoding="utf-8", newline="\n")
    for p in (gh, py3):
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    fx = tmp_path / "fixtures"
    fx.mkdir()
    (fx / "prs.json").write_text(
        "[" + ",".join(
            '{"number":%d,"headRefName":"kanban/t%d","isDraft":false,"title":"t%d"}' % (n, n, n)
            for n in prs) + "]", encoding="utf-8")
    for n in prs:
        (fx / f"view-{n}.json").write_text(
            VIEW % ((changed or {}).get(n, len(files[n]))), encoding="utf-8")
        (fx / f"files-{n}.txt").write_text("".join(f + "\n" for f in files[n]), encoding="utf-8")
    if config is not None:
        (fx / "config.yaml").write_text(config, encoding="utf-8")
    tree = tmp_path / "tree"
    (tree / "tools" / "ci").mkdir(parents=True)
    shutil.copy(ROOT / RULE, tree / RULE)
    script = tmp_path / "run.sh"
    script.write_text(_run_script(), encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update({
        "PATH": str(binp) + os.pathsep + env.get("PATH", ""),
        "GH_LOG": str(tmp_path / "gh.log"), "GH_MERGES": str(tmp_path / "merges.log"),
        "GH_FIXTURES": str(fx), "REAL_PYTHON": sys.executable.replace("\\", "/"),
        "REPO": "o/r", "PROTECTED_PATHS_CONFIG": "args/pr_watcher_config.yaml",
        "GH_TOKEN": "x",
    })
    r = subprocess.run([_bash(), "-e", str(script)], cwd=str(tree), env=env,
                       capture_output=True, text=True, timeout=120)
    merges = (tmp_path / "merges.log").read_text(encoding="utf-8") \
        if (tmp_path / "merges.log").exists() else ""
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    return merges, log, r.stdout + r.stderr, r.returncode


LIVE_CFG = "protected_paths:\n  - tools/ci/pr_watcher.py\n  - .github/workflows/pr-watcher.yml\n"


def test_a_protected_pr_is_skipped_and_a_clean_sibling_merges(tmp_path):
    merges, log, out, rc = _drive(
        tmp_path, prs=[7, 8],
        files={7: ["tools/ci/pr_watcher.py", "docs/x.md"], 8: ["docs/y.md"]},
        config=LIVE_CFG)
    assert rc == 0, out
    assert "merged 8" in merges and "merged 7" not in merges, (merges, out)
    assert "PR #7: PROTECTED" in out and "tools/ci/pr_watcher.py" in out, out
    assert "PR #8: CLEAN" in out, out
    # The list was read from the BASE branch, once, keyed on its name.
    fetches = [ln for ln in log.splitlines() if "/contents/args/pr_watcher_config.yaml" in ln]
    assert len(fetches) == 1 and "ref=main" in fetches[0], log


def test_an_unreadable_config_merges_nothing(tmp_path):
    merges, _, out, rc = _drive(tmp_path, prs=[7], files={7: ["docs/y.md"]}, config=None)
    assert rc == 0, out
    assert merges == "", (merges, out)
    assert "fail-closed" in out, out


def test_a_truncated_file_listing_merges_nothing(tmp_path):
    merges, _, out, rc = _drive(tmp_path, prs=[7], files={7: ["docs/y.md"]},
                                config=LIVE_CFG, changed={7: 3})
    assert rc == 0, out
    assert merges == "", (merges, out)
    assert "UNDECIDABLE" in out and "truncated" in out, out


def test_a_pr_deleting_its_own_path_from_the_list_is_still_refused(tmp_path):
    """The fake forge serves the BASE config whatever the PR's head says --
    which is the property the workflow relies on: the head is never asked."""
    merges, log, out, rc = _drive(
        tmp_path, prs=[9],
        files={9: ["args/pr_watcher_config.yaml"]},
        config=LIVE_CFG + "  - args/pr_watcher_config.yaml\n")
    assert rc == 0, out
    assert merges == "", out
    assert "args/pr_watcher_config.yaml" in out
    fetches = [ln for ln in log.splitlines() if "/contents/" in ln]
    assert fetches and all("ref=main" in ln and "headRef" not in ln for ln in fetches), log


def test_the_survey_in_the_docstring_is_the_shipped_rule():
    """The measurement quoted above is re-derived with the shipped predicate,
    never a second copy: the same function the ladder and workflow run."""
    sample = textwrap.dedent("""\
        2143 tools/ci/pr_watcher.py
        2069 tools/kanban/task_factory.py
        2100 docs/x.md,tools/ci/pr_watcher_helpers.py
    """).strip().splitlines()
    verdict = {ln.split()[0]: pp.protected_hits(ln.split()[1].split(","), _live_list())
               for ln in sample}
    assert verdict["2143"] == ["tools/ci/pr_watcher.py"]
    assert verdict["2069"] == ["tools/kanban/task_factory.py"]
    assert verdict["2100"] == []
