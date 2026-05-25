# Network Migration Runbook
## Cisco ASR 1001-X → Arista 7280R3-48S6

**Classification:** CUI // SP-CTI
**Site:** WAM-POP-ASH
**Generated:** 2026-05-22T19:40:18Z
**Migration ID:** mig-2a5e4aa9f5

---

## 1. Executive Summary

**Purpose:** Replace the Cisco ASR 1001-X at WAM-POP-ASH with a Arista 7280R3-48S6.

**Why:** Source device Cisco ASR 1001-X is approaching EOL (2025-11-23). Replacement is required to maintain supportability and compliance.

**What:** Migrate all services from Cisco ASR 1001-X to Arista 7280R3-48S6.

**When:** Scheduled during approved maintenance window.

**Who:** Network Engineering Team / ICDEV Migration Canvas

**Selected COA:** Side-by-Side VLAN — Run old and new devices in parallel on the same L2 VLAN domain. New device learns routes without carrying production traffic. Gradual shift. Near-zero downtime.

**Estimated Downtime:** Near-zero (sub-second hit during final preference shift)
**Risk Level:** low
**Duration:** 21 day(s)

---

## 2. Current State (AS-IS)

### 2.1 Device Inventory

| Attribute | Value |
|-----------|-------|
| Device ID | dev-edge-wam-pop-ash |
| Label | EDGE-ASH |
| Vendor | Cisco |
| Model | ASR 1001-X |
| Device Type | router |
| Site | WAM-POP-ASH |
| Rack Location | Rack-18 |
| Firmware | 16.12.5 |
| EOL Date | 2025-11-23 |
| EOS Date | 2025-11-23 |
| Criticality Score | 7.8 |
| Downstream Count | 10 |

### 2.2 Topology

```json
{
  "edges": [
    {
      "config": {
        "ip": "10.0.0.1/30",
        "vrf": "WAN-ATT"
      },
      "id": "e-edge-wam-pop-ash-isp-att",
      "label": "eBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-ash",
      "target": "isp-att"
    },
    {
      "config": {
        "ip": "10.0.0.9/30",
        "vrf": "WAN-LUMEN"
      },
      "id": "e-edge-wam-pop-ash-isp-lumen",
      "label": "eBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-ash",
      "target": "isp-lumen"
    },
    {
      "config": {
        "ip": "10.0.0.17/30",
        "vrf": "WAN-VERIZON"
      },
      "id": "e-edge-wam-pop-ash-isp-vz",
      "label": "eBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-ash",
      "target": "isp-vz"
    },
    {
      "config": {
        "ip": "10.0.0.25/30"
      },
      "id": "e-edge-wam-pop-ash-core-wam-pop-ash",
      "label": "iBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-ash",
      "target": "core-wam-pop-ash"
    },
    {
      "config": {
        "ip": "10.0.0.29/30"
      },
      "id": "e-core-wam-pop-ash-fw-wam-pop-ash",
      "label": "inside",
      "protocol": "static",
      "source": "core-wam-pop-ash",
      "target": "fw-wam-pop-ash"
    },
    {
      "config": {
        "ip": "10.0.0.33/30",
        "vrf": "WAN-ATT"
      },
      "id": "e-edge-wam-pop-dal-isp-att",
      "label": "eBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-dal",
      "target": "isp-att"
    },
    {
      "config": {
        "ip": "10.0.0.41/30",
        "vrf": "WAN-LUMEN"
      },
      "id": "e-edge-wam-pop-dal-isp-lumen",
      "label": "eBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-dal",
      "target": "isp-lumen"
    },
    {
      "config": {
        "ip": "10.0.0.49/30",
        "vrf": "WAN-VERIZON"
      },
      "id": "e-edge-wam-pop-dal-isp-vz",
      "label": "eBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-dal",
      "target": "isp-vz"
    },
    {
      "config": {
        "ip": "10.0.0.57/30"
      },
      "id": "e-edge-wam-pop-dal-core-wam-pop-dal",
      "label": "iBGP",
      "protocol": "bgp",
      "source": "edge-wam-pop-dal",
      "target": "core-wam-pop-dal"
    },
    {
      "config": {
        "ip": "10.0.0.61/30"
      },
      "id": "e-core-wam-pop-dal-fw-wam-pop-dal",
      "label": "inside",
      "protocol": "static",
      "source": "core-wam-pop-dal",
      "target": "fw-wam-pop-dal"
    },
    {
      "config": {
        "ip": "10.0.0.65/30"
      },
      "id": "e-ash-dal-backbone",
      "label": "backbone",
      "protocol": "bgp",
      "source": "edge-wam-pop-ash",
      "target": "edge-wam-pop-dal"
    }
  ],
  "nodes": [
    {
      "config": {
        "asn": 7018,
        "ip": "203.0.113.1/32"
      },
      "id": "isp-att",
      "label": "ISP-ATT",
      "type": "pe-router"
    },
    {
      "config": {
        "asn": 3356,
        "ip": "198.51.100.1/32"
      },
      "id": "isp-lumen",
      "label": "ISP-LUMEN",
      "type": "pe-router"
    },
    {
      "config": {
        "asn": 701,
        "ip": "192.0.2.1/32"
      },
      "id": "isp-vz",
      "label": "ISP-VERIZON",
      "type": "pe-router"
    },
    {
      "config": {
        "asn": 65001,
        "bfd": true,
        "hostname": "edge-wam-pop-ash",
        "ip": "10.0.0.1/32",
        "local_pref": 100,
        "model": "ASR 1001-X",
        "os": "ios_xr",
        "vendor": "Cisco"
      },
      "id": "edge-wam-pop-ash",
      "label": "EDGE-ASH",
      "type": "router"
    },
    {
      "config": {
        "asn": 65001,
        "hostname": "core-wam-pop-ash",
        "ip": "10.0.0.2/32",
        "model": "MX204",
        "os": "junos",
        "ospf_area": 0,
        "vendor": "Juniper"
      },
      "id": "core-wam-pop-ash",
      "label": "CORE-ASH",
      "type": "router"
    },
    {
      "config": {
        "hostname": "fw-wam-pop-ash",
        "ip": "10.0.0.3/32",
        "model": "FortiGate 600F",
        "os": "fortios",
        "vendor": "Fortinet"
      },
      "id": "fw-wam-pop-ash",
      "label": "FW-ASH",
      "type": "firewall"
    },
    {
      "config": {
        "asn": 65002,
        "bfd": true,
        "hostname": "edge-wam-pop-dal",
        "ip": "10.0.0.4/32",
        "local_pref": 110,
        "model": "ASR 1001-X",
        "os": "ios_xr",
        "vendor": "Cisco"
      },
      "id": "edge-wam-pop-dal",
      "label": "EDGE-DAL",
      "type": "router"
    },
    {
      "config": {
        "asn": 65002,
        "hostname": "core-wam-pop-dal",
        "ip": "10.0.0.5/32",
        "model": "MX204",
        "os": "junos",
        "ospf_area": 0,
        "vendor": "Juniper"
      },
      "id": "core-wam-pop-dal",
      "label": "CORE-DAL",
      "type": "router"
    },
    {
      "config": {
        "hostname": "fw-wam-pop-dal",
        "ip": "10.0.0.6/32",
        "model": "PA-3220",
        "os": "panos",
        "vendor": "Palo Alto"
      },
      "id": "fw-wam-pop-dal",
      "label": "FW-DAL",
      "type": "firewall"
    }
  ]
}
```

### 2.3 Config Summary

```
!! ============================================================
!! ICDEV(tm) Network Canvas -- Generated Configuration
!! Device   : edge-wam-pop-ash
!! Topology : WAM-BGP-42
!! OS       : Cisco IOS-XR (Carrier / Service Provider)
!! Generated: 2026-05-22T19:06:29Z
!! WARNING  : Review all TODO comments before deploying.
!! ============================================================
!!
hostname edge-wam-pop-ash
!!
logging console informational
logging buffered 2097152
!!
interface Loopback0
 description Management Loopback
 ipv4 address 10.0.0.1 255.255.255.255
 no shutdown
!
interface HundredGigE0/0/0/1
 description eBGP — Link to ISP-ATT
 ipv4 address 10.0.0.1 255.255.255.252
 no shutdown
!
interface HundredGigE0/0/0/2
 description eBGP — Link to ISP-LUMEN
 ipv4 address 10.0.0.9 255.255.255.252
 no shutdown
!
interface HundredGigE0/0/0/3
 description eBGP — Link to ISP-VERIZON
 ipv4 address 10.0.0.17 255.255.255.252
 no shutdown
!
interface HundredGigE0/0/0/4
 description iBGP — Lin
```

---

## 3. Target State (TO-BE)

### 3.1 Replacement Device

| Attribute | Value |
|-----------|-------|
| Vendor | Arista |
| Model | 7280R3-48S6 |
| Throughput | 4.8 Gbps |
| Rack Units | 1U |
| Replacement Cost | $32,000 |

### 3.2 Port Mapping

| Source Interface | Target Interface | Notes |
|------------------|-------------------|-------|

| Loopback0 |  | OK |

| HundredGigE0/0/0/1 |  | speed mismatch |

| HundredGigE0/0/0/2 |  | speed mismatch |

| HundredGigE0/0/0/3 |  | speed mismatch |

| HundredGigE0/0/0/4 |  | speed mismatch |

| HundredGigE0/0/0/5 |  | speed mismatch |


---

## 4. Migration Strategy

**Selected Course of Action:** Side-by-Side VLAN

Run old and new devices in parallel on the same L2 VLAN domain. New device learns routes without carrying production traffic. Gradual shift. Near-zero downtime.

**Justification:** COA-3 (Side-by-Side VLAN) recommended for critical production devices with low tolerance for downtime. COA-2 (Phased) for moderate risk tolerance. COA-1 (Rip & Replace) only when maintenance windows are long and rollback hardware is standby.

---

## 5. Phased Cutover Plan


### Phase 1: Pre-Work & Parallel Wiring

**Duration:** 12 hour(s)

**Actions:**

- Order and rack target hardware

- Apply base config (hostname, Mgmt, AAA, SNMP, NTP)

- Verify out-of-band console + management connectivity

- Physically connect new device alongside old (not inline) — same VLAN trunk

- Configure identical SVIs on new device with unique but valid IPs in same subnet

- Configure HSRP/VRRP on both devices with same VIP; new device as standby (lower priority)


**Validation:**
Both devices see HSRP/VRRP hello packets; new device shows Standby state; no IP conflict.

**Rollback:**
Disconnect new device trunk links; remove SVIs.


### Phase 2: Learning & Validation

**Duration:** 48 hour(s)

**Actions:**

- Allow new device to form routing adjacencies (passive/no-export)

- Mirror production traffic to new device port (SPAN/tap) for validation

- Run synthetic traffic through new device without affecting production paths


**Validation:**
Route table converged; BGP/OSPF neighbors Established; no drops on mirrored traffic.

**Rollback:**
Disable routing adjacencies on new device; revert to pure standby.


### Phase 3: Gradual Traffic Shift

**Duration:** 24 hour(s)

**Actions:**

- Raise HSRP/VRRP priority on new device to make it Active for one VLAN at a time

- Or: shift BGP route preference (local-pref / MED) per peer to new device

- Monitor end-to-end latency and loss for 4h per shift


**Validation:**
Active gateway transitions to new device; ARP/MAC tables update; sub-second hit.

**Rollback:**
Lower new device HSRP/VRRP priority; traffic immediately returns to old device.


### Phase 4: Drain Old & Decomm

**Duration:** 8 hour(s)

**Actions:**

- Once all traffic shifted, set old device SVIs to shutdown

- Remove old device from routing adjacencies

- Keep old device racked & powered 30 days for emergency rollback


**Validation:**
All traffic confirmed on new device; zero packets ingress on old device SVIs.

**Rollback:**
Re-enable old device SVIs; restore HSRP/VRRP priority; traffic returns instantly.



---

## 6. Configuration Mapping

### 6.1 Old Port → New Port

See Port Mapping table in Section 3.2.

### 6.2 Config Diff

```diff
Port map applied: 0 interfaces
Unmapped interfaces: HundredGigE0/0/0/1, HundredGigE0/0/0/2, HundredGigE0/0/0/3, HundredGigE0/0/0/4, HundredGigE0/0/0/5
```

---

## 7. Validation Procedures

### 7.1 Pre-Cutover Checklist


- [ ] Capture baseline traffic counters

- [ ] Snapshot routing table

- [ ] Verify BGP neighbor states

- [ ] Backup running config

- [ ] Confirm management reachability


### 7.2 During Cutover Checklist


- [ ] Verify link up on migrated ports

- [ ] Confirm BGP re-establishment

- [ ] Ping all BGP peers

- [ ] Verify OSPF/ISIS adjacency

- [ ] Traceroute path validation


### 7.3 Post-Cutover Checklist


- [ ] Zero CRC errors after 1h

- [ ] Traffic within ±10% baseline

- [ ] No critical alarms

- [ ] NOC sign-off

- [ ] Update CMDB/NetBox


---

## 8. Rollback Procedures


### Phase 1 Rollback

Disconnect new device trunk links; remove SVIs.


### Phase 2 Rollback

Disable routing adjacencies on new device; revert to pure standby.


### Phase 3 Rollback

Lower new device HSRP/VRRP priority; traffic immediately returns to old device.


### Phase 4 Rollback

Re-enable old device SVIs; restore HSRP/VRRP priority; traffic returns instantly.



**Emergency Rollback Contact:** NOC Lead / On-call Network Architect

---

## 9. Risk Register

| Risk | Likelihood | Impact | Mitigation | Owner |
|------|-----------|--------|------------|-------|

| BGP session failure during cutover | Medium | High | Pre-stage BGP config; use passive mode; verify prefix counts. | Network Engineering |

| Optic incompatibility on target | Low | Medium | Verify ports_json against source optics before ordering. | Hardware Team |

| Insufficient target FIB capacity | Low | High | Compare routing_table_size in hardware profiles. | Architecture Team |


---

## 10. ERB / ARB Q&A


**Q: Why this device model?**

A: The Arista 7280R3-48S6 was selected by the replacement recommender based on hardware parity (0.99), feature parity (0.5833), and cost score (1.0).


**Q: What if the new device fails during cutover?**

A: Rollback procedures are defined per phase. The source device remains racked and powered for 30 days as an emergency fallback.


**Q: How do we verify traffic is flowing correctly?**

A: Pre-, during-, and post-cutover checklists include ping tests, route-table comparison, traffic counter validation, and NOC sign-off.


**Q: What is the impact on existing SLAs?**

A: Selected COA (Side-by-Side VLAN) estimates downtime of Near-zero (sub-second hit during final preference shift). SLA impact is minimized by phased or side-by-side strategies.


**Q: How does this affect cross-domain traffic?**

A: Upstream/downstream devices maintain existing routing adjacencies. Traffic shift is controlled via routing metrics or HSRP/VRRP priority.


**Q: What STIG/cATO changes are needed?**

A: Post-migration STIG scan required. Any new CAT1 findings from the alignment analyzer must be remediated before cATO re-authorization.



---

## Appendix A: Alignment Analysis

**Overall Alignment Score:** 61% (FAIL)


### bgp

- **Status:** FAIL
- **Score:** 33%
- **Rationale:** Missing: BGP MD5 Authentication; Missing: BGP BFD; Missing: BGP Route Dampening
- **Recommendations:**

  - Add BGP MD5 Authentication: BGP sessions without MD5/TCP-AO authentication are vulnerable to session hijacking.

  - Add BGP BFD: BFD provides sub-second failure detection for BGP peering sessions.

  - Add BGP Route Dampening: Route dampening suppresses unstable routes from propagating.



### management

- **Status:** WARN
- **Score:** 75%
- **Rationale:** Missing: NTP Configuration
- **Recommendations:**

  - Add NTP Configuration: Clock drift breaks certificate validation, logging correlation, and event sequencing.



### interfaces

- **Status:** PASS
- **Score:** 100%
- **Rationale:** All checked best practices present.
- **Recommendations:**




---

*Generated by ICDEV™ Network Design Canvas — Phase 6 Migration Runbook Generator*