---
ontology_id: icdev:mission:m-netops-pna-01:step:3
step_class: icdev:configure
---

# Compliance Drift Prediction

The Compliance Drift Predictor watches DISA STIG compliance on network devices. It flags devices whose posture is sliding *before* a failed audit finds them.

## How drift is measured

`tools/network/compliance_drift_predictor.py` runs ten STIG checks against each device's running config. Three of them are critical (`V-220518` SSH v2, `V-220519` no telnet, `V-220521` no plain HTTP server). For each device it records:
- `current_score`: the fraction of checks that pass (0.0-1.0)
- `drift_delta`: the change since that device's previous assessment
- `critical_controls_failing` and `failing_controls`
- `risk_score` and `risk_tier`: the base risk rises as `current_score` falls below 0.85, 0.70 and 0.50, with penalties for a negative drift and for each failing critical check

Every run is appended to the `nc_compliance_drift` table.

## Using the predictor

```python
from tools.network.compliance_drift_predictor import (
    predict_compliance_drift, get_compliance_drift,
)

# Run a fresh assessment (writes nc_compliance_drift rows)
result = predict_compliance_drift(network_id=None)
# {"devices_assessed": N, "predictions": [{device_name, current_score, drift_delta,
#   failing_controls, critical_controls_failing, risk_score, risk_tier, stig_details}, ...]}

# Read recent assessments without re-running
rows = get_compliance_drift(device_name=None, framework="DISA_STIG", limit=50)
```

The Network canvas serves the same data at `GET /network/api/network/predict/compliance`. It also feeds the **Compliance Drift** panel on `/network/network/predictive-analytics`, which stays empty until the predictor has run against a network.

## Your task

Using the code above and `_STIG_CHECKS` in `compliance_drift_predictor.py` (or the Compliance Drift panel, if your install has data), work out the following for a device whose config lacks `ip ssh version 2` and `aaa new-model`:
1. Which of its failing checks are critical, and what is its `current_score` if every other check passes?
2. Which `risk_tier` would `_compute_drift_risk()` give it on a first assessment (no previous score, so `drift_delta` is 0)?
3. Which config lines would fix it? Each check's lambda names the line it looks for.

This step has no form fields. When you have your answers, click **Configure** to record that you completed the review.
