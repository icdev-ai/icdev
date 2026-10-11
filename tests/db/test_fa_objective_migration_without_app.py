# CUI // SP-CTI
"""Post-snapshot migrations on a PACKAGE install, where child-app code is absent (omx-setup-02).

The Arch Package Smoke job loaded the PG snapshot from the wheel, then failed
`bootstrap_pg` on two migrations that import code no wheel ships:
20260803005919 (apps/forge_academy -- the column must still be added; the
objectives stay NULL, as for content that states none) and 20260912122759
(tools.autoresearch, excluded from the wheel -- a stated skip).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MIGRATION = "20260803005919_fa_mission_learning_objective"
LANE_MIGRATION = "20260912122759_add_experiment_candidate_lane"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"mig_{MIGRATION}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "pkg.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE fa_missions (id INTEGER PRIMARY KEY, slug TEXT)")
    conn.execute("INSERT INTO fa_missions (slug) VALUES ('first-mission')")
    conn.commit()
    return conn


def test_without_the_child_app_the_column_is_added_and_nothing_raises(tmp_path, monkeypatch):
    mod = _load(REPO / "tools" / "db" / "migrations" / MIGRATION / "up.py")
    monkeypatch.setattr(mod, "_is_pg", lambda: False)

    def _no_app():
        raise ModuleNotFoundError("No module named 'forge_academy'")
    monkeypatch.setattr(mod, "_content_loader", _no_app)
    conn = _db(tmp_path)

    mod.up(conn)
    mod.up(conn)  # idempotent

    cols = [r[1] for r in conn.execute("PRAGMA table_info(fa_missions)").fetchall()]
    assert "learning_objective" in cols
    assert conn.execute("SELECT learning_objective FROM fa_missions").fetchone()[0] is None


def test_lane_migration_is_a_stated_skip_without_autoresearch(tmp_path, monkeypatch):
    """20260912122759 imported tools.autoresearch, which the wheel EXCLUDES -- the next failure."""
    mod = _load(REPO / "tools" / "db" / "migrations" / LANE_MIGRATION / "up.py")
    monkeypatch.setitem(sys.modules, "tools.autoresearch.real_mutation", None)  # import -> ImportError
    conn = _db(tmp_path)
    assert mod.up(conn) == {"column_added": False, "reason": "autoresearch_not_installed"}


def test_the_packaged_mirrors_are_the_same_files():
    """The wheel runs the icdev/ copies; they must carry the same fix."""
    for name in (MIGRATION, LANE_MIGRATION):
        src = (REPO / "tools" / "db" / "migrations" / name / "up.py").read_bytes()
        assert (REPO / "icdev" / "tools" / "db" / "migrations" / name / "up.py").read_bytes() == src, name
