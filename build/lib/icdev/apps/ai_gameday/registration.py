# CUI // SP-CTI
"""AI GameDay — player registration + snake-draft team formation."""

from __future__ import annotations

import json
import math
import random
import re
from datetime import datetime, timezone

from tools.ttx.scenario_loader import load_scenario, list_scenario_slugs
from tools.db.storage import get_connection


def get_all_scenario_roles() -> list[dict]:
    """Aggregate roles from every loadable scenario pack, deduped by role_id."""
    roles_by_id: dict[str, dict] = {}
    for slug in list_scenario_slugs():
        try:
            scenario = load_scenario(slug)
        except Exception:
            continue
        for role in scenario.get("roles", []):
            rid = role["id"]
            if rid not in roles_by_id:
                roles_by_id[rid] = {
                    "id": rid,
                    "label": role.get("label", rid),
                    "icon": role.get("icon", ""),
                    "color": role.get("color", "#8b949e"),
                    "description": role.get("description", ""),
                    "academy_missions": role.get("academy_missions", []),
                    "scenarios": [],
                }
            roles_by_id[rid]["scenarios"].append(slug)
    return list(roles_by_id.values())


def get_scenario_roles(scenario_slug: str) -> list[dict]:
    """Return roles for one specific scenario."""
    try:
        scenario = load_scenario(scenario_slug)
        return [
            {
                "id": r["id"],
                "label": r.get("label", r["id"]),
                "icon": r.get("icon", ""),
                "color": r.get("color", "#8b949e"),
                "description": r.get("description", ""),
                "academy_missions": r.get("academy_missions", []),
            }
            for r in scenario.get("roles", [])
        ]
    except Exception:
        return []


# Keyword banks for scoring each role_id against free-text input
_ROLE_KEYWORDS: dict[str, list[str]] = {
    "ml_engineer": [
        "machine learning", "ml", "model", "training", "fine-tuning", "finetune",
        "pytorch", "tensorflow", "mlops", "inference", "neural", "deep learning",
        "huggingface", "transformers", "llm", "embedding", "weights", "epochs",
        "model ops", "model deployment", "model evaluation", "mlflow",
    ],
    "data_scientist": [
        "data science", "data scientist", "analytics", "statistics", "pandas",
        "numpy", "feature engineering", "eda", "visualization", "regression",
        "classification", "clustering", "jupyter", "notebook", "dataset", "drift",
        "sklearn", "scikit", "scipy", "data analysis", "data pipeline",
    ],
    "devops_engineer": [
        "devops", "platform engineer", "kubernetes", "k8s", "docker", "container",
        "terraform", "ansible", "cicd", "ci/cd", "pipeline", "helm", "iac",
        "infrastructure", "deployment", "aws", "azure", "gcp", "eks",
        "argocd", "jenkins", "github actions", "gitops", "containerization",
    ],
    "swe": [
        "software engineer", "swe", "developer", "programming", "backend",
        "frontend", "api", "rest", "python", "java", "golang", "typescript",
        "unit test", "testing", "code review", "debugging", "refactor",
        "software development", "engineer",
    ],
    "network_engineer": [
        "network", "networking", "zero trust", "zta", "firewall", "vpn",
        "bgp", "ospf", "tcp/ip", "dns", "load balancer", "ingress",
        "network policy", "microsegmentation", "security group",
        "network security", "network analysis", "topology",
    ],
    "captain": [
        "captain", "commanding officer", "commander", "leadership",
        "strategy", "strategic planning", "operations", "co", "coa",
    ],
    "signals_intel": [
        "signals", "sigint", "signal intelligence", "elint", "electronic",
        "spectrum", "frequency", "radar", "communications intelligence",
    ],
    "imagery_analyst": [
        "imagery", "geospatial", "satellite", "reconnaissance", "isr",
        "remote sensing", "photogrammetry", "gis",
    ],
    "sonar_analyst": [
        "sonar", "acoustic", "subsurface", "underwater", "hydrophone",
        "maritime", "submarine",
    ],
    "military_intel": [
        "military intelligence", "intel analyst", "s2", "j2",
        "threat analysis", "intelligence assessment", "all source",
    ],
    "civilian_analyst": [
        "civilian analyst", "policy analyst", "research analyst",
        "program analyst", "contractor analyst",
    ],
    "contractor_swe": [
        "contractor", "defense contractor", "software contractor",
        "systems integrator",
    ],
    "ciso_lead": [
        "ciso", "chief information security", "cybersecurity", "infosec",
        "cyber defense", "incident response", "zero trust security",
    ],
}


def match_skill_to_role(
    stated_skill: str,
    scenario_slug: str | None = None,
) -> dict:
    """Match free-text skill description to the closest scenario role.

    Returns:
        {role_id, role_label, confidence (0-1), method, reasoning}
    """
    if scenario_slug:
        roles = get_scenario_roles(scenario_slug)
    else:
        roles = get_all_scenario_roles()

    if not roles:
        return {
            "role_id": "custom",
            "role_label": "Custom",
            "confidence": 0.5,
            "method": "fallback",
            "reasoning": "No roles available for matching",
        }

    skill_lower = stated_skill.lower()
    best_role_id: str | None = None
    best_score = -1.0
    best_role_label = ""

    for role in roles:
        rid = role["id"]
        # Base keyword bank + role metadata words
        keywords: list[str] = list(_ROLE_KEYWORDS.get(rid, []))
        keywords += role["label"].lower().split()
        keywords += role["description"].lower().split()
        keywords += [m.replace("-", " ") for m in role.get("academy_missions", [])]
        keywords_unique = list(set(keywords))

        hits = sum(1 for kw in keywords_unique if kw in skill_lower)
        normalized = hits / max(len(keywords_unique), 1)

        if normalized > best_score:
            best_score = normalized
            best_role_id = rid
            best_role_label = role["label"]

    # Weak keyword match — try LLM
    if best_score < 0.04:
        llm_result = _llm_match(stated_skill, roles)
        if llm_result:
            return llm_result

    confidence = round(min(1.0, max(0.3, best_score * 12)), 2)
    return {
        "role_id": best_role_id or roles[0]["id"],
        "role_label": best_role_label or roles[0]["label"],
        "confidence": confidence,
        "method": "keyword",
        "reasoning": f"Matched '{best_role_id}' via keyword overlap (score={best_score:.3f})",
    }


def _llm_match(stated_skill: str, roles: list[dict]) -> dict | None:
    """LLM-based skill→role classification. Returns None on any failure."""
    try:
        from tools.llm.router import LLMRouter, LLMRequest  # type: ignore
        router = LLMRouter()
        role_list = "\n".join(
            f"- {r['id']}: {r['label']} — {r['description']}"
            for r in roles
        )
        prompt = (
            "You are a team formation assistant for a technical competition.\n"
            f'A player describes their background as: "{stated_skill}"\n\n'
            f"Available roles:\n{role_list}\n\n"
            "Respond with ONLY a JSON object (no markdown, no explanation):\n"
            '{"role_id": "<id>", "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}'
        )
        req = LLMRequest(
            function="classification",
            prompt=prompt,
            max_tokens=160,
            temperature=0.0,
        )
        result = router.complete(req)
        text = (result.get("text") or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            rid = data.get("role_id", "")
            matched = next((r for r in roles if r["id"] == rid), None)
            if matched:
                return {
                    "role_id": rid,
                    "role_label": matched["label"],
                    "confidence": round(float(data.get("confidence", 0.7)), 2),
                    "method": "llm",
                    "reasoning": data.get("reasoning", "LLM classification"),
                }
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Team formation — snake-draft
# ---------------------------------------------------------------------------

_DEFAULT_TEAM_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta",
    "Echo", "Foxtrot", "Ghost", "Hydra",
]


def form_teams(
    registrations: list[dict],
    num_teams: int,
    team_names: list[str] | None = None,
) -> list[dict]:
    """Snake-draft assignment. Returns plan rows (no DB IDs yet).

    Strategy:
    - Group registrations by role_id
    - Interleave layers: first player from each role, then second, etc.
    - Snake-draft the interleaved queue across num_teams
    - This ensures role diversity: teams receive one of each role before any
      team gets a duplicate.

    Returns list of:
        {registration_id, player_name, role_id, team_slot: int, team_name: str}
    """
    if not registrations or num_teams < 1:
        return []

    names: list[str] = []
    if team_names:
        names = list(team_names)[:num_teams]
    while len(names) < num_teams:
        i = len(names)
        names.append(
            f"Team {_DEFAULT_TEAM_NAMES[i]}" if i < len(_DEFAULT_TEAM_NAMES)
            else f"Team {i + 1}"
        )

    # Group by role, shuffle within group
    by_role: dict[str, list[dict]] = {}
    for reg in registrations:
        rid = reg.get("matched_role_id", "custom")
        by_role.setdefault(rid, []).append(reg)
    for bucket in by_role.values():
        random.shuffle(bucket)

    # Interleave: layer 0 = first player of each role, layer 1 = second, etc.
    buckets = list(by_role.values())
    max_depth = max(len(b) for b in buckets) if buckets else 0
    pick_queue: list[dict] = []
    for depth in range(max_depth):
        for bucket in buckets:
            if depth < len(bucket):
                pick_queue.append(bucket[depth])

    # Snake-draft
    assignments: list[dict] = []
    fwd = list(range(num_teams))
    rev = list(range(num_teams - 1, -1, -1))
    for i, reg in enumerate(pick_queue):
        rnd = i // num_teams
        order = fwd if rnd % 2 == 0 else rev
        slot = order[i % num_teams]
        assignments.append({
            "registration_id": reg["registration_id"],
            "player_name": reg["player_name"],
            "role_id": reg.get("matched_role_id", "custom"),
            "team_slot": slot,
            "team_name": names[slot],
        })
    return assignments


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def create_registration(session_id: int, data: dict) -> dict:
    """Insert a new registration row and return it."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO ttx_registrations
           (session_id, player_name, email, stated_skill,
            matched_role_id, matched_role_label, match_confidence,
            match_method, match_reasoning, academy_username, registered_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id,
            data["player_name"],
            data.get("email", ""),
            data["stated_skill"],
            data["matched_role_id"],
            data["matched_role_label"],
            data.get("match_confidence", 1.0),
            data.get("match_method", "selected"),
            data.get("match_reasoning", ""),
            data.get("academy_username", ""),
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM ttx_registrations WHERE session_id=? AND player_name=? AND registered_at=?",
        (session_id, data["player_name"], now),
    ).fetchone()
    return dict(row) if row else {}


def get_registrations(session_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM ttx_registrations WHERE session_id = ?
           ORDER BY registered_at""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_formation_plan(session_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT fp.*, r.player_name, r.matched_role_id, r.matched_role_label,
                  r.academy_username, r.stated_skill
           FROM ttx_formation_plan fp
           JOIN ttx_registrations r ON r.registration_id = fp.registration_id
           WHERE fp.session_id = ?
           ORDER BY fp.team_slot, fp.plan_id""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_formation(session_id: int, max_teams: int | None = None) -> dict:
    """Compute snake-draft and persist to ttx_formation_plan.

    Returns {"teams": [...], "num_teams": int, "total_players": int}
    """
    conn = get_connection()
    regs = conn.execute(
        """SELECT registration_id, player_name, matched_role_id,
                  matched_role_label, academy_username, stated_skill
           FROM ttx_registrations WHERE session_id = ?
           ORDER BY registered_at""",
        (session_id,),
    ).fetchall()
    regs = [dict(r) for r in regs]

    if not regs:
        return {"teams": [], "num_teams": 0, "total_players": 0}

    n = len(regs)
    ideal_t = max(2, math.ceil(n / 5))
    if max_teams:
        ideal_t = min(ideal_t, max_teams)

    session_row = conn.execute(
        "SELECT max_teams FROM ttx_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if session_row:
        ideal_t = min(ideal_t, session_row["max_teams"])

    assignments = form_teams(regs, ideal_t)

    conn.execute("DELETE FROM ttx_formation_plan WHERE session_id = ?", (session_id,))
    now = datetime.now(timezone.utc).isoformat()
    for a in assignments:
        conn.execute(
            """INSERT INTO ttx_formation_plan
               (session_id, registration_id, team_slot, team_name, confirmed, created_at)
               VALUES (?,?,?,?,0,?)""",
            (session_id, a["registration_id"], a["team_slot"], a["team_name"], now),
        )
    conn.commit()

    # Build structured output
    teams_map: dict[int, dict] = {}
    for a in assignments:
        slot = a["team_slot"]
        if slot not in teams_map:
            teams_map[slot] = {"team_slot": slot, "team_name": a["team_name"], "members": []}
        teams_map[slot]["members"].append({
            "registration_id": a["registration_id"],
            "player_name": a["player_name"],
            "role_id": a["role_id"],
        })
    teams = sorted(teams_map.values(), key=lambda t: t["team_slot"])
    return {"teams": teams, "num_teams": ideal_t, "total_players": n}


def confirm_formation(session_id: int) -> dict:
    """Materialise formation plan into ttx_teams + ttx_team_members. Idempotent."""
    from tools.ttx.engine import TTXEngine
    from tools.ttx.session_manager import get_session

    conn = get_connection()
    plan = conn.execute(
        """SELECT fp.team_slot, fp.team_name,
                  r.player_name, r.matched_role_id, r.academy_username
           FROM ttx_formation_plan fp
           JOIN ttx_registrations r ON r.registration_id = fp.registration_id
           WHERE fp.session_id = ?
           ORDER BY fp.team_slot, fp.plan_id""",
        (session_id,),
    ).fetchall()

    if not plan:
        return {"teams_created": 0, "members_created": 0}

    session = get_session(session_id)
    cfg = json.loads(session.get("config_json") or "{}")
    scenario_roles = cfg.get("scenario", {}).get("roles", [])

    slots: dict[int, list[dict]] = {}
    for row in plan:
        row = dict(row)
        slots.setdefault(row["team_slot"], []).append(row)

    engine = TTXEngine()
    teams_created = 0
    members_created = 0

    for slot in sorted(slots):
        members = slots[slot]
        team_name = members[0]["team_name"]
        existing = conn.execute(
            "SELECT team_id FROM ttx_teams WHERE session_id=? AND team_name=?",
            (session_id, team_name),
        ).fetchone()
        if existing:
            team_id = existing["team_id"]
        else:
            team = engine.create_team(session_id, team_name)
            team_id = team["team_id"]
            teams_created += 1

        for m in members:
            try:
                engine.join_team(team_id, m["player_name"], m["matched_role_id"], scenario_roles)
                members_created += 1
            except Exception:
                pass
            if m.get("academy_username"):
                try:
                    conn.execute(
                        """UPDATE ttx_team_members
                           SET academy_username=?
                           WHERE team_id=? AND player_name=?""",
                        (m["academy_username"], team_id, m["player_name"]),
                    )
                except Exception:
                    pass

    conn.execute(
        "UPDATE ttx_formation_plan SET confirmed=1 WHERE session_id=?",
        (session_id,),
    )
    conn.commit()
    return {"teams_created": teams_created, "members_created": members_created}


# ---------------------------------------------------------------------------
# Scenario recommendation
# ---------------------------------------------------------------------------

def recommend_scenario(session_id: int) -> dict:
    """Analyse registration role composition and recommend the best-fit scenario.

    Returns:
        {tech_ratio, role_breakdown, total_players, recommended_slug,
         recommended_label, confidence, reasoning, all_options}
    """
    from apps.ai_gameday.constants import (
        ROLE_TECH_WEIGHTS, ROLE_TECH_WEIGHT_DEFAULT, SCENARIO_TECH_PROFILES,
    )

    # Strong tech-keyword signals used to derive a skill-based weight independent
    # of which game-scenario role was assigned (avoids scenario-biasing).
    _TECH_KEYWORDS = {
        "high": {
            "pytorch","tensorflow","kubernetes","k8s","docker","terraform","ansible",
            "mlops","ci/cd","cicd","devops","deep learning","neural","fine-tuning",
            "finetune","containerization","helm","iac","eks","argocd","gitops",
        },
        "mid": {
            "python","java","golang","api","backend","ml","model","sklearn","pandas",
            "numpy","feature engineering","network","firewall","zero trust","bgp",
            "security","developer","programming","data science","analytics",
        },
        "low": {
            # Non-technical / leadership roles
            "analyst","leadership","strategy","policy","management","pm","director",
            "executive","governance","acquisition","program","coa","brief",
            # Military / intelligence domain (specialised but not software-engineering)
            "sigint","signals intelligence","imagery","reconnaissance","sonar",
            "acoustic","maritime","submarine","satellite","isr","radar","spectrum",
            "frequency","geospatial","intelligence","military","commander",
            "commanding","officer","captain","tactical","operations",
        },
    }

    conn = get_connection()
    regs = conn.execute(
        "SELECT matched_role_id, stated_skill FROM ttx_registrations WHERE session_id = ?",
        (session_id,),
    ).fetchall()

    if not regs:
        return {
            "tech_ratio": 0.5,
            "role_breakdown": {},
            "total_players": 0,
            "recommended_slug": None,
            "recommended_label": "—",
            "confidence": 0.0,
            "reasoning": "No registrations yet — add players first.",
            "all_options": [],
        }

    role_counts: dict[str, int] = {}
    total_weight = 0.0
    for row in regs:
        rid = row["matched_role_id"] if hasattr(row, "keys") else row[0]
        rid = rid or "custom"
        role_counts[rid] = role_counts.get(rid, 0) + 1

        # Primary signal: role-based weight
        role_w = ROLE_TECH_WEIGHTS.get(rid, ROLE_TECH_WEIGHT_DEFAULT)

        # Secondary signal: keyword scan of stated_skill text (catches cross-scenario bias)
        skill_text = (row["stated_skill"] if hasattr(row, "keys") else row[1] or "").lower()
        high_hits = sum(1 for kw in _TECH_KEYWORDS["high"] if kw in skill_text)
        mid_hits  = sum(1 for kw in _TECH_KEYWORDS["mid"]  if kw in skill_text)
        low_hits  = sum(1 for kw in _TECH_KEYWORDS["low"]  if kw in skill_text)
        total_kw  = high_hits + mid_hits + low_hits
        if total_kw > 0:
            skill_w = (high_hits * 1.0 + mid_hits * 0.55 + low_hits * 0.05) / total_kw
            # 85% skill text, 15% role mapping.
            # High skill-weight dominance prevents scenario-assignment bias from
            # inflating non-technical players registered against a tech scenario.
            blended_w = 0.85 * skill_w + 0.15 * role_w
        else:
            blended_w = role_w

        total_weight += blended_w

    n = len(regs)
    tech_ratio = total_weight / n

    # Score every on-disk scenario
    options: list[dict] = []
    for slug in list_scenario_slugs():
        if slug == "ai_gameday":
            continue
        profile = SCENARIO_TECH_PROFILES.get(slug)
        if not profile:
            try:
                s = load_scenario(slug)
                profile = {
                    "label": s.get("name", slug),
                    "description": s.get("description", "")[:80],
                    "difficulty": "intermediate",
                    "audience": "mixed",
                    "tech_ideal": 0.50,
                    "tech_min": 0.0,
                    "tech_max": 1.0,
                    "icon": "🎯",
                    "color": "#8b949e",
                }
            except Exception:
                continue

        distance = abs(tech_ratio - profile["tech_ideal"])
        in_range = profile["tech_min"] <= tech_ratio <= profile["tech_max"]
        fit_score = round(1.0 - distance, 3)
        if not in_range:
            fit_score = round(fit_score * 0.6, 3)  # penalise but don't exclude

        options.append({
            "slug":        slug,
            "label":       profile.get("label", slug),
            "description": profile.get("description", ""),
            "difficulty":  profile.get("difficulty", "intermediate"),
            "audience":    profile.get("audience", "mixed"),
            "tech_ideal":  profile.get("tech_ideal", 0.5),
            "icon":        profile.get("icon", "🎯"),
            "color":       profile.get("color", "#8b949e"),
            "fit_score":   fit_score,
            "in_range":    in_range,
        })

    options.sort(key=lambda x: -x["fit_score"])
    best = options[0] if options else None

    tech_pct = round(tech_ratio * 100)
    if tech_ratio >= 0.70:
        profile_label = "highly technical"
    elif tech_ratio >= 0.45:
        profile_label = "mixed technical / operational"
    else:
        profile_label = "non-technical / leadership"

    reasoning = (
        f"{n} players — {profile_label} team ({tech_pct}% weighted technical score). "
    )
    if best:
        reasoning += (
            f"Best match: {best['label']} "
            f"(fit {round(best['fit_score']*100)}%, audience: {best['audience']})."
        )

    return {
        "tech_ratio":        round(tech_ratio, 3),
        "role_breakdown":    role_counts,
        "total_players":     n,
        "recommended_slug":  best["slug"] if best else None,
        "recommended_label": best["label"] if best else "—",
        "confidence":        best["fit_score"] if best else 0.0,
        "reasoning":         reasoning,
        "all_options":       options,
    }


# ---------------------------------------------------------------------------
# Player move within formation plan
# ---------------------------------------------------------------------------

def move_player_in_plan(
    session_id: int,
    registration_id: int,
    target_team_slot: int,
    target_team_name: str,
) -> dict:
    """Move a player to a different team slot in the formation plan.

    Returns the updated formation as {teams, num_teams, total_players}.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT plan_id FROM ttx_formation_plan WHERE session_id=? AND registration_id=?",
        (session_id, registration_id),
    ).fetchone()
    if not row:
        raise ValueError(f"Registration {registration_id} not in session {session_id} plan")

    conn.execute(
        "UPDATE ttx_formation_plan SET team_slot=?, team_name=?, confirmed=0 WHERE plan_id=?",
        (target_team_slot, target_team_name, row["plan_id"] if hasattr(row, "keys") else row[0]),
    )
    conn.commit()

    # Return rebuilt team structure
    plan = get_formation_plan(session_id)
    teams_map: dict[int, dict] = {}
    for p in plan:
        slot = p["team_slot"]
        if slot not in teams_map:
            teams_map[slot] = {"team_slot": slot, "team_name": p["team_name"], "members": []}
        teams_map[slot]["members"].append({
            "registration_id": p["registration_id"],
            "player_name":     p["player_name"],
            "role_id":         p["matched_role_id"],
            "role_label":      p["matched_role_label"],
        })
    teams = sorted(teams_map.values(), key=lambda t: t["team_slot"])
    return {"teams": teams, "num_teams": len(teams), "total_players": len(plan)}
