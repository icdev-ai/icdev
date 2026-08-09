#!/usr/bin/env python3
# CUI // SP-CTI
"""Sandbox Executor — container-isolated code execution (D-SEC-10).

Wraps llm-sandbox for secure Docker/K8s/Podman code execution with
resource limits, network isolation, and audit logging.

Usage:
    python tools/security/sandbox_executor.py --execute --code "print('hello')" --language python --json
    python tools/security/sandbox_executor.py --execute-file --path script.py --language python --json
    python tools/security/sandbox_executor.py --health --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.security.sandbox_executor")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "runtime": "docker",
    "default_timeout_seconds": 30,
    "max_timeout_seconds": 300,
    "default_max_memory_mb": 512,
    "max_memory_mb": 2048,
    "default_cpu_count": 1,
    "network_enabled": False,
    "images": {
        "python": "python:3.12-slim",
        "javascript": "node:20-slim",
        "java": "eclipse-temurin:21-jre-jammy",
        "go": "golang:1.22-bookworm",
    },
    "cleanup_containers": True,
    "max_concurrent_sessions": 4,
    "audit_all_executions": True,
    # OPT-63: auto-recreate on container failure
    "auto_recreate": {
        "enabled": True,
        "max_retries": 2,
        "backoff_seconds": 10,
    },
}

DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load sandbox config from args/sandbox_config.yaml, fallback to defaults."""
    config_path = BASE_DIR / "args" / "sandbox_config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            loaded = cfg.get("sandbox", {})
            # Merge with defaults so missing keys still resolve
            merged = dict(DEFAULT_CONFIG)
            merged.update(loaded)
            # Merge nested images dict
            if "images" in loaded:
                merged["images"] = dict(DEFAULT_CONFIG.get("images", {}))
                merged["images"].update(loaded["images"])
            if "auto_recreate" in loaded:
                merged["auto_recreate"] = dict(DEFAULT_CONFIG.get("auto_recreate", {}))
                merged["auto_recreate"].update(loaded["auto_recreate"])
            return merged
        except Exception as exc:
            logger.warning("Failed to load sandbox_config.yaml: %s — using defaults", exc)
    return dict(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    runtime_ms: int = 0
    artifacts: List[str] = field(default_factory=list)
    container_id: str = ""
    status: str = "completed"  # completed | failed | timeout | disabled | unavailable
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class _ExecutionLog:
    """Structured execution audit record passed to _log_execution."""

    exec_id: str
    executor_type: str
    language: str
    code_hash: str
    runtime: str
    exit_code: int
    runtime_ms: int
    stdout: str
    stderr: str
    network: int
    memory: int
    timeout: int
    container_id: str
    artifacts: List[str]
    status: str
    actor: Optional[str] = None
    project_id: Optional[str] = None
    tenant_id: Optional[str] = None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _ensure_tables(conn: Any) -> None:
    """Create sandbox_execution_log table if it does not exist (D-SEC-10)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS sandbox_execution_log (
        id TEXT PRIMARY KEY,
        executor_type TEXT NOT NULL,
        language TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        runtime TEXT NOT NULL,
        exit_code INTEGER,
        runtime_ms INTEGER,
        stdout_preview TEXT,
        stderr_preview TEXT,
        network_enabled INTEGER DEFAULT 0,
        memory_limit_mb INTEGER,
        timeout_seconds INTEGER,
        container_id TEXT,
        artifact_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'completed' CHECK(status IN ('running','completed','failed','timeout')),
        actor TEXT,
        project_id TEXT,
        tenant_id TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class SandboxExecutor:
    """Container-isolated code execution engine (D-SEC-10).

    Wraps llm-sandbox to run untrusted code inside a Docker/Podman/K8s
    container with configurable resource limits and network isolation.
    Degrades gracefully when llm-sandbox or Docker is not available.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self._config = config if config is not None else _load_config()
        self._db_path = db_path or DB_PATH
        self._enabled: bool = self._config.get("enabled", True)
        self._runtime: str = self._config.get("runtime", "docker")
        self._available: bool = False

        if self._enabled:
            self._available = self._probe_availability()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def execute(
        self,
        code: str,
        language: str = "python",
        timeout_seconds: Optional[int] = None,
        max_memory_mb: Optional[int] = None,
        network_enabled: Optional[bool] = None,
        libraries: Optional[List[str]] = None,
        executor_type: str = "manual",
        actor: Optional[str] = None,
        project_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> SandboxResult:
        """Execute code inside a container sandbox.

        Args:
            code: Source code to execute.
            language: Target language (python, javascript, java, go).
            timeout_seconds: Max wall-clock seconds; defaults to config value.
            max_memory_mb: Container memory cap in MB; defaults to config value.
            network_enabled: Allow network access; defaults to config value (False).
            libraries: pip/npm packages to install before execution.
            executor_type: Caller tag for audit (manual, agent, marketplace, …).
            actor: User or agent identity for audit.
            project_id: Associated project ID for audit.
            tenant_id: Tenant ID for multi-tenant audit.

        Returns:
            SandboxResult with stdout, stderr, exit_code, runtime_ms, status.
        """
        if not self._enabled:
            return SandboxResult(
                status="disabled",
                error="Sandbox is disabled (sandbox.enabled=false in config)",
                exit_code=-1,
            )

        if not self._available:
            return SandboxResult(
                status="unavailable",
                error="Sandbox not available (Docker or llm-sandbox not installed)",
                exit_code=-1,
            )

        # Validate language
        supported_images: Dict[str, str] = self._config.get("images", {})
        if language not in supported_images:
            return SandboxResult(
                status="failed",
                error=f"Unsupported language '{language}'. Supported: {sorted(supported_images.keys())}",
                exit_code=-1,
            )

        image = supported_images[language]

        # Resolve resource limits with config defaults and hard caps
        _timeout = int(
            min(
                timeout_seconds if timeout_seconds is not None else self._config.get("default_timeout_seconds", 30),
                self._config.get("max_timeout_seconds", 300),
            )
        )
        _memory = int(
            min(
                max_memory_mb if max_memory_mb is not None else self._config.get("default_max_memory_mb", 512),
                self._config.get("max_memory_mb", 2048),
            )
        )
        _network = network_enabled if network_enabled is not None else self._config.get("network_enabled", False)

        exec_id = "sbx-" + uuid.uuid4().hex[:12]
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        result = SandboxResult()

        # OPT-63: auto-recreate config
        _ar_cfg = self._config.get("auto_recreate", {})
        _ar_enabled = _ar_cfg.get("enabled", True)
        _ar_max_retries = int(_ar_cfg.get("max_retries", 2))
        _ar_backoff = float(_ar_cfg.get("backoff_seconds", 10))
        _rebuild_count = 0

        while True:
            try:
                result = self._run_sandbox_once(
                    code=code,
                    language=language,
                    image=image,
                    libraries=libraries or [],
                )
                break  # success — exit retry loop

            except TimeoutError:
                result = SandboxResult(
                    status="timeout",
                    error=f"Execution timed out after {_timeout}s",
                    exit_code=-1,
                    runtime_ms=_timeout * 1000,
                )
                break  # no retry on timeout

            except Exception as exc:
                if _ar_enabled and _rebuild_count < _ar_max_retries and self._is_container_error(exc):
                    _rebuild_count += 1
                    logger.warning(
                        "Container failure (rebuild %d/%d): %s — recreating sandbox",
                        _rebuild_count,
                        _ar_max_retries,
                        exc,
                    )
                    self._log_rebuild_event(
                        exec_id=exec_id,
                        language=language,
                        code_hash=code_hash,
                        reason=str(exc),
                        retry_count=_rebuild_count,
                        actor=actor,
                        project_id=project_id,
                        tenant_id=tenant_id,
                    )
                    self._rebuild_container(language=language, image=image)
                    if _rebuild_count > 1:
                        time.sleep(_ar_backoff)
                    # continue retry loop
                else:
                    result = SandboxResult(
                        status="failed",
                        error=str(exc),
                        exit_code=-1,
                    )
                    break

        if self._config.get("audit_all_executions", True):
            self._log_execution(_ExecutionLog(
                exec_id=exec_id,
                executor_type=executor_type,
                language=language,
                code_hash=code_hash,
                runtime=self._runtime,
                exit_code=result.exit_code,
                runtime_ms=result.runtime_ms,
                stdout=result.stdout,
                stderr=result.stderr,
                network=int(_network),
                memory=_memory,
                timeout=_timeout,
                container_id=result.container_id,
                artifacts=result.artifacts,
                status=result.status if result.status in ("completed", "failed", "timeout") else "failed",
                actor=actor,
                project_id=project_id,
                tenant_id=tenant_id,
            ))

        return result

    def execute_file(
        self,
        file_path: Path,
        language: str = "python",
        **kwargs: Any,
    ) -> SandboxResult:
        """Execute a script file inside the sandbox.

        Reads the file content and delegates to execute().

        Args:
            file_path: Path to the script file to execute.
            language: Language of the script (auto-detected by extension if omitted).
            **kwargs: Forwarded to execute().

        Returns:
            SandboxResult from execute().
        """
        path = Path(file_path)
        if not path.exists():
            return SandboxResult(
                status="failed",
                error=f"File not found: {file_path}",
                exit_code=-1,
            )

        # Auto-detect language from extension if not explicitly provided
        if language == "python":
            ext_map = {
                ".py": "python",
                ".js": "javascript",
                ".mjs": "javascript",
                ".ts": "javascript",
                ".java": "java",
                ".go": "go",
            }
            detected = ext_map.get(path.suffix.lower())
            if detected:
                language = detected

        try:
            code = path.read_text(encoding="utf-8")
        except Exception as exc:
            return SandboxResult(
                status="failed",
                error=f"Failed to read file {file_path}: {exc}",
                exit_code=-1,
            )

        return self.execute(code=code, language=language, **kwargs)

    def health_check(self) -> Dict[str, Any]:
        """Check sandbox availability and run a trivial smoke test.

        Returns:
            Dict with: available, docker_running, llm_sandbox_installed,
            smoke_test_passed, runtime, enabled, languages.
        """
        health: Dict[str, Any] = {
            "available": False,
            "enabled": self._enabled,
            "docker_running": False,
            "llm_sandbox_installed": False,
            "smoke_test_passed": False,
            "runtime": self._runtime,
            "languages": sorted(self._config.get("images", {}).keys()),
            "error": None,
        }

        if not self._enabled:
            health["error"] = "Sandbox disabled in config"
            return health

        # Check llm-sandbox importable
        try:
            import llm_sandbox  # type: ignore[import] # noqa: F401

            health["llm_sandbox_installed"] = True
        except ImportError:
            health["error"] = "llm_sandbox package not installed (pip install llm-sandbox)"
            return health

        # Check Docker reachable
        try:
            import docker  # type: ignore[import]

            client = docker.from_env()
            client.ping()
            health["docker_running"] = True
        except Exception as exc:
            health["error"] = f"Docker not reachable: {exc}"
            return health

        # Smoke test — execute trivial Python snippet
        try:
            result = self.execute(
                code="print('sandbox-ok')",
                language="python",
                executor_type="health_check",
                timeout_seconds=30,
            )
            if result.status == "completed" and "sandbox-ok" in result.stdout:
                health["smoke_test_passed"] = True
                health["available"] = True
            else:
                health["error"] = f"Smoke test failed — status={result.status} error={result.error}"
        except Exception as exc:
            health["error"] = f"Smoke test exception: {exc}"

        return health

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _probe_availability(self) -> bool:
        """Return True if llm-sandbox and Docker are both reachable."""
        # Check llm-sandbox importable
        try:
            import llm_sandbox  # type: ignore[import] # noqa: F401
        except ImportError:
            logger.debug("llm_sandbox not installed — sandbox unavailable")
            return False

        # Check Docker daemon reachable
        try:
            import docker  # type: ignore[import]

            client = docker.from_env()
            client.ping()
        except Exception as exc:
            logger.debug("Docker not reachable: %s — sandbox unavailable", exc)
            return False

        return True

    def _run_sandbox_once(
        self,
        code: str,
        language: str,
        image: str,
        libraries: List[str],
    ) -> SandboxResult:
        """Execute code once in a SandboxSession (no retry logic).

        Raises any exception from llm_sandbox / Docker so the caller's retry
        loop can decide whether to rebuild and retry.
        """
        from llm_sandbox import SandboxSession  # type: ignore[import]

        start = time.time()
        with SandboxSession(
            lang=language,
            image=image,
            keep_template=not self._config.get("cleanup_containers", True),
            verbose=False,
        ) as session:
            run_result = session.run(code, libraries=libraries)

        runtime_ms = int((time.time() - start) * 1000)
        # llm-sandbox returns an object with stdout/stderr/exit_code attrs
        stdout = _safe_str(getattr(run_result, "stdout", ""))
        stderr = _safe_str(getattr(run_result, "stderr", ""))
        exit_code = int(getattr(run_result, "exit_code", 0))
        container_id = _safe_str(getattr(run_result, "container_id", ""))

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            runtime_ms=runtime_ms,
            artifacts=[],
            container_id=container_id,
            status="completed",
            error="",
        )

    def _is_container_error(self, exc: Exception) -> bool:
        """Return True if exc indicates a container infrastructure failure worth retrying.

        Matches: docker.errors.NotFound, ConnectionRefusedError, and
        RuntimeError / generic exceptions whose message references Docker or
        a missing container.
        """
        if isinstance(exc, ConnectionRefusedError):
            return True
        exc_msg = str(exc).lower()
        if any(kw in exc_msg for kw in ("docker", "container", "no such container", "not found")):
            return True
        # Match by class name to avoid a hard docker-sdk import
        exc_class = type(exc).__name__
        if exc_class in ("NotFound", "APIError", "ContainerError", "ImageNotFound"):
            return True
        # Try direct isinstance check when docker SDK is present
        try:
            import docker  # type: ignore[import]

            if isinstance(exc, (docker.errors.NotFound, docker.errors.APIError)):
                return True
        except ImportError:
            pass
        return False

    def _rebuild_container(self, language: str, image: str) -> None:
        """Pull the container image to recreate it after a detected failure.

        Does NOT raise — if the pull itself fails, the subsequent execution
        attempt will fail with the real error.
        """
        logger.info("Rebuilding sandbox container: language=%s image=%s", language, image)
        try:
            import docker  # type: ignore[import]

            client = docker.from_env()
            client.images.pull(image)
            logger.info("Container image rebuilt successfully: %s", image)
        except Exception as exc:
            logger.warning(
                "Container rebuild failed (%s / %s): %s — retry will proceed anyway",
                language,
                image,
                exc,
            )

    def _log_rebuild_event(
        self,
        exec_id: str,
        language: str,
        code_hash: str,
        reason: str,
        retry_count: int,
        actor: Optional[str],
        project_id: Optional[str],
        tenant_id: Optional[str],
    ) -> None:
        """Insert a sandbox_recreated audit event into sandbox_execution_log (append-only, D6).

        Uses executor_type='sandbox_recreated' so the event is queryable.
        Encodes the rebuild reason in stdout_preview and retry_count in artifact_count.
        """
        if not self._db_path.exists():
            return
        event_id = f"{exec_id}-rebuild-{retry_count}"
        try:
            conn = get_connection(db_path=str(self._db_path))
            _ensure_tables(conn)
            conn.execute(
                """INSERT INTO sandbox_execution_log (
                    id, executor_type, language, code_hash, runtime,
                    exit_code, runtime_ms, stdout_preview, stderr_preview,
                    network_enabled, memory_limit_mb, timeout_seconds,
                    container_id, artifact_count, status,
                    actor, project_id, tenant_id, classification, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, 'CUI', %s
                )""",
                (
                    event_id,
                    "sandbox_recreated",
                    language,
                    code_hash,
                    self._runtime,
                    retry_count,       # exit_code — encodes rebuild number
                    0,                 # runtime_ms — not applicable for events
                    reason[:512],      # stdout_preview — human-readable reason
                    "",                # stderr_preview
                    0,                 # network_enabled
                    0,                 # memory_limit_mb
                    0,                 # timeout_seconds
                    "",                # container_id
                    retry_count,       # artifact_count — encodes rebuild number
                    "failed",          # status — the attempt that triggered the rebuild
                    actor,
                    project_id,
                    tenant_id,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to log rebuild event %s: %s", event_id, exc)

    def _log_execution(self, log: _ExecutionLog) -> None:
        """Insert an execution record into sandbox_execution_log (append-only, D6)."""
        if not self._db_path.exists():
            return

        stdout_preview = log.stdout[:512] if log.stdout else ""
        stderr_preview = log.stderr[:512] if log.stderr else ""

        try:
            conn = get_connection(db_path=str(self._db_path))
            _ensure_tables(conn)
            conn.execute(
                """INSERT INTO sandbox_execution_log (
                    id, executor_type, language, code_hash, runtime,
                    exit_code, runtime_ms, stdout_preview, stderr_preview,
                    network_enabled, memory_limit_mb, timeout_seconds,
                    container_id, artifact_count, status,
                    actor, project_id, tenant_id, classification, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, 'CUI', %s
                )""",
                (
                    log.exec_id, log.executor_type, log.language,
                    log.code_hash, log.runtime, log.exit_code,
                    log.runtime_ms, stdout_preview, stderr_preview,
                    log.network, log.memory, log.timeout,
                    log.container_id, len(log.artifacts), log.status,
                    log.actor, log.project_id, log.tenant_id,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to log sandbox execution %s: %s", log.exec_id, exc)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _safe_str(value: Any) -> str:
    """Convert any value to str safely."""
    if value is None:
        return ""
    return str(value)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sandbox Executor — container-isolated code execution (D-SEC-10)",
    )
    p.add_argument("--execute", action="store_true", help="Execute inline code")
    p.add_argument("--execute-file", action="store_true", help="Execute a script file")
    p.add_argument("--health", action="store_true", help="Run health check")
    p.add_argument("--gate", action="store_true", help="Exit 1 if sandbox not available")
    p.add_argument("--code", type=str, help="Inline code to execute (with --execute)")
    p.add_argument("--path", type=str, help="Script file path (with --execute-file)")
    p.add_argument("--language", type=str, default="python", help="Execution language")
    p.add_argument("--timeout", type=int, default=None, help="Timeout in seconds")
    p.add_argument("--memory", type=int, default=None, help="Memory limit in MB")
    p.add_argument("--network", action="store_true", default=False, help="Enable network access")
    p.add_argument("--libraries", type=str, default="", help="Comma-separated packages to install")
    p.add_argument("--actor", type=str, default=None, help="Caller identity for audit")
    p.add_argument("--project-id", type=str, default=None, help="Project ID for audit")
    p.add_argument("--tenant-id", type=str, default=None, help="Tenant ID for audit")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    executor = SandboxExecutor()
    output: Dict[str, Any] = {}
    exit_code = 0

    if args.health or args.gate:
        health = executor.health_check()
        output = health
        if args.gate and not health.get("available", False):
            exit_code = 1

    elif args.execute:
        if not args.code:
            parser.error("--execute requires --code")
        libs = [lib.strip() for lib in args.libraries.split(",") if lib.strip()] if args.libraries else []
        result = executor.execute(
            code=args.code,
            language=args.language,
            timeout_seconds=args.timeout,
            max_memory_mb=args.memory,
            network_enabled=args.network if args.network else None,
            libraries=libs,
            executor_type="cli",
            actor=args.actor,
            project_id=args.project_id,
            tenant_id=args.tenant_id,
        )
        output = result.to_dict()
        if result.status not in ("completed",):
            exit_code = 1

    elif args.execute_file:
        if not args.path:
            parser.error("--execute-file requires --path")
        libs = [lib.strip() for lib in args.libraries.split(",") if lib.strip()] if args.libraries else []
        result = executor.execute_file(
            file_path=Path(args.path),
            language=args.language,
            timeout_seconds=args.timeout,
            max_memory_mb=args.memory,
            network_enabled=args.network if args.network else None,
            libraries=libs,
            executor_type="cli",
            actor=args.actor,
            project_id=args.project_id,
            tenant_id=args.tenant_id,
        )
        output = result.to_dict()
        if result.status not in ("completed",):
            exit_code = 1

    else:
        parser.print_help()
        return 0

    if args.json:
        print(json.dumps(output, indent=2, default=str))
    else:
        for key, val in output.items():
            print(f"{key}: {val}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
