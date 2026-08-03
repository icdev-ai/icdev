# CUI // SP-CTI
"""No packaged module may launch a child as ``python -m tools.…``.

The ``tools`` → ``icdev.tools`` alias is a ``sys.modules`` entry installed by
``icdev/__init__.py``. It exists only inside the process that imported ``icdev``;
a child launched with ``-m`` resolves the module name before any ICDEV code
runs. The wheel ships only ``icdev``, so every such child died with::

    ModuleNotFoundError: No module named 'tools'

Verified against a real 1.2.42 wheel in a clean venv. A source checkout has a
real top-level ``tools/`` shim, so this never reproduces in development — which
is exactly why four call sites shipped broken:

  * ``cli/provision_db.py``            — schema init (fixed earlier, PR #1028)
  * ``agent_runtime/delegation.py``    — every delegated child agent
  * ``strategos/research_bridge.py``   — three research-pipeline stages

The scan below is the guard for the class, not for those four: a new call site
is a one-line mistake that no unit test would otherwise catch.

It sweeps ``icdev/tools/`` as well, but only the ~41 modules the source sweep
cannot reach — those with no twin in ``tools/``, and those whose twin is a
back-compat shim so the real implementation lives only in the mirror. Note
that ``strategos`` supplied three of the four call sites above and holds six
such twinless modules today.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

from icdev.tools.installer.sync_package_tree import _is_backcompat_shim

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("tools", "apps")

#: Directories whose content is not packaged, or is about the repo itself.
_SKIP_PARTS = {"__pycache__", "node_modules", ".venv", "venv", "installer"}


def _mirror_only_sources(repo_root: Path) -> list[Path]:
    """Packaged modules that scanning ``tools/`` cannot reach.

    ``icdev/tools/`` is normally a copy of ``tools/``, so sweeping the source
    tree covers it. Two kinds of file break that assumption, and both ship in
    the wheel:

      * no twin in ``tools/`` at all (~36 modules: ``llm/agent_hitl.py``,
        ``strategos/conflict_pipeline.py``, ``forecast/``, ``safety/``, …)
      * the twin is a back-compat shim, so the real implementation lives only
        here — including the 1,825-line ``llm/agent_loop.py``.

    ``strategos`` is not a hypothetical neighbourhood for this: it held three
    of the four ``python -m tools.…`` call sites this file was written for.
    Scanning the whole mirror instead would double-report every ordinary file
    and make a stale mirror fail branches that never touched it.
    """
    source, mirror = repo_root / "tools", repo_root / "icdev" / "tools"
    if not mirror.is_dir():
        return []
    out: list[Path] = []
    for path in mirror.rglob("*.py"):
        if _SKIP_PARTS & set(path.parts):
            continue
        twin = source / path.relative_to(mirror)
        if not twin.is_file() or _is_backcompat_shim(twin, path):
            out.append(path)
    return out


def _packaged_sources(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if _SKIP_PARTS & set(path.parts):
                continue
            out.append(path)
    out.extend(_mirror_only_sources(repo_root))
    return out


def _module_arg_after_dash_m(call: ast.Call) -> str | None:
    """Return the literal module name in a ``[..., "-m", "<mod>"]`` argv list.

    Only literals are reported. A name routed through ``runnable_module(...)``
    is a ``Call`` node, not a ``Constant``, so the correct form is invisible
    here — which is the point.
    """
    for arg in call.args:
        if not isinstance(arg, (ast.List, ast.Tuple)):
            continue
        items = arg.elts
        for i, el in enumerate(items[:-1]):
            if (
                isinstance(el, ast.Constant)
                and el.value == "-m"
                and isinstance(items[i + 1], ast.Constant)
                and isinstance(items[i + 1].value, str)
            ):
                return items[i + 1].value
    return None


#: A match requires a literal ``"-m"`` argv element, so a file whose bytes do
#: not contain one cannot possibly offend. Parsing every .py under tools/ and
#: apps/ took ~16s and timed out under load; this skips ~99% of them and is a
#: strict superset of what the AST walk can match, so it cannot hide a hit.
_DASH_M_NEEDLES = (b'"-m"', b"'-m'")


def _offenders(repo_root: Path = REPO_ROOT) -> list[str]:
    bad: list[str] = []
    for path in _packaged_sources(repo_root):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if not any(needle in raw for needle in _DASH_M_NEEDLES):
            continue
        try:
            with warnings.catch_warnings():
                # Invalid escape sequences in unrelated scanned files would
                # otherwise surface as this test's warnings. They belong to
                # those files, not to the scan, and are not what it reports on.
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(raw.decode("utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            mod = _module_arg_after_dash_m(node)
            if mod and mod.split(".")[0] == "tools":
                rel = path.relative_to(repo_root).as_posix()
                bad.append(f"{rel}:{node.lineno} -> python -m {mod}")
    return sorted(bad)


def test_no_packaged_module_launches_a_bare_tools_dash_m():
    offenders = _offenders()
    assert not offenders, (
        "these launch a child as `python -m tools.…`, which raises "
        "ModuleNotFoundError in a pip-installed wheel. Wrap the module name in "
        "tools.compat.subprocess_utils.runnable_module():\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_would_actually_catch_a_regression(tmp_path):
    """A guard that cannot fail is not a guard.

    Points the scan at a planted offender, so a future refactor of the AST walk
    cannot silently reduce this to a no-op that passes on an empty result.
    """
    planted = tmp_path / "tools" / "sub"
    planted.mkdir(parents=True)
    (planted / "bad.py").write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-m', 'tools.db.init_icdev_db'])\n",
        encoding="utf-8",
    )
    found = _offenders(tmp_path)
    assert any("bad.py" in f and "tools.db.init_icdev_db" in f for f in found), found


def test_the_scan_reaches_modules_that_exist_only_in_the_mirror(tmp_path):
    """Sweeping ``tools/`` alone left ~41 packaged modules unscanned.

    A module with no twin in ``tools/`` is never reached by the source sweep,
    yet it is exactly what a wheel install runs. Plant one and require a hit.
    """
    (tmp_path / "tools").mkdir()
    mirror = tmp_path / "icdev" / "tools" / "strategos"
    mirror.mkdir(parents=True)
    (mirror / "research_bridge.py").write_text(
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-m', 'tools.research.vertical_loader'])\n",
        encoding="utf-8",
    )
    found = _offenders(tmp_path)
    assert any(
        "research_bridge.py" in f and "tools.research.vertical_loader" in f
        for f in found
    ), found


def test_a_mirrored_twin_is_not_double_reported(tmp_path):
    """The mirror copy of an ordinary module must not duplicate its finding.

    One defect, one line. Reporting `tools/x.py` and `icdev/tools/x.py`
    separately would also mean a stale mirror keeps failing after the source
    is fixed.
    """
    (tmp_path / "tools" / "sub").mkdir(parents=True)
    (tmp_path / "icdev" / "tools" / "sub").mkdir(parents=True)
    body = (
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-m', 'tools.db.init_icdev_db'])\n"
    )
    (tmp_path / "tools" / "sub" / "bad.py").write_text(body, encoding="utf-8")
    (tmp_path / "icdev" / "tools" / "sub" / "bad.py").write_text(body, encoding="utf-8")
    assert len(_offenders(tmp_path)) == 1, _offenders(tmp_path)


def test_the_scan_accepts_the_runnable_module_form(tmp_path):
    """The corrected form must not be reported — else the guard blocks the fix."""
    planted = tmp_path / "tools" / "sub"
    planted.mkdir(parents=True)
    (planted / "good.py").write_text(
        "import subprocess, sys\n"
        "from tools.compat.subprocess_utils import runnable_module\n"
        "subprocess.run([sys.executable, '-m',\n"
        "                runnable_module('tools.db.init_icdev_db')])\n",
        encoding="utf-8",
    )
    assert _offenders(tmp_path) == []


@pytest.mark.parametrize("module", [
    "tools.agent_runtime.delegation",
    "tools.research.vertical_loader",
    "tools.research.session_manager",
    "tools.research.research_engine",
    "tools.db.init_icdev_db",
])
def test_each_fixed_target_resolves_where_it_is_launched(module):
    """The names the four fixed sites hand to a child must be importable.

    Resolution is environment-dependent by design; what must hold everywhere is
    that the result names a module this interpreter can actually find.
    """
    import importlib.util

    from tools.compat.subprocess_utils import runnable_module

    target = runnable_module(module)
    assert importlib.util.find_spec(target) is not None, (
        f"runnable_module({module!r}) -> {target!r}, which does not resolve"
    )
