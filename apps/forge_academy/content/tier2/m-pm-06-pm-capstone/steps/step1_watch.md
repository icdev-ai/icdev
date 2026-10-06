---
ontology_id: icdev:mission:m-pm-06-pm-capstone:step:1
step_class: icdev:Lesson
---

# PM Capstone — Full Capture Cycle: Opportunity to Proposal Draft

You have seen opportunity scanning, requirements decomposition, schedule and cost forecasting, and reporting. This capstone strings them into one capture cycle, from SAM.gov discovery to a proposal draft.

## What You'll See

> **Illustrative walkthrough.** The opportunity, the competitors and every figure below are fictional, written to show what the workflow produces. They are not the output of a live run on this platform. The *Run it for real* section names the ICDEV tools that do each part.

A complete capture cycle for a realistic but fictional opportunity:

**Hour 0: Opportunity Discovery**
SAM.gov scan complete. 1 high-fit opportunity identified:
```
Solicitation: W911QY-26-R-0047
Agency: Army DEVCOM
Value: $8.2M (estimated)
NAICS: 541511 (Custom Computer Programming)
Match score: 94% (keyword + incumbent analysis)
Deadline: 2026-06-30
```

**Hour 0.5: Competitive Analysis**
5 likely competitors identified from USASpending incumbent data. Your assessed differentiators:

- Delivery velocity (a claimed differentiator: back it with past-performance data, or evaluators will discount it)
- IL4/IL5 cleared facility and personnel (competitors: 2 of 5 have this)
- Existing DoD IDIQ vehicle match (reduces proposal risk for agency)

**Hour 1: Requirements Decomposition**
PWS analyzed → 63 requirements extracted → 241 tasks estimated → 18-month schedule drafted.
Estimated cost: $6.8M (about 17% below the $8.2M estimated value, which leaves room for a competitive price).

**Hour 2: Proposal Structure**
7-section proposal outline generated. Technical approach drafted: 12 pages covering architecture, staffing, management approach, past performance mapping.

**Hour 4: Complete Draft**

- Executive Summary: 1 page
- Technical Approach: 18 pages (evaluation criteria aligned)
- Management Plan: 6 pages
- Past Performance: 3 references mapped to requirements
- Price Volume: basis-of-estimate from task decomposition

Win probability estimate: 67%. Treat any PWin as a structured opinion, not a measurement: ask which inputs drove it.

## Run it for real, stage by stage

| Stage | ICDEV tool |
|---|---|
| Discovery | `tools/govcon/sam_scanner.py`, `tools/govcon/govcon_engine.py --stage discover` |
| Competitive analysis | `tools/govcon/competitor_profiler.py` |
| Requirements | `tools/govcon/requirement_extractor.py`, `tools/govcon/compliance_matrix_builder.py` |
| Bid decision | `tools/govcon/bayesian_bid_scorer.py --opportunity-id <id> --score` |
| Proposal drafting | `tools/govcon/response_drafter.py`, dashboard `/proposals` |

## Your task

For an opportunity in your own market, write down which stage you would trust the tooling to do unattended, which needs a human before the next stage starts, and why.
