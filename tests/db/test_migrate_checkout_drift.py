# CUI // SP-CTI
"""`Pending: 0` from a checkout BEHIND the branch is not a currency claim.

THE DEFECT, measured 2026-09-12 while clearing task-det-12d839d263 from a
worktree 7 commits behind main. Against the SAME live PostgreSQL, in the same
minute:

    python tools/db/migration_drift.py   -> state: pending,
                                            20260912122759_add_experiment_candidate_lane
    python tools/db/migrate.py --up --dry-run -> "No pending migrations."

The detector reads the DEFAULT BRANCH out of git; the applier reads THIS
FILESYSTEM. So the tool an operator acts on is the one that reads as "nothing
to do", and the card's own instructions had to warn about it in prose.

The consumer made it a certification rather than merely confusing output:
`production_audit.check_migration_status` (PRF-001) read that `pending_count`
and reported `pass` / "All migrations applied", after which
`production_remediate` auto-fixed PRF-001 by running `--up` — which applied
nothing and exited 0. The audit that exists to catch an unmigrated deployment
certified one.

These tests pin the seam in BOTH directions: the derivation must name what is
absent, and the audit must refuse to call it a pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db import migrate as migrate_cli  # noqa: E402


class _Runner:
    """Stand-in for MigrationRunner exposing only what the derivation reads."""

    def __init__(self, migrations_dir):
        self.migrations_dir = migrations_dir


def _mkdir_migration(root: Path, name: str, *, runnable: bool = True) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if runnable:
        (d / "up.py").write_text("def up(conn):\n    return {}\n", encoding="utf-8")


@pytest.fixture()
def migrations_dir(tmp_path: Path) -> Path:
    d = tmp_path / "migrations"
    d.mkdir()
    return d


# ── the derivation ──────────────────────────────────────────────────────────

def test_branch_migration_absent_from_this_checkout_reads_behind(migrations_dir):
    """The exact live case: the branch holds one this filesystem does not."""
    _mkdir_migration(migrations_dir, "20260911220746_hook_events_session_start_type")

    # Drive the git read deterministically rather than depending on a remote.
    drift = _drift_with_branch(
        migrations_dir,
        {
            "20260911220746": "20260911220746_hook_events_session_start_type",
            "20260912122759": "20260912122759_add_experiment_candidate_lane",
        },
    )

    assert drift["state"] == "behind"
    assert drift["missing_count"] == 1
    names = [m["name"] for m in drift["missing_here"]]
    assert names == ["20260912122759_add_experiment_candidate_lane"], (
        "the absent migration must be NAMED — an operator cannot merge what "
        "the tool will not name"
    )


def test_checkout_holding_everything_on_the_branch_reads_current(migrations_dir):
    _mkdir_migration(migrations_dir, "20260911220746_hook_events_session_start_type")
    drift = _drift_with_branch(
        migrations_dir,
        {"20260911220746": "20260911220746_hook_events_session_start_type"},
    )
    assert drift["state"] == "current"
    assert drift["missing_count"] == 0


def test_unreadable_ref_is_unmeasurable_and_never_current(migrations_dir):
    """A shallow clone with no remote must not report a clean checkout."""
    drift = _drift_with_branch(migrations_dir, None)
    assert drift["state"] == "unmeasurable"
    assert drift["state"] != "current"
    assert drift["missing_here"] is None
    assert drift["reason"]


def test_present_but_unrunnable_migration_is_not_reported_behind(migrations_dir):
    """A directory with no up.sql/up.py is PRESENT — a different defect.

    `discover_migrations()` silently drops such a directory (17 exist), so
    comparing the branch against the RUNNABLE set would diagnose "your checkout
    is behind" for a migration sitting right there. The two need different
    fixes, so the comparison is against directory names.
    """
    _mkdir_migration(migrations_dir, "20260911220746_no_up_file", runnable=False)
    drift = _drift_with_branch(
        migrations_dir, {"20260911220746": "20260911220746_no_up_file"}
    )
    assert drift["state"] == "current", (
        "a present-but-unrunnable migration must not masquerade as a behind "
        "checkout"
    )


def test_flat_sql_migration_on_disk_counts_as_present(migrations_dir):
    """The legacy flat `<version>_name.sql` shape is a migration too."""
    (migrations_dir / "173_legacy_thing.sql").write_text("SELECT 1;\n", encoding="utf-8")
    drift = _drift_with_branch(migrations_dir, {"173": "173_legacy_thing.sql"})
    assert drift["state"] == "current"


def _drift_with_branch(migrations_dir: Path, on_branch):
    """Call the derivation with `branch_migrations` returning *on_branch*."""
    import tools.db.migration_drift as drift_mod

    original = drift_mod.branch_migrations
    drift_mod.branch_migrations = lambda ref, root: on_branch
    try:
        return migrate_cli.checkout_drift(_Runner(migrations_dir))
    finally:
        drift_mod.branch_migrations = original


# ── the rendering ───────────────────────────────────────────────────────────

def test_behind_renders_a_warning_that_names_the_absent_migration():
    lines = migrate_cli._format_checkout_drift({
        "state": "behind",
        "ref": "origin/main",
        "missing_count": 1,
        "missing_here": [{"version": "20260912122759",
                          "name": "20260912122759_add_experiment_candidate_lane"}],
    })
    blob = "\n".join(lines)
    assert "BEHIND" in blob
    assert "20260912122759_add_experiment_candidate_lane" in blob
    assert "migration_drift.py" in blob, "point the operator at the cross-check"


def test_unmeasurable_renders_and_says_it_is_not_current():
    lines = migrate_cli._format_checkout_drift({
        "state": "unmeasurable", "ref": "origin/main",
        "reason": "migrations on origin/main could not be read",
        "missing_here": None, "missing_count": None,
    })
    blob = "\n".join(lines)
    assert blob, "an unmeasurable check must not render as silence"
    assert "NOT current" in blob


def test_current_renders_nothing():
    assert migrate_cli._format_checkout_drift(
        {"state": "current", "ref": "origin/main",
         "missing_count": 0, "missing_here": []}
    ) == []


# ── the consumer: PRF-001 must not certify a behind checkout ────────────────

def _prf001_with_status_json(monkeypatch, payload: str):
    import json as _json

    import tools.testing.production_audit as audit

    monkeypatch.setattr(audit, "_run_subprocess",
                        lambda cmd, timeout=120: (0, payload, ""))
    # The check returns `skip` when migrate.py is absent; it is present in this
    # checkout, so the real path runs.
    assert (audit.PROJECT_ROOT / "tools" / "db" / "migrate.py").exists()
    result = audit.check_migration_status()
    _json.loads(payload)  # payload must be the shape migrate.py --status emits
    return result


_BASE_STATUS = {
    "engine": "postgresql",
    "db_path": "data/icdev.db",
    "has_migrations_table": True,
    "current_version": "20260911220746",
    "applied_count": 446,
    "pending_count": 0,
    "applied": [],
    "pending": [],
    "issues": [],
}


def test_prf001_does_not_pass_when_the_checkout_is_behind(monkeypatch):
    """The regression this card measured: pending 0 + behind branch != pass."""
    import json as _json

    payload = dict(_BASE_STATUS)
    payload["checkout_drift"] = {
        "state": "behind", "ref": "origin/main", "missing_count": 1,
        "missing_here": [{"version": "20260912122759",
                          "name": "20260912122759_add_experiment_candidate_lane"}],
    }
    check = _prf001_with_status_json(monkeypatch, _json.dumps(payload))

    assert check.check_id == "PRF-001"
    assert check.status != "pass", (
        "a checkout that cannot see a merged migration must never be certified "
        "as 'All migrations applied'"
    )
    assert check.status == "warn"
    assert "BEHIND" in check.message
    assert "20260912122759_add_experiment_candidate_lane" in check.message


def test_prf001_does_not_pass_when_drift_is_unmeasurable(monkeypatch):
    import json as _json

    payload = dict(_BASE_STATUS)
    payload["checkout_drift"] = {
        "state": "unmeasurable", "ref": "origin/main",
        "reason": "migrations on origin/main could not be read",
        "missing_here": None, "missing_count": None,
    }
    check = _prf001_with_status_json(monkeypatch, _json.dumps(payload))
    assert check.status == "warn"
    assert "unmeasurable" in check.message


def test_prf001_still_passes_on_a_genuinely_current_deployment(monkeypatch):
    """The fix must not turn every clean deployment into a warning."""
    import json as _json

    payload = dict(_BASE_STATUS)
    payload["checkout_drift"] = {
        "state": "current", "ref": "origin/main",
        "missing_count": 0, "missing_here": [],
    }
    check = _prf001_with_status_json(monkeypatch, _json.dumps(payload))
    assert check.status == "pass"
    assert check.message == "All migrations applied"


def test_prf001_still_warns_on_real_filesystem_pending(monkeypatch):
    import json as _json

    payload = dict(_BASE_STATUS)
    payload["pending_count"] = 2
    payload["checkout_drift"] = {
        "state": "current", "ref": "origin/main",
        "missing_count": 0, "missing_here": [],
    }
    check = _prf001_with_status_json(monkeypatch, _json.dumps(payload))
    assert check.status == "warn"
    assert "2 pending migrations" == check.message


def test_prf001_tolerates_a_status_payload_with_no_drift_key(monkeypatch):
    """An older migrate.py, or a tenant path, emits no `checkout_drift`.

    Absence must fall back to the previous behaviour rather than raise — this
    check runs inside a production audit and a KeyError there loses every other
    finding in the category.
    """
    import json as _json

    check = _prf001_with_status_json(monkeypatch, _json.dumps(_BASE_STATUS))
    assert check.status == "pass"


# ── the live seam, exercised end to end ─────────────────────────────────────

def test_status_json_carries_checkout_drift_for_this_real_checkout():
    """`--status` must always emit the key, whatever this checkout's state."""
    from tools.db.migration_runner import MigrationRunner

    runner = MigrationRunner(
        db_path=Path(os.environ.get("ICDEV_DB_PATH", ":memory:")),
        engine="sqlite",
    )
    drift = migrate_cli.checkout_drift(runner)
    assert drift["state"] in {"behind", "current", "unmeasurable"}
    assert "ref" in drift
