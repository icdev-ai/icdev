"""Generate compliance report from security baseline and environment state."""

import json
import sys
from pathlib import Path

import yaml


def load_security_baseline(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_environment_state(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def generate_report(baseline: dict, env_state: dict) -> dict:
    return {
        "baseline_controls": list(baseline.keys()),
        "environment_keys": list(env_state.keys()),
        "findings": [],
    }


def main(
    baseline_path: str = "security_baseline.yaml",
    env_state_path: str = "environment_state.json",
) -> None:
    baseline = load_security_baseline(Path(baseline_path))
    env_state = load_environment_state(Path(env_state_path))
    report = generate_report(baseline, env_state)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        baseline_path=args[0] if len(args) > 0 else "security_baseline.yaml",
        env_state_path=args[1] if len(args) > 1 else "environment_state.json",
    )
