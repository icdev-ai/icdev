# Action Ticket — Option Exercise / Expiration

> **CUI // SP-CTI**

| Field | Value |
|-------|-------|
| **Ticket** | `opt-exercise-0042` |
| **Contract** | W911NF-DEMO-24-C-0042 |
| **Contract ID** | fed203f4-6692-41c9-8ffd-530f9d7f604a |
| **Option period** | Option 1 — Services Continuation |
| **Option ID** | 5243c63d-20cb-453a-b158-00889f3ac132 |
| **Option ceiling** | $4,200,000 |
| **Exercise deadline** | **2026-06-22** |
| **Risk Tier** | CRITICAL |
| **Priority** | **CRITICAL** *(matches Risk Tier)* |
| **Source task** | pmo-opt-bb1725bf5f-d3 (chain: parent → d1 fetch → d2 log → **d3 ticket**) |

---

## ✅ INSTRUCTION: **DO** — EXERCISE OPTION 1

> **DO exercise Option 1 of W911NF-DEMO-24-C-0042 before the 2026-06-22 deadline.**

This instruction is derived **directly from the AI go/no-go API response** (Step 1):

```
GET /api/cpmp/options/5243c63d-20cb-453a-b158-00889f3ac132/recommend  →  200 OK
"recommendation": "Contract health is GREEN — recommend exercising this option.
                   Cost performance (CPI 0.99) is healthy."
```

The advisor returned a **GREEN / GO** verdict, so the action is **DO** (not "DON'T DO").

### Assessment snapshot (from API `context`)

| Metric | Value |
|--------|-------|
| Health | GREEN (94.0) |
| CPI (cost) | 0.9885 |
| SPI (schedule) | 0.9773 |
| CPARS rating | Exceptional |
| API response ID | 4a96699ab81e |
| Assessed at | 2026-06-06T11:37:10Z |

---

## Next immediate steps (per 2026-06-22 deadline)

1. **PMO / KO** — initiate the option-exercise modification (unilateral mod) for Option 1; route for KO signature. **Target: on or before 2026-06-22.**
2. **Contracts** — confirm Option 1 ceiling ($4,200,000) is funded/obligated before exercise.
3. **Notify** the program team and incumbent vendor of intent to exercise (continuity of services).
4. **Record** the exercised modification against the contract and close out the assessment chain — see follow-on task **pmo-opt-bb1725bf5f-d4** (set `assessment_completed`, status → `assessed_with_recommendation`).
5. **Calendar gate** — if not exercised by **2026-06-22**, the option lapses (expiration); escalate immediately as a CRITICAL miss.

---

## References

- AI assessment log: `Kanban/pmo-opt-bb1725bf5f/options-assessment.log`
- Backing function: `tools.govcon.option_period_tracker.ai_exercise_recommendation`
- Decision rule: AI recommends exercise → **ACTION_REQUIRED / DO** (otherwise MONITOR_ONLY / DON'T DO)
