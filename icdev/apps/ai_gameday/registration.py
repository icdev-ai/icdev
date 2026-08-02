# CUI // SP-CTI
"""Pre-session registration and snake-draft team formation (gdx-reg-01).

Players register for a session, state their skills, and are matched to one of
the scenario's roles. The facilitator then runs a snake draft over the roster,
adjusts it by hand, and confirms — which materialises real ``ttx_teams`` and
``ttx_team_members`` rows that the rest of the GameDay flow already understands.

Roles are never hardcoded. Every role comes from the session's own scenario
definition (``config_json -> scenario -> roles``), which is what the ``/play``
console already renders, so a scenario with different roles matches against
those and nothing here needs to change.

An earlier implementation of this module was deleted by ``penta-gd-03`` as
unreachable dead code — nothing routed to it. This one is wired; see
``blueprint.py`` and the tests in ``tests/test_gameday_registration.py``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from tools.db.storage import get_connection

from .constants import TECHNICAL_MARKERS

#: Tokens that carry no signal when matching a skill blurb to a role.
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "with", "for", "of", "in", "on", "at", "to", "is",
    "am", "are", "was", "were", "be", "been", "i", "my", "me", "we", "our",
    "years", "year", "yrs", "experience", "experienced", "work", "worked",
    "working", "using", "used", "use", "some", "lots", "lot", "very", "really",
    "good", "strong", "solid", "familiar", "knowledge", "skills", "skill",
})

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


def _tokens(text: str) -> set:
    """Lowercase content tokens, stopwords and 1-character noise removed."""
    return {
        t for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) > 1 and t not in _STOPWORDS
    }


def _role_tokens(role: Dict[str, Any]) -> set:
    """Everything a role says about itself, as a token bag."""
    parts = [
        str(role.get("label") or ""),
        str(role.get("description") or ""),
        str(role.get("id") or "").replace("_", " ").replace("-", " "),
    ]
    missions = role.get("missions") or role.get("objectives") or []
    if isinstance(missions, (list, tuple)):
        parts.extend(str(m) for m in missions)
    skills = role.get("skills") or role.get("keywords") or []
    if isinstance(skills, (list, tuple)):
        parts.extend(str(s) for s in skills)
    return _tokens(" ".join(parts))


def match_skill_to_role(
    stated_skill: str, roles: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Best-fitting role for a free-text skill description.

    Deterministic token overlap against each role's own text — no LLM call. A
    registration form is a hot, unauthenticated path where a provider outage
    must not stop someone signing up, and the facilitator can override every
    result from the roster view anyway.

    Confidence is the share of the player's own tokens that the role accounts
    for, so a short precise blurb scores higher than a long vague one. Returns
    None when there are no roles to match against.
    """
    if not roles:
        return None

    player = _tokens(stated_skill)
    scored: List[tuple] = []
    for role in roles:
        overlap = player & _role_tokens(role)
        scored.append((len(overlap), overlap, role))

    scored.sort(key=lambda row: row[0], reverse=True)
    hits, overlap, role = scored[0]

    if not player or hits == 0:
        # Nothing matched. Say so rather than inventing a confident answer —
        # the form shows this confidence to the player.
        return {
            "role_id": str(role.get("id") or ""),
            "role_label": str(role.get("label") or role.get("id") or ""),
            "icon": role.get("icon") or "🎯",
            "confidence": 0.0,
            "method": "keyword",
            "reasoning": (
                "No overlap with any role description — pick a role manually, "
                "or describe the tools and systems you work with."
            ),
        }

    confidence = round(min(1.0, hits / max(len(player), 1)), 3)
    matched = ", ".join(sorted(overlap)[:6])
    return {
        "role_id": str(role.get("id") or ""),
        "role_label": str(role.get("label") or role.get("id") or ""),
        "icon": role.get("icon") or "🎯",
        "confidence": confidence,
        "method": "keyword",
        "reasoning": f"Matched on: {matched}",
    }


# ---------------------------------------------------------------------------
# Scenario fit
# ---------------------------------------------------------------------------

def technical_ratio(registrations: Sequence[Dict[str, Any]]) -> float:
    """Share of the roster whose matched role reads as hands-on technical.

    Reported to the facilitator as context for scenario choice, not used to
    gate anything. Zero for an empty roster rather than undefined.
    """
    roster = [r for r in registrations if r]
    if not roster:
        return 0.0
    technical = sum(
        1 for r in roster
        if _tokens(
            f"{r.get('matched_role_label') or ''} {r.get('stated_skill') or ''}"
        ) & TECHNICAL_MARKERS
    )
    return round(technical / len(roster), 3)


def scenario_fit(
    registrations: Sequence[Dict[str, Any]], scenario: Dict[str, Any]
) -> float:
    """0–1 fit between a roster and a scenario, by role coverage.

    A scenario fits when the roles people registered for are roles it actually
    defines. An empty roster gives every scenario 0.5 — no evidence either way
    is not the same as a bad fit, and ranking them all at 0 would tell the
    facilitator nothing.
    """
    roster = [r for r in registrations if r]
    scenario_roles = scenario.get("roles") or []
    if not roster or not scenario_roles:
        return 0.5

    role_ids = {str(r.get("id") or "") for r in scenario_roles}
    role_bags = [_role_tokens(r) for r in scenario_roles]

    covered = 0
    for reg in roster:
        if str(reg.get("matched_role_id") or "") in role_ids:
            covered += 1
            continue
        # Different scenario, comparable role — count a token match too.
        player = _tokens(
            f"{reg.get('matched_role_label') or ''} {reg.get('stated_skill') or ''}"
        )
        if any(player & bag for bag in role_bags):
            covered += 1
    return round(covered / len(roster), 3)


def recommendation_reasoning(
    registrations: Sequence[Dict[str, Any]], options: Sequence[Dict[str, Any]]
) -> str:
    """One sentence explaining the ranking, for the facilitator panel."""
    roster = [r for r in registrations if r]
    if not roster:
        return "No registrations yet — every scenario is ranked neutrally."
    if not options:
        return "No scenario packs found on disk."
    best = options[0]
    pct = int(round(technical_ratio(roster) * 100))
    return (
        f"{len(roster)} registered, {pct}% in technical roles. "
        f"{best['label']} covers {int(round(best['fit_score'] * 100))}% of the "
        "roles people signed up for."
    )


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

_REG_COLUMNS = (
    "registration_id, session_id, player_name, email, stated_skill, "
    "matched_role_id, matched_role_label, match_confidence, match_method, "
    "match_reasoning, academy_username, registered_at"
)


def create_registration(session_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Insert one registration. Returns the stored row.

    Raises ValueError on missing required fields so the route can answer 400
    rather than surfacing a database error to a player.
    """
    player_name = str(payload.get("player_name") or "").strip()
    role_id = str(payload.get("role_id") or "").strip()
    role_label = str(payload.get("role_label") or "").strip()
    if not player_name:
        raise ValueError("player_name is required")
    if not role_id:
        raise ValueError("role_id is required")

    stated_skill = str(payload.get("stated_skill") or "").strip() or role_label
    try:
        confidence = float(payload.get("match_confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    confidence = max(0.0, min(1.0, confidence))

    conn = get_connection()
    conn.execute(
        "INSERT INTO ttx_registrations "
        "(session_id, player_name, email, stated_skill, matched_role_id, "
        " matched_role_label, match_confidence, match_method, match_reasoning, "
        " academy_username) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            session_id,
            player_name,
            str(payload.get("email") or "").strip() or None,
            stated_skill,
            role_id,
            role_label or role_id,
            confidence,
            str(payload.get("match_method") or "selected"),
            str(payload.get("match_reasoning") or "") or None,
            str(payload.get("academy_username") or "").strip() or None,
        ),
    )
    conn.commit()
    rows = list_registrations(session_id)
    return rows[-1] if rows else {}


def list_registrations(session_id: int) -> List[Dict[str, Any]]:
    """Roster for a session, oldest first."""
    conn = get_connection()
    cur = conn.execute(
        f"SELECT {_REG_COLUMNS} FROM ttx_registrations "
        "WHERE session_id = %s ORDER BY registration_id",
        (session_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def delete_registration(registration_id: int) -> bool:
    """Remove a registration and any draft slot holding it."""
    conn = get_connection()
    cur = conn.execute(
        "SELECT registration_id FROM ttx_registrations WHERE registration_id = %s",
        (registration_id,),
    )
    if not cur.fetchone():
        return False
    conn.execute(
        "DELETE FROM ttx_formation_plan WHERE registration_id = %s", (registration_id,)
    )
    conn.execute(
        "DELETE FROM ttx_registrations WHERE registration_id = %s", (registration_id,)
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Snake draft
# ---------------------------------------------------------------------------

def snake_draft(
    registrations: Sequence[Dict[str, Any]], max_teams: int
) -> List[Dict[str, Any]]:
    """Deal the roster into teams in snake order (0,1,2 → 2,1,0 → …).

    Serpentine rather than round-robin so the team that picks last in one round
    picks first in the next; with the roster sorted strongest-match-first that
    keeps confidence roughly level across teams instead of stacking it in team 1.

    Also spreads duplicate roles: players are grouped by matched role and dealt
    role-block by role-block, so five people who all matched "Analyst" land on
    five different teams rather than all on the first.

    Returns ``[{team_slot, team_name, members: [...]}, ...]`` — the exact shape
    registrations.html renders.
    """
    roster = [r for r in registrations if r]
    if not roster:
        return []

    teams_wanted = max(1, min(int(max_teams or 1), len(roster)))

    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for reg in roster:
        by_role.setdefault(str(reg.get("matched_role_id") or ""), []).append(reg)
    # Largest role blocks first, and strongest match first inside each block.
    ordered: List[Dict[str, Any]] = []
    for role_id in sorted(by_role, key=lambda k: (-len(by_role[k]), k)):
        ordered.extend(
            sorted(
                by_role[role_id],
                key=lambda r: (-float(r.get("match_confidence") or 0.0),
                               int(r.get("registration_id") or 0)),
            )
        )

    buckets: List[List[Dict[str, Any]]] = [[] for _ in range(teams_wanted)]
    for index, reg in enumerate(ordered):
        round_no, position = divmod(index, teams_wanted)
        slot = position if round_no % 2 == 0 else teams_wanted - 1 - position
        buckets[slot].append(reg)

    return [
        {
            "team_slot": slot,
            "team_name": f"Team {slot + 1}",
            "members": [
                {
                    "registration_id": m.get("registration_id"),
                    "player_name": m.get("player_name"),
                    "role_id": m.get("matched_role_id"),
                    "role_label": m.get("matched_role_label"),
                    "match_confidence": m.get("match_confidence"),
                    "academy_username": m.get("academy_username"),
                }
                for m in members
            ],
        }
        for slot, members in enumerate(buckets)
    ]


def save_formation_plan(session_id: int, teams: Sequence[Dict[str, Any]]) -> None:
    """Replace the stored draft for a session."""
    conn = get_connection()
    conn.execute("DELETE FROM ttx_formation_plan WHERE session_id = %s", (session_id,))
    for team in teams:
        for member in team.get("members") or []:
            conn.execute(
                "INSERT INTO ttx_formation_plan "
                "(session_id, registration_id, team_slot, team_name) "
                "VALUES (%s, %s, %s, %s)",
                (
                    session_id,
                    member.get("registration_id"),
                    int(team.get("team_slot") or 0),
                    str(team.get("team_name") or ""),
                ),
            )
    conn.commit()


def get_formation_plan(session_id: int) -> List[Dict[str, Any]]:
    """Stored draft, rebuilt into the team shape the template renders.

    Empty list when no draft has been run — the facilitator view then shows the
    roster with an un-drafted board rather than erroring.
    """
    conn = get_connection()
    cur = conn.execute(
        "SELECT p.team_slot, p.team_name, p.registration_id, "
        "       r.player_name, r.matched_role_id, r.matched_role_label, "
        "       r.match_confidence, r.academy_username "
        "FROM ttx_formation_plan p "
        "JOIN ttx_registrations r ON r.registration_id = p.registration_id "
        "WHERE p.session_id = %s "
        "ORDER BY p.team_slot, p.plan_id",
        (session_id,),
    )
    teams: Dict[int, Dict[str, Any]] = {}
    for row in cur.fetchall():
        row = dict(row)
        slot = int(row["team_slot"])
        team = teams.setdefault(
            slot, {"team_slot": slot, "team_name": row["team_name"], "members": []}
        )
        team["members"].append({
            "registration_id": row["registration_id"],
            "player_name": row["player_name"],
            "role_id": row["matched_role_id"],
            "role_label": row["matched_role_label"],
            "match_confidence": row["match_confidence"],
            "academy_username": row["academy_username"],
        })
    return [teams[slot] for slot in sorted(teams)]


def move_player(
    session_id: int, registration_id: int, team_slot: int, team_name: str
) -> List[Dict[str, Any]]:
    """Move one player to another team slot; returns the updated plan.

    Raises ValueError if there is no draft yet or the player is not in it —
    moving a player into a plan that does not exist would silently create a
    one-team draft and lose everyone else.
    """
    conn = get_connection()
    cur = conn.execute(
        "SELECT plan_id FROM ttx_formation_plan "
        "WHERE session_id = %s AND registration_id = %s",
        (session_id, registration_id),
    )
    if not cur.fetchone():
        raise ValueError("player is not in the current formation plan")

    conn.execute(
        "UPDATE ttx_formation_plan SET team_slot = %s, team_name = %s "
        "WHERE session_id = %s AND registration_id = %s",
        (int(team_slot), str(team_name), session_id, registration_id),
    )
    conn.commit()
    return get_formation_plan(session_id)


def confirm_formation(session_id: int) -> Dict[str, int]:
    """Materialise the draft into real ttx_teams / ttx_team_members rows.

    Goes through ``tools.ttx.team_manager`` rather than writing those two tables
    directly, so confirmed teams are indistinguishable from ones created any
    other way — same join-code scheme, same persona/timestamp defaults. The rest
    of the GameDay flow then needs no knowledge that a draft was involved.

    Confirming twice replaces rather than doubles: teams already created for the
    session are cleared first.
    """
    from tools.ttx.team_manager import add_member, create_team  # noqa: PLC0415

    plan = get_formation_plan(session_id)
    if not plan:
        raise ValueError("no formation plan to confirm — run the draft first")

    conn = get_connection()
    cur = conn.execute(
        "SELECT team_id FROM ttx_teams WHERE session_id = %s", (session_id,)
    )
    for row in cur.fetchall():
        conn.execute(
            "DELETE FROM ttx_team_members WHERE team_id = %s", (dict(row)["team_id"],)
        )
    conn.execute("DELETE FROM ttx_teams WHERE session_id = %s", (session_id,))
    conn.commit()

    teams_created = 0
    members_created = 0
    for team in plan:
        if not team.get("members"):
            continue
        created = create_team(session_id, team["team_name"])
        teams_created += 1
        for member in team["members"]:
            add_member(
                created["team_id"],
                member["player_name"],
                member["role_id"] or "",
                persona={"from_registration_id": member["registration_id"]},
            )
            members_created += 1

    conn.execute(
        "UPDATE ttx_formation_plan SET confirmed = 1 WHERE session_id = %s",
        (session_id,),
    )
    conn.commit()
    return {"teams_created": teams_created, "members_created": members_created}
