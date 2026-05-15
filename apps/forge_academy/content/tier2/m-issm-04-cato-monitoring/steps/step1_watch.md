---
ontology_id: icdev:mission:m-issm-04-cato-monitoring:step:1
step_class: icdev:Lesson
---

# cATO Monitoring — Continuous Authorization Setup

Traditional ATOs expire. Continuous Authorization to Operate (cATO) never does — because your security posture is always visible and always evidenced. In this mission you'll see ICDEV configure a cATO monitoring posture for your system portfolio.

## What You'll See

Watch ICDEV configure and activate cATO monitoring for 3 systems:

**Posture Dashboard (real-time)**
```
ICDEV-Prod:    ██████████ 94%  AUTHORIZED   Last evidence: 2h ago
ICDEV-Dev:     ████████░░ 82%  AUTHORIZED   Last evidence: 6h ago
ICDEV-Test:    ████░░░░░░ 41%  CONDITIONAL  Last evidence: 8 days ago
```

**Evidence Cadence Configuration**
- Automated evidence: every 6 hours (scan results, config pulls, log summaries)
- Manual evidence checkpoints: every 30 days (policy reviews, access recertification)
- Threshold: <70% posture score → ISSO alert; <50% → automatic escalation to ISSM

**Drift Detection**
Since last assessment: 2 control changes detected on ICDEV-Dev.
- AC-2: account count changed (+3 accounts) — auto-recertification triggered
- SI-4: monitoring config modified — evidence re-collected and compared to baseline

**Evidence Cadence Report**
Automated: 847 evidence items collected this month. Manual gaps: 2 (scheduled for Thursday). ISCM program score: 91/100 → On track for annual review.
