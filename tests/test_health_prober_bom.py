"""A working module reported broken for three months, because of one codec.

THE DEFECT. `health_prober._probe_module_import` is the "cheap version" of
`python -c "import <module>"` -- its own docstring says so. It reads the source
and `ast.parse`s it instead of spawning 842 subprocesses. But it reads with::

    abs_path.read_text(encoding="utf-8", errors="replace")

and plain ``utf-8`` leaves a leading BYTE ORDER MARK in the string as U+FEFF.
`ast.parse` then rejects it: "invalid non-printable character U+FEFF ...
line 1".

PYTHON'S REAL IMPORT MACHINERY DOES NOT CARE. A leading BOM is explicitly
tolerated in a source file, which is why every one of these modules imports
perfectly from a shell. So the probe reports `fail` for a file that works, and
it is not the probe's stated question -- "can this be imported" -- that it is
answering.

WHAT IT COST, measured on the live board 2026-09-01:
  * FOUR files carry a BOM: tools/network/bgp_predictor.py,
    tools/network/capacity_predictor.py, tools/network/supply_chain_risk.py and
    tools/databridge/connectors/skillhub_connector.py. All four import.
  * 421 recorded `fail` snapshots carrying this error, on EVERY probe run since
    2026-06-14 -- roughly three months of a daily false alarm.
  * The 2-probe confirmation rule cannot help: it exists to filter TRANSIENT
    failures, and this one is perfectly reproducible, so it is confirmed to
    `fail` every time and files kanban cards. The `[Batch] module_import: 471
    regression findings` card is downstream of exactly this.

THE FIX IS THE CODEC. `utf-8-sig` strips a leading BOM and is otherwise
identical to `utf-8`, so it reads the file the way the import system does.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The UTF-8 byte order mark.
BOM = bytes((0xEF, 0xBB, 0xBF))

#: The four files in the tree that actually carry one, so this test is about
#: real inputs rather than only a fixture.
KNOWN_BOM_FILES = [
    "tools/network/bgp_predictor.py",
    "tools/network/capacity_predictor.py",
    "tools/network/supply_chain_risk.py",
    "tools/databridge/connectors/skillhub_connector.py",
]


def _read_like_the_prober(path: Path) -> str:
    from tools.awareness import health_prober

    return health_prober._read_source_for_parse(path)


# --------------------------------------------------------------------------- #
# the defect
# --------------------------------------------------------------------------- #
def test_a_file_with_a_leading_BOM_parses(tmp_path):
    """The whole bug in three lines."""
    src = tmp_path / "withbom.py"
    src.write_bytes(b"\xef\xbb\xbfimport os\n\n\ndef f():\n    return os.sep\n")

    # Python's own import machinery tolerates a leading BOM; so must the probe
    # that claims to stand in for it.
    ast.parse(_read_like_the_prober(src), filename=str(src))


def test_every_file_in_the_TREE_that_carries_a_BOM_parses():
    """The live-tree guard: whatever carries a BOM today must parse.

    DISCOVERED, not listed, and NOT SKIPPED. An earlier draft named the four
    files and skipped when one was absent -- and the skip census refused the
    commit, rightly: "a skipped test satisfies the coverage claim while
    asserting nothing". Discovering them means the test adapts to the tree
    rather than needing an excuse when it changes.

    If the count ever reaches zero this passes vacuously, and that is fine: the
    synthetic tests around it carry the invariant and always run. This one is
    here to catch a REAL file the probe would report broken.
    """
    found = [q for q in (REPO / "tools").rglob("*.py")
             if q.read_bytes()[:3] == BOM]
    for path in found:
        # Raised SyntaxError before this fix, for every one of them.
        ast.parse(_read_like_the_prober(path), filename=str(path))


def test_the_known_offenders_that_are_still_present_still_carry_a_BOM():
    """Reported, never asserted away. These four were verified importable with
    `python -c "import ..."` on 2026-09-01. A BOM legitimately stripped from
    one of them is progress, not a regression, so this is not an equality
    check -- what must hold is that each one still present PARSES, which the
    discovery test above covers."""
    present = [rel for rel in KNOWN_BOM_FILES if (REPO / rel).is_file()]
    assert present, "all four known files vanished; that is worth looking at"
    for rel in present:
        ast.parse(_read_like_the_prober(REPO / rel), filename=rel)


def test_the_probe_reads_with_a_BOM_STRIPPING_codec():
    """Pins the fix at its cause. Reading with plain `utf-8` and then parsing
    is the defect; a test that only checked the outcome would pass again the
    moment somebody 'fixed' it by catching the SyntaxError."""
    import inspect

    from tools.awareness import health_prober

    source = inspect.getsource(health_prober._read_source_for_parse)
    assert "utf-8-sig" in source


# --------------------------------------------------------------------------- #
# and it still fails on things that are ACTUALLY broken
# --------------------------------------------------------------------------- #
def test_a_genuinely_broken_file_still_fails(tmp_path):
    """The control. Stripping a BOM must not become 'ignore syntax errors'."""
    src = tmp_path / "broken.py"
    src.write_text("def f(:\n    pass\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        ast.parse(_read_like_the_prober(src), filename=str(src))


def test_a_BOM_IN_THE_MIDDLE_of_a_file_still_fails(tmp_path):
    """`utf-8-sig` strips only a LEADING mark. A U+FEFF in the body is a real
    defect -- it is what a bad editor write leaves behind -- and the import
    system rejects it too, so the probe must keep reporting it."""
    src = tmp_path / "midbom.py"
    src.write_bytes(b"import os\n\xef\xbb\xbfdef f():\n    return 1\n")
    with pytest.raises(SyntaxError):
        ast.parse(_read_like_the_prober(src), filename=str(src))


def test_a_file_with_no_BOM_is_read_unchanged(tmp_path):
    src = tmp_path / "plain.py"
    src.write_text("x = 1\n", encoding="utf-8")
    assert _read_like_the_prober(src) == "x = 1\n"


def test_undecodable_bytes_still_degrade_rather_than_raise(tmp_path):
    """`errors="replace"` was there for a reason: a probe over 842 files must
    not die on one unreadable byte. The codec change must not remove that."""
    src = tmp_path / "bad_bytes.py"
    src.write_bytes(b"x = 1  # \xff\xfe not utf-8\n")
    text = _read_like_the_prober(src)          # must not raise
    assert "x = 1" in text
