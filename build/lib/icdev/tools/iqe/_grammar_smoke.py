"""Grammar smoke test — prints Lark AST for 5 IQE example queries."""
from __future__ import annotations

import sys
from pathlib import Path

from lark import Lark

GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"

EXAMPLES: list[tuple[str, str]] = [
    (
        "NDC vendor inventory",
        'foreach device in network.devices where device.vendor == "cisco" '
        "select device.hostname, device.ios_version",
    ),
    (
        "SDC attack path filter",
        'foreach path in attack_paths where goal == "data_exfiltration" and cost < 5 '
        "select path.ttp_sequence",
    ),
    (
        "BDC unsatisfied controls",
        "foreach ctrl in framework('FedRAMP Moderate').controls "
        "where ctrl.status != 'satisfied' select ctrl.id, affected_projects(ctrl)",
    ),
    (
        "BDC stale assessments",
        "foreach proj in projects where days_since_last_assessment(proj) > 30 "
        "select proj.name",
    ),
    (
        "DDC PII governance",
        "foreach dataset where contains_pii and region != 'US' "
        "select dataset.name, violating_controls",
    ),
]


def main() -> int:
    grammar = GRAMMAR_PATH.read_text(encoding="utf-8")
    parser = Lark(grammar, parser="earley", ambiguity="resolve")

    errors: list[str] = []
    for label, query in EXAMPLES:
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"  Query: {query}")
        print("=" * 60)
        try:
            tree = parser.parse(query)
            print(tree.pretty())
        except Exception as exc:  # noqa: BLE001
            print(f"  PARSE ERROR: {exc}", file=sys.stderr)
            errors.append(label)

    if errors:
        print(f"\nFAIL — {len(errors)} quer(ies) failed: {errors}", file=sys.stderr)
        return 1

    print(f"\nPASS — all {len(EXAMPLES)} queries parsed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
