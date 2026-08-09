# CUI // SP-CTI
"""Sandbox Manager — Stage 5 of Connector Forge pipeline (D-CF-1).

Docker primary sandbox (--network none, memory/cpu limits).
Python subprocess fallback (restricted PYTHONPATH, timeout).
Both paths log to db_forge_sandbox_log (append-only, NIST AU).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import subprocess
import sys
import tempfile
import time
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from tools.db.storage import get_connection

logger = get_logger("databridge.forge.sandbox_manager")

DB_PATH = Path(__file__).resolve().parents[3] / "data" / "icdev.db"

# Test harness injected alongside generated connector code
_SANDBOX_HARNESS = textwrap.dedent("""\
    import json, sys, importlib.util

    result = {"status": "ok", "events": []}
    try:
        spec = importlib.util.spec_from_file_location("forge_connector", sys.argv[1])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result["events"].append({"type": "import", "status": "ok"})

        # Find DataConnector subclass
        connector_cls = None
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and hasattr(obj, 'connector_name'):
                if name != 'DataConnector' and not name.endswith('BaseConnector'):
                    connector_cls = obj
                    break

        if not connector_cls:
            result["status"] = "error"
            result["error"] = "No DataConnector subclass found"
        else:
            instance = connector_cls()
            result["events"].append({"type": "instantiate", "status": "ok", "class": connector_cls.__name__})
            try:
                connected = instance.connect({})
                result["events"].append({"type": "connect", "status": "ok" if connected else "skipped"})
            except Exception as e:
                result["events"].append({"type": "connect", "status": "skipped", "reason": str(e)[:200]})
            try:
                health = instance.health_check()
                result["events"].append({"type": "health", "status": "ok", "result": str(health)[:200]})
            except Exception as e:
                result["events"].append({"type": "health", "status": "skipped", "reason": str(e)[:200]})
            try:
                tables = instance.list_tables()
                result["events"].append({"type": "list_tables", "status": "ok", "count": len(tables)})
            except Exception as e:
                result["events"].append({"type": "list_tables", "status": "skipped", "reason": str(e)[:200]})
            try:
                instance.disconnect()
            except Exception:
                pass
            result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:500]

    print(json.dumps(result))
""")


def run_sandbox(
    connector_id: str,
    code: str,
    connector_name: str,
    config: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute generated connector code in a sandbox.

    Tries Docker first, falls back to subprocess.
    Returns: {sandbox_type, status, events, duration_ms, error}
    """
    if _docker_available():
        result = _run_docker_sandbox(connector_id, code, connector_name, db_path)
        if result.get("status") != "docker_unavailable":
            return result

    return _run_subprocess_sandbox(connector_id, code, connector_name, db_path)


def _docker_available() -> bool:
    """Check if Docker daemon is running."""
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_docker_sandbox(
    connector_id: str,
    code: str,
    connector_name: str,
    db_path: Optional[str],
) -> Dict[str, Any]:
    """Run connector in Docker container (network=none, memory=256m)."""
    start = time.time()
    _log_event(connector_id, "docker", "start", f"Docker sandbox for {connector_name}", db_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        (tmp / "connector.py").write_text(code, encoding="utf-8", newline="")
        (tmp / "harness.py").write_text(_SANDBOX_HARNESS, encoding="utf-8", newline="")

        cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "--read-only",
            "--tmpfs",
            "/tmp",  # nosec B108 -- temp path for ephemeral scratch files
            "-v",
            f"{tmp_dir}:/sandbox:ro",
            "python:3.11-slim",
            "python",
            "/sandbox/harness.py",
            "/sandbox/connector.py",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            duration_ms = int((time.time() - start) * 1000)

            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    result = json.loads(proc.stdout.strip().split("\n")[-1])
                    _log_event(connector_id, "docker", "stop", f"Completed: {result['status']}", db_path, duration_ms)
                    return {
                        "sandbox_type": "docker",
                        "status": result["status"],
                        "events": result.get("events", []),
                        "duration_ms": duration_ms,
                        "error": result.get("error", ""),
                    }
                except json.JSONDecodeError:
                    pass

            _log_event(connector_id, "docker", "error", proc.stderr[:500], db_path, duration_ms)
            return {
                "sandbox_type": "docker",
                "status": "error",
                "events": [],
                "duration_ms": duration_ms,
                "error": proc.stderr[:500] or "No output from container",
            }
        except subprocess.TimeoutExpired:
            dur = int((time.time() - start) * 1000)
            _log_event(connector_id, "docker", "error", "Timed out", db_path, dur)
            return {
                "sandbox_type": "docker",
                "status": "timeout",
                "events": [],
                "duration_ms": dur,
                "error": "Docker sandbox timed out (60s)",
            }
        except FileNotFoundError:
            return {
                "sandbox_type": "docker",
                "status": "docker_unavailable",
                "events": [],
                "duration_ms": 0,
                "error": "docker not found",
            }


def _run_subprocess_sandbox(
    connector_id: str,
    code: str,
    connector_name: str,
    db_path: Optional[str],
) -> Dict[str, Any]:
    """Fallback: run connector in restricted subprocess."""
    start = time.time()
    _log_event(connector_id, "subprocess", "start", f"Subprocess sandbox for {connector_name}", db_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        (tmp / "connector.py").write_text(code, encoding="utf-8", newline="")
        (tmp / "harness.py").write_text(_SANDBOX_HARNESS, encoding="utf-8", newline="")

        project_root = str(Path(__file__).resolve().parents[3])
        import os

        env = {**os.environ, "PYTHONPATH": project_root}

        try:
            proc = subprocess.run(
                [sys.executable, str(tmp / "harness.py"), str(tmp / "connector.py")],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=project_root,
            )
            duration_ms = int((time.time() - start) * 1000)

            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    # Take last line (harness prints JSON as last line)
                    result = json.loads(proc.stdout.strip().split("\n")[-1])
                    _log_event(
                        connector_id, "subprocess", "stop", f"Completed: {result['status']}", db_path, duration_ms
                    )
                    return {
                        "sandbox_type": "subprocess",
                        "status": result["status"],
                        "events": result.get("events", []),
                        "duration_ms": duration_ms,
                        "error": result.get("error", ""),
                    }
                except json.JSONDecodeError:
                    pass

            _log_event(connector_id, "subprocess", "error", proc.stderr[:500], db_path, duration_ms)
            return {
                "sandbox_type": "subprocess",
                "status": "error",
                "events": [],
                "duration_ms": duration_ms,
                "error": proc.stderr[:500] or proc.stdout[:500],
            }
        except subprocess.TimeoutExpired:
            dur = int((time.time() - start) * 1000)
            _log_event(connector_id, "subprocess", "error", "Timed out", db_path, dur)
            return {
                "sandbox_type": "subprocess",
                "status": "timeout",
                "events": [],
                "duration_ms": dur,
                "error": "Subprocess timed out (30s)",
            }


def _log_event(
    connector_id: str,
    sandbox_type: str,
    event_type: str,
    details: str,
    db_path: Optional[str],
    duration_ms: int = 0,
) -> None:
    """Append sandbox event to db_forge_sandbox_log."""
    db_path or str(DB_PATH)
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO db_forge_sandbox_log
               (connector_id, sandbox_type, event_type, details, duration_ms, logged_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                connector_id,
                sandbox_type,
                event_type,
                details[:2000],
                duration_ms,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Sandbox log write failed: %s", exc)
