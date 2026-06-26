---
ontology_id: icdev:mission:m-netops-pna-01:step:1
step_class: icdev:Lesson
---

# Predictive Network Analytics: 6 ML Predictors

ICDEV's Predictive Network Analytics (PNA) module provides 6 machine learning models for proactive network management.

## The 6 PNA Predictors

| Predictor | What it forecasts | ML approach |
|-----------|------------------|-------------|
| BGP Predictor | BGP route instability, prefix hijacks | Time-series anomaly detection |
| Capacity Predictor | Link saturation, bandwidth exhaustion | Regression + seasonal decomposition |
| Compliance Drift Predictor | NIST/STIG control drift over time | Classification + trend analysis |
| Supply Chain Risk | Vendor EOL, firmware CVE emergence | Graph-based risk scoring |
| Latency Predictor | P95 latency degradation | ARIMA forecasting |
| Failure Predictor | Hardware failure probability | Survival analysis |

## How predictions work

Each predictor follows the same pattern:
1. **Ingest**: pull telemetry from the network canvas DB
2. **Feature extraction**: transform raw telemetry into ML features
3. **Predict**: run the model, output probability + confidence + horizon
4. **Alert**: if probability > threshold, promote to kanban `suggested`

## Your task

Navigate to `/network/pna` in the ICDEV dashboard. Find the BGP Predictor. What telemetry inputs does it use? What threshold triggers a "high risk" alert?
