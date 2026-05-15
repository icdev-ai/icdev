---
ontology_id: icdev:mission:m-isso-04-audit-trail:step:1
step_class: icdev:Lesson
---

# Audit Trail Intelligence

Your audit trail holds every privileged action, configuration change, and authentication event across your system boundary. Manually reviewing thousands of daily entries is impossible — ICDEV's NLQ engine lets you ask questions in plain English and get answers in seconds.

## What You'll See

Watch ICDEV's audit trail intelligence query three real scenarios:

**Query 1: Privileged access after hours**
```
Natural language: "Who accessed privileged accounts between 10 PM and 6 AM this week?"
```
Result: 4 after-hours logins detected — 3 automated service accounts (expected), 1 admin account (ANOMALY — no change ticket found). Alert generated.

**Query 2: Failed authentication spike**
```
Natural language: "Show me authentication failures exceeding 5 attempts in any 10-minute window"
```
Result: 2 events in 72-hour window. Event at 2026-04-29 14:22 UTC: 23 failed attempts from 10.0.1.47 → brute force candidate. Auto-escalated to incident queue.

**Query 3: Configuration change delta**
```
Natural language: "What security configurations changed in the past 30 days that weren't in a change window?"
```
Result: 7 changes identified. 6 within approved windows. 1 unauthorized: firewall rule modified 2026-04-18 03:47 UTC — no change ticket, no approval. POA&M entry auto-drafted.

## What This Means for Your Role

Continuous monitoring means continuous evidence. Every query you run produces a timestamped record automatically added to your audit evidence folder — ready for your next assessment.
