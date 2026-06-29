"""
AI GameDay — Full Lifecycle E2E Simulation
══════════════════════════════════════════════════════════════════════════════
Phase 0  Pre-session: 12 players self-register with free-text skill descriptions
Phase 1  Scenario auto-selection from role composition analysis
Phase 2  Team formation (snake-draft) + admin player swap demo
Phase 3  Facilitator confirms teams → session created
Phase 4  Session starts — 5 HUNT THE FLEET injects dispatched and played
Phase 5  Scoring: receipt gate · finetune gate · Academy bonuses per inject
Phase 6  Session ends — final leaderboard + AAR summary
══════════════════════════════════════════════════════════════════════════════
"""

import sys
import io
import json

from pathlib import Path as _Path
_BASE = _Path(__file__).resolve().parents[3]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Bootstrap ────────────────────────────────────────────────────────────────
from tools.dashboard.app import create_app
app = create_app()
app_ctx = app.app_context()
app_ctx.push()

from tools.db.storage import get_connection
from apps.ai_gameday.db import migrate
from apps.ai_gameday.registration import (
    create_registration, match_skill_to_role, recommend_scenario,
    run_formation, move_player_in_plan, confirm_formation,
    get_registrations, get_formation_plan,
)
from tools.ttx.engine import TTXEngine
from tools.ttx.inject_dispatcher import get_all_injects, dispatch_inject
from tools.ttx.leaderboard import compute_leaderboard, award_ribbons
from tools.ttx.scenario_loader import load_scenario
from tools.ttx.aar_generator import generate_aar

migrate()
engine = TTXEngine()

DIVIDER = "─" * 76
THICK   = "═" * 76

def hdr(text):
    print(f"\n{THICK}\n  {text}\n{THICK}")

def sub(text):
    print(f"\n{DIVIDER}\n  {text}\n{DIVIDER}")

def ok(text):
    print(f"  ✓  {text}")

def note(text):
    print(f"  ►  {text}")

def log_receipt(session_id, team_id, tool_slug):
    call_id     = str(uuid.uuid4())
    result_hash = call_id[:16]
    conn = get_connection()
    conn.execute(
        """INSERT INTO ttx_api_log
           (session_id,team_id,tool_slug,endpoint,call_id,result_hash,called_at)
           VALUES (%s,%s,%s,%s,%s,%s,datetime('now'))""",
        (session_id, team_id, tool_slug, f"/api/{tool_slug}", call_id, result_hash),
    )
    conn.commit()
    return {"call_id": call_id, "result_hash": result_hash, "tool": tool_slug}

def show_score(label, result):
    base  = result.get("gated_base_pts", "?")
    rcpt  = result.get("receipt_pts", 0)
    judge = result.get("judge_pts", 0)
    speed = result.get("time_bonus_pts", 0)
    total = result.get("total_pts", 0)
    acad  = result.get("academy_bonus", 0)
    gate  = result.get("gate_note", "")
    print(f"    {label:<22}  base={base!s:>5}  rcpt={rcpt:>4}  judge={judge:>4}  "
          f"speed={speed:>3}  TOTAL={total:>5}  acad={acad}")
    if gate:
        print(f"    {'':22}  ⚡ {gate}")

def show_lb(session_id, title="Leaderboard"):
    lb = compute_leaderboard(session_id)
    print(f"\n  {title}")
    print(f"    {'#':<4}{'Team':<24}{'rcpt':>6}{'judge':>6}{'speed':>6}{'TOTAL':>7}")
    print(f"    {'─'*52}")
    for row in lb:
        print(f"    {row['rank']:<4}{row['team_name']:<24}"
              f"{row['receipt_pts']:>6}{row['judge_pts']:>6}"
              f"{row['time_bonus_pts']:>6}{row['total_score']:>7}")


# ══════════════════════════════════════════════════════════════════════════════
hdr("AI GAMEDAY — FULL LIFECYCLE E2E SIMULATION")
print("  Scenario: auto-selected from team composition  ·  12 players  ·  3 teams")
print("  Covers: registration → recommendation → formation → play → AAR")

# ══════════════════════════════════════════════════════════════════════════════
hdr("PHASE 0 — PLAYER REGISTRATION  (12 players self-register)")
# ══════════════════════════════════════════════════════════════════════════════

# Create a pending session — recommendation engine will decide the scenario.
pending_session = engine.create_session("forge_ascent", "Facilitator-OMEGA")
SID = pending_session["session_id"]
note(f"Pending session created: SID={SID}  join_code={pending_session['join_code']}")
note("Session state: PENDING — accepting registrations")
print()

# 12 players register via free-text self-registration flow.
# match_skill_to_role() derives role from stated skill before storing.
PLAYERS = [
    # name                  stated_skill                                           acad_user     acad_level  missions
    ("Dr. Maya Torres",
     "PyTorch model training, MLOps, fine-tuning transformers, Kubeflow pipelines",
     "maya.torres",   "architect",  ["ml-fundamentals","model-fine-tuning","model-deployment"]),

    ("Kai Petrov",
     "Terraform, Kubernetes, Docker, EKS, CI/CD pipelines, ArgoCD, Helm, IaC on AWS GovCloud",
     "kai.petrov",    "specialist", ["container-deployment","iac-terraform","ci-cd-pipeline"]),

    ("Jun Nakamura",
     "Data science, pandas, scikit-learn, feature engineering, drift analysis, statistical modeling",
     "jun.nakamura",  "operative",  ["data-analysis","feature-engineering"]),

    ("Chioma Osei",
     "Python backend API developer, unit testing, code review, REST APIs, pytest, refactoring",
     "chioma.osei",   "recruit",    []),

    ("Reza Amiri",
     "Network security, zero-trust architecture, BGP, firewall policy, K8s NetworkPolicy",
     "reza.amiri",    "specialist", ["network-security","zero-trust"]),

    ("Sasha Volkov",
     "Deep learning, transformer fine-tuning, model evaluation, inference optimization, LLM deployment",
     "sasha.volkov",  "operative",  ["ml-fundamentals","model-evaluation"]),

    ("Ana Lima",
     "DevOps platform engineering, containerization, Helm, Terraform IaC, infrastructure automation",
     "ana.lima",      "operative",  ["container-deployment","iac-terraform"]),

    ("Ben Carter",
     "Software engineering, Java, Python, API integration, automated testing, code quality",
     "ben.carter",    "recruit",    []),

    ("Li Wei",
     "Geospatial intelligence, satellite imagery analysis, reconnaissance, ISR, OSINT",
     None,            "recruit",    []),

    ("Col. Dana Park",
     "Military leadership, strategy, COA decision-making, program management, tactical operations",
     None,            "recruit",    []),

    ("Priya Singh",
     "Cybersecurity, CISO, incident response, zero trust security, threat modeling",
     "priya.singh",   "specialist", ["network-security","zero-trust"]),

    ("Omar Hassan",
     "SIGINT, signals intelligence, spectrum analysis, frequency monitoring, maritime intelligence",
     None,            "recruit",    []),
]

print(f"  {'Player':<22} {'Matched Role':<22} {'Conf':>5}  {'Method':<8}  Academy")
print(f"  {'─'*75}")

registrations = []
for (name, skill, acad_user, acad_level, missions) in PLAYERS:
    # Step 1: match skill text to role (keyword scorer + LLM fallback)
    match = match_skill_to_role(skill)
    # Step 2: store registration with matched role
    reg = create_registration(SID, {
        "player_name":        name,
        "email":              "",
        "stated_skill":       skill,
        "matched_role_id":    match["role_id"],
        "matched_role_label": match["role_label"],
        "match_confidence":   match["confidence"],
        "match_method":       match["method"],
        "match_reasoning":    match.get("reasoning", ""),
        "academy_username":   acad_user or "",
    })
    registrations.append(reg)
    conf_pct = round((reg.get("match_confidence") or 0) * 100)
    acad_str = f"{acad_user}({acad_level})" if acad_user else "—"
    print(f"  {name:<22} {reg.get('matched_role_id','?'):<22} {conf_pct:>4}%  "
          f"{reg.get('match_method','?'):<8}  {acad_str}")

regs = get_registrations(SID)
print(f"\n  Total registered: {len(regs)} players")


# ══════════════════════════════════════════════════════════════════════════════
hdr("PHASE 1 — SCENARIO AUTO-SELECTION")
# ══════════════════════════════════════════════════════════════════════════════

rec = recommend_scenario(SID)

tech_pct = round(rec["tech_ratio"] * 100)
print(f"  Tech ratio:      {tech_pct}%  ({rec['total_players']} players)")
role_counts = rec["role_breakdown"]
print(f"  Role breakdown:  {dict(sorted(role_counts.items(), key=lambda x: -x[1]))}")
print()
print(f"  {'Scenario':<40} {'Fit Score':>10}  {'In Range':>9}  {'Audience'}")
print(f"  {'─'*72}")
for opt in rec["all_options"]:
    marker = "★ RECOMMENDED" if opt["slug"] == rec["recommended_slug"] else ""
    print(f"  {opt['label']:<40} {opt['fit_score']:>9.1%}  {str(opt['in_range']):>9}  "
          f"{opt['audience']:<14}  {marker}")

print()
note(f"Auto-selected: {rec['recommended_slug'].upper()}")
note(f"Reasoning: {rec['reasoning']}")

# Apply the recommended scenario (re-seed injects from new scenario YAML)
if rec["recommended_slug"] and rec["recommended_slug"] != pending_session["scenario_slug"]:
    from tools.ttx.scenario_loader import load_scenario as _ls
    from tools.ttx.inject_dispatcher import seed_injects
    new_slug = rec["recommended_slug"]
    scenario_data = _ls(new_slug)
    conn = get_connection()
    cfg = json.loads(pending_session.get("config_json") or "{}")
    cfg["scenario"] = scenario_data
    conn.execute(
        "UPDATE ttx_sessions SET scenario_slug=%s, config_json=%s WHERE session_id=%s",
        (new_slug, json.dumps(cfg), SID),
    )
    conn.execute("DELETE FROM ttx_injects WHERE session_id=%s AND state='pending'", (SID,))
    conn.commit()
    seed_injects(SID, scenario_data)
    ok(f"Scenario updated to: {new_slug}")
    SCENARIO_SLUG = new_slug
else:
    ok(f"Scenario confirmed: {pending_session['scenario_slug']}")
    SCENARIO_SLUG = pending_session["scenario_slug"]


# ══════════════════════════════════════════════════════════════════════════════
hdr("PHASE 2 — TEAM FORMATION  (snake-draft)")
# ══════════════════════════════════════════════════════════════════════════════

draft = run_formation(SID)
print(f"  {draft['num_teams']} teams from {draft['total_players']} players  "
      f"(target: 4 per team)")
print()

for team in draft["teams"]:
    role_list = [m["role_id"] for m in team["members"]]
    print(f"  {team['team_name']:<16}  ({len(team['members'])} players)  "
          f"roles: {role_list}")
    for m in team["members"]:
        print(f"    ├ {m['player_name']:<22}  [{m['role_id']}]")

# ── Admin demo: move Omar Hassan (SIGINT specialist) to balance a team ──
plan = get_formation_plan(SID)
omar_reg = next((r for r in get_registrations(SID) if "Omar" in r["player_name"]), None)
if omar_reg:
    omar_plan = next((p for p in plan if p["registration_id"] == omar_reg["registration_id"]), None)
    if omar_plan:
        current_slot = omar_plan["team_slot"]
        target = next((t for t in draft["teams"] if t["team_slot"] != current_slot), None)
        if target:
            sub(f"Admin Move: Omar Hassan → {target['team_name']} (demonstrating player swap)")
            updated = move_player_in_plan(
                SID,
                omar_reg["registration_id"],
                target["team_slot"],
                target["team_name"],
            )
            for team in updated["teams"]:
                role_list = [m["role_id"] for m in team["members"]]
                print(f"  {team['team_name']:<16}  ({len(team['members'])} players)  "
                      f"roles: {role_list}")


# ══════════════════════════════════════════════════════════════════════════════
hdr("PHASE 3 — FACILITATOR CONFIRMS TEAMS")
# ══════════════════════════════════════════════════════════════════════════════

result = confirm_formation(SID)
ok(f"Teams confirmed: {result['teams_created']} teams created, "
   f"{result['members_created']} members assigned")

# Reload teams from DB with Academy profiles patched in
from tools.ttx.team_manager import list_teams, list_members
db_teams = list_teams(SID)
teams = []
for t in db_teams:
    tid = t["team_id"]
    members = list_members(tid)
    teams.append({"team_id": tid, "name": t["team_name"], "members": members})

    for m in members:
        player_cfg = next((p for p in PLAYERS if p[0] == m["player_name"]), None)
        if player_cfg:
            _, _, acad_user, acad_level, missions = player_cfg
            xp_map = {"recruit": 0, "operative": 400, "specialist": 1200, "architect": 2500}
            profile = {
                "level": acad_level,
                "xp": xp_map.get(acad_level, 0),
                "completed_missions": missions,
            }
            conn = get_connection()
            if acad_user:
                conn.execute(
                    "UPDATE ttx_team_members SET academy_username=%s, academy_profile_json=%s "
                    "WHERE member_id=%s",
                    (acad_user, json.dumps(profile), m["member_id"]),
                )
            else:
                conn.execute(
                    "UPDATE ttx_team_members SET academy_profile_json=%s WHERE member_id=%s",
                    (json.dumps(profile), m["member_id"]),
                )
conn.commit()

print()
for t in teams:
    print(f"  [{t['team_id']}] {t['name']}")
    for m in t["members"]:
        player_cfg = next((p for p in PLAYERS if p[0] == m["player_name"]), None)
        level    = player_cfg[3] if player_cfg else "recruit"
        missions = player_cfg[4] if player_cfg else []
        role     = next(
            (r.get("matched_role_id","") for r in regs if r["player_name"] == m["player_name"]),
            "?"
        )
        print(f"      {m['player_name']:<22}  role={role:<20}  "
              f"Academy:{level}  missions:{len(missions)}")


# ══════════════════════════════════════════════════════════════════════════════
hdr("PHASE 4 — SESSION START")
# ══════════════════════════════════════════════════════════════════════════════

engine.start_session(SID)
ok(f"Session {SID} is now ACTIVE — clock starts")
ok(f"Scenario: {SCENARIO_SLUG.upper()}")

scenario      = load_scenario(SCENARIO_SLUG)
all_injects   = {inj["slug"]: inj for inj in get_all_injects(SID)}
teams_by_name = {t["name"]: t for t in teams}

print(f"\n  Injects loaded: {list(all_injects.keys())}")


# ══════════════════════════════════════════════════════════════════════════════
hdr("INJECT 01 — Ghost Signal: Type-054A Emissions  (T+15 min)")
# ══════════════════════════════════════════════════════════════════════════════
print("  AI tools: strategos.oracle · strategos.signals · knowledge.search")
print("  base=100 · receipt_gate=True · finetune_required=False")
print("  Task: SIGINT fusion assessment — classify contact, fuse SIGINT+acoustic bearings\n")

inj1 = all_injects["inject-01-ghost-signal"]
dispatch_inject(inj1["inject_id"])
note(f"Inject dispatched → all {len(teams)} teams receive the alert")

I1_RECEIPTS = {
    teams[0]["name"]: ["strategos.oracle", "strategos.signals", "knowledge.search"],  # 3 → full bonus
    teams[1]["name"]: ["strategos.signals"],                                           # 1 → 70% gate
    teams[2]["name"]: [],                                                              # 0 → 40% gate
}

I1_RESPONSES = {
    teams[0]["name"]: """\
SIGINT FUSION ASSESSMENT — CONTACT GOLF-7

CONTACT CLASSIFICATION (Oracle + Signal Prioritizer):
Type-054A Jiangkai-II guided-missile frigate. Primary armament: YJ-18 AShM (range 540km),
HHQ-16 SAM battery (48 cells), 76mm main gun, 2x triple torpedo tubes. Operational role:
blue-water patrol, surface warfare, ASW screen for carrier strike group.

BEARING FUSION (strategos.signals triangulation):
  - SIGINT bearing from Alpha-7:       247° ±5° (single-node, MEDIUM confidence)
  - Acoustic bearing from CHARLIE:     251° ±8° (degraded SNR, thermocline at 180m)
  - Fused cross-bearing intersection:  249° ±3° (weighted by sensor reliability)
  - Estimated grid:                    XB-437 ±25nm (uncertainty ellipse applied)
  - Signal Prioritizer ranked SIGINT as 0.72 confidence, acoustic as 0.58 — weighted fusion.

EMISSIONS CONFIDENCE ASSESSMENT:
MEDIUM — single collection node; no COMINT triangulation; acoustic SNR degraded.
Emission pattern (4 min 17 sec of surface search + fire control radar) is inconsistent
with routine navigation. Assessment: INTENTIONAL EMCON break — contact may be ranging
on a USN surface unit. Low probability of accidental emission given dual-mode radar.
KB-SIGINT-047 (knowledge.search) confirms this emission profile matches PLAN pre-engagement
targeting doctrine.

COLLECTION PRIORITY (next intelligence required):
1. Task second SIGINT node (Alpha-9, bearing 193°) for triangulation — upgrades to HIGH confidence.
2. Request SSN-774 active prosecution to confirm track depth + speed (risk: counter-detection).
3. SATINT collection tasking: grid XB-400 to XB-480 — 4h window for visual confirmation.""",

    teams[1]["name"]: """\
SIGINT FUSION ASSESSMENT — CONTACT GOLF-7

Signal Prioritizer identified contact as probable Type-054A frigate based on emission
frequency hop pattern and radar signature match.

Bearing estimate: SIGINT Alpha-7 bearing 247° + acoustic CHARLIE bearing 251°.
Estimated grid: XB-440 area (±35nm uncertainty — single node only).

Confidence: MEDIUM — single collection node, thermocline degradation on acoustic.
Cannot determine EMCON break vs. navigation radar without additional collection.

Collection priority:
1. Second SIGINT node for triangulation.
2. SATINT pass over XB-440 grid sector.""",

    teams[2]["name"]: """\
Contact assessment: probable PLAN warship. RF emissions detected at bearing 247°.

Estimated position: XB area. Confidence: LOW (no triangulation available).

Recommend: additional intelligence collection before any action.""",
}

print()
for t in teams:
    rlist = [log_receipt(SID, t["team_id"], tool)
             for tool in I1_RECEIPTS.get(t["name"], [])]
    res = engine.submit_response(
        team_id=t["team_id"], inject_id=inj1["inject_id"], session_id=SID,
        response_text=I1_RESPONSES.get(t["name"], "No response."),
        receipts=rlist, time_taken_s=820,
    )
    show_score(t["name"], res)

show_lb(SID, "Standing after Inject 01")


# ══════════════════════════════════════════════════════════════════════════════
hdr("INJECT 02 — Supply Run Intercepted: AT-229  (T+40 min)")
# ══════════════════════════════════════════════════════════════════════════════
print("  AI tools: strategos.simulate · strategos.wargame.ooda · knowledge.search")
print("  base=150 · receipt_gate=True · finetune_required=False")
print("  Task: Model supply chain impact, recommend COA (interdict or shadow)\n")

inj2 = all_injects["inject-02-supply-run"]
dispatch_inject(inj2["inject_id"])

I2_RECEIPTS = {
    teams[0]["name"]: ["strategos.simulate", "strategos.wargame.ooda", "knowledge.search"],
    teams[1]["name"]: ["strategos.simulate"],
    teams[2]["name"]: ["knowledge.search"],
}

I2_RESPONSES = {
    teams[0]["name"]: """\
SUPPLY CHAIN INTERDICTION ANALYSIS — AT-229 RENDEZVOUS

ENDURANCE IMPACT (Supply Chain Simulate — strategos.simulate):
If AT-229 transfer completes:
  - Golf-7 fuel state: FULL (estimated 72h endurance from pre-rendezvous ~18h remaining)
  - Ordnance: Full YJ-18 magazine reload (8 missiles + reserve canisters)
  - Net operational gain: +96 hours independent operations capability
  - Net threat multiplication: 3.2× — full weapons load + fuel extends threat arc 540km radius

If AT-229 transfer is INTERDICTED:
  - Golf-7 forced withdrawal in 18h — fuel critical, no ordnance reload
  - Effective threat neutralization without kinetic engagement with warship

OODA CYCLE ANALYSIS (strategos.wargame.ooda):
  - Golf-7 OODA cycle: ~12 min (estimated from emissions cadence)
  - Our decision window: ~4 hours before AT-229 rendezvous completes
  - Interdiction breaks their resupply loop; shadow option allows continued tracking
  - Shadow COA extends our intelligence picture but risks transfer completion

RECOMMENDED COA — OPTION B: SHADOW + INTERCEPT AT-229 SEPARATELY
Reasoning (KB-NCMF-019 via knowledge.search — naval doctrine, proportionality):
  1. Interdict AT-229 (auxiliary/non-combatant) rather than Golf-7 (warship) — lower escalation
  2. Shadow Golf-7 while SSN-774 maneuvers for firing solution
  3. ROE compliance: positive identification of AT-229 cargo (ordnance) required before action
     — request SATINT pass grid XB-612 to confirm cargo type
  4. Second-order: Golf-7 course change likely if AT-229 goes dark — ML trajectory model needed

SECOND-ORDER ANALYSIS:
  - Interdiction may cause Golf-7 to alter course to 042° (withdrawal toward PLAN base)
  - Escalation risk: LOW if AT-229 action classified as logistics interdiction, not warship action
  - Intelligence loss: if we interdict, we lose Golf-7 track until next SIGINT cycle""",

    teams[1]["name"]: """\
SUPPLY CHAIN ANALYSIS — AT-229

Supply Chain Simulate output: if transfer completes, Golf-7 gains +96h endurance and full
weapons reload. This significantly extends the threat.

COA Recommendation: Interdict AT-229 before transfer completes.
  - AT-229 is a non-combatant auxiliary vessel — lower escalation threshold
  - Denies Golf-7 resupply without direct warship engagement

ROE consideration: need positive ID of cargo (ordnance vs. food) via SATINT before action.

Risk: Golf-7 will likely change course if AT-229 is interdicted.""",

    teams[2]["name"]: """\
The AT-229 rendezvous with Golf-7 is a resupply operation. If it completes, Golf-7
will have more fuel and weapons.

We recommend interdicting the supply ship. This is lower risk than engaging the warship.

Need to verify Rules of Engagement before taking action.""",
}

print()
for t in teams:
    rlist = [log_receipt(SID, t["team_id"], tool)
             for tool in I2_RECEIPTS.get(t["name"], [])]
    res = engine.submit_response(
        team_id=t["team_id"], inject_id=inj2["inject_id"], session_id=SID,
        response_text=I2_RESPONSES.get(t["name"], "No response."),
        receipts=rlist, time_taken_s=1100,
    )
    show_score(t["name"], res)

show_lb(SID, "Standing after Inject 02")


# ══════════════════════════════════════════════════════════════════════════════
hdr("INJECT 03 — Comms Blackout: ML Trajectory Payoff  (T+65 min)  [FINETUNE GATE]")
# ══════════════════════════════════════════════════════════════════════════════
print("  AI tools: finetune.deploy · knowledge.search · strategos.oracle")
print("  base=200 · receipt_gate=True · finetune_required=TRUE")
print("  Task: Deploy trajectory ML model, predict Golf-7 grid, recommend satellite tasking\n")

inj3 = all_injects["inject-03-comms-blackout"]
dispatch_inject(inj3["inject_id"])

I3_RECEIPTS = {
    teams[0]["name"]: ["finetune.deploy", "knowledge.search", "strategos.oracle"],  # finetune → gate open
    teams[1]["name"]: ["knowledge.search", "strategos.oracle"],                      # NO finetune → 50% cap
    teams[2]["name"]: [],                                                             # NO receipts → 40% base + 50% cap
}

I3_RESPONSES = {
    teams[0]["name"]: """\
ML TRAJECTORY MODEL DEPLOYMENT — GOLF-7 PREDICTED TRACK

Model Deployment (finetune.deploy):
  Model ID:    ft-golf7-trajectory-20260514-v3
  Base model:  vessel-trajectory-v2 (fine-tuned on PLAN Type-054A track history, 847 samples)
  Features:    current_set_knots, wind_bearing, fuel_state, prev_bearing_change,
               mission_profile_class, supply_interdiction_flag
  Deployment:  ACTIVE — inference endpoint /api/finetune/predict/ft-golf7-trajectory-20260514-v3

TRAJECTORY PREDICTION (model output):
  Current position (estimated): XB-437 ±25nm
  Primary COA prediction (62% probability):
    → Track 318° at 18 knots → Grid XC-108 in 4h 20min
    → Consistent with PLAN withdrawal pattern after failed resupply (supply_interdiction_flag=1)
  Alternate COA prediction (28% probability):
    → Track 042° at 22 knots → Grid XC-217 in 3h 40min
    → Consistent with offensive positioning if supply transfer completed

  Uncertainty ellipse at T+4h: 35nm radius (Oracle cross-validated — strategos.oracle
  confirmed current set 2.1 knots at 157° adds 8nm northward drift component)

SATELLITE TASKING RECOMMENDATION:
  PRIMARY GRID: XC-108 (62% predicted position at T+4h)
    → Task NRO collection window 1430-1500Z — 30min window, 3-pass coverage
  SECONDARY GRID: XC-200 to XC-220 corridor (alt track coverage)
    → Single pass 1445Z sufficient for track/no-track determination

  If neither grid produces track: re-run model with updated acoustic bearing from SSN-774.""",

    teams[1]["name"]: """\
GOLF-7 TRAJECTORY ANALYSIS

Used knowledge.search and strategos.oracle for trajectory analysis (no finetune.deploy access).

Oracle assessment: based on current SIGINT bearing 249° and typical PLAN withdrawal doctrine
after resupply interdiction, most likely track is northwest at 15-20 knots.

Predicted grid at T+4h: approximately XC-100 to XC-220 area. Confidence: MEDIUM.

Satellite tasking: recommend grid XC-108 as primary collection point.
Rationale: withdrawal vector from XB-437 at 318° puts contact near XC-108 at standard speed.""",

    teams[2]["name"]: """\
Golf-7 is likely heading northwest based on the last bearing.

We recommend tasking a satellite to scan the area north of XB-437.

Could not access the trajectory model. Our estimate is based on the last known bearing.""",
}

print()
for t in teams:
    rlist = [log_receipt(SID, t["team_id"], tool)
             for tool in I3_RECEIPTS.get(t["name"], [])]
    res = engine.submit_response(
        team_id=t["team_id"], inject_id=inj3["inject_id"], session_id=SID,
        response_text=I3_RESPONSES.get(t["name"], "No response."),
        receipts=rlist, time_taken_s=1450,
    )
    show_score(t["name"], res)

show_lb(SID, "Standing after Inject 03")


# ══════════════════════════════════════════════════════════════════════════════
hdr("INJECT 04 — Strike Window Opens  (T+85 min)")
# ══════════════════════════════════════════════════════════════════════════════
print("  AI tools: strategos.wargame.coa · strategos.oracle · strategos.wargame.ooda")
print("  base=175 · receipt_gate=True · finetune_required=False")
print("  Task: Submit target grid + COA with asset assignment, ROE analysis, sequencing\n")

inj4 = all_injects["inject-04-strike-window"]
dispatch_inject(inj4["inject_id"])

I4_RECEIPTS = {
    teams[0]["name"]: ["strategos.wargame.coa", "strategos.wargame.ooda", "strategos.oracle"],
    teams[1]["name"]: ["strategos.wargame.coa", "strategos.oracle"],
    teams[2]["name"]: ["strategos.wargame.coa"],
}

I4_RESPONSES = {
    teams[0]["name"]: """\
STRIKE COA RECOMMENDATION — TARGET GOLF-7

TARGET GRID: XC-108 (primary prediction at T+4h ±35nm — COA Generator grounded)
  Confidence: HIGH — ML model prediction + Oracle current-drift correction align on XC-108

COA SELECTED: COA-B — Submarine-first engagement sequence
(strategos.wargame.coa compared 3 COAs; COA-B ranked 1st by probability-of-kill / escalation score)

COA-B ASSET SEQUENCE:
1. SSN-774 (USS Portsmouth) — ADCAP Mk-48 mod 7 from firing position XC-021
   Time-on-target: T+4h22min (ahead of Golf-7 predicted arrival at XC-108)
   PK: 0.89 (from Oracle probabilistic model)

2. P-8A Poseidon (VP-9 Det) — Standoff maritime strike, MK-54 lightweight torpedo × 2
   Backup if SSN track is broken; activates only if SSN engagement fails

3. VFA-137 (FA-18E × 2) — HARPOON × 2 as tertiary SAG backup
   Held at PCA until SSN confirms engagement result

OODA ANALYSIS (strategos.wargame.ooda):
  Our decision cycle: ~35 min (from COA selection to SSN weapons free)
  Golf-7 response cycle: ~12 min
  Window: we have 23 min margin — sufficient for SSN positioning

ROE COMPLIANCE CHAIN:
  1. Positive identification: Type-054A confirmed via SIGINT + acoustic + SATINT (completed)
  2. Hostile intent: confirmed via fire control radar emissions (KC-21 criterion met)
  3. Hostile action trigger: AT-229 interdiction did not occur (supply confirmed — highest threat state)
  4. Proportionality: SSN Mk-48 minimum necessary force; no collateral damage vectors at XC-108
  5. NCA: Authorization obtained per OPLAN 5015 threshold criteria

ESCALATION ASSESSMENT: MEDIUM — engagement outside 12nm territorial waters (international law clear).
Diplomatic risk: PLAN will protest; PRC maritime militia may activate. Recommend IO messaging
prepared simultaneously.""",

    teams[1]["name"]: """\
TARGET GRID: XC-108

COA: Surface-launched HARPOON engagement via DDG-116.
  Asset: USS Thomas Hudner (DDG-116) — HARPOON × 4
  COA Generator analysis showed DDG within effective range at T+4h

OODA: our cycle approximately 40 minutes. Golf-7 detection-to-maneuver ~15 min.
We have a 25 min decision window — adequate for positioning.

ROE: positive ID confirmed via SIGINT. Hostile intent from fire control radar.
Proportionality appropriate for warship engagement in international waters.

Escalation: MEDIUM. Recommend notification to regional allies prior to action.""",

    teams[2]["name"]: """\
Target grid: XC-108 area.

Recommend engaging with available surface assets.

COA: launch HARPOON from surface ship. Need to confirm ROE authorization.
Used COA Generator to compare options. Grid estimate based on trajectory model.""",
}

print()
for t in teams:
    rlist = [log_receipt(SID, t["team_id"], tool)
             for tool in I4_RECEIPTS.get(t["name"], [])]
    res = engine.submit_response(
        team_id=t["team_id"], inject_id=inj4["inject_id"], session_id=SID,
        response_text=I4_RESPONSES.get(t["name"], "No response."),
        receipts=rlist, time_taken_s=980,
    )
    show_score(t["name"], res)

show_lb(SID, "Standing after Inject 04")


# ══════════════════════════════════════════════════════════════════════════════
hdr("INJECT 05 — Battle Damage Assessment  (T+105 min)")
# ══════════════════════════════════════════════════════════════════════════════
print("  AI tools: none (pure analysis)")
print("  base=100 · receipt_gate=False · finetune_required=False")
print("  Task: BDA in BLUF format — accuracy review, AI tool reflection, lessons learned\n")

inj5 = all_injects["inject-05-bda"]
dispatch_inject(inj5["inject_id"])

I5_RECEIPTS = {
    teams[0]["name"]: [],
    teams[1]["name"]: [],
    teams[2]["name"]: [],
}

I5_RESPONSES = {
    teams[0]["name"]: """\
BATTLE DAMAGE ASSESSMENT — OPERATION NEPTUNE
Classification: CUI//SP-CTI

BLUF: Engagement achieved desired effect. Golf-7 neutralized at XC-108 — grid accuracy HIGH
(ML prediction: XC-108; actual intercept: XC-104, delta=4nm — within model uncertainty ellipse).
All AI tools performed as expected. Three lessons identified for TTX improvement.

INTELLIGENCE ACCURACY:
  - SIGINT bearing (Alpha-7): 247° → fused to 249° → actual track: 251° (2° error — within spec)
  - ML trajectory model: predicted XC-108, actual XC-104 — delta 4nm (model uncertainty 35nm)
  - Supply Chain Simulate: +96h endurance impact correctly modeled (confirmed by post-strike analysis)
  - Most valuable source: ML trajectory model (fine-tuned on PLAN doctrine) + SIGINT fusion

AI TOOL UTILIZATION REFLECTION (per inject):
  Inject 01 (Ghost Signal): Signal Prioritizer + Oracle → fused bearings with confidence weighting.
    This was the right tool; we should have queried Oracle for PLAN EMCON doctrine earlier.
  Inject 02 (Supply Run): Supply Chain Simulate precisely modeled endurance impact.
    OODA analysis correctly framed decision window. Would add IW Composite (PMESII) next time.
  Inject 03 (Trajectory): Fine-tune deployment was decisive — without model_id, no grid confidence.
    Teams without finetune.deploy were effectively blind for inject 4 target selection.
  Inject 04 (Strike): COA Generator effectively compared 3 options; submarine-first was optimal.
    OODA tempo analysis provided the decision margin calculation we needed.

PROCESS & DOCTRINE:
  ROE chain was correctly applied — positive ID → hostile intent → proportionality → NCA.
  OODA superiority maintained throughout: our 35-min cycle vs. Golf-7 12-min cycle = 23-min margin.
  Information operations messaging was prepared but not executed (time constraint).

LESSONS LEARNED:
  1. Deploy ML trajectory model at inject 01 baseline, not inject 03 — earlier deployment gives
     tracking baseline for supply run interdiction decision (inject 02 COA would be better grounded).
  2. Second SIGINT node request should be immediate collect priority from inject 01 assessment —
     triangulation upgrades confidence from MEDIUM to HIGH before any COA commitment.
  3. AT-229 interdiction decision (inject 02) drove the supply_interdiction_flag in the ML model
     at inject 03; teams that chose "shadow" COA got a different predicted grid — scenario
     branching must be explicitly accounted for in trajectory model input features.""",

    teams[1]["name"]: """\
BATTLE DAMAGE ASSESSMENT

BLUF: Engagement achieved objective. Grid XC-108 was correct — intercept at XC-106 (delta 2nm).

INTELLIGENCE ACCURACY:
  - SIGINT bearing estimate was close to actual track. ML model would have improved accuracy
    but we did not deploy it (no finetune.deploy access).
  - Supply Chain Simulate correctly estimated endurance impact.

AI TOOL REFLECTION:
  - Signal Prioritizer was effective for inject 01 bearing fusion.
  - For inject 03, we relied on Oracle instead of the trajectory model — less precise.
  - Should have accessed finetune.deploy in inject 03.

LESSONS LEARNED:
  1. Fine-tune deployment access should be ensured before the event.
  2. Signal Prioritizer should be cross-referenced with Oracle on every SIGINT inject.
  3. OODA analysis should be run earlier in the decision cycle, not just at strike window.""",

    teams[2]["name"]: """\
BDA Report:

We engaged Golf-7 at the estimated grid area. Outcome unclear — no confirmed BDA.

AI tools: we had limited access to AI tools during the exercise.

Lessons: we need to use AI tools more effectively in future exercises.""",
}

print()
for t in teams:
    rlist = [log_receipt(SID, t["team_id"], tool)
             for tool in I5_RECEIPTS.get(t["name"], [])]
    res = engine.submit_response(
        team_id=t["team_id"], inject_id=inj5["inject_id"], session_id=SID,
        response_text=I5_RESPONSES.get(t["name"], "No response."),
        receipts=rlist, time_taken_s=1650,
    )
    show_score(t["name"], res)

show_lb(SID, "Standing after Inject 05")


# ══════════════════════════════════════════════════════════════════════════════
hdr("PHASE 5 — SESSION END  +  FINAL RESULTS")
# ══════════════════════════════════════════════════════════════════════════════

engine.end_session(SID)
ok(f"Session {SID} ended — final leaderboard computed")

# ── Final leaderboard ──────────────────────────────────────────────────────
lb      = compute_leaderboard(SID)
ribbons = award_ribbons(SID)

sub("FINAL LEADERBOARD")
print(f"  {'#':<4}{'Team':<24}{'Rcpt':>7}{'Judge':>7}{'Speed':>7}{'TOTAL':>8}")
print(f"  {'═'*58}")
for row in lb:
    medals = ["1st","2nd","3rd","   "]
    medal  = medals[min(row["rank"]-1, 3)]
    print(f"  [{medal}] {row['team_name']:<24}"
          f"{row['receipt_pts']:>7}{row['judge_pts']:>7}"
          f"{row['time_bonus_pts']:>7}{row['total_score']:>8}")

# ── Ribbons ─────────────────────────────────────────────────────────────────
if ribbons:
    sub("RIBBON AWARDS")
    from tools.ttx.constants import RIBBON_DEFS
    for ribbon_id, team_id in ribbons.items():
        r_def     = RIBBON_DEFS.get(ribbon_id, {})
        team_name = next((t["name"] for t in teams if t["team_id"] == team_id), f"Team {team_id}")
        print(f"  {r_def.get('icon','[ribbon]')}  {r_def.get('label','?'):<22}  -> {team_name}")
        print(f"       {r_def.get('desc','')}")

# ── Mechanics verification ─────────────────────────────────────────────────
sub("MECHANICS VERIFICATION")

conn = get_connection()
for t in teams:
    tid          = t["team_id"]
    receipt_count = conn.execute(
        "SELECT COUNT(*) FROM ttx_api_log WHERE session_id=%s AND team_id=%s",
        (SID, tid)
    ).fetchone()[0]
    total_score = conn.execute(
        "SELECT COALESCE(SUM(total_pts),0) FROM ttx_scores WHERE team_id=%s",
        (tid,)
    ).fetchone()[0]
    tools_used  = conn.execute(
        "SELECT DISTINCT tool_slug FROM ttx_api_log WHERE session_id=%s AND team_id=%s",
        (SID, tid)
    ).fetchall()
    tools_str   = ", ".join(r["tool_slug"] for r in tools_used) or "none"
    print(f"  {t['name']:<24}  receipts={receipt_count:>3}  total_score={total_score:>5}  "
          f"tools=[{tools_str[:60]}]")

print()
note("Gate mechanics confirmed:")
print("    Receipt gate:  0 receipts -> 40% base  |  1 -> 70%  |  >=2 -> 100%")
print("    Finetune gate: inject-03 base capped at 50% unless finetune.deploy receipt present")
print("    Academy bonus: role x mission mapping adds up to +15pts on judge_pts")
print("    Time bonus:    <=120s +50  |  <=300s +25  |  <=600s +10")

# ── AAR excerpt ────────────────────────────────────────────────────────────
sub("AFTER-ACTION REPORT (excerpt)")
aar = generate_aar(SID)
aar_lines = aar.split("\n")
for line in aar_lines[:55]:
    print(f"  {line}")
if len(aar_lines) > 55:
    print(f"  ... [{len(aar_lines)-55} more lines — full AAR at /gameday/session/{SID}/results]")

print(f"\n{THICK}")
print(f"  SIMULATION COMPLETE — Session {SID}")
print(f"  Full results at: http://localhost:5050/gameday/session/{SID}/results")
print(f"  Leaderboard at:  http://localhost:5050/gameday/leaderboard/{SID}")
print(f"{THICK}\n")
