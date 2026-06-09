# CUI // SP-CTI
"""Tests for tools/security/dependency_auditor.py (eqo-sipa-s2).

Focus: ``audit_python`` must never silently audit the whole installed Python
environment. pip-audit invoked with no ``--requirement`` audits the active
environment, which surfaces ambient/repo-wide CVEs unrelated to the target. A
"scan THIS project" call against a path with no dependency manifest must instead
skip cleanly with zero findings.

Regression: SIPA's PR-diff gate stages only the changed ``*.py`` files (a
manifest-less subtree) and was quarantining benign changes on ambient CVEs that
no changed file introduced.
"""
from tools.security import dependency_auditor


def test_audit_python_skips_when_no_manifest(tmp_path, monkeypatch):
    """A project path with no requirements.txt/pyproject.toml is a clean skip.

    Crucially, pip-audit (the subprocess) is never invoked — so the result can
    never carry ambient-environment CVEs.
    """
    proj = tmp_path / "code_only"
    proj.mkdir()
    (proj / "helper.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    # If audit_python tried to run pip-audit, this would blow up the test.
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess invoked despite no dependency manifest")

    monkeypatch.setattr(dependency_auditor.subprocess, "run", _boom)

    result = dependency_auditor.audit_python(str(proj))

    assert result["success"] is True
    assert result["findings"] == []
    assert result["summary"]["total"] == 0
    assert "skipping pip-audit" in result["raw_output"]


def test_audit_python_attempts_audit_with_requirements(tmp_path, monkeypatch):
    """With a requirements.txt present, audit_python pins pip-audit to it.

    The subprocess is stubbed; we assert the manifest guard let it through and
    that ``--requirement <requirements.txt>`` scopes the audit to the manifest.
    """
    proj = tmp_path / "with_reqs"
    proj.mkdir()
    req = proj / "requirements.txt"
    req.write_text("flask==0.12.2\n", encoding="utf-8")

    calls = []

    class _Proc:
        def __init__(self, returncode, stdout, stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        # First call is the `pip_audit --version` availability probe.
        if "--version" in cmd:
            return _Proc(0, "pip-audit 2.0.0")
        # The real audit call: return a clean (empty) pip-audit JSON document.
        return _Proc(0, '{"dependencies": []}')

    monkeypatch.setattr(dependency_auditor.subprocess, "run", _fake_run)

    result = dependency_auditor.audit_python(str(proj))

    assert result["success"] is True
    # The audit command pinned pip-audit to this project's requirements.txt.
    audit_cmd = next(c for c in calls if "--requirement" in c)
    assert str(req) in audit_cmd
