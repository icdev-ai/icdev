# CUI // SP-CTI
"""The drift reflex must report regressions and stay silent about known debt.

Every assertion here exists because the opposite behaviour would get the reflex
switched off:

  * report only pass -> fail. ~1,823 ungated modules exist and an unknown number
    are already red; re-reporting those every 6h buries the one line that matters.
  * seed silently on a database with no baseline. A fresh worktree or ephemeral
    CI database has no "before", so every file looks like a new regression — a
    reflex whose first act is to file 1,823 cards does not survive the hour.
  * deterministic card ids. A uuid defeats INSERT-OR-IGNORE and refiles the same
    finding every cycle.
  * always return a 'success' key. A reflex that omits it is scored a failure
    forever and circuit-breaks itself.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

drift = importlib.import_module("tools.genesis.reflexes.ungated_test_drift")

_SCHEMA = """
CREATE TABLE ungated_test_baseline (
    path           TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    first_seen     TEXT,
    last_checked   TEXT,
    last_detail    TEXT,
    classification TEXT DEFAULT 'CUI'
);
"""


class _Conn:
    """Minimal stand-in honouring the %s style the reflex writes."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        sql = sql.replace("INSERT OR IGNORE", "INSERT OR IGNORE").replace("%s", "?")
        return self._raw.execute(sql, params)

    def commit(self):
        self._raw.commit()

    def close(self):
        pass


@pytest.fixture()
def db(tmp_path, monkeypatch):
    raw = sqlite3.connect(str(tmp_path / "drift.db"))
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA)
    raw.commit()

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn(raw))
    monkeypatch.setattr(drift, "_table_exists", lambda conn: True)
    return raw


@pytest.fixture()
def two_files(monkeypatch):
    monkeypatch.setattr(drift, "_ungated_files",
                        lambda: ["tests/test_alpha.py", "tests/test_beta.py"])


def _verdicts(monkeypatch, mapping):
    monkeypatch.setattr(
        drift, "_run_alone",
        lambda rel: {"status": mapping[rel], "detail": f"{mapping[rel]} detail",
                     "seconds": 0.1},
    )


def _seed(raw, path, status):
    raw.execute(
        "INSERT INTO ungated_test_baseline (path, status, first_seen, last_checked)"
        " VALUES (?,?,?,?)", (path, status, "2026-01-01", "2026-01-01"))
    raw.commit()


# --- the contract ------------------------------------------------------------


def test_returns_a_success_key(db, two_files, monkeypatch):
    """Its absence is scored a failure forever and self-circuit-breaks."""
    _verdicts(monkeypatch, {"tests/test_alpha.py": "pass", "tests/test_beta.py": "pass"})
    out = drift.run({}, None)
    assert "success" in out and out["success"] is True


def test_first_run_seeds_and_reports_nothing(db, two_files, monkeypatch):
    """No baseline means no 'before' — every file would look newly broken."""
    _verdicts(monkeypatch, {"tests/test_alpha.py": "fail", "tests/test_beta.py": "fail"})
    out = drift.run({}, None)

    assert out["baseline_seeded_this_run"] is True
    assert out["seeded"] == 2
    assert out["regressions"] == [], (
        "a fresh database must not turn every already-failing file into a card"
    )
    assert out["cards_filed"] == 0


def test_pass_to_fail_is_reported(db, two_files, monkeypatch):
    _seed(db, "tests/test_alpha.py", "pass")
    _seed(db, "tests/test_beta.py", "pass")
    _verdicts(monkeypatch, {"tests/test_alpha.py": "fail", "tests/test_beta.py": "pass"})

    out = drift.run({"dry_run": True}, None)
    assert [r["path"] for r in out["regressions"]] == ["tests/test_alpha.py"]


def test_already_failing_is_not_reported(db, two_files, monkeypatch):
    """Known debt. Re-reporting it every cycle is how a reflex earns suppression."""
    _seed(db, "tests/test_alpha.py", "fail")
    _seed(db, "tests/test_beta.py", "pass")
    _verdicts(monkeypatch, {"tests/test_alpha.py": "fail", "tests/test_beta.py": "pass"})

    out = drift.run({"dry_run": True}, None)
    assert out["regressions"] == []


def test_recovery_is_recorded_not_carded(db, two_files, monkeypatch):
    _seed(db, "tests/test_alpha.py", "fail")
    _seed(db, "tests/test_beta.py", "pass")
    _verdicts(monkeypatch, {"tests/test_alpha.py": "pass", "tests/test_beta.py": "pass"})

    out = drift.run({"dry_run": True}, None)
    assert [r["path"] for r in out["recoveries"]] == ["tests/test_alpha.py"]
    assert out["regressions"] == []


def test_baseline_is_updated_so_a_regression_reports_once(db, two_files, monkeypatch):
    _seed(db, "tests/test_alpha.py", "pass")
    _seed(db, "tests/test_beta.py", "pass")
    _verdicts(monkeypatch, {"tests/test_alpha.py": "fail", "tests/test_beta.py": "pass"})

    first = drift.run({"dry_run": True}, None)
    second = drift.run({"dry_run": True}, None)

    assert len(first["regressions"]) == 1
    assert second["regressions"] == [], (
        "the same breakage must not be re-reported every cycle — the baseline "
        "has to absorb it after the first card"
    )


def test_unrunnable_file_does_not_overwrite_the_verdict(db, two_files, monkeypatch):
    """A spawn failure is not evidence the tests fail."""
    _seed(db, "tests/test_alpha.py", "pass")
    _seed(db, "tests/test_beta.py", "pass")
    monkeypatch.setattr(drift, "_run_alone",
                        lambda rel: {"status": "unknown", "detail": "spawn failed",
                                     "seconds": 0.0})
    out = drift.run({"dry_run": True}, None)

    assert out["regressions"] == []
    row = db.execute(
        "SELECT status FROM ungated_test_baseline WHERE path='tests/test_alpha.py'"
    ).fetchone()
    assert row["status"] == "pass", "an unrunnable file must keep its last real verdict"


def test_missing_table_is_skipped_not_an_error(db, two_files, monkeypatch):
    """Before the migration runs, say so — do not crash the daemon."""
    monkeypatch.setattr(drift, "_table_exists", lambda conn: False)
    out = drift.run({}, None)
    assert out["status"] == "skipped"
    assert out["success"] is True


# --- card identity -----------------------------------------------------------


def test_card_id_is_deterministic():
    a = drift._card_id("tests/test_alpha.py")
    assert a == drift._card_id("tests/test_alpha.py")
    assert a != drift._card_id("tests/test_beta.py")
    assert a.startswith("tsg-drift-")


# --- scope -------------------------------------------------------------------


def test_browser_suites_are_excluded():
    """They fail environmentally without a live dashboard — drift is meaningless."""
    for p in ("tests/e2e/x.py", "tests/e2e_selenium/y.py", "tests/browser/z.py"):
        assert p.startswith(drift.EXCLUDED_PREFIXES)


def test_allowlisted_files_are_not_sampled():
    """CI already gates those; this reflex exists for the ones it does not."""
    files = drift._ungated_files()
    listed = set()
    for name in ("core.txt", "windows.txt"):
        p = drift.BASE_DIR / "args" / "ci_test_files" / name
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    listed.add(line.split("::")[0].replace("\\", "/"))
    assert files, "no ungated files found — the allowlist parse is wrong"
    assert not (set(files) & listed)
