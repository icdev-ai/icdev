#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-42 — ANVIL `test` command (headless wrapper).

Source: `.claude/commands/test.md` (md)

Delegates to tools.anvil.runner.cli_main. Usage:

    python tools/anvil/test.py --json
    python tools/anvil/test.py --dry-run
    python tools/anvil/test.py -- ARGS...
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
        wrapper_name="test",
        source=".claude/commands/test.md",
        kind="md",
    ))
