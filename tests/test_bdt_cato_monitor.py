# CUI // SP-CTI
"""bdt-mon-1: cato_monitor seed queries must execute against the REAL compliance
tables — not a phantom ``controls`` table.

Root cause fixed here: ``_run_iqe_query`` instantiated a *fresh* ``Executor()``
whose registry held no ``compliance.*`` collections and never imported the
compliance adapter. So ``compliance.controls`` fell through to the executor's
bare-table fallback, which strips the namespace and runs ``SELECT * FROM
controls`` — a relation that does not exist (the pre-existing "missing controls
table" failure documented in tools/manifest/bdc-cato-twin.md "Known gaps").

The fix imports ``tools.iqe.adapters.compliance`` and executes via the
module-level *default* Executor, repointing the seed queries at the real tables:
``project_controls`` LEFT JOIN ``compliance_controls`` (controls),
``pi_compliance_tracking`` (snapshots), ``poam_items`` (violations).

Honesty rules (this canvas was purged of fake data):
  * an empty compliance table is a healthy 0-row no-op (no error);
  * a genuinely missing/impossible table degrades EXPLICITLY — the error is
    surfaced in the result, never a silent success.

Hermetic: an in-memory SQLite fixture with exactly the columns the adapters
read. The adapters use no bound placeholders, so raw sqlite3 needs no ``%s``→``?``
translation.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

_SEED_DIR = Path(__file__).resolve().parents[1] / "context" / "iqe" / "queries" / "compliance"


@pytest.fixture
def reflex():
    return importlib.import_module("tools.genesis.reflexes.cato_monitor")


def _make_conn(*, with_tables: bool, controls=None) -> sqlite3.Connection:
    """In-memory SQLite carrying the columns the compliance IQE adapters read.

    ``with_tables=False`` yields a bare connection (no compliance tables) to
    exercise the missing-table degradation path.
    """
    conn = sqlite3.connect(":memory:")
    if not with_tables:
        return conn
    conn.executescript(
        """
        CREATE TABLE compliance_controls (
            id TEXT PRIMARY KEY, family TEXT, title TEXT, impact_level TEXT
        );
        CREATE TABLE project_controls (
            id TEXT PRIMARY KEY, project_id TEXT, control_id TEXT,
            implementation_status TEXT, implementation_description TEXT,
            responsible_role TEXT
        );
        CREATE TABLE pi_compliance_tracking (
            id TEXT PRIMARY KEY, project_id TEXT, pi_number INTEGER,
            pi_start_date TEXT, pi_end_date TEXT,
            compliance_score_start REAL, compliance_score_end REAL,
            controls_implemented INTEGER, controls_remaining INTEGER,
            poam_items_closed INTEGER, poam_items_opened INTEGER,
            findings_remediated INTEGER, notes TEXT, created_at TEXT
        );
        CREATE TABLE poam_items (
            id TEXT PRIMARY KEY, project_id TEXT, weakness_id TEXT,
            weakness_description TEXT, severity TEXT, source TEXT,
            control_id TEXT, status TEXT, corrective_action TEXT,
            milestone_date TEXT, completion_date TEXT,
            responsible_party TEXT, created_at TEXT
        );
        """
    )
    for c in controls or []:
        conn.execute(
            "INSERT INTO compliance_controls (id, family, title, impact_level) "
            "VALUES (?,?,?,?)",
            (c["control_id"], c["family"], c.get("title", "t"), c.get("impact_level", "MODERATE")),
        )
        conn.execute(
            "INSERT INTO project_controls (id, project_id, control_id, "
            "implementation_status, implementation_description, responsible_role) "
            "VALUES (?,?,?,?,?,?)",
            (
                c["control_id"],
                c["project_id"],
                c["control_id"],
                c["implementation_status"],
                c.get("implementation_description", "desc"),
                c.get("responsible_role", "ISSO"),
            ),
        )
    conn.commit()
    return conn


def test_seed_query_executes_against_real_tables(reflex):
    """A real seed file resolves compliance.controls to project_controls and matches rows."""
    conn = _make_conn(
        with_tables=True,
        controls=[
            {"control_id": "AC-2", "project_id": "sparkpilot", "family": "AC",
             "implementation_status": "not_implemented"},
            {"control_id": "SC-7", "project_id": "sparkpilot", "family": "SC",
             "implementation_status": "implemented"},
        ],
    )
    qf = _SEED_DIR / "fedramp-moderate" / "ac2_status.iqe"
    res = reflex._run_iqe_query(qf, conn)

    assert res["error"] is None, f"seed query must not error: {res['error']}"
    assert len(res["rows"]) == 1, res["rows"]
    assert res["rows"][0]["control_id"] == "AC-2"


def test_empty_tables_are_healthy_noop(reflex):
    """Legitimately-empty compliance tables return 0 rows with NO error."""
    conn = _make_conn(with_tables=True)
    qf = _SEED_DIR / "fedramp-moderate" / "ac2_status.iqe"
    res = reflex._run_iqe_query(qf, conn)

    assert res["error"] is None
    assert res["rows"] == []


def test_missing_table_degrades_explicitly(reflex):
    """A missing table surfaces an explicit error (never a silent success) and
    the error names the REAL table (project_controls), proving the phantom
    `controls` table is no longer queried."""
    conn = _make_conn(with_tables=False)
    qf = _SEED_DIR / "fedramp-moderate" / "ac2_status.iqe"
    res = reflex._run_iqe_query(qf, conn)

    assert res["rows"] == []
    assert res["error"], "a failed query must report its error, not swallow it"
    assert "project_controls" in res["error"], (
        f"error must reference the real table, not the phantom 'controls': {res['error']}"
    )


def test_run_end_to_end_no_missing_table_errors(reflex, monkeypatch):
    """Full run() over the real seed set against a fixture DB: zero query
    failures, and metric_value equals the real violation count (truthful)."""
    conn = _make_conn(
        with_tables=True,
        controls=[
            {"control_id": "AC-2", "project_id": "sparkpilot", "family": "AC",
             "implementation_status": "not_implemented"},
        ],
    )
    from tools.db import storage as _stor
    monkeypatch.setattr(_stor, "get_connection", lambda: conn)
    monkeypatch.setattr(reflex, "_collect_iqe_files", lambda: sorted(_SEED_DIR.rglob("*.iqe")))
    monkeypatch.setattr(
        reflex, "_call_poam_generator",
        lambda pid: {"success": True, "path": "poam.md"},
    )

    result = reflex.run({"project_id": "sparkpilot"}, None)

    assert result["success"] is True
    assert result["scanned"] == len(sorted(_SEED_DIR.rglob("*.iqe")))
    assert result["details"]["queries_failed"] == 0, result["details"]["query_results"]
    for qr in result["details"]["query_results"]:
        assert qr["error"] is None, f"{qr['name']}: {qr['error']}"
    # Truthful metric: metric_value mirrors the counted violations exactly.
    assert isinstance(result["metric_value"], float)
    assert result["violations"] == result["metric_value"]
    # At least the AC-2 gap matched (ac2_status + controls_failed_30d seed queries).
    assert result["violations"] >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
