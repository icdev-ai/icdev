---
ontology_id: icdev:mission:m-netops-pna-01:step:1
step_class: icdev:Lesson
title: Predictive Network Analytics: 6 Predictors
---

# Predictive Network Analytics: 6 Predictors

ICDEV's Predictive Network Analytics (PNA) module ships six predictive risk scorers for proactive network management. They live in `tools/network/` and are served by the Network Design Canvas.

## The 6 PNA predictors

| Predictor | Module and entry point | What it scores |
|-----------|------------------------|----------------|
| Device EOL / EOS risk | `eol_predictor.py` `predict_eol_risk()` | Days to vendor end-of-sale/support, plus active CVEs on the device |
| BGP session instability | `bgp_predictor.py` `predict_bgp_stability()` | Flap counts over 24h / 7d and the session state |
| Compliance drift (DISA STIG) | `compliance_drift_predictor.py` `predict_compliance_drift()` | STIG checks run against each device config, and the change since the last run |
| Capacity exhaustion | `capacity_predictor.py` `predict_capacity_exhaustion()` | Interface utilization and its 7-day trend, projected days to 90% saturation |
| Change failure probability | `change_failure_predictor.py` `predict_change_failure()` | Blast radius and concurrent changes for a planned change |
| Supply chain risk | `supply_chain_risk_scorer.py` `score_supply_chain_risk()` | Per-vendor CVE density, CISA KEV exploitation and critical-CVE ratio |

These are **deterministic, weighted risk scores**, not trained ML models. Each formula is in its module's docstring, so an assessor can read exactly why a device scored "high". That transparency matters in a regulated environment.

## How a prediction is produced

Each predictor follows the same pattern:
1. **Ingest**: pull inventory and telemetry through Forward Networks NQE (`FallbackNQEClient`), plus history from the network canvas DB.
2. **Score**: apply the weighted formula, producing a 0-1 score (BGP reports the inverse, a `stability_score`).
3. **Tier**: map the score to `critical` / `high` / `medium` / `low`.
4. **Record**: write the prediction to its `nc_*` table (for example `nc_bgp_predictions`) so the dashboard and trend views can read it.

## Your task

Open `tools/network/bgp_predictor.py` and read `_compute_bgp_score()`. Which inputs does the BGP predictor use, and what puts a session in the "high" tier? The dashboard page **Predictive Network Analytics** (`/network/network/predictive-analytics` on the Network canvas) shows the six panels. On an install where the predictors have not run against a network yet, the panels are empty and the code is the reference.

Hint: in `bgp_predictor.py`, every flap in the last 24h costs 0.08 of stability (capped at 0.64). Up to 0.20 more comes from the 7-day trend, and a session that is not `established` loses another 0.20. A `stability_score` at or below 0.50 is `high`; at or below 0.30 it is `critical`.
