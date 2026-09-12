# CUI // SP-CTI
"""mfx-own-07: the mirror disk-reader census keeps its three kinds apart.

Every assertion here is about a DISCRIMINATION the survey's verdict rests on.
A scanner that merged any two of them would have produced a different answer
to "can a worker worktree skip the icdev/ mirror", so each is pinned.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from tools.ci import mirror_disk_readers as mdr

REPO_ROOT = Path(__file__).resolve().parents[2]


def _kinds(findings, kind):
    return [f for f in findings if f.kind == kind]


def _scan(tmp_path: Path, name: str, body: str):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return list(mdr._scan_python(p, name))


class TestSeparatorIsTheDiscriminator:
    """A DOTTED reference is an import; a SEPARATOR reference is a disk path.

    This is the whole tool. Collapsing the two either way inverts the survey:
    counting imports as disk reads makes every one of the 1,751 aliased
    imports a blocker, and ignoring them entirely lets "nothing reads the
    mirror from disk" be produced by a scanner that never looked.
    """

    def test_import_statement_is_never_a_disk_read(self, tmp_path):
        out = _scan(tmp_path, "a.py", "from icdev.tools.db.storage import get_connection\n")
        assert _kinds(out, mdr.KIND_DISK) == []
        assert len(_kinds(out, mdr.KIND_IMPORT_REF)) == 1

    def test_dotted_string_is_an_import_reference_not_a_disk_read(self, tmp_path):
        out = _scan(tmp_path, "a.py", 'patch("icdev.tools.db.storage.get_connection")\n')
        assert _kinds(out, mdr.KIND_DISK) == []
        assert len(_kinds(out, mdr.KIND_IMPORT_REF)) == 1

    def test_separator_literal_is_a_disk_read(self, tmp_path):
        out = _scan(tmp_path, "a.py", 'ROOT = "icdev/tools"\n')
        assert len(_kinds(out, mdr.KIND_DISK)) == 1

    def test_path_join_chain_is_a_disk_read(self, tmp_path):
        out = _scan(tmp_path, "a.py",
                    'from pathlib import Path\np = Path(".") / "icdev" / "tools" / "x.py"\n')
        assert len(_kinds(out, mdr.KIND_DISK)) == 1

    def test_adjacent_call_segments_are_a_disk_read(self, tmp_path):
        out = _scan(tmp_path, "a.py",
                    'import os\np = os.path.join(root, "icdev", "tools", "x.py")\n')
        assert len(_kinds(out, mdr.KIND_DISK)) == 1


class TestProseIsNotAReader:
    """360 of the 1,077 raw hits were prose, and this module's own docstring
    names the path five times. A census whose first entries are its own
    explanation of itself is one nobody reads."""

    def test_docstring_mention_is_prose(self, tmp_path):
        out = _scan(tmp_path, "a.py", '"""We deliberately do not read icdev/tools/x.py."""\n')
        assert _kinds(out, mdr.KIND_DISK) == []
        assert len(_kinds(out, mdr.KIND_PROSE)) == 1

    def test_path_buried_in_a_sentence_is_prose(self, tmp_path):
        out = _scan(tmp_path, "a.py", 'MSG = "the canonical copy lives in icdev/tools/llm/"\n')
        assert _kinds(out, mdr.KIND_DISK) == []
        assert len(_kinds(out, mdr.KIND_PROSE)) == 1

    def test_this_modules_own_docstring_is_prose_not_five_disk_reads(self):
        """The positive control, against the real file.

        The scanner's module docstring names ``icdev/tools`` five times to
        EXPLAIN that a dotted import is not a disk read. A census whose first
        entries are its own explanation of itself is one nobody reads
        (rem-hyg-13's first-entry problem). The docstring must fold to a
        single `prose_mention`.

        Its two module-level path CONSTANTS (`MIRROR_ROOT`, `_DISK_MARKERS`)
        are deliberately still reported: they really are structural path
        references, exactly as `scan_roots: [tools, icdev/tools]` is in
        `undeclared_import_census.py`. Exempting the scanner from its own rule
        would be the special case this file argues against everywhere else.
        """
        rel = "tools/ci/mirror_disk_readers.py"
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        doc_end = ast.parse(src).body[0].end_lineno
        out = list(mdr._scan_python(REPO_ROOT / rel, rel))

        in_doc = [f for f in out if f.line <= doc_end]
        assert [f.kind for f in in_doc] == [mdr.KIND_PROSE], \
            "the module docstring's five mentions must fold to one prose_mention"
        assert all(f.line > doc_end for f in _kinds(out, mdr.KIND_DISK)), \
            "no disk_read may come from the docstring"


class TestStructuralTextLines:
    def test_census_entry_is_structural(self):
        line = "icdev/tools/ace/controller.py::ACEController._x[1]  # grandfathered"
        assert mdr._is_structural(line, mdr._path_token(line)) is True

    def test_packaging_directive_is_structural(self):
        line = "recursive-exclude icdev/tools/pulse *"
        assert mdr._is_structural(line, mdr._path_token(line)) is True

    def test_prose_line_is_not_structural(self):
        line = "core insight holds: agent_loop.py (run_agent_loop, canonical icdev/tools/llm/),"
        assert mdr._is_structural(line, mdr._path_token(line)) is False


class TestOneStatementOfWhatAShimIs:
    """xit-decl-02's predicate is IMPORTED, never re-implemented.

    A behavioural test cannot see the difference -- a local copy would agree
    with the original on today's five shims -- so this reads the AST. The
    failure mode is a future edit inlining the `_SELF_SHIM_RE` heuristic here,
    after which the census and icdev/_shim.py could disagree about which
    module a name resolves to.
    """

    def test_shim_predicate_is_imported_from_icdev_shim(self):
        src = (REPO_ROOT / "tools/ci/mirror_disk_readers.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = any(
            isinstance(n, ast.ImportFrom) and n.module == "icdev._shim"
            and any(a.name == "is_backcompat_shim" for a in n.names)
            for n in ast.walk(tree)
        )
        assert imported, "must import icdev._shim.is_backcompat_shim"
        defined = any(isinstance(n, ast.FunctionDef) and "shim" in n.name.lower()
                      for n in ast.walk(tree))
        assert not defined, "must not define a second shim predicate"


class TestUnmeasurableIsNeverClean:
    def test_a_tree_with_no_mirror_reports_unmeasurable(self, tmp_path, monkeypatch):
        """A sparse worktree scanning ITSELF must not report a clean bill of
        health -- it has no mirror to measure, which is not the same as a
        mirror nothing reads."""
        monkeypatch.setattr(mdr, "_tracked", lambda root: ["tools/a.py", "tests/b.py"])
        rep = mdr.collect(tmp_path)
        assert rep.measured is False
        assert "no tracked files" in rep.reason

    def test_unreadable_git_reports_unmeasurable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mdr, "_tracked", lambda root: None)
        rep = mdr.collect(tmp_path)
        assert rep.measured is False
        assert rep.findings == []


class TestAgainstTheRealTree:
    """The blockers the verdict names must actually be found."""

    def test_the_five_shims_are_reported_as_import_blockers(self):
        out = subprocess.run(
            [sys.executable, "tools/ci/mirror_disk_readers.py", "--json", "--kind",
             mdr.KIND_SHIM],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=900)
        assert out.returncode == 0, out.stderr[-500:]
        import json
        rep = json.loads(out.stdout)
        files = {f["file"] for f in rep["findings"]}
        for expected in ("tools/billing/tier.py", "tools/llm/agent_loop.py",
                         "tools/showcase/synthetic_data_engine.py",
                         "tools/testing/qa_agent_runner.py",
                         "tools/testing/selector_healer.py"):
            assert expected in files, f"{expected} must be reported as a shim blocker"
