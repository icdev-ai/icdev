#!/usr/bin/env python3
# CUI // SP-CTI
"""Wargame Turn Engine — advance a wargame by one turn.

Applies Lanchester attrition, Colonel Blotto resource allocation,
and OODA tempo delta to produce a turn record persisted to sg_wargame_turns.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection, is_pg
from tools.strategos.ooda import (
    blotto_expected_payoff,
    lanchester_linear,
    lanchester_square,
)

_SMALL_FORCE_THRESHOLD = 50


def _fetch_ooda_tempo_delta(conn: Any, wargame_id: str, ph: str) -> float:
    """Return blue_tempo_score minus red_tempo_score from recent sg_ooda_events.

    Positive = Blue has faster decision cycle.  Falls back to 0.0 on any error.
    """
    try:
        rows = conn.execute(
            f"SELECT side, latency_s FROM sg_ooda_events "  # nosec B608
            f"WHERE wargame_id = {ph} ORDER BY created_at DESC LIMIT 20",
            (wargame_id,),
        ).fetchall()
    except Exception:
        return 0.0

    if not rows:
        return 0.0

    blue_lats = [float(r[1]) for r in rows if r[0] == "blue" and r[1] is not None]
    red_lats  = [float(r[1]) for r in rows if r[0] == "red"  and r[1] is not None]
    blue_avg = sum(blue_lats) / len(blue_lats) if blue_lats else 600.0
    red_avg  = sum(red_lats)  / len(red_lats)  if red_lats  else 600.0
    max_lat  = max(blue_avg, red_avg, 1.0)
    return round((1.0 - blue_avg / max_lat) - (1.0 - red_avg / max_lat), 4)


def advance_turn(wargame_id: str) -> dict[str, Any]:
    """Advance a wargame by one turn.

    Steps:
      1. Load sg_wargames row (blue_strength, red_strength, attrition coefficients)
      2. Run Lanchester one step — square law if both sides ≥50 units, linear otherwise
      3. Run Colonel Blotto expected payoff from force-allocation JSON columns
      4. Fetch OODA tempo delta from sg_ooda_events for this wargame
      5. INSERT a new row into sg_wargame_turns
      6. UPDATE sg_wargames blue_strength and red_strength
      7. Return the turn row dict with embedded lanchester + blotto summaries

    Args:
        wargame_id: Primary key of the sg_wargames row to advance.

    Returns:
        Dict matching sg_wargame_turns schema plus ``"lanchester"`` and
        ``"blotto"`` summary sub-dicts.

    Raises:
        ValueError: If the wargame row does not exist.
    """
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        # 1. Load wargame row
        row = conn.execute(
            f"SELECT id, blue_strength, red_strength, "  # nosec B608
            f"attrition_coefficients_json, blue_force, red_force "
            f"FROM sg_wargames WHERE id = {ph}",
            (wargame_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Wargame {wargame_id!r} not found")

        _, blue_strength, red_strength, attrition_json, blue_force_raw, red_force_raw = row
        b0 = float(blue_strength or 0)
        r0 = float(red_strength or 0)

        coeff: dict = {}
        if attrition_json:
            try:
                coeff = json.loads(attrition_json)
            except Exception:
                pass
        beta = float(coeff.get("beta", 0.01))
        rho  = float(coeff.get("rho",  0.01))

        # 2. Lanchester attrition — one step
        if b0 < _SMALL_FORCE_THRESHOLD or r0 < _SMALL_FORCE_THRESHOLD:
            lanch = lanchester_linear(b0, r0, beta=beta, rho=rho, dt=1.0, max_steps=1)
        else:
            lanch = lanchester_square(b0, r0, beta=beta, rho=rho, dt=1.0, max_steps=1)

        new_blue = lanch["final_b"]
        new_red  = lanch["final_r"]
        blue_losses = max(0, int(round(b0 - new_blue)))
        red_losses  = max(0, int(round(r0 - new_red)))

        # 3. Colonel Blotto payoff — parse force-allocation arrays from JSON columns
        try:
            blue_forces: list[float] = json.loads(blue_force_raw) if blue_force_raw else [b0]
        except Exception:
            blue_forces = [b0]
        try:
            red_forces: list[float] = json.loads(red_force_raw) if red_force_raw else [r0]
        except Exception:
            red_forces = [r0]
        blotto = blotto_expected_payoff(blue_forces, red_forces)

        # 4. OODA tempo delta (blue advantage > 0, red advantage < 0)
        tempo_delta = _fetch_ooda_tempo_delta(conn, wargame_id, ph)

        # Derive next turn number from existing turn count
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM sg_wargame_turns WHERE wargame_id = {ph}",  # nosec B608
            (wargame_id,),
        ).fetchone()
        next_turn = (count_row[0] if count_row else 0) + 1

        turn_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        notes = json.dumps({
            "lanchester_model":  lanch["model"],
            "lanchester_winner": lanch["winner"],
            "blotto_winner":     blotto.get("overall_winner"),
            "blotto_blue_score": blotto.get("blue_score"),
            "blotto_red_score":  blotto.get("red_score"),
        })

        # 5. INSERT turn record
        conn.execute(
            f"INSERT INTO sg_wargame_turns "  # nosec B608
            f"(id, wargame_id, turn_number, blue_losses, red_losses, "
            f"blue_remaining, red_remaining, tempo_delta, notes, created_at) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (
                turn_id, wargame_id, next_turn,
                blue_losses, red_losses,
                int(round(new_blue)), int(round(new_red)),
                tempo_delta, notes, now,
            ),
        )

        # 6. Update wargame strengths
        conn.execute(
            f"UPDATE sg_wargames SET blue_strength={ph}, red_strength={ph}, "  # nosec B608
            f"updated_at={ph} WHERE id={ph}",
            (int(round(new_blue)), int(round(new_red)), now, wargame_id),
        )
        conn.commit()

        # 7. Return turn row dict
        return {
            "id":             turn_id,
            "wargame_id":     wargame_id,
            "turn_number":    next_turn,
            "blue_losses":    blue_losses,
            "red_losses":     red_losses,
            "blue_remaining": int(round(new_blue)),
            "red_remaining":  int(round(new_red)),
            "tempo_delta":    tempo_delta,
            "notes":          notes,
            "created_at":     now,
            "lanchester":     lanch,
            "blotto":         blotto,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Wargame Turn Engine")
    parser.add_argument("wargame_id", help="sg_wargames UUID to advance by one turn")
    parser.add_argument("--json", dest="as_json", action="store_true")
    _args = parser.parse_args()

    try:
        _result = advance_turn(_args.wargame_id)
    except ValueError as _exc:
        print(f"Error: {_exc}", file=sys.stderr)
        sys.exit(1)

    if _args.as_json:
        import json as _json
        print(_json.dumps(_result, indent=2))
    else:
        import pprint
        pprint.pprint(_result)
