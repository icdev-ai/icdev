---
ontology_id: icdev:mission:m-netops-pna-01:step:2
step_class: icdev:coding
---

# Run a PNA Predictor

Write a Python script that calls the BGP and Capacity predictors and interprets their output.

## The real predictors

On a live ICDEV install the predictors are plain functions:

- `tools.network.bgp_predictor.predict_bgp_stability(session_key=None, network_id=None)` returns one dict per BGP session, with `stability_score`, `flap_risk` (the tier), `predicted_outage_hrs` and `confidence`
- `tools.network.capacity_predictor.predict_capacity_exhaustion(device_name=None, network_id=None)` returns one dict per interface, with `current_util_pct`, `days_to_saturation`, `risk_score` and `risk_tier`

The Network canvas serves stored predictions at `GET /network/api/network/predict/bgp` and `GET /network/api/network/predict/capacity`.

## In the exercise

The Academy sandbox cannot import `tools.*` and has no network access. The starter therefore provides **simplified stubs** that give both predictors one common field, `risk_level`, so you can rank them side by side. Use them as-is:

- `bgp_predict(as_number, prefix, lookback_hours=24)` returns one BGP prediction:

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

- `capacity_predict_top_n(n=3, lookback_hours=24)` returns a list of predictions, highest-utilization link first. Each one carries `link`, `utilization`, `risk_level` and `recommended_action`.

## Your task

Complete `run_pna_analysis()` so that it:
1. Runs the BGP predictor for AS 64512, prefix `10.0.0.0/8`, and appends `("BGP", result)`
2. Runs the Capacity predictor for the top-3 highest-utilization links and appends `("Capacity", link)` for each
3. Prints a risk summary: each predictor's `risk_level` and its `recommended_action`
4. For every high-risk finding, builds a kanban backlog card (`title`, `status: "backlog"`, `description`) and prints it. On the platform a card is seeded with `tools.kanban.task_factory.create_tasks`; the sandbox has no database, so printing it is enough
5. Returns the list of `(predictor_name, prediction)` tuples
