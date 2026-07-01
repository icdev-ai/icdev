"""Deterministic markdown structure validator for RFI workbench content.

No LLM required. Four synchronous checks:
  1. Code fence parity (unclosed ``` = error)
  2. Table column alignment (cell count mismatch = error)
  3. Truncated table row (<2 cells = warning)
  4. Orphaned pipe marker (starts with | but no closing | = warning)

Returns {"valid": bool, "issues": [{"type", "line", "severity", "message"}]}.
valid=True only when zero error-severity issues.
"""
from __future__ import annotations

import re


def validate_markdown_structure(text: str) -> dict:
    if not text:
        return {"valid": True, "issues": []}

    lines = text.splitlines()
    issues: list[dict] = []

    # ── Check 1: code fence parity ─────────────────────────────────────────────
    fence_lines: list[int] = []
    for i, line in enumerate(lines, 1):
        if re.match(r"^```", line):
            fence_lines.append(i)

    if len(fence_lines) % 2 != 0:
        unclosed = fence_lines[-1]
        issues.append({
            "type": "unclosed_code_fence",
            "line": unclosed,
            "severity": "error",
            "message": f"Unclosed code fence starting at line {unclosed}",
        })

    # ── Check 2 & 3: table column alignment and truncated rows ─────────────────
    in_fence = False
    in_table = False
    header_cell_count = 0
    table_start_line = 0

    for i, line in enumerate(lines, 1):
        if re.match(r"^```", line):
            in_fence = not in_fence
            if in_fence and in_table:
                in_table = False
                header_cell_count = 0
            continue

        if in_fence:
            continue

        is_pipe_row = line.lstrip().startswith("|")

        if not is_pipe_row:
            in_table = False
            header_cell_count = 0
            continue

        cells = [c for c in line.strip().strip("|").split("|")]

        # Detect separator row (|---|---|) — skip it but don't reset table state
        stripped_cells = [c.strip() for c in cells]
        if stripped_cells and all(re.match(r"^[-:]+$", c) for c in stripped_cells if c):
            continue

        cell_count = len(stripped_cells)

        if not in_table:
            # First content row of a new table block
            in_table = True
            header_cell_count = cell_count
            table_start_line = i
            if cell_count < 2:
                issues.append({
                    "type": "truncated_table_row",
                    "line": i,
                    "severity": "warning",
                    "message": f"Table header at line {i} has fewer than 2 cells ({cell_count} found)",
                })
        else:
            # Subsequent content row — check alignment with header
            if cell_count != header_cell_count:
                issues.append({
                    "type": "misaligned_table_columns",
                    "line": i,
                    "severity": "error",
                    "message": (
                        f"Table row at line {i} has {cell_count} cell(s) "
                        f"but header at line {table_start_line} has {header_cell_count}"
                    ),
                })
            elif cell_count < 2:
                issues.append({
                    "type": "truncated_table_row",
                    "line": i,
                    "severity": "warning",
                    "message": f"Table row at line {i} has fewer than 2 cells ({cell_count} found)",
                })

    # ── Check 4: orphaned pipe markers ─────────────────────────────────────────
    in_fence = False
    for i, line in enumerate(lines, 1):
        if re.match(r"^```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if stripped.startswith("|") and not stripped.endswith("|"):
            issues.append({
                "type": "orphaned_pipe_marker",
                "line": i,
                "severity": "warning",
                "message": f"Line {i} starts with '|' but does not end with '|' — may be a broken table row",
            })

    valid = not any(iss["severity"] == "error" for iss in issues)
    return {"valid": valid, "issues": issues}
