"""A closed census may lose names and must never gain one (cef-ci-02).

WHAT WENT RED HERE, AND WHY IT COULD
------------------------------------
`args/ci_test_backlog.txt` and `args/ci_skip_census.txt` are enumerated rather
than counted because, in `args/test_gating_gate.yaml`'s own words, "a bare count
can be held constant while the set churns". Identity was tracked — and never
ratcheted. Nothing compared either census against its previous self, so the only
thing between a gated test and the grandfathered list was the numeric ceiling.

Measured on main at 42f7ea894: `skip_max` was 81 against 81 registered sites, and
`backlog_max` was 1711 against 1703 entries. Eight slots, and eight is enough —
un-gating eight CEF suites into the census left `--check-coverage` reporting
"0 unlisted" and exiting 0.

`test_the_ungating_manoeuvre_is_refused` is the recorded RED: against the merge
base it FAILS, because `tools/ci/census_growth.py` does not exist there and the
manoeuvre is accepted.

WHY THE ASSERTIONS ARE BEHAVIOURAL AND NOT NUMERIC
--------------------------------------------------
`assert backlog_max == 1703` would pin a number that is SUPPOSED to move — it
goes down every time somebody gates a backlogged file — so it would fail on the
good outcome and earn a `|| true` inside a week. What must hold is the property:
a name added to a closed census is refused, whatever the counts are that day.
So the manoeuvre is performed against a synthetic tree and the gate's verdict is
asserted, rather than the tree's arithmetic.

`test_a_shrinking_census_is_not_a_finding` is the discrimination proof. A check
that refused every census edit would pass the test above while being useless and
would block the one edit policy actively wants.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ci import census_growth  # noqa: E402
from tools.ci.isolation_run import ResolutionError  # noqa: E402


# --------------------------------------------------------------------------- #
# A disposable git repo holding just the two censuses.
#
# Built rather than pointed at the real checkout on purpose: the assertions are
# about what the gate DOES to a census that changed, and the real censuses do
# not change during a test run. It also keeps the test self-sufficient, which is
# what the isolation half of the pipeline (trust-disc-02) requires — this file
# must pass alone, and a fixture reaching for the surrounding repository's git
# state is exactly the order-dependence that gate exists to catch.
# --------------------------------------------------------------------------- #
def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc


#: The base ref the fixture compares against. Never the checked-out branch:
#: `git init` puts HEAD on whatever `init.defaultBranch` says, so hard-coding
#: "main" fails on a machine configured for "master", and force-moving the
#: branch that IS checked out is refused by git outright.
BASE_BRANCH = "censusbase"

BASE_BACKLOG = """\
# CUI // SP-CTI
# Grandfathered census.
tests/test_alpha.py
tests/test_beta.py
tests/test_gamma.py
"""

BASE_SKIPS = """\
# CUI // SP-CTI
tests/test_alpha.py::test_one::pytest.skip[1]  # needs a live PostgreSQL service
"""


@pytest.fixture()
def census_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "args").mkdir(parents=True)
    (root / "args" / "ci_test_backlog.txt").write_text(BASE_BACKLOG, encoding="utf-8")
    (root / "args" / "ci_skip_census.txt").write_text(BASE_SKIPS, encoding="utf-8")

    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "census test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", BASE_BRANCH)
    return root


def _backlog(root: Path) -> Path:
    return root / "args" / "ci_test_backlog.txt"


def _report(root: Path):
    return census_growth.check(root, base=BASE_BRANCH)


def _entry(report, path: str):
    return next(c for c in report["censuses"] if c["path"] == path)


# --------------------------------------------------------------------------- #
# THE RED
# --------------------------------------------------------------------------- #
def test_the_ungating_manoeuvre_is_refused(census_repo: Path) -> None:
    """A gated suite moved into the grandfathered census is a finding.

    This is the manoeuvre measured on the live tree: the file leaves the
    allowlist and its path is appended here, which the ceiling cannot see
    because the ceiling is a count.
    """
    backlog = _backlog(census_repo)
    backlog.write_text(
        backlog.read_text(encoding="utf-8") + "tests/cortex/test_resolve_facade.py\n",
        encoding="utf-8",
    )

    report = _report(census_repo)

    assert report["ok"] is False
    assert "args/ci_test_backlog.txt" in report["grew"]
    entry = _entry(report, "args/ci_test_backlog.txt")
    assert entry["added"] == ["tests/cortex/test_resolve_facade.py"]
    assert entry["ok"] is False


def test_the_cli_exits_1_and_names_the_added_entry(census_repo: Path) -> None:
    """Exit 1, and the message must name the file — a count is not actionable."""
    backlog = _backlog(census_repo)
    backlog.write_text(
        backlog.read_text(encoding="utf-8") + "tests/cortex/test_resolve_trust_loop.py\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ci" / "census_growth.py"),
         "--check", "--root", str(census_repo), "--base", BASE_BRANCH],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(REPO_ROOT),
    )

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "tests/cortex/test_resolve_trust_loop.py" in proc.stderr


# --------------------------------------------------------------------------- #
# DISCRIMINATION — the check must not simply refuse every census edit
# --------------------------------------------------------------------------- #
def test_a_shrinking_census_is_not_a_finding(census_repo: Path) -> None:
    """Deleting a line is the outcome policy WANTS. It must pass."""
    backlog = _backlog(census_repo)
    kept = [
        line for line in backlog.read_text(encoding="utf-8").splitlines()
        if line.strip() != "tests/test_beta.py"
    ]
    backlog.write_text("\n".join(kept) + "\n", encoding="utf-8")

    report = _report(census_repo)

    assert report["ok"] is True
    entry = _entry(report, "args/ci_test_backlog.txt")
    assert entry["removed"] == ["tests/test_beta.py"]
    assert entry["added"] == []


def test_an_unchanged_census_is_not_a_finding(census_repo: Path) -> None:
    report = _report(census_repo)
    assert report["ok"] is True
    assert report["grew"] == []


def test_a_swap_is_a_finding_even_though_the_count_is_unchanged(census_repo: Path) -> None:
    """The exact failure mode enumeration exists for.

    One line out, one line in: the count is identical, the ceiling sees nothing,
    and a suite has left CI. This is the assertion that makes the census an
    identity ratchet rather than a budget.
    """
    backlog = _backlog(census_repo)
    text = backlog.read_text(encoding="utf-8").replace(
        "tests/test_beta.py", "tests/cortex/test_finding_store.py"
    )
    backlog.write_text(text, encoding="utf-8")

    report = _report(census_repo)
    entry = _entry(report, "args/ci_test_backlog.txt")

    assert entry["now"] == entry["base"], "the count is deliberately unchanged"
    assert report["ok"] is False
    assert entry["added"] == ["tests/cortex/test_finding_store.py"]
    assert entry["removed"] == ["tests/test_beta.py"]


# --------------------------------------------------------------------------- #
# THE SKIP CENSUS — same rule, same primitive
# --------------------------------------------------------------------------- #
def test_a_new_skip_site_is_a_finding(census_repo: Path) -> None:
    path = census_repo / "args" / "ci_skip_census.txt"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "tests/test_gamma.py::test_two::pytest.skip[1]  # the fixture is not vendored here\n",
        encoding="utf-8",
    )

    report = _report(census_repo)
    entry = _entry(report, "args/ci_skip_census.txt")

    assert report["ok"] is False
    assert entry["added"] == ["tests/test_gamma.py::test_two::pytest.skip[1]"]


def test_rewording_a_reason_is_not_a_new_skip(census_repo: Path) -> None:
    """The key is the identity; the reason is not.

    Improving a written reason must not read as registering a skip, or the gate
    punishes the one edit that makes the census more useful.
    """
    path = census_repo / "args" / "ci_skip_census.txt"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "needs a live PostgreSQL service",
            "needs a live PostgreSQL service; the SQLite conftest cannot express RLS",
        ),
        encoding="utf-8",
    )

    report = _report(census_repo)
    assert report["ok"] is True
    assert _entry(report, "args/ci_skip_census.txt")["added"] == []


# --------------------------------------------------------------------------- #
# THE THIRD STATE — could-not-compare is not clean
# --------------------------------------------------------------------------- #
def test_an_introduced_census_has_gained_nothing(census_repo: Path, tmp_path: Path) -> None:
    """A census absent at the base is being ADDED, not grown.

    Without this every entry in a brand-new census reads as a violation on the
    PR that introduces it, and the author's only route through is to disarm the
    gate.
    """
    new = census_repo / "args" / "ci_skip_census.txt"
    new.unlink()
    _git(census_repo, "add", "-A")
    _git(census_repo, "commit", "-q", "-m", "drop the skip census")
    _git(census_repo, "branch", "-f", BASE_BRANCH)
    new.write_text(BASE_SKIPS, encoding="utf-8")

    report = _report(census_repo)
    entry = _entry(report, "args/ci_skip_census.txt")

    assert entry["introduced"] is True
    assert entry["added"] == []
    assert report["ok"] is True


def test_an_unresolvable_base_raises_rather_than_reporting_clean(tmp_path: Path) -> None:
    """A comparison that could not be made has not found the censuses unchanged."""
    root = tmp_path / "norepo"
    (root / "args").mkdir(parents=True)
    (root / "args" / "ci_test_backlog.txt").write_text(BASE_BACKLOG, encoding="utf-8")

    with pytest.raises(ResolutionError):
        census_growth.check(root, base="definitely-not-a-ref")


def test_the_cli_exits_2_when_it_cannot_compare(tmp_path: Path) -> None:
    """Exit 2, distinct from both 0 and 1.

    A gate that could not run is not a gate that found nothing. Collapsing this
    into 0 is how a shallow checkout silently disarms the check.
    """
    root = tmp_path / "norepo2"
    (root / "args").mkdir(parents=True)
    (root / "args" / "ci_test_backlog.txt").write_text(BASE_BACKLOG, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ci" / "census_growth.py"),
         "--check", "--root", str(root), "--base", "definitely-not-a-ref"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(REPO_ROOT),
    )

    assert proc.returncode == 2, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# HONEST REPORTING
# --------------------------------------------------------------------------- #
def test_an_unsurveyed_census_reports_unmeasurable_not_a_clean_zero(census_repo: Path) -> None:
    """`args/ci_skip_census.txt` has no post-adoption history to measure.

    Reporting "0 fires" for a file nobody has measured is the four-causes-of-a-
    zero defect: no-traffic and measured-zero rendered identically.
    """
    report = _report(census_repo)

    backlog = _entry(report, "args/ci_test_backlog.txt")
    skips = _entry(report, "args/ci_skip_census.txt")

    assert backlog["fire_rate"] == "0/35 commits"
    assert skips["fire_rate"] == "unmeasurable"


def test_the_live_censuses_are_registered() -> None:
    """A registry matching nothing is a gate that runs and guards nothing.

    That is the property, and it is what is asserted: the registry is non-empty, every entry
    names a file that exists, and the two censuses this gate was BUILT for are still in it.

    It used to assert set EQUALITY against those two. That pinned today's population rather
    than the property, so registering a third census — which strengthens the gate — failed a
    test whose own docstring says the danger is a registry guarding too LITTLE. wire-req-01
    added `args/kanban_seeder_criteria_census.txt` and hit exactly that.
    """
    registered = {c.path for c in census_growth.CENSUSES}
    assert registered, "an empty registry is a gate that guards nothing"
    for original in ("args/ci_test_backlog.txt", "args/ci_skip_census.txt"):
        assert original in registered, (
            f"{original} is one of the two censuses this gate was built for; removing it "
            "would silently stop ratcheting the set it was written to protect"
        )
    for c in census_growth.CENSUSES:
        assert (REPO_ROOT / c.path).is_file(), f"{c.path} is registered but absent"


def test_the_seeder_criteria_census_is_ratcheted() -> None:
    """wire-req-01's census must be under the SET ratchet, not merely committed.

    It is the named path to arming `KANBAN_REQUIRE_ACCEPTANCE_CRITERIA`: the gate ships
    `report` because 13 modules — five of them live reflexes seeding `fix` cards every six
    hours — would raise on their next cycle if it were armed. A file that records that debt
    but is not ratcheted can grow silently, and then the gate can never be armed at all.

    This is the assertion the relaxation above gave up, put back pointing at the property
    that actually matters: not "the registry holds exactly these two", but "the file this
    programme depends on is being watched".
    """
    registered = {c.path for c in census_growth.CENSUSES}
    assert "args/kanban_seeder_criteria_census.txt" in registered, (
        "the seeder-criteria census is not registered in census_growth.CENSUSES, so it is "
        "not ratcheted and may gain entries silently"
    )
