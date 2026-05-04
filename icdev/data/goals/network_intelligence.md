# Goal: Network Infrastructure Intelligence

## Purpose
Import network diagrams (Visio VSDX/VDX, Draw.io, PDF/image), build an intelligent
knowledge graph of network infrastructure, and answer questions across 13 analysis
dimensions: redundancy, EOL lifecycle, blast radius, change impact, capacity planning,
cost projection, config management, compliance/security, SLA/availability, latency,
config drift, vendor risk, and circuit/contract management.

## When to Use
- Importing network diagrams for automated analysis
- Asking "Do I need a redundant link in NYC?" type questions
- EOL device lifecycle and multi-year cost planning
- "What if device X fails?" blast radius analysis
- Capacity planning: "If I add 250 VDI users, what's impacted?"
- Config lifecycle: generate, push via Ansible, validate, drift detection
- Compliance posture assessment against NIST/STIG
- SLA/availability and latency path calculations
- Vendor concentration risk assessment
- Circuit/contract expiration and renewal planning

## Workflow

### 1. Import Diagram
```bash
python tools/network/network_ingester.py --file diagram.vsdx --project-id P1 --json
```
Supported: .vsdx, .vdx, .drawio, .xml, .pdf, .png, .jpg

### 2. Enrich Devices
```bash
python tools/network/device_manager.py --topology-id T1 --bulk-import devices.csv --json
python tools/network/device_manager.py --topology-id T1 --compute-criticality --json
```

### 3. Run Analysis (any of 13 dimensions)
```bash
python tools/network/network_intelligence.py --topology-id T1 --redundancy --json
python tools/network/network_intelligence.py --topology-id T1 --eol --years 5 --json
python tools/network/network_intelligence.py --topology-id T1 --blast-radius --device-id D1 --json
python tools/network/network_intelligence.py --topology-id T1 --capacity --users 250 --region Northeast --services vdi teams --json
python tools/network/network_intelligence.py --topology-id T1 --cost --years 5 --json
python tools/network/network_intelligence.py --topology-id T1 --config generate --json
python tools/network/network_intelligence.py --topology-id T1 --compliance --json
python tools/network/network_intelligence.py --topology-id T1 --vendor-risk --json
python tools/network/network_intelligence.py --topology-id T1 --circuits --json
python tools/network/network_intelligence.py --topology-id T1 --availability --src SRC --dst DST --json
python tools/network/network_intelligence.py --topology-id T1 --latency --src SRC --dst DST --json
```

### 4. Natural Language Query
```bash
python tools/network/network_query_router.py --topology-id T1 --query "Do I need a redundant link in NYC?" --json
```

### 5. Self-Provisioning Workflow
1. Design topology → 2. Generate configs → 3. Push via Ansible → 4. Validate → 5. Monitor drift

## Architecture Decisions
- D-NII-1: Pure Python graph algorithms (Tarjan's for articulation points) — no NetworkX dependency for core analysis
- D-NII-2: PERT Monte Carlo for cost projections — pure stdlib random.betavariate
- D-NII-3: Scanner-tier LLM (qwen3.5) for narratives — zero Claude cost
- D-NII-4: Config push generates Ansible playbook but does NOT auto-execute — human review gate
- D-NII-5: All analyses cached to ni_analyses table for dashboard retrieval

## Database Tables (network_canvas.db)
- ni_devices — Device inventory with EOL, cost, criticality
- ni_analyses — Cached analysis results
- ni_state_snapshots — Temporal network state tracking
- ni_device_configs — Config version tracking for drift detection

## Configuration
`args/network_intelligence_config.yaml`
