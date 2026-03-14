"""ICDEV Services Launcher — starts Dashboard + Genesis Daemon.

Spawned by Task Scheduler via start_daemon.bat.
Dashboard runs as a subprocess; daemon runs in the main process.
"""
import subprocess
import sys
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

# Ensure .tmp dirs exist
os.makedirs(".tmp/genesis", exist_ok=True)

# Start Dashboard as a detached subprocess
dash_log = open(".tmp/dashboard.log", "a")
dash_proc = subprocess.Popen(
    [sys.executable, "tools/dashboard/app.py"],
    stdout=dash_log,
    stderr=dash_log,
    cwd=ROOT,
)
print(f"Dashboard started (PID {dash_proc.pid})")

# Give dashboard time to bind port
time.sleep(3)

# Run Genesis Daemon in foreground (so Task Scheduler can track it)
try:
    daemon_log = open(".tmp/genesis/daemon.log", "a")
    daemon = subprocess.Popen(
        [sys.executable, "tools/genesis/daemon.py"],
        stdout=daemon_log,
        stderr=daemon_log,
        cwd=ROOT,
    )
    print(f"Genesis Daemon started (PID {daemon.pid})")
    daemon.wait()
except KeyboardInterrupt:
    print("Shutting down...")
finally:
    dash_proc.terminate()
    dash_log.close()
    daemon_log.close()
