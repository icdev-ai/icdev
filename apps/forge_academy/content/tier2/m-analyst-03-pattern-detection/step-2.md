---
ontology_id: icdev:mission:m-analyst-03-pattern-detection:step:2
step_class: icdev:Lesson
---

# Set Your Detection Parameters

An anomaly detector with no parameters configured will alert on everything — or nothing. The configuration step forces you to make explicit decisions about sensitivity, tolerance, and response.

## Five-Parameter Configuration Walkthrough

### 1. Baseline Period
Define the "normal" window your system learns from. This is the historical range used to establish expected behavior.

**Decision:** How far back should normal go? (Options: 30 days / 90 days / 12 months / same period last year)
**Rule of thumb:** Use 90 days for volatile metrics, 12 months for seasonal metrics.

### 2. Key Metric to Monitor
Name the single metric this detector watches. Be specific: not "spending" but "award count per NAICS code per quarter for competitors in the target domain."

**One detector = one metric.** Multiple metrics require multiple detectors.

### 3. Sensitivity Threshold
Define how far from baseline a value must deviate to trigger review.

**Examples:**
- Flag if current value is > 2 standard deviations from 90-day average
- Flag if week-over-week change exceeds 25%
- Flag if value enters the top or bottom 5% of historical distribution

### 4. Alert Criteria
Not all anomalies require immediate action. Define the conditions that trigger: (a) real-time alert, (b) daily summary, (c) weekly digest only.

### 5. False Positive Tolerance
How many false alerts per week is acceptable before you reduce sensitivity?

**Conservative:** Accept 1–2 false positives per week, higher sensitivity
**Aggressive:** Accept ≤ 1 per month, lower sensitivity, higher false negative risk

---

## Configuration Template

| Parameter | Your Configuration |
|---|---|
| Baseline period | |
| Metric to monitor | |
| Sensitivity threshold | |
| Alert criteria | |
| False positive tolerance | |

---

**Your task:** Complete the configuration template for the metric you identified in Step 1. A completed template is ready for implementation handoff.
