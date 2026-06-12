from __future__ import annotations
# CUI // SP-CTI
"""ICDEV™ Services Launcher — starts Dashboard + Genesis Daemon + Kanban Scheduler.

Cross-platform: works on Windows, Linux, and macOS.
Entry point: python tools/genesis/launch.py  (or start_daemon.bat / start_daemon.ps1 as wrappers)
All services run as subprocesses with auto-restart on crash.
Ollama health is checked before daemon start.
"""

import os
import subprocess
import sys
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

# Ensure .tmp dirs exist (anchored to ROOT — container-safe)
os.makedirs(os.path.join(ROOT, ".tmp", "genesis"), exist_ok=True)

# Set environment for Genesis
os.environ["ICDEV_GENESIS_ENABLED"] = "true"
os.environ.setdefault("PYTHONPATH", ROOT)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DASHBOARD_PORT = 5050
TRADING_DASHBOARD_PORT = 5100
LOG_FILE = os.path.join(ROOT, ".tmp", "genesis", "launcher.log")
_PID_FILE = os.path.join(ROOT, ".tmp", "genesis", "launcher.pid")


def _acquire_pid_lock() -> bool:
    """Return True if this process is now the sole launcher. False = another is running."""
    from tools.compat.platform_utils import pid_exists
    if os.path.exists(_PID_FILE):
        try:
            with open(_PID_FILE) as f:
                existing_pid = int(f.read().strip())
            if pid_exists(existing_pid):
                return False  # another launcher is alive
        except Exception:
            pass  # stale or unreadable PID file — take the lock
    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_pid_lock() -> None:
    try:
        os.remove(_PID_FILE)
    except OSError:
        pass


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
        with urllib.request.urlopen(req, timeout=timeout):  # nosec B310 -- URL scheme validated; internal/configured endpoints only
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
    _kill_stale_instances("tools/dashboard/app.py")
    dash_log = open(os.path.join(ROOT, ".tmp", "dashboard.log"), "a", encoding="utf-8")
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
    _kill_stale_instances("genesis/daemon.py")
    daemon_log = open(os.path.join(ROOT, ".tmp", "genesis", "daemon.log"), "a", encoding="utf-8")
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
    _kill_stale_instances("proposal_genesis/daemon.py")
    os.makedirs(os.path.join(ROOT, ".tmp", "proposal_genesis"), exist_ok=True)
    pg_log = open(os.path.join(ROOT, ".tmp", "proposal_genesis", "daemon.log"), "a", encoding="utf-8")
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


def _kill_stale_instances(script_name: str) -> None:
    """Kill any existing instances of a script before starting a fresh one.

    Cross-platform: uses psutil when available, falls back to platform-agnostic
    process enumeration via tools.compat.platform_utils.
    Prevents duplicate processes from accumulating across launcher restarts.
    """
    from tools.compat.platform_utils import find_pids_by_cmdline, kill_process
    own_pid = os.getpid()
    try:
        for pid in find_pids_by_cmdline(script_name):
            if pid == own_pid:
                continue
            try:
                if kill_process(pid):
                    _log(f"Killed stale {script_name} (PID {pid})")
            except Exception as exc:
                _log(f"Could not kill stale {script_name} PID {pid}: {exc}")
    except Exception as exc:
        _log(f"Stale process cleanup failed for {script_name}: {exc}")


def _start_kanban_scheduler():
    """Start Kanban Scheduler subprocess."""
    _kill_stale_instances("kanban_scheduler.py")
    kb_log = open(os.path.join(ROOT, ".tmp", "kanban_scheduler.log"), "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "tools/genesis/kanban_scheduler.py", "--interval", "60"],
        stdout=kb_log,
        stderr=kb_log,
        cwd=ROOT,
        env=_child_env(),
    )
    _log(f"Kanban Scheduler started (PID {proc.pid})")
    return proc, kb_log


def _start_trading_dashboard():
    """Start FathomDesk trading dashboard subprocess."""
    _kill_stale_instances("trading/dashboard/app.py")
    td_log = open(os.path.join(ROOT, ".tmp", "trading_dashboard.log"), "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "tools/trading/dashboard/app.py"],
        stdout=td_log,
        stderr=td_log,
        cwd=ROOT,
        env=_child_env(),
    )
    _log(f"FathomDesk Dashboard started (PID {proc.pid}, port {TRADING_DASHBOARD_PORT})")
    return proc, td_log


def main():
    if not _acquire_pid_lock():
        _log("Another launcher instance is already running — exiting to avoid port conflicts.")
        return

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

    # Start Kanban Scheduler (autonomous task execution)
    kb_proc, kb_log_f = _start_kanban_scheduler()

    # Start FathomDesk Trading Dashboard
    td_proc, td_log_f = _start_trading_dashboard()

    # Monitor loop — restart crashed processes
    try:
        while True:
            time.sleep(30)

            try:
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

                # Check Kanban Scheduler
                if kb_proc.poll() is not None:
                    _log(f"Kanban Scheduler exited (code {kb_proc.returncode}), restarting...")
                    kb_log_f.close()
                    time.sleep(2)
                    kb_proc, kb_log_f = _start_kanban_scheduler()

                # Check FathomDesk Trading Dashboard
                if td_proc.poll() is not None:
                    _log(f"FathomDesk Dashboard exited (code {td_proc.returncode}), restarting...")
                    td_log_f.close()
                    time.sleep(2)
                    td_proc, td_log_f = _start_trading_dashboard()

            except Exception as exc:
                _log(f"Monitor loop error (non-fatal): {exc}")

    except KeyboardInterrupt:
        _log("Shutdown requested")
    finally:
        _log("Stopping services...")
        for proc in [daemon_proc, pg_proc, kb_proc, td_proc, dash_proc]:
            proc.terminate()
        for proc in [daemon_proc, pg_proc, kb_proc, td_proc, dash_proc]:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        dash_log_f.close()
        daemon_log_f.close()
        pg_log_f.close()
        kb_log_f.close()
        td_log_f.close()
        _release_pid_lock()
        _log("ICDEV™ Services stopped")


if __name__ == "__main__":
    main()
