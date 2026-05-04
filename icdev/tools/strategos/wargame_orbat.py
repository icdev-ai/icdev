#!/usr/bin/env python3
# CUI // SP-CTI
"""ORBAT strength loader — aggregates unit strengths into a wargame row."""
from __future__ import annotations

from typing import Any

from tools.db.storage import get_connection, is_pg


def load_orbat_strengths(wargame_id: str) -> dict[str, Any]:
    """Aggregate ORBAT unit strengths for a wargame and persist the totals.

    Steps:
      1. Load the sg_wargames row to obtain its conflict_id.
      2. Query sg_orbat_units WHERE conflict_id matches.
      3. Sum strength_value by side ('blue' / 'red').
      4. UPDATE sg_wargames blue_strength and red_strength.
      5. Return {blue_strength, red_strength, unit_count}.

    Args:
        wargame_id: Primary key of the sg_wargames row.

    Returns:
        Dict with keys ``blue_strength``, ``red_strength``, ``unit_count``.
        All values are zero when no ORBAT rows exist for the conflict.

    Raises:
        ValueError: If the wargame row does not exist.
    """
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT conflict_id FROM sg_wargames WHERE id = {ph}",  # nosec B608
            (wargame_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Wargame {wargame_id!r} not found")

        conflict_id = row[0]

        blue_strength = 0
        red_strength = 0
        unit_count = 0

        if conflict_id:
            units = conn.execute(
                f"SELECT side, strength_value FROM sg_orbat_units "  # nosec B608
                f"WHERE conflict_id = {ph}",
                (conflict_id,),
            ).fetchall()
            unit_count = len(units)
            for side, val in units:
                v = int(val or 0)
                if side == "blue":
                    blue_strength += v
                elif side == "red":
                    red_strength += v

        conn.execute(
            f"UPDATE sg_wargames "  # nosec B608
            f"SET blue_strength = {ph}, red_strength = {ph} "
            f"WHERE id = {ph}",
            (blue_strength, red_strength, wargame_id),
        )
        conn.commit()

        return {
            "blue_strength": blue_strength,
            "red_strength": red_strength,
            "unit_count": unit_count,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Load ORBAT strengths into a wargame row")
    parser.add_argument("wargame_id", help="sg_wargames UUID")
    parser.add_argument("--json", dest="as_json", action="store_true")
    _args = parser.parse_args()

    try:
        _result = load_orbat_strengths(_args.wargame_id)
    except ValueError as _exc:
        print(f"Error: {_exc}", file=sys.stderr)
        sys.exit(1)

    if _args.as_json:
        print(json.dumps(_result, indent=2))
    else:
        import pprint
        pprint.pprint(_result)
