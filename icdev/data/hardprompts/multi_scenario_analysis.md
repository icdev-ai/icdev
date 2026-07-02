# Multi-Scenario Analysis

> Pattern for structured planning under uncertainty: triple-scenario forecasting and
> 2×2 scenario matrix. Source: adapted from "Financial Analyst" (#21) and
> "Trend Forecaster" (#44), 50 Mega-Prompts, 2026.
> Use in: planning tools, roadmap generation, financial projections, strategic decisions.

---

## Why Single-Point Forecasts Fail

Single forecasts assume certainty. When assumptions change (growth slower, key hire delayed,
competitor enters), a single-point plan has no fallback. Scenario analysis makes the
uncertainty explicit and forces "no-regret" planning.

---

## Pattern A — Triple-Scenario (Financial / Operational)

Use when planning metrics, timelines, or resource allocation.

```
CONSERVATIVE (downside):
  Assumptions: [50% of expected growth, higher churn, delayed milestones]
  Key metric at month 6: [value]
  Key metric at month 12: [value]

BASE CASE (central):
  Assumptions: [stated assumptions]
  Key metric at month 6: [value]
  Key metric at month 12: [value]

OPTIMISTIC (upside):
  Assumptions: [150% of expected growth, lower churn, accelerated milestones]
  Key metric at month 6: [value]
  Key metric at month 12: [value]
```

**Rules:**
- Every assumption must be stated explicitly — no hidden inputs.
- Flag calculations where a small input change causes a large output swing.
- Present all three scenarios as a comparison table, not separate sections.

### Sensitivity Analysis

After building the three scenarios, run one more check:

```
SENSITIVITY:
  What if growth is 50% slower? → runway impact: [N months lost]
  What if key expense increases 30%? → EBITDA impact: [value]
  What if churn doubles? → LTV:CAC ratio: [value]
```

---

## Pattern B — 2×2 Scenario Matrix (Strategic / Foresight)

Use when planning strategy under uncertain macro conditions.

### Step 1 — Identify the Two Most Uncertain Axes

Pick the two variables that are (a) most impactful AND (b) most uncertain for your domain.

```
AXIS 1: [dimension, e.g., "Regulatory adoption: slow ↔ fast"]
AXIS 2: [dimension, e.g., "Competitor response: fragmented ↔ consolidated"]
```

### Step 2 — Name Four Scenarios

```
┌─────────────────────────┬─────────────────────────┐
│  SCENARIO A             │  SCENARIO B             │
│  [name]                 │  [name]                 │
│  [3-5 sentence world]   │  [3-5 sentence world]   │
│  P = XX%                │  P = XX%                │
├─────────────────────────┼─────────────────────────┤
│  SCENARIO C             │  SCENARIO D             │
│  [name]                 │  [name]                 │
│  [3-5 sentence world]   │  [3-5 sentence world]   │
│  P = XX%                │  P = XX%                │
└─────────────────────────┴─────────────────────────┘
                           (probabilities sum to 100%)
```

For each scenario, state:
- **Leading indicators**: how to know you're entering this scenario
- **Strategic implication**: what to do if this unfolds

### Step 3 — Strategic Response Tiers

```
NO-REGRET MOVES (work across most scenarios):
  1. [action] — why it's robust
  2. [action]
  3. [action]

OPTIONS (low-cost insurance, pay off in specific scenarios):
  1. [action] — triggers if scenario A or B unfolds
  2. [action]

BIG BET (bold, high-reward if most likely scenario unfolds):
  [action] — works in [scenario], breaks in [scenario]
```

---

## Wild Cards

For any scenario analysis, add 2–3 low-probability, high-impact events:

```
WILD CARD 1: [event] — P = [1-10%], impact if happens: [description]
WILD CARD 2: [event] — P = [1-10%], impact if happens: [description]
```

---

## Prompt Template

```
[SYSTEM]
You are a strategic analyst. Before providing a single-point recommendation, apply
multi-scenario analysis:

1. TRIPLE SCENARIO: State conservative, base, and optimistic assumptions explicitly.
   Present key metrics for all three at the same time points in a comparison table.
2. SENSITIVITY: Show what happens to the critical metric when one key assumption
   changes by ±50%.
3. NO-REGRET MOVES: Identify 2-3 actions that improve outcomes across all three scenarios.
4. BIG BET: Identify one bold action that is transformative if the base case holds.

State all assumptions explicitly. Flag any calculation where a small input change
causes a large output swing. Distinguish between what is estimated vs. what is known.
```

---

## RULES

- Probabilities in a 2×2 matrix must sum to 100%.
- Every scenario must have a name (not just "Scenario A") — the name encodes the narrative.
- "No-regret moves" are not the same as "safe choices" — they must still create value.
- Never present a single forecast as THE plan. Even if the base case is most likely,
  the org must know what triggers a pivot to conservative or optimistic posture.
