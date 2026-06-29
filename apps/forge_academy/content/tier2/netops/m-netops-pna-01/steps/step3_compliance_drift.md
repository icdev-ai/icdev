---
ontology_id: icdev:mission:m-netops-pna-01:step:3
step_class: icdev:configure
---

# Compliance Drift Prediction

The Compliance Drift Predictor monitors NIST 800-53 and STIG control compliance over time. It detects when controls are trending toward non-compliance before they actually fail — giving you a 48-72 hour window to remediate.

## How drift is measured

The predictor tracks:
- Control assessment scores over time (weekly snapshots)
- Rate of change (improving, stable, degrading)
- Historical failure patterns for each control family

## Using the predictor

```python
from tools.network.compliance_drift_predictor import ComplianceDriftPredictor

predictor = ComplianceDriftPredictor()
forecast = predictor.predict(
    control_families=["AC", "AU", "SC"],
    horizon_days=7,
)
# Returns list of {control_id, current_score, predicted_score, drift_rate, risk_level}
```

## Your task

Run the Compliance Drift Predictor for control families AC, AU, and SI. Identify the control most at risk of drifting below 70% in the next 7 days. What remediation action does the predictor recommend?
