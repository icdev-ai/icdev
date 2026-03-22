"""ICDEV™ Services Launcher — starts Dashboard + Genesis Daemon.

Spawned by Task Scheduler via start_daemon.bat.
Dashboard runs as a subprocess; daemon runs in the main process.
Both auto-restart on crash. Ollama health is checked before daemon start.
"""
import subprocess
import sys
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

# Load .env so dashboard/daemon get API keys, LLM config, etc.
_env_file = os.path.join(ROOT, ".env")
if os.path.isfile(_env_file):
    with open(_env_file, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                if _k:
                    os.environ.setdefault(_k, _v)

# Ensure .tmp dirs exist
os.makedirs(".tmp/genesis", exist_ok=True)

# Set environment for Genesis
os.environ["ICDEV_GENESIS_ENABLED"] = "true"
os.environ.setdefault("PYTHONPATH", ROOT)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DASHBOARD_PORT = 5050
LOG_FILE = ".tmp/genesis/launcher.log"


def _log(msg: str) -> None:
    """Write to both stdout and launcher log."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _check_ollama(timeout: float = 3.0) -> bool:
    """Check if Ollama is reachable."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def _wait_for_ollama(max_wait: int = 120) -> bool:
    """Wait for Ollama to become available (it may start after reboot too)."""
    _log(f"Waiting for Ollama (up to {max_wait}s)...")
    for i in range(0, max_wait, 5):
        if _check_ollama():
            _log("Ollama is ready")
            return True
        time.sleep(5)
    _log("WARNING: Ollama not available — Genesis will run with degraded LLM")
    return False


def _child_env():
    """Build environment dict for child processes with PYTHONPATH guaranteed."""
    env = {**os.environ}
    env["PYTHONPATH"] = ROOT
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["ICDEV_GENESIS_ENABLED"] = "true"
    return env


def _start_dashboard():
    """Start Dashboard subprocess."""
    dash_log = open(".tmp/dashboard.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "tools/dashboard/app.py", "--port", str(DASHBOARD_PORT)],
        stdout=dash_log,
        stderr=dash_log,
        cwd=ROOT,
        env=_child_env(),
    )
    _log(f"Dashboard started (PID {proc.pid}, port {DASHBOARD_PORT})")
    return proc, dash_log


def _start_daemon():
    """Start Genesis Daemon subprocess."""
    daemon_log = open(".tmp/genesis/daemon.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "tools/genesis/daemon.py"],
        stdout=daemon_log,
        stderr=daemon_log,
        cwd=ROOT,
        env=_child_env(),
    )
    _log(f"Genesis Daemon started (PID {proc.pid})")
    return proc, daemon_log


def _start_proposal_genesis():
    """Start Proposal Genesis Daemon subprocess."""
    os.makedirs(".tmp/proposal_genesis", exist_ok=True)
    pg_log = open(".tmp/proposal_genesis/daemon.log", "a", encoding="utf-8")
    env = _child_env()
    env["ICDEV_PROPOSAL_GENESIS_ENABLED"] = "true"
    proc = subprocess.Popen(
        [sys.executable, "tools/proposal_genesis/daemon.py"],
        stdout=pg_log,
        stderr=pg_log,
        cwd=ROOT,
        env=env,
    )
    _log(f"Proposal Genesis Daemon started (PID {proc.pid})")
    return proc, pg_log


def main():
    _log("=" * 60)
    _log("ICDEV™ Services Launcher starting")
    _log(f"  Root: {ROOT}")
    _log(f"  Python: {sys.executable}")

    # Wait for Ollama (scanner-tier LLM)
    _wait_for_ollama(max_wait=90)

    # Start Dashboard
    dash_proc, dash_log_f = _start_dashboard()
    time.sleep(3)

    # Start Genesis Daemon
    daemon_proc, daemon_log_f = _start_daemon()

    # Start Proposal Genesis Daemon (SAM.gov scanning, proposal intelligence)
    pg_proc, pg_log_f = _start_proposal_genesis()

    # Monitor loop — restart crashed processes
    try:
        while True:
            time.sleep(30)

            # Check dashboard
            if dash_proc.poll() is not None:
                _log(f"Dashboard exited (code {dash_proc.returncode}), restarting...")
                dash_log_f.close()
                time.sleep(2)
                dash_proc, dash_log_f = _start_dashboard()

            # Check Genesis daemon
            if daemon_proc.poll() is not None:
                _log(f"Genesis Daemon exited (code {daemon_proc.returncode}), restarting...")
                daemon_log_f.close()
                time.sleep(5)
                daemon_proc, daemon_log_f = _start_daemon()

            # Check Proposal Genesis daemon
            if pg_proc.poll() is not None:
                _log(f"Proposal Genesis exited (code {pg_proc.returncode}), restarting...")
                pg_log_f.close()
                time.sleep(5)
                pg_proc, pg_log_f = _start_proposal_genesis()

    except KeyboardInterrupt:
        _log("Shutdown requested")
    finally:
        _log("Stopping services...")
        for proc in [daemon_proc, pg_proc, dash_proc]:
            proc.terminate()
        for proc in [daemon_proc, pg_proc, dash_proc]:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        dash_log_f.close()
        daemon_log_f.close()
        pg_log_f.close()
        _log("ICDEV™ Services stopped")


if __name__ == "__main__":
    main()
