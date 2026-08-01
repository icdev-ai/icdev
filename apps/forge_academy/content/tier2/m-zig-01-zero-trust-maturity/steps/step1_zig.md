---
ontology_id: icdev:mission:m-zig-01-zero-trust-maturity:step:1
step_class: icdev:Lab
---

# NSA ZIG — Scoring Zero Trust Maturity

Zero Trust is not a product you buy; it is a **posture you measure and grow**. ICDEV
implements the **NSA Zero Trust Implementation Guide (ZIG, January 2026)** at `/security/zig`
inside the Security Design Canvas (`sdc`). ZIG organizes the whole journey into **7 pillars**,
**42 target capabilities**, and 91 activities (`tools/security_canvas/constants.py`:
`ZIG_PILLARS`, `ZIG_CAPABILITIES`, `ZIG_MATURITY_LEVELS`).

## The 7 pillars

| Pillar | Focus |
|--------|-------|
| **User** | Identity & Access Management (ICAM) |
| **Device** | Endpoint Security |
| **Network & Environment** | Network Segmentation & Isolation |
| **Application & Workload** | Secure Software Development & Runtime |
| **Data** | Data Protection & Governance |
| **Visibility & Analytics** | Monitoring & Threat Detection |
| **Automation & Orchestration** | Speed, Scale & Orchestrated Response |

The pillars are **not equally weighted**: identity (User) carries the most, because in Zero
Trust identity is the new perimeter. The weights in this lab's `PILLAR_WEIGHTS` sum to 1.0.

## How a pillar is scored

`tools/security_canvas/zig_pillar_scorer.py::score_pillar` blends two ratios:

```
pillar_score = 0.6 * (activities_complete / activities_total)
             + 0.4 * (capabilities_implemented / capabilities_total)
```

Activities weigh more than capabilities (0.6 vs 0.4): ZIG rewards *doing the work*, not just
declaring a capability exists. Your `pillar_score()` implements exactly this — and it must not
divide by zero when a pillar has no activities or capabilities recorded yet.

## Maturity bands and the roll-up

A pillar's numeric score maps to a **ZIG maturity level** (`ZIG_MATURITY_LEVELS`):

- `preparation` (0.00–0.24) → `basic` (0.25–0.49) → `intermediate` (0.50–0.74) → `advanced` (0.75–1.0)

`aggregate_zig_score()` rolls the 7 weighted pillars into one overall posture (normalized by
the weights actually present, so a partial assessment still produces a fair number). Finally,
`weakest_pillar()` answers the question every ISSO asks after an assessment: *where do we
invest next?* — the lowest-scoring pillar is the biggest risk-reduction opportunity.

> Note: ZIG's pillar/maturity model (`zig_*` modules) is bridged to — but distinct from — the
> older DoD **ZTA** maturity model (`tools/devsecops/zta_maturity_scorer.py`, MCP tools
> `zta_maturity_score` / `zta_posture_check`, levels *Traditional / Advanced / Optimal*). Don't
> conflate the two pillar-slug sets or their level names.

Open `step1_starter.py` and implement the four `TODO`s.
