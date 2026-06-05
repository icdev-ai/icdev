# CUI // SP-CTI
"""Tests for SIPA scanner adapters (sipa-scan-01).

Covers the acceptance criteria for ``tools/integrity/scanners.py``:

  * Each adapter shells out to an existing scanner and folds its output into the
    ``integrity_findings`` shape (source_scanner / finding_type / severity /
    file_path / line / detail), persisted append-only.
  * Every scanner's native severity vocabulary maps onto ``constants.SEVERITY``.
  * A fixture with a **hardcoded secret** is detected end-to-end via the real
    ``secret_detector`` shell-out (built-in pattern fallback, offline).
  * A fixture with a **known-bad dependency manifest** is normalized via the
    subprocess seam (monkeypatched) so the test is deterministic without
    pip-audit / npm / the network present.

SQLite-backed via the shared ``icdev_db`` fixture; quarantine is redirected to a
tmp dir so staging never touches the repo tree.
"""
from pathlib import Path

import pytest

from tools.integrity import constants, ingest, scanners


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def staged_env(icdev_db, tmp_path, monkeypatch):
    """Point get_connection() at the temp SQLite db and quarantine at tmp."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    return icdev_db


def _make_target(root: Path) -> Path:
    """A target tree with a hardcoded secret + a known-bad dependency manifest."""
    src = Path(root)
    src.mkdir(parents=True, exist_ok=True)
    # Hardcoded AWS access key (NOT an 'example'/'placeholder' string, so the
    # built-in secret scanner does not skip it) + a hardcoded password.
    (src / "config.py").write_text(
        "# service credentials\n"
        'API_KEY = "AKIAQYLPMN5HGNAZ7XYZ"\n'
        'DB_PASSWORD = "s3cr3t-hunter2-hunter2"\n',
        encoding="utf-8",
    )
    # Known-bad dependency manifest (old, CVE-laden pins).
    (src / "requirements.txt").write_text(
        "flask==0.12.2\nrequests==2.19.1\npyyaml==3.13\n", encoding="utf-8"
    )
    (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return src


def _findings(aid):
    """All integrity_findings rows for an assessment, as dicts."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT source_scanner, finding_type, severity, file_path, line, detail "
            "FROM integrity_findings WHERE assessment_id = ? ORDER BY id",
            (aid,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _new_assessment(staged_env) -> int:
    """Insert a bare quarantine assessment row and return its id (FK target)."""
    from tools.db.storage import get_connection
    from tools.integrity.db.init_db import init_db

    conn = get_connection()
    try:
        init_db(conn)
        cur = conn.execute(
            "INSERT INTO integrity_assessments "
            "(source_type, source_ref, mode, status) VALUES (?, ?, ?, ?)",
            ("local", "fixture", "provenance_blind", "quarantine"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _canned(stdout: str):
    """A fake _invoke_scanner returning canned stdout (rc=0)."""
    def _fake(cmd, timeout):
        _fake.cmd = cmd  # captured for assertions
        return 0, stdout, ""
    return _fake


# --------------------------------------------------------------------------- #
# Severity normalization — folds every scanner scale into constants.SEVERITY
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("CRITICAL", "critical"),
        ("High", "high"),
        ("medium", "medium"),
        ("LOW", "low"),
        ("info", "info"),
        ("moderate", "medium"),   # npm audit
        ("warning", "medium"),    # clippy / linters
        ("error", "high"),
        ("note", "low"),
        ("unknown", "info"),      # pip-audit w/o CVSS
        ("", "info"),
        (None, "info"),
        ("bogus-value", "info"),  # unrecognized -> default
    ],
)
def test_norm_severity_maps_into_constants(raw, expected):
    out = scanners._norm_severity(raw)
    assert out == expected
    assert out in constants.SEVERITY


def test_norm_severity_honours_default():
    assert scanners._norm_severity(None, default="high") == "high"
    assert scanners._norm_severity("nonsense", default="low") == "low"


# --------------------------------------------------------------------------- #
# End-to-end: a hardcoded secret is found via the REAL secret_detector shell-out
# --------------------------------------------------------------------------- #
def test_secret_scan_detects_hardcoded_secret_end_to_end(staged_env, tmp_path):
    src = _make_target(tmp_path / "target_pkg")
    staged = ingest.stage(str(src))
    aid = staged["assessment_id"]

    result = scanners.run_secret_scan(aid)  # resolves staged path from quarantine

    assert result["scanner"] == "secrets"
    assert result["success"] is True
    assert result["findings_persisted"] >= 1

    rows = _findings(aid)
    secret_rows = [r for r in rows if r["source_scanner"] == "secrets"]
    assert secret_rows, "expected at least one persisted secret finding"
    for r in secret_rows:
        assert r["finding_type"] == "secret"
        assert r["severity"] in constants.SEVERITY


# --------------------------------------------------------------------------- #
# Deterministic normalization via the subprocess seam (monkeypatched)
# --------------------------------------------------------------------------- #
def test_secret_scan_normalizes_canned_output(staged_env, monkeypatch):
    aid = _new_assessment(staged_env)
    payload = (
        '{"tool":"builtin-scanner","findings":['
        '{"file":"config.py","line":2,"type":"AWS Access Key",'
        '"severity":"critical","match_preview":"API_KEY = [REDACTED]"}]}'
    )
    monkeypatch.setattr(scanners, "_invoke_scanner", _canned(payload))

    result = scanners.run_secret_scan(aid, staged_path="/quarantine/x")
    assert result["success"] and result["findings_persisted"] == 1

    (r,) = _findings(aid)
    assert r["source_scanner"] == "secrets"
    assert r["finding_type"] == "secret"
    assert r["severity"] == "critical"
    assert r["file_path"] == "config.py"
    assert r["line"] == 2
    assert "AWS Access Key" in r["detail"]


def test_dependency_scan_normalizes_known_bad_manifest(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    staged = str(tmp_path / "x")
    # Mirrors dependency_auditor's CLI shape: {results: {lang: {findings:[...]}}}.
    payload = (
        '{"results":{"python":{"findings":['
        '{"package":"flask","version":"0.12.2","vulnerability_id":"PYSEC-2019-179",'
        '"description":"XSS in flask","severity":"high","fix_versions":["0.12.3"]},'
        '{"package":"requests","version":"2.19.1","vulnerability_id":"PYSEC-2018-28",'
        '"severity":"moderate"},'
        '{"package":"pyyaml","version":"3.13","vulnerability_id":"CVE-2017-18342",'
        '"severity":"unknown"}]}},"total_findings":3}'
    )
    fake = _canned(payload)
    monkeypatch.setattr(scanners, "_invoke_scanner", fake)

    result = scanners.run_dependency_scan(aid, staged_path=staged)
    assert result["success"] and result["findings_persisted"] == 3

    # The adapter targeted the dependency auditor in auto mode against the staged tree.
    assert any("dependency_auditor.py" in c for c in fake.cmd)
    assert "auto" in fake.cmd and staged in fake.cmd

    rows = _findings(aid)
    assert {r["finding_type"] for r in rows} == {"vuln_dependency"}
    assert {r["source_scanner"] for r in rows} == {"deps"}
    sev_by_pkg = {r["file_path"]: r["severity"] for r in rows}
    assert sev_by_pkg["flask==0.12.2"] == "high"
    assert sev_by_pkg["requests==2.19.1"] == "medium"   # moderate -> medium
    assert sev_by_pkg["pyyaml==3.13"] == "info"          # unknown -> info
    for r in rows:
        assert r["line"] is None
        assert "vulnerability_id" in r["detail"]


def test_sast_scan_normalizes_canned_output(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    staged = tmp_path / "x"
    app_py = staged / "app.py"  # absolute path, as a real SAST tool reports
    payload = (
        '{"all_findings":['
        '{"file":' + repr(str(app_py)).replace("'", '"') + ',"line":10,"severity":"HIGH",'
        '"test_id":"B602","test_name":"subprocess_with_shell",'
        '"issue_text":"shell=True identified","confidence":"HIGH","issue_cwe":{"id":78}}]}'
    )
    monkeypatch.setattr(scanners, "_invoke_scanner", _canned(payload))

    result = scanners.run_sast_scan(aid, staged_path=str(staged))
    assert result["success"] and result["findings_persisted"] == 1

    (r,) = _findings(aid)
    assert r["source_scanner"] == "sast"
    assert r["finding_type"] == "dangerous_api"
    assert r["severity"] == "high"          # HIGH -> high
    assert r["file_path"] == "app.py"        # relativized to staged root
    assert r["line"] == 10
    assert "shell=True" in r["detail"]


# --------------------------------------------------------------------------- #
# Resilience — a broken/absent scanner degrades gracefully (no crash, no rows)
# --------------------------------------------------------------------------- #
def test_scanner_failure_is_graceful(staged_env, monkeypatch):
    aid = _new_assessment(staged_env)

    def _broken(cmd, timeout):
        return 127, "", "scanner executable not found"

    monkeypatch.setattr(scanners, "_invoke_scanner", _broken)

    result = scanners.run_dependency_scan(aid, staged_path="/quarantine/x")
    assert result["success"] is False
    assert result["findings_persisted"] == 0
    assert result["error"]
    assert _findings(aid) == []


def test_unparseable_output_is_graceful(staged_env, monkeypatch):
    aid = _new_assessment(staged_env)
    monkeypatch.setattr(scanners, "_invoke_scanner", _canned("not json at all"))

    result = scanners.run_sast_scan(aid, staged_path="/quarantine/x")
    assert result["success"] is False
    assert _findings(aid) == []


# --------------------------------------------------------------------------- #
# scan_all — fan-out across enabled scanners + toggle respect
# --------------------------------------------------------------------------- #
def _dispatch_fake(secret_json, dep_json, sast_json):
    """A fake _invoke_scanner that returns the right canned JSON per scanner cmd."""
    def _fake(cmd, timeout):
        joined = " ".join(cmd)
        if "secret_detector.py" in joined:
            return 0, secret_json, ""
        if "dependency_auditor.py" in joined:
            return 0, dep_json, ""
        return 0, sast_json, ""  # the run_sast `-c` snippet
    return _fake


def test_scan_all_runs_every_enabled_scanner(staged_env, monkeypatch):
    aid = _new_assessment(staged_env)
    monkeypatch.setattr(
        scanners,
        "_invoke_scanner",
        _dispatch_fake(
            secret_json='{"findings":[{"file":"config.py","line":2,"type":"pw","severity":"high"}]}',
            dep_json='{"results":{"python":{"findings":['
            '{"package":"flask","version":"0.12.2","vulnerability_id":"V1","severity":"critical"}]}}}',
            sast_json='{"all_findings":[{"file":"main.py","line":1,"severity":"LOW","issue_text":"x"}]}',
        ),
    )

    result = scanners.scan_all(aid, staged_path="/quarantine/x")
    assert result["total_findings"] == 3
    assert set(result["scanners"]) == {"sast", "secrets", "deps"}
    for name in ("sast", "secrets", "deps"):
        assert result["scanners"][name]["success"]
        assert result["scanners"][name]["findings_persisted"] == 1

    scanners_seen = {r["source_scanner"] for r in _findings(aid)}
    assert scanners_seen == {"sast", "secrets", "deps"}


def test_scan_all_respects_disabled_toggle(staged_env, monkeypatch):
    aid = _new_assessment(staged_env)
    monkeypatch.setattr(
        scanners,
        "_invoke_scanner",
        _dispatch_fake(
            secret_json='{"findings":[]}',
            dep_json='{"results":{}}',
            sast_json='{"all_findings":[]}',
        ),
    )
    # Disable the deps scanner via config.
    monkeypatch.setattr(
        scanners, "_load_config", lambda: {"scanners": {"deps": False}}
    )

    result = scanners.scan_all(aid, staged_path="/quarantine/x")
    assert result["scanners"]["deps"].get("skipped") is True
    assert result["scanners"]["sast"].get("skipped") is not True
    assert result["scanners"]["secrets"].get("skipped") is not True
