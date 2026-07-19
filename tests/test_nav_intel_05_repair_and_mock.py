# CUI // SP-CTI
"""nav-intel-05 — Translation repair loop is actually invoked, and mock output
is flagged explicitly and excluded from success metrics.

Regression coverage for two P2 bugs found in the nav-intel-02 audit of
tools/translation/:

  1. ``repair_translation`` was implemented but NEVER invoked — the manager
     reported repair status "attempted" with no repaired code. These tests
     assert the bounded compiler-feedback repair loop (D255) actually calls
     ``repair_translation``, replaces the failing code, re-validates, and marks
     the unit repaired — and that it is bounded by ``max_repair_attempts``.
  2. An LLM error silently degraded a unit to a MOCK that could be mistaken for
     a real translation. These tests assert mock units carry an explicit
     ``mock: true`` flag, are recognised by ``_is_mocked``, and are excluded
     from the job summary's success metrics (real vs mock vs repaired).
"""

import importlib

import pytest

ct = importlib.import_module("tools.translation.code_translator")
tv = importlib.import_module("tools.translation.translation_validator")
tm = importlib.import_module("tools.translation.translation_manager")


# ---------------------------------------------------------------------------
# Bug 2 — mock output is flagged explicitly + excluded from success metrics
# ---------------------------------------------------------------------------
def _min_ir():
    return {
        "language": "python",
        "file_count": 1,
        "total_lines": 3,
        "imports": [],
        "units": [
            {
                "name": "a",
                "kind": "function",
                "source_file": "main.py",
                "params": [],
                "return_type": "",
                "source_code": "def a():\n    return 1\n",
                "source_hash": "h1",
                "calls": [],
                "bases": [],
                "line_count": 2,
            }
        ],
    }


def _cfg():
    return {"translation": {"candidates": 1, "mock_on_failure": True, "max_mock_pct": 100}}


def test_llm_failure_flags_mock_true(monkeypatch):
    """An LLM error (None result) degrades to a mock flagged ``mock: True`` and
    is NOT counted among real translations."""
    monkeypatch.setattr(ct, "_invoke_llm", lambda *a, **k: None)
    res = ct.translate_units(_min_ir(), "python", "java", config=_cfg())

    assert res["stats"]["translated_count"] == 0
    assert res["stats"]["mocked_count"] == 1
    assert res["mocked_units"][0]["mock"] is True
    assert res["mocked_units"][0]["status"] == "mocked"


def test_real_translation_flagged_mock_false(monkeypatch):
    """A successful translation is flagged ``mock: False`` and ``repaired: False``."""
    monkeypatch.setattr(ct, "_invoke_llm", lambda *a, **k: "// CUI // SP-CTI\npublic void a() {}")
    res = ct.translate_units(_min_ir(), "python", "java", config=_cfg())

    assert res["stats"]["translated_count"] == 1
    assert res["translated_units"][0]["mock"] is False
    assert res["translated_units"][0]["repaired"] is False


def test_is_mocked_honors_explicit_flag():
    """``_is_mocked`` treats an explicit ``mock: True`` flag as authoritative,
    even without a ``status`` or body marker."""
    assert tv._is_mocked({"mock": True, "translated_code": "def a(): pass"}) is True
    assert tv._is_mocked({"mock": False, "status": "translated", "translated_code": "def a(): pass"}) is False


# ---------------------------------------------------------------------------
# _collect_gate_errors — only verified, failed checks feed the repair prompt
# ---------------------------------------------------------------------------
def test_collect_gate_errors_skips_not_verified():
    report = {
        "checks": {
            "syntax": {"verified": False, "passed": False, "findings": ["toolchain absent"]},
            "compliance": {"verified": True, "passed": False, "findings": ["Missing CUI marking: greet"]},
            "api_surface": {"verified": True, "passed": True, "findings": []},
        }
    }
    errors = tm._collect_gate_errors(report)
    assert "Missing CUI marking: greet" in errors
    assert "toolchain absent" not in errors  # not-verified never triggers repair


# ---------------------------------------------------------------------------
# Bug 1 — repair loop is actually invoked, re-validates, and is bounded
# ---------------------------------------------------------------------------
def _failing_trans_result():
    """A real (non-mock) translated unit missing its CUI banner → compliance fails."""
    return {
        "translated_units": [
            {
                "name": "greet",
                "kind": "function",
                "source_file": "main.py",
                "status": "translated",
                "mock": False,
                "repaired": False,
                "translated_code": "def greet():\n    return 1\n",
                "source_hash": "h1",
            }
        ],
        "mocked_units": [],
        "stats": {
            "total_units": 1,
            "translated_count": 1,
            "mocked_count": 0,
            "failed_count": 0,
            "mock_percentage": 0.0,
            "mock_threshold_exceeded": False,
        },
    }


def _source_ir_greet():
    return {"language": "python", "units": [{"name": "greet", "source_code": "def greet():\n    return 1\n"}]}


def test_repair_loop_repairs_and_revalidates(monkeypatch):
    """A failed validation triggers repair; the repaired unit (now CUI-marked) is
    re-validated and the gate resolves. The unit is flagged ``repaired``."""
    calls = {"n": 0}

    def fake_repair(unit, source_code, translated_code, errors, source_language, target_language, config=None):
        calls["n"] += 1
        # Repair by adding the missing CUI banner the compliance check demands.
        return "# CUI // SP-CTI\n" + translated_code

    monkeypatch.setattr(tv, "repair_translation", fake_repair)

    source_ir = _source_ir_greet()
    trans_result = _failing_trans_result()
    config = {"repair": {"max_repair_attempts": 3, "include_compiler_errors": True}}

    initial = tv.validate_translation(
        source_ir=source_ir,
        translated_data=trans_result,
        source_language="python",
        target_language="python",
        output_dir=None,
        config=tv._load_config(),
        db_path=None,
    )
    assert initial["overall_pass"] is False  # compliance blocks

    summary = tm._run_repair_loop(
        trans_result=trans_result,
        source_ir=source_ir,
        validation_report=initial,
        src_lang="python",
        tgt_lang="python",
        output_dir=None,
        out_path=None,
        config=config,
        dep_mappings=None,
        db_path=None,
    )

    assert calls["n"] >= 1, "repair_translation was never invoked"
    assert summary["resolved"] is True, "gate should resolve after CUI banner added"
    assert "greet" in summary["repaired_units"]
    assert summary["attempts"] == 1
    unit = trans_result["translated_units"][0]
    assert unit["repaired"] is True
    assert "CUI // SP-CTI" in unit["translated_code"]
    assert summary["final_report"]["overall_pass"] is True


def test_repair_loop_is_bounded(monkeypatch):
    """Repair that keeps changing code but never fixes the defect stops at
    ``max_repair_attempts`` (bounded)."""
    calls = {"n": 0}

    def stubborn_repair(unit, source_code, translated_code, errors, source_language, target_language, config=None):
        calls["n"] += 1
        # Change the code every time (so the loop keeps going) but never add CUI.
        return translated_code + f"\n# tweak-{calls['n']}"

    monkeypatch.setattr(tv, "repair_translation", stubborn_repair)

    source_ir = _source_ir_greet()
    trans_result = _failing_trans_result()
    config = {"repair": {"max_repair_attempts": 2, "include_compiler_errors": True}}

    initial = tv.validate_translation(
        source_ir=source_ir,
        translated_data=trans_result,
        source_language="python",
        target_language="python",
        output_dir=None,
        config=tv._load_config(),
        db_path=None,
    )

    summary = tm._run_repair_loop(
        trans_result=trans_result,
        source_ir=source_ir,
        validation_report=initial,
        src_lang="python",
        tgt_lang="python",
        output_dir=None,
        out_path=None,
        config=config,
        dep_mappings=None,
        db_path=None,
    )

    assert summary["attempts"] == 2, "must stop at max_repair_attempts"
    assert calls["n"] == 2, "one repair call per unit per bounded attempt"
    assert summary["resolved"] is False


def test_repair_loop_stops_early_on_no_change(monkeypatch):
    """When repair yields nothing (None), the loop stops early instead of burning
    all attempts."""
    calls = {"n": 0}

    def null_repair(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(tv, "repair_translation", null_repair)

    source_ir = _source_ir_greet()
    trans_result = _failing_trans_result()
    config = {"repair": {"max_repair_attempts": 3, "include_compiler_errors": True}}

    initial = tv.validate_translation(
        source_ir=source_ir,
        translated_data=trans_result,
        source_language="python",
        target_language="python",
        output_dir=None,
        config=tv._load_config(),
        db_path=None,
    )

    summary = tm._run_repair_loop(
        trans_result=trans_result,
        source_ir=source_ir,
        validation_report=initial,
        src_lang="python",
        tgt_lang="python",
        output_dir=None,
        out_path=None,
        config=config,
        dep_mappings=None,
        db_path=None,
    )

    assert calls["n"] == 1, "repair attempted once then stopped (no improvement)"
    assert summary["repaired_units"] == []
    assert summary["resolved"] is False


# ---------------------------------------------------------------------------
# Integration — full pipeline job summary distinguishes real/mock/repaired
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _patch_db_path(tmp_path, monkeypatch):
    """Redirect DB_PATH to a non-existent temp path (never touch the real DB)."""
    monkeypatch.setattr(tm, "DB_PATH", tmp_path / "nav_intel_05.db")


@pytest.fixture
def python_project(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    code = (
        "# CUI // SP-CTI\n"
        "def greet(name):\n"
        '    return "Hello, " + name\n\n'
        "def add(a, b):\n"
        "    return a + b\n"
    )
    (src / "main.py").write_text(code, encoding="utf-8")
    return tmp_path


def test_pipeline_summary_excludes_mocks_from_success(python_project, tmp_path, monkeypatch):
    """When every unit degrades to a mock, the job summary reports 0 real
    translations and 0 success — mocks are excluded from success metrics."""
    monkeypatch.setattr(ct, "_invoke_llm", lambda *a, **k: None)  # force mock-and-continue

    out = tmp_path / "out"
    result = tm.run_pipeline(
        source_path=str(python_project / "src"),
        source_language="python",
        target_language="java",
        output_dir=str(out),
    )

    summary = result.get("summary")
    assert summary is not None, "job summary must be produced"
    assert summary["total_units"] >= 2
    assert summary["real_translations"] == 0
    assert summary["mocked_units"] == summary["total_units"]
    assert summary["success_count"] == 0
    assert summary["success_rate"] == 0.0
    # translate-phase stats agree with the summary
    assert result["phases"]["translate"]["stats"]["mocked_count"] == summary["mocked_units"]


def test_pipeline_summary_counts_real_translations(python_project, tmp_path, monkeypatch):
    """When translation succeeds, the summary counts real translations and no
    mocks."""
    monkeypatch.setattr(ct, "_invoke_llm", lambda *a, **k: "// CUI // SP-CTI\npublic void u() {}")

    out = tmp_path / "out"
    result = tm.run_pipeline(
        source_path=str(python_project / "src"),
        source_language="python",
        target_language="java",
        output_dir=str(out),
    )

    summary = result["summary"]
    assert summary["mocked_units"] == 0
    assert summary["real_translations"] == summary["total_units"]
    assert summary["success_count"] == summary["total_units"]
    assert summary["success_rate"] == 1.0
