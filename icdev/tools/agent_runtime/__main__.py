# CUI // SP-CTI
"""``python -m tools.agent_runtime`` — standalone agent runtime CLI entry point.

Provided for the pip-less checkout, where the ``icdev`` console script may not be
installed. Delegates to :func:`tools.agent_runtime.cli.main`, so it accepts the
same surface as ``icdev chat`` / ``icdev sessions``.
"""
from __future__ import annotations

import sys

from tools.agent_runtime.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
