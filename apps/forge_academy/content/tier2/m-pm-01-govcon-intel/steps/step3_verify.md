---
ontology_id: icdev:mission:m-pm-01-govcon-intel:step:3
step_class: icdev:Lesson
---

# Verify: Review Opportunity Results

Review your first scan results before activating the daily cadence.

## What to look for

**Match score** — The percentage of your capability keywords found in the opportunity synopsis + attached documents. A score above 0.75 means strong capability alignment. Scores below 0.5 are usually false positives.

**Days to due date** — The solicitation response deadline. Less than 7 days: skip unless you already have a team assembled. 14–30 days: standard pursuit window. 30+ days: ideal — enough time for thorough color reviews.

**Contract type** — Understanding the vehicle:
- **IDIQ/GWAC**: On-ramp opportunity — winning gets you a spot to compete for task orders
- **FFP**: Firm-Fixed Price — bid a fixed cost, you own the risk
- **T&M**: Time & Materials — lower risk, often used for R&D and advisory work
- **SBIR**: Small Business only — check your size standard

**Set-aside status** — If `set_aside: 8(a)` or `set_aside: SDVOSB`, confirm your firm qualifies before investing pursuit resources.

## Tuning your results

If the results feel off:
- Too many false positives → raise the `high_fit_threshold` to 0.80+
- Missing obvious fits → add more specific keywords, especially program names
- Wrong agencies → tighten the target agency list

Once results look right, confirm to activate daily scanning.
