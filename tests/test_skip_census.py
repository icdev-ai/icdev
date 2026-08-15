# CUI // SP-CTI
"""Tests for the CI skip census (trust-disc-03).

The thing under test is a GATE, so most of these assert that it goes RED. A gate
that has only ever been observed green is indistinguishable from a gate that
cannot fire — which is the same defect class the gate itself exists to catch
(`.claude/settings.json` wrapped its PreToolUse hook in `|| true` and eleven
checks printed BLOCKED while blocking nothing).

Note on style: every skip-shaped construct in this file is a STRING that gets
`ast.parse`d, never a real call. A literal `pytest.skip(...)` here would be a
genuine skip site inside a gated test file, and the census would rightly demand
it be registered — this suite would then be enforcing a rule it breaks.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "ci" / "skip_census.py"


def _load_module():
    """Load the tool BY PATH, matching how CI and the pre-commit hook invoke it."""
    spec = importlib.util.spec_from_file_location("_test_skip_census", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves `cls.__module__` through
    # sys.modules, and a by-path load that skips this step raises
    # `AttributeError: 'NoneType' object has no attribute '__dict__'` on the
    # first decorated class. The tool's own sibling loader does the same.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sc = _load_module()


# --------------------------------------------------------------------------- #
# A synthetic checkout, so the gate's failure modes can be exercised without
# editing the real tree.
# --------------------------------------------------------------------------- #
GATE_YAML = """\
scope:
  roots: [tests/]
  patterns: ["test_*.py"]
exclusions: []
backlog_file: args/ci_test_backlog.txt
backlog_max: 10
skip_census:
  census_file: args/ci_skip_census.txt
  skip_max: {skip_max}
  min_reason_chars: 12
"""


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A minimal checkout: one gated test file, one ungated, empty census."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "args" / "ci_test_files").mkdir(parents=True)

    (tmp_path / "tests" / "test_gated.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_ungated.py").write_text(
        "import pytest\n\n\ndef test_x():\n    pytest.skip('ungated, not our problem')\n",
        encoding="utf-8",
    )
    (tmp_path / "args" / "ci_test_files" / "core.txt").write_text(
        "tests/test_gated.py\n", encoding="utf-8"
    )
    (tmp_path / "args" / "ci_test_files" / "windows.txt").write_text("", encoding="utf-8")
    (tmp_path / "args" / "ci_test_backlog.txt").write_text(
        "tests/test_ungated.py\n", encoding="utf-8"
    )
    (tmp_path / "args" / "test_gating_gate.yaml").write_text(
        GATE_YAML.format(skip_max=10), encoding="utf-8"
    )
    (tmp_path / "args" / "ci_skip_census.txt").write_text("# census\n", encoding="utf-8")

    # tmp_path is not a git repo, so `git ls-files` fails and the os.walk fallback
    # runs. Pin it anyway: if the temp dir ever lands inside a checkout, the walk
    # would be replaced by that repo's file list and every assertion below would
    # be measuring the wrong tree.
    def _walk(root: Path):
        return sorted(
            p.relative_to(root).as_posix()
            for p in Path(root).rglob("*") if p.is_file()
        )

    monkeypatch.setattr(sc._gtl, "_tracked_files", _walk)
    return tmp_path


def _set_gate(root: Path, **kwargs):
    root.joinpath("args", "test_gating_gate.yaml").write_text(
        GATE_YAML.format(**{"skip_max": 10, **kwargs}), encoding="utf-8"
    )


def _write_gated(root: Path, body: str):
    root.joinpath("tests", "test_gated.py").write_text(
        textwrap.dedent(body), encoding="utf-8"
    )


def _write_census(root: Path, *lines: str):
    root.joinpath("args", "ci_skip_census.txt").write_text(
        "# census\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Static scan — the vocabulary of "this test did not run"
# --------------------------------------------------------------------------- #
def test_scan_finds_every_skip_spelling():
    """One site per spelling, each classified, none double-counted.

    `@pytest.mark.skipif(...)` is both a Call and an Attribute in the AST. A
    naive `ast.walk` records it twice, which would make the ordinal of every
    later site in the same function wrong and its census key unmatchable.
    """
    source = textwrap.dedent(
        """
        import pytest
        import unittest

        pytestmark = pytest.mark.skipif(True, reason="module wide")


        def test_call():
            pytest.skip("inline")


        @pytest.mark.skip
        def test_bare_decorator():
            assert True


        @pytest.mark.skipif(True, reason="conditional")
        def test_call_decorator():
            assert True


        def test_import_guard():
            pytest.importorskip("nonexistent")


        class TestCase(unittest.TestCase):
            def test_method(self):
                self.skipTest("unittest style")
        """
    )
    sites = sc.scan_source("tests/test_x.py", source)
    kinds = [s.kind for s in sites]

    assert kinds == [
        "pytest.mark.skipif",   # module-level pytestmark
        "pytest.skip",
        "pytest.mark.skip",     # bare decorator, no call
        "pytest.mark.skipif",   # decorator WITH a call — recorded once
        "pytest.importorskip",
        "unittest.skipTest",
    ]
    quals = [s.qualname for s in sites]
    assert quals[0] == "<module>"
    assert quals[1] == "test_call"
    assert quals[-1] == "TestCase.test_method"
    # The decorator belongs to what it decorates, not to the enclosing module.
    assert quals[2] == "test_bare_decorator"


def test_ordinals_are_stable_when_a_second_skip_is_added():
    """Adding a skip must not renumber an existing one — that is why every key
    carries an ordinal even when it is the only site in its group."""
    one = sc.scan_source("tests/t.py", "import pytest\ndef test_a():\n    pytest.skip('x')\n")
    assert [s.key for s in one] == ["tests/t.py::test_a::pytest.skip[1]"]

    two = sc.scan_source(
        "tests/t.py",
        "import pytest\ndef test_a():\n    pytest.skip('x')\n    pytest.skip('y')\n",
    )
    keys = [s.key for s in two]
    assert keys[0] == one[0].key, "the existing site's key changed — its reason would be orphaned"
    assert keys[1] == "tests/t.py::test_a::pytest.skip[2]"


def test_key_survives_the_census_parser():
    """Regression: the ordinal separator must not collide with the comment mark.

    The first cut keyed sites as `...::pytest.skip#1` and split the reason on the
    first `#`. Every line then parsed as malformed and the whole census read as
    unregistered — the gate caught it on its own adoption run, which is the only
    reason it is not still in here.
    """
    line = "tests/t.py::test_a::pytest.skip[1]  # a genuinely written reason"
    parsed = sc.parse_census(line)
    assert parsed.malformed == []
    assert set(parsed.entries) == {"tests/t.py::test_a::pytest.skip[1]"}
    assert parsed.entries["tests/t.py::test_a::pytest.skip[1]"].reason == "a genuinely written reason"


def test_scan_ignores_a_file_that_does_not_parse():
    assert sc.scan_source("tests/t.py", "def test_a(:\n") == []


# --------------------------------------------------------------------------- #
# Scope — the census covers exactly the gated allowlist, plus its conftests
# --------------------------------------------------------------------------- #
def test_scope_is_the_gated_allowlist_not_every_test_file(fake_repo):
    scope = sc.gated_scope(fake_repo)
    assert "tests/test_gated.py" in scope
    assert "tests/test_ungated.py" not in scope, (
        "an ungated file's skips are not this gate's business — gated_test_list's "
        "backlog already governs it, and claiming it here would double-count the debt"
    )


def test_scope_includes_conftest_beside_a_gated_file(fake_repo):
    """A session-scoped autouse fixture that skips silences a whole directory,
    and conftest.py is not a collectible module so the coverage census never
    sees it."""
    fake_repo.joinpath("tests", "conftest.py").write_text("x = 1\n", encoding="utf-8")
    assert "tests/conftest.py" in sc.gated_scope(fake_repo)


def test_conftest_without_a_gated_neighbour_is_out_of_scope(fake_repo):
    fake_repo.joinpath("tests", "sub").mkdir()
    fake_repo.joinpath("tests", "sub", "conftest.py").write_text("x = 1\n", encoding="utf-8")
    assert "tests/sub/conftest.py" not in sc.gated_scope(fake_repo)


# --------------------------------------------------------------------------- #
# The gate goes RED
# --------------------------------------------------------------------------- #
def test_an_unregistered_skip_fails(fake_repo):
    """The acceptance criterion: adding a skip to a gated test fails CI."""
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("no reason given anywhere")
    """)
    report = sc.census(fake_repo)
    assert report["ok"] is False
    assert report["unregistered"] == ["tests/test_gated.py::test_ok::pytest.skip[1]"]
    # The message must name the offending site AND the file to append to; a gate
    # whose failure needs an investigation gets neutralised instead of fixed.
    joined = " ".join(report["errors"])
    assert "tests/test_gated.py::test_ok::pytest.skip[1]" in joined
    assert "args/ci_skip_census.txt" in joined


def test_registering_it_with_a_reason_passes(fake_repo):
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("platform schema is absent under SQLite")
    """)
    _write_census(
        fake_repo,
        "tests/test_gated.py::test_ok::pytest.skip[1]  # SQLite fixture lacks the "
        "platform schema; covered by the PG tier instead",
    )
    report = sc.census(fake_repo)
    assert report["ok"] is True, report["errors"]
    assert report["registered"] == 1


@pytest.mark.parametrize("reason", ["", "flaky", "TBD", "wip", "todo", "short"])
def test_a_non_reason_fails(fake_repo, reason):
    """"flaky" records that a skip happened, not why it is acceptable."""
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("x")
    """)
    _write_census(fake_repo, f"tests/test_gated.py::test_ok::pytest.skip[1]  # {reason}")
    report = sc.census(fake_repo)
    assert report["ok"] is False
    assert any("reason" in e for e in report["errors"]), report["errors"]


def test_the_ceiling_catches_a_skip_that_was_registered_anyway(fake_repo):
    """Registering a new skip satisfies the by-name check. The ceiling is what
    makes adding one a deliberate, reviewable act rather than a one-line
    workaround."""
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("a")
            pytest.skip("b")
    """)
    _write_census(
        fake_repo,
        "tests/test_gated.py::test_ok::pytest.skip[1]  # a properly written reason here",
        "tests/test_gated.py::test_ok::pytest.skip[2]  # another properly written reason",
    )
    _set_gate(fake_repo, skip_max=1)
    report = sc.census(fake_repo)
    assert report["ok"] is False
    assert any("above the ceiling" in e for e in report["errors"]), report["errors"]
    assert any("never raise it" in e for e in report["errors"])


def test_a_duplicate_entry_fails(fake_repo):
    """What a careless union merge leaves behind — the census file is merge=union
    exactly like args/ci_test_files/*.txt."""
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("a")
    """)
    line = "tests/test_gated.py::test_ok::pytest.skip[1]  # a properly written reason here"
    _write_census(fake_repo, line, line)
    report = sc.census(fake_repo)
    assert report["ok"] is False
    assert any("twice" in e for e in report["errors"]), report["errors"]


def test_a_malformed_line_fails(fake_repo):
    _write_census(fake_repo, "tests/test_gated.py  # not a site key")
    report = sc.census(fake_repo)
    assert report["ok"] is False
    assert any("malformed" in e for e in report["errors"]), report["errors"]


# --------------------------------------------------------------------------- #
# The gate stays GREEN where it should
# --------------------------------------------------------------------------- #
def test_a_stale_entry_warns_but_never_fails(fake_repo):
    """Deleting a skip is the outcome this tool wants. It must not cost the PR
    that does it a red build for forgetting one line of bookkeeping."""
    _write_census(
        fake_repo,
        "tests/test_gated.py::test_gone::pytest.skip[1]  # this site no longer exists",
    )
    report = sc.census(fake_repo)
    assert report["ok"] is True, report["errors"]
    assert report["stale"] == ["tests/test_gated.py::test_gone::pytest.skip[1]"]


def test_prune_drops_stale_entries_and_keeps_the_rest(fake_repo):
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("a")
    """)
    _write_census(
        fake_repo,
        "tests/test_gated.py::test_ok::pytest.skip[1]  # a properly written reason here",
        "tests/test_gated.py::test_gone::pytest.skip[1]  # this site no longer exists",
    )
    result = sc.prune(fake_repo)
    assert result["pruned"] == ["tests/test_gated.py::test_gone::pytest.skip[1]"]
    text = fake_repo.joinpath("args", "ci_skip_census.txt").read_text(encoding="utf-8")
    assert "test_ok" in text and "test_gone" not in text
    assert text.startswith("# census"), "prune must preserve the header, not rewrite the file"


def test_a_partial_scan_does_not_report_stale_or_breach_the_ceiling(fake_repo):
    """The pre-commit fast path scans only the staged files. It cannot tell a
    deleted site from an unscanned one, so reporting "80 entries are stale" for a
    one-file commit would train everyone to ignore the tool."""
    _write_census(
        fake_repo,
        "tests/test_gated.py::test_elsewhere::pytest.skip[1]  # a properly written reason",
    )
    _set_gate(fake_repo, skip_max=0)
    report = sc.census(fake_repo, files=["tests/test_gated.py"])
    assert report["partial"] is True
    assert report["stale"] == []
    assert report["ok"] is True, report["errors"]


def test_a_missing_tests_tree_reports_not_run_rather_than_crying_wolf(tmp_path):
    """An installed wheel mirrors args/ but ships no suite. Flagging all 81
    entries there is how a check earns a `|| true`."""
    report = sc.census(tmp_path)
    assert report["ran"] is False and report["ok"] is True


# --------------------------------------------------------------------------- #
# Runtime half — what the gated run actually skipped
# --------------------------------------------------------------------------- #
def _junit(root: Path, body: str) -> Path:
    path = root / "report.xml"
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>',
                    encoding="utf-8")
    return path


def test_runtime_report_counts_and_attributes_skips(fake_repo):
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("a")
    """)
    _write_census(
        fake_repo,
        "tests/test_gated.py::test_ok::pytest.skip[1]  # a properly written reason here",
    )
    abs_path = (fake_repo / "tests" / "test_gated.py").as_posix()
    xml = _junit(fake_repo, (
        f'<testcase classname="tests.test_gated" name="test_ok">'
        f'<skipped type="pytest.skip" message="a">{abs_path}:5: a</skipped></testcase>'
        f'<testcase classname="tests.test_gated" name="test_other"/>'
    ))
    report = sc.runtime_report([xml], fake_repo)
    assert report["total_tests"] == 2
    assert report["total_skipped"] == 1
    assert report["skip_rate"] == 0.5
    assert report["per_file"]["tests/test_gated.py"]["skipped"] == 1
    assert report["per_file"]["tests/test_gated.py"]["registered_sites"] == 1
    assert report["unaccounted"] == []
    assert report["ok"] is True


def test_a_runtime_skip_with_no_declared_site_is_unaccounted(fake_repo):
    """The whole reason the runtime half exists: a conftest fixture, a plugin, or
    an alias can silence a gated file without the file's own source saying so,
    and the static scan is blind to all three."""
    abs_path = (fake_repo / "tests" / "test_gated.py").as_posix()
    xml = _junit(fake_repo, (
        f'<testcase classname="tests.test_gated" name="test_ok">'
        f'<skipped type="pytest.skip" message="fixture said no">'
        f'{abs_path}:1: fixture said no</skipped></testcase>'
    ))
    report = sc.runtime_report([xml], fake_repo)
    assert report["ok"] is False
    assert report["unaccounted"] == ["tests/test_gated.py"]
    assert any("silenced them" in e for e in report["errors"]), report["errors"]


def test_an_empty_run_is_not_a_clean_run(fake_repo):
    """0 skipped out of 0 collected is UNKNOWN, not zero — the same trap as a
    coverage census over a tree with no tests in it."""
    report = sc.runtime_report([_junit(fake_repo, "")], fake_repo)
    assert report["ok"] is False
    assert any("zero testcases" in e for e in report["errors"]), report["errors"]


def test_an_unreadable_report_fails_rather_than_reporting_zero(fake_repo):
    report = sc.runtime_report([fake_repo / "does-not-exist.xml"], fake_repo)
    assert report["ok"] is False
    assert any("UNKNOWN" in e for e in report["errors"]), report["errors"]


def test_classname_attribution_falls_back_when_the_body_is_absent(fake_repo):
    """Collection-time skips can arrive with no body text. classname is the only
    identifier left, and it must resolve to a real file rather than be dropped."""
    xml = _junit(fake_repo, (
        '<testcase classname="tests.test_gated" name="test_ok">'
        '<skipped type="pytest.skip" message="no body"/></testcase>'
    ))
    report = sc.runtime_report([xml], fake_repo)
    assert "tests/test_gated.py" in report["per_file"]
    assert report["unattributed"] == []


# --------------------------------------------------------------------------- #
# The pre-commit fast path
# --------------------------------------------------------------------------- #
def test_staged_filter_keeps_gated_files_and_drops_the_rest(fake_repo):
    staged = sc.staged_gated_test_files(
        fake_repo,
        files=["tests/test_gated.py", "tests/test_ungated.py", "tools/ci/skip_census.py"],
    )
    assert staged == ["tests/test_gated.py"]


# --------------------------------------------------------------------------- #
# The REAL tree — the gate this PR is actually installing
# --------------------------------------------------------------------------- #
def test_the_live_census_passes_its_own_gate():
    """If this fails, main is red for a reason the failure message names."""
    report = sc.census(REPO_ROOT)
    assert report["ran"] is True
    assert report["ok"] is True, report["errors"]


def test_the_live_census_is_enumerated_not_counted():
    """Acceptance: 'The existing skip census is enumerated, not counted.'

    A bare count can be held constant while the set churns — delete one skip, add
    another, count unchanged, gate green. So the census must name every site, and
    the ceiling must equal the enumerated count with no headroom for the set to
    grow into unobserved.
    """
    report = sc.census(REPO_ROOT)
    census_text = (REPO_ROOT / str(report["census_file"])).read_text(encoding="utf-8")
    parsed = sc.parse_census(census_text)

    assert parsed.malformed == [] and parsed.duplicates == []
    assert len(parsed.entries) == report["total_sites"], (
        "every skip site in a gated file must appear in the census BY NAME"
    )
    assert report["registered"] == report["skip_max"], (
        "the ceiling carries no headroom: headroom is room for unmeasured surface "
        "to grow without anyone deciding to grow it"
    )
    assert report["total_sites"] > 0, (
        "a census of zero sites means the scan found nothing, which on this tree "
        "means the scope broke — not that the skips went away"
    )


def test_every_live_census_entry_names_a_site_that_exists():
    """A census whose entries have drifted off their sites is a list of prose."""
    report = sc.census(REPO_ROOT)
    assert report["stale"] == [], (
        "run `python tools/ci/skip_census.py --prune` and lower skip_census.skip_max"
    )


def test_the_cli_gate_exits_nonzero_on_an_unregistered_skip(tmp_path, fake_repo):
    """End-to-end through the CLI, because that is what CI and the hook run —
    an in-process assertion would not catch a broken exit code or a crash in
    argument handling."""
    _write_gated(fake_repo, """
        import pytest


        def test_ok():
            pytest.skip("brand new, registered nowhere")
    """)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(fake_repo), "--check", "--json"],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["static"]["unregistered"] == [
        "tests/test_gated.py::test_ok::pytest.skip[1]"
    ]


def test_the_cli_gate_exits_zero_on_the_live_tree():
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--check"],
        capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_seed_refuses_to_overwrite_an_existing_census(fake_repo):
    """--seed is an adoption tool. If it could be re-run at will it would be a
    one-command way to launder a new skip past the by-name check."""
    with pytest.raises(sc.SkipCensusError, match="already exists"):
        sc.seed(fake_repo)
