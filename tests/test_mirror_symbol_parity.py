#!/usr/bin/env python3
# CUI // SP-CTI
"""No symbol may exist only in the packaged mirror — CUI // SP-CTI.

``sync_package_tree`` copies canonical ``tools/`` **over** ``icdev/tools/``.
Code authored only in the mirror is therefore deleted by the next release
sync: silently, with no failing build, and with no diff to review because the
sync is a build step rather than a commit.

Three ACE features were lost exactly this way and found only by hand:

  * ``ace/evidence_report._write_generate_audit`` — generating an evidence
    report wrote no audit row, though the module read ``ace_audit_log``.
  * ``ace_audit_log.control_refs`` — SELECTed *with a graceful fallback*, so
    the missing column rendered NIST 800-53 traceability permanently empty
    instead of erroring. The worst shape: it degrades silently.
  * ``ace_sessions.last_user_message`` / ``last_agent_message``.

``test_mirror_drift_baseline`` does not cover this. It compares file *hashes*
for six named packages, so it is (a) blind outside those six — ``ace`` is not
among them — and (b) non-directional: it cannot say which side is ahead, and
fires just as loudly when ``tools/`` legitimately moves ahead of a mirror
awaiting the next sync.

This gate is directional. It reports only the dangerous direction — a symbol
the mirror has and the source lacks — which is the one that loses work.

Back-compat shims are exempt, and the exemption is not a hand-written list:
it calls the same ``_is_backcompat_shim`` predicate ``sync_package_tree``
itself consults, so what this test forgives cannot drift from what the sync
actually protects. For those modules ``icdev.tools.x`` is canonical by design
and ``tools/x.py`` is the stub.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from icdev.tools.installer.sync_package_tree import _is_backcompat_shim

REPO_ROOT = Path(__file__).resolve().parents[1]

_SKIP_PARTS = {"__pycache__", "node_modules", ".venv", "venv"}


def _symbols(text: str) -> set[str]:
    """Definitions and CONSTANT bindings anywhere in a module.

    ``ast.walk`` rather than a top-level scan on purpose: a method or a nested
    helper is just as capable of being the thing that only ever existed in the
    mirror, and the false-positive cost is nil because both sides are read the
    same way.
    """
    out: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    out.add(target.id)
    return out


def _mirror_only(repo_root: Path = REPO_ROOT) -> list[str]:
    """Symbols present in ``icdev/tools/<rel>`` and absent from ``tools/<rel>``.

    Byte-identical twins are skipped without parsing. That is not merely an
    optimisation — parsing all ~3,400 pairs takes ~27s, which does not belong
    in the unit tier — and it cannot hide a hit, since identical bytes cannot
    yield different symbols.

    Files with no twin in ``tools/`` are also skipped: nothing overwrites them,
    so they are not at risk from the copy. Their exposure is a different
    problem, handled by ``test_pkg_subprocess_namespace``.
    """
    source, mirror = repo_root / "tools", repo_root / "icdev" / "tools"
    if not mirror.is_dir() or not source.is_dir():
        return []
    bad: list[str] = []
    for mirror_file in mirror.rglob("*.py"):
        if _SKIP_PARTS & set(mirror_file.parts):
            continue
        rel = mirror_file.relative_to(mirror)
        twin = source / rel
        if not twin.is_file():
            continue
        try:
            mirror_raw, twin_raw = mirror_file.read_bytes(), twin.read_bytes()
        except OSError:
            continue
        if mirror_raw == twin_raw:
            continue
        if _is_backcompat_shim(twin, mirror_file):
            continue
        try:
            missing = _symbols(mirror_raw.decode("utf-8")) - _symbols(
                twin_raw.decode("utf-8")
            )
        except (SyntaxError, UnicodeDecodeError):
            continue
        if missing:
            bad.append(f"icdev/tools/{rel.as_posix()} -> {', '.join(sorted(missing))}")
    return sorted(bad)


def _shim_exempted(repo_root: Path = REPO_ROOT) -> list[Path]:
    """The twins currently forgiven because ``tools/<rel>`` is a shim."""
    source, mirror = repo_root / "tools", repo_root / "icdev" / "tools"
    out: list[Path] = []
    for mirror_file in mirror.rglob("*.py"):
        if _SKIP_PARTS & set(mirror_file.parts):
            continue
        twin = source / mirror_file.relative_to(mirror)
        if twin.is_file() and _is_backcompat_shim(twin, mirror_file):
            out.append(twin)
    return sorted(out)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_no_symbol_exists_only_in_the_packaged_mirror():
    offenders = _mirror_only()
    assert not offenders, (
        "these symbols exist in icdev/tools/ but not in tools/, and the next "
        "`sync_package_tree` run copies tools/ over the mirror and deletes them.\n  "
        + "\n  ".join(offenders)
        + "\n\nRun `git log --oneline -S\"<symbol>\" -- tools/... icdev/tools/...` to "
        "tell the two causes apart:\n"
        "  * hits only on the icdev/ path -> the feature was authored in the "
        "mirror. Restore it into tools/ (canonical); never re-add it to the mirror.\n"
        "  * the symbol was renamed or removed in tools/ -> the mirror is merely "
        "stale. Reconcile with `python tools/dx/mirror_parity.py --paths <pkg> --fix`."
    )


# --------------------------------------------------------------------------- #
# The guard on the guard
# --------------------------------------------------------------------------- #


def test_the_scan_would_actually_catch_a_regression(tmp_path):
    """A guard that cannot fail is not a guard.

    The byte-equality prefilter is the part most likely to be "optimised" into
    a no-op that reports nothing and passes, so plant a real offender.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "icdev" / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "m.py").write_text("def kept():\n    pass\n", encoding="utf-8")
    (tmp_path / "icdev" / "tools" / "m.py").write_text(
        "def kept():\n    pass\n\n\ndef _write_generate_audit():\n    pass\n",
        encoding="utf-8",
    )
    found = _mirror_only(tmp_path)
    assert any("_write_generate_audit" in f for f in found), found


def test_an_identical_twin_is_not_reported(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "icdev" / "tools").mkdir(parents=True)
    for p in (tmp_path / "tools" / "m.py", tmp_path / "icdev" / "tools" / "m.py"):
        p.write_text("def kept():\n    pass\n", encoding="utf-8")
    assert _mirror_only(tmp_path) == []


def test_a_source_that_moved_ahead_is_not_reported(tmp_path):
    """The safe direction must stay quiet, or the gate is unusable between syncs.

    ``tools/`` gaining a symbol the mirror lacks is the ordinary state of the
    repo right after any edit. Reporting it would make this fire on every
    branch and teach people to skip it.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "icdev" / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "m.py").write_text(
        "def kept():\n    pass\n\n\ndef brand_new():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "icdev" / "tools" / "m.py").write_text(
        "def kept():\n    pass\n", encoding="utf-8"
    )
    assert _mirror_only(tmp_path) == []


def test_a_backcompat_shim_twin_is_exempt(tmp_path):
    """`tools/x.py` re-exporting from `icdev.tools.x` is the documented pattern.

    For these the mirror IS canonical, the sync refuses to copy over them, and
    every symbol is "mirror-only" by design. Reporting them would bury the real
    finding under five permanent false positives.
    """
    (tmp_path / "tools").mkdir()
    (tmp_path / "icdev" / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "m.py").write_text(
        "from icdev.tools.m import Thing\n\n__all__ = [\"Thing\"]\n", encoding="utf-8"
    )
    (tmp_path / "icdev" / "tools" / "m.py").write_text(
        "class Thing:\n" + "".join(f"    def m{i}(self):\n        pass\n" for i in range(20)),
        encoding="utf-8",
    )
    assert _mirror_only(tmp_path) == []


def test_every_live_exemption_really_is_a_shim():
    """The exemption must stay narrow.

    ``_is_backcompat_shim`` is a heuristic (imports from the canonical package
    AND is less than half the size of its twin). If a real implementation ever
    satisfied it, this gate would go quiet on exactly the file it exists to
    watch. Re-check the defining property independently.
    """
    exempted = _shim_exempted()
    assert exempted, (
        "no shim exemptions found at all — either the repo changed shape or "
        "_is_backcompat_shim moved, and this gate is no longer aligned with "
        "what sync_package_tree protects"
    )
    for twin in exempted:
        text = twin.read_text(encoding="utf-8", errors="replace")
        rel = twin.relative_to(REPO_ROOT).as_posix()
        assert "from icdev.tools." in text, f"{rel} exempted but imports nothing canonical"
        assert len(text.splitlines()) < 100, (
            f"{rel} is exempted as a stub but is {len(text.splitlines())} lines — "
            "a real implementation must not be forgiven by the shim heuristic"
        )


@pytest.mark.parametrize(
    "symbol,path",
    [
        ("_write_generate_audit", "tools/ace/evidence_report.py"),
        ("control_refs", "tools/ace/db/init_db.py"),
        ("last_user_message", "tools/ace/db/init_db.py"),
    ],
)
def test_the_three_lost_ace_features_are_in_the_canonical_tree(symbol, path):
    """Regression pins for the losses that motivated this file.

    They were restored into ``tools/``; a future sync must be unable to take
    them away again. Asserted against the SOURCE tree — asserting against the
    mirror would pass right up until the sync that deletes them.
    """
    text = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
    assert symbol in text, f"{symbol} is missing from {path} — mirror-only regression"
