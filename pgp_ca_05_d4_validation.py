#!/usr/bin/env python3
"""pgp-ca-05-d4 validation harness (v4 — final).

Validates the SQLite-fallback contract for child app init:

  PHASE A — Subprocess, PG host non-routable
    The auto-generated child app init script is invoked with
    ICDEV_STORAGE_BACKEND=postgresql and ICDEV_DATABASE_URL pointed at
    RFC 5737 TEST-NET-1 (192.0.2.1:65530). The init script's helper
    routes through tools.db.storage.get_connection() with a `.db` path,
    which (by design) takes the canvas/aux SQLite branch. The script
    must succeed: exit 0, .db file on disk, all 12 core tables present,
    no ConnectionError.

  PHASE B — In-process, NO_FALLBACK unset (silent fallback path)
    Reload env (clear ICDEV_PG_NO_FALLBACK, point URL at bad host), then
    call icdev.tools.db.storage.get_connection(db_path="") — empty
    db_path forces the main-backend branch. The call must NOT raise and
    must return a sqlite-backed StorageConnection.

  PHASE C — In-process, NO_FALLBACK=true (refusal path)
    Set ICDEV_PG_NO_FALLBACK=true, point URL at bad host, call
    get_connection(db_path="") — must raise ConnectionError whose
    message mentions NO_FALLBACK.

  PHASE D — In-process, local PG reachable (positive control)
    With NO_FALLBACK unset and URL pointed at the working localhost PG,
    get_connection(db_path="") must return a postgresql-backed
    StorageConnection (proves we don't fall back when PG is up).
"""
# CUI // SP-CTI

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(r"C:\AI\ICDev")
ARTIFACT_DIR = REPO_ROOT / "playwright" / "screenshots" / "artifacts" / "pgp-ca-05-d4"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = ARTIFACT_DIR / "validation.log"
RESULT_FILE = ARTIFACT_DIR / "result.json"

BAD_HOST = "192.0.2.1"      # RFC 5737 TEST-NET-1 (non-routable)
BAD_PORT = "65530"
BAD_URL = f"postgresql://icdev:invalid@{BAD_HOST}:{BAD_PORT}/icdev"


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def phase(label: str) -> None:
    log("")
    log(f"### {label}")


def find_local_pg_url() -> str | None:
    """Read localhost PG URL from the project's .env file, NOT from the
    live os.environ (which we mutate during PHASE B/C).
    """
    env_path = REPO_ROOT / ".env"
    db_url: str | None = None
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ICDEV_DATABASE_URL=") and "postgresql://" in line:
                db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not db_url:
        db_url = "postgresql://icdev:icdev-local-dev@localhost:5432/icdev"
    try:
        rest = db_url.split("postgresql://", 1)[1]
        _user_pw, host_db = rest.rsplit("@", 1)
        host_port, db = host_db.split("/", 1)
        host, port = host_port.split(":")
        port = int(port)
    except Exception:
        return None
    try:
        with socket.create_connection((host, port), timeout=2):
            return db_url
    except Exception as exc:
        log(f"local PG not reachable on {host}:{port}: {exc}")
        return None


def set_env(backend: str, url: str, no_fallback: bool | None) -> None:
    """Set the three storage env vars in-process.

    no_fallback=None  -> unset (clear)
    no_fallback=True  -> set to "true"
    no_fallback=False -> set to "false"
    """
    os.environ["ICDEV_STORAGE_BACKEND"] = backend
    os.environ["ICDEV_DATABASE_URL"] = url
    if no_fallback is None:
        os.environ.pop("ICDEV_PG_NO_FALLBACK", None)
    else:
        os.environ["ICDEV_PG_NO_FALLBACK"] = "true" if no_fallback else "false"


def main() -> int:
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    log("=== pgp-ca-05-d4 SQLite fallback validation (v4) start ===")
    log(f"REPO_ROOT: {REPO_ROOT}")

    sys.path.insert(0, str(REPO_ROOT))
    from tools.builder.db_init_generator import write_init_script  # type: ignore

    blueprint = {"app_name": "pgp_ca_05_d4_child", "classification": "CUI", "capabilities": {}}
    work_dir = Path(tempfile.mkdtemp(prefix="pgp_ca_05_d4_v4_"))
    log(f"work_dir: {work_dir}")
    script_path = write_init_script(blueprint, work_dir)
    log(f"generated init script: {script_path}")
    assert script_path.exists()

    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    # ============== PHASE A — subprocess: PG unreachable, child app init ==============
    phase("PHASE A — subprocess, PG host non-routable, child app init must succeed via sqlite")

    env_a = os.environ.copy()
    env_a["PYTHONPATH"] = str(REPO_ROOT)
    env_a["ICDEV_STORAGE_BACKEND"] = "postgresql"
    env_a["ICDEV_DATABASE_URL"] = BAD_URL
    env_a["ICDEV_PG_HOST"] = BAD_HOST
    env_a["ICDEV_PG_PORT"] = BAD_PORT
    env_a.pop("ICDEV_PG_NO_FALLBACK", None)
    env_a["PGCONNECT_TIMEOUT"] = "3"
    env_a["PYTHONUNBUFFERED"] = "1"

    target_db_a = work_dir / "data" / "phase_a_child.db"
    target_db_a.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(script_path), "--db-path", str(target_db_a)]
    log(f"cmd: {' '.join(cmd)}")
    started = time.time()
    res_a = subprocess.run(cmd, capture_output=True, text=True, env=env_a, timeout=60)
    log(f"exit_code={res_a.returncode} duration={time.time() - started:.2f}s")
    log("stdout:\n" + (res_a.stdout or "<empty>").rstrip())
    log("stderr:\n" + (res_a.stderr or "<empty>").rstrip())

    checks["A_no_crash"] = res_a.returncode == 0
    checks["A_db_created"] = target_db_a.exists() and target_db_a.stat().st_size > 0
    if checks["A_db_created"]:
        with sqlite3.connect(str(target_db_a)) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()]
        details["A_sqlite_tables"] = tables
        details["A_sqlite_size_bytes"] = target_db_a.stat().st_size
        expected_core = {"projects", "agents", "a2a_tasks", "audit_trail",
                         "knowledge_patterns", "alerts", "deployments",
                         "maintenance_audits", "metric_snapshots",
                         "self_healing_events", "code_reviews", "tasks"}
        checks["A_core_tables"] = expected_core.issubset(set(tables))
    else:
        details["A_sqlite_tables"] = []
        checks["A_core_tables"] = False
    checks["A_no_connectionerror"] = "ConnectionError" not in res_a.stderr

    # ============== PHASE B — in-process: PG unreachable, NO_FALLBACK unset ==============
    phase("PHASE B — in-process get_connection(), NO_FALLBACK unset, expect silent sqlite fallback")

    # storage.py re-loads .env at module level. To exercise the "unset"
    # branch we need to set the env AFTER module import. storage.py reads
    # os.environ on every call (not cached), so once it's loaded once, we
    # can mutate os.environ in place and the next call sees our value.
    import icdev.tools.db.storage as storage_mod  # type: ignore

    set_env("postgresql", BAD_URL, no_fallback=None)
    log(f"DEBUG pre-call NO_FALLBACK={os.environ.get('ICDEV_PG_NO_FALLBACK')!r}")

    conn_b = None
    try:
        conn_b = storage_mod.get_connection(db_path="")
    except Exception as exc:
        log(f"B get_connection raised: {type(exc).__name__}: {exc}")
        checks["B_no_raise"] = False
    else:
        checks["B_no_raise"] = True
        backend_b = getattr(conn_b, "_backend", "unknown")
        log(f"B backend={backend_b}, conn type={type(conn_b).__name__}")
        checks["B_backend_sqlite"] = backend_b == "sqlite"
        try:
            row = conn_b.execute("SELECT 1 AS x").fetchone()
            log(f"B round-trip row: {row}")
            checks["B_usable"] = row is not None
        except Exception as exc:  # pragma: no cover
            log(f"B round-trip failed: {exc}")
            checks["B_usable"] = False
        try:
            conn_b.close()
        except Exception:
            pass

    # ============== PHASE C — in-process: NO_FALLBACK=true must raise ==============
    phase("PHASE C — in-process get_connection(), NO_FALLBACK=true, expect ConnectionError")

    set_env("postgresql", BAD_URL, no_fallback=True)
    log(f"DEBUG pre-call NO_FALLBACK={os.environ.get('ICDEV_PG_NO_FALLBACK')!r}")

    raised_exc: Exception | None = None
    try:
        storage_mod.get_connection(db_path="")
    except ConnectionError as exc:
        raised_exc = exc
        log(f"C raised ConnectionError as expected: {exc}")
    except Exception as exc:  # noqa: BLE001
        raised_exc = exc
        log(f"C raised UNEXPECTED {type(exc).__name__}: {exc}")

    checks["C_raises_connectionerror"] = isinstance(raised_exc, ConnectionError)
    checks["C_message_mentions_no_fallback"] = bool(raised_exc) and "NO_FALLBACK" in str(raised_exc)
    details["C_exception_type"] = type(raised_exc).__name__ if raised_exc else None
    details["C_exception_message"] = str(raised_exc) if raised_exc else None

    # ============== PHASE D — local PG reachable: must use postgresql, NOT fallback ==============
    phase("PHASE D — in-process get_connection(), local PG up, expect postgresql backend")

    pg_url = find_local_pg_url()
    details["D_local_pg_url"] = pg_url

    if pg_url is None:
        log("local PG not running — skipping PHASE D (informational only)")
        checks["D_skipped"] = True
    else:
        set_env("postgresql", pg_url, no_fallback=None)
        # Force main-backend branch (canvas/aux branch only fires on .db path)
        os.environ["ICDEV_DB_PATH"] = "/non/existent/path/marker.db"

        try:
            conn_d = storage_mod.get_connection(db_path="")
            backend_d = getattr(conn_d, "_backend", "unknown")
            log(f"D backend={backend_d}")
            checks["D_backend_postgresql"] = backend_d == "postgresql"
            try:
                conn_d.close()
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover
            log(f"D raised unexpectedly: {type(exc).__name__}: {exc}")
            checks["D_backend_postgresql"] = False

    # ============== summary ==============
    phase("SUMMARY")
    substantive_checks = {k: v for k, v in checks.items() if not k.endswith("_skipped")}
    passed = all(substantive_checks.values())

    summary = {
        "task_id": "pgp-ca-05-d4",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "pg_target_unreachable": BAD_URL,
        "pg_target_local": pg_url,
        "checks": checks,
        "details": details,
        "passed": passed,
    }
    RESULT_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"result json: {RESULT_FILE}")
    log(f"checks: {checks}")
    log(f"PASSED: {passed}")

    if not passed:
        log("VALIDATION FAILED — see checks above")
        return 1
    log("VALIDATION PASSED — child app falls back to SQLite when PG is unreachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
