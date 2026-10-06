---
ontology_id: icdev:mission:m-pm-03-requirements-intake:step:1
step_class: icdev:Lesson
---

# Requirements Intake — Conversational Requirements to Decomposed Tasks

The hardest part of any GovCon project isn't the technical work — it's translating a contracting officer's vague performance work statement into specific, estimable engineering tasks. ICDEV's requirements intake (RICOAS) does this as a structured conversation instead of weeks of workshops.

## What You'll See

> **Illustrative walkthrough.** The contract, the conversation and every figure below are fictional, written to show what the workflow produces. They are not the output of a live run on this platform. The *Run it for real* section names the ICDEV tools that do each part.

An intake session for a hypothetical contract:

**Intake Conversation (excerpt)**
```
ICDEV: What is the primary mission capability this system must deliver?
PM:    Provide real-time threat intelligence to warfighters in degraded network conditions.

ICDEV: What are the top 3 constraints the system must operate under?
PM:    Must work offline for 72+ hours, SWaP-C under 15 watts, SECRET classification.

ICDEV: Who are the end users and what's their technical literacy?
PM:    Intelligence analysts, moderate technical literacy, trained on NIPRnet systems.
```

**Requirements extracted (47)**
```
Category          Count   Priority
Connectivity       8      HIGH (offline-first architecture)
Security          11      CRITICAL (SECRET handling, encryption)
Performance        6      HIGH (72h offline, <15W)
User Interface     9      MEDIUM (analyst workflows)
Integration        7      HIGH (existing intel feeds)
Testing            6      HIGH (acceptance criteria)
```

**Decomposition (automated)**
The requirements are decomposed into a SAFe hierarchy (Epic > Capability > Feature > Story > Enabler) that a team can estimate and sprint-plan, and sync to Jira or another tracker.

**Readiness gate.** Before decomposition the session is scored on 7 readiness dimensions (completeness, clarity, feasibility, compliance, testability, DevSecOps, AI governance) and must reach 0.7. Gaps such as "no requirement covers SECRET data handling" are surfaced as questions back to the PM.

## Run it for real

```bash
python tools/requirements/intake_engine.py --project-id <project-id> --customer-name "PM" --customer-org "Program Office" --json   # start a session
python tools/requirements/intake_engine.py --session-id <session-id> --message "<your answer>" --json
python tools/requirements/intake_engine.py --session-id <session-id> --score-readiness --json
python tools/requirements/decomposition_engine.py --session-id <session-id> --json
```

The same conversation runs in the dashboard chat (`/chat`). Sync to Jira, ServiceNow, GitLab and DOORS lives in `tools/integration/` (for example `jira_connector.py` and `doors_exporter.py`).
