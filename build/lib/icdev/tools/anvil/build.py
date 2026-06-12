#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-42 — ANVIL `build` command (headless wrapper).

Source: `.claude/commands/build.md` (md)

Delegates to tools.anvil.runner.cli_main. Usage:

    python tools/anvil/build.py --json
    python tools/anvil/build.py --dry-run
    python tools/anvil/build.py -- ARGS...
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.anvil.runner import cli_main  # noqa: E402


if __name__ == "__main__":
    sys.exit(cli_main(
        wrapper_name="build",
        source=".claude/commands/build.md",
        kind="md",
    ))
