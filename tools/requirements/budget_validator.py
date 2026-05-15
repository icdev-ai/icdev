# CUI // SP-CTI
"""Budget Range Validator for Requirements Intake.

Parses budget expressions from requirement text and validates them against
configurable min/max thresholds.  Supports millions, thousands, and bare
numbers with dollar signs, commas, and M/K suffixes.

Usage:
    python tools/requirements/budget_validator.py --text \
        "Project budget is 3 to 5 million dollars" --min 3_000_000 --max 5_000_000 --json
    python tools/requirements/budget_validator.py --requirement-id req-abc --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ── Data structures ──────────────────────────────────────────────────────────


@dataclasses.dataclass
class ParsedBudget:
    raw_text: str
    amount_usd: float
    currency_symbol: str
    original_phrase: str


@dataclasses.dataclass
class BudgetValidationResult:
    status: str  # "pass", "fail", "not_found", "error"
    requirement_id: str
    parsed_budgets: list[ParsedBudget]
    min_usd: float
    max_usd: float
    messages: list[str]
    within_range: bool | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "requirement_id": self.requirement_id,
            "min_usd": self.min_usd,
            "max_usd": self.max_usd,
            "within_range": self.within_range,
            "messages": self.messages,
            "parsed_budgets": [
                {
                    "raw_text": pb.raw_text,
                    "amount_usd": pb.amount_usd,
                    "currency_symbol": pb.currency_symbol,
                    "original_phrase": pb.original_phrase,
                }
                for pb in self.parsed_budgets
            ],
        }


# ── Parsing ──────────────────────────────────────────────────────────────────


# Matches patterns like:
#   $3 million, $3.5M, 4,000,000 USD, 5m dollars, 2.5 million USD, etc.
_BUDGET_PATTERN = re.compile(
    r"""
    (?i)                                # case-insensitive
    (?:budget|cost|funding|price)\s+(?:is\s+)?(?:of\s+)?
    (?:\$?\s*([0-9,]+(?:\.[0-9]+)?)\s*(million|m|billion|b|thousand|k)?\s*(?:dollars?|usd)?)
    |
    (?:\$\s*([0-9,]+(?:\.[0-9]+)?)\s*(million|m|billion|b|thousand|k)?\s*(?:dollars?|usd)?)
    |
    (?:([0-9,]+(?:\.[0-9]+)?)\s*(million|m|billion|b|thousand|k)\s+(?:dollars?|usd)?)
    |
    (?:([0-9,]+(?:\.[0-9]+)?)\s*(?:USD|usd))
    """,
    re.VERBOSE,
)

# Simpler fallback for standalone expressions like "3 to 5 million"
_RANGE_PATTERN = re.compile(
    r"(?i)([0-9]+(?:\.[0-9]+)?)\s*(?:to|[-–])\s*([0-9]+(?:\.[0-9]+)?)\s*(million|m|billion|b|thousand|k)?\s*(?:dollars?|usd)?"
)


def _normalize_amount(amount_str: str, multiplier_str: str | None) -> float:
    """Convert parsed amount string + multiplier to USD float."""
    amount = float(amount_str.replace(",", ""))
    if not multiplier_str:
        return amount
    mult = multiplier_str.lower().strip()
    if mult in ("million", "m"):
        return amount * 1_000_000
    if mult in ("billion", "b"):
        return amount * 1_000_000_000
    if mult in ("thousand", "k"):
        return amount * 1_000
    return amount


def parse_budgets(text: str) -> list[ParsedBudget]:
    """Extract all budget expressions from *text*.

    Returns a list of :class:`ParsedBudget` objects.  Duplicates are
    deduplicated by original_phrase.
    """
    results: list[ParsedBudget] = []
    seen: set[str] = set()

    # First, extract range expressions.
    range_spans: list[tuple[int, int]] = []
    for match in _RANGE_PATTERN.finditer(text):
        low_str, high_str, mult = match.groups()
        phrase = match.group(0)
        if phrase in seen:
            continue
        seen.add(phrase)
        range_spans.append((match.start(), match.end()))

        low_usd = _normalize_amount(low_str, mult)
        high_usd = _normalize_amount(high_str, mult)

        results.append(
            ParsedBudget(
                raw_text=phrase,
                amount_usd=low_usd,
                currency_symbol="USD",
                original_phrase=phrase,
            )
        )
        results.append(
            ParsedBudget(
                raw_text=phrase,
                amount_usd=high_usd,
                currency_symbol="USD",
                original_phrase=phrase,
            )
        )

    # Blank out range spans so the primary pattern can't produce partial
    # false-positive matches inside them.
    masked_text = list(text)
    for start, end in range_spans:
        for i in range(start, end):
            masked_text[i] = " "
    masked_text = "".join(masked_text)

    # Primary pattern: keyword-led expressions on masked text
    for match in _BUDGET_PATTERN.finditer(masked_text):
        groups = [g for g in match.groups() if g is not None]
        if not groups:
            continue
        phrase = match.group(0).strip()
        if not phrase or phrase in seen:
            continue
        seen.add(phrase)

        # Heuristic: first numeric-looking group is the amount,
        # the next (if it looks like a multiplier word) is the multiplier.
        amount_str = None
        mult_str = None
        for g in groups:
            g_stripped = g.strip()
            if amount_str is None and re.match(r"^[0-9,.]+$", g_stripped):
                amount_str = g_stripped
            elif mult_str is None and re.match(r"^(million|m|billion|b|thousand|k)$", g_stripped, re.I):
                mult_str = g_stripped

        if amount_str is None:
            continue

        amount_usd = _normalize_amount(amount_str, mult_str)
        results.append(
            ParsedBudget(
                raw_text=phrase,
                amount_usd=amount_usd,
                currency_symbol="USD",
                original_phrase=phrase,
            )
        )

    return results


# ── Validation ───────────────────────────────────────────────────────────────


def validate_budget(
    text: str,
    requirement_id: str = "",
    min_usd: float = 0.0,
    max_usd: float = float("inf"),
) -> BudgetValidationResult:
    """Parse budgets from *text* and validate against *[min_usd, max_usd]*.

    If no budget expression is found, ``status`` is ``"not_found"``.
    If all found budgets fall within the range, ``status`` is ``"pass"``.
    If any budget falls outside the range, ``status`` is ``"fail"``.
    """
    parsed = parse_budgets(text)
    messages: list[str] = []

    if not parsed:
        return BudgetValidationResult(
            status="not_found",
            requirement_id=requirement_id,
            parsed_budgets=[],
            min_usd=min_usd,
            max_usd=max_usd,
            messages=["No budget expression detected in requirement text."],
            within_range=None,
        )

    within_range = True
    for pb in parsed:
        if pb.amount_usd < min_usd:
            within_range = False
            messages.append(
                f"Budget ${pb.amount_usd:,.2f} is below minimum ${min_usd:,.2f}"
            )
        elif pb.amount_usd > max_usd:
            within_range = False
            messages.append(
                f"Budget ${pb.amount_usd:,.2f} exceeds maximum ${max_usd:,.2f}"
            )
        else:
            messages.append(
                f"Budget ${pb.amount_usd:,.2f} is within acceptable range."
            )

    status = "pass" if within_range else "fail"
    return BudgetValidationResult(
        status=status,
        requirement_id=requirement_id,
        parsed_budgets=parsed,
        min_usd=min_usd,
        max_usd=max_usd,
        messages=messages,
        within_range=within_range,
    )


# ── CLI ────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate budget ranges in requirement text.")
    parser.add_argument("--text", required=True, help="Requirement text to analyze")
    parser.add_argument("--requirement-id", default="", help="Optional requirement ID")
    parser.add_argument("--min", dest="min_usd", type=float, default=0.0, help="Minimum allowed budget in USD")
    parser.add_argument("--max", dest="max_usd", type=float, default=float("inf"), help="Maximum allowed budget in USD")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args(argv)

    result = validate_budget(
        text=args.text,
        requirement_id=args.requirement_id,
        min_usd=args.min_usd,
        max_usd=args.max_usd,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Status: {result.status}")
        print(f"Requirement: {result.requirement_id or '(none)'}")
        print(f"Range: ${result.min_usd:,.2f} – ${result.max_usd:,.2f}")
        for msg in result.messages:
            print(f"  • {msg}")
        for pb in result.parsed_budgets:
            print(f"  → Parsed: ${pb.amount_usd:,.2f} from \"{pb.original_phrase}\"")

    return 0 if result.status in ("pass", "not_found") else 1


if __name__ == "__main__":
    sys.exit(main())
