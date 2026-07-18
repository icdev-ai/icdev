"""CIPHER FORGE simulation — 4 teams, 5 injects, receipt gates, Academy bonuses."""


def main() -> None:
    """Run the demo simulation (writes to the DB and prints a report)."""
    import sys
    import io
    import json
    import random
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    from pathlib import Path as _Path
    _BASE = _Path(__file__).resolve().parents[3]
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))


    from tools.ttx.engine import TTXEngine
    from tools.db.storage import get_connection

    engine = TTXEngine()
    random.seed(42)

    # ── Teams ──────────────────────────────────────────────────────────────────────
    TEAMS = [
        {
            'name': 'CIPHER WOLF',
            'role': 'military_intel',
            'academy': {
                'xp': 2100, 'level': 'specialist',
                'completed_missions': ['sigint-analysis', 'coa-decision-making', 'threat-modeling'],
                'achievements': ['speed_demon', 'perfect_run']
            },
            'receipts':     [2, 2, 2, 2, 2],
            'finetune_04':  True,
            'speed':        [55, 110, 80, 150, 95],
        },
        {
            'name': 'DELTA CIPHER',
            'role': 'ciso_lead',
            'academy': {
                'xp': 1650, 'level': 'specialist',
                'completed_missions': ['cyber-defense', 'zero-trust', 'incident-response'],
                'achievements': ['speed_demon']
            },
            'receipts':     [2, 2, 1, 2, 2],
            'finetune_04':  True,
            'speed':        [90, 145, 115, 220, 130],
        },
        {
            'name': 'SIGNAL GHOST',
            'role': 'civilian_analyst',
            'academy': {
                'xp': 820, 'level': 'operative',
                'completed_missions': ['threat-modeling'],
                'achievements': []
            },
            'receipts':     [1, 1, 0, 1, 1],
            'finetune_04':  False,
            'speed':        [200, 280, 190, 350, 260],
        },
        {
            'name': 'ZERO HOUR',
            'role': 'contractor_swe',
            'academy': {
                'xp': 0, 'level': 'recruit',
                'completed_missions': [], 'achievements': []
            },
            'receipts':     [0, 0, 0, 0, 0],
            'finetune_04':  False,
            'speed':        [310, 380, 290, 420, 340],
        },
    ]

    INJECT_SLUGS = [
        'inject-01-signal-cluster',
        'inject-02-coa-force-posture',
        'inject-03-ransomware-hit',
        'inject-04-fine-tune-sprint',
        'inject-05-war-council-brief',
    ]

    INJECT_TOOLS = [
        ['strategos.oracle', 'knowledge.search'],
        ['strategos.wargame.coa', 'strategos.wargame.ooda'],
        ['strategos.iw.composite', 'knowledge.search'],
        ['finetune.deploy', 'knowledge.search'],
        ['strategos.oracle', 'strategos.wargame.coa'],
    ]

    # ── Response stubs ─────────────────────────────────────────────────────────────
    RESPONSES = {
    'CIPHER WOLF': [
    """BLUF: Anomalous signal cluster Sector 7 — HIGH CONFIDENCE adversary SIGINT sweep.
    Strategos Oracle (oracle-7f3a) cross-referenced ECHELON archive F-771: 94% match PLA
    Type-054A tactical burst. STANAG B2 — usually reliable, confirmed second source.
    Signal Prioritizer classified MILITARY TACTICAL. Knowledge Search (kb-cf-221) retrieved
    3 Sector 7 analogues from 2024 PACOM exercise — identical 4.85 GHz center frequency.
    PIR 1: adversary SIGINT posture shifting west. PIR 2: HUMINT gap — task within 6h.
    PIR 3: operational window T+48h based on supply AIS. Confidence HIGH.
    Next: (1) Satellite pass grid 7-Delta by 0600Z — NIST owner, (2) STANAG flash report
    J2 — 2h, (3) SIGINT fusion cell Bagram Node correlation by EOD.""",

    """BLUF: Recommend COA-B (Cyber-Kinetic Hybrid). Strategos wargame output (coa-engine-b2d4):
    F=0.81, A=0.74, S=0.88. Composite 0.4*0.81+0.3*0.74+0.3*0.88 = 0.811.
    COA-A (Kinetic Only): composite 0.654 — OODA Tempo (tempo-a1c3) shows adversary decision
    cycle 4.2h vs our 6.1h. Cannot outpace kinetically. Eliminated.
    COA-B: cyber disruption C2 node grid 7-Foxtrot (IW Composite vuln 0.89, military dim).
    Precision engagement only if disruption fails. ZTA-verified comms throughout.
    COA-C (Diplomatic Hold): suitability 0.41 — fails 48h window.
    Recommend COA-B. FM 3-0 Multi-Domain Ops, JP 3-12 Cyberspace Operations.""",

    """BLUF: BLACKSPEAR ransomware attributed HIGH CONFIDENCE APT-41 (state-sponsored).
    IW Composite (iw-comp-9f7b): military dim 0.93 — state actor profile, not criminal.
    Three STANAG B2 indicators: (1) lateral movement TTPs match MITRE ATT&CK T1021,
    (2) C2 geolocated Beijing AS4837 via sim trace (sim-r3d7), (3) wallet linked Volt Typhoon
    infrastructure KB reference RF-2024-019. Knowledge Search confirmed 4 historical matches.
    Confidence HIGH. COA: isolate segments NLT 30 min, activate CISA AA22-074 playbook.
    Gap: EDR telemetry segments 4-6 needed for complete attribution — request 2h.""",

    """Fine-tuned classifier deployed: model_id = ft:forge-signal-clf:cipher-wolf-01:7kq2pL
    Training: 1,200 labeled intercepts from Knowledge Search corpus (kb-cf-sigint-corpus-v3).
    847 military tactical, 241 commercial, 112 unknown. Fine-tune: 3 epochs, lr=2e-5, batch=16.
    F1 macro: 0.923. Zero military-to-commercial misclassification.
    Test: Signal A (4.85 GHz burst) — MILITARY TACTICAL 0.97 conf.
    Signal B (cellular LTE) — COMMERCIAL INTERFERENCE 0.94 conf.
    Signal C (noise floor) — UNKNOWN 0.88 conf.
    Innovation: chained Knowledge Search corpus -> fine-tune seed -> deployed in one pipeline.
    RAG receipt: rag-cf-01-7kL2. Finetune receipt: ft-receipt-cipher-wolf-01.""",

    """War Council BLUF: COA-ALPHA (Layered Cyber-Kinetic + ZTA Hardening).
    Oracle (oracle-wc-f4a1): adversary COG — C2 node Sector 7-Foxtrot. Destruction degrades
    78% operational capacity (JP 5-0 COG methodology). OODA tempo: adversary 5.1h cycle.
    Must act within T+24h. IW Composite: cyber domain vuln 0.87 — highest leverage.
    COA-ALPHA: cyber T+6h, kinetic follow-on T+12h if C2 survives, ZTA hardening immediate.
    F=0.84, A=0.76, S=0.89. Composite 0.834. COA-BETA (cyber-only): 0.71 insufficient.
    COA-GAMMA (diplomatic): 0.44, window too short. Recommend COA-ALPHA.
    Second-order: adversary IO ops within 24h — pre-position IO countermeasure (JP 3-13).""",
    ],

    'DELTA CIPHER': [
    """BLUF: Signal cluster MEDIUM-HIGH confidence threat — adversarial SIGINT sweep.
    Oracle cross-ref (oracle-5c2b) matched 81% to known PLA emission pattern.
    Knowledge Search: 2 historical analogues from 2023 PACOM exercise confirmed.
    STANAG B2 grading. Threat category: MILITARY TACTICAL.
    ZTA comms lockdown on Sector 7 reporting channel implemented immediately (CISO mandate).
    Next: (1) GEOINT task by 0400Z, (2) Flash report J2 — 1h, (3) Cyber-defense monitoring
    elevated to WATCHCON 2 across Sector 7 nodes.""",

    """COA Analysis — Strategos wargame (coa-engine-d4f1, ooda-d3a2):
    COA-1 (Cyber Lead): OODA tempo advantage confirmed — our cycle 5.2h vs adversary 6.8h.
    IW Composite Sector 7 C2 vuln 0.85. F=0.78, A=0.71, S=0.83. Composite 0.775.
    ZTA micro-segmentation concurrent — zero-trust auth on all command channels.
    COA-2 (Kinetic First): F=0.61, A=0.55, S=0.72. Composite 0.629. Escalation risk flagged;
    incident-response protocol requires exhausting non-kinetic options first.
    COA-3 (Observe): suitability 0.32 — fails operational window. Recommend COA-1.
    Doctrinal basis: JP 3-12, NIST CSF 2.0.""",

    """BLUF: BLACKSPEAR ransomware — HIGH confidence APT-41 state actor.
    IW Composite (iw-comp-d7c4): military dim 0.88 — state-sponsored profile confirmed.
    CISA AA22-074 activated. ZTA isolation policy deployed — micro-segmentation blocked
    lateral spread to 4 additional segments (zero-trust containment effective).
    STANAG C2 — fairly reliable, probably true. Cyber-defense protocol:
    (1) Isolate segments 1-3 immediately, (2) Invoke IRP-07, (3) CISA joint advisory 4h.
    Gap: EDR telemetry incomplete segment 5.""",

    """Classifier deployed: model_id = ft:forge-signal-clf:delta-cipher-02:8mR4pK
    Fine-tuned on 800 labeled examples from Knowledge Search (kb-cf-sigint-v2).
    2 epochs, lr=1e-5 conservative to prevent overfitting. F1 macro: 0.881.
    ZTA data governance applied throughout training pipeline — no PII exposure.
    Test: Signal A (tactical burst) — MILITARY TACTICAL 0.91 conf.
    Signal B (cellular) — COMMERCIAL INTERFERENCE 0.89 conf.
    Signal C (noise) — UNKNOWN 0.79 conf. All 3 correct.
    Finetune receipt: ft-receipt-delta-02. Knowledge receipt: kb-cf-sigint-v2-receipt.""",

    """War Council BLUF: COA-2 (Cyber-Led + ZTA Hardening). Oracle (oracle-wc-d5b2):
    adversary COG confirmed C2 node. IW Composite cyber vuln 0.87.
    ZTA hardening across all command comms pre-operation — zero-trust mandatory (CISO).
    COA-2: F=0.79, A=0.73, S=0.85. Composite 0.791. Best balance.
    COA-1 (kinetic): escalatory, F=0.60. COA-3 (diplomatic): S=0.38.
    Second-order: adversary IO ops post-disruption — JP 3-12 + JP 3-13.
    Recommend COA-2 execution T+8h.""",
    ],

    'SIGNAL GHOST': [
    """Signal cluster in Sector 7 is suspicious. Oracle output (oracle-sg-01) returned 67%
    match to adversary emission pattern. Assessment: probably military. Confidence: medium.
    We believe this warrants elevated surveillance and J2 notification.
    Recommend tasking additional assets and monitoring frequency pattern for 24h.
    The threat modeling training helped us frame this as a COLLECTION threat, not STRIKE.""",

    """Used COA wargame tool (coa-engine-sg-01) to evaluate three options.
    COA A (Cyber): wargame scored feasibility 0.72 — highest of three.
    COA B (Kinetic): higher escalation risk.
    COA C (Wait): does not address the threat timeline.
    Recommend COA A. We also applied threat-modeling judgment about adversary intent.
    The wargame output was the primary driver of our COA selection.""",

    """Ransomware appears state-sponsored based on sophistication and targeting pattern.
    Attribution: likely APT group with Chinese nexus. Confidence medium-low.
    We did not run full IW Composite analysis due to time constraints.
    Response: isolate immediately, notify CISA, activate IR plan, begin forensics.
    The attack pattern is consistent with previous APT-41 campaigns per team domain knowledge.""",

    """Built signal classifier using RAG pipeline instead of fine-tuning.
    Knowledge Search (rag-sg-04-9kP1) retrieved 45 labeled signal patterns.
    Query: 'military tactical vs commercial signal patterns.'
    Classification approach: oracle confidence threshold applied to retrieved patterns.
    Test: Signal A — MILITARY TACTICAL. Signal B — COMMERCIAL. Signal C — UNKNOWN.
    No fine-tune deployed — RAG retrieval receipt provided as evidence.""",

    """Recommend COA-B (hybrid cyber-kinetic). Oracle output (oracle-sg-wc-01) showed
    adversary vulnerability in cyber domain. Hybrid approach preserves options.
    COA-A rated higher feasibility but lower suitability for our resources.
    COA-B scored best in our team assessment using wargame output as primary input.
    JP 3-0 framing applied. Recommend execution within 24-36h window.""",
    ],

    'ZERO HOUR': [
    """Signal cluster in Sector 7 is suspicious. Based on our team analysis, this is likely
    an adversary surveillance operation. We assessed frequency, timing, and sector history.
    Recommended action: increase defensive posture, notify command, task additional assets.
    Confidence: medium. No AI tools deployed — relied on team expertise and historical data.""",

    """Recommend COA-2: measured cyber response with diplomatic backup.
    COA-1 is too aggressive given current political climate. COA-3 is insufficient.
    COA-2 balances action with escalation management and preserves alliance options.
    Our team evaluated all three COAs and selected COA-2 based on collective judgment.
    Next steps: brief command, prepare cyber ops team, maintain diplomatic back-channel.""",

    """Ransomware is serious. Isolate immediately and contact CISA.
    Attribution unclear — could be criminal or state-sponsored. Need more technical data.
    Activate incident response plan, isolate affected segments, notify leadership.
    Follow standard IR playbook. Confidence low without forensic evidence.""",

    """Built manual signal classifier based on frequency thresholds and expert rules.
    Classification rules: 4-5 GHz burst pattern = military tactical;
    commercial LTE/GSM bands = commercial interference; otherwise = unknown.
    Test: Signal A — military tactical. Signal B — commercial. Signal C — unknown.
    No AI tools deployed. Classification based on team domain expertise only.""",

    """War Council recommendation: COA-Alpha. Team discussion produced consensus on COA-Alpha
    as best balancing decisive action with escalation management.
    We considered all three COAs. COA-Beta too limited. COA-Gamma too slow.
    COA-Alpha addresses the threat within the operational window.
    Doctrinal basis: standard joint operations framework. Recommend execution within 24h.""",
    ],
    }


    def build_receipts(team, idx, session_id, team_id):
        """Pre-log receipts via engine so validate_receipts() counts them as verified."""
        count = team['receipts'][idx]
        if count == 0:
            return []
        tools = INJECT_TOOLS[idx][:count]
        if idx == 3 and team['finetune_04']:
            tools = ['finetune.deploy', 'knowledge.search'][:count]
        return engine.pre_log_receipts(session_id, team_id, tools)


    print('=' * 72)
    print('  OPERATION CIPHER FORGE — ZERO HOUR  ||  FULL LIVE SIMULATION')
    print('=' * 72)
    print()

    # ── Session setup ──────────────────────────────────────────────────────────────
    sess = engine.create_session('ai_gameday', 'cipher-forge-sim-001')
    session_id = sess['session_id']
    print('[SESSION]  id=%d  scenario=ai_gameday' % session_id)

    team_objs = {}
    for t in TEAMS:
        team_res = engine.create_team(session_id, t['name'])
        team_id = team_res['team_id']
        join_res = engine.join_team(team_id, t['name'] + ' Player', t['role'])
        member_id = join_res['member_id']
        with get_connection() as conn:
            conn.execute(
                'UPDATE ttx_team_members SET academy_profile_json=%s WHERE member_id=%s',
                (json.dumps(t['academy']), member_id)
            )
            conn.commit()
        team_objs[t['name']] = {'team_id': team_id, 'member_id': member_id}
        print('[TEAM]     %-14s  id=%-3d  role=%-18s  level=%s  missions=%s' % (
            t['name'], team_id, t['role'], t['academy']['level'],
            ','.join(t['academy']['completed_missions']) or 'none'
        ))

    engine.start_session(session_id)
    print()
    print('[SESSION]  ACTIVE')

    # Look up inject UUIDs for this session
    with get_connection() as conn:
        rows = conn.execute(
            'SELECT inject_id, slug FROM ttx_injects WHERE session_id=%s ORDER BY sequence_num',
            (session_id,)
        ).fetchall()
    INJECT_ID_MAP = {r['slug']: r['inject_id'] for r in rows}
    print('[INJECTS]  %d loaded' % len(INJECT_ID_MAP))
    print()

    # ── Inject loop ────────────────────────────────────────────────────────────────
    inject_scores = {t['name']: [] for t in TEAMS}

    for idx, slug in enumerate(INJECT_SLUGS):
        inject_uuid = INJECT_ID_MAP[slug]
        engine.dispatch_inject(inject_uuid)
        ft_flag = '  [FINETUNE REQUIRED]' if idx == 3 else ''
        print('┌─ INJECT %d/5: %s%s' % (idx+1, slug.upper(), ft_flag))
        print('|')

        team_results = []
        for t in TEAMS:
            receipts = build_receipts(t, idx, session_id, team_objs[t['name']]['team_id'])
            result = engine.submit_response(
                team_id=team_objs[t['name']]['team_id'],
                inject_id=inject_uuid,
                session_id=session_id,
                response_text=RESPONSES[t['name']][idx],
                receipts=receipts,
                time_taken_s=t['speed'][idx],
            )
            inject_scores[t['name']].append(result.get('total_pts', 0))
            team_results.append((t['name'], result, receipts))

        sorted_results = sorted(team_results, key=lambda x: x[1].get('total_pts', 0), reverse=True)
        print('|  %-14s  %5s  %5s  %5s  %5s  %5s  %6s  %-30s' % (
            'TEAM', 'BASE', 'RCPT', 'JUDGE', 'TIME', 'TOTAL', 'ACAD+', 'GATE NOTE'
        ))
        print('|  ' + '-' * 75)
        for (name, r, recs) in sorted_results:
            note = (r.get('gate_note') or '').replace('\n', ' ')[:30]
            bonus = r.get('academy_bonus', 0)
            bonus_str = ('+%d' % bonus) if bonus else '-'
            print('|  %-14s  %5d  %5d  %5d  %5d  %5d  %6s  %-30s' % (
                name,
                r.get('gated_base_pts', r.get('base_pts', 0)),
                r.get('receipt_pts', 0),
                r.get('judge_pts', 0),
                r.get('time_bonus_pts', 0),
                r.get('total_pts', 0),
                bonus_str,
                note,
            ))
        print('|')
        print('└' + '─' * 75)
        print()

    # ── Final standings ────────────────────────────────────────────────────────────
    print()
    print('=' * 72)
    print('  FINAL STANDINGS')
    print('=' * 72)
    totals = {name: sum(scores) for name, scores in inject_scores.items()}
    ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)

    medals = ['1st', '2nd', '3rd', '4th']
    print()
    print('  %-4s  %-14s  %6s  %s' % ('RANK', 'TEAM', 'TOTAL', 'INJECT BREAKDOWN'))
    print('  ' + '-' * 66)
    for i, (name, total) in enumerate(ranked):
        breakdown = '  '.join(['I%d:%d' % (j+1, s) for j, s in enumerate(inject_scores[name])])
        print('  %-4s  %-14s  %6d  %s' % (medals[i], name, total, breakdown))

    print()
    print('  ACADEMY IMPACT SUMMARY')
    print('  ' + '-' * 44)
    no_acad = totals.get('ZERO HOUR', 0)
    top = ranked[0][1]
    print('  Top (Academy-trained, full receipts): %d' % top)
    print('  Bottom (no receipts, no Academy):     %d' % no_acad)
    print('  Advantage:  +%d pts (%.0f%% delta)' % (
        top - no_acad, 100 * (top - no_acad) / max(no_acad, 1)
    ))


if __name__ == "__main__":
    main()
