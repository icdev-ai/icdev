from __future__ import annotations
# CUI // SP-CTI
"""ICDEV™ Services Launcher — Dashboard + Genesis Daemon + Kanban Scheduler + PR Watcher.

Cross-platform: works on Windows, Linux, and macOS.
Entry point: python tools/genesis/launch.py  (or start_daemon.bat / start_daemon.ps1 as wrappers)
All services run as subprocesses with auto-restart on crash.
Ollama and PostgreSQL health are checked before dependent services start.

The Kanban Scheduler and the PR Watcher are two halves of one loop: the
scheduler builds and opens PRs, the watcher merges the green ones and frees the
task. Starting one without the other yields an open loop that quietly stops
making progress, so both are supervised here.
"""

import os
import subprocess
import sys
import time
import urllib.request
from typing import Optional

ROOT =os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
PR_WATCHER_INTERVAL = int(os.environ.get("ICDEV_PR_WATCHER_INTERVAL", "30"))
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


#: The supervised children that read the declared database at start-up
#: (icdev_domain.yaml `db:`). Every one of them either crashes at
#: assert_identity while PostgreSQL is in recovery (dashboard, the two daemons)
#: or registers into a dead database and carries on (scheduler, pr_watcher).
#: The trading dashboard reads ICDEV[FT]'s own database and is not listed.
DB_BOUND_CHILDREN = (
    "dashboard", "genesis_daemon", "proposal_genesis", "kanban_scheduler", "pr_watcher",
)

#: How long the supervisor waits for the database before starting DB-bound
#: children anyway. MEASURED: on 2026-09-03 (05:02:55 -> ~05:11) and 2026-09-04
#: (07:20:38 -> ~07:29) PostgreSQL took ~8 minutes after logon to accept a
#: connection, and the previous 120s bound expired on both boots. 600s covers
#: both with margin; ICDEV_LAUNCHER_DB_WAIT_SECONDS overrides it. It is a bound,
#: not a dependency: on expiry the children are started and the log says so.
DB_WAIT_SECONDS_ENV = "ICDEV_LAUNCHER_DB_WAIT_SECONDS"
DEFAULT_DB_WAIT_SECONDS = 600
DB_PROBE_EVERY_SECONDS = 5
DB_PROGRESS_EVERY_SECONDS = 15


def _db_wait_seconds() -> int:
    raw = (os.environ.get(DB_WAIT_SECONDS_ENV) or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_DB_WAIT_SECONDS
    except ValueError:
        value = DEFAULT_DB_WAIT_SECONDS
    return value if value >= 0 else DEFAULT_DB_WAIT_SECONDS


def _declared_database() -> dict:
    """What the declaration (icdev_domain.yaml) and .env say the children will open.

    `backend` is what the children will actually use (storage.get_backend reads
    the env, defaulting to PostgreSQL); `database` is the name the env carries
    under the declaration's `name_env` / `dsn_env`, or None when neither names
    one -- an unnamed database is reported as such, never guessed.
    """
    out = {"backend": None, "database": None, "named_by": None, "declared": False}
    try:
        from tools.db.storage import get_backend
        out["backend"] = get_backend()
    except Exception:  # noqa: BLE001
        out["backend"] = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    try:
        # The same reader the children's assert_identity uses (pinned core
        # API, args/core_api.yaml): `database_observed` is the name the env
        # carries under the declaration's name_env / dsn_env, `database_source`
        # says which. check_identity REPORTS a mismatch; only assert_ raises.
        from icdev.core.context import check_identity
        report = check_identity()
        out["declared"] = True
        out["declared_databases"] = list(report.database_declared)
        out["database"], out["named_by"] = report.database_observed, report.database_source
    except Exception:  # noqa: BLE001 -- a wheel or scaffold without the core still boots
        name = (os.environ.get("ICDEV_PG_DATABASE") or "").strip()
        if name:
            out["database"], out["named_by"] = name, "ICDEV_PG_DATABASE"
    return out


def _check_postgres(timeout: float = 3.0) -> bool:
    """Return True if the declared PostgreSQL database answers a trivial query.

    Uses the same connection path the services themselves use, so a success here
    means they will connect too — unlike pg_isready, which is not on PATH on
    every host and does not exercise auth/database selection.

    A SQLite FALLBACK connection does not count. Without ICDEV_PG_NO_FALLBACK
    `get_connection()` hands back SQLite when PostgreSQL is down, and a probe
    that accepted it would report "ready" for a database that is still in
    recovery -- the children would then start against the wrong store, or crash
    at assert_identity, exactly what this wait exists to prevent.
    """
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            if getattr(conn, "_backend", None) != "postgresql":
                return False
            conn.execute("SELECT 1").fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return True
    except Exception:
        return False


def _wait_for_postgres(max_wait: Optional[int] = None, *, probe=None, sleep=None,
                       clock=None, log=None) -> dict:
    """Wait for the declared database before starting the DB-bound children.

    Bounded by `max_wait` (default ICDEV_LAUNCHER_DB_WAIT_SECONDS, else 600s),
    probing every 5s and logging progress every 15s with the database's name.
    On expiry the children are started ANYWAY and the log says which ones and
    why -- an unreachable database must never keep the supervisor from ever
    starting, and each child has its own recovery: the dashboard and daemons
    are restarted by the monitor loop until PG accepts, and the scheduler and
    pr_watcher retry their registration with backoff (mfx-boot-01).

    Returns {"ready", "waited", "attempts", "database", "backend", "bound"}
    rather than a bare bool so a caller (or a test) can see what was measured.
    Skipped -- `ready: True`, `skipped: True` -- when the backend is not
    PostgreSQL: SQLite needs no server.
    """
    probe = probe or _check_postgres
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    log = log or _log
    bound = _db_wait_seconds() if max_wait is None else int(max_wait)
    decl = _declared_database()
    label = f"PostgreSQL database '{decl['database']}'" if decl.get("database") \
        else "PostgreSQL (no database named in .env)"
    result = {
        "ready": False, "waited": 0.0, "attempts": 0, "bound": bound,
        "database": decl.get("database"), "backend": decl.get("backend"),
        "skipped": False,
    }
    if (decl.get("backend") or "") != "postgresql":
        result.update(ready=True, skipped=True)
        return result

    log(f"Waiting for {label} to accept a connection (up to {bound}s) before "
        f"starting DB-bound children: {', '.join(DB_BOUND_CHILDREN)}")
    started = clock()
    next_progress = DB_PROGRESS_EVERY_SECONDS
    while True:
        result["attempts"] += 1
        if probe():
            result["ready"] = True
            result["waited"] = clock() - started
            log(f"{label} is ready after {result['waited']:.0f}s "
                f"({result['attempts']} probe{'s' if result['attempts'] != 1 else ''})")
            return result
        waited = clock() - started
        if waited >= bound:
            result["waited"] = waited
            log(f"WARNING: {label} did not accept a connection within {bound}s "
                f"({result['attempts']} probes) — starting DB-bound children anyway "
                f"({', '.join(DB_BOUND_CHILDREN)}). The dashboard and daemons will be "
                f"restarted by the monitor loop until it accepts; the scheduler and "
                f"pr_watcher retry their registration with backoff.")
            return result
        if waited >= next_progress:
            log(f"Still waiting for {label} ({waited:.0f}s of {bound}s, "
                f"{result['attempts']} probes)...")
            next_progress += DB_PROGRESS_EVERY_SECONDS
        sleep(DB_PROBE_EVERY_SECONDS)


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
    # "genesis/daemon.py" would also match tools/proposal_genesis/daemon.py.
    _kill_stale_instances("tools/genesis/daemon.py")
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


def _is_inline_snippet(pid: int, fragment: str) -> bool:
    """True if *pid* only matches because *fragment* appears inside a -c snippet.

    ``find_pids_by_cmdline`` substring-matches the whole joined command line, so
    any shell or interpreter invoked as ``bash -c '... pr_watcher ...'`` matches
    a script it merely mentions. Measured: the fragment "pr_watcher" matched five
    processes, four of which were diagnostic shells that had simply typed the
    name. Killing those would take out unrelated work.

    Everything after ``-c`` is code, not a program name, so it is excluded from
    the match. Fails closed: if the process cannot be inspected, it is treated as
    an inline snippet and spared.
    """
    try:
        import psutil
        argv = psutil.Process(pid).cmdline() or []
    except Exception:
        return True
    if "-c" in argv:
        argv = argv[: argv.index("-c")]
    return fragment not in " ".join(argv)


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
            if _is_inline_snippet(pid, script_name):
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


def _start_pr_watcher():
    """Start the PR Watcher subprocess — the half of the loop that *lands* work.

    Without it the pipeline is open: the scheduler keeps building and opening
    PRs, nothing merges, tasks stay parked in pr_opened, and the respawn guard
    eventually withholds every task. That presents as "the dispatcher stopped
    working" when the dispatcher is in fact idle and correct.
    """
    # Matches both "tools/ci/pr_watcher.py" and "-m tools.ci.pr_watcher" invocations.
    _kill_stale_instances("pr_watcher")
    pw_log = open(os.path.join(ROOT, ".tmp", "pr_watcher.log"), "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "tools/ci/pr_watcher.py", "--daemon",
         "--interval", str(PR_WATCHER_INTERVAL)],
        stdout=pw_log,
        stderr=pw_log,
        cwd=ROOT,
        env=_child_env(),
    )
    _log(f"PR Watcher started (PID {proc.pid}, interval {PR_WATCHER_INTERVAL}s)")
    return proc, pw_log


def _trading_dashboard_available() -> bool:
    """xit-gen-01: FathomDesk is moving to the private ICDEV[FT] repository.

    Start its dashboard only while the entry point is still in this tree and
    nobody has switched it off (ICDEV_TRADING_DASHBOARD_ENABLED=0). After the
    removal the file is gone and the launcher simply logs that it skipped it,
    instead of a subprocess that dies on FileNotFoundError every restart cycle.
    """
    if os.environ.get("ICDEV_TRADING_DASHBOARD_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return False
    return os.path.isfile(os.path.join(ROOT, "tools", "trading", "dashboard", "app.py"))


def _start_trading_dashboard():
    """Start FathomDesk trading dashboard subprocess (None, None when absent)."""
    if not _trading_dashboard_available():
        _log("FathomDesk Dashboard skipped (tools/trading/dashboard/app.py absent or disabled)")
        return None, None
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

    # Wait for the declared database BEFORE the first DB-bound child. Bounded
    # (ICDEV_LAUNCHER_DB_WAIT_SECONDS, default 600s -- measured, see
    # DEFAULT_DB_WAIT_SECONDS); on expiry the children start anyway and the
    # log says so. mfx-boot-01.
    _wait_for_postgres()

    # Start Dashboard
    dash_proc, dash_log_f = _start_dashboard()
    time.sleep(3)

    # Start Genesis Daemon
    daemon_proc, daemon_log_f = _start_daemon()

    # Start Proposal Genesis Daemon (SAM.gov scanning, proposal intelligence)
    pg_proc, pg_log_f = _start_proposal_genesis()

    # Start Kanban Scheduler (autonomous task execution)
    kb_proc, kb_log_f = _start_kanban_scheduler()

    # Start PR Watcher (merges CI-green PRs — closes the autonomous loop)
    pw_proc, pw_log_f = _start_pr_watcher()

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

                # Check PR Watcher
                if pw_proc.poll() is not None:
                    _log(f"PR Watcher exited (code {pw_proc.returncode}), restarting...")
                    pw_log_f.close()
                    time.sleep(2)
                    pw_proc, pw_log_f = _start_pr_watcher()

                # Check FathomDesk Trading Dashboard
                if td_proc is not None and td_proc.poll() is not None:
                    _log(f"FathomDesk Dashboard exited (code {td_proc.returncode}), restarting...")
                    if td_log_f is not None:
                        td_log_f.close()
                    time.sleep(2)
                    td_proc, td_log_f = _start_trading_dashboard()

            except Exception as exc:
                _log(f"Monitor loop error (non-fatal): {exc}")

    except KeyboardInterrupt:
        _log("Shutdown requested")
    finally:
        _log("Stopping services...")
        _procs = [p for p in (daemon_proc, pg_proc, kb_proc, pw_proc, td_proc, dash_proc) if p is not None]
        for proc in _procs:
            proc.terminate()
        for proc in _procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        dash_log_f.close()
        daemon_log_f.close()
        pg_log_f.close()
        kb_log_f.close()
        pw_log_f.close()
        if td_log_f is not None:
            td_log_f.close()
        _release_pid_lock()
        _log("ICDEV™ Services stopped")


if __name__ == "__main__":
    main()
