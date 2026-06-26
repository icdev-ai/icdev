---
ontology_id: icdev:mission:m-netops-pna-01:step:2
step_class: icdev:coding
---

# Run a PNA Predictor

Write a Python script that calls the BGP Predictor and interprets its output.

## Predictor API

```python
from tools.network.bgp_predictor import BGPPredictor

predictor = BGPPredictor()
result = predictor.predict(
    as_number=64512,
    prefix="10.0.0.0/8",
    lookback_hours=24,
)
# Returns:
# {
#   "instability_probability": 0.73,
#   "confidence": 0.85,
#   "horizon_hours": 4,
#   "risk_level": "high",
#   "features": {...},
#   "recommended_action": "Pre-stage backup route for prefix 10.0.0.0/8"
# }
```

## Your task

Write a script that:
1. Runs the BGP Predictor for AS 64512
2. Runs the Capacity Predictor for the top-3 highest-utilization links
3. Prints a risk summary: which predictors flagged high risk, and what action do they recommend?
4. If any predictor flags high risk, write the finding to the kanban backlog via the kanban API
