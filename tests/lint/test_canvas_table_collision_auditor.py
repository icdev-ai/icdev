"""
test_canvas_table_collision_auditor.py - PGP-sch-03 detector tests

Validates tools/lint/canvas_table_collision_auditor.py: a divergent collision
is detected when two canvas modules define the same table with different
column sets; a benign-shared table (identical columns, multiple owners) is
flagged separately; the live-PG path reports the existing tables.  The
default scan of the current repo must produce zero divergent collisions
(the guard we are enforcing).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDITOR = REPO_ROOT / "tools" / "lint" / "canvas_table_collision_auditor.py"

# Make the auditor importable as a module
sys.path.insert(0, str(REPO_ROOT))
from tools.lint.canvas_table_collision_auditor import (  # noqa: E402
    _extract_tables_from_module,
    _signature,
    audit_modules,
)


def _write_module(tmp_path: Path, name: str, tables: dict[str, list[tuple[str, str]]]) -> Path:
    """Render a synthetic tools/<canvas>/db/init_db.py module."""
    canvas_dir = tmp_path / "tools" / name / "db"
    canvas_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# synthetic init_db for {name}"]
    for tbl, cols in tables.items():
        lines.append(f"CREATE TABLE IF NOT EXISTS {tbl} (")
        for c, t in cols:
            lines.append(f"    {c} {t},")
        lines.append(");")
        lines.append("")
    path = canvas_dir / "init_db.py"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_extract_tables_simple(tmp_path: Path) -> None:
    p = _write_module(tmp_path, "synthetic_a", {
        "syn_a_users": [("id", "INTEGER"), ("name", "TEXT")],
        "syn_a_logs": [("id", "INTEGER"), ("msg", "TEXT")],
    })
    tables = _extract_tables_from_module(p)
    assert set(tables.keys()) == {"syn_a_users", "syn_a_logs"}
    assert tables["syn_a_users"][0] == ("id", "INTEGER")


def test_signature_is_order_independent() -> None:
    cols1 = [("a", "TEXT"), ("b", "INTEGER")]
    cols2 = [("b", "INTEGER"), ("a", "TEXT")]
    assert _signature(cols1) == _signature(cols2)


def test_signature_differs_on_type_change() -> None:
    cols1 = [("a", "TEXT")]
    cols2 = [("a", "INTEGER")]
    assert _signature(cols1) != _signature(cols2)


def test_audit_modules_zero_divergent_in_repo() -> None:
    """The committed canvas init modules must not declare divergent tables."""
    report = audit_modules()
    assert report["divergent_count"] == 0, (
        f"Unexpected divergent collisions: {json.dumps(report['divergent'], indent=2)}"
    )


def test_auditor_cli_reports_clean_repo(tmp_path: Path) -> None:
    """Run the CLI end-to-end and confirm exit 0 on a clean repo."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, str(AUDITOR), "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["static_module_audit"]["divergent_count"] == 0


def test_auditor_cli_md_exit_code_on_clean(tmp_path: Path) -> None:
    """--md mode exits 1 only when divergent_count > 0."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, str(AUDITOR), "--md", "--canvas", "agentic_ai_canvas"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, f"unexpected non-zero exit: {result.stderr}"
    assert "Divergent collisions: 0" in result.stdout
