---
ontology_id: icdev:mission:m-pm-04-schedule-cost:step:1
step_class: icdev:Lesson
---

# Schedule & Cost Intelligence — EVM with AI Prediction

Earned Value Management tells you where you were. Forecasting on top of EVM tells you where you are going, early enough to act. ICDEV's contract performance tools (CPMP) record EVM periods and add forecasting and CPARS prediction on top.

## What You'll See

> **Illustrative walkthrough.** The $4.2M contract and every figure below are fictional, written to show what the workflow produces. They are not the output of a live run on this platform. The *Run it for real* section names the ICDEV tools that do each part.

An analysis of EVM data for a $4.2M software development contract:

**Current EVM Status (Month 8 of 24)**
```
Metric                  Value       Status
Planned Value (PV):     $1,750,000
Earned Value (EV):      $1,512,000
Actual Cost (AC):       $1,847,000
Schedule Variance (SV): -$238,000   ⚠ BEHIND
Cost Variance (CV):     -$335,000   ✗ OVER BUDGET
CPI (Cost Performance): 0.82        ✗ Red    (red below 0.85)
SPI (Schedule Perf.):   0.86        ⚠ Yellow (yellow below 0.95)
```

**Forecast**
Estimate at Completion from the current CPI: BAC ÷ CPI = $4.2M ÷ 0.82 ≈ **$5.1M** (about 22% over budget). A Monte Carlo forecast puts a range around that number instead of a single point.
Root cause (from the WBS-level periods): integration testing underestimated.

**Early warning (missed)**
In month 6 the integration WBS element's CPI had already crossed the yellow threshold. Addressed then, a replan was cheap; now it is a conversation with the contracting officer.

**Recommended Recovery Plan**
3 options modeled:

1. Descope 2 features → lower EAC, on-time delivery (recommended)
2. Add 2 engineers → higher EAC, on-time delivery
3. Accept slip → lowest EAC, 6-week schedule slip

**CPARS prediction:** current trajectory → "Satisfactory"; Option 1 → "Very Good".

## Run it for real

```bash
python tools/govcon/evm_engine.py --record --contract-id <id> --wbs-id <id> --period-date 2026-08 --pv 1750000 --ev 1512000 --ac 1847000 --json
python tools/govcon/evm_engine.py --aggregate --contract-id <id> --json
python tools/govcon/evm_engine.py --forecast --contract-id <id> --iterations 10000 --json   # Monte Carlo EAC
python tools/govcon/cpars_predictor.py --predict --contract-id <id> --json
```

The CPI/SPI colour thresholds (yellow below 0.95, red below 0.85) are `cpmp` settings in `args/govcon_config.yaml`. The dashboard view is `/cpmp`.

**Check the math yourself:** recompute SV (EV − PV), CV (EV − AC), CPI (EV ÷ AC) and SPI (EV ÷ PV) from the three dollar figures. Then work out the EAC if the remaining work is done at budget rate instead of at the current CPI: EAC = AC + (BAC − EV). Which forecast would you brief, and why?
