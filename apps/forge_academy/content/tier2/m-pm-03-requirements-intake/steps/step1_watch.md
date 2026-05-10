# Requirements Intake — Conversational Requirements to Decomposed Tasks

The hardest part of any GovCon project isn't the technical work — it's translating a contracting officer's vague performance work statement into specific, estimable engineering tasks. ICDEV's requirements intake engine does this in a structured conversation that takes 15 minutes instead of 3 weeks of workshops.

## What You'll See

Watch ICDEV run a requirements intake session for a hypothetical contract:

**Intake Conversation (excerpt)**
```
ICDEV: What is the primary mission capability this system must deliver?
PM:    Provide real-time threat intelligence to warfighters in degraded network conditions.

ICDEV: What are the top 3 constraints the system must operate under?
PM:    Must work offline for 72+ hours, SWaP-C under 15 watts, SECRET classification.

ICDEV: Who are the end users and what's their technical literacy?
PM:    Intelligence analysts, moderate technical literacy, trained on NIPRnet systems.
```

**Decomposition Output (47 requirements extracted)**
```
Category          Count   Priority
Connectivity       8      HIGH (offline-first architecture)
Security          11      CRITICAL (SECRET handling, encryption)
Performance        6      HIGH (72h offline, <15W)
User Interface     9      MEDIUM (analyst workflows)
Integration        7      HIGH (existing intel feeds)
Testing            6      HIGH (acceptance criteria)
```

**Task Breakdown (automated)**
47 requirements → 183 engineering tasks → 14 sprints estimated at GS-13 rate. 

**WBS auto-generated** with CDRLs mapped to each major deliverable. Ready for JIRA import.

**Risk register** pre-populated: 3 HIGH risks (offline-first architecture, SECRET facility requirements, SWaP-C compliance) with mitigation strategies drafted.
