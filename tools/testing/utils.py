# CUI // SP-CTI
# ICDEV Testing Utilities
# Adapted from ADW utils.py for Gov/DoD testing workflows

"""Utility functions for ICDEV testing framework.

Provides JSON parsing (handles markdown wrapping), logger setup,
safe subprocess environments, and run ID generation.
"""

import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar, Type, Union, Dict

T = TypeVar('T')

# Project root — tools/testing/utils.py → go up 2 levels
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def make_run_id() -> str:
    """Generate a short 8-character UUID for test run tracking."""
    return str(uuid.uuid4())[:8]


def setup_logger(run_id: str, phase: str = "test_run") -> logging.Logger:
    """Set up logger that writes to both console and file.

    Adapted from ADW setup_logger pattern with dual-output (file + console).

    Args:
        run_id: The test run ID
        phase: Phase name (test_run, health_check, e2e_test, etc.)

    Returns:
        Configured logger instance
    """
    # Create log directory: .tmp/test_runs/{run_id}/{phase}/
    log_dir = PROJECT_ROOT / ".tmp" / "test_runs" / run_id / phase
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "execution.log"

    # Create logger with unique name
    logger = logging.getLogger(f"icdev_{run_id}_{phase}")
    logger.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates
    logger.handlers.clear()

    # File handler - captures everything
    file_handler = logging.FileHandler(str(log_file), mode='a')
    file_handler.setLevel(logging.DEBUG)

    # Console handler - INFO and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    # Format with timestamp for file
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Simpler format for console
    console_formatter = logging.Formatter('%(message)s')

    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"ICDEV Test Logger initialized - Run: {run_id}, Phase: {phase}")
    logger.debug(f"Log file: {log_file}")

    return logger


def get_logger(run_id: str, phase: str = "test_run") -> logging.Logger:
    """Get existing logger by run ID and phase."""
    return logging.getLogger(f"icdev_{run_id}_{phase}")


def parse_json(text: str, target_type: Type[T] = None) -> Union[T, Any]:
    """Parse JSON that may be wrapped in markdown code blocks.

    Directly adapted from ADW parse_json — handles:
    - Raw JSON
    - JSON wrapped in ```json ... ```
    - JSON wrapped in ``` ... ```
    - JSON with extra whitespace or newlines

    Args:
        text: String containing JSON, possibly wrapped in markdown
        target_type: Optional Pydantic model or List[Model] to validate into

    Returns:
        Parsed JSON object, optionally validated as target_type

    Raises:
        ValueError: If JSON cannot be parsed from the text
    """
    # Try to extract JSON from markdown code blocks
    code_block_pattern = r'```(?:json)?\s*\n(.*?)\n```'
    match = re.search(code_block_pattern, text, re.DOTALL)

    if match:
        json_str = match.group(1).strip()
    else:
        json_str = text.strip()

    # Try to find JSON array or object boundaries if not already clean
    if not (json_str.startswith('[') or json_str.startswith('{')):
        # Look for JSON array
        array_start = json_str.find('[')
        array_end = json_str.rfind(']')

        # Look for JSON object
        obj_start = json_str.find('{')
        obj_end = json_str.rfind('}')

        # Determine which comes first and extract accordingly
        if array_start != -1 and (obj_start == -1 or array_start < obj_start):
            if array_end != -1:
                json_str = json_str[array_start:array_end + 1]
        elif obj_start != -1:
            if obj_end != -1:
                json_str = json_str[obj_start:obj_end + 1]

    try:
        result = json.loads(json_str)

        # If target_type is provided and has __origin__ (e.g., List[SomeType])
        if target_type and hasattr(target_type, '__origin__'):
            if target_type.__origin__ is list:
                item_type = target_type.__args__[0]
                if hasattr(item_type, 'model_validate'):
                    result = [item_type.model_validate(item) for item in result]
                elif hasattr(item_type, 'parse_obj'):
                    result = [item_type.parse_obj(item) for item in result]
        elif target_type:
            if hasattr(target_type, 'model_validate'):
                result = target_type.model_validate(result)
            elif hasattr(target_type, 'parse_obj'):
                result = target_type.parse_obj(result)

        return result
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}. Text was: {json_str[:200]}...")


def get_safe_subprocess_env() -> Dict[str, str]:
    """Get filtered environment variables safe for subprocess execution.

    Adapted from ADW get_safe_subprocess_env with ICDEV-specific variables.
    Prevents accidental exposure of sensitive credentials to subprocesses.

    Returns:
        Dictionary containing only required environment variables
    """
    safe_env_vars = {
        # ICDEV Configuration
        "ICDEV_DB_PATH": os.getenv("ICDEV_DB_PATH", str(PROJECT_ROOT / "data" / "icdev.db")),
        "ICDEV_PROJECT_ROOT": os.getenv("ICDEV_PROJECT_ROOT", str(PROJECT_ROOT)),

        # Anthropic / Bedrock Configuration
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "us-gov-west-1"),

        # Claude Code Configuration
        "CLAUDE_CODE_PATH": os.getenv("CLAUDE_CODE_PATH", "claude"),
        "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": os.getenv(
            "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR", "true"
        ),

        # GitLab Configuration (optional)
        "GITLAB_TOKEN": os.getenv("GITLAB_TOKEN"),
        "GITLAB_URL": os.getenv("GITLAB_URL"),

        # Essential system environment variables
        "HOME": os.getenv("HOME") or os.getenv("USERPROFILE"),
        "USER": os.getenv("USER") or os.getenv("USERNAME"),
        "PATH": os.getenv("PATH"),
        "SHELL": os.getenv("SHELL"),
        "TERM": os.getenv("TERM"),
        "LANG": os.getenv("LANG"),

        # Windows-specific — needed for Python user site-packages and gh keyring
        "USERPROFILE": os.getenv("USERPROFILE"),
        "APPDATA": os.getenv("APPDATA"),
        "LOCALAPPDATA": os.getenv("LOCALAPPDATA"),
        "SYSTEMROOT": os.getenv("SYSTEMROOT"),
        "TEMP": os.getenv("TEMP"),
        "TMP": os.getenv("TMP"),

        # GitHub CLI — needed for gh auth keyring access
        "GH_TOKEN": os.getenv("GH_TOKEN") or os.getenv("GITHUB_PAT"),

        # Python-specific
        "PYTHONPATH": os.getenv("PYTHONPATH"),
        "PYTHONUNBUFFERED": "1",

        # Working directory
        "PWD": os.getcwd(),
    }

    # Filter out None values
    return {k: v for k, v in safe_env_vars.items() if v is not None}


def timestamp_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat() + "Z"


def ensure_run_dir(run_id: str) -> Path:
    """Ensure test run directory exists and return its path."""
    run_dir = PROJECT_ROOT / ".tmp" / "test_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
