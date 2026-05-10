# Stakeholder Reporting Agent — Configure Automated Status Reports

PMs spend 30-40% of their time writing status reports. ICDEV's stakeholder reporting agent collects data from your project management tools, EVM system, and risk register, then generates tailored briefs for each audience — weekly, automatically.

## What You'll See

Watch ICDEV configure and generate the first automated status report cycle for 3 stakeholders:

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

Three audiences. Three different reports. Zero PM writing time.

**Annual PM time saved:** 312 hours → redirected to technical leadership and customer engagement.
