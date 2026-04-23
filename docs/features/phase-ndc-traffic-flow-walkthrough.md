# CUI // SP-CTI
# Feature: NDC Traffic Flow Walkthrough — Multi-Persona Engine

**Phase:** NDC TFW (Tasks TFW-01 through TFW-15)
**Status:** V&V Complete (TFW-15)
**Classification:** CUI // SP-CTI

---

## Overview

The Traffic Flow Walkthrough (TFW) engine generates role-specific, hop-by-hop narratives
for DoD network topology traffic flows. Given a source, destination, application type, and
classification level, TFW walks through every node on the path and produces a detailed
narrative for each of 7 stakeholder personas — from Security Engineer to CISO.

---

## Architecture

```
args/tfw_personas.yaml          ← 7 persona definitions + 5 CSP contexts + 5 classification levels
tools/network/traffic_flow.py   ← TrafficFlowEngine: create_flow(), generate_walkthrough()
tools/network/narrative_generator.py ← generate_for_persona(), generate_all()
tools/network/path_analyzer.py  ← BFS path discovery
tools/network/blueprint.py      ← Flask routes: /api/twin/<id>/traffic-flows/<id>/walkthrough
tools/dashboard/templates/network/twin.html ← Persona chip UI + TFW panel
```

---

## 7 Personas

| ID            | Name                     | Focus                                              |
|---------------|--------------------------|---------------------------------------------------|
| `seceng`      | Security Engineer        | Firewall ACLs, TLS, cert chains, zero-trust        |
| `neteng`      | Network Engineer         | Routing, BGP, IPSec VPN, failover                 |
| `cloudarch`   | Cloud Architect          | CSP services, landing zones, inter-cloud peering  |
| `compofficer` | Compliance Officer/ISSO  | NIST 800-53, FedRAMP, CMMC, STIG                  |
| `appdev`      | Application Developer    | Endpoints, latency, auth headers, token flows     |
| `missionowner`| Mission Owner            | Capability impact, risk, team contacts            |
| `ciso`        | CISO / Executive         | Risk posture, compliance status, open findings    |

---

## Classification Levels

| Level | Description                  | Encryption                  | MFA                        |
|-------|------------------------------|-----------------------------|----------------------------|
| NIPR  | NIPRNet (Unclassified/CUI)   | FIPS 140-2 Level 1+, AES-256 | CAC/PIV or FIDO2           |
| IL4   | FedRAMP-High CUI             | FIPS 140-2 Level 1, AES-256-GCM | CAC/PIV mandatory      |
| IL5   | National Security / Dedicated | FIPS 140-2 Level 2+, AES-256-GCM | CAC/PIV + hardware token |
| IL6   | SECRET                       | NSA Type 1                  | CAC/PIV + biometric        |
| SIPR  | SIPRNet (SECRET)             | NSA Type 1                  | Classified PKI             |

---

## Multi-CSP Support

| CSP Context   | Name                    | Regions                             |
|---------------|-------------------------|-------------------------------------|
| `aws_govcloud`| AWS GovCloud (US)       | us-gov-east-1, us-gov-west-1        |
| `azure_gov`   | Azure Government        | USGovVirginia, USGovArizona         |
| `gcp_gov`     | Google Assured Workloads| us-central1, us-east1               |
| `oci_gov`     | OCI Government FedRAMP  | us-langley-1, us-luke-1             |

Node type prefixes trigger CSP detection: `aws-*`, `azure-*`, `gcp-*`, `google-*`, `oci-*`.

---

## API

### POST `/api/twin/<topo_id>/traffic-flows/<flow_id>/walkthrough`

Generate multi-persona walkthrough for a traffic flow.

**Request body** (all optional):

```json
{
  "personas": ["seceng", "compofficer"],
  "classification": "IL4",
  "use_llm": false
}
```

**Response**:

```json
{
  "steps": [
    {
      "step_number": 1,
      "node_id": "n-onprem",
      "node_label": "On-Prem User",
      "action_type": "authenticate",
      "persona_responses": {
        "seceng": {
          "narrative": "...",
          "detail_json": {
            "allowed_ports": [443, 80],
            "inspection_type": "deep_packet",
            "tls_version": "TLS 1.3",
            "stig_controls": ["SRG-NET-000019"]
          }
        },
        "compofficer": {
          "narrative": "...",
          "detail_json": {
            "nist_controls": ["IA-2", "IA-2(1)", "IA-2(2)"],
            "fedramp_controls": ["IA-2(1)", "IA-2(11)"],
            "fips_compliance": "FIPS 140-2 Level 1"
          }
        }
      }
    }
  ],
  "summary": {
    "hop_count": 3,
    "csps_traversed": ["Azure Government"],
    "classification": "Impact Level 4 (CUI/FedRAMP-High)",
    "encryption": "FIPS 140-2 Level 1 (TLS 1.2+, AES-256-GCM)",
    "key_risk": "medium",
    "total_latency_ms": 22,
    "description": "3-hop flow traversing CSP IL4, Azure Government..."
  }
}
```

### GET `/api/twin/<topo_id>/persona-definitions`

Returns all 7 persona definitions from `args/tfw_personas.yaml`.

---

## DB Tables

| Table                       | Purpose                                     |
|-----------------------------|---------------------------------------------|
| `nc_traffic_flows`          | Flow definitions (src, dst, classification) |
| `nc_flow_walkthrough_steps` | Hop-by-hop steps for a flow                 |
| `nc_step_persona_responses` | Persona narrative + detail_json per step    |

---

## Persona Section

The twin page (`/network/twin/<topo_id>`) renders a **Persona Selector** panel with 7 chips:

- **SecEng** · **NetEng** · **CloudArch** · **CompOfficer** · **AppDev** · **Mission** · **CISO**

Users click chips to toggle persona inclusion before clicking **Walk Through**.
Active personas (default: all 7) control which `persona_responses` appear in the API
response and rendered accordion sections.

Each persona section in the step card shows:
- Narrative text (LLM-generated or deterministic fallback template)
- Structured detail fields (ports, NIST controls, latency, CSP service, etc.)

---

## NIST Control Mappings (Key)

| Action Type   | Controls                            |
|---------------|-------------------------------------|
| `authenticate`| IA-2, IA-2(1), IA-2(2), IA-5       |
| `encrypt_vpn` | SC-8, SC-8(1), SC-28, SC-12, SC-13  |
| `tls-inspect` | SC-7, SC-7(5), SI-3, SI-4          |
| `mfa-verify`  | IA-2(1), IA-2(11), AC-7            |
| `fips-check`  | SC-13, SC-12, SA-9                 |

---

## V&V Sign-off (TFW-15)

All 8 acceptance criteria passed:

1. `pytest tests/test_traffic_flow_walkthrough.py tests/test_tfw_personas.py -v` — green
2. Selenium E2E: 7 persona chips render; deselect 2 → 5 active; walkthrough renders correctly
3. API smoke: `compofficer.detail_json.nist_controls` non-empty
4. IL5 classification: narrative/overlay contains `FIPS 140-2 Level 2`
5. Multi-CSP: `aws-tgw` node → `cloudarch` response mentions Transit Gateway / GovCloud
6. `python tools/dx/companion.py --sync --write --json` — no errors
7. `python tools/workflow/coherence_checker.py --all --fix --gate` — passes
8. Feature doc created (this file)
