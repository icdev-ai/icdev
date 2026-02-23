# [TEMPLATE: CUI // SP-CTI]
"""
ICDEV CLI Output Formatter
===========================

Human-friendly terminal output formatting for ICDEV tools.

When tools are invoked with ``--human`` (instead of ``--json``), they use
this module for colorized, table-formatted, readable terminal output.

Dependencies: Python stdlib only (air-gap safe for DoD environments).

Usage::

    from tools.cli.output_formatter import (
        format_table, format_banner, format_score, format_kv,
        format_section, format_list, format_pipeline,
        format_json_human, human_output, auto_format,
        add_human_flag, should_use_human,
    )
"""

from __future__ import annotations

import argparse
import functools
import io
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

# Ensure UTF-8 output on Windows (box-drawing and Unicode block chars).
from tools.compat.platform_utils import ensure_utf8_console
ensure_utf8_console()

# ---------------------------------------------------------------------------
# CUI banner
# ---------------------------------------------------------------------------
CUI_BANNER = "# CUI // SP-CTI"

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------

def _is_tty() -> bool:
    """Return True if stdout is connected to a terminal."""
    try:
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    except Exception:
        return False


# Also respect NO_COLOR (https://no-color.org/) and FORCE_COLOR env vars.
_COLORS_ENABLED: bool = (
    os.environ.get("FORCE_COLOR", "") == "1"
    or (_is_tty() and os.environ.get("NO_COLOR") is None)
)


class _Ansi:
    """ANSI escape-code helpers.  All methods return empty strings when color
    is disabled (piped output, NO_COLOR, etc.)."""

    _CODES = {
        "reset":     "\033[0m",
        "bold":      "\033[1m",
        "dim":       "\033[2m",
        "underline": "\033[4m",
        "red":       "\033[31m",
        "green":     "\033[32m",
        "yellow":    "\033[33m",
        "blue":      "\033[34m",
        "magenta":   "\033[35m",
        "cyan":      "\033[36m",
    }

    @classmethod
    def code(cls, name: str) -> str:
        if not _COLORS_ENABLED:
            return ""
        return cls._CODES.get(name, "")

    @classmethod
    def wrap(cls, text: str, *styles: str) -> str:
        """Wrap *text* with one or more ANSI styles."""
        if not _COLORS_ENABLED or not styles:
            return text
        prefix = "".join(cls._CODES.get(s, "") for s in styles)
        return f"{prefix}{text}{cls._CODES['reset']}"

    @classmethod
    def strip(cls, text: str) -> str:
        """Remove all ANSI escape sequences from *text*."""
        import re
        return re.sub(r"\033\[[0-9;]*m", "", text)


C = _Ansi  # short alias

# ---------------------------------------------------------------------------
# Value-based auto-coloring
# ---------------------------------------------------------------------------

_VALUE_COLORS: List[Tuple[str, List[str]]] = [
    # (pattern_substring, [ansi_styles])
    ("critical",       ["red", "bold"]),
    ("high",           ["red"]),
    ("firing",         ["red", "bold"]),
    ("failed",         ["red"]),
    ("fail",           ["red"]),
    ("error",          ["red"]),
    ("blocked",        ["red"]),
    ("not_met",        ["red"]),
    ("not_satisfied",  ["red"]),
    ("non_compliant",  ["red"]),
    ("expired",        ["red"]),
    ("degraded",       ["yellow", "bold"]),
    ("medium",         ["yellow"]),
    ("warning",        ["yellow"]),
    ("pending",        ["yellow"]),
    ("partial",        ["yellow"]),
    ("skipped",        ["dim"]),
    ("healthy",        ["green"]),
    ("pass",           ["green"]),
    ("passed",         ["green"]),
    ("completed",      ["green"]),
    ("active",         ["green"]),
    ("resolved",       ["green"]),
    ("satisfied",      ["green"]),
    ("compliant",      ["green"]),
    ("low",            ["green"]),
    ("info",           ["blue"]),
]


def _auto_color_value(value: str) -> str:
    """Apply color to *value* if it matches a known status pattern."""
    lower = value.lower().strip()
    for pattern, styles in _VALUE_COLORS:
        if pattern in lower:
            return C.wrap(value, *styles)
    return value


def _visible_len(text: str) -> int:
    """Return the display width of *text*, ignoring ANSI codes."""
    return len(C.strip(str(text)))

# ---------------------------------------------------------------------------
# CUI wrapper helper
# ---------------------------------------------------------------------------

def _cui_wrap(text: str, classification: Optional[str] = None) -> str:
    """Prepend and append classification banner if provided."""
    if not classification:
        return text
    banner = C.wrap(f"  {classification}  ", "bold", "yellow")
    rule = C.wrap("-" * max(40, _visible_len(classification) + 4), "yellow")
    return f"{rule}\n{banner}\n{rule}\n\n{text}\n\n{rule}\n{banner}\n{rule}"

# ---------------------------------------------------------------------------
# 1. format_table
# ---------------------------------------------------------------------------

def format_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    title: Optional[str] = None,
    classification: Optional[str] = None,
) -> str:
    """Render an ASCII table with box-drawing characters and auto-width columns.

    Values matching known status patterns are automatically colorized.

    Args:
        headers: Column header strings.
        rows: Iterable of row tuples/lists (same length as *headers*).
        title: Optional title displayed above the table.
        classification: Optional CUI marking banner.

    Returns:
        Fully formatted table string.
    """
    str_rows = [[str(c) for c in row] for row in rows]

    # Column widths (based on raw text, not ANSI codes)
    widths = [len(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def _hline(left: str, mid: str, right: str, fill: str = "\u2500") -> str:
        return left + mid.join(fill * (w + 2) for w in widths) + right

    top    = _hline("\u250c", "\u252c", "\u2510")
    sep    = _hline("\u251c", "\u253c", "\u2524")
    bottom = _hline("\u2514", "\u2534", "\u2518")

    def _row_str(cells: List[str], color_fn: Optional[Callable] = None) -> str:
        parts = []
        for i, cell in enumerate(cells):
            w = widths[i] if i < len(widths) else 0
            display = color_fn(cell) if color_fn else cell
            pad = w - len(cell)  # pad using raw len
            parts.append(f" {display}{' ' * pad} ")
        return "\u2502" + "\u2502".join(parts) + "\u2502"

    lines: List[str] = []
    if title:
        lines.append(C.wrap(f"  {title}", "bold", "underline"))
        lines.append("")
    lines.append(top)
    lines.append(_row_str(list(headers), lambda c: C.wrap(c, "bold", "cyan")))
    lines.append(sep)
    for row in str_rows:
        lines.append(_row_str(row, _auto_color_value))
    lines.append(bottom)

    result = "\n".join(lines)
    return _cui_wrap(result, classification)

# ---------------------------------------------------------------------------
# 2. format_banner
# ---------------------------------------------------------------------------

_BANNER_STYLES = {
    "healthy":  ("green",),
    "degraded": ("yellow",),
    "critical": ("red", "bold"),
    "info":     ("blue",),
}


def format_banner(
    status: str,
    message: str,
    classification: Optional[str] = None,
) -> str:
    """Full-width colored status banner.

    Args:
        status: One of healthy, degraded, critical, info.
        message: Text to display inside the banner.
        classification: Optional CUI marking banner.
    """
    styles = _BANNER_STYLES.get(status.lower(), ("blue",))
    icon_map = {"healthy": "[OK]", "degraded": "[!!]", "critical": "[XX]", "info": "[ii]"}
    icon = icon_map.get(status.lower(), "[--]")
    width = max(60, len(message) + 12)
    rule = "\u2550" * width
    inner = f"  {icon}  {message}"
    pad = width - _visible_len(inner)
    text = "\n".join([
        C.wrap(rule, *styles),
        C.wrap(f"{inner}{' ' * max(pad, 0)}", *styles),
        C.wrap(rule, *styles),
    ])
    return _cui_wrap(text, classification)

# ---------------------------------------------------------------------------
# 3. format_score
# ---------------------------------------------------------------------------

def format_score(
    value: float,
    threshold: float,
    label: str,
    width: int = 30,
    classification: Optional[str] = None,
) -> str:
    """Colored score display with Unicode bar visualization.

    Green if value >= threshold, yellow if >= 80% of threshold, red otherwise.
    """
    if value >= threshold:
        style = "green"
    elif value >= threshold * 0.8:
        style = "yellow"
    else:
        style = "red"

    clamped = max(0.0, min(1.0, value))
    filled = int(round(clamped * width))
    bar = "\u2588" * filled + "\u2591" * (width - filled)

    score_str = f"{value:.2f}" if isinstance(value, float) else str(value)
    thresh_str = f"{threshold:.2f}" if isinstance(threshold, float) else str(threshold)

    text = (
        f"  {C.wrap(label, 'bold')}  "
        f"{C.wrap(bar, style)}  "
        f"{C.wrap(score_str, style, 'bold')} / {thresh_str}"
    )
    return _cui_wrap(text, classification)

# ---------------------------------------------------------------------------
# 4. format_kv
# ---------------------------------------------------------------------------

def format_kv(
    pairs: Union[Dict[str, Any], List[Tuple[str, Any]]],
    title: Optional[str] = None,
    classification: Optional[str] = None,
) -> str:
    """Formatted key-value display with aligned colons and colored values."""
    if isinstance(pairs, dict):
        items = list(pairs.items())
    else:
        items = list(pairs)

    if not items:
        return ""

    max_key = max(len(str(k)) for k, _ in items)
    lines: List[str] = []
    if title:
        lines.append(C.wrap(f"  {title}", "bold", "underline"))
        lines.append("")
    for key, val in items:
        k_str = str(key).ljust(max_key)
        v_str = _auto_color_value(str(val))
        lines.append(f"  {C.wrap(k_str, 'cyan')} : {v_str}")

    result = "\n".join(lines)
    return _cui_wrap(result, classification)

# ---------------------------------------------------------------------------
# 5. format_section
# ---------------------------------------------------------------------------

def format_section(
    title: str,
    width: int = 60,
    classification: Optional[str] = None,
) -> str:
    """Decorated section header with horizontal rules."""
    rule = "\u2500" * width
    text = "\n".join([
        C.wrap(rule, "dim"),
        C.wrap(f"  {title}", "bold", "magenta"),
        C.wrap(rule, "dim"),
    ])
    return _cui_wrap(text, classification)

# ---------------------------------------------------------------------------
# 6. format_list
# ---------------------------------------------------------------------------

def format_list(
    items: Sequence[str],
    numbered: bool = False,
    bullet: str = "\u2022",
    classification: Optional[str] = None,
) -> str:
    """Bulleted or numbered list."""
    lines: List[str] = []
    for i, item in enumerate(items, start=1):
        prefix = f"  {i}." if numbered else f"  {bullet}"
        lines.append(f"{prefix} {_auto_color_value(str(item))}")
    result = "\n".join(lines)
    return _cui_wrap(result, classification)

# ---------------------------------------------------------------------------
# 7. format_pipeline
# ---------------------------------------------------------------------------

_PIPELINE_ICONS = {
    "completed": ("\u2714", "green"),   # checkmark
    "active":    ("\u25b6", "cyan"),     # play
    "pending":   ("\u25cb", "dim"),      # circle
    "blocked":   ("\u2718", "red"),      # X
    "skipped":   ("\u2500", "dim"),      # dash
}


def format_pipeline(
    steps: Sequence[Dict[str, str]],
    classification: Optional[str] = None,
) -> str:
    """Horizontal pipeline with status indicators.

    Each step is ``{"name": "...", "status": "completed|active|pending|blocked|skipped"}``.
    """
    parts: List[str] = []
    for i, step in enumerate(steps):
        name = step.get("name", "?")
        status = step.get("status", "pending").lower()
        icon, style = _PIPELINE_ICONS.get(status, ("\u25cb", "dim"))
        colored_icon = C.wrap(icon, style)
        colored_name = C.wrap(name, style)
        parts.append(f" {colored_icon} {colored_name} ")
        if i < len(steps) - 1:
            arrow_style = "green" if status == "completed" else "dim"
            parts.append(C.wrap("\u2500\u25b8", arrow_style))

    text = "".join(parts)
    return _cui_wrap(text, classification)

# ---------------------------------------------------------------------------
# 8. format_json_human
# ---------------------------------------------------------------------------

def format_json_human(
    data: Any,
    title: Optional[str] = None,
    classification: Optional[str] = None,
    _indent: int = 0,
) -> str:
    """Recursively format a dict/list as readable colored output (not raw JSON)."""
    pad = "  " * _indent
    lines: List[str] = []

    if _indent == 0 and title:
        lines.append(C.wrap(f"  {title}", "bold", "underline"))
        lines.append("")

    if isinstance(data, dict):
        if not data:
            lines.append(f"{pad}  {C.wrap('(empty)', 'dim')}")
        for key, val in data.items():
            k_colored = C.wrap(str(key), "cyan")
            if isinstance(val, (dict, list)):
                lines.append(f"{pad}  {k_colored}:")
                lines.append(format_json_human(val, _indent=_indent + 1))
            else:
                v_str = _auto_color_value(str(val))
                lines.append(f"{pad}  {k_colored}: {v_str}")
    elif isinstance(data, (list, tuple)):
        if not data:
            lines.append(f"{pad}  {C.wrap('(empty list)', 'dim')}")
        for i, item in enumerate(data):
            if isinstance(item, dict):
                lines.append(f"{pad}  {C.wrap(f'[{i}]', 'dim')}")
                lines.append(format_json_human(item, _indent=_indent + 1))
            else:
                lines.append(f"{pad}  {C.wrap('\u2022', 'dim')} {_auto_color_value(str(item))}")
    else:
        lines.append(f"{pad}  {_auto_color_value(str(data))}")

    result = "\n".join(lines)
    if _indent == 0:
        return _cui_wrap(result, classification)
    return result

# ---------------------------------------------------------------------------
# 9. auto_format
# ---------------------------------------------------------------------------

def auto_format(
    data: Any,
    title: Optional[str] = None,
    classification: Optional[str] = None,
) -> str:
    """Intelligently pick the right formatter based on data shape.

    Heuristics:
    - List of dicts with uniform keys -> table
    - Dict with ``status`` + ``message`` keys -> banner
    - Dict with ``score`` / ``value`` + ``threshold`` -> score
    - Dict with ``steps`` (list of dicts with name/status) -> pipeline
    - Plain dict -> key-value display
    - Plain list -> list
    - Fallback -> format_json_human
    """
    if isinstance(data, dict):
        keys = set(data.keys())

        # Pipeline
        if "steps" in keys and isinstance(data.get("steps"), list):
            step_list = data["steps"]
            if step_list and isinstance(step_list[0], dict) and "name" in step_list[0]:
                header = ""
                if title:
                    header = format_section(title, classification=classification) + "\n\n"
                return header + format_pipeline(step_list)

        # Score
        if "value" in keys and "threshold" in keys:
            return format_score(
                float(data["value"]),
                float(data["threshold"]),
                data.get("label", title or "Score"),
                classification=classification,
            )

        # Banner
        if "status" in keys and "message" in keys and len(keys) <= 4:
            return format_banner(
                str(data["status"]),
                str(data["message"]),
                classification=classification,
            )

        # Table from nested list
        if "headers" in keys and "rows" in keys:
            return format_table(
                data["headers"],
                data["rows"],
                title=title or data.get("title"),
                classification=classification,
            )

        # Default dict -> kv
        return format_kv(data, title=title, classification=classification)

    if isinstance(data, (list, tuple)):
        if not data:
            return format_list(["(no items)"], classification=classification)

        # List of uniform dicts -> table
        if all(isinstance(item, dict) for item in data):
            all_keys: List[str] = []
            seen: set = set()
            for item in data:
                for k in item:
                    if k not in seen:
                        all_keys.append(k)
                        seen.add(k)
            rows = [[str(item.get(k, "")) for k in all_keys] for item in data]
            return format_table(all_keys, rows, title=title, classification=classification)

        # Plain list
        return format_list(
            [str(item) for item in data],
            classification=classification,
        )

    # Scalar fallback
    return format_json_human(data, title=title, classification=classification)

# ---------------------------------------------------------------------------
# 10. human_output decorator
# ---------------------------------------------------------------------------

def human_output(func: Callable) -> Callable:
    """Decorator that intercepts a tool function's dict return value and
    formats it for human-readable terminal output when ``--human`` is active.

    The decorated function should return a ``dict``.  If ``--human`` was
    passed on the CLI (detected via a ``human`` attribute on the first
    positional arg or via ``sys.argv``), the dict is rendered through
    :func:`auto_format`.  Otherwise the dict is printed as JSON.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        if not isinstance(result, dict):
            return result

        use_human = "--human" in sys.argv

        # Also check for argparse Namespace in first arg
        if args and hasattr(args[0], "human"):
            use_human = getattr(args[0], "human", False)

        if use_human:
            title = result.get("_title") or func.__name__.replace("_", " ").title()
            classification_val = result.get("_classification")
            output = auto_format(result, title=title, classification=classification_val)
            print(output)
        else:
            print(json.dumps(result, indent=2, default=str))

        return result

    return wrapper

# ---------------------------------------------------------------------------
# 11. CLI integration helpers
# ---------------------------------------------------------------------------

def add_human_flag(parser: argparse.ArgumentParser) -> None:
    """Add ``--human`` flag to an argparse parser.

    This flag is mutually informational with ``--json`` (the existing ICDEV
    convention).  When ``--human`` is supplied, tool output is rendered as
    colorized terminal text instead of raw JSON.
    """
    parser.add_argument(
        "--human",
        action="store_true",
        default=False,
        help="Human-friendly colorized terminal output (instead of JSON)",
    )


def should_use_human(args: argparse.Namespace) -> bool:
    """Return True if the ``--human`` flag is set on *args*."""
    return getattr(args, "human", False)

# ---------------------------------------------------------------------------
# 12. Demo / self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print(C.wrap("=" * 70, "bold"))
    print(C.wrap("  ICDEV Output Formatter Demo", "bold", "cyan"))
    print(C.wrap("=" * 70, "bold"))
    print()

    # -- Banner --
    print(format_banner("healthy", "All 13 agents operational"))
    print()
    print(format_banner("degraded", "Builder agent high latency"))
    print()
    print(format_banner("critical", "Compliance agent unreachable"))
    print()

    # -- Section --
    print(format_section("Agent Status Table"))
    print()

    # -- Table --
    print(format_table(
        headers=["Agent", "Port", "Status", "Uptime"],
        rows=[
            ["Orchestrator", "8443", "healthy",  "99.9%"],
            ["Architect",    "8444", "healthy",  "99.8%"],
            ["Builder",      "8445", "degraded", "97.2%"],
            ["Compliance",   "8446", "critical", "0.0%"],
            ["Security",     "8447", "healthy",  "99.7%"],
            ["Infrastructure","8448","healthy",  "99.9%"],
            ["MBSE",         "8451", "healthy",  "99.5%"],
            ["Monitor",      "8450", "healthy",  "99.6%"],
        ],
        title="ICDEV Agent Fleet",
    ))
    print()

    # -- Score --
    print(format_section("Compliance Scores"))
    print()
    print(format_score(0.92, 0.80, "FedRAMP Readiness"))
    print(format_score(0.74, 0.80, "CMMC Level 2"))
    print(format_score(0.45, 0.70, "cATO Evidence Freshness"))
    print()

    # -- Key-Value --
    print(format_kv(
        {
            "Project":           "mission-planner-v2",
            "Impact Level":      "IL5",
            "Classification":    "CUI // SP-CTI",
            "ATO Status":        "active",
            "STIG Findings":     "0 CAT1, 2 CAT2",
            "SBOM Components":   "142",
            "Last Deploy":       "2026-02-17 14:30 UTC",
        },
        title="Project Summary",
    ))
    print()

    # -- Pipeline --
    print(format_section("ATLAS Workflow"))
    print()
    print(format_pipeline([
        {"name": "Model",     "status": "completed"},
        {"name": "Architect", "status": "completed"},
        {"name": "Trace",     "status": "completed"},
        {"name": "Link",      "status": "active"},
        {"name": "Assemble",  "status": "pending"},
        {"name": "Stress",    "status": "pending"},
    ]))
    print()

    # -- Pipeline with blocked --
    print(format_pipeline([
        {"name": "Plan",   "status": "completed"},
        {"name": "Build",  "status": "completed"},
        {"name": "Test",   "status": "completed"},
        {"name": "Review", "status": "blocked"},
        {"name": "Deploy", "status": "skipped"},
    ]))
    print()

    # -- List --
    print(format_section("Recent Findings"))
    print()
    print(format_list([
        "critical: AC-2 control implementation missing",
        "high: Outdated dependency openssl 1.1.1",
        "medium: CUI banner not on 3 generated artifacts",
        "low: Non-standard port in K8s service manifest",
        "info: New STIG checklist V2R4 available",
    ]))
    print()

    # -- Numbered List --
    print(format_list([
        "Run FIPS 199 categorization",
        "Validate FIPS 200 minimum security",
        "Generate SSP with dynamic baseline",
        "Submit to eMASS for review",
    ], numbered=True))
    print()

    # -- JSON Human --
    print(format_section("Nested Data (format_json_human)"))
    print()
    print(format_json_human(
        {
            "project_id": "proj-42",
            "status": "active",
            "compliance": {
                "fedramp": "satisfied",
                "cmmc": "partial",
                "stig": {
                    "cat1": 0,
                    "cat2": 3,
                    "cat3": 12,
                },
            },
            "agents": ["Orchestrator", "Builder", "Compliance"],
            "gates": {
                "merge": "passed",
                "deploy": "blocked",
            },
        },
        title="Project Detail",
    ))
    print()

    # -- Auto-format: list of dicts -> table --
    print(format_section("auto_format: List of Dicts"))
    print()
    print(auto_format(
        [
            {"CVE": "CVE-2025-1234", "Severity": "critical", "Component": "openssl",  "SLA": "24h"},
            {"CVE": "CVE-2025-5678", "Severity": "high",     "Component": "libxml2",  "SLA": "72h"},
            {"CVE": "CVE-2025-9012", "Severity": "medium",   "Component": "requests", "SLA": "30d"},
        ],
        title="CVE Triage Queue",
    ))
    print()

    # -- Auto-format: score dict --
    print(auto_format(
        {"value": 0.85, "threshold": 0.70, "label": "Readiness Score"},
    ))
    print()

    # -- CUI-wrapped output --
    print(format_section("CUI-Marked Output"))
    print()
    print(format_banner("info", "This output carries CUI markings", classification=CUI_BANNER))
    print()

    print(C.wrap("=" * 70, "bold"))
    print(C.wrap("  Demo complete.", "bold", "green"))
    print(C.wrap("=" * 70, "bold"))
    print()
