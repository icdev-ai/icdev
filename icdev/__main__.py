"""Allow `python -m icdev` to invoke the CLI dispatcher."""
import runpy
import sys
from pathlib import Path

_cli = Path(__file__).parent / "tools" / "cli" / "__main__.py"
sys.exit(runpy.run_path(str(_cli), run_name="__main__").get("_exit_code", 0))
