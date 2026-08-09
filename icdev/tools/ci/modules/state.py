# CUI // SP-CTI
"""ICDEV™ workflow state helper.

A tiny persistent-state object that the ``tools/ci/workflows/icdev_*``
pipeline scripts pass between steps. Two transports are supported:

* a flat JSON file under ``agents/<run_id>/icdev_state.json`` and
* a stdin/stdout pipe (one-line JSON).

This module is intentionally minimal — no concurrency, no locking, no
schema migration, no DB writes, no LLM calls, no network. It exists so a
``plan | build | test`` shell pipeline can carry a few well-known fields
between scripts without re-deriving them at each step.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/ci/modules/state.md`` (OPT-75 Phase 3
clean-room rewrite).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional


# The whitelisted set of fields that round-trip through both transports.
# Anything else passed to ``update()`` is silently dropped, and anything
# else loaded from disk is preserved internally but stripped from the
# stdout pipe payload (so callers can stash extra metadata locally
# without leaking it cross-process).
CORE_FIELDS: FrozenSet[str] = frozenset({
    "run_id",
    "issue_number",
    "branch_name",
    "plan_file",
    "issue_class",
    "platform",
    "project_id",
})


# Resolve the repo root once at import time. The state file always lives
# under ``<repo>/agents/<run_id>/icdev_state.json`` regardless of the
# caller's working directory.
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
_AGENTS_ROOT: Path = _REPO_ROOT / "agents"
_STATE_FILENAME: str = "icdev_state.json"


_module_logger = get_logger(__name__)


def _resolve_logger(injected) -> logging.Logger:
    """Pick the caller-supplied logger if it has the right shape, else
    fall back to the module logger. Keeps the public API loose so the
    workflow scripts can pass any of the duck-typed loggers they like
    without us asserting a class."""
    if injected is None:
        return _module_logger
    for attr in ("debug", "warning"):
        if not hasattr(injected, attr):
            return _module_logger
    return injected


class ICDevState:
    """Persistent state object for an ICDEV™ CI/CD workflow run."""

    __slots__ = ("run_id", "_store", "_log")

    # ── Construction ────────────────────────────────────────────────

    def __init__(self, run_id: str, logger: Any = None) -> None:
        self.run_id: str = run_id
        self._store: Dict[str, Any] = {"run_id": run_id}
        self._log: logging.Logger = _resolve_logger(logger)

    @classmethod
    def load(cls, run_id: str, logger: Any = None) -> "ICDevState":
        """Return the state for ``run_id``, reading the on-disk file if
        it exists. A missing or malformed file produces an empty state
        instead of raising — the caller can check ``.get(key)`` to tell
        whether anything was actually loaded.
        """
        instance = cls(run_id, logger=logger)
        path = instance.state_file
        if not path.exists():
            return instance
        try:
            raw = path.read_text(encoding="utf-8")
            decoded = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            instance._log.warning(
                "ICDevState.load: ignoring corrupt state file %s: %s",
                path, exc,
            )
            return instance
        if isinstance(decoded, dict):
            decoded.setdefault("run_id", run_id)
            instance._store = decoded
            instance._log.debug("ICDevState.load: read %s", path)
        else:
            instance._log.warning(
                "ICDevState.load: %s did not contain a JSON object", path
            )
        return instance

    @classmethod
    def from_stdin(cls, logger: Any = None) -> Optional["ICDevState"]:
        """Try to read piped state from stdin. Returns None when stdin
        is a TTY, when stdin is empty, when the payload isn't valid
        JSON, or when the payload lacks a ``run_id`` key.
        """
        if sys.stdin.isatty():
            return None
        try:
            raw = sys.stdin.read().strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, dict):
            return None
        run_id = decoded.get("run_id")
        if not run_id:
            return None
        instance = cls(str(run_id), logger=logger)
        instance._store = decoded
        return instance

    # ── Reads ───────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._store)

    @property
    def state_dir(self) -> Path:
        return _AGENTS_ROOT / self.run_id

    @property
    def state_file(self) -> Path:
        return self.state_dir / _STATE_FILENAME

    # ── Mutations ───────────────────────────────────────────────────

    def update(self, **fields: Any) -> None:
        """Merge ``fields`` into the state, ignoring any key not on the
        :data:`CORE_FIELDS` whitelist and any explicitly-None value."""
        for key, value in fields.items():
            if key not in CORE_FIELDS:
                continue
            if value is None:
                continue
            self._store[key] = value

    def save(self, workflow_step: str = "") -> None:
        """Persist the state to disk, creating the parent directory on
        first write. ``workflow_step`` is informational only and is
        emitted as a debug log line so callers can correlate the save
        with their pipeline stage."""
        path = self.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._store, indent=2),
            encoding="utf-8", newline="",
        )
        if workflow_step:
            self._log.debug(
                "ICDevState.save: %s -> %s", workflow_step, path
            )
        else:
            self._log.debug("ICDevState.save: -> %s", path)

    # ── Pipe transport ──────────────────────────────────────────────

    def to_stdout(self) -> None:
        """Emit the CORE_FIELDS-filtered view of the state as a single
        JSON line on stdout, ready to be piped into the next workflow
        script."""
        payload = {
            k: v for k, v in self._store.items() if k in CORE_FIELDS
        }
        sys.stdout.write(json.dumps(payload))
        sys.stdout.write("\n")
        sys.stdout.flush()

    # ── Diagnostics ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"ICDevState(run_id={self.run_id!r}, "
            f"keys={sorted(self._store.keys())})"
        )
