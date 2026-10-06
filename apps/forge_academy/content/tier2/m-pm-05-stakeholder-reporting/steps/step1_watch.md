---
ontology_id: icdev:mission:m-pm-05-stakeholder-reporting:step:1
step_class: icdev:Lesson
---

# Stakeholder Reporting Agent — Configure Automated Status Reports

PMs spend a large share of their time writing status reports. This lesson shows the pattern for an automated reporting agent: collect from your project tools, EVM data and risk register, then generate a brief tailored to each audience on a schedule.

## What You'll See

> **Illustrative walkthrough.** The project, the stakeholders and every figure below are fictional, written to show what the workflow produces. They are not the output of a live run on this platform. The *Run it for real* section names the ICDEV tools that do each part.

A first automated status report cycle for 3 stakeholders:

**Configuration (one-time setup)**

- Data sources: Jira (task status), Confluence (documentation), EVM spreadsheet
- Stakeholders: Program Executive, Contracting Officer, Technical Lead
- Frequency: weekly (Fridays at 0800 EST)

**Generated Report — Program Executive (2-page brief)**
```
Project: ICDEV Threat Intelligence System
Week: 2026-04-28 | Status: YELLOW (schedule risk)

ACCOMPLISHMENTS: Sprint 14 complete. 7 of 8 user stories delivered (88%).
                 STIG compliance scan integrated. SecDevOps pipeline active.

RISKS: Integration testing 2 weeks behind. Recovery plan submitted.
       Decision needed: descope vs. schedule slip (due COB Friday).

COST/SCHEDULE: CPI 0.82 (recovering). Replan targets CPI 0.90 by month 10.

REQUEST: Approval for 2-week schedule slip to protect technical quality.
```

**Generated Report — Contracting Officer (1-page)**
Focuses on CDRL delivery status, contract compliance, and modification request summary.

**Generated Report — Technical Lead (5-page)**
Full sprint metrics, defect trends, velocity charts, technical risk register, and architecture decision log.

Three audiences, three different reports, from one set of data. The PM's job shifts from writing to reviewing: every brief still goes out under a human's name.

## Run it for real

What ICDEV has today is the portfolio-level version of this: the **PMO weekly report** reflex (`tools/genesis/reflexes/pmo_weekly_report.py`) runs every Monday at 07:00, aggregates contract health, EVM performance, overdue deliverables and option-period countdowns, and writes an AI-narrated executive summary to the kanban board and memory. It labels its own data quality (`unmeasurable`, `synthetic`, `degraded`, `measured`), so a brief built on placeholder data says so. The underlying views are `/cpmp` and `/cpmp/reports`.

Per-audience briefs (executive, contracting officer, technical lead) as shown above are a design you would build on that data, not a switch you turn on.
