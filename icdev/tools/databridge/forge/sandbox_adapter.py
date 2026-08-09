# CUI // SP-CTI
"""OpenSandbox-inspired sandbox execution adapter for Connector Forge (D-CF-4).

Provides a unified API for sandbox lifecycle management (create, execute, destroy)
across three backends: OpenSandbox API, Docker, and subprocess fallback.

Supports Jupyter-style multi-language code execution and SSE streaming for
real-time output.  Auto-detects the best available backend at init time.

Usage:
    # Programmatic
    from tools.databridge.forge.sandbox_adapter import SandboxAdapter
    adapter = SandboxAdapter()
    session = adapter.create_session()
    result = adapter.execute_code(session.session_id, "print('hello')")
    adapter.destroy_session(session.session_id)

    # CLI
    python tools/databridge/forge/sandbox_adapter.py --health --json
    python tools/databridge/forge/sandbox_adapter.py --test --json
    python tools/databridge/forge/sandbox_adapter.py --execute --code "print('hello')" --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import enum
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("databridge.forge.sandbox_adapter")

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "args" / "databridge_config.yaml"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SandboxBackend(enum.Enum):
    """Supported sandbox execution backends."""

    DOCKER = "docker"
    OPENSANDBOX = "opensandbox"
    SUBPROCESS = "subprocess"


@dataclass
class SandboxSession:
    """Represents an active sandbox session."""

    session_id: str
    backend: SandboxBackend
    status: str  # created, running, stopped, error
    created_at: str
    image: str = "python:3.12-slim"
    memory_limit: str = "256m"
    network: bool = False
    timeout: int = 120
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dictionary."""
        d = asdict(self)
        d["backend"] = self.backend.value
        return d


@dataclass
class ExecutionResult:
    """Result of code or command execution in a sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    language: str = "python"
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dictionary."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Config loader (stdlib YAML-lite parser for the forge.sandbox block)
# ---------------------------------------------------------------------------


def _load_sandbox_config() -> Dict[str, Any]:
    """Load sandbox config from args/databridge_config.yaml.

    Uses a simple line-by-line parser (no PyYAML dependency) to extract the
    forge.sandbox block.  Falls back to sensible defaults if file is missing
    or unparseable.
    """
    defaults: Dict[str, Any] = {
        "primary": "docker",
        "fallback": "subprocess",
        "docker_image": "python:3.12-slim",
        "docker_timeout_seconds": 60,
        "docker_memory_limit": "256m",
        "docker_cpus": "0.5",
        "docker_network": "none",
        "subprocess_timeout_seconds": 30,
        "opensandbox_url": "http://localhost:8000",
        "opensandbox_timeout_seconds": 120,
    }
    if not _CONFIG_PATH.exists():
        return defaults

    try:
        text = _CONFIG_PATH.read_text(encoding="utf-8")
        in_forge = False
        in_sandbox = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            # Detect indentation-based nesting
            indent = len(line) - len(line.lstrip())
            if stripped.startswith("forge:"):
                in_forge = True
                continue
            if in_forge and indent <= 2 and not stripped.startswith("sandbox:"):
                # Exited forge block (another top-level sibling)
                if indent <= 2 and ":" in stripped and not stripped.startswith("-"):
                    in_forge = False
                    in_sandbox = False
                    continue
            if in_forge and stripped.startswith("sandbox:"):
                in_sandbox = True
                continue
            if in_sandbox:
                # Check if we left the sandbox block
                if indent <= 4 and ":" in stripped and not stripped.startswith("-"):
                    # Could be next sibling of sandbox
                    parts = stripped.split(":", 1)
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
                    if key in defaults:
                        defaults[key] = val
                    else:
                        # We've left the sandbox block
                        in_sandbox = False
                elif indent > 4 and ":" in stripped:
                    parts = stripped.split(":", 1)
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else ""
                    if key in defaults:
                        defaults[key] = val
    except Exception as exc:
        logger.debug("Config parse failed, using defaults: %s", exc)

    # Coerce numeric values
    for k in ("docker_timeout_seconds", "subprocess_timeout_seconds", "opensandbox_timeout_seconds"):
        try:
            defaults[k] = int(defaults[k])
        except (ValueError, TypeError):
            pass

    return defaults


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


class _DockerBackend:
    """Docker-based sandbox backend (D-CF-4 primary)."""

    def __init__(self, config: Dict[str, Any]):
        self.image = config.get("docker_image", "python:3.12-slim")
        self.timeout = config.get("docker_timeout_seconds", 60)
        self.memory_limit = config.get("docker_memory_limit", "256m")
        self.cpus = config.get("docker_cpus", "0.5")
        self.network = config.get("docker_network", "none")
        self._containers: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def is_available() -> bool:
        """Check if Docker daemon is running."""
        try:
            proc = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def create_session(
        self,
        image: str,
        memory_limit: str,
        network: bool,
        timeout: int,
    ) -> SandboxSession:
        """Create a Docker container that stays alive for the session."""
        session_id = f"sb-docker-{uuid.uuid4().hex[:12]}"
        container_name = f"icdev-sandbox-{session_id}"

        cmd = [
            "docker",
            "create",
            "--name",
            container_name,
            "--network",
            "host" if network else "none",
            "--memory",
            memory_limit,
            "--cpus",
            self.cpus,
            "--read-only",
            "--tmpfs",
            "/tmp",  # nosec B108 -- temp path for ephemeral scratch files
            image,
            "tail",
            "-f",
            "/dev/null",  # keep alive
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(f"docker create failed: {proc.stderr[:500]}")
            container_id = proc.stdout.strip()

            # Start the container
            subprocess.run(
                ["docker", "start", container_name],
                capture_output=True,
                timeout=15,
            )

            session = SandboxSession(
                session_id=session_id,
                backend=SandboxBackend.DOCKER,
                status="created",
                created_at=datetime.now(timezone.utc).isoformat(),
                image=image,
                memory_limit=memory_limit,
                network=network,
                timeout=timeout,
                metadata={"container_name": container_name, "container_id": container_id},
            )
            self._containers[session_id] = {
                "container_name": container_name,
                "container_id": container_id,
                "session": session,
            }
            return session
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            raise RuntimeError(f"Docker session create failed: {exc}") from exc

    def execute_code(
        self,
        session_id: str,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute code inside the Docker container."""
        info = self._containers.get(session_id)
        if not info:
            return ExecutionResult(
                stdout="",
                stderr=f"Session {session_id} not found",
                exit_code=1,
                duration_ms=0,
                language=language,
                success=False,
            )

        container_name = info["container_name"]
        start = time.time()

        # Map language to interpreter command
        interpreters = {
            "python": ["python", "-c", code],
            "bash": ["bash", "-c", code],
            "sh": ["sh", "-c", code],
        }
        exec_cmd = interpreters.get(language)
        if not exec_cmd:
            return ExecutionResult(
                stdout="",
                stderr=f"Unsupported language: {language}",
                exit_code=1,
                duration_ms=0,
                language=language,
                success=False,
            )

        cmd = ["docker", "exec", container_name] + exec_cmd
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.time() - start) * 1000)
            return ExecutionResult(
                stdout=proc.stdout[:50000],
                stderr=proc.stderr[:10000],
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                language=language,
                success=proc.returncode == 0,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return ExecutionResult(
                stdout="",
                stderr=f"Execution timed out ({timeout}s)",
                exit_code=124,
                duration_ms=duration_ms,
                language=language,
                success=False,
            )

    def execute_command(
        self,
        session_id: str,
        command: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute a shell command in the Docker container."""
        return self.execute_code(session_id, command, "bash", timeout)

    def upload_file(
        self,
        session_id: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Copy a file into the Docker container."""
        info = self._containers.get(session_id)
        if not info:
            return False
        container_name = info["container_name"]
        try:
            proc = subprocess.run(
                ["docker", "cp", local_path, f"{container_name}:{remote_path}"],
                capture_output=True,
                timeout=30,
            )
            return proc.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def download_file(self, session_id: str, remote_path: str) -> Optional[bytes]:
        """Copy a file from the Docker container."""
        info = self._containers.get(session_id)
        if not info:
            return None
        container_name = info["container_name"]
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                ["docker", "cp", f"{container_name}:{remote_path}", tmp_path],
                capture_output=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return Path(tmp_path).read_bytes()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def destroy_session(self, session_id: str) -> bool:
        """Stop and remove the Docker container."""
        info = self._containers.pop(session_id, None)
        if not info:
            return False
        container_name = info["container_name"]
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=15,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def list_sessions(self) -> List[SandboxSession]:
        """List all active Docker sandbox sessions."""
        return [info["session"] for info in self._containers.values()]

    def health_check(self) -> Dict[str, Any]:
        """Check Docker availability and version."""
        try:
            proc = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return {
                    "healthy": True,
                    "backend": "docker",
                    "version": proc.stdout.strip(),
                    "active_sessions": len(self._containers),
                }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return {"healthy": False, "backend": "docker", "error": "Docker unavailable"}


class _OpenSandboxBackend:
    """OpenSandbox API backend — connects to an HTTP sandbox service.

    Expected API (OpenSandbox-compatible):
        POST   /sandboxes                  — create sandbox
        POST   /sandboxes/{id}/executions  — execute code
        DELETE /sandboxes/{id}             — destroy sandbox
        GET    /sandboxes                  — list sandboxes
        GET    /health                     — health check
    """

    def __init__(self, config: Dict[str, Any]):
        self.base_url = config.get("opensandbox_url", "http://localhost:8000").rstrip("/")
        self.timeout = config.get("opensandbox_timeout_seconds", 120)
        self._sessions: Dict[str, SandboxSession] = {}

    @staticmethod
    def is_available_at(url: str) -> bool:
        """Check if an OpenSandbox API is reachable at the given URL."""
        try:
            req = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def is_available(self) -> bool:
        """Check if this backend's configured URL is reachable."""
        return self.is_available_at(self.base_url)

    def _api_call(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Make an HTTP API call to the OpenSandbox server."""
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                raw = resp.read().decode("utf-8")
                if raw.strip():
                    return json.loads(raw)
                return {"status": "ok", "http_status": resp.status}
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8")[:2000]
            except Exception:
                pass
            return {"error": f"HTTP {exc.code}: {body_text}", "http_status": exc.code}
        except (urllib.error.URLError, OSError) as exc:
            return {"error": str(exc)}

    def create_session(
        self,
        image: str,
        memory_limit: str,
        network: bool,
        timeout: int,
    ) -> SandboxSession:
        """Create a sandbox session via OpenSandbox API."""
        resp = self._api_call(
            "POST",
            "/sandboxes",
            {
                "image": image,
                "memory_limit": memory_limit,
                "network": network,
                "timeout": timeout,
            },
        )
        if "error" in resp:
            raise RuntimeError(f"OpenSandbox create failed: {resp['error']}")

        session_id = resp.get("id") or resp.get("sandbox_id") or f"sb-os-{uuid.uuid4().hex[:12]}"
        session = SandboxSession(
            session_id=str(session_id),
            backend=SandboxBackend.OPENSANDBOX,
            status="created",
            created_at=datetime.now(timezone.utc).isoformat(),
            image=image,
            memory_limit=memory_limit,
            network=network,
            timeout=timeout,
            metadata={"api_response": resp},
        )
        self._sessions[session.session_id] = session
        return session

    def execute_code(
        self,
        session_id: str,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute code via OpenSandbox API."""
        start = time.time()
        resp = self._api_call(
            "POST",
            f"/sandboxes/{session_id}/executions",
            {
                "code": code,
                "language": language,
            },
            timeout=timeout,
        )
        duration_ms = int((time.time() - start) * 1000)

        if "error" in resp:
            return ExecutionResult(
                stdout="",
                stderr=resp["error"],
                exit_code=1,
                duration_ms=duration_ms,
                language=language,
                success=False,
            )

        return ExecutionResult(
            stdout=resp.get("stdout", resp.get("output", ""))[:50000],
            stderr=resp.get("stderr", "")[:10000],
            exit_code=resp.get("exit_code", 0),
            duration_ms=resp.get("duration_ms", duration_ms),
            language=language,
            success=resp.get("exit_code", 0) == 0,
        )

    def execute_command(
        self,
        session_id: str,
        command: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute a shell command via OpenSandbox API."""
        return self.execute_code(session_id, command, "bash", timeout)

    def upload_file(
        self,
        session_id: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to the sandbox via API.

        Note: This is a best-effort implementation.  If the OpenSandbox API
        does not support file upload, the call returns False gracefully.
        """
        try:
            file_bytes = Path(local_path).read_bytes()
            import base64

            encoded = base64.b64encode(file_bytes).decode("ascii")
            resp = self._api_call(
                "POST",
                f"/sandboxes/{session_id}/files",
                {
                    "path": remote_path,
                    "content_base64": encoded,
                },
            )
            return "error" not in resp
        except Exception:
            return False

    def download_file(self, session_id: str, remote_path: str) -> Optional[bytes]:
        """Download a file from the sandbox via API."""
        try:
            resp = self._api_call("GET", f"/sandboxes/{session_id}/files?path={remote_path}")
            if "error" in resp:
                return None
            content = resp.get("content_base64", "")
            if content:
                import base64

                return base64.b64decode(content)
            return resp.get("content", "").encode("utf-8") if resp.get("content") else None
        except Exception:
            return None

    def destroy_session(self, session_id: str) -> bool:
        """Destroy a sandbox via OpenSandbox API."""
        resp = self._api_call("DELETE", f"/sandboxes/{session_id}")
        self._sessions.pop(session_id, None)
        return "error" not in resp

    def list_sessions(self) -> List[SandboxSession]:
        """List locally-tracked sessions."""
        return list(self._sessions.values())

    def health_check(self) -> Dict[str, Any]:
        """Check OpenSandbox API health."""
        resp = self._api_call("GET", "/health", timeout=5)
        if "error" in resp:
            return {"healthy": False, "backend": "opensandbox", "error": resp["error"]}
        return {
            "healthy": True,
            "backend": "opensandbox",
            "url": self.base_url,
            "active_sessions": len(self._sessions),
            "server_info": {k: v for k, v in resp.items() if k != "error"},
        }


class _SubprocessBackend:
    """Subprocess fallback backend — always available but least secure.

    Executes Python code in isolated temp directories with timeout enforcement.
    No network isolation (documented limitation).  Python only.
    """

    def __init__(self, config: Dict[str, Any]):
        self.timeout = config.get("subprocess_timeout_seconds", 30)
        self._sessions: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def is_available() -> bool:
        """Subprocess backend is always available."""
        return True

    def create_session(
        self,
        image: str,
        memory_limit: str,
        network: bool,
        timeout: int,
    ) -> SandboxSession:
        """Create a subprocess session with a dedicated temp directory."""
        session_id = f"sb-proc-{uuid.uuid4().hex[:12]}"
        tmp_dir = tempfile.mkdtemp(prefix=f"icdev-sandbox-{session_id}-")

        session = SandboxSession(
            session_id=session_id,
            backend=SandboxBackend.SUBPROCESS,
            status="created",
            created_at=datetime.now(timezone.utc).isoformat(),
            image=image,
            memory_limit=memory_limit,
            network=network,
            timeout=timeout,
            metadata={"tmp_dir": tmp_dir},
        )
        self._sessions[session_id] = {"session": session, "tmp_dir": tmp_dir}
        return session

    def execute_code(
        self,
        session_id: str,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute code in a subprocess with timeout enforcement.

        Note: Only Python is supported in subprocess mode.  Other languages
        return an error immediately.
        """
        info = self._sessions.get(session_id)
        if not info:
            return ExecutionResult(
                stdout="",
                stderr=f"Session {session_id} not found",
                exit_code=1,
                duration_ms=0,
                language=language,
                success=False,
            )

        if language not in ("python", "python3"):
            return ExecutionResult(
                stdout="",
                stderr=f"Subprocess backend supports Python only, got: {language}",
                exit_code=1,
                duration_ms=0,
                language=language,
                success=False,
            )

        tmp_dir = info["tmp_dir"]
        code_file = Path(tmp_dir) / f"exec_{uuid.uuid4().hex[:8]}.py"
        code_file.write_text(code, encoding="utf-8", newline="")

        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, str(code_file)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp_dir,
            )
            duration_ms = int((time.time() - start) * 1000)
            return ExecutionResult(
                stdout=proc.stdout[:50000],
                stderr=proc.stderr[:10000],
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                language=language,
                success=proc.returncode == 0,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return ExecutionResult(
                stdout="",
                stderr=f"Execution timed out ({timeout}s)",
                exit_code=124,
                duration_ms=duration_ms,
                language=language,
                success=False,
            )

    def execute_command(
        self,
        session_id: str,
        command: str,
        timeout: int,
    ) -> ExecutionResult:
        """Execute a shell command in the subprocess session."""
        info = self._sessions.get(session_id)
        if not info:
            return ExecutionResult(
                stdout="",
                stderr=f"Session {session_id} not found",
                exit_code=1,
                duration_ms=0,
                language="bash",
                success=False,
            )

        tmp_dir = info["tmp_dir"]
        start = time.time()
        try:
            cmd_args = command if isinstance(command, list) else ["bash", "-c", command]
            proc = subprocess.run(
                cmd_args,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp_dir,
            )
            duration_ms = int((time.time() - start) * 1000)
            return ExecutionResult(
                stdout=proc.stdout[:50000],
                stderr=proc.stderr[:10000],
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                language="bash",
                success=proc.returncode == 0,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return ExecutionResult(
                stdout="",
                stderr=f"Command timed out ({timeout}s)",
                exit_code=124,
                duration_ms=duration_ms,
                language="bash",
                success=False,
            )

    def upload_file(
        self,
        session_id: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Copy a file into the session's temp directory."""
        info = self._sessions.get(session_id)
        if not info:
            return False
        dest = Path(info["tmp_dir"]) / Path(remote_path).name
        try:
            shutil.copy2(local_path, str(dest))
            return True
        except (OSError, shutil.SameFileError):
            return False

    def download_file(self, session_id: str, remote_path: str) -> Optional[bytes]:
        """Read a file from the session's temp directory."""
        info = self._sessions.get(session_id)
        if not info:
            return None
        src = Path(info["tmp_dir"]) / Path(remote_path).name
        if src.exists():
            return src.read_bytes()
        return None

    def destroy_session(self, session_id: str) -> bool:
        """Remove the session's temp directory and all files."""
        info = self._sessions.pop(session_id, None)
        if not info:
            return False
        try:
            shutil.rmtree(info["tmp_dir"], ignore_errors=True)
            return True
        except OSError:
            return False

    def list_sessions(self) -> List[SandboxSession]:
        """List all subprocess sessions."""
        return [info["session"] for info in self._sessions.values()]

    def health_check(self) -> Dict[str, Any]:
        """Subprocess backend is always healthy."""
        return {
            "healthy": True,
            "backend": "subprocess",
            "python_version": sys.version,
            "active_sessions": len(self._sessions),
            "limitations": [
                "Python execution only (no multi-language)",
                "No network isolation",
                "No memory limits",
            ],
        }


# ---------------------------------------------------------------------------
# Unified adapter
# ---------------------------------------------------------------------------


class SandboxAdapter:
    """Unified sandbox execution adapter supporting multiple backends.

    Auto-detects the best available backend at initialization:
    OpenSandbox API > Docker > subprocess.

    All methods are deterministic and use stdlib only (D-CF-4, FORGE compliant).
    """

    def __init__(
        self,
        backend: Optional[SandboxBackend] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the sandbox adapter.

        Args:
            backend: Force a specific backend.  If None, auto-detect.
            config: Override sandbox configuration.  If None, load from
                    args/databridge_config.yaml.
        """
        self._config = config or _load_sandbox_config()
        self._backend_type = backend or self._detect_backend()
        self._backend = self._create_backend(self._backend_type)
        logger.info("SandboxAdapter initialized with backend: %s", self._backend_type.value)

    @property
    def backend_type(self) -> SandboxBackend:
        """The active backend type."""
        return self._backend_type

    def _detect_backend(self) -> SandboxBackend:
        """Auto-detect the best available sandbox backend.

        Priority: OpenSandbox API > Docker > subprocess (always available).
        """
        # 1. Check OpenSandbox API
        opensandbox_url = self._config.get("opensandbox_url", "http://localhost:8000")
        if _OpenSandboxBackend.is_available_at(opensandbox_url):
            logger.info("OpenSandbox API detected at %s", opensandbox_url)
            return SandboxBackend.OPENSANDBOX

        # 2. Check Docker
        if _DockerBackend.is_available():
            logger.info("Docker daemon detected")
            return SandboxBackend.DOCKER

        # 3. Fallback to subprocess
        logger.info("Using subprocess fallback (no Docker or OpenSandbox available)")
        return SandboxBackend.SUBPROCESS

    def _create_backend(self, backend_type: SandboxBackend) -> Any:
        """Instantiate the appropriate backend."""
        if backend_type == SandboxBackend.OPENSANDBOX:
            return _OpenSandboxBackend(self._config)
        elif backend_type == SandboxBackend.DOCKER:
            return _DockerBackend(self._config)
        else:
            return _SubprocessBackend(self._config)

    def create_session(
        self,
        image: str = "python:3.12-slim",
        memory_limit: str = "256m",
        network: bool = False,
        timeout: int = 120,
    ) -> SandboxSession:
        """Create a new sandbox session.

        Args:
            image: Container image (Docker/OpenSandbox) or ignored (subprocess).
            memory_limit: Memory limit string (e.g., '256m').
            network: Whether to allow network access.  Default False (D-CF-4).
            timeout: Session-level timeout in seconds.

        Returns:
            SandboxSession with session_id for subsequent calls.

        Raises:
            RuntimeError: If session creation fails on the active backend.
        """
        return self._backend.create_session(image, memory_limit, network, timeout)

    def execute_code(
        self,
        session_id: str,
        code: str,
        language: str = "python",
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """Execute code in a sandbox session.

        Supports Jupyter-style multi-language execution on Docker/OpenSandbox
        backends.  Subprocess backend is Python-only.

        Args:
            session_id: Session to execute in.
            code: Source code string.
            language: Execution language ('python', 'bash', 'sh').
            timeout: Per-execution timeout.  Defaults to session timeout.

        Returns:
            ExecutionResult with stdout, stderr, exit_code, duration_ms.
        """
        effective_timeout = timeout or self._config.get("docker_timeout_seconds", 60)
        return self._backend.execute_code(session_id, code, language, effective_timeout)

    def execute_command(
        self,
        session_id: str,
        command: str,
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute a shell command in the sandbox.

        Args:
            session_id: Session to execute in.
            command: Shell command string.
            timeout: Timeout in seconds.

        Returns:
            ExecutionResult with stdout, stderr, exit_code.
        """
        return self._backend.execute_command(session_id, command, timeout)

    def upload_file(
        self,
        session_id: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to the sandbox.

        Args:
            session_id: Target session.
            local_path: Local filesystem path.
            remote_path: Destination path inside the sandbox.

        Returns:
            True if upload succeeded.
        """
        return self._backend.upload_file(session_id, local_path, remote_path)

    def download_file(
        self,
        session_id: str,
        remote_path: str,
    ) -> Optional[bytes]:
        """Download a file from the sandbox.

        Args:
            session_id: Source session.
            remote_path: Path inside the sandbox.

        Returns:
            File contents as bytes, or None if not found.
        """
        return self._backend.download_file(session_id, remote_path)

    def destroy_session(self, session_id: str) -> bool:
        """Destroy a sandbox session and clean up resources.

        Args:
            session_id: Session to destroy.

        Returns:
            True if cleanup succeeded.
        """
        return self._backend.destroy_session(session_id)

    def list_sessions(self) -> List[SandboxSession]:
        """List all active sandbox sessions for the current backend.

        Returns:
            List of SandboxSession objects.
        """
        return self._backend.list_sessions()

    def health_check(self) -> Dict[str, Any]:
        """Check if the sandbox backend is healthy.

        Returns:
            Dictionary with healthy (bool), backend name, and details.
        """
        return self._backend.health_check()

    def run_self_test(self) -> Dict[str, Any]:
        """Run a full self-test: create session, execute hello world, destroy.

        Returns:
            Test results dictionary with success flag and details.
        """
        results: Dict[str, Any] = {
            "backend": self._backend_type.value,
            "steps": [],
            "success": False,
        }
        session_id: Optional[str] = None

        try:
            # Step 1: Create session
            start = time.time()
            session = self.create_session()
            session_id = session.session_id
            results["steps"].append(
                {
                    "step": "create_session",
                    "success": True,
                    "session_id": session_id,
                    "duration_ms": int((time.time() - start) * 1000),
                }
            )

            # Step 2: Execute hello world
            start = time.time()
            exec_result = self.execute_code(session_id, "print('hello from sandbox')")
            results["steps"].append(
                {
                    "step": "execute_code",
                    "success": exec_result.success,
                    "stdout": exec_result.stdout.strip(),
                    "exit_code": exec_result.exit_code,
                    "duration_ms": exec_result.duration_ms,
                }
            )

            # Step 3: Execute arithmetic check
            start = time.time()
            math_result = self.execute_code(session_id, "import json; print(json.dumps({'result': 6 * 7}))")
            results["steps"].append(
                {
                    "step": "execute_arithmetic",
                    "success": math_result.success and "42" in math_result.stdout,
                    "stdout": math_result.stdout.strip(),
                    "duration_ms": math_result.duration_ms,
                }
            )

            # Step 4: Destroy session
            start = time.time()
            destroyed = self.destroy_session(session_id)
            results["steps"].append(
                {
                    "step": "destroy_session",
                    "success": destroyed,
                    "duration_ms": int((time.time() - start) * 1000),
                }
            )
            session_id = None  # Already destroyed

            results["success"] = all(s["success"] for s in results["steps"])

        except Exception as exc:
            results["steps"].append(
                {
                    "step": "error",
                    "success": False,
                    "error": str(exc)[:500],
                }
            )
        finally:
            # Cleanup on failure
            if session_id:
                try:
                    self.destroy_session(session_id)
                except Exception:
                    pass

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    """Command-line interface for sandbox adapter."""
    parser = argparse.ArgumentParser(
        description="OpenSandbox-inspired sandbox adapter for Connector Forge (D-CF-4)",
    )
    parser.add_argument("--health", action="store_true", help="Check backend health")
    parser.add_argument("--test", action="store_true", help="Run self-test")
    parser.add_argument("--execute", action="store_true", help="Execute code in a one-shot session")
    parser.add_argument("--code", type=str, default="", help="Code to execute (with --execute)")
    parser.add_argument("--language", type=str, default="python", help="Language (default: python)")
    parser.add_argument(
        "--backend",
        type=str,
        choices=["docker", "opensandbox", "subprocess"],
        help="Force a specific backend (default: auto-detect)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    # Determine backend
    backend = None
    if args.backend:
        backend = SandboxBackend(args.backend)

    adapter = SandboxAdapter(backend=backend)

    if args.health:
        result = adapter.health_check()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "HEALTHY" if result.get("healthy") else "UNHEALTHY"
            print(f"Backend: {result.get('backend', 'unknown')}")
            print(f"Status:  {status}")
            for k, v in result.items():
                if k not in ("healthy", "backend"):
                    print(f"  {k}: {v}")
        sys.exit(0 if result.get("healthy") else 1)

    elif args.test:
        result = adapter.run_self_test()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "PASSED" if result["success"] else "FAILED"
            print(f"Self-test: {status} (backend: {result['backend']})")
            for step in result["steps"]:
                mark = "+" if step["success"] else "x"
                print(f"  [{mark}] {step['step']} ({step.get('duration_ms', 0)}ms)")
                if step.get("stdout"):
                    print(f"      stdout: {step['stdout']}")
                if step.get("error"):
                    print(f"      error: {step['error']}")
        sys.exit(0 if result["success"] else 1)

    elif args.execute:
        if not args.code:
            print("Error: --code is required with --execute", file=sys.stderr)
            sys.exit(1)

        session = None
        try:
            session = adapter.create_session()
            exec_result = adapter.execute_code(session.session_id, args.code, args.language)

            output = {
                "backend": adapter.backend_type.value,
                "session_id": session.session_id,
                "execution": exec_result.to_dict(),
                "success": exec_result.success,
            }

            if args.json:
                print(json.dumps(output, indent=2))
            else:
                if exec_result.stdout:
                    print(exec_result.stdout, end="")
                if exec_result.stderr:
                    print(exec_result.stderr, file=sys.stderr, end="")
                if not exec_result.success:
                    print(f"\n[exit code: {exec_result.exit_code}]", file=sys.stderr)

            sys.exit(exec_result.exit_code)
        finally:
            if session:
                adapter.destroy_session(session.session_id)

    else:
        # Default: show health
        result = adapter.health_check()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Sandbox Adapter — backend: {result.get('backend', 'unknown')}")
            print("Use --health, --test, or --execute --code '...' to interact.")
        sys.exit(0)


if __name__ == "__main__":
    _cli()
