# CUI // SP-CTI
"""ICDEV™ testing framework utilities.

A small grab-bag of helpers used by the scripts under
``tools/testing/``: run-id generation, dual-sink logger setup, tolerant
JSON extraction, subprocess environment scrubbing, ISO timestamps, and
per-run directory creation. Each helper is self-contained and shares
nothing but :data:`PROJECT_ROOT` with its siblings.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/testing/utils.md`` (OPT-75 Phase 3
clean-room rewrite).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar


PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

T = TypeVar("T")

# Internal constant — fenced code-block extractor used by parse_json.
_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n```",
    re.DOTALL,
)


# ────────────────────────────────────────────────────────────────────────────
# Run id + run directory
# ────────────────────────────────────────────────────────────────────────────


def make_run_id() -> str:
    """Return an 8-character lowercase hex run identifier."""
    return uuid.uuid4().hex[:8]


def ensure_run_dir(run_id: str) -> Path:
    """Create (if needed) and return ``<repo>/.tmp/test_runs/<run_id>``."""
    run_dir = PROJECT_ROOT / ".tmp" / "test_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ────────────────────────────────────────────────────────────────────────────
# Logger setup (dual sink)
# ────────────────────────────────────────────────────────────────────────────


_LOGGER_NAME_FMT = "icdev_{run_id}_{phase}"
_FILE_FMT = "%(asctime)s - %(levelname)s - %(message)s"
_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONSOLE_FMT = "%(message)s"


def _logger_name(run_id: str, phase: str) -> str:
    return _LOGGER_NAME_FMT.format(run_id=run_id, phase=phase)


def setup_logger(run_id: str, phase: str = "test_run") -> logging.Logger:
    """Configure (or reconfigure) the dual-sink logger for a test run.

    Writes the full DEBUG stream to a per-phase log file under
    ``.tmp/test_runs/<run_id>/<phase>/execution.log`` and INFO+ to
    stdout. Re-calling for the same ``(run_id, phase)`` does not double
    the handlers — any existing handlers are cleared first.
    """
    log_dir = PROJECT_ROOT / ".tmp" / "test_runs" / run_id / phase
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "execution.log"

    logger = logging.getLogger(_logger_name(run_id, phase))
    logger.setLevel(logging.DEBUG)
    # Drop any handlers from a previous setup_logger call so we don't
    # emit duplicate lines on re-init.
    while logger.handlers:
        logger.removeHandler(logger.handlers[0])

    file_handler = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_FILE_DATEFMT))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FMT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    # Don't propagate to the root logger — otherwise pytest's caplog
    # captures duplicates.
    logger.propagate = False

    logger.info(
        "ICDEV™ Test Logger initialized - Run: %s, Phase: %s",
        run_id, phase,
    )
    logger.debug("Log file: %s", log_file)
    return logger


def get_logger(run_id: str, phase: str = "test_run") -> logging.Logger:
    """Return the previously-configured logger for ``(run_id, phase)``."""
    return logging.getLogger(_logger_name(run_id, phase))


# ────────────────────────────────────────────────────────────────────────────
# Tolerant JSON extraction
# ────────────────────────────────────────────────────────────────────────────


def _slice_to_json_boundary(text: str) -> str:
    """If ``text`` is not already a JSON object/array, walk inward to
    the first ``{`` or ``[`` and the last matching ``}`` or ``]`` and
    return that slice. Returns ``text`` unchanged if no obvious slice
    can be made."""
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return stripped

    arr_open = stripped.find("[")
    arr_close = stripped.rfind("]")
    obj_open = stripped.find("{")
    obj_close = stripped.rfind("}")

    array_first = (
        arr_open != -1
        and (obj_open == -1 or arr_open < obj_open)
    )
    if array_first and arr_close != -1:
        return stripped[arr_open: arr_close + 1]
    if obj_open != -1 and obj_close != -1:
        return stripped[obj_open: obj_close + 1]
    return stripped


def _validate_with_target(payload: Any, target_type: Type[T]) -> Any:
    origin = getattr(target_type, "__origin__", None)
    if origin is list:
        item_type = target_type.__args__[0]  # type: ignore[attr-defined]
        validator = (
            getattr(item_type, "model_validate", None)
            or getattr(item_type, "parse_obj", None)
        )
        if validator is None:
            return payload
        return [validator(item) for item in payload]
    validator = (
        getattr(target_type, "model_validate", None)
        or getattr(target_type, "parse_obj", None)
    )
    if validator is None:
        return payload
    return validator(payload)


def parse_json(text: str, target_type: Optional[Type[T]] = None) -> Any:
    """Extract JSON from a (possibly markdown-wrapped) string.

    Handles raw JSON, fenced code blocks, and prose surrounding a single
    JSON object/array. Optionally validates the result against a
    Pydantic-shaped ``target_type`` (or ``List[Model]``). Raises
    :class:`ValueError` on any parse failure.
    """
    fenced = _FENCE_RE.search(text or "")
    candidate = fenced.group(1).strip() if fenced else (text or "").strip()
    candidate = _slice_to_json_boundary(candidate)

    try:
        result = json.loads(candidate)
    except json.JSONDecodeError as exc:
        snippet = candidate[:200]
        raise ValueError(
            f"Failed to parse JSON: {exc}. Text was: {snippet}..."
        ) from exc

    if target_type is None:
        return result
    return _validate_with_target(result, target_type)


# ────────────────────────────────────────────────────────────────────────────
# Subprocess environment scrubber
# ────────────────────────────────────────────────────────────────────────────


def get_safe_subprocess_env() -> Dict[str, str]:
    """Return a scrubbed environment dict suitable for ``subprocess.run``.

    Only the variables explicitly enumerated below are forwarded; every
    other key from the calling environment is dropped to minimise the
    risk of leaking unrelated credentials into a child process. Values
    that resolve to ``None`` are stripped from the result.

    PROJECT_ROOT is always prepended to PYTHONPATH so that ``import tools``
    resolves in subprocesses even when the parent environment does not have
    the worktree root on sys.path.
    """
    root_str = str(PROJECT_ROOT)
    existing_pp = os.getenv("PYTHONPATH", "")
    pythonpath = root_str if not existing_pp else root_str + os.pathsep + existing_pp

    raw: Dict[str, Optional[str]] = {
        # ── ICDEV configuration ────────────────────────────────────
        "ICDEV_DB_PATH": os.getenv(
            "ICDEV_DB_PATH",
            str(PROJECT_ROOT / "data" / "icdev.db"),
        ),
        "ICDEV_PROJECT_ROOT": os.getenv(
            "ICDEV_PROJECT_ROOT",
            str(PROJECT_ROOT),
        ),
        # ── LLM provider credentials ───────────────────────────────
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "us-gov-west-1"),
        # ── Claude Code CLI ────────────────────────────────────────
        "CLAUDE_CODE_PATH": os.getenv("CLAUDE_CODE_PATH", "claude"),
        "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": os.getenv(
            "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR",
            "true",
        ),
        # ── GitLab ─────────────────────────────────────────────────
        "GITLAB_TOKEN": os.getenv("GITLAB_TOKEN"),
        "GITLAB_URL": os.getenv("GITLAB_URL"),
        # ── POSIX essentials ───────────────────────────────────────
        "HOME": os.getenv("HOME") or os.getenv("USERPROFILE"),
        "USER": os.getenv("USER") or os.getenv("USERNAME"),
        "PATH": os.getenv("PATH"),
        "SHELL": os.getenv("SHELL"),
        "TERM": os.getenv("TERM"),
        "LANG": os.getenv("LANG"),
        # ── Windows essentials ─────────────────────────────────────
        "USERPROFILE": os.getenv("USERPROFILE"),
        "APPDATA": os.getenv("APPDATA"),
        "LOCALAPPDATA": os.getenv("LOCALAPPDATA"),
        "SYSTEMROOT": os.getenv("SYSTEMROOT"),
        "TEMP": os.getenv("TEMP"),
        "TMP": os.getenv("TMP"),
        # ── GitHub CLI ─────────────────────────────────────────────
        "GH_TOKEN": os.getenv("GH_TOKEN") or os.getenv("GITHUB_PAT"),
        # ── Python tunables ────────────────────────────────────────
        # Always includes worktree root so `import tools` resolves in subprocesses.
        "PYTHONPATH": pythonpath,
        "PYTHONUNBUFFERED": "1",
        # ── Working directory at call time ─────────────────────────
        "PWD": os.getcwd(),
    }
    return {k: v for k, v in raw.items() if v is not None}


# ────────────────────────────────────────────────────────────────────────────
# Timestamps
# ────────────────────────────────────────────────────────────────────────────


def timestamp_iso() -> str:
    """Return current UTC time as ISO 8601 with a trailing ``Z``."""
    return datetime.now(timezone.utc).isoformat() + "Z"
