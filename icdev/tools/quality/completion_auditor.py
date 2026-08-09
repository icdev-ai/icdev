#!/usr/bin/env python3
"""Completion Auditor — per-canvas 8-component completeness scorecard.

The `coherence_checker.check_new_page_completeness` gate only enumerates
canvases via ``tools/dashboard/templates/<canvas>/page.html``. Canvases whose
templates are named differently (slides/mfa/zta) or that have no page.html at
all are never scored, which is how incomplete canvases (e.g. ACE) shipped while
the kanban board reported them "done".

This auditor enumerates EVERY canvas directory under the dashboard templates
tree and scores each against the 8-component completeness gate from CLAUDE.md,
producing a deterministic scorecard so the gaps are visible at a glance.

Usage:
    python tools/quality/completion_auditor.py --json
    python tools/quality/completion_auditor.py --md
    python tools/quality/completion_auditor.py            # human table to stdout

``--md`` writes ``docs/quality/completion-scorecard.md`` (table sorted from
least to most complete) and prints the path.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The 8 components every shipped canvas page must have (CLAUDE.md completeness gate).
COMPONENT_KEYS = [
    "template",          # 1. tools/dashboard/templates/<canvas>/*.html
    "icdev_mirror",      # 2. icdev/tools/dashboard/templates/<canvas>/*.html parity
    "blueprint_route",   # 3. tools/<canvas>/blueprint.py with @route
    "backing_module",    # 4. tools/<canvas>/<module>.py (not just __init__/blueprint)
    "nav_link",          # 5. href="/<canvas>" in base.html
    "iqe_adapter",       # 6. tools/iqe/adapters/<canvas>.py
    "iqe_seed_queries",  # 7. context/iqe/queries/<canvas>/ with >=1 yaml
    "iqe_widget",        # 8. iqe_query_widget include in a template
]
TOTAL_COMPONENTS = len(COMPONENT_KEYS)


@dataclass
class CanvasScore:
    canvas: str
    present: Dict[str, bool] = field(default_factory=dict)
    score: int = 0
    total: int = TOTAL_COMPONENTS

    @property
    def pct(self) -> float:
        return round(100.0 * self.score / self.total, 1) if self.total else 0.0

    @property
    def missing(self) -> List[str]:
        return [k for k, v in self.present.items() if not v]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _discover_canvases(templates_dir: Path) -> List[str]:
    """Every templates sub-directory that holds at least one .html file."""
    canvases: List[str] = []
    if not templates_dir.exists():
        return canvases
    for sub in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        if any(sub.glob("*.html")):
            canvases.append(sub.name)
    return canvases


def audit_canvas(canvas: str, base_html_text: str) -> CanvasScore:
    templates_dir = PROJECT_ROOT / "tools" / "dashboard" / "templates"
    icdev_templates_dir = PROJECT_ROOT / "icdev" / "tools" / "dashboard" / "templates"
    src_dir = templates_dir / canvas
    src_html = {p.name for p in src_dir.glob("*.html")}

    present: Dict[str, bool] = {}

    # 1. Template(s) exist
    present["template"] = bool(src_html)

    # 2. icdev/ mirror parity — every source template has a matching mirror
    mirror_dir = icdev_templates_dir / canvas
    mirror_html = {p.name for p in mirror_dir.glob("*.html")} if mirror_dir.exists() else set()
    present["icdev_mirror"] = bool(src_html) and src_html.issubset(mirror_html)

    # 3. Blueprint with a @route decorator
    bp_file = PROJECT_ROOT / "tools" / canvas / "blueprint.py"
    present["blueprint_route"] = (
        bp_file.exists() and bool(re.search(r"@\w+\.route\s*\(", _read_text(bp_file)))
    )

    # 4. Backing module (any .py other than __init__/blueprint)
    canvas_dir = PROJECT_ROOT / "tools" / canvas
    present["backing_module"] = canvas_dir.exists() and any(
        f.name not in ("__init__.py", "blueprint.py") for f in canvas_dir.glob("*.py")
    )

    # 5. Nav link in base.html
    present["nav_link"] = (
        f'href="/{canvas}' in base_html_text or f"href='/{canvas}" in base_html_text
    )

    # 6. IQE adapter
    present["iqe_adapter"] = (PROJECT_ROOT / "tools" / "iqe" / "adapters" / f"{canvas}.py").exists()

    # 7. IQE seed queries
    q_dir = PROJECT_ROOT / "context" / "iqe" / "queries" / canvas
    present["iqe_seed_queries"] = q_dir.exists() and bool(
        list(q_dir.glob("*.yaml")) + list(q_dir.glob("*.yml"))
    )

    # 8. IQE widget include in any template
    iqe_widget = False
    for html in src_dir.glob("*.html"):
        txt = _read_text(html)
        if "iqe_query_widget" in txt or "iqe-widget" in txt:
            iqe_widget = True
            break
    present["iqe_widget"] = iqe_widget

    score = sum(1 for v in present.values() if v)
    return CanvasScore(canvas=canvas, present=present, score=score)


def run_audit() -> List[CanvasScore]:
    templates_dir = PROJECT_ROOT / "tools" / "dashboard" / "templates"
    base_html_text = _read_text(templates_dir / "base.html")
    results = [audit_canvas(c, base_html_text) for c in _discover_canvases(templates_dir)]
    # Sort least -> most complete; tie-break alphabetically for determinism.
    results.sort(key=lambda r: (r.score, r.canvas))
    return results


def to_json(results: List[CanvasScore]) -> str:
    complete = sum(1 for r in results if r.score == r.total)
    payload = {
        "total_canvases": len(results),
        "fully_complete": complete,
        "incomplete": len(results) - complete,
        "components": COMPONENT_KEYS,
        "canvases": [
            {
                "canvas": r.canvas,
                "score": r.score,
                "total": r.total,
                "pct": r.pct,
                "present": r.present,
                "missing": r.missing,
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2)


def to_markdown(results: List[CanvasScore]) -> str:
    complete = sum(1 for r in results if r.score == r.total)
    lines: List[str] = []
    lines.append("# Completion Scorecard")
    lines.append("")
    lines.append(
        "Per-canvas 8-component completeness audit "
        "(generated by `tools/quality/completion_auditor.py`)."
    )
    lines.append("")
    lines.append(
        f"**{complete}/{len(results)}** canvases fully complete · "
        f"**{len(results) - complete}** incomplete. "
        "Sorted least → most complete."
    )
    lines.append("")
    header = "| Canvas | Score | % |" + "".join(f" {k} |" for k in COMPONENT_KEYS)
    sep = "|--------|-------|---|" + "".join("---|" for _ in COMPONENT_KEYS)
    lines.append(header)
    lines.append(sep)
    for r in results:
        cells = "".join(" ✅ |" if r.present.get(k) else " ❌ |" for k in COMPONENT_KEYS)
        lines.append(f"| {r.canvas} | {r.score}/{r.total} | {r.pct} |{cells}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-canvas 8-component completion auditor")
    ap.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    ap.add_argument("--md", action="store_true",
                    help="Write docs/quality/completion-scorecard.md and print its path")
    args = ap.parse_args()

    results = run_audit()

    if args.json:
        print(to_json(results))
        return 0

    if args.md:
        out_path = PROJECT_ROOT / "docs" / "quality" / "completion-scorecard.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(to_markdown(results), encoding="utf-8", newline="")
        print(str(out_path))
        return 0

    # Default: human-readable table to stdout.
    complete = sum(1 for r in results if r.score == r.total)
    print(f"Completion Scorecard — {complete}/{len(results)} complete "
          f"({len(results) - complete} incomplete)")
    for r in results:
        flag = "OK " if r.score == r.total else "   "
        miss = "" if r.score == r.total else f"  missing: {', '.join(r.missing)}"
        print(f"  [{flag}] {r.canvas:<28} {r.score}/{r.total} ({r.pct}%)" + miss)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
