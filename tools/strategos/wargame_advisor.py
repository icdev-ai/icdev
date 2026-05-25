#!/usr/bin/env python3
# CUI // SP-CTI
"""Wargame Advisor — AI strategic assessment for a wargame session.

Entry point: get_ai_assessment(wargame_id)

  1. Loads sg_wargames + latest sg_wargame_turns row from DB
  2. Calls WarCouncilEngine.generate() (war council deliberation)
  3. Calls ooda.score_coa() on the top COAs from the council brief
  4. Returns {summary, recommended_coa, risk_level, rationale}

LLM guard: wraps the council generation with has_any_llm() check.
Returns a mock assessment when no LLM is available.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.wargame_advisor")


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _load_wargame(conn: Any, wargame_id: str, ph: str) -> dict | None:
    """Return sg_wargames row as dict, or None if not found."""
    try:
        row = conn.execute(
            f"SELECT id, name, scenario, state, blue_strength, red_strength, "  # nosec B608
            f"blue_force, red_force, attrition_coefficients_json, outcome, updated_at "
            f"FROM sg_wargames WHERE id = {ph}",
            (wargame_id,),
        ).fetchone()
    except Exception as exc:
        logger.debug("sg_wargames query failed: %s", exc)
        return None
    if row is None:
        return None
    cols = [
        "id", "name", "scenario", "state", "blue_strength", "red_strength",
        "blue_force", "red_force", "attrition_coefficients_json", "outcome", "updated_at",
    ]
    return dict(zip(cols, row))


def _load_latest_turn(conn: Any, wargame_id: str, ph: str) -> dict | None:
    """Return the most recent sg_wargame_turns row as dict, or None."""
    try:
        row = conn.execute(
            f"SELECT id, turn_number, blue_losses, red_losses, "  # nosec B608
            f"blue_remaining, red_remaining, tempo_delta, notes, created_at "
            f"FROM sg_wargame_turns WHERE wargame_id = {ph} "
            f"ORDER BY turn_number DESC LIMIT 1",
            (wargame_id,),
        ).fetchone()
    except Exception as exc:
        logger.debug("sg_wargame_turns query failed: %s", exc)
        return None
    if row is None:
        return None
    cols = [
        "id", "turn_number", "blue_losses", "red_losses",
        "blue_remaining", "red_remaining", "tempo_delta", "notes", "created_at",
    ]
    return dict(zip(cols, row))


# ── Mock assessment (no-LLM path) ─────────────────────────────────────────────

def _mock_assessment(wargame: dict, latest_turn: dict | None) -> dict[str, Any]:
    """Deterministic assessment returned when no LLM is available."""
    blue = int(wargame.get("blue_strength") or 0)
    red = int(wargame.get("red_strength") or 0)
    wargame.get("scenario") or wargame.get("name") or "Unknown scenario"

    if blue > red * 1.2:
        risk = "LOW"
        summary = (
            f"[STUB — no LLM] Blue force holds numerical advantage "
            f"({blue} vs {red}). Current trajectory favors Blue."
        )
        rationale = "Blue numerical superiority reduces operational risk."
    elif red > blue * 1.2:
        risk = "HIGH"
        summary = (
            f"[STUB — no LLM] Red force holds numerical advantage "
            f"({red} vs {blue}). Blue faces elevated operational risk."
        )
        rationale = "Red numerical superiority elevates operational risk for Blue."
    else:
        risk = "MEDIUM"
        summary = (
            f"[STUB — no LLM] Forces are roughly balanced ({blue} Blue / {red} Red). "
            "Outcome is operationally undetermined."
        )
        rationale = "Parity conditions create moderate risk; tempo and initiative are decisive."

    if latest_turn:
        td = float(latest_turn.get("tempo_delta") or 0.0)
        if td > 0.1:
            summary += " Blue holds OODA tempo advantage."
        elif td < -0.1:
            summary += " Red holds OODA tempo advantage."

    return {
        "summary": summary,
        "recommended_coa": "COA Bravo",
        "risk_level": risk,
        "rationale": rationale,
        "llm_active": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
    }


# ── Theater inference ──────────────────────────────────────────────────────────

_THEATER_KEYWORDS: list[tuple[list[str], str]] = [
    (["taiwan", "pla", "strait", "pacific", "indo-pacific"], "taiwan"),
    (["ukraine", "russia", "eastern europe", "nato"], "ukraine"),
    (["middle east", "iran", "iraq", "syria", "israel", "persian gulf"], "middle_east"),
    (["korea", "dprk", "korean peninsula"], "korea"),
    (["south china sea", "spratly", "paracel"], "south_china_sea"),
    (["arctic", "polar"], "arctic"),
    (["cyber", "information warfare"], "cyber"),
]


def _infer_theater(scenario: str) -> str:
    lower = scenario.lower()
    for keywords, theater in _THEATER_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return theater
    return "unspecified"


# ── Main entry point ──────────────────────────────────────────────────────────

def get_ai_assessment(wargame_id: str) -> dict[str, Any]:
    """Return an AI-generated strategic assessment for the given wargame.

    Steps:
      1. Load sg_wargames + latest sg_wargame_turns row
      2. Call WarCouncilEngine.generate() (war council deliberation)
      3. Call ooda.score_coa() on the top COAs from the council brief
      4. Return {summary, recommended_coa, risk_level, rationale}

    Returns mock assessment when no LLM is reachable.

    Raises:
        ValueError: If wargame_id is not found in sg_wargames.
    """
    from tools.db.storage import get_connection, is_pg
    from tools.llm.router import LLMRouter

    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        wargame = _load_wargame(conn, wargame_id, ph)
        if wargame is None:
            raise ValueError(f"Wargame {wargame_id!r} not found in sg_wargames")
        latest_turn = _load_latest_turn(conn, wargame_id, ph)
    finally:
        conn.close()

    if not LLMRouter().has_any_llm():
        logger.info("No LLM reachable — returning mock assessment for wargame %s", wargame_id)
        return _mock_assessment(wargame, latest_turn)

    # Build enriched scenario description from wargame state
    scenario = wargame.get("scenario") or wargame.get("name") or f"Wargame {wargame_id[:8]}"
    blue_strength = int(wargame.get("blue_strength") or 0)
    red_strength = int(wargame.get("red_strength") or 0)

    state_lines: list[str] = [f"Wargame: {scenario}"]
    if blue_strength or red_strength:
        state_lines.append(f"Force balance: Blue={blue_strength}, Red={red_strength}")
    if latest_turn:
        tn = latest_turn.get("turn_number", 0)
        td = float(latest_turn.get("tempo_delta") or 0.0)
        state_lines.append(
            f"Latest turn: {tn}  Tempo delta: {td:+.4f} (positive = Blue advantage)"
        )
        try:
            notes = json.loads(latest_turn.get("notes") or "{}")
            winner = notes.get("lanchester_winner") or notes.get("blotto_winner")
            if winner:
                state_lines.append(f"Current momentum: {winner}")
        except Exception:
            pass

    enriched_scenario = "\n".join(state_lines)

    # War council deliberation
    from tools.strategos.war_council import WarCouncilEngine, WarCouncilRequest
    from tools.strategos.ooda import score_coa

    req = WarCouncilRequest(
        scenario=enriched_scenario,
        theater=_infer_theater(scenario),
        commander_intent=f"Achieve decisive outcome in {scenario}",
    )

    try:
        brief = WarCouncilEngine().generate(req)
        strategy_result = brief.strategy_result
        error = brief.error
    except Exception as exc:
        logger.warning("War council deliberation failed: %s — returning mock", exc)
        return _mock_assessment(wargame, latest_turn)

    if not strategy_result.courses_of_action:
        return _mock_assessment(wargame, latest_turn)

    # Score top COAs using ooda.score_coa() (maps F/A/S fields to OODA criteria)
    coa_dicts = [coa.to_dict() for coa in strategy_result.courses_of_action]
    for d in coa_dicts:
        d.setdefault("speed", d.get("feasibility", 0.5))
        d.setdefault("surprise", d.get("acceptability", 0.5))
        d.setdefault("mass", d.get("suitability", 0.5))
        d.setdefault("economy_of_force", (d.get("feasibility", 0.5) + d.get("acceptability", 0.5)) / 2)
        d.setdefault("maneuver", d.get("suitability", 0.5))
        d.setdefault("sustainability", d.get("acceptability", 0.5))

    ranked_coas = score_coa(coa_dicts)

    rec_idx = strategy_result.recommended_coa_index
    rec_coa = strategy_result.courses_of_action[rec_idx]
    risk_level = rec_coa.risk_level or "MEDIUM"

    rationale_parts: list[str] = []
    if rec_coa.distinguishing_factor:
        rationale_parts.append(rec_coa.distinguishing_factor)
    if rec_coa.doctrine_basis:
        rationale_parts.append(f"Doctrine: {rec_coa.doctrine_basis}")
    if rec_coa.historical_analogy:
        rationale_parts.append(f"Precedent: {rec_coa.historical_analogy}")
    if not rationale_parts:
        rationale_parts.append(rec_coa.description or "See recommended COA.")

    summary_parts: list[str] = [rec_coa.description or ""]
    if strategy_result.rag_active:
        summary_parts.append(
            f"({strategy_result.rag_doc_count} doctrine/event documents retrieved)"
        )

    return {
        "summary": " ".join(p for p in summary_parts if p).strip(),
        "recommended_coa": rec_coa.name,
        "risk_level": risk_level,
        "rationale": " | ".join(rationale_parts),
        "llm_active": True,
        "model_used": strategy_result.model_used,
        "rag_active": strategy_result.rag_active,
        "rag_doc_count": strategy_result.rag_doc_count,
        "coa_scores": [
            {
                "name": c.get("name"),
                "composite_score": c.get("composite_score"),
                "rank": c.get("rank"),
            }
            for c in ranked_coas
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json
    import sys

    parser = argparse.ArgumentParser(
        description="Wargame Advisor — AI assessment for a wargame session"
    )
    parser.add_argument("wargame_id", help="sg_wargames UUID")
    parser.add_argument("--json", dest="as_json", action="store_true")
    _args = parser.parse_args()

    import logging as _logging
    _logging.basicConfig(level=_logging.WARNING, stream=sys.stderr)

    try:
        _result = get_ai_assessment(_args.wargame_id)
    except ValueError as _exc:
        print(f"Error: {_exc}", file=sys.stderr)
        sys.exit(1)

    if _args.as_json:
        print(_json.dumps(_result, indent=2))
    else:
        print("\n── Wargame AI Assessment ──────────────────────────────────")
        print(f"LLM       : {'ON (' + _result.get('model_used', '') + ')' if _result.get('llm_active') else 'STUB'}")
        print(f"Rec COA   : {_result['recommended_coa']}")
        print(f"Risk      : {_result['risk_level']}")
        print(f"Summary   : {_result['summary'][:160]}")
        print(f"Rationale : {_result['rationale'][:160]}")
        if _result.get("error"):
            print(f"Error     : {_result['error']}")
