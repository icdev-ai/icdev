# CUI // SP-CTI
"""`_repo_importers` must stay correct while no longer costing a pytest session.

tsh-kill-01: a full ``pytest tests/`` died at 17%. `_repo_importers` ran
``ast.parse`` + ``ast.walk`` over every one of ~3,750 files under ``tools/`` —
~13s warm, ~49s cold — which exceeds the 30s per-test timeout.

The reported root cause was a "repo-wide realpath walk". That is not what it
was: `path.resolve()` across all 3,759 files measures 0.26s of the 49s, and for
the one module this actually runs on (`tools.rag.adaptive_router`)
`_module_to_path` returns None, so the resolve branch is never even reached.
The cost was the AST work. The realpath call is still hoisted out of the loop
here — it was genuinely loop-invariant — but that is a tidy-up, not the fix.

The fix is a substring prefilter on the module's last component. It is only
sound because every import form that can contribute *module* to `_imports_of`
contains that component literally; these tests pin that property, since a form
that violated it would silently make the harness report a wired toggle as dead.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.rag import toggle_harness as th  # noqa: E402


def _brute_force(module: str, base: Path) -> frozenset:
    """The pre-fix implementation, kept as the reference answer."""
    found = set()
    for path in (base / "tools").rglob("*.py"):
        if module in th._imports_of(path):
            found.add(".".join(path.relative_to(base).with_suffix("").parts))
    return frozenset(found)


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A miniature tools/ tree exercising every import form that counts."""
    pkg = tmp_path / "tools" / "rag"
    pkg.mkdir(parents=True)
    (tmp_path / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "adaptive_router.py").write_text("VALUE = 1\n", encoding="utf-8")

    files = {
        # `import tools.a.b`
        "dotted.py": "import tools.rag.adaptive_router\n",
        # `from tools.a.b import x`
        "from_module.py": "from tools.rag.adaptive_router import VALUE\n",
        # `from tools.a import b` — the form with no contiguous dotted name.
        # This is the one a naive full-dotted-path prefilter would miss.
        "from_package.py": "from tools.rag import adaptive_router\n",
        # deferred, inside a function — the reason ast.walk is used at all
        "deferred.py": (
            "def go():\n"
            "    from tools.rag import adaptive_router\n"
            "    return adaptive_router\n"
        ),
        # aliased
        "aliased.py": "import tools.rag.adaptive_router as ar\n",
        # must NOT match: a similarly named module in another package
        "decoy.py": "from tools.other import adaptive_router\n",
        # must NOT match: the bare word appears, but no import of it
        "mentions_only.py": "# adaptive_router is discussed but never imported\nX = 1\n",
        # must NOT match: unrelated
        "unrelated.py": "import os\n",
        # must not crash the scan
        "broken.py": "def (\n",
    }
    for name, src in files.items():
        (pkg / name).write_text(src, encoding="utf-8")

    monkeypatch.setattr(th, "BASE_DIR", tmp_path)
    th._repo_importers.cache_clear()
    yield tmp_path
    th._repo_importers.cache_clear()


TARGET = "tools.rag.adaptive_router"


def test_finds_every_import_form(tree):
    got = th._repo_importers(TARGET)
    for expected in (
        "tools.rag.dotted",
        "tools.rag.from_module",
        "tools.rag.from_package",
        "tools.rag.deferred",
        "tools.rag.aliased",
    ):
        assert expected in got, f"{expected} must be reported as an importer"


def test_from_package_form_survives_the_prefilter(tree):
    """`from tools.rag import adaptive_router` has no contiguous dotted name.

    Prefiltering on the full dotted module would drop it and silently report a
    wired toggle as dead. The leaf-component prefilter must keep it.
    """
    assert "tools.rag.from_package" in th._repo_importers(TARGET)


def test_does_not_match_decoys(tree):
    got = th._repo_importers(TARGET)
    for excluded in ("tools.rag.decoy", "tools.rag.mentions_only", "tools.rag.unrelated"):
        assert excluded not in got, f"{excluded} must not count as an importer"


def test_unparseable_file_does_not_abort_the_scan(tree):
    got = th._repo_importers(TARGET)
    assert "tools.rag.broken" not in got
    assert "tools.rag.dotted" in got, "a syntax error must not truncate the walk"


def test_matches_the_brute_force_reference(tree):
    assert th._repo_importers(TARGET) == _brute_force(TARGET, tree)


def test_unknown_module_returns_empty(tree):
    assert th._repo_importers("tools.rag.does_not_exist") == frozenset()


def test_imports_of_source_matches_imports_of(tree):
    p = tree / "tools" / "rag" / "from_package.py"
    assert th._imports_of_source(p.read_text(encoding="utf-8")) == th._imports_of(p)


def test_imports_of_tolerates_a_missing_file(tmp_path):
    assert th._imports_of(tmp_path / "gone.py") == set()


def test_imports_of_source_tolerates_a_syntax_error():
    assert th._imports_of_source("def (\n") == set()


# --------------------------------------------------------------------------
# The regression itself
# --------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_real_repo_scan_parses_almost_nothing(monkeypatch):
    """The load-bearing assertion, and deliberately not a wall-clock one.

    A timing threshold loose enough not to flake on a slow runner (15s) is also
    loose enough to pass against the unfixed code (~13s warm) — it caught
    nothing. Counting parses instead is machine-independent: pre-fix this parsed
    every file under ``tools/`` (~3,750); post-fix the prefilter admits a
    handful. Anything near the file count means the prefilter is gone and a full
    ``pytest tests/`` will be killed on the per-test timeout again.
    """
    calls = {"n": 0}
    real = th._imports_of_source

    def counting(source):
        calls["n"] += 1
        return real(source)

    monkeypatch.setattr(th, "_imports_of_source", counting)

    total_files = sum(1 for _ in (th.BASE_DIR / "tools").rglob("*.py"))
    th._repo_importers.cache_clear()
    th._repo_importers("tools.rag.adaptive_router")
    th._repo_importers.cache_clear()

    assert total_files > 500, "sanity: expected a large tools/ tree to scan"
    assert calls["n"] < total_files // 10, (
        f"parsed {calls['n']} of {total_files} files — the prefilter in "
        "_repo_importers is not working; this is what killed the suite at 17%"
    )


@pytest.mark.timeout(120)
def test_real_repo_scan_is_fast_enough_to_run_inside_a_test():
    """Secondary guard. The parse-count test above is the real one."""
    th._repo_importers.cache_clear()
    start = time.perf_counter()
    th._repo_importers("tools.rag.adaptive_router")
    elapsed = time.perf_counter() - start
    th._repo_importers.cache_clear()
    assert elapsed < 20.0, f"scan took {elapsed:.1f}s, versus a 30s per-test budget"
