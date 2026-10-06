---
ontology_id: icdev:mission:m-netops-pna-01:step:2
step_class: icdev:coding
---

# Run a PNA Predictor

Write a Python script that calls the BGP and Capacity predictors and interprets their output.

## Predictor output

A BGP prediction looks like this:

```python
# {
#   "instability_probability": 0.73,
#   "confidence": 0.85,
#   "horizon_hours": 4,
#   "risk_level": "high",
#   "features": {...},
#   "recommended_action": "Pre-stage backup route for prefix 10.0.0.0/8"
# }
```

A Capacity prediction (one per link) carries `link`, `utilization`, `risk_level` and `recommended_action`.

## In the exercise

The Academy sandbox cannot import `tools.*` and has no network access. The starter therefore provides stub predictors with the shapes above — use them as-is:

- `bgp_predict(as_number, prefix, lookback_hours=24)` — one BGP prediction
- `capacity_predict_top_n(n=3, lookback_hours=24)` — a list of predictions, highest-utilization link first

## Your task

Complete `run_pna_analysis()` so that it:
1. Runs the BGP predictor for AS 64512, prefix `10.0.0.0/8`, and appends `("BGP", result)`
2. Runs the Capacity predictor for the top-3 highest-utilization links and appends `("Capacity", link)` for each
3. Prints a risk summary: each predictor's `risk_level` and its `recommended_action`
4. For every high-risk finding, builds a kanban backlog card (`title`, `status: "backlog"`, `description`) and prints it — on the platform you would POST it to the kanban API; the sandbox has no network
5. Returns the list of `(predictor_name, prediction)` tuples
