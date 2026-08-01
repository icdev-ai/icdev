# CUI // SP-CTI
"""TTX Engine — main orchestrator for tabletop exercise sessions."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from .scenario_loader import load_scenario
from .session_manager import create_session, get_session, update_session_state
from .team_manager import create_team, add_member
from .inject_dispatcher import (
    seed_injects, dispatch_inject, close_inject,
    get_dispatched_injects, get_all_injects, unlock_next_async,
)
from .ai_scorer import score_response
from .persona_generator import generate_persona
from .leaderboard import compute_leaderboard, get_leaderboard, award_ribbons
from .aar_generator import generate_aar

log = get_logger(__name__)


class TTXEngine:
    """Facade over all TTX engine subsystems."""

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(
        self,
        scenario_slug: str,
        facilitator_name: str,
        session_mode: str | None = None,
        config: dict | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        scenario = load_scenario(scenario_slug)
        mode = session_mode or scenario.get("session_mode", "live")
        duration = scenario.get("duration_minutes", 120)
        max_teams = scenario.get("max_teams", 8)
        session = create_session(
            scenario_slug=scenario_slug,
            session_mode=mode,
            facilitator_name=facilitator_name,
            duration_minutes=duration,
            max_teams=max_teams,
            config={"scenario": scenario, **(config or {})},
            tenant_id=tenant_id,
        )
        session_id = session["session_id"]
        seed_injects(session_id, scenario)
        log.info("Created session %s for scenario %s", session_id, scenario_slug)
        return session

    def start_session(self, session_id: int) -> dict[str, Any]:
        session = update_session_state(session_id, "active")
        # For async mode: dispatch first inject(s) with no depends_on
        s = get_session(session_id)
        if s and s.get("session_mode") == "async":
            conn = get_connection()
            first_injects = conn.execute(
                """SELECT inject_id FROM ttx_injects
                   WHERE session_id = %s AND depends_on_slug IS NULL AND state = 'pending'""",
                (session_id,),
            ).fetchall()
            for row in first_injects:
                dispatch_inject(row["inject_id"])
        return session

    def pause_session(self, session_id: int) -> dict[str, Any]:
        return update_session_state(session_id, "paused")

    def end_session(self, session_id: int) -> dict[str, Any]:
        compute_leaderboard(session_id)
        return update_session_state(session_id, "ended")

    # ------------------------------------------------------------------
    # Team + player management
    # ------------------------------------------------------------------

    def create_team(self, session_id: int, team_name: str) -> dict[str, Any]:
        return create_team(session_id, team_name)

    def join_team(
        self,
        team_id: int,
        player_name: str,
        role_id: str,
        scenario_roles: list[dict] | None = None,
    ) -> dict[str, Any]:
        persona_data = None
        if scenario_roles:
            for role in scenario_roles:
                if role.get("id") == role_id:
                    persona_data = role.get("persona_data")
                    break
        persona = generate_persona(role_id, player_name, persona_data)
        return add_member(team_id, player_name, role_id, persona)

    # ------------------------------------------------------------------
    # Inject management
    # ------------------------------------------------------------------

    def dispatch_inject(self, inject_id: str) -> bool:
        return dispatch_inject(inject_id)

    def close_inject(self, inject_id: str) -> None:
        close_inject(inject_id)

    def get_injects(self, session_id: int, state: str | None = None) -> list[dict[str, Any]]:
        if state == "dispatched":
            return get_dispatched_injects(session_id)
        return get_all_injects(session_id)

    def check_async_unlock(self, session_id: int, team_id: int) -> dict | None:
        return unlock_next_async(session_id, team_id)

    # ------------------------------------------------------------------
    # Response submission + scoring
    # ------------------------------------------------------------------

    def submit_response(
        self,
        team_id: int,
        inject_id: str,
        session_id: int,
        response_text: str,
        receipts: list[dict] | None = None,
        time_taken_s: float | None = None,
    ) -> dict[str, Any]:
        conn = get_connection()

        # Persist response
        conn.execute(
            """INSERT INTO ttx_responses
               (team_id, inject_id, response_text, ai_receipts_json,
                submitted_at, time_taken_s)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                team_id, inject_id, response_text,
                json.dumps(receipts or []),
                datetime.now(timezone.utc).isoformat(),
                time_taken_s,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT response_id FROM ttx_responses WHERE team_id = %s AND inject_id = %s ORDER BY submitted_at DESC LIMIT 1",
            (team_id, inject_id),
        ).fetchone()
        response_id = row["response_id"]

        # Load inject config for scoring params
        inj = conn.execute(
            "SELECT body_md, config_json FROM ttx_injects WHERE inject_id = %s", (inject_id,)
        ).fetchone()
        cfg = json.loads(inj["config_json"] or "{}") if inj else {}
        scoring = cfg.get("scoring", {})
        inject_type = cfg.get("inject_type", "")

        result = score_response(
            response_id=response_id,
            team_id=team_id,
            inject_id=inject_id,
            session_id=session_id,
            response_text=response_text,
            inject_body=inj["body_md"] if inj else "",
            receipts=receipts or [],
            rubric=scoring.get("rubric", {}),
            bonus_per_call=scoring.get("receipt_bonus_per_call", 50),
            max_receipt_bonus=scoring.get("max_receipt_bonus", 150),
            time_taken_s=time_taken_s,
            time_bonus_enabled=scoring.get("time_bonus", True),
            inject_type=inject_type,
        )
        # Recompute leaderboard after each submission
        compute_leaderboard(session_id)
        return {"response_id": response_id, **result}

    # ------------------------------------------------------------------
    # Leaderboard & AAR
    # ------------------------------------------------------------------

    def get_leaderboard(self, session_id: int) -> list[dict[str, Any]]:
        return get_leaderboard(session_id)

    def get_ribbons(self, session_id: int) -> dict:
        return award_ribbons(session_id)

    def generate_aar(self, session_id: int) -> str:
        return generate_aar(session_id)

    # ------------------------------------------------------------------
    # Receipt logging (called by ICDEV tool endpoints)
    # ------------------------------------------------------------------

    def log_api_receipt(
        self,
        session_id: int,
        team_id: int,
        tool_slug: str,
        endpoint: str,
        call_id: str,
        result_hash: str,
        token_count: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Append an AI-call receipt for a team. ``token_count``/``cost_usd`` are
        the per-team spend attribution (lpx-teams-03) — recorded here at the
        existing insert hook so the facilitator report is a single-store query.
        Both default to 0 so callers that only log the call keep working."""
        conn = get_connection()
        conn.execute(
            """INSERT INTO ttx_api_log
               (session_id, team_id, tool_slug, endpoint, call_id, result_hash,
                token_count, cost_usd, called_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                session_id, team_id, tool_slug, endpoint,
                call_id, result_hash,
                max(0, int(token_count)), max(0.0, float(cost_usd)),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
