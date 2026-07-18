# CUI // SP-CTI
"""Guard test (shx-safe-05): daemon/long-running entrypoints bootstrap sys.path
BEFORE the first ``tools.*`` / ``icdev.*`` import.

The live bug: ``tools/genesis/daemon.py`` imported ``get_logger`` (a ``tools.*``
import) BEFORE its ``sys.path.insert(0, BASE_DIR)`` bootstrap. On a machine
where a user-site ``.pth`` file (fathomdesk-root.pth) puts a STALE vendored copy
of the repo on sys.path, that first import bound ``sys.modules["tools"]`` to the
stale tree. Script-style launches (``python tools/genesis/daemon.py``) have only
the script dir on sys.path[0], so user-site wins, and EVERY later ``tools.*``
import — including the reflex dispatch
``importlib.import_module(f"tools.genesis.reflexes.{name}")`` — executes STALE
code silently.

Two layers of protection are asserted here:

  * Static: for each audited entrypoint, the sys.path bootstrap statement
    appears textually before the first ``tools.``/``from tools`` (or ``icdev.``)
    import. A future regression that reorders them fails CI.

  * Behavioral: subprocess-launch the real daemon with a SYNTHETIC shadow
    ``tools`` package prepended via PYTHONPATH, and assert stale silent
    execution is IMPOSSIBLE — the daemon either resolves the REAL checkout
    (bootstrap's insert(0) beats PYTHONPATH) or fails closed via the
    integrity guard (exit 1). Never both silent and stale.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Entrypoints audited for the bootstrap-before-tools-import invariant.
# Each is a script-style / long-running entrypoint launched via
# ``python tools/<...>.py`` where sys.path[0] is only the script dir.
# ---------------------------------------------------------------------------
AUDITED_ENTRYPOINTS = [
    "tools/genesis/daemon.py",
    "tools/genesis/kanban_scheduler.py",
    "tools/genesis/launch.py",
    "tools/proposal_genesis/daemon.py",
    "tools/daemon/base.py",
]


def _first_tools_import_line(source: str) -> int | None:
    """Return the 1-based line number of the first ``import tools.*`` /
    ``from tools[.] ...`` / ``import icdev.*`` / ``from icdev[.] ...`` statement,
    or None if there is no such import."""
    tree = ast.parse(source)
    best: int | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in ("tools", "icdev"):
                    if best is None or node.lineno < best:
                        best = node.lineno
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import; module may be None for "from . import x"
            root = (node.module or "").split(".")[0]
            if node.level == 0 and root in ("tools", "icdev"):
                if best is None or node.lineno < best:
                    best = node.lineno
    return best


def _bootstrap_line(source: str) -> int | None:
    """Return the 1-based line number of the ``sys.path.insert(0, ...)``
    bootstrap statement, or None if absent."""
    for i, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("sys.path.insert(0") or stripped.startswith(
            "sys.path.insert(\n"
        ):
            return i
        # tolerate ``sys.path.insert(0, str(BASE_DIR))`` style
        if "sys.path.insert(0" in stripped:
            return i
    return None


@pytest.mark.parametrize("relpath", AUDITED_ENTRYPOINTS)
def test_bootstrap_precedes_first_tools_import(relpath: str) -> None:
    """Static guard: the sys.path bootstrap must appear before the first
    ``tools.*`` / ``icdev.*`` import in every audited entrypoint."""
    path = REPO_ROOT / relpath
    assert path.is_file(), f"audited entrypoint missing: {relpath}"
    source = path.read_text(encoding="utf-8")

    boot = _bootstrap_line(source)
    assert boot is not None, (
        f"{relpath}: no ``sys.path.insert(0, ...)`` bootstrap found — a "
        f"script-style entrypoint MUST insert the repo root at sys.path[0]."
    )

    first_import = _first_tools_import_line(source)
    if first_import is None:
        # No tools.* import at all (unlikely for these files) — nothing to order.
        return

    assert boot < first_import, (
        f"{relpath}: sys.path bootstrap at line {boot} does NOT precede the "
        f"first tools.*/icdev.* import at line {first_import}. A stale vendored "
        f"'tools' tree on sys.path would bind sys.modules['tools'] to it and run "
        f"stale code (shx-safe-05 regression)."
    )


def test_integrity_guard_exists_in_base() -> None:
    """The fail-closed startup guard must exist and be wired into run_cli."""
    base_src = (REPO_ROOT / "tools/daemon/base.py").read_text(encoding="utf-8")
    assert "def verify_tools_tree_integrity" in base_src, (
        "verify_tools_tree_integrity guard missing from tools/daemon/base.py"
    )
    assert "verify_tools_tree_integrity(BASE_DIR)" in base_src, (
        "integrity guard is not called from run_cli() in tools/daemon/base.py"
    )
    assert "ICDEV_SKIP_TREE_GUARD" in base_src, (
        "guard bypass env var ICDEV_SKIP_TREE_GUARD not documented/implemented"
    )


def test_behavioral_shadow_cannot_run_stale_silently(tmp_path: Path) -> None:
    """Behavioral guard: launch the real daemon with a SYNTHETIC shadow ``tools``
    package prepended via PYTHONPATH. The bootstrap's ``sys.path.insert(0, ...)``
    must beat PYTHONPATH so the REAL checkout is imported — OR the integrity
    guard must fail closed (exit 1). Stale silent execution must be impossible.
    """
    # Build a fake shadow tree: <shadow>/tools/__init__.py carrying a marker that
    # is IMPOSSIBLE in the real package. If the daemon ever imports this, we can
    # prove stale execution occurred.
    shadow = tmp_path / "shadow"
    (shadow / "tools").mkdir(parents=True)
    (shadow / "tools" / "__init__.py").write_text(
        textwrap.dedent(
            """
            # Synthetic shadow 'tools' package (shx-safe-05 behavioral test).
            __ICDEV_SHADOW_MARKER__ = "STALE-SHADOW-TREE"
            """
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    # Prepend the shadow so, absent a correct bootstrap, user-site-style shadowing
    # would win. The real checkout root is NOT added here on purpose.
    env["PYTHONPATH"] = str(shadow) + os.pathsep + env.get("PYTHONPATH", "")
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    # Do NOT set ICDEV_SKIP_TREE_GUARD — we want the guard active.
    env.pop("ICDEV_SKIP_TREE_GUARD", None)

    proc = subprocess.run(
        [sys.executable, "tools/genesis/daemon.py", "--status", "--json"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )

    combined = (proc.stdout or "") + (proc.stderr or "")

    # The shadow marker must NEVER have been the source of truth. If the daemon
    # had bound to the shadow tree, its very first tools.* import would fail
    # (the shadow has no tools.logging), so a traceback naming the shadow path
    # is itself proof of shadowing. Assert we never silently ran the shadow.
    assert "STALE-SHADOW-TREE" not in combined, (
        "daemon appears to have imported the synthetic shadow 'tools' tree — "
        "stale execution was NOT prevented:\n" + combined
    )

    if proc.returncode == 0:
        # Success path: bootstrap won, real tree resolved, status printed.
        assert '"reflexes"' in proc.stdout or '"daemon"' in proc.stdout or proc.stdout.strip().startswith("{"), (
            "daemon exited 0 but did not emit real JSON status:\n" + combined
        )
    else:
        # Fail-closed path: the integrity guard (or an import error from the
        # shadow) aborted startup. Exit 1 with a CRITICAL/shadow diagnostic is
        # the acceptable protective outcome. Either way, NOT silent+stale.
        assert proc.returncode == 1, (
            f"daemon exited with unexpected code {proc.returncode}:\n" + combined
        )
