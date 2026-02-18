# CUI // SP-CTI
# ICDEV Workflow State Management
# Adapted from ADW state.py

"""
Persistent state management for ICDEV CI/CD workflows.

State is stored at agents/{run_id}/icdev_state.json and tracks:
- run_id, issue_number, branch_name, plan_file, issue_class
- platform (github/gitlab)

Supports piping between scripts via stdin/stdout (ADW pattern).

Usage:
    state = ICDevState.load(run_id, logger)
    state.update(branch_name="feat-123")
    state.save("icdev_plan")
    state.to_stdout()  # For piping
"""

import json
import sys
from pathlib import Path
from typing import Optional, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Core fields persisted to state file
CORE_FIELDS = {
    "run_id", "issue_number", "branch_name", "plan_file",
    "issue_class", "platform", "project_id",
}


class ICDevState:
    """Persistent workflow state for ICDEV CI/CD pipelines."""

    def __init__(self, run_id: str, logger=None):
        self.run_id = run_id
        self._data = {"run_id": run_id}
        self._logger = logger

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, **kwargs):
        """Update state with key-value pairs (only core fields)."""
        for key, value in kwargs.items():
            if key in CORE_FIELDS and value is not None:
                self._data[key] = value

    @property
    def state_dir(self) -> Path:
        return PROJECT_ROOT / "agents" / self.run_id

    @property
    def state_file(self) -> Path:
        return self.state_dir / "icdev_state.json"

    def save(self, workflow_step: str = ""):
        """Persist state to file."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self._data, f, indent=2)
        if self._logger:
            self._logger.debug(f"State saved to {self.state_file}")

    @classmethod
    def load(cls, run_id: str, logger=None) -> "ICDevState":
        """Load state from file, or create new."""
        state = cls(run_id, logger)
        state_file = state.state_file
        if state_file.exists():
            try:
                with open(state_file) as f:
                    data = json.load(f)
                state._data = data
                if logger:
                    logger.debug(f"State loaded from {state_file}")
            except (json.JSONDecodeError, IOError) as e:
                if logger:
                    logger.warning(f"Could not load state: {e}")
        return state

    @classmethod
    def from_stdin(cls, logger=None) -> Optional["ICDevState"]:
        """Read state from stdin if piped (ADW chaining pattern).

        Used for: script1 | script2 (pipe state between workflow steps)
        """
        if sys.stdin.isatty():
            return None

        try:
            raw = sys.stdin.read().strip()
            if not raw:
                return None
            data = json.loads(raw)
            run_id = data.get("run_id")
            if not run_id:
                return None
            state = cls(run_id, logger)
            state._data = data
            return state
        except (json.JSONDecodeError, IOError):
            return None

    def to_stdout(self):
        """Write core state to stdout for piping to next script."""
        output = {k: v for k, v in self._data.items() if k in CORE_FIELDS}
        print(json.dumps(output))

    def to_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self):
        return f"ICDevState(run_id={self.run_id}, keys={list(self._data.keys())})"
