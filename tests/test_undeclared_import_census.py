# CUI // SP-CTI
"""The undeclared-import census must actually discriminate (tsg-iso-03).

`python-dateutil` was imported by two runtime modules and declared in NEITHER
requirements.txt nor pyproject.toml, both times inside a bare `except Exception`
returning a benign value. The stale reaper skipped every task and every
notification duration read "unknown", on CI and on any air-gapped install, and
nothing went red for an unknown length of time.

The gate that stops that returning is only worth its runtime if it fails on the
shape and passes on the correct one. Both directions are asserted here, because
a gate that never fires and a gate that fires on everything are the same
useless artifact from the outside.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from tools.ci.undeclared_import_census import (
    REPO,
    build_report,
    declared_distributions,
    handler_swallows,
    load_census,
    load_gate,
    scan_file,
)


def _handler(src: str) -> ast.ExceptHandler:
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            return node
    raise AssertionError("no handler in fixture")


# ── the swallow predicate ──────────────────────────────────────────────────
@pytest.mark.parametrize("body", ["return None", "pass", "continue", "x = None"])
def test_a_handler_that_only_returns_is_swallowing(body):
    src = f"""
        for _ in []:
            try:
                import nowhere
            except Exception:
                {body}
    """
    assert handler_swallows(_handler(src)) is True


def test_a_handler_that_logs_is_not_swallowing():
    """The correct shape for a genuinely optional dependency: the operator gets
    something to read, so the degradation is distinguishable from working."""
    src = """
        try:
            import nowhere
        except ImportError:
            log.warning("nowhere is not installed; feature disabled")
            nowhere = None
    """
    assert handler_swallows(_handler(src)) is False


def test_a_handler_that_raises_is_not_swallowing():
    """tools/blockchain/transports does exactly this, naming the package."""
    src = """
        try:
            import hfc
        except ImportError as exc:
            raise RuntimeError("fabric-sdk-py (hfc) is an undeclared optional dep") from exc
    """
    assert handler_swallows(_handler(src)) is False


# ── the scan ───────────────────────────────────────────────────────────────
def _write(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "tools"
    root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "requirements.txt").write_text("flask>=3.0\npyyaml\n", encoding="utf-8")
    (tmp_path / "args").mkdir(exist_ok=True)
    path = root / "probe.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_an_undeclared_import_in_a_swallowing_handler_is_a_finding(tmp_path):
    path = _write(tmp_path, """
        def f():
            try:
                from dateutil.parser import parse
                return parse
            except Exception:
                return None
    """)
    sites = scan_file(path, declared_distributions(tmp_path), tmp_path)
    assert [s["package"] for s in sites] == ["dateutil"]
    assert sites[0]["distribution"] == "python_dateutil"
    assert sites[0]["key"] == "tools/probe.py::f::dateutil"


def test_the_same_import_behind_a_speaking_handler_is_not(tmp_path):
    """This is the fix, and it must not still read as a finding — otherwise the
    gate gives no way out except deleting the dependency."""
    path = _write(tmp_path, """
        def f():
            try:
                from dateutil.parser import parse
                return parse
            except ImportError:
                log.warning("python-dateutil is absent; durations read unknown")
                return None
    """)
    assert scan_file(path, declared_distributions(tmp_path), tmp_path) == []


def test_a_DECLARED_package_is_not_a_finding(tmp_path):
    """The other way out: declare it. `flask` is in the fixture requirements."""
    path = _write(tmp_path, """
        def f():
            try:
                import flask
                return flask
            except Exception:
                return None
    """)
    assert scan_file(path, declared_distributions(tmp_path), tmp_path) == []


def test_an_import_name_that_differs_from_its_distribution_resolves(tmp_path):
    """`import yaml` is satisfied by `pyyaml`. Matching on the import name alone
    would report the single most-imported package in the tree as undeclared."""
    path = _write(tmp_path, """
        def f():
            try:
                import yaml
                return yaml
            except Exception:
                return None
    """)
    assert scan_file(path, declared_distributions(tmp_path), tmp_path) == []


def test_a_stdlib_import_is_never_a_finding(tmp_path):
    path = _write(tmp_path, """
        def f():
            try:
                import tomllib
                return tomllib
            except Exception:
                return None
    """)
    assert scan_file(path, declared_distributions(tmp_path), tmp_path) == []


def test_an_import_outside_a_try_is_not_a_finding(tmp_path):
    """A hard import fails LOUDLY at import time — that is not the defect. The
    defect is degradation that reads as success."""
    path = _write(tmp_path, """
        import dateutil.parser

        def f():
            return dateutil.parser
    """)
    assert scan_file(path, declared_distributions(tmp_path), tmp_path) == []


def test_one_package_imported_twice_in_a_function_is_one_site(tmp_path):
    """The key is the decision, not the statement."""
    path = _write(tmp_path, """
        def f():
            try:
                import fakepkg.a
                import fakepkg.b
                return fakepkg
            except Exception:
                return None
    """)
    sites = scan_file(path, declared_distributions(tmp_path), tmp_path)
    assert len(sites) == 1


# ── the census, against the real tree ──────────────────────────────────────
def test_the_tree_has_no_unregistered_site():
    """The gate itself. A NEW site fails here by name."""
    report = build_report(REPO)
    assert not report["unregistered"], (
        "undeclared third-party import(s) inside a swallowing handler:\n  "
        + "\n  ".join(
            f"{s['file']}:{s['line']} imports {s['module']} "
            f"(distribution {s['distribution']!r})"
            for s in report["unregistered"]
        )
        + "\n\nDeclare the distribution, or make the handler say which package "
          "was missing. Registering it is a debt you have written down."
    )


def test_the_ceiling_is_not_above_the_census():
    """`undeclared_max` may only go DOWN. Headroom is permission."""
    cfg = load_gate()
    census = load_census(REPO, cfg)
    assert len(census) <= int(cfg["undeclared_max"]), (
        f"census {len(census)} exceeds ceiling {cfg['undeclared_max']}"
    )


def test_the_census_is_enumerated_not_counted():
    """A bare count can be held constant while the SET churns — which is exactly
    how the ungated-test gap regrew behind a green gate."""
    cfg = load_gate()
    assert load_census(REPO, cfg), "census file is empty; the ceiling alone gates nothing"


def test_the_two_dateutil_sites_are_gone_and_not_grandfathered():
    """They were deleted rather than declared. If either comes back, it is a NEW
    site — it must not find a grandfather entry waiting for it."""
    cfg = load_gate()
    census = load_census(REPO, cfg)
    assert not [e for e in census if e.endswith("::dateutil")], (
        "dateutil appears in the census; it was removed from the tree, not registered"
    )
