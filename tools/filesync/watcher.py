#!/usr/bin/env python3
# CUI // SP-CTI
"""File watcher — optional watchdog + periodic scan fallback (D-SYNC-8).

Pattern: tools/monitor/heartbeat_daemon.py for periodic check pattern.
Uses watchdog if available; falls back to periodic os.walk scan.
"""

import os
import time
import threading
from typing import Callable, Optional, Set

# Graceful watchdog import
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    _HAS_WATCHDOG = True
except ImportError:
    Observer = None
    FileSystemEventHandler = object
    _HAS_WATCHDOG = False


class _SyncEventHandler(FileSystemEventHandler):
    """Watchdog event handler that collects changed paths."""

    def __init__(self, debounce_seconds: float = 2.0):
        super().__init__()
        self._changed: Set[str] = set()
        self._lock = threading.Lock()
        self._debounce = debounce_seconds
        self._last_event = 0.0

    def _record(self, path: str):
        with self._lock:
            self._changed.add(path)
            self._last_event = time.monotonic()

    def on_created(self, event):
        if not event.is_directory:
            self._record(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._record(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._record(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._record(event.src_path)
            self._record(event.dest_path)

    def drain(self) -> Set[str]:
        """Return accumulated changes if debounce period has passed."""
        with self._lock:
            elapsed = time.monotonic() - self._last_event
            if self._changed and elapsed >= self._debounce:
                paths = self._changed.copy()
                self._changed.clear()
                return paths
        return set()


class FileWatcher:
    """Watch a directory for changes and trigger sync callback.

    Uses watchdog if installed; otherwise falls back to periodic scan
    comparing mtime of files.
    """

    def __init__(
        self,
        watch_path: str,
        on_changes: Callable[[Set[str]], None],
        debounce_seconds: float = 2.0,
        recursive: bool = True,
        fallback_scan_interval: float = 60.0,
    ):
        """Initialize file watcher.

        Args:
            watch_path: Directory to watch.
            on_changes: Callback invoked with set of changed paths.
            debounce_seconds: Wait for rapid changes to settle.
            recursive: Watch subdirectories.
            fallback_scan_interval: Periodic scan interval when watchdog unavailable.
        """
        self._path = watch_path
        self._callback = on_changes
        self._debounce = debounce_seconds
        self._recursive = recursive
        self._scan_interval = fallback_scan_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._observer = None
        self._using_watchdog = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def using_watchdog(self) -> bool:
        return self._using_watchdog

    def start(self):
        """Start watching in a background thread."""
        if self.is_running:
            return
        self._stop_event.clear()

        if _HAS_WATCHDOG:
            self._thread = threading.Thread(target=self._run_watchdog, daemon=True)
            self._using_watchdog = True
        else:
            self._thread = threading.Thread(target=self._run_polling, daemon=True)
            self._using_watchdog = False

        self._thread.start()

    def stop(self):
        """Stop watching."""
        self._stop_event.set()
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                pass
            self._observer = None
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def _run_watchdog(self):
        """Watch using watchdog library."""
        handler = _SyncEventHandler(debounce_seconds=self._debounce)
        self._observer = Observer()
        self._observer.schedule(handler, self._path, recursive=self._recursive)
        self._observer.start()

        try:
            while not self._stop_event.is_set():
                changes = handler.drain()
                if changes:
                    try:
                        self._callback(changes)
                    except Exception:
                        pass
                self._stop_event.wait(timeout=1.0)
        finally:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                pass

    def _run_polling(self):
        """Watch using periodic mtime scan (fallback)."""
        prev_state = self._scan_mtimes()

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._scan_interval)
            if self._stop_event.is_set():
                break

            current_state = self._scan_mtimes()
            changed = set()

            # Check for new or modified files
            for path, mtime in current_state.items():
                if path not in prev_state or prev_state[path] != mtime:
                    changed.add(path)

            # Check for deleted files
            for path in prev_state:
                if path not in current_state:
                    changed.add(path)

            if changed:
                try:
                    self._callback(changed)
                except Exception:
                    pass

            prev_state = current_state

    def _scan_mtimes(self) -> dict:
        """Build {path: mtime} map via os.walk."""
        state = {}
        try:
            for root, _dirs, files in os.walk(self._path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        state[fpath] = os.path.getmtime(fpath)
                    except OSError:
                        pass
        except OSError:
            pass
        return state
