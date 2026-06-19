# Action Ticket — Option Exercise (Option 2)

> **CUI // SP-CTI**

| Field | Value |
|-------|-------|
| **Ticket** | `opt-exercise-0042-opt2` |
| **Contract** | W911NF-DEMO-24-C-0042 |
| **Contract ID** | fed203f4-6692-41c9-8ffd-530f9d7f604a |
| **Option period** | Option 2 — Enhanced Capabilities |
| **Option ID** | 9376f12c-313c-4f24-912c-635cc72e413a |
| **Option ceiling** | $4,500,000 |
| **Exercise deadline** | 2026-07-15 |
| **Assessment chain** | pmo-opt-9cabf1a414 (assessed_with_recommendation) |

---

## ✅ EXECUTED — OPTION 2 EXERCISED (2026-06-06)

> **STATUS: DONE.** Decision authority **PMO/KO (sovanna.chuon@gmail.com)** approved the GREEN/GO recommendation and exercised Option 2 on **2026-06-06**, ahead of the 2026-07-15 deadline (39 days to spare).
> Option status flipped `pending → exercised`; immutable audit row written (`action=option_exercised`, `option_id=9376f12c-313c-4f24-912c-635cc72e413a`). Recorded via `tools.govcon.option_period_tracker.exercise_option`.

### Recommendation basis (AI go/no-go, `GET /api/cpmp/options/9376f12c.../recommend`)

> "Contract health is GREEN — recommend exercising this option. Cost performance (CPI 0.99) is healthy."

| Metric | Value |
|--------|-------|
| Health | GREEN (94.0) |
| CPI (cost) | 0.99 |
| SPI (schedule) | 0.98 |
| CPARS rating | Exceptional |

---

## Follow-on (human contracting actions)

1. **PMO / KO** — issue the unilateral mod for Option 2 (Enhanced Capabilities); route for KO signature.
2. **Contracts** — confirm Option 2 ceiling ($4,500,000) is funded/obligated before exercise.
3. **Notify** program team + incumbent vendor of enhanced-capabilities scope.

---

## References

- Backing function: `tools.govcon.option_period_tracker.exercise_option`
- Companion ticket (Option 1): `.claude/tickets/opt-exercise-0042.md`
