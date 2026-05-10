# Canvas Selection

ICDEV organizes all AI capabilities into 7 design canvases. Before building a child app, you choose the canvas — each canvas determines what your app can do, what tables it gets, and how it integrates with ICDEV core. Choosing the wrong canvas is the single most expensive architectural mistake you can make at Tier 3.

## The 7 Canvases

| Canvas | Code | Purpose |
|--------|------|---------|
| **Network Data Canvas** | NDC | Network topology, traffic analysis, infrastructure monitoring |
| **Signal Data Canvas** | SDC | SIGINT, sensor streams, time-series signal processing |
| **Pattern Data Canvas** | PDC | Behavioral patterns, anomaly detection, threat intelligence |
| **Business Data Canvas** | BDC | Finance, contracts, procurement, business intelligence |
| **Domain Data Canvas** | DDC | Domain-specific knowledge bases, expertise systems |
| **Operations Data Canvas** | ODC | Workflows, task automation, operational procedures |
| **Intelligence Data Canvas** | IDC | Multi-source fusion, strategic analysis, decision support |

## Canvas Selection Criteria

Each canvas has a primary question:

- **NDC**: "Is this primarily about networks, infrastructure, or connectivity?"
- **SDC**: "Is this primarily about signals, sensors, or time-series streams?"
- **PDC**: "Is this primarily about detecting patterns, anomalies, or behavior?"
- **BDC**: "Is this primarily about business data, contracts, or financial flows?"
- **DDC**: "Is this primarily about a specific knowledge domain or expertise?"
- **ODC**: "Is this primarily about workflows, pipelines, or operational automation?"
- **IDC**: "Is this primarily about fusing intelligence from multiple sources?"

## What You'll Build

A `CanvasSelector` that recommends the right canvas given an app description:

```python
selector = CanvasSelector()
result = selector.select("Build a tool to monitor packet loss across our DoD network segments")
# → {"canvas": "NDC", "confidence": 0.9, "reasoning": "..."}
```

## Selection Logic

Each canvas has keyword signals. Score each canvas by keyword matches, return the highest scorer.

## Success Criteria

- `score_canvas()` returns a float score for a given canvas code and description
- `select()` returns the top-scoring canvas with confidence and reasoning
- Tie-breaking: when scores are equal, prefer IDC (most general)
- Descriptions with no matches return canvas="IDC" (safe default)
- `explain_canvas()` returns the human-readable description for a canvas code
