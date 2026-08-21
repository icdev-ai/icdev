# CUI // SP-CTI
"""Is this deployment running the schema that MERGED? (autonomy-dep-01)

`code_staleness` reported every process on the live board as `no recorded code
version` — not because the code was missing, but because autonomy-id-01's
migration had never been APPLIED here. The code was on main, its tests were
green, and the capability produced nothing.

THE TEST THAT MATTERS MOST is the sort trap. `schema_migrations.version` holds
BOTH the closed legacy `NNN` sequence and 14-digit timestamps, and lexicographic
ordering puts `'343'` AFTER `'20260821...'` because `'3' > '2'`. Scoping this
card, that mistake produced the conclusion "no timestamped migration has ever
been applied", which was wrong by a factor of 37. A comparison built on a
maximum is confidently wrong; a set difference cannot make that error.

The other invariant: `unmeasurable` never becomes `current`. A database with no
migration history has applied nothing, and reporting that as "0 pending" would
call an empty deployment fully migrated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db import migration_drift as md  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. The sort trap — why this compares SETS
# --------------------------------------------------------------------------- #
def test_a_legacy_version_does_not_hide_applied_timestamps():
    """`'343' > '20260821...'` lexicographically. A max-based comparison reads
    the newest applied as 343 and concludes the entire timestamp era is
    pending. This is that exact scenario."""
    on_branch = {"342": "342_x", "343": "343_y",
                 "20260820231102": "20260820231102_a",
                 "20260821024132": "20260821024132_b"}
    applied = {"342", "343", "20260820231102", "20260821024132"}

    report = md.drift(on_branch=on_branch, applied=applied)
    assert report["state"] == md.CURRENT, (
        f"a lexicographic comparison leaked in: {report.get('pending')}"
    )
    assert report["pending_count"] == 0


def test_only_the_genuinely_missing_version_is_pending():
    on_branch = {"343": "343_y", "20260821024132": "20260821024132_b"}
    applied = {"343"}

    report = md.drift(on_branch=on_branch, applied=applied)
    assert report["state"] == md.PENDING
    assert [p["version"] for p in report["pending"]] == ["20260821024132"]
    assert report["pending"][0]["name"] == "20260821024132_b", (
        "a pending migration must be NAMED — a bare count cannot be acted on"
    )


def test_the_comparison_never_sorts_versions_to_decide():
    """Structural, and narrow: it pins that `drift` reaches no maximum. Written
    because the failure it guards produced a plausible, confident, wrong
    answer — the shape that survives review."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(md.drift)))
    fn = tree.body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(ast.dump(n) for n in body)
    for forbidden in ("'max'", "'min'"):
        assert forbidden not in code, (
            "drift() reached for a max/min over versions — see the sort trap"
        )


# --------------------------------------------------------------------------- #
# 2. Unmeasurable never becomes current
# --------------------------------------------------------------------------- #
def test_an_unreadable_branch_is_unmeasurable():
    """A shallow clone with no remote must not report every deployment current.

    Injected through a FAILING RUNNER rather than `on_branch=None`: None is the
    "not provided" sentinel, so passing it would fall through to a real git read
    and the test would pass for the wrong reason.
    """
    def _fail(*_a, **_k):
        raise OSError("no remote")

    report = md.drift(applied={"1"}, runner=_fail)
    assert report["state"] == md.UNMEASURABLE
    assert report["pending_count"] is None, "an unmeasurable report gave a count"


def test_an_unreadable_migrations_table_is_unmeasurable():
    report = md.drift(on_branch={"1": "1_a"}, applied=None)
    assert report["state"] == md.UNMEASURABLE
    assert report["pending_count"] is None


def test_a_database_with_no_history_is_unmeasurable_not_drifted():
    """A fresh database has applied nothing. Reporting every branch migration as
    pending is technically true and useless; reporting 0 pending would call an
    empty deployment fully migrated. Neither — it is unmeasurable."""
    report = md.drift(on_branch={"1": "1_a", "2": "2_b"}, applied=set())
    assert report["state"] == md.UNMEASURABLE
    assert "no migration history" in report["reason"]


def test_an_unreadable_branch_still_reports_what_it_could_read():
    """The report says which SIDE failed, because "git could not answer" and
    "the database could not answer" send you to different fixes."""
    def _fail(*_a, **_k):
        raise OSError("no remote")

    report = md.drift(applied={"1"}, runner=_fail)
    assert "could not be read" in report["reason"]
    assert report["applied_count"] is None


# --------------------------------------------------------------------------- #
# 3. What counts as applied
# --------------------------------------------------------------------------- #
def test_a_rolled_back_migration_is_not_applied():
    """Its schema change has been undone. Counting it would report a deployment
    as current while the column it added is gone."""
    class _Conn:
        def __init__(self):
            self.sql = None

        def execute(self, sql, *_a):
            self.sql = sql
            return self

        def fetchall(self):
            return [{"version": "1"}]

        def close(self):
            return None

    conn = _Conn()
    assert md.applied_versions(conn) == {"1"}
    assert "rolled_back_at IS NULL" in conn.sql, (
        "rolled-back migrations were counted as applied"
    )


def test_an_unreadable_table_returns_none_not_an_empty_set():
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("no such table")

        def close(self):
            return None

    assert md.applied_versions(_Boom()) is None


# --------------------------------------------------------------------------- #
# 4. Applied here but not on the branch is CONTEXT, not a finding
# --------------------------------------------------------------------------- #
def test_extra_local_migrations_do_not_make_it_pending():
    """A migration from a branch that never merged says nothing about whether
    this deployment is MISSING something."""
    report = md.drift(on_branch={"1": "1_a"}, applied={"1", "999"})
    assert report["state"] == md.CURRENT
    assert report["applied_not_on_branch"] == ["999"]


# --------------------------------------------------------------------------- #
# 5. The branch reader
# --------------------------------------------------------------------------- #
def _result(stdout, rc=0):
    class _R:
        returncode = rc

        def __init__(self, out):
            self.stdout = out

    return _R(stdout)


def test_branch_migrations_parses_versions_from_directory_names():
    out = "\n".join([
        "tools/db/migrations/343_legacy_thing",
        "tools/db/migrations/20260821024132_agent_sessions_code_identity",
        "tools/db/migrations/README.md",
        "tools/db/migrations/not_a_migration",
    ])
    got = md.branch_migrations(runner=lambda *_a, **_k: _result(out))
    assert got == {
        "343": "343_legacy_thing",
        "20260821024132": "20260821024132_agent_sessions_code_identity",
    }


def test_a_failed_git_call_is_none_not_an_empty_branch():
    """Empty would mean "the branch has no migrations", which reads as current."""
    assert md.branch_migrations(runner=lambda *_a, **_k: _result("", rc=128)) is None


def test_git_raising_is_none():
    def _boom(*_a, **_k):
        raise OSError("no git")

    assert md.branch_migrations(runner=_boom) is None


def test_it_uses_the_runners_own_version_regex():
    """"What counts as a migration" must not drift between applying and
    auditing. The runner's regex is imported, never re-expressed."""
    import inspect

    src = inspect.getsource(md)
    assert "from tools.db.migration_runner import _VERSION_DIR_RE" in src
    assert "_VERSION_DIR_RE" in inspect.getsource(md.branch_migrations)


# --------------------------------------------------------------------------- #
# 6. Exit codes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state,gate,expected", [
    (md.CURRENT, True, 0),
    (md.PENDING, True, 1),
    (md.PENDING, False, 0),
    (md.UNMEASURABLE, False, 2),
    (md.UNMEASURABLE, True, 2),
])
def test_exit_codes(monkeypatch, capsys, state, gate, expected):
    """Unmeasurable exits 2 even without --gate: a check that could not run is
    not a check that found nothing."""
    monkeypatch.setattr(md, "drift", lambda **_k: {
        "state": state, "ref": "origin/main", "reason": "",
        "pending": [], "pending_count": 0, "on_branch_count": 1,
        "applied_count": 1, "applied_not_on_branch_count": 0,
        "applied_not_on_branch": [],
    })
    argv = ["--gate"] if gate else []
    assert md.main(argv) == expected
    capsys.readouterr()
