# Anomaly and Trend Detection for Analysts

Pattern detection is the intelligence analyst's core discipline. AI does not replace that discipline — it scales it. A human analyst can monitor dozens of indicators. An AI-assisted system can monitor thousands.

## Types of Patterns AI Can Surface

| Pattern Type | Description | Analyst Use Case |
|---|---|---|
| **Anomalies** | Values that deviate significantly from established norms | Unusual procurement activity, unexplained spending spikes |
| **Trends** | Directional movement in a metric over time | Competitor win rate increasing in a specific domain |
| **Clusters** | Groups of events or entities that co-occur more than expected | Multiple vendors winning contracts at the same agency in a short window |
| **Leading indicators** | Early signals that historically precede a significant event | Staffing changes before a major contract pursuit |

## Rule-Based vs. ML-Based Detection

### Rule-Based Detection
Uses explicit thresholds you define. Fast to configure, transparent, auditable.
- **Best for:** Known threats, compliance monitoring, clear business rules
- **Limitation:** Cannot detect novel patterns you did not anticipate

### ML-Based Detection
Learns baseline behavior and flags deviations without explicit rules. Can find what you did not know to look for.
- **Best for:** Large, complex datasets with non-obvious patterns
- **Limitation:** Requires training data, harder to explain outputs, higher false positive risk initially

## When to Use Each

Start with rule-based detection for any use case where you can define the condition clearly. Add ML-based detection when your rule library stops catching meaningful signals and false negatives become costly.

---

**Your task:** Identify one metric in your domain that you currently monitor manually — a number, a rate, or a count that you check periodically and that matters when it moves unexpectedly. This is your anomaly detection target for Step 2.
