---
ontology_id: icdev:mission:m-analyst-05-capstone:step:1
step_class: icdev:Lesson
---

# Full Intelligence Cycle: Collect → Detect → Report → Predict

The intelligence cycle is the foundational framework for all analytical tradecraft. AI does not replace the cycle — it compresses each phase and increases throughput at every stage.

## The Four-Phase Cycle

| Phase | Traditional Method | AI-Augmented Method |
|---|---|---|
| **Collect** | Manual source monitoring, periodic data pulls, human tipping | Automated agents monitoring sources continuously, alerting on new signals in near real-time |
| **Detect** | Analyst reviews data for anomalies and patterns | Anomaly detectors flag deviations against baseline automatically; analyst reviews flagged items only |
| **Report** | Analyst drafts report from scratch each cycle | RAG pipeline drafts from indexed sources; analyst reviews, edits, and adds judgment layer |
| **Predict** | Analyst projects forward based on experience and pattern recognition | Trend forecaster applies historical patterns to current signals; analyst evaluates model outputs |

## How the Four Missions Connect

In this capstone, you are wiring together the capabilities from Missions 01 through 04:

- **Mission 01** — Your intel agent is the Collect layer
- **Mission 03** — Your anomaly detector is the Detect layer
- **Mission 04** — Your report generator is the Report layer
- **Mission 02** — Your RAG pipeline supports both Detect and Report layers

Together, they form a continuous intelligence pipeline that runs with minimal manual intervention between collection events and finished products.

## What AI Cannot Replace

AI cannot determine the significance of a signal. It cannot weigh a source's credibility based on contextual judgment. It cannot decide that a pattern is operationally relevant versus statistically interesting. These remain exclusively analyst functions.

---

**Your task:** Before Step 2, review your outputs from Missions 01 through 04. Identify any gaps — places where you did not complete a configuration or left a question unanswered. Those gaps will affect your pipeline design in Step 2.
