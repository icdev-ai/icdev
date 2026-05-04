# CUI // SP-CTI
"""WriteGuard WG 14 — Structure enforcer.

Detects and validates structured documents (RCA, ADR, pitch, policy, runbook,
pilot proposal) against required-section templates. Deterministic, no LLM.

Template definitions live in ``args/writeguard_structures.yaml``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Built-in fallback templates (used if YAML missing)
# ---------------------------------------------------------------------------

FALLBACK_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "rca": {
        "required": [
            "Executive Summary", "Timeline", "Impact", "Root Cause",
            "5 Whys", "Corrective Actions", "Preventive Actions",
        ],
    },
    "adr": {
        "required": ["Context", "Decision", "Consequences", "Alternatives Considered"],
    },
    "pitch": {
        "required": ["Problem", "Insight", "Solution", "Ask"],
    },
    "policy": {
        "required": [
            "Purpose", "Scope", "Roles and Responsibilities",
            "Requirements", "Exceptions", "Enforcement",
        ],
    },
    "runbook": {
        "required": ["Prerequisites", "Procedure", "Validation", "Rollback", "Escalation"],
    },
    "pilot_proposal": {
        "required": ["Hypothesis", "Metrics", "Budget", "Timeline", "Exit Criteria"],
    },
}


_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "writeguard_structures.yaml"


def _load_templates() -> Dict[str, Dict[str, Any]]:
    if yaml is not None and _CONFIG_PATH.exists():
        try:
            data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            tpls = data.get("templates") or {}
            if tpls:
                return tpls
        except Exception:
            pass
    return FALLBACK_TEMPLATES


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------


_MD_HEADING = re.compile(r"^\s*#+\s+(.+?)\s*$")
_ALLCAPS_COLON = re.compile(r"^\s*([A-Z][A-Z0-9 /&-]{2,})\s*:\s*$")
_BOLD_SECTION = re.compile(r"^\s*\*\*([^*]+)\*\*\s*:?\s*$")
_SETEXT_UNDERLINE = re.compile(r"^\s*[=-]{3,}\s*$")


def _extract_headings(text: str) -> List[Tuple[int, str]]:
    """Return list of (line_index, heading_text) tuples."""
    lines = text.splitlines()
    headings: List[Tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _MD_HEADING.match(line)
        if m:
            headings.append((i, m.group(1).strip().rstrip(":").strip()))
            continue
        m = _BOLD_SECTION.match(line)
        if m:
            headings.append((i, m.group(1).strip().rstrip(":").strip()))
            continue
        m = _ALLCAPS_COLON.match(line)
        if m:
            headings.append((i, m.group(1).strip().title()))
            continue
        # setext style: heading line followed by === or ---
        if i + 1 < len(lines) and _SETEXT_UNDERLINE.match(lines[i + 1]):
            candidate = line.strip()
            if candidate and not candidate.startswith("#"):
                headings.append((i, candidate.rstrip(":").strip()))
    return headings


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _match_heading(required: str, heading: str) -> bool:
    return _norm(required) == _norm(heading) or _norm(required) in _norm(heading)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_structure_type(text: str) -> str:
    """Guess template name from present headings.

    Returns one of: rca, adr, pitch, policy, runbook, pilot_proposal, unknown
    """
    templates = _load_templates()
    headings = [h for _, h in _extract_headings(text)]
    if not headings:
        return "unknown"

    best_name = "unknown"
    best_ratio = 0.0
    for name, cfg in templates.items():
        required = cfg.get("required", [])
        if not required:
            continue
        matches = 0
        for req in required:
            if any(_match_heading(req, h) for h in headings):
                matches += 1
        ratio = matches / len(required)
        if ratio > best_ratio:
            best_ratio = ratio
            best_name = name

    return best_name if best_ratio >= 0.5 else "unknown"


def enforce_structure(text: str, template: Optional[str] = None) -> Dict[str, Any]:
    """Check required sections are present and in order.

    Returns: {template, missing_sections, extra_sections, section_order_issues,
              per_section_word_counts, score}
    """
    templates = _load_templates()

    if template is None:
        template = detect_structure_type(text)

    if template == "unknown" or template not in templates:
        return {
            "template": template,
            "missing_sections": [],
            "extra_sections": [],
            "section_order_issues": [],
            "per_section_word_counts": {},
            "score": 0,
            "error": f"unknown or unsupported template: {template}",
        }

    required = list(templates[template].get("required", []))
    headings = _extract_headings(text)
    heading_texts = [h for _, h in headings]

    # Missing sections
    missing: List[str] = []
    matched_required_index: Dict[str, int] = {}
    for req in required:
        found_idx = None
        for idx, (line_no, h) in enumerate(headings):
            if _match_heading(req, h):
                found_idx = idx
                break
        if found_idx is None:
            missing.append(req)
        else:
            matched_required_index[req] = found_idx

    # Extra sections (present but not in required list)
    extra: List[str] = []
    for h in heading_texts:
        if not any(_match_heading(req, h) for req in required):
            extra.append(h)

    # Order issues
    order_issues: List[str] = []
    last_idx = -1
    last_req = None
    for req in required:
        if req in matched_required_index:
            idx = matched_required_index[req]
            if idx < last_idx:
                order_issues.append(f"'{req}' appears before '{last_req}'")
            last_idx = idx
            last_req = req

    # Per-section word counts
    per_section: Dict[str, int] = {}
    lines = text.splitlines()
    for i, (line_no, h) in enumerate(headings):
        start = line_no + 1
        end = headings[i + 1][0] if i + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start:end])
        per_section[h] = len(body.split())

    # Score: required coverage minus penalties
    total_req = len(required) if required else 1
    present = total_req - len(missing)
    base = (present / total_req) * 100.0
    penalty = min(20.0, len(order_issues) * 5.0)
    score = max(0, int(round(base - penalty)))

    return {
        "template": template,
        "missing_sections": missing,
        "extra_sections": extra,
        "section_order_issues": order_issues,
        "per_section_word_counts": per_section,
        "score": score,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="WriteGuard WG 14 — Structure enforcer")
    parser.add_argument("--text", help="Inline text to check")
    parser.add_argument("--file", help="Path to text file")
    parser.add_argument("--template", help="Template name (rca|adr|pitch|policy|runbook|pilot_proposal)")
    parser.add_argument("--detect", action="store_true", help="Detect template type only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    text = args.text or ""
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        text = sys.stdin.read()

    if args.detect:
        detected = detect_structure_type(text)
        if args.json:
            print(json.dumps({"template": detected}, indent=2))
        else:
            print(f"Detected template: {detected}")
        return

    result = enforce_structure(text, args.template)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Template: {result['template']}")
        print(f"Score: {result['score']}/100")
        if result.get("error"):
            print(f"Error: {result['error']}")
        if result["missing_sections"]:
            print("Missing sections:")
            for s in result["missing_sections"]:
                print(f"  - {s}")
        if result["section_order_issues"]:
            print("Order issues:")
            for s in result["section_order_issues"]:
                print(f"  - {s}")
        if result["extra_sections"]:
            print(f"Extra sections: {', '.join(result['extra_sections'])}")


if __name__ == "__main__":
    main()
