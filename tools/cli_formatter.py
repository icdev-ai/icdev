#!/usr/bin/env python3
"""
CUI // SP-CTI
ICDEV CLI Output Formatter
===========================
Universal pretty-print for all ICDEV CLI tools.
Converts structured dicts to colored, tabular terminal output.

Usage (in any tool's main()):
    from tools.cli_formatter import CLIOutput
    out = CLIOutput(json_mode=args.json)
    out.print(result)
    # Automatically routes to json.dumps or pretty-print

Direct usage:
    python tools/cli_formatter.py --demo
"""



# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

class _Colors:
    """ANSI escape codes. Disabled when not TTY or color=False."""
    RESET = '\x1b[0m'
    BOLD = '\x1b[1m'
    DIM = '\x1b[2m'
    UNDERLINE = '\x1b[4m'
    RED = '\x1b[31m'
    GREEN = '\x1b[32m'
    YELLOW = '\x1b[33m'
    BLUE = '\x1b[34m'
    MAGENTA = '\x1b[35m'
    CYAN = '\x1b[36m'
    WHITE = '\x1b[37m'
    BG_RED = '\x1b[41m'
    BG_GREEN = '\x1b[42m'
    BG_YELLOW = '\x1b[43m'
    BG_BLUE = '\x1b[44m'

