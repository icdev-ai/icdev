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

## Steps

### 1. NOC Overview (aggregated)
```bash
python -c "
from tools.noc_canvas.db.init_db import get_connection
from tools.noc_canvas.noc_aggregator import get_noc_overview
conn = get_connection()
import json
print(json.dumps(get_noc_overview(conn), indent=2, default=str))
conn.close()
"
```

### 2. Alarm Correlator Summary
```bash
python -c "
from tools.noc_canvas.db.init_db import get_connection
from tools.noc_canvas.alarm_correlator import get_active_alarms, get_correlated_incidents
conn = get_connection()
import json
alarms = get_active_alarms(conn)
print(f'Active alarms: {len(alarms)}')
critical = [a for a in alarms if (a.get(\"severity\") or a[3] if not hasattr(a, \"keys\") else a[\"severity\"]) == \"critical\"]
print(f'Critical: {len(critical)}')
conn.close()
"
```

### 3. SLA Dashboard
```bash
python -c "
from tools.noc_canvas.db.init_db import get_connection
from tools.noc_canvas.sla_predictor import get_sla_dashboard
conn = get_connection()
import json
print(json.dumps(get_sla_dashboard(conn), indent=2, default=str))
conn.close()
"
```

### 4. Upcoming Maintenance
```bash
python -c "
from tools.noc_canvas.db.init_db import get_connection
from tools.noc_canvas.maintenance_planner import get_upcoming_windows
conn = get_connection()
import json
print(json.dumps(get_upcoming_windows(conn, days=7), indent=2, default=str))
conn.close()
"
```

### 5. Peering Health (PMC)
```bash
python -c "
from tools.pmc_canvas.db.init_db import get_connection
from tools.pmc_canvas.pmc_aggregator import get_pmc_overview
conn = get_connection()
import json
print(json.dumps(get_pmc_overview(conn), indent=2, default=str))
conn.close()
"
```

### 6. Genesis Reflex Status (last run results)
```bash
python -c "
from tools.genesis.reflexes.nocc_alarm_triage import run as alarm_triage, CADENCE_HOURS as c1
from tools.genesis.reflexes.nocc_sla_watcher import run as sla_watcher, CADENCE_HOURS as c2
from tools.genesis.reflexes.peering_health_monitor import run as peer_monitor, CADENCE_HOURS as c4
import json
print('Reflexes registered:')
print(f'  nocc_alarm_triage: {c1}h cadence')
print(f'  nocc_sla_watcher: {c2}h cadence')
print(f'  peering_health_monitor: {c4}h cadence')
"
```

## Display Format

After collecting data, format the output as:

```
╔══════════════════════════════════════════════╗
║  ICDEV™ NOC Operations Canvas                ║
║  CUI // SP-CTI                               ║
╠══════════════════════════════════════════════╣
║  Active Alarms:    <N>  (Critical: <N>)      ║
║  Open Incidents:   <N>  (P1: <N>, P2: <N>)  ║
║  SLA Breaches:     <N>                       ║
║  Next Maintenance: <window_title> @ <time>   ║
╠══════════════════════════════════════════════╣
║  BGP Peers Active:     <N>                   ║
║  RPKI Valid:           <N>%                  ║
║  Peering Requests:     <N> pending           ║
╚══════════════════════════════════════════════╝
```

## Output Flags

- `$ARGUMENTS --json` → Output all sections as JSON to stdout
- `$ARGUMENTS --alarms` → Show alarm table only
- `$ARGUMENTS --incidents` → Show open incident list
- `$ARGUMENTS --sla` → Show SLA dashboard only
- `$ARGUMENTS --peering` → Show PMC peering health only

## Error Handling

If NOCC DB is not initialized:
```
⚠ NOCC database not found. Run:
  python -c "from tools.noc_canvas.db.init_db import init_db; init_db()"
```

If PMC DB is not initialized:
```
⚠ PMC database not found. Run:
  python -c "from tools.pmc_canvas.db.init_db import init_db; init_db()"
```
