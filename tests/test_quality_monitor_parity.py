#!/usr/bin/env python3
"""quality_monitor: both copies must expose the same API — CUI // SP-CTI.

`tools/finetune/quality_monitor.py` and its `icdev/` twin had diverged in BOTH
directions, which is the worst shape this can take:

  * `icdev/` carried `compare_jobs`, `detect_regression` and the regression
    detection section — roughly 100 lines the canonical copy simply did not
    have. `import tools.finetune.quality_monitor` resolves to the canonical
    file (it is a real module, not a shim), so those names raised AttributeError
    depending only on how the caller spelled the import.
  * `tools/` carried the `%s` placeholder fix. The `icdev/` copy still used
    SQLite-style `?`, which `translate_sql` rewrites while logging
    "bare ? placeholder detected — use %s for psycopg2 directly".
  * `tools/` was ALSO missing `finally: conn.close()`, so the canonical copy
    leaked a connection on every call that raised.

Neither side was simply behind. Reconciling meant taking the complete file and
applying the placeholder fix to it, not copying one over the other.

`tests/test_regression_detector.py` imports via `icdev.tools.*` and so never
noticed. This test compares the two copies directly.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CANONICAL = _ROOT / "tools" / "finetune" / "quality_monitor.py"
_MIRROR = _ROOT / "icdev" / "tools" / "finetune" / "quality_monitor.py"


def _public_api(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    return {
        n.name
        for n in tree.body  # module level only — nested helpers are private detail
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def test_both_copies_exist():
    assert _CANONICAL.is_file() and _MIRROR.is_file()


def test_public_api_is_identical():
    a, b = _public_api(_CANONICAL), _public_api(_MIRROR)
    assert a == b, (
        f"API divergence — only in tools/: {sorted(a - b)}; "
        f"only in icdev/: {sorted(b - a)}. Which one a caller gets depends "
        "purely on whether they wrote `tools.` or `icdev.tools.`"
    )


@pytest.mark.parametrize("fn", ["compare_jobs", "detect_regression"])
def test_the_previously_missing_functions_are_importable_both_ways(fn):
    """These existed only in icdev/. Both spellings must now work."""
    import icdev.tools.finetune.quality_monitor as mirror
    import tools.finetune.quality_monitor as canonical

    assert hasattr(canonical, fn), f"tools.finetune.quality_monitor lacks {fn}"
    assert hasattr(mirror, fn), f"icdev.tools.finetune.quality_monitor lacks {fn}"


@pytest.mark.parametrize("path", [_CANONICAL, _MIRROR], ids=["canonical", "mirror"])
def test_no_sqlite_style_placeholders(path: pathlib.Path):
    """PostgreSQL is primary; `?` only survives via a translation that warns.

    Per CLAUDE.md, runtime SQL is authored for PostgreSQL — `translate_sql` is
    an init-only fallback and must never be load-bearing.
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    offenders = re.findall(r"(?:VALUES\s*\([^)]*\?|=\s*\?[,\s)])", src)
    assert not offenders, f"{path.name} still uses SQLite-style placeholders: {offenders[:3]}"


@pytest.mark.parametrize("path", [_CANONICAL, _MIRROR], ids=["canonical", "mirror"])
def test_connection_is_released_on_failure(path: pathlib.Path):
    """The canonical copy was missing the finally-block entirely."""
    src = path.read_text(encoding="utf-8", errors="replace")
    assert "finally:" in src and "conn.close()" in src, (
        f"{path.name} does not release its connection on the error path"
    )


def test_files_are_byte_identical():
    """The strongest form of the guarantee, and the cheapest to check."""
    assert _CANONICAL.read_bytes() == _MIRROR.read_bytes(), (
        "quality_monitor copies differ. Reconcile deliberately — this module "
        "has previously diverged in BOTH directions, so a blind copy either way "
        "loses something."
    )
