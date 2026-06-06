# CUI // SP-CTI
"""Tests for the AI code-optimization coherence checks (no_placeholders, duplicate_code).

Covers the zero-tolerance placeholder gate and the verbatim-duplicate warn check
added to tools/workflow/coherence_checker.py.
"""
from __future__ import annotations

from pathlib import Path

from tools.workflow.coherence_checker import (
    _added_line_numbers,
    _func_body_fingerprints,
    check_duplicate_code,
    check_no_placeholders,
)

_COMPLETE_FUNC = '''
def add(a, b):
    """Add two numbers."""
    total = a + b
    return total
'''

_STUB_VARIANTS = {
    "pass_only": "def f():\n    pass\n",
    "todo_comment": "def f():\n    # TODO: implement\n    return compute()\n",
    "not_implemented": "def f():\n    raise NotImplementedError\n",
    "ellipsis": "def f():\n    ...\n",
}


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_no_placeholders_passes_on_clean_file(tmp_path):
    f = _write(tmp_path, "clean_mod.py", _COMPLETE_FUNC)
    result = check_no_placeholders(changed_files=[f])
    assert result.status == "pass", result.message


def test_no_placeholders_noop_without_changed_files():
    result = check_no_placeholders(changed_files=None)
    assert result.status == "pass"
    assert "no files specified" in result.message.lower()


def test_no_placeholders_blocks_each_stub_variant(tmp_path):
    for label, body in _STUB_VARIANTS.items():
        f = _write(tmp_path, f"stub_{label}.py", body)
        result = check_no_placeholders(changed_files=[f])
        assert result.status == "fail", f"{label} should fail: {result.message}"
        assert result.missing, f"{label} should report findings"


def test_added_line_numbers_none_for_untracked(tmp_path):
    # An untracked file outside git tracking → None ⇒ whole-file check (all-new).
    f = _write(tmp_path, "fresh.py", _COMPLETE_FUNC)
    assert _added_line_numbers(f) is None


def test_no_placeholders_excludes_test_files(tmp_path):
    # A test file full of stubs must NOT block — test scaffolding is permitted.
    f = _write(tmp_path, "test_scaffold.py", _STUB_VARIANTS["pass_only"])
    result = check_no_placeholders(changed_files=[f])
    assert result.status == "pass", result.message


def test_fingerprint_identical_bodies_match():
    body_a = "def alpha():\n    x = 1\n    y = 2\n    z = x + y\n    w = z * 2\n    q = w - 1\n    return q\n"
    body_b = body_a.replace("alpha", "beta")  # only the name differs
    fp_a = _func_body_fingerprints(body_a)
    fp_b = _func_body_fingerprints(body_b)
    assert set(fp_a.keys()) == set(fp_b.keys()), "rename-only copies must share a fingerprint"


def test_fingerprint_skips_trivial_bodies():
    # Below the minimum body-line threshold → not fingerprinted (avoids noise).
    assert _func_body_fingerprints(_COMPLETE_FUNC) == {}


_BIG_BODY = (
    "    acc = 0\n"
    "    for i in range(7):\n"
    "        acc += i * 3\n"
    "        acc -= 1\n"
    "        acc ^= 2\n"
    "    return acc\n"
)


def test_duplicate_code_passes_on_unique_function(tmp_path):
    # Hermetic: scan_root is an empty existing-code dir, so nothing can match.
    existing = tmp_path / "existing"
    existing.mkdir()
    changed = tmp_path / "changed"
    changed.mkdir()
    f = _write(changed, "unique_mod.py", "def zzq_unique():\n" + _BIG_BODY)
    result = check_duplicate_code(changed_files=[f], scan_root=existing)
    assert result.status == "pass", result.message


def test_duplicate_code_warns_on_verbatim_copy(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    changed = tmp_path / "changed"
    changed.mkdir()
    _write(existing, "original.py", "def original_helper():\n" + _BIG_BODY)
    # Same body, different name → must be flagged as a duplicate (warn).
    f = _write(changed, "pasted.py", "def pasted_helper():\n" + _BIG_BODY)
    result = check_duplicate_code(changed_files=[f], scan_root=existing)
    assert result.status == "warn", result.message
    assert any("original.py" in d for d in result.extra)


def test_duplicate_code_noop_without_changed_files():
    result = check_duplicate_code(changed_files=None)
    assert result.status == "pass"
