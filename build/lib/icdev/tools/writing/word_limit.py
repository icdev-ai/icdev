#!/usr/bin/env python3
# CUI // SP-CTI
"""tools/writing/word_limit.py — Per-section word limit checker for WriteGuard.

Deterministic, zero-LLM module.

Public API:
  parse_limits(yaml_str)  → dict[str, int]
      Parse a YAML config string (section name → word limit).
  count_by_section(text)  → dict[str, int]
      Split markdown text by heading and count words per section.
  check_limits(text, limits) → dict
      Compare section counts against limits; return violations + status.

CLI (smoke-test):
  python tools/writing/word_limit.py --json
"""

from __future__ import annotations

import re
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# parse_limits
# ---------------------------------------------------------------------------

def parse_limits(yaml_str: str) -> dict[str, int]:
    """Parse a YAML config string into {section_name: word_limit}.

    Accepts two wrapper styles:
        limits:                     # with 'limits:' key
          Introduction: 150
          total: 1000

        Introduction: 150           # flat (no wrapper)
        total: 1000

    Heading markers are stripped so ``## Introduction: 150`` also works.
    Non-integer values are silently skipped.
    Returns empty dict on blank input or parse errors.
    """
    if not yaml_str or not yaml_str.strip():
        return {}

    # Try pyyaml first (handles multi-line, anchors, etc.)
    try:
        import yaml  # pyyaml

        parsed = yaml.safe_load(yaml_str)
        if isinstance(parsed, dict):
            data = parsed.get("limits", parsed)
            if isinstance(data, dict):
                return _normalise_limits(data)
    except Exception:  # noqa: BLE001
        pass

    # Fallback: manual line-by-line parse (no external deps)
    result: dict[str, int] = {}
    for line in yaml_str.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            raw_key, _, raw_val = line.partition(":")
            key = re.sub(r"^#+\s*", "", raw_key).strip().strip("\"'")
            val = raw_val.strip().strip("\"'")
            if key and val:
                try:
                    result[key] = int(val)
                except ValueError:
                    pass
    return result


def _normalise_limits(raw: dict) -> dict[str, int]:
    """Strip heading markers from keys and cast values to int."""
    result: dict[str, int] = {}
    for k, v in raw.items():
        key = re.sub(r"^#+\s*", "", str(k)).strip()
        try:
            result[key] = int(v)
        except (TypeError, ValueError):
            pass
    return result


# ---------------------------------------------------------------------------
# count_by_section
# ---------------------------------------------------------------------------

def count_by_section(text: str) -> dict[str, int]:
    """Split markdown text into sections by heading and count words in each.

    Returns a dict where keys are section names (heading text without ``#``
    markers) and values are word counts.  Two special keys are added:
      - ``"(preamble)"`` — words before the first heading (omitted if zero)
      - ``"total"``      — grand total across all sections

    If the text has no headings the entire text is placed under
    ``"(entire document)"``.
    """
    if not text:
        return {"total": 0}

    heading_re = re.compile(r"^#{1,6}\s+(.*)", re.MULTILINE)
    positions: list[tuple[int, str]] = [
        (m.start(), m.group(1).strip()) for m in heading_re.finditer(text)
    ]

    if not positions:
        total = _word_count(text)
        return {"(entire document)": total, "total": total}

    sections: dict[str, int] = {}
    seen: dict[str, int] = {}

    # Text before the first heading
    preamble = text[: positions[0][0]]
    preamble_words = _word_count(preamble)

    for i, (pos, heading) in enumerate(positions):
        end_pos = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        body_text = text[pos:end_pos]
        # Remove the heading line itself
        body = body_text.split("\n", 1)[1] if "\n" in body_text else ""
        wc = _word_count(body)

        # Deduplicate heading names
        if heading in seen:
            seen[heading] += 1
            key = f"{heading} ({seen[heading]})"
        else:
            seen[heading] = 1
            key = heading
        sections[key] = wc

    if preamble_words:
        sections["(preamble)"] = preamble_words

    sections["total"] = preamble_words + sum(
        v for k, v in sections.items() if k not in ("total",)
    )
    return sections


def _word_count(text: str) -> int:
    """Return whitespace-delimited word count, ignoring empty strings."""
    return len(text.split()) if text.strip() else 0


# ---------------------------------------------------------------------------
# check_limits
# ---------------------------------------------------------------------------

def check_limits(text: str, limits: dict[str, int]) -> dict:
    """Compare section word counts against limits.

    Args:
        text:   The content to analyse.
        limits: Output of ``parse_limits()`` — {section_name: word_limit}.

    Returns:
        {
          "sections": [
            {
              "name":       str,
              "word_count": int,
              "limit":      int | None,
              "pct":        float,   # 0–100+ (over 100 → over limit)
              "status":     "ok" | "warn" | "over" | "no_limit",
            },
            ...
          ],
          "violations": [            # entries where status == "over"
            {"name": str, "word_count": int, "limit": int, "excess": int},
            ...
          ],
          "total_words": int,
          "total_limit": int | None,
          "total_pct":   float,
          "total_status": "ok" | "warn" | "over" | "no_limit",
          "passed":      bool,
        }
    """
    counts = count_by_section(text)
    total_words: int = counts.get("total", 0)
    total_limit: Optional[int] = limits.get("total")

    section_results: list[dict] = []
    violations: list[dict] = []

    for name, word_count in counts.items():
        if name == "total":
            continue
        limit = _find_limit(name, limits)
        if limit is None:
            status = "no_limit"
            pct = 0.0
        else:
            pct = (word_count / limit * 100) if limit > 0 else 0.0
            if pct > 100:
                status = "over"
                violations.append(
                    {
                        "name": name,
                        "word_count": word_count,
                        "limit": limit,
                        "excess": word_count - limit,
                    }
                )
            elif pct >= 80:
                status = "warn"
            else:
                status = "ok"

        section_results.append(
            {
                "name": name,
                "word_count": word_count,
                "limit": limit,
                "pct": round(pct, 1),
                "status": status,
            }
        )

    # Total row
    total_pct = 0.0
    total_status = "no_limit"
    if total_limit is not None:
        total_pct = (total_words / total_limit * 100) if total_limit > 0 else 0.0
        if total_pct > 100:
            total_status = "over"
            violations.append(
                {
                    "name": "total",
                    "word_count": total_words,
                    "limit": total_limit,
                    "excess": total_words - total_limit,
                }
            )
        elif total_pct >= 80:
            total_status = "warn"
        else:
            total_status = "ok"

    return {
        "sections": section_results,
        "violations": violations,
        "total_words": total_words,
        "total_limit": total_limit,
        "total_pct": round(total_pct, 1),
        "total_status": total_status,
        "passed": len(violations) == 0,
    }


def _find_limit(section_name: str, limits: dict[str, int]) -> Optional[int]:
    """Return limit for *section_name* using case-insensitive fuzzy match.

    Priority: exact → case-insensitive exact → substring containment.
    Returns None if no match found.
    """
    if not limits:
        return None
    # Exact match
    if section_name in limits:
        return limits[section_name]
    lower = section_name.lower()
    # Case-insensitive exact
    for key, val in limits.items():
        if key.lower() == lower:
            return val
    # Substring: limit key is a substring of section name, or vice versa
    for key, val in limits.items():
        kl = key.lower()
        if kl in lower or lower in kl:
            return val
    return None


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

def _cli() -> None:
    """Run a built-in smoke test and print JSON results."""
    import json

    sample_text = """# Introduction
This is the introduction to the document. It covers the background and context.
Words here are counted per section to ensure compliance with the word budget.

## Methodology
Here we describe the approach taken in this study. The methodology section
explains in detail how data was collected, processed, and validated.

## Results
Results are concise. Key finding: the system works.

## Conclusion
In summary, the word limit checker validates per-section budgets accurately.
"""
    sample_limits_yaml = """
Introduction: 30
Methodology: 25
Results: 10
Conclusion: 20
total: 100
"""

    limits = parse_limits(sample_limits_yaml)
    result = check_limits(sample_text, limits)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if "--json" in sys.argv or "--gate" in sys.argv:
        _cli()
    else:
        _cli()
