#!/usr/bin/env python3
"""freshness_guardian: the packaged copy was a stale, broken implementation.

Unlike `quality_monitor` — which had diverged in BOTH directions, so a blind
copy would have lost something — this file's `icdev/` twin was simply an older
implementation that the canonical copy had deliberately replaced. Every
difference was a regression against a documented rule, so `tools/` is
authoritative here and the reconcile is a straight copy.

The four defects in the packaged copy, each pinned below:

1. `last_modified` fell back to `profiled_at`. `data_profiler` sets
   `profiled_at` to `datetime.now()` on every run, so EVERY table looked fresh
   and the guardian could never report anything stale. The canonical
   `_derive_last_modified` uses an honest signal — an explicit
   `last_modified`/`updated_at`, else the newest value across the table's
   datetime columns — and reports "unknown" rather than "fresh" when no real
   timestamp exists.
2. `_sqlite_last_modified` used the whole-DB **file mtime** as a proxy for one
   table's freshness. SQLite-only, and wrong even there.
3. It persisted via `get_connection()`. `dd_freshness_runs` is a canvas table
   with no `classification`/`tenant_id` columns, so the RLS predicate raises
   `UndefinedColumn` on every query — the exact failure CLAUDE.md documents,
   requiring `get_canvas_connection()`.
4. Bare `?` placeholders, a duplicated except-branch identical to the branch it
   was retrying (so the retry could only fail the same way), and
   `except Exception: pass` swallowing the persistence failure entirely.

Not a live outage: both reflex copies import `tools.data_canvas.*`, so the
hourly Genesis sweep got the good implementation. But this is what a wheel
install ships, and `icdev.tools.data_canvas.freshness_guardian` is importable.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CANONICAL = _ROOT / "tools" / "data_canvas" / "freshness_guardian.py"
_MIRROR = _ROOT / "icdev" / "tools" / "data_canvas" / "freshness_guardian.py"
_BOTH = [_CANONICAL, _MIRROR]
_IDS = ["canonical", "mirror"]


def _src(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_both_copies_exist():
    assert _CANONICAL.is_file() and _MIRROR.is_file()


def test_files_are_byte_identical():
    assert _CANONICAL.read_bytes() == _MIRROR.read_bytes(), (
        "freshness_guardian copies differ — reconcile toward tools/, which "
        "carries the backend-agnostic implementation."
    )


@pytest.mark.parametrize("path", _BOTH, ids=_IDS)
def test_never_falls_back_to_profiled_at(path: pathlib.Path):
    """The defect that made the guardian useless.

    data_profiler sets profiled_at = now() on every run, so using it as a
    last-modified signal reports every table as fresh, always.
    """
    src = _src(path)
    assert 'tbl.get("last_modified") or tbl.get("profiled_at")' not in src, (
        f"{path.name} falls back to profiled_at — every table would look fresh"
    )


@pytest.mark.parametrize("path", _BOTH, ids=_IDS)
def test_derives_last_modified_honestly(path: pathlib.Path):
    src = _src(path)
    assert "def _derive_last_modified" in src
    assert "_sqlite_last_modified" not in src, (
        f"{path.name} still uses the whole-DB file-mtime proxy, which is "
        "SQLite-only and wrong even there"
    )


@pytest.mark.parametrize("path", _BOTH, ids=_IDS)
def test_uses_canvas_connection(path: pathlib.Path):
    """dd_freshness_runs is a canvas table — get_connection() raises on PG.

    CLAUDE.md: canvas tables carry no classification/tenant_id, so the global
    RLS predicate produces UndefinedColumn on every query.
    """
    src = _src(path)
    assert "get_canvas_connection" in src, f"{path.name} must use get_canvas_connection()"
    assert "sqlite3.connect" not in src, (
        f"{path.name} keeps a raw sqlite3 fallback that bypasses the storage layer"
    )


@pytest.mark.parametrize("path", _BOTH, ids=_IDS)
def test_no_sqlite_style_placeholders(path: pathlib.Path):
    src = _src(path)
    offenders = re.findall(r"VALUES\s*\([^)]*\?", src)
    assert not offenders, f"{path.name} uses SQLite-style placeholders: {offenders[:2]}"


@pytest.mark.parametrize("path", _BOTH, ids=_IDS)
def test_persistence_failure_is_logged_not_swallowed(path: pathlib.Path):
    """`except Exception: pass` turns a lost run record into silence.

    Scoped to the persistence path deliberately. The module has a legitimate
    bare `except: pass` in config loading — falling back to default thresholds
    when the YAML is missing is correct and should stay silent. Asserting "no
    bare pass anywhere" would have flagged that, which is how an over-broad
    test trains people to ignore it.
    """
    src = _src(path)
    anchor = src.find("dd_freshness_runs")
    assert anchor != -1, f"{path.name}: persistence block not found"
    tail = src[anchor:anchor + 1200]
    assert "logger.warning" in tail, (
        f"{path.name} does not log a failed dd_freshness_runs write"
    )
    assert not re.search(r"except Exception:\s*\n\s*pass\s*\n", tail), (
        f"{path.name} swallows the persistence failure without logging"
    )


def test_importable_both_ways():
    import icdev.tools.data_canvas.freshness_guardian as mirror
    import tools.data_canvas.freshness_guardian as canonical

    for mod in (canonical, mirror):
        assert hasattr(mod, "_derive_last_modified")
