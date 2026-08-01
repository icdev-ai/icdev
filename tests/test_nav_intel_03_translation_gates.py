# CUI // SP-CTI
"""nav-intel-03 — Translation validation gates must not fabricate success.

Regression coverage for three honesty bugs in tools/translation/translation_validator.py:

  1. ``check_feature_mapping`` previously hardcoded a 1.0 pass and never called
     ``FeatureMapLoader.validate_output``. These tests assert the real validator
     is invoked and that the score reflects its result.
  2. Mocked translation stubs (D256 mock-and-continue) previously inflated the
     API-surface and compliance scores. These tests assert mocked units are
     excluded from the passing numerator and surfaced as "not verified".
  3. ``check_syntax`` previously returned PASS when the target-language compiler
     was absent (air-gap). These tests assert an explicit not-verified state,
     distinct from pass, that never blocks the gate.
"""

import importlib
import subprocess

tv = importlib.import_module("tools.translation.translation_validator")


# ---------------------------------------------------------------------------
# Bug 1 — check_feature_mapping wires the real validate_output
# ---------------------------------------------------------------------------
class _RecordingLoader:
    """Fake FeatureMapLoader that records validate_output calls."""

    def __init__(self, check_passed):
        self._check_passed = check_passed
        self.validate_calls = 0
        self.detect_calls = 0

    def get_rules(self, src, tgt):
        return [{"id": "r1", "validation": "no_yield_keyword"}]

    def detect_features(self, code, src, tgt):
        self.detect_calls += 1
        return [{"id": "r1", "validation": "no_yield_keyword"}]

    def validate_output(self, translated_code, detected, target_lang):
        self.validate_calls += 1
        check = {
            "rule_id": "r1",
            "validation": "no_yield_keyword",
            "passed": self._check_passed,
            "details": "" if self._check_passed else "yield keyword found in target code",
        }
        bucket = "passed" if self._check_passed else "failed"
        return {"passed": [], "failed": [], "checks": [check], bucket: [check]}


def _patch_loader(monkeypatch, loader):
    monkeypatch.setattr(
        "tools.translation.feature_map.FeatureMapLoader",
        lambda *a, **k: loader,
    )


def test_feature_mapping_calls_validate_output_and_fails_on_violation(monkeypatch):
    """A failing feature-validation check must lower the score (not hardcoded 1.0)."""
    loader = _RecordingLoader(check_passed=False)
    _patch_loader(monkeypatch, loader)

    source_ir = {"units": [{"name": "gen", "source_code": "def gen():\n    yield 1\n"}]}
    translated = [{"name": "gen", "translated_code": "function gen() { yield 1; }"}]

    score, findings, verified = tv.check_feature_mapping(source_ir, translated, "python", "typescript")

    assert loader.validate_calls >= 1, "validate_output was never called (gate fabricated success)"
    assert verified is True
    assert score == 0.0, "a failing validation check must produce a failing score"
    assert findings, "a violation must be reported"


def test_feature_mapping_passes_when_validation_clean(monkeypatch):
    """A clean validation must score 1.0 and still have invoked validate_output."""
    loader = _RecordingLoader(check_passed=True)
    _patch_loader(monkeypatch, loader)

    source_ir = {"units": [{"name": "gen", "source_code": "def gen():\n    yield 1\n"}]}
    translated = [{"name": "gen", "translated_code": "function gen() { return 1; }"}]

    score, findings, verified = tv.check_feature_mapping(source_ir, translated, "python", "typescript")

    assert loader.validate_calls >= 1
    assert verified is True
    assert score == 1.0
    assert findings == []


def test_feature_mapping_not_verified_when_loader_missing(monkeypatch):
    """Loader import failure yields a not-verified state, never a silent pass."""

    def _boom(*a, **k):
        raise ImportError("feature_map unavailable")

    monkeypatch.setattr("tools.translation.feature_map.FeatureMapLoader", _boom)
    score, findings, verified = tv.check_feature_mapping({"units": []}, [], "python", "go")
    assert verified is False


# ---------------------------------------------------------------------------
# Bug 2 — mocked stubs must not inflate api_surface / compliance
# ---------------------------------------------------------------------------
def test_is_mocked_detects_status_and_body_marker():
    assert tv._is_mocked({"status": "mocked"}) is True
    assert tv._is_mocked({"translated_code": 'panic("ICDEV™ translation mock")'}) is True
    assert tv._is_mocked({"status": "translated", "translated_code": "def a(): pass"}) is False


def test_api_surface_mocked_unit_does_not_count_as_matched():
    """A source unit present only as a mock stub must NOT count toward surface."""
    source_ir = {"units": [{"name": "greet", "kind": "function"}, {"name": "add", "kind": "function"}]}
    translated = [
        {"name": "greet", "status": "translated", "translated_code": "def greet(): ..."},
        {"name": "add", "status": "mocked", "translated_code": "# MOCK — Translation failed\ndef add(): ..."},
    ]
    score, findings = tv.check_api_surface(source_ir, translated)
    assert score == 0.5, "mocked 'add' must not fabricate a preserved API surface"
    assert any("Mocked" in f and "add" in f for f in findings)


def test_api_surface_all_real_still_full():
    """Backward-compat: real units (no status) still score fully."""
    source_ir = {"units": [{"name": "greet"}, {"name": "add"}]}
    translated = [{"name": "greet"}, {"name": "add"}]
    score, findings = tv.check_api_surface(source_ir, translated)
    assert score == 1.0
    assert findings == []


def test_compliance_mocked_unit_does_not_inflate():
    """A mocked stub carrying a CUI banner must not count as compliant."""
    units = [
        {"name": "a", "status": "translated", "translated_code": "# CUI // SP-CTI\ndef a(): pass"},
        {
            "name": "b",
            "status": "mocked",
            "translated_code": "# CUI // SP-CTI\n# MOCK — Translation failed\ndef b(): pass",
        },
    ]
    score, findings = tv.check_compliance(units, "python")
    assert score == 0.5, "mocked unit's CUI banner must not fabricate compliance coverage"
    assert any("Mocked" in f and "b" in f for f in findings)


# ---------------------------------------------------------------------------
# Bug 3 — check_syntax not-verified when compiler absent (air-gap)
# ---------------------------------------------------------------------------
def test_check_syntax_valid_python_is_verified_pass(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    passed, errors, verified = tv.check_syntax(f, "python")
    assert (passed, verified) == (True, True)


def test_check_syntax_invalid_python_is_verified_fail(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    passed, errors, verified = tv.check_syntax(f, "python")
    assert verified is True
    assert passed is False
    assert errors


def test_check_syntax_compiler_absent_is_not_verified(monkeypatch, tmp_path):
    """FileNotFoundError (toolchain absent) => verified=False, distinct from pass."""

    def _no_tool(*a, **k):
        raise FileNotFoundError("compiler not installed")

    monkeypatch.setattr(subprocess, "run", _no_tool)
    f = tmp_path / "src.py"
    f.write_text("x = 1\n", encoding="utf-8")
    passed, errors, verified = tv.check_syntax(f, "python")
    assert verified is False, "compiler absence must be an explicit not-verified state"
    assert errors  # carries an explanatory not-verified message


def test_check_syntax_unconfigured_language_is_not_verified(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("noop\n", encoding="utf-8")
    passed, errors, verified = tv.check_syntax(f, "cobol")
    assert verified is False


# ---------------------------------------------------------------------------
# Integration — validate_translation surfaces verified + not-verified honestly
# ---------------------------------------------------------------------------
def test_validate_translation_syntax_not_verified_does_not_block(monkeypatch, tmp_path):
    """Compiler absent => syntax not_verified, gate not failed on that account."""

    def _no_tool(*a, **k):
        raise FileNotFoundError("compiler not installed")

    monkeypatch.setattr(subprocess, "run", _no_tool)

    out = tmp_path / "proj"
    out.mkdir()
    (out / "mod.py").write_text("# CUI // SP-CTI\ndef greet():\n    return 1\n", encoding="utf-8")

    source_ir = {"language": "python", "units": [{"name": "greet"}]}
    translated_data = {
        "translated_units": [
            {"name": "greet", "status": "translated", "translated_code": "# CUI // SP-CTI\ndef greet(): return 1"}
        ],
        "mocked_units": [],
    }

    report = tv.validate_translation(
        source_ir=source_ir,
        translated_data=translated_data,
        source_language="python",
        target_language="python",
        output_dir=str(out),
        config=tv._load_config(),
        db_path=None,
    )

    syntax = report["checks"]["syntax"]
    assert syntax["verified"] is False
    assert syntax["status"] == "not_verified"
    assert syntax["passed"] is False  # never counts as a pass
    assert syntax["score"] is None
    assert "syntax" in report["not_verified_checks"]
    assert report["fully_verified"] is False
    # Not-verified syntax must not, by itself, fail the gate (air-gap safe).
    assert report["overall_pass"] is True


def test_validate_translation_mocked_count_and_no_inflation(monkeypatch, tmp_path):
    """Mocked units are counted and excluded from api_surface / compliance passes."""
    # Avoid running a real compiler in this check.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    source_ir = {"language": "python", "units": [{"name": "greet"}, {"name": "add"}]}
    translated_data = {
        "translated_units": [
            {"name": "greet", "status": "translated", "translated_code": "# CUI // SP-CTI\ndef greet(): ..."}
        ],
        "mocked_units": [
            {"name": "add", "status": "mocked", "translated_code": "# CUI // SP-CTI\n# MOCK — Translation failed\ndef add(): ..."}
        ],
    }

    report = tv.validate_translation(
        source_ir=source_ir,
        translated_data=translated_data,
        source_language="python",
        target_language="python",
        output_dir=None,
        config=tv._load_config(),
        db_path=None,
    )

    assert report["mocked_count"] == 1
    # 'add' only exists as a mock -> api_surface must be 0.5, not 1.0
    assert report["checks"]["api_surface"]["score"] == 0.5
    assert report["checks"]["api_surface"]["mocked_count"] == 1
    # mocked 'add' CUI banner must not inflate compliance
    assert report["checks"]["compliance"]["score"] == 0.5


# ---------------------------------------------------------------------------
# Consumer — persistence stores NULL for not-verified so the dashboard can
# render an honest "Not verified" state distinct from pass/fail.
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def execute(self, sql, params):
        self._sink.append(params)


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _FakeCursor(self._sink)

    def commit(self):
        pass

    def close(self):
        pass


def test_record_validations_persists_null_for_not_verified(monkeypatch):
    sink = []
    monkeypatch.setattr(tv, "get_connection", lambda *a, **k: _FakeConn(sink))

    results = {
        "syntax": {"passed": False, "verified": False, "score": None, "findings": ["absent"]},
        "api_surface": {"passed": True, "verified": True, "score": 1.0, "findings": []},
    }
    tv._record_validations("dummy.db", "job-1", results)

    by_check = {p[2]: p for p in sink}  # params: (id, job_id, check_type, passed, score, findings)
    # not-verified => passed NULL, score NULL
    assert by_check["syntax"][3] is None
    assert by_check["syntax"][4] is None
    # verified pass => passed 1, score 1.0
    assert by_check["api_surface"][3] == 1
    assert by_check["api_surface"][4] == 1.0


def test_detail_template_renders_not_verified_from_null_passed():
    """The dashboard status cell logic maps NULL passed -> 'Not verified'."""
    from jinja2 import Environment

    snippet = (
        "{% if v.passed is none %}Not verified"
        "{% elif v.passed %}Pass{% else %}Fail{% endif %}"
    )
    env = Environment()
    tmpl = env.from_string(snippet)
    assert tmpl.render(v={"passed": None}) == "Not verified"
    assert tmpl.render(v={"passed": 1}) == "Pass"
    assert tmpl.render(v={"passed": 0}) == "Fail"
