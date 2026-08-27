# CUI // SP-CTI
"""xit-decl-03 — the self-root census.

A module that computes the REPOSITORY ROOT from its own location is a
hard-coded claim that breaks silently the moment the file moves. These tests
pin the predicate (what is and is not a site), the ratchet (a NEW site fails,
the ceiling only goes down), the key format (survives the comment-stripping
reader, unique per scope) and ``--fix``.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.ci import self_root_census as census

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# the predicate
# --------------------------------------------------------------------------- #
def _hops(expr: str) -> int | None:
    return census.climb_hops(ast.parse(expr, mode="eval").body)


@pytest.mark.parametrize("expr,hops", [
    ("Path(__file__).resolve().parent", 0),
    ("Path(__file__).parent.parent", 1),
    ("Path(__file__).resolve().parent.parent.parent", 2),
    ("Path(__file__).resolve().parents[2]", 2),
    ("Path(__file__).parents[0]", 0),
    ("os.path.dirname(os.path.abspath(__file__))", 0),
    ("os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))", 2),
    ("Path(__file__)", -1),
    ("Path(x).parent.parent", None),                 # not __file__
    ("some_root / 'args'", None),
    ("Path(__file__).resolve().parents[n]", None),   # not a literal: unknowable
])
def test_climb_hops(expr, hops):
    assert _hops(expr) == hops


def _scan(tmp_path: Path, rel: str, source: str) -> list[dict]:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    return census.scan_file(f, tmp_path)


def test_root_climb_is_a_site_and_module_local_is_not(tmp_path):
    sites = _scan(tmp_path, "tools/db/mod.py", """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent.parent     # root  -> site
        TEMPLATES = Path(__file__).resolve().parent / "templates"    # local -> not a site
        PKG = Path(__file__).resolve().parent.parent / "shared"      # tools/ -> not the root
        CFG = BASE_DIR / "args" / "x.yaml"
    """)
    assert [s["name"] for s in sites] == ["BASE_DIR"]
    assert sites[0]["kind"] == "root" and sites[0]["hops"] == 2 and sites[0]["depth"] == 2
    assert sites[0]["key"] == "tools/db/mod.py::<module>::BASE_DIR"
    assert sites[0]["fixable"] is True


def test_overwalk_is_reported_as_its_own_kind(tmp_path):
    sites = _scan(tmp_path, "tools/db/mod.py", """
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[3]
        X = ROOT / "args"
    """)
    assert len(sites) == 1 and sites[0]["kind"] == "overwalk" and sites[0]["fixable"] is False


def test_sys_path_bootstrap_idiom_is_not_a_site(tmp_path):
    sites = _scan(tmp_path, "tools/db/mod.py", """
        import sys
        from pathlib import Path
        _REPO_ROOT = Path(__file__).resolve().parents[2]
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
    """)
    assert sites == []


def test_bootstrap_name_also_used_for_data_is_a_site(tmp_path):
    sites = _scan(tmp_path, "tools/db/mod.py", """
        import sys
        from pathlib import Path
        _REPO_ROOT = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(_REPO_ROOT))
        DB = _REPO_ROOT / "data" / "icdev.db"
    """)
    assert [s["name"] for s in sites] == ["_REPO_ROOT"]


def test_marker_walk_is_never_a_site(tmp_path):
    sites = _scan(tmp_path, "tools/db/mod.py", """
        from pathlib import Path
        def _find_root():
            for p in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
                if (p / "pyproject.toml").exists():
                    return p
        ROOT = _find_root()
    """)
    assert sites == []


def test_inline_join_and_function_scope_sites(tmp_path):
    sites = _scan(tmp_path, "tools/a/b/mod.py", """
        from pathlib import Path
        def load():
            return (Path(__file__).resolve().parents[3] / "args" / "x.yaml").read_text()
        def other():
            a = Path(__file__).resolve().parents[3] / "data"
            b = Path(__file__).resolve().parents[3] / "context"
            return a, b
    """)
    keys = sorted(s["key"] for s in sites)
    assert keys == [
        "tools/a/b/mod.py::load::inline-1",
        "tools/a/b/mod.py::other::inline-1",
        "tools/a/b/mod.py::other::inline-2",
    ]
    assert all(s["fixable"] is False for s in sites)


def test_keys_survive_the_comment_stripping_reader_and_are_unique(tmp_path):
    sites = _scan(tmp_path, "tools/db/mod.py", """
        from pathlib import Path
        try:
            BASE_DIR = Path(__file__).resolve().parent.parent.parent
        except Exception:
            BASE_DIR = Path(__file__).resolve().parents[2]
        X = BASE_DIR / "args"
    """)
    keys = [s["key"] for s in sites]
    assert keys == ["tools/db/mod.py::<module>::BASE_DIR", "tools/db/mod.py::<module>::BASE_DIR-2"]
    for k in keys:
        assert "#" not in k
        assert k.split("#")[0].strip() == k


# --------------------------------------------------------------------------- #
# the ratchet, against a throwaway repository
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "args").mkdir()
    (tmp_path / "args" / "self_root_gate.yaml").write_text(yaml.safe_dump({
        "self_root_census": {
            "census_file": "args/self_root_census.txt",
            "self_root_max": 1,
            "scan_roots": ["tools"],
            "exclude": [],
        }
    }), encoding="utf-8")
    (tmp_path / "tools" / "pkg").mkdir(parents=True)
    (tmp_path / "tools" / "pkg" / "old.py").write_text(textwrap.dedent("""
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent.parent
        CFG = BASE_DIR / "args" / "x.yaml"
    """), encoding="utf-8")
    (tmp_path / "args" / "self_root_census.txt").write_text(
        "# header\ntools/pkg/old.py::<module>::BASE_DIR\n", encoding="utf-8")
    monkeypatch.setattr(census, "REPO", tmp_path)
    return tmp_path


def test_registered_site_passes_and_new_site_fails(repo):
    assert census.build_report(repo)["ok"] is True
    (repo / "tools" / "pkg" / "new.py").write_text(textwrap.dedent("""
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[2]
        DATA = ROOT / "data"
    """), encoding="utf-8")
    rep = census.build_report(repo)
    assert rep["ok"] is False
    assert [s["key"] for s in rep["unregistered"]] == ["tools/pkg/new.py::<module>::ROOT"]
    assert census.main(["--check"]) == 1
    # changed-scope: only the named file is scanned, and it still fails
    assert census.main(["--changed", "tools/pkg/new.py", "--check"]) == 1
    assert census.main(["--changed", "tools/pkg/old.py", "--check"]) == 0


def test_ceiling_breach_fails_even_when_every_site_is_registered(repo):
    (repo / "args" / "self_root_census.txt").write_text(
        "tools/pkg/old.py::<module>::BASE_DIR\ntools/pkg/ghost.py::<module>::X\n", encoding="utf-8")
    rep = census.build_report(repo)
    assert rep["over_ceiling"] is True and rep["ok"] is False
    assert census.prune(repo) == 1  # the ghost goes; the census only ever shrinks
    assert census.build_report(repo)["ok"] is True


def test_fix_rewrites_the_simple_form_and_refuses_the_rest(repo):
    (repo / "tools" / "pkg" / "mixed.py").write_text(textwrap.dedent("""
        import sys
        from pathlib import Path
        _BOOT = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(_BOOT))
        BASE_DIR = Path(__file__).resolve().parent.parent.parent
        def f():
            return Path(__file__).resolve().parents[2] / "args"
    """), encoding="utf-8")
    names = census.fix_file(repo / "tools" / "pkg" / "mixed.py", repo)
    assert names == ["BASE_DIR"]
    text = (repo / "tools" / "pkg" / "mixed.py").read_text(encoding="utf-8")
    assert "BASE_DIR = repo_root(__file__)" in text
    assert "from icdev.core.paths import repo_root" in text
    assert "_BOOT = Path(__file__).resolve().parents[2]" in text  # bootstrap untouched
    assert 'Path(__file__).resolve().parents[2] / "args"' in text  # function-scope site untouched
    ast.parse(text)  # still valid Python
    remaining = census.scan_file(repo / "tools" / "pkg" / "mixed.py", repo)
    assert [s["qualname"] for s in remaining] == ["f"]


# --------------------------------------------------------------------------- #
# the checked-in census
# --------------------------------------------------------------------------- #
def test_checked_in_census_is_consistent_with_the_tree():
    rep = census.build_report(REPO_ROOT)
    assert rep["unregistered"] == [], [s["key"] for s in rep["unregistered"]][:10]
    assert rep["stale_entries"] == [], rep["stale_entries"][:10]
    assert rep["census_size"] <= rep["ceiling"]


def test_ceiling_equals_the_census_at_adoption():
    cfg = census.load_gate(REPO_ROOT / "args" / "self_root_gate.yaml")
    entries = census.load_census(REPO_ROOT, cfg)
    assert int(cfg["self_root_max"]) == len(entries), "headroom is permission"


def test_known_shapes_in_the_tree():
    """The scanner sees the canonical shape, and does NOT see the bootstrap idiom.

    THE POSITIVE CONTROL IS THE SHAPE, NOT A NAMED FILE. It used to assert
    `tools/config/core_profile.py::<module>::BASE_DIR` was present, and that broke the moment
    that site was migrated onto repo_root(__file__) -- which is the census's entire PURPOSE.
    Pinning any single entry makes this test fail on success: every name in the census is
    somebody's future fix, so the exemplar has to be the shape it detects.

    The NEGATIVE control stays exact, because it is a claim about ONE file: storage.py's
    `_REPO_ROOT = parents[2]` is the sys.path BOOTSTRAP idiom, which resolves the IMPORT root
    and is identical before and after a move. It must never be counted, and naming it is the
    only way to say so.
    """
    keys = census.load_census(REPO_ROOT, census.load_gate(REPO_ROOT / "args" / "self_root_gate.yaml"))
    assert any(k.endswith("::<module>::BASE_DIR") for k in keys),         "the canonical module-level BASE_DIR shape is no longer detected by the scanner"
    assert any("::inline-" in k for k in keys),         "the inline (function-local) shape is no longer detected by the scanner"
    # storage.py's _REPO_ROOT is the sys.path bootstrap idiom; its root resolver walks by marker
    assert not any(k.startswith("tools/db/storage.py::") for k in keys)


def test_tool_is_mirrored_byte_for_byte():
    a = (REPO_ROOT / "tools" / "ci" / "self_root_census.py").read_bytes()
    b = (REPO_ROOT / "icdev" / "tools" / "ci" / "self_root_census.py").read_bytes()
    assert a == b
