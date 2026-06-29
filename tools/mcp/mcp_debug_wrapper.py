#!/usr/bin/env python3
from __future__ import annotations
"""Debug wrapper: logs all stdin/stdout to a file then proxies to unified_server.

Goose / Claude Code config.yaml example:
  command: python $ICDEV_PROJECT_ROOT/tools/mcp/mcp_debug_wrapper.py
"""
import datetime
import os
import subprocess
import sys
import threading
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent.parent
LOG = str(_ROOT / ".tmp" / "mcp_debug.log")
SERVER = str(_ROOT / "tools" / "mcp" / "unified_server.py")
PYTHON = sys.executable

os.makedirs(os.path.dirname(LOG), exist_ok=True)

def log(msg):
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")

log(f"=== MCP wrapper started, python={PYTHON}, cwd={os.getcwd()} ===")

proc = subprocess.Popen(
    [PYTHON, SERVER],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

def relay_stdin():
    try:
        while True:
            chunk = sys.stdin.buffer.read1(4096)  # read1 returns immediately with available bytes
            if not chunk:
                break
            log(f"STDIN→server ({len(chunk)}B): {chunk}")  # log full bytes
            proc.stdin.write(chunk)
            proc.stdin.flush()
    except Exception as e:
        log(f"stdin relay error: {e}")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

def relay_stdout():
    try:
        while True:
            chunk = proc.stdout.read1(4096)  # read1 returns immediately with available bytes
            if not chunk:
                break
            log(f"server→STDOUT ({len(chunk)}B): {chunk[:300]}")
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except Exception as e:
        log(f"stdout relay error: {e}")

def relay_stderr():
    try:
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            log(f"SERVER STDERR: {chunk.decode(errors='replace')[:500]}")
    except Exception as e:
        log(f"stderr relay error: {e}")

t1 = threading.Thread(target=relay_stdin, daemon=True)
t2 = threading.Thread(target=relay_stdout, daemon=True)
t3 = threading.Thread(target=relay_stderr, daemon=True)
t1.start(); t2.start(); t3.start()
proc.wait()
log(f"=== server exited rc={proc.returncode} ===")
