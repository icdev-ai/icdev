# CUI // SP-CTI
"""penta-gd-07 — import-smoke across the full GameDay / TTX surface.

Walks EVERY ``.py`` module under the four GameDay package trees and asserts it
imports cleanly. This is the regression class that would have caught the
historical "missing engine module" bug (a blueprint / agent importing a
never-built module -> ImportError at startup, e.g. the deleted ``pack.py`` that
imported a nonexistent generic engine). Parametrized by *discovered* files so
any module added under these trees is covered automatically — no test edit
needed.

Trees:
  * tools/gameday/                  (AI League engine)
  * tools/ttx/  (incl. scenarios/)  (tabletop-exercise engine)
  * tools/ai_game_engine/           (ontology + scenario registry)
  * apps/ai_gameday/                (Flask blueprint + auth + db)

Four modules under ``tools/ttx/scenarios/`` (``sim_cipher_forge``,
``sim_forge_ascent``, ``sim_hunt_the_fleet``, ``sim_meridian``) are *executable
demo scripts*. penta-fix-01 wrapped their simulation body in ``main()`` behind an
``if __name__ == "__main__"`` guard, so they no longer self-run on import. They
are still compile-checked (syntax) rather than import-smoked to avoid pulling
their heavy demo dependency graph into the smoke suite, and the presence of the
guard is now asserted as a real (non-xfail) test.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TREES = ("tools/gameday", "tools/ttx", "tools/ai_game_engine", "apps/ai_gameday")

# Executable demo scripts that self-run on import (no __main__ guard).
_SELF_EXECUTING = {
    "tools.ttx.scenarios.sim_cipher_forge",
    "tools.ttx.scenarios.sim_forge_ascent",
    "tools.ttx.scenarios.sim_hunt_the_fleet",
    "tools.ttx.scenarios.sim_meridian",
}


def _discover() -> list[str]:
    mods: list[str] = []
    for tree in _TREES:
        base = REPO_ROOT / tree
        for p in sorted(base.rglob("*.py")):
            mods.append(".".join(p.relative_to(REPO_ROOT).with_suffix("").parts))
    return mods


_ALL_MODS = _discover()
_IMPORTABLE = [m for m in _ALL_MODS if m not in _SELF_EXECUTING]
_SCRIPTS = [m for m in _ALL_MODS if m in _SELF_EXECUTING]


def _mod_path(mod: str) -> pathlib.Path:
    return REPO_ROOT / (mod.replace(".", "/") + ".py")


def test_surface_discovered():
    """Guard against a silently-empty walk: all four trees must contribute
    modules and every known executable script must be present."""
    assert len(_ALL_MODS) >= 30, f"only discovered {len(_ALL_MODS)} modules"
    for tree in _TREES:
        prefix = tree.replace("/", ".")
        assert any(m == prefix or m.startswith(prefix + ".") for m in _ALL_MODS), (
            f"no modules discovered under {tree}"
        )
    assert set(_SCRIPTS) == _SELF_EXECUTING, (
        f"executable-script set drifted: {sorted(_SCRIPTS)}"
    )


@pytest.mark.parametrize("mod", _IMPORTABLE)
def test_module_imports(mod, monkeypatch):
    """Every library module under the four trees imports without error."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    importlib.import_module(mod)


@pytest.mark.parametrize("mod", _SCRIPTS)
def test_executable_script_compiles(mod):
    """Self-running demo scripts can't be import-smoked (importing runs the
    sim), but they must at least be syntactically valid."""
    src = _mod_path(mod).read_text(encoding="utf-8")
    ast.parse(src)  # raises SyntaxError on a broken script


@pytest.mark.parametrize("mod", _SCRIPTS)
def test_executable_script_has_main_guard(mod):
    """penta-fix-01: the sim_* demo scripts now wrap their simulation in a
    ``main()`` behind an ``if __name__ == "__main__"`` guard, so importing them
    no longer runs a DB-backed sim. Previously an xfail (no guard)."""
    src = _mod_path(mod).read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in src or "__name__=='__main__'" in src
