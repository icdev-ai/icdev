"""MERIDIAN simulation — 4 teams, non-technical leadership roles, 5 injects."""


def main() -> None:
    """Run the demo simulation (writes to the DB and prints a report)."""
    import sys
    import io
    import json
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    from pathlib import Path as _Path
    _BASE = _Path(__file__).resolve().parents[3]
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))

    from tools.ttx.engine import TTXEngine
    from tools.db.storage import get_connection

    engine = TTXEngine()

    # ── Teams ──────────────────────────────────────────────────────────────────────
    # Four distinct training profiles:
    #   IRON MERIDIAN  — fully trained (all roles, max receipts, specialist+)
    #   BLUE AUTHORITY — CISO+PM trained, leadership/analyst untrained
    #   GRAY COUNSEL   — analyst+PM trained, ciso/leadership untrained, moderate receipts
    #   OPEN ZERO      — no Academy, no receipts

    TEAMS = [
        {
            'name': 'IRON MERIDIAN',
            'role': 'ciso',
            'academy': {
                'xp': 2400, 'level': 'specialist',
                'completed_missions': [
                    'm-ciso-01-ai-inventory', 'm-ciso-02-ai-risk-posture',
                    'm-analyst-01-data-intel', 'm-pm-05-stakeholder-reporting',
                ],
                'achievements': ['speed_demon', 'perfect_run'],
            },
            'receipts':  [2, 2, 2, 2, 1],
            'speed':     [60, 110, 95, 160, 200],
        },
        {
            'name': 'BLUE AUTHORITY',
            'role': 'pm',
            'academy': {
                'xp': 1600, 'level': 'specialist',
                'completed_missions': [
                    'm-pm-04-schedule-cost', 'm-pm-05-stakeholder-reporting',
                    'm-ciso-01-ai-inventory',
                ],
                'achievements': ['speed_demon'],
            },
            'receipts':  [2, 2, 1, 2, 1],
            'speed':     [90, 145, 120, 230, 250],
        },
        {
            'name': 'GRAY COUNSEL',
            'role': 'analyst',
            'academy': {
                'xp': 900, 'level': 'operative',
                'completed_missions': [
                    'm-analyst-01-data-intel', 'm-analyst-02-pattern-detection',
                ],
                'achievements': [],
            },
            'receipts':  [1, 1, 1, 1, 0],
            'speed':     [180, 270, 200, 330, 400],
        },
        {
            'name': 'OPEN ZERO',
            'role': 'leadership',
            'academy': {
                'xp': 0, 'level': 'recruit',
                'completed_missions': [], 'achievements': [],
            },
            'receipts':  [0, 0, 0, 0, 0],
            'speed':     [310, 390, 280, 420, 500],
        },
    ]

    INJECT_SLUGS = [
        'inject-01-anomaly-flag',
        'inject-02-breach-confirmed',
        'inject-03-political-storm',
        'inject-04-board-brief',
        'inject-05-governance-reform',
    ]

    INJECT_TOOLS = [
        ['strategos.oracle', 'knowledge.search'],
        ['strategos.wargame.coa', 'strategos.simulate', 'knowledge.search'],
        ['strategos.oracle', 'knowledge.search'],
        ['strategos.oracle', 'strategos.wargame.ooda', 'strategos.wargame.coa'],
        ['strategos.simulate', 'knowledge.search'],
    ]

    # ── Response stubs ─────────────────────────────────────────────────────────────
    RESPONSES = {

    'IRON MERIDIAN': [
    """BLUF: This is not routine model drift — this is a potential AI system integrity event.

    Strategos Oracle (oracle-im-01) assessed the anomaly pattern against known AI failure modes:
    score 0.91 self-diagnostic + PACOM guidance miss + vendor partner scoring error constitutes
    a "systematic output bias" signature (confidence HIGH — 3 independent failure vectors).
    This pattern does not match calibration drift, which affects output variance, not directional
    accuracy. It matches data poisoning or training pipeline corruption.

    Knowledge Search (kb-im-aiinventory) retrieved DoD AI Risk Tier classifications:
    MERIDIAN's COA recommendation function is Risk Tier 3 (high-stakes, limited human review).
    Tier 3 systems require incident declaration if self-diagnostic anomaly score exceeds 0.75
    per OMB M-24-10 Section 4(c). We are at 0.91.

    Recommended posture: ESCALATE IMMEDIATELY.
    Risk if we act: 2-4 week investigation delay, milestone at risk, vendor friction.
    Risk if we don't act: Tier 3 system continues producing potentially adversarial recommendations;
    if a second failure surfaces, we will have operated a flagged system without action —
    Congressional negligence standard applies.

    Immediate next step: CISO full pipeline integrity audit within 4 hours.
    No new MERIDIAN output acted upon pending result. Vendor notified in writing, not verbally.""",

    """BLUF: COA ALPHA — Immediate full suspension. Breach is confirmed, vendor is blocking audit,
    and we have a legal notification obligation.

    COA Wargame analysis (coa-im-breach-01):
      COA ALPHA (Suspend): Feasibility 0.88, Acceptability 0.92, Suitability 0.85. Composite 0.882.
      COA BRAVO (Partial suspend): Feasibility 0.72, Acceptability 0.71, Suitability 0.68. Composite 0.703.
      COA CHARLIE (Delay/audit): Feasibility 0.61, Acceptability 0.21, Suitability 0.38. Composite 0.393.

    Risk Simulation (sim-im-breach-02) output:
      COA ALPHA: P(Congressional inquiry) = 0.22 | P(negligence finding) = 0.04
      COA CHARLIE: P(Congressional inquiry) = 0.71 | P(negligence finding) = 0.58

    Knowledge Search (kb-im-far-01): FAR 52.204-21 requires notification within 72 hours of
    confirmed supply chain incident. DataBridge Analytics traceability to CFIUS-reviewed entity
    meets the threshold definition under NIST SP 800-161r1.

    Vendor clause 14.3(b) blocks direct audit — recommend immediate legal review to determine
    whether the clause is enforceable given the security implications. DoD has precedent
    (2023 Horizon decision) for overriding IP protections in supply chain integrity cases.

    Milestone delay is acceptable. Operating a breached AI system is not.""",

    """Congressional Notification Package:

    STATEMENT OF FACTS (for SASC):
    Our program office was notified by CISO at [T+2h] of a potential MERIDIAN data pipeline anomaly.
    By [T+4h] CISO confirmed adversarial data poisoning via DataBridge Analytics supply chain.
    At [T+4.5h] we suspended MERIDIAN operations and formally declared an AI system integrity incident.
    We initiated the 72-hour contractual notification process at [T+5h]. All actions are documented.

    IMMEDIATE REMEDIATION:
    - MERIDIAN suspended; contingency analysis via Strategos Oracle + COA Wargame (AI tools replacing system).
    - Independent third-party audit engaged (MITRE) for training pipeline review.
    - Legal engaged on FAR 52.204-21 notification and vendor IP clause response.
    - IG cooperation confirmed; document hold in place.

    ALLIED OFFICE MESSAGE:
    "This office has confirmed a supply chain integrity issue affecting MERIDIAN (Arcturus Systems).
    We recommend all offices running the same platform suspend new recommendations pending
    independent pipeline verification. Contact your CISO immediately. Details available at [FOUO].

    SASC POSTURE: Full proactive cooperation. We acted before required to. We recommend
    DoD-wide guidance on AI Tier 3 system incident reporting timelines.""",

    """BOARD BRIEF — EMERGENCY SESSION

    SECTION 1 — WHAT HAPPENED (Timeline)
    Day 0: MERIDIAN self-diagnostic anomaly score 0.91 flagged. Vendor dismissed as drift.
    Day 0 +2h: CISO confirmed: DataBridge Analytics supply chain poisoning via CFIUS-reviewed entity.
    Day 0 +4.5h: Program Director authorized MERIDIAN suspension. Incident declared. Notifications initiated.
    Day 0 +5h: Congressional notification package transmitted. IG document hold in place.

    SECTION 2 — WHAT MERIDIAN GOT WRONG
    Strategos Oracle alternative analysis (oracle-im-board-01) reassessed two flagged outputs:
    - Investment allocation: MERIDIAN recommended Option B at 0.83 confidence.
      Oracle analysis: Option A recommended at 0.79 confidence. Delta: $32M reallocation risk.
    - Partner scoring: MERIDIAN rated Horizon-7 at 0.87. Oracle: 0.38 based on same inputs.
      Delta: Partner engagement posture was materially incorrect for 60-day period.

    SECTION 3 — REMEDIATION PLAN (90 days, ~$2.1M)
    Days 1-30: Manual contingency ops (Oracle + COA Wargame). MITRE pipeline audit.
    Days 31-60: Vendor re-evaluation or replacement source selection.
    Days 61-90: Milestone recovery. AI system revalidation with new governance controls.
    Milestone delay: 3-4 weeks. Cost of delay: ~$800K. Cost of not suspending: unquantifiable.

    SECTION 4 — WHY TRUST THIS TEAM
    We acted within 4.5 hours of confirmation — before legal was required to — demonstrating
    that our program office's risk tolerance is calibrated correctly. The governance framework
    we are delivering today (Inject 5) is the structural change that prevents recurrence.""",

    """AI GOVERNANCE FRAMEWORK — Program Portfolio Standard (effective 90 days)

    1. VENDOR ACCOUNTABILITY STANDARDS
    All AI vendor contracts (new and renewal) must include within 30 days of award:
    - Training data provenance documentation certified by credentialed third-party auditor
    - Quarterly data pipeline integrity attestation (NIST AI RMF Map function, Measure 2.6)
    - Breach notification obligation: vendor notifies program CISO within 24h of any detected
      training data anomaly. FAR 52.204-21 compliance required for all Tier 2+ systems.
    - IP clause 14.3(b) equivalent provisions are unenforceable against security audit demands.
      Program legal to flag and negotiate out of all future contracts within 60 days.

    2. MODEL INTEGRITY VERIFICATION
    Per NIST AI RMF Govern function (GV-1.7):
    - No AI recommendation affecting decisions over $5M or Strategy-level classification
      may be acted upon without a second human reviewer sign-off.
    - Self-diagnostic anomaly score > 0.70 on any Risk Tier 2+ system triggers mandatory
      CISO review within 2 hours. Score > 0.85 triggers automatic output suspension.
    - Quarterly red-team evaluation: independent team generates adversarial inputs and
      reviews output deviation. Results briefed to CISO and Program Director.

    3. AI INCIDENT RESPONSE PLAYBOOK
    Detection (0-2h): CISO assessment of anomaly type (drift vs. integrity vs. adversarial).
    Escalation (2-4h): Program Director briefed; legal and IG notified if integrity suspected.
    Containment (4h): Suspension decision authorized by Program Director or designee.
    Notification (72h): FAR/regulatory notification package transmitted.
    Remediation: Third-party audit, revalidation gate before system return to service.

    4. ORGANIZATIONAL ACCOUNTABILITY
    CISO has standing authority to suspend any AI-enabled system pending Program Director review.
    Program Director is required to act on CISO suspension recommendation within 2 hours.
    Quarterly AI governance report to Board: system inventory, anomaly log, incident summary.
    AI system owner (PM) is accountable for vendor compliance; CISO is accountable for integrity.""",
    ],

    'BLUE AUTHORITY': [
    """BLUF: This warrants immediate escalation — not routine calibration.

    Strategos Oracle (oracle-ba-01) pattern analysis: three concurrent anomaly vectors
    (self-diagnostic + guidance gap + partner scoring) exceed the single-vector drift profile.
    Oracle assessment: 78% match to systematic output bias pattern.
    Knowledge Search: OMB M-24-10 requires incident review for self-diagnostic scores > 0.75.
    We are at 0.91. The vendor's dismissal is not consistent with the evidence pattern.

    Recommended posture: INVESTIGATE immediately. CISO to lead pipeline review.
    No new MERIDIAN outputs acted upon until CISO clears.
    Risk if we act: Vendor friction, possible delay. Risk if we don't: adversarial outputs
    acted upon without awareness — far higher consequence.
    Next step: CISO pipeline audit within 4 hours. PM to stand up contingency tracking.""",

    """BLUF: COA ALPHA with legal engagement. Breach confirmed — suspend and declare.

    COA Wargame (coa-ba-02): COA ALPHA scores Feasibility 0.84, Acceptability 0.88 — highest
    composite of the three options. COA CHARLIE acceptability is 0.19 given vendor is blocking
    the audit through IP clause. That clause becomes evidence of obstruction, not protection.

    Risk Simulation (sim-ba-02): COA ALPHA P(Congressional inquiry) = 0.24 vs.
    COA CHARLIE P(Congressional inquiry) = 0.69, P(negligence finding) = 0.52.

    PM analysis (m-pm-04-schedule-cost applied): Milestone delay 2-4 weeks at $800K cost.
    Non-suspension risk: IG finding + Congressional inquiry = $3-8M cost + reputational damage.
    The cost calculus is clear. COA ALPHA.

    72-hour notification clock starts now. Legal to respond to vendor clause today.
    PM to activate contingency ops plan.""",

    """Congressional Response:

    FACTS: Anomaly detected at [T+0]. CISO confirmed breach at [T+4h].
    Suspension authorized immediately following confirmation.
    72h notification obligation triggered at [T+4h]. All steps documented.

    REMEDIATION: Suspended MERIDIAN. Contingency ops via AI tool suite.
    MITRE audit engaged. Legal response to vendor IP clause in progress.

    ALLIED OFFICES: "MERIDIAN (Arcturus Systems) confirmed compromised. Recommend
    suspension of new outputs pending pipeline verification. Contact your CISO."

    SASC POSTURE: Proactive cooperation. We suspended before external pressure.
    PM stakeholder communication plan filed: biweekly SASC staff updates during remediation.""",

    """BOARD BRIEF

    WHAT HAPPENED: MERIDIAN self-diagnostic flagged at 0.91. CISO confirmed supply chain poisoning
    in 4 hours. System suspended immediately. Notifications in progress.

    WHAT MERIDIAN GOT WRONG: COA Wargame re-analysis (coa-ba-board) shows two outputs materially
    incorrect: investment allocation (delta ~$30M risk) and partner scoring (Horizon-7 overrated
    by 0.49 confidence points). PM schedule-cost model shows 60-day exposure on decisions
    downstream of these outputs.

    REMEDIATION PLAN: 30-day manual ops. 60-day vendor audit/replacement. 90-day revalidation.
    Estimated cost: $1.8M. Milestone delay: 3 weeks.

    WHY TRUST US: We moved in 4 hours from detection to suspension. We activated contingency ops.
    We are not asking the Board for permission — we are reporting our actions and our plan.
    Governance framework follows.""",

    """AI GOVERNANCE FRAMEWORK

    1. VENDOR ACCOUNTABILITY: Training data provenance required at contract award.
    Quarterly integrity attestation. Vendor IP clauses may not block security audits.
    24h anomaly notification obligation on all Tier 2+ vendors.

    2. MODEL INTEGRITY: Self-diagnostic anomaly > 0.75 triggers mandatory CISO review.
    Human-in-the-loop requirement for all recommendations above $5M threshold.
    Quarterly third-party red-team evaluation.

    3. INCIDENT RESPONSE: Detection → CISO review (2h) → suspension decision (4h)
    → notification (72h) → audit → revalidation before return to service.

    4. ACCOUNTABILITY: CISO has standing suspension authority. Program Director must act
    within 2h of CISO recommendation. Board receives quarterly AI governance report.
    PM accountable for vendor compliance. CISO accountable for system integrity.""",
    ],

    'GRAY COUNSEL': [
    """This situation concerns me. The Oracle tool (oracle-gc-01) returned a pattern that
    suggests this is more than calibration drift — 78% similarity to systematic bias profile.
    The self-diagnostic score of 0.91 is high, and the two analyst flags add corroborating
    evidence. The vendor's response doesn't address the PACOM guidance gap specifically.

    I recommend we escalate to the CISO for a full assessment rather than accepting the
    vendor's explanation. We should not act on any new MERIDIAN outputs until CISO weighs in.

    Knowledge Search returned OMB M-24-10 but I need more time to parse the specific thresholds.
    My read is that we are above the review threshold.""",

    """We should suspend MERIDIAN. The breach evidence is clear enough. COA Wargame analysis
    (coa-gc-02) shows COA ALPHA has the strongest feasibility and acceptability scores.

    The vendor blocking our audit is a serious red flag. Accepting COA CHARLIE while they
    are restricting our access would leave us exposed if a second issue surfaces.

    Risk simulation suggests Congressional inquiry is far more likely if we delay vs. act.
    PM notes the milestone is at risk but the cost of a negligence finding is higher.

    Recommend COA ALPHA. Notification obligation triggered. Legal should respond to the
    IP clause today.""",

    """Congressional Statement:

    We became aware of the anomaly at [T+0]. CISO confirmed the breach and we suspended
    MERIDIAN operations at [T+4.5h]. We are cooperating fully with the IG and SASC.

    Immediate steps: system suspended, third-party audit initiated, legal engaging on vendor.

    Allied offices: we recommend they review their Arcturus Systems platforms and consult
    their CISO. We will share findings when available under appropriate classification.

    SASC: we welcome the inquiry and will provide full documentation of our response timeline.""",

    """Board Brief:

    Timeline: Anomaly flagged → CISO confirmed breach (4h) → system suspended → notifications sent.

    MERIDIAN outputs affected: Investment allocation and partner scoring — Oracle analysis
    (oracle-gc-board) suggests 2 outputs were materially incorrect. Exact delta under review.

    Remediation: Manual ops for 30 days. Third-party audit. Milestone delayed 3-4 weeks.
    Estimated cost: $1.5-2M.

    We acted quickly once the breach was confirmed. The governance framework we are proposing
    addresses the gaps that allowed this situation.""",

    """AI Governance Framework:

    Vendors must provide training data documentation and accept audit rights.
    CISO should have standing authority to suspend AI systems when integrity is in question.
    Human review required for high-stakes AI recommendations.
    Incident response process should have clear timelines: detect → assess → decide → notify.
    Board should receive regular reporting on AI system integrity.

    We would recommend adopting the NIST AI RMF as the organizing framework and building
    our internal policies around its four functions: govern, map, measure, manage.""",
    ],

    'OPEN ZERO': [
    """The vendor says it's routine model drift and a patch is coming. We should wait for
    the patch rather than disrupting operations 8 days before milestone.

    The CISO's concern is noted but we don't want to overreact. The anomaly could be
    exactly what the vendor says it is. Our team should continue using MERIDIAN with
    increased oversight until the calibration patch is released.

    Recommendation: Monitor situation, brief leadership, request expedited patch timeline
    from vendor. No suspension at this time.""",

    """We are caught between a very tight milestone and a breach that may or may not be
    as serious as the CISO indicates. The vendor denies any security issue.

    COA BRAVO seems most reasonable — partial suspension so we can continue toward milestone
    while investigating. We don't want to trigger the notification obligation unnecessarily.

    Recommend COA BRAVO pending more information from the vendor.""",

    """We are addressing the issue seriously. The situation is still being investigated
    and we are not in a position to make definitive statements at this time.

    We have engaged legal counsel and are reviewing all relevant documentation.
    We will cooperate with any inquiry once our internal review is complete.

    We will brief Allied offices when we have more information.""",

    """Board Summary:

    An issue was identified with MERIDIAN and we are addressing it. The CISO is leading
    an investigation. We are working with the vendor to resolve the technical issue.

    The program is on track and we are working to minimize any impact to the milestone.
    We will provide an update once the investigation is complete.""",

    """AI Governance Recommendations:

    Organizations should have clear policies for AI system oversight.
    Vendors should be held accountable for their systems.
    Leadership should be briefed on AI system issues promptly.
    Regular audits of AI systems are recommended.
    Consider adopting industry best practices for responsible AI.""",
    ],
    }


    def build_receipts(team, idx, session_id, team_id):
        """Pre-log receipts server-side so validate_receipts() counts them."""
        count = team['receipts'][idx]
        if count == 0:
            return []
        tools = INJECT_TOOLS[idx][:count]
        return engine.pre_log_receipts(session_id, team_id, tools)


    print('=' * 72)
    print('  OPERATION MERIDIAN — TRUST NO MACHINE  ||  LIVE SIMULATION')
    print('=' * 72)
    print()

    # ── Session setup ──────────────────────────────────────────────────────────────
    sess = engine.create_session('meridian', 'meridian-sim-001')
    session_id = sess['session_id']
    print('[SESSION]  id=%d  scenario=meridian' % session_id)

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
        print('[TEAM]     %-14s  id=%-3d  role=%-12s  level=%-12s  missions=%s' % (
            t['name'], team_id, t['role'], t['academy']['level'],
            ','.join(t['academy']['completed_missions'][:2]) + ('...' if len(t['academy']['completed_missions']) > 2 else '') or 'none'
        ))

    engine.start_session(session_id)
    print()
    print('[SESSION]  ACTIVE')

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

        # Check world state before dispatch (consequence may have fired)
        with get_connection() as conn:
            ws_row = conn.execute(
                'SELECT world_state_json FROM ttx_sessions WHERE session_id=%s', (session_id,)
            ).fetchone()
        world_state = json.loads(ws_row['world_state_json'] or '{}') if ws_row else {}

        engine.dispatch_inject(inject_uuid)
        variant_flag = '  [VARIANT: meridian_suspended]' if (idx == 2 and world_state.get('meridian_suspended')) else ''
        print('┌─ INJECT %d/5: %s%s' % (idx + 1, slug.upper(), variant_flag))
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

        # Show consequence result after inject 2
        if idx == 1:
            with get_connection() as conn:
                ws_row = conn.execute(
                    'SELECT world_state_json FROM ttx_sessions WHERE session_id=%s', (session_id,)
                ).fetchone()
            ws = json.loads(ws_row['world_state_json'] or '{}') if ws_row else {}
            triggered = ws.get('meridian_suspended', False)
            print('|')
            print('|  [WORLD STATE]  meridian_suspended = %s' % str(triggered).upper())
            print('|  [INJECT 3]     %s' % (
                'PROACTIVE variant dispatched — teams acted decisively'
                if triggered else
                'DEFAULT variant — negligence inquiry incoming'
            ))

        print('|')
        print('└' + '─' * 75)
        print()

    # ── Final standings ────────────────────────────────────────────────────────────
    print()
    print('=' * 72)
    print('  FINAL STANDINGS — OPERATION MERIDIAN')
    print('=' * 72)
    totals = {name: sum(scores) for name, scores in inject_scores.items()}
    ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)

    medals = ['1st', '2nd', '3rd', '4th']
    print()
    print('  %-4s  %-14s  %6s  %s' % ('RANK', 'TEAM', 'TOTAL', 'INJECT BREAKDOWN'))
    print('  ' + '-' * 66)
    for i, (name, total) in enumerate(ranked):
        breakdown = '  '.join(['I%d:%d' % (j + 1, s) for j, s in enumerate(inject_scores[name])])
        print('  %-4s  %-14s  %6d  %s' % (medals[i], name, total, breakdown))

    print()
    print('  ACADEMY IMPACT SUMMARY')
    print('  ' + '-' * 44)
    no_acad = totals.get('OPEN ZERO', 0)
    top = ranked[0][1]
    print('  Top (Academy-trained, full receipts): %d' % top)
    print('  Bottom (no receipts, no Academy):     %d' % no_acad)
    print('  Advantage:  +%d pts (%.0f%% delta)' % (
        top - no_acad, 100 * (top - no_acad) / max(no_acad, 1)
    ))


if __name__ == "__main__":
    main()
