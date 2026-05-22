---
name: icdev-noc
description: "Display NOC Operations Canvas status: active alarms, open incidents, SLA health, upcoming maintenance windows, and peering health summary. Use when checking carrier-grade NOC posture or preparing an operations brief."
allowed-tools: Bash, Read, Glob, Grep
---

# $icdev-noc

## What This Does
Provides a comprehensive NOC operations overview:
1. Alarm summary (active, critical, unacknowledged)
2. Open incidents (P1–P4 counts, MTTR metrics)
3. SLA health (breach count, projected breaches)
4. Upcoming maintenance windows (next 7 days)
5. BGP/peering health (from PMC canvas)
6. Auto-triage status (Genesis reflex results)

See [REFERENCE.md](REFERENCE.md) for step-by-step Python commands and display format.

## Output Flags

- `$ARGUMENTS --json` → Output all sections as JSON to stdout
- `$ARGUMENTS --alarms` → Show alarm table only
- `$ARGUMENTS --incidents` → Show open incident list
- `$ARGUMENTS --sla` → Show SLA dashboard only
- `$ARGUMENTS --peering` → Show PMC peering health only
