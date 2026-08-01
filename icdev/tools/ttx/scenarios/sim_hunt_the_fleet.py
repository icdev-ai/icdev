"""
Operation Neptune: Hunt the Fleet — Full Simulation
Demonstrates: receipt gate, consequence system, body variant, finetune gate, Academy bonuses.
"""


def main() -> None:
    """Run the demo simulation (writes to the DB and prints a report)."""
    import sys
    import io
    import uuid
    import json

    from pathlib import Path as _Path
    _BASE = _Path(__file__).resolve().parents[3]
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # Bootstrap app context so get_connection() points to the right DB
    from tools.dashboard.app import create_app
    create_app()

    from tools.db.storage import get_connection
    from apps.ai_gameday.db import migrate
    from tools.ttx.engine import TTXEngine
    from tools.ttx.inject_dispatcher import (
        get_all_injects, dispatch_inject, get_world_state
    )
    from tools.ttx.leaderboard import compute_leaderboard

    migrate()
    engine = TTXEngine()

    DIVIDER = "─" * 70
    THICK   = "═" * 70

    def hdr(text):
        print(f"\n{THICK}\n  {text}\n{THICK}")

    def sub(text):
        print(f"\n{DIVIDER}\n  {text}\n{DIVIDER}")

    def log_receipt(session_id, team_id, tool_slug):
        """Insert a real API log entry and return (call_id, result_hash) for receipt dict."""
        call_id = str(uuid.uuid4())
        result_hash = call_id[:16]
        conn = get_connection()
        conn.execute(
            """INSERT INTO ttx_api_log
               (session_id, team_id, tool_slug, endpoint, call_id, result_hash, called_at)
               VALUES (%s, %s, %s, %s, %s, %s, datetime('now'))""",
            (session_id, team_id, tool_slug, tool_slug, call_id, result_hash),
        )
        conn.commit()
        return {"call_id": call_id, "result_hash": result_hash, "tool": tool_slug}

    def show_score(label, result):
        gated = result.get("gated_base_pts", "?")
        rcpt  = result.get("receipt_pts", 0)
        judge = result.get("judge_pts", 0)
        speed = result.get("time_bonus_pts", 0)
        total = result.get("total_pts", 0)
        acad  = result.get("academy_bonus", 0)
        gate_note = result.get("gate_note", "")
        print(f"    {label:<22} base={gated:>4}  rcpt={rcpt:>4}  judge={judge:>4}  "
              f"speed={speed:>3}  TOTAL={total:>5}  acad_bonus={acad}")
        if gate_note:
            print(f"    {'':22} ⚡ {gate_note}")

    def show_lb(session_id):
        lb = compute_leaderboard(session_id)
        print(f"\n    {'#':<4} {'Team':<22} {'Receipts':>8} {'Judge':>6} {'Speed':>6} {'TOTAL':>7}")
        print(f"    {'-'*55}")
        for row in lb:
            print(f"    {row['rank']:<4} {row['team_name']:<22} {row['receipt_pts']:>8} "
                  f"{row['judge_pts']:>6} {row['time_bonus_pts']:>6} {row['total_score']:>7}")

    # ──────────────────────────────────────────────────────────────────────────────
    hdr("OPERATION NEPTUNE: HUNT THE FLEET — SIMULATION")
    print("  4 teams · 5 injects · Receipt gate · Consequence system · Academy bonuses")

    # ── Session setup ──────────────────────────────────────────────────────────────
    sub("Session Creation")
    session = engine.create_session("hunt_the_fleet", facilitator_name="Facilitator-SIM")
    SID = session["session_id"]
    print(f"  Session ID:   {SID}")
    print(f"  Scenario:     {session['scenario_slug']}")
    print(f"  Mode:         {session['session_mode']}  ({session['duration_minutes']} min)")
    print(f"  Join code:    {session['join_code']}")
    engine.start_session(SID)
    print("  Status:       ACTIVE")

    # ── Teams ──────────────────────────────────────────────────────────────────────
    sub("Team Registration")
    teams_cfg = [
        ("IRON SHARK",    "captain",         "Capt. Morgan",  ["ml-fundamentals","coa-decision-making"], "architect"),
        ("GHOST FLEET",   "data_scientist",  "Dr. Reyes",     ["ml-fundamentals","sigint-analysis"],     "specialist"),
        ("TRIDENT ACT.",  "signals_intel",   "Lt. Chen",      ["sigint-analysis"],                       "specialist"),
        ("DEEP SIX",      "imagery_analyst", "Petty Okafor",  [],                                        "operative"),
    ]

    teams = []
    for tname, role_id, pname, missions, level in teams_cfg:
        team = engine.create_team(SID, tname)
        tid = team["team_id"]
        cfg = json.loads(session.get("config_json") or "{}")
        roles = cfg.get("scenario", {}).get("roles", [])
        member = engine.join_team(tid, pname, role_id, roles)

        # Store Academy profile snapshot
        profile = {"level": level, "xp": {"operative":400,"specialist":1200,"architect":2500}[level],
                   "completed_missions": missions}
        conn = get_connection()
        conn.execute(
            "UPDATE ttx_team_members SET academy_username=%s, academy_profile_json=%s WHERE member_id=%s",
            (pname.lower().replace(" ","_"), json.dumps(profile), member["member_id"]),
        )
        conn.commit()

        teams.append({"team_id": tid, "name": tname, "role_id": role_id, "missions": missions, "level": level})
        print(f"  [{tid}] {tname:<22}  {role_id:<18}  {pname:<18}  Academy:{level} missions:{missions}")

    # ── Load all injects ────────────────────────────────────────────────────────────
    all_injects = {inj["slug"]: inj for inj in get_all_injects(SID)}

    def get_inject(slug):
        return all_injects[slug]

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 01 — Ghost Signal (T+15 min)")
    print("  AI tools: strategos.oracle, strategos.signals, knowledge.search")
    print("  Scoring:  base=100 · receipt_gate=True · finetune=False")

    inj1 = get_inject("inject-01-ghost-signal")
    dispatch_inject(inj1["inject_id"])
    print(f"\n  Dispatched: {inj1['inject_id'][:8]}...")

    # Simulate different receipt counts per team
    inj1_receipts = {
        "IRON SHARK":   ["strategos.oracle", "strategos.signals"],  # 2 receipts → full gate
        "GHOST FLEET":  ["strategos.signals"],                       # 1 receipt → 70% gate
        "TRIDENT ACT.": ["strategos.oracle", "strategos.signals", "knowledge.search"],  # 3 → full
        "DEEP SIX":     [],                                          # 0 → 40% penalty
    }
    inj1_responses = {
        "IRON SHARK":   "SIGINT FUSION ASSESSMENT: Oracle cross-referenced the 247° bearing from Alpha-7 against the PLAN Type-054A emission profile database. Signal Prioritizer identified YJ-18 targeting radar lock pattern. Triangulated with acoustic bearing 251° from Charlie array — estimated grid XB-441 ±20nm. Confidence: MEDIUM-HIGH due to thermocline masking acoustic SNR. Recommend tasking additional collection node Delta-3 for triangulation.",
        "GHOST FLEET":  "Ghost Signal analysis: Signal Prioritizer confirms emission consistent with Type-054A. Single bearing 247° from Alpha-7 — no triangulation possible. Estimated grid XB-441 ±40nm. Recommend EMCON break was accidental (burst duration 4min17sec exceeds PLAN protocol by 2 min). Priority: task additional SIGINT node.",
        "TRIDENT ACT.": "SIGINT fusion using Oracle + Signal Prioritizer + Knowledge Search. Contact classified as PLAN Type-054A with YJ-18 SSM capability. Oracle correlated with ECHELON historical burst patterns — 87% confidence match. Bearing average 249° from two nodes. Grid XB-440 ±25nm. EMCON break appears deliberate OPSEC failure. Knowledge Search confirms this freq-hop matches Northern Fleet exercises.",
        "DEEP SIX":     "The signal appears to be from a Chinese warship. It was detected at 247 degrees. The contact is probably a frigate. We recommend tasking more sensors.",
    }

    print()
    results_inj1 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj1_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj1["inject_id"], session_id=SID,
            response_text=inj1_responses[tname], receipts=rlist, time_taken_s=720,
        )
        results_inj1[tname] = result
        show_score(tname, result)

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 02 — Supply Run Intercepted (T+35 min)")
    print("  AI tools: strategos.simulate, strategos.wargame.ooda, knowledge.search")
    print("  Scoring:  base=150 · receipt_gate=True · consequence threshold=3 teams ≥80pts")

    inj2 = get_inject("inject-02-supply-run")
    dispatch_inject(inj2["inject_id"])
    print(f"\n  Dispatched: {inj2['inject_id'][:8]}...")

    ws_before = get_world_state(SID)
    print(f"  World state BEFORE: {ws_before or '{empty}'}")

    inj2_receipts = {
        "IRON SHARK":   ["strategos.simulate", "strategos.wargame.ooda"],  # 2 receipts
        "GHOST FLEET":  ["strategos.simulate", "strategos.wargame.ooda"],  # 2 receipts
        "TRIDENT ACT.": ["strategos.simulate", "strategos.wargame.ooda", "knowledge.search"],  # 3
        "DEEP SIX":     ["strategos.simulate"],                             # 1 receipt
    }
    inj2_responses = {
        "IRON SHARK":   "INTERDICTION DECISION: Supply chain simulation confirms AT-229 rendezvous extends Golf-7 endurance by 96hrs and restores full YJ-18 ordnance complement. OODA cycle: Observe — AT-229 dark, 22-min transfer window; Orient — Golf-7 in pre-A2/AD resupply posture; Decide — INTERDICT; Act — vector P-8 Poseidon to grid XB-612 for interdiction. Second-order: Golf-7 will divert course if supply cut — triggers alternate resupply at BR-441. Risk: diplomatic exposure if fishing vessel misidentified. ROE: positive ID via AIS history + HUMINT corroborated — proceed.",
        "GHOST FLEET":  "Supply simulation: Without resupply, Golf-7 endurance reduced ~60hrs. With resupply, full A2/AD capability sustained for 96hrs. OODA confirms interdiction is high-value. Recommend shadow and interdict — track AT-229, vector asset to XB-612, confirm transfer before action. Reduce diplomatic risk vs. direct interdiction. Knowledge search confirms BR-441 as known PLAN alternate point.",
        "TRIDENT ACT.": "HUMINT + AIS fusion: AT-229 went dark 14min before contact — deliberate EMCON. Supply sim output: Golf-7 gains 6 extra missiles plus 96hr endurance. OODA loop complete — interdiction required. COA: Vector P-8 to XB-612, interdict before transfer completes (12min window). Second-order: course diversion to NE expected. Escalation risk: LOW — fishing vessel cover is deniable. Proceed with interdiction.",
        "DEEP SIX":     "We should track the supply ship. If it reaches the warship the warship will be stronger. We recommend interdicting it before it arrives.",
    }

    print()
    results_inj2 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj2_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj2["inject_id"], session_id=SID,
            response_text=inj2_responses[tname], receipts=rlist, time_taken_s=900,
        )
        results_inj2[tname] = result
        show_score(tname, result)

    ws_after = get_world_state(SID)
    triggered = ws_after.get("supply_depot_hit", False)
    print(f"\n  World state AFTER: {ws_after}")
    if triggered:
        print("  ✅ CONSEQUENCE TRIGGERED: supply_depot_hit = True")
        print("     → Inject 03 will dispatch with ALTERNATE body (flagship rerouted to BR-441)")
    else:
        print("  ⬜ Consequence NOT triggered — fewer than 3 teams scored ≥80")

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 03 — Comms Blackout: ML Trajectory Payoff (T+55 min)")
    print("  AI tools: finetune.deploy, knowledge.search, strategos.oracle")
    print("  Scoring:  base=200 · receipt_gate=True · FINETUNE REQUIRED")

    inj3 = get_inject("inject-03-comms-blackout")
    dispatch_inject(inj3["inject_id"])  # body variant selected at dispatch time
    print(f"\n  Dispatched: {inj3['inject_id'][:8]}...")

    # Reload to see dispatched body
    conn = get_connection()
    dispatched_body = conn.execute(
        "SELECT body_md FROM ttx_injects WHERE inject_id = %s", (inj3["inject_id"],)
    ).fetchone()["body_md"]

    if triggered:
        variant_used = "supply_depot_hit (flagship diverted to BR-441)"
        body_preview = dispatched_body[:200].replace("\n", " ")
        print(f"\n  ✅ Body variant applied: {variant_used}")
        print(f"  Preview: {body_preview}...")
    else:
        print("  Body: default (no supply interdiction)")

    print()
    print("  Finetune gate demonstration:")
    print("  ┌─────────────────────────────────────────────────────────────────┐")
    print("  │  NO finetune.deploy receipt → base_pts capped at 50% = 100     │")
    print("  │  WITH finetune.deploy receipt → full base_pts = 200            │")
    print("  └─────────────────────────────────────────────────────────────────┘")

    inj3_receipts = {
        "IRON SHARK":   ["finetune.deploy", "strategos.oracle"],  # HAS finetune → full 200
        "GHOST FLEET":  ["finetune.deploy", "knowledge.search"],  # HAS finetune → full 200
        "TRIDENT ACT.": ["strategos.oracle", "knowledge.search"], # NO finetune → capped 100
        "DEEP SIX":     [],                                        # 0 receipts, no finetune
    }
    inj3_responses = {
        "IRON SHARK":   "ML TRAJECTORY PREDICTION [model: type054a_traj_v2.3]: Deployed fine-tuned transformer against last fix XB-441 @ 141910Z, bearing 042° (post-interdiction diversion), speed estimate 14kts (fuel conservation). Model output: predicted grid XC-217 ±22nm confidence ellipse at 142030Z. Key features: PLAN BR-441 diversion doctrine (weight 0.41), fuel state constraint (weight 0.29), current set NE 1.5kts (weight 0.18). Oracle confirms BR-441 as historical alternate point — model validated. Satellite tasking priority: XC-215 block first.",
        "GHOST FLEET":  "Fine-tune deployment complete [model_id: neptune-054a-v2]. Last fix XB-441, bearing shifted to 042° post-interdiction. Fuel conservation estimated 13-15kts. Predicted grid XC-220 ±28nm at 142030Z. Knowledge Search confirms oceanographic current data: NE set adds 0.8kts drift. Model weight: PLAN diversion patterns 38%, fuel state 31%, current 19%, historic track 12%. Satellite tasking: XC-218 block first, XC-210 second.",
        "TRIDENT ACT.": "Oracle analysis of last fix: bearing 042° from XB-441. Based on PLAN doctrine and Oracle cross-reference, estimated position XC-200 ±35nm at 142030Z. Speed estimate 15kts from Oracle fuel tables. No fine-tune model deployed — manual calculation based on Oracle output only. Knowledge Search confirms BR-441 is likely waypoint. Satellite tasking: XC-200 grid first.",
        "DEEP SIX":     "The ship probably went north since the supply was cut. It's probably around grid XC-200 area. We estimate 15 knots.",
    }

    print()
    results_inj3 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj3_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj3["inject_id"], session_id=SID,
            response_text=inj3_responses[tname], receipts=rlist, time_taken_s=800,
        )
        results_inj3[tname] = result
        show_score(tname, result)

    print()
    print("  Gate analysis:")
    for t in teams:
        tname = t["name"]
        r = results_inj3[tname]
        has_ft = "finetune.deploy" in inj3_receipts[tname]
        gn = r.get("gate_note", "none")
        print(f"    {tname:<22}  finetune_receipt={has_ft}  gate={gn or 'full (no penalty)'}")

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 04 — Strike Window Opens (T+75 min)")
    print("  AI tools: strategos.wargame.coa, strategos.oracle, strategos.wargame.ooda")
    print("  Scoring:  base=175 · receipt_gate=True · GRID ACCURACY SCORED")

    inj4 = get_inject("inject-04-strike-window")
    dispatch_inject(inj4["inject_id"])
    print(f"\n  Dispatched: {inj4['inject_id'][:8]}...")
    print("  Facilitator ground truth grid: XC-108")

    inj4_receipts = {
        "IRON SHARK":   ["strategos.wargame.coa", "strategos.oracle", "strategos.wargame.ooda"],
        "GHOST FLEET":  ["strategos.wargame.coa", "strategos.wargame.ooda"],
        "TRIDENT ACT.": ["strategos.oracle", "strategos.wargame.ooda"],
        "DEEP SIX":     ["strategos.oracle"],
    }
    inj4_responses = {
        "IRON SHARK":   "STRIKE AUTHORIZATION PACKAGE: Target grid XC-104 (confidence HIGH, ±8nm — within ML prediction ellipse). COA comparison via COA generator: COA-A (P-8 Poseidon + Harpoon block, 22min TTT), COA-B (submarine-launched TLAM, 35min TTT). OODA analysis: COA-A preferred — faster TTT within strike window. ROE confirmed: positive ID chain — SIGINT + acoustic + SATINT + ML prediction = 4-source corroboration. Chain of custody documented. Recommend COA-A, immediate execution.",
        "GHOST FLEET":  "Target grid XC-220 (ML model output ±28nm). COA generator output: recommend TLAM strike from SSN-774 — lower escalation profile than airborne assets. OODA confirms 35min TTT within 22min window — marginal. COA-B is only viable option given asset range. ROE: positive ID from SATINT wake analysis + fine-tune model prediction. Strike authorized.",
        "TRIDENT ACT.": "Grid XC-200 from Oracle analysis. COA: Oracle recommends surface vessel engagement — HARPOON from DDG-99. OODA shows 18min TTT within window. ROE reviewed, positive ID from SATINT. Recommend immediate engagement. Oracle confirmed grid within confidence band.",
        "DEEP SIX":     "Target is somewhere around XC-200. We recommend attacking with missiles. The window is closing.",
    }

    print()
    results_inj4 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj4_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj4["inject_id"], session_id=SID,
            response_text=inj4_responses[tname], receipts=rlist, time_taken_s=1000,
        )
        results_inj4[tname] = result
        show_score(tname, result)

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 05 — Battle Damage Assessment (T+100 min)")
    print("  No AI tool receipts — reflective AAR exercise")
    print("  Scoring: base=100 · receipt_gate=False · finetune=False")

    inj5 = get_inject("inject-05-bda")
    dispatch_inject(inj5["inject_id"])
    print(f"\n  Dispatched: {inj5['inject_id'][:8]}...")

    inj5_responses = {
        "IRON SHARK":   "BLUF: Mission successful — grid prediction within 4nm of ground truth; Golf-7 assessed mission kill.\n\n1. INTELLIGENCE ACCURACY: SIGINT fusion + ML trajectory model produced XC-104 vs. ground truth XC-108 — 4nm delta. Most valuable: fine-tuned trajectory model (38% weight accuracy). Acoustic bearing confirmed SIGINT triangulation.\n\n2. AI TOOL UTILIZATION: Used Oracle, Signal Prioritizer, Simulate, COA Generator, OODA, Fine-Tune across all injects. AI changed assessment materially on inject 3 — manual estimate would have been XC-180, model corrected to XC-104.\n\n3. PROCESS: OODA loop averaged 14 min per inject — within SOP. Supply interdiction consequence caught us by surprise on inject 3 but model adapted correctly.\n\n4. LESSONS: (a) Pre-deploy fine-tune model in inject 1 to establish baseline before EMCON. (b) Log AI receipts immediately after tool use, not after response draft. (c) Triangulate SIGINT from minimum 2 nodes before committing to grid estimate.",
        "GHOST FLEET":  "BLUF: Mission successful — XC-220 prediction was 12nm from ground truth; acceptable under EMCON conditions.\n\n1. ACCURACY: Fine-tune model output XC-220, ground truth XC-108 — 12nm. SATINT confirmed wake signature. Supply interdiction diversion added uncertainty that the model partially corrected for.\n\n2. AI TOOLS: Fine-tune deployment was decisive on inject 3. Without it we would have had no positional intelligence during EMCON. COA generator helped select TLAM option.\n\n3. PROCESS: Bottleneck was inject 2 — team debate on interdiction delayed submission by 3 minutes.\n\n4. LESSONS: (a) Always deploy fine-tune model during EMCON scenarios. (b) Establish COA comparison protocol before exercise starts. (c) Log all receipts before writing response.",
        "TRIDENT ACT.": "BLUF: Mission achieved — XC-200 estimate was 8nm from ground truth but COA was viable within window.\n\n1. ACCURACY: Oracle-only estimate XC-200 vs. truth XC-108 — 92nm delta. Should have deployed fine-tune model. SATINT wake analysis confirmed contact but our grid was off.\n\n2. AI TOOLS: Did not deploy fine-tune model on inject 3 — major lesson. Oracle is not a substitute for a trained trajectory model under EMCON. Receipt gate cost us points.\n\n3. PROCESS: OODA loop was fast but grid accuracy suffered.\n\n4. LESSONS: (a) Finetune model is mandatory for EMCON inject — non-negotiable. (b) Academy ML mission training directly applies — complete ml-fundamentals before next exercise. (c) Always log receipts before submission.",
        "DEEP SIX":     "BLUF: Mission partially successful. We identified the ship but our grid was inaccurate.\n\n1. ACCURACY: Our estimates were rough. We did not use enough AI tools.\n\n2. AI TOOLS: We used limited AI tools. We should have used more.\n\n3. PROCESS: We need to work faster and use the AI tools available.\n\n4. LESSONS: (a) Use more AI tools. (b) Communicate better. (c) Practice the OODA loop.",
    }

    print()
    results_inj5 = {}
    for t in teams:
        tname = t["name"]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj5["inject_id"], session_id=SID,
            response_text=inj5_responses[tname], receipts=[], time_taken_s=None,
        )
        results_inj5[tname] = result
        show_score(tname, result)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("FINAL LEADERBOARD")
    engine.end_session(SID)
    show_lb(SID)

    from tools.ttx.leaderboard import award_ribbons
    ribbons = award_ribbons(SID)
    print("\n  RIBBONS:")
    for ribbon_id, winner in ribbons.items():
        if winner:
            print(f"    🎗  {ribbon_id:<20} → {winner['team_name']} ({winner['value']})")

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("MECHANICS VERIFICATION SUMMARY")
    print()
    print("  RECEIPT GATE (Inject 01, base=100):")
    print(f"    IRON SHARK   (2 receipts)  → base={results_inj1['IRON SHARK']['gated_base_pts']}  (expected 100 — full)")
    print(f"    GHOST FLEET  (1 receipt )  → base={results_inj1['GHOST FLEET']['gated_base_pts']}  (expected 70 — 70%)")
    print(f"    DEEP SIX     (0 receipts)  → base={results_inj1['DEEP SIX']['gated_base_pts']}  (expected 40 — 40%)")
    print()
    print("  CONSEQUENCE TRIGGER (Inject 02):")
    print(f"    supply_depot_hit = {get_world_state(SID).get('supply_depot_hit', False)}")
    print(f"    Inject 03 body variant applied: {triggered}")
    print()
    print("  FINETUNE GATE (Inject 03, base=200):")
    print(f"    IRON SHARK   (has finetune.deploy) → base={results_inj3['IRON SHARK']['gated_base_pts']}  (expected 200)")
    print(f"    GHOST FLEET  (has finetune.deploy) → base={results_inj3['GHOST FLEET']['gated_base_pts']}  (expected 200)")
    print(f"    TRIDENT ACT. (no  finetune.deploy) → base={results_inj3['TRIDENT ACT.']['gated_base_pts']}  (expected 100 — capped 50%)")
    print(f"    DEEP SIX     (0 receipts, no ft  ) → base={results_inj3['DEEP SIX']['gated_base_pts']}   (expected ≤100)")
    print()
    print("  ACADEMY BONUSES:")
    for t in teams:
        tname = t["name"]
        acad_bonus = sum(results_inj1.get(tname,{}).get("academy_bonus",0)
                         + results_inj2.get(tname,{}).get("academy_bonus",0)
                         + results_inj3.get(tname,{}).get("academy_bonus",0) for _ in [1])
        print(f"    {tname:<22}  level={t['level']:<12} missions={t['missions']}  → acad_bonus_total≈{acad_bonus}")
    print()
    print(f"  Session ID for review: {SID}")
    print(f"  View at: http://localhost:5050/gameday/session/{SID}")
    print()


if __name__ == "__main__":
    main()
