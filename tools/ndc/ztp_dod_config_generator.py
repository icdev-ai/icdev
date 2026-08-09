"""ZTP DoD Config Generator — BCAP/NIPR/JWICS/CSP Demo Lab.

Generates all provisioning artifacts for the DoD complex topology:
  - MikroTik RouterOS .rsc scripts (per device)
  - VPCS startup IP configs (per host)
  - Ansible site.yml + inventory + group_vars (community.routeros)
  - Terraform configs (AWS GovCloud IL4 + Azure Gov IL5 modules)
  - Demo scenario playbooks (6 use cases)

Usage:
  python tools/ndc/ztp_dod_config_generator.py [--json]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_LAB_DIR = _ROOT / "data" / "studio_artifacts" / "ndc" / "dod_lab"

# ══════════════════════════════════════════════════════════════════════════════
# RouterOS ZTP configs
# ══════════════════════════════════════════════════════════════════════════════

def _rsc_nipr_edge1() -> str:
    return """\
# CUI // SP-CTI
# ZTP: NIPR-EDGE-1 — Dual-homed NIPR edge router (AS 65001)
# Interfaces: ether1=ISP-ATT WAN, ether2=ISP-LUMEN WAN, ether3=NIPR-CORE link
:log info "ZTP: NIPR-EDGE-1 — starting config apply"

/system identity set name=NIPR-EDGE-1

# Disable attack surface (STIG NET-040 / DoD JRSS hardening)
/ip service disable telnet,ftp,www,api,api-ssl,winbox

# ── Interface addresses ────────────────────────────────────────────────────────
/ip address
add address=203.0.113.1/30   interface=ether1 comment="WAN-ATT"
add address=198.51.100.1/30  interface=ether2 comment="WAN-LUMEN"
add address=10.10.0.1/30     interface=ether3 comment="NIPR-CORE-LINK"

# ── BGP AS 65001 — dual ISP peering ───────────────────────────────────────────
/routing bgp instance set default as=65001 router-id=10.10.0.1
/routing bgp peer
add name=ISP-ATT    remote-address=203.0.113.2   remote-as=65101 nexthop-choice=force-self
add name=ISP-LUMEN  remote-address=198.51.100.2  remote-as=65102 nexthop-choice=force-self

# Default route injection (BGP learned, prefer ATT then Lumen)
/routing bgp network add network=10.10.0.0/8 comment="NIPR summary to ISPs"

# ── OSPF — internal NIPR failover with NIPR-EDGE-2 ───────────────────────────
/routing ospf instance set default router-id=10.10.0.1
/routing ospf network add network=10.10.0.0/30 area=backbone
/routing ospf network add network=10.10.0.4/30 area=backbone

# ── Policy routing — ECMP across dual ISPs ────────────────────────────────────
/ip route
add dst-address=0.0.0.0/0 gateway=203.0.113.2  distance=1 comment="Primary via ATT"
add dst-address=0.0.0.0/0 gateway=198.51.100.2 distance=10 comment="Failover via Lumen"

# ── Firewall — NIST SP 800-41 + DoD IL4 perimeter ────────────────────────────
/ip firewall filter
add chain=input   connection-state=established,related action=accept
add chain=input   connection-state=invalid             action=drop   comment="Drop invalid"
add chain=input   protocol=icmp                        action=accept comment="Allow ICMP"
add chain=input   src-address=10.0.0.0/8 protocol=tcp dst-port=22 action=accept comment="SSH mgmt"
add chain=input   src-address=10.0.0.0/8 protocol=udp dst-port=161 action=accept comment="SNMP mgmt"
add chain=input                                        action=drop   comment="Default deny"
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid             action=drop
add chain=forward src-address=10.0.0.0/8              action=accept comment="Allow NIPR"
add chain=forward                                      action=drop   comment="Default deny fwd"

# ── NAT — masquerade outbound to ISPs ─────────────────────────────────────────
/ip firewall nat
add chain=srcnat out-interface=ether1 action=masquerade comment="NAT via ATT"
add chain=srcnat out-interface=ether2 action=masquerade comment="NAT via Lumen"

# ── SSH hardening (STIG SRG-OS-000364) ────────────────────────────────────────
/ip ssh set strong-crypto=yes forwarding-enabled=no

# ── NTP, SNMP, Syslog ─────────────────────────────────────────────────────────
/system ntp client set enabled=yes servers=10.99.0.2
/snmp set enabled=yes trap-version=2
/snmp community add name=icdev-ro addresses=10.0.0.0/8 read-access=yes write-access=no
/system logging action set 0 memory-lines=1000
/system logging add topics=info,warning,error action=memory

:log info "ZTP: NIPR-EDGE-1 config complete"
"""


def _rsc_nipr_edge2() -> str:
    return """\
# CUI // SP-CTI
# ZTP: NIPR-EDGE-2 — Single-homed NIPR edge (Verizon), OSPF failover peer
:log info "ZTP: NIPR-EDGE-2 — starting config apply"

/system identity set name=NIPR-EDGE-2
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=192.0.2.1/30   interface=ether1 comment="WAN-VERIZON"
add address=10.10.0.5/30   interface=ether2 comment="NIPR-CORE-LINK"

/routing bgp instance set default as=65001 router-id=10.10.0.5
/routing bgp peer add name=ISP-VERIZON remote-address=192.0.2.2 remote-as=65103

/routing ospf instance set default router-id=10.10.0.5
/routing ospf network add network=10.10.0.4/30 area=backbone

/ip route add dst-address=0.0.0.0/0 gateway=192.0.2.2 distance=1 comment="Default via Verizon"

/ip firewall filter
add chain=input   connection-state=established,related action=accept
add chain=input   connection-state=invalid             action=drop
add chain=input   protocol=icmp                        action=accept
add chain=input   src-address=10.0.0.0/8 protocol=tcp dst-port=22 action=accept
add chain=input                                        action=drop
add chain=forward connection-state=established,related action=accept
add chain=forward src-address=10.0.0.0/8              action=accept
add chain=forward                                      action=drop

/ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade

/ip ssh set strong-crypto=yes forwarding-enabled=no
/system ntp client set enabled=yes servers=10.99.0.2
/snmp set enabled=yes trap-version=2
/snmp community add name=icdev-ro addresses=10.0.0.0/8 read-access=yes write-access=no

:log info "ZTP: NIPR-EDGE-2 config complete"
"""


def _rsc_nipr_core() -> str:
    return """\
# CUI // SP-CTI
# ZTP: NIPR-CORE-RTR — L3 routing hub (NIPR backbone)
# ether1=NIPR-EDGE-1, ether2=NIPR-EDGE-2, ether3=BCAP, ether4=OnPrem, ether5=MGMT
:log info "ZTP: NIPR-CORE-RTR — starting config apply"

/system identity set name=NIPR-CORE-RTR
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=10.10.0.2/30   interface=ether1 comment="NIPR-EDGE-1-LINK"
add address=10.10.0.6/30   interface=ether2 comment="NIPR-EDGE-2-LINK"
add address=10.20.0.1/30   interface=ether3 comment="BCAP-INGRESS"
add address=10.30.0.1/30   interface=ether4 comment="ONPREM-LINK"
add address=10.99.0.1/30   interface=ether5 comment="MGMT-LINK"

# OSPF — redistribute NIPR internal routes
/routing ospf instance set default router-id=10.10.0.2
/routing ospf network
add network=10.10.0.0/30  area=backbone
add network=10.10.0.4/30  area=backbone
add network=10.20.0.0/30  area=backbone
add network=10.30.0.0/30  area=backbone
add network=10.99.0.0/30  area=backbone

# Default routes via both edges (ECMP)
/ip route
add dst-address=0.0.0.0/0 gateway=10.10.0.1 distance=1  comment="via NIPR-EDGE-1"
add dst-address=0.0.0.0/0 gateway=10.10.0.5 distance=10 comment="via NIPR-EDGE-2 fallback"

# Summary routes toward BCAP
/ip route add dst-address=10.100.0.0/16 gateway=10.20.0.2 comment="AWS GovCloud via BCAP"
/ip route add dst-address=10.200.0.0/16 gateway=10.20.0.2 comment="Azure Gov via BCAP"

/ip firewall filter
add chain=input   connection-state=established,related action=accept
add chain=input   connection-state=invalid             action=drop
add chain=input   protocol=icmp                        action=accept
add chain=input   src-address=10.0.0.0/8 protocol=tcp dst-port=22 action=accept
add chain=input   src-address=10.99.0.0/30 protocol=udp dst-port=161 action=accept
add chain=input                                        action=drop
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid             action=drop
add chain=forward src-address=10.0.0.0/8 dst-address=10.0.0.0/8 action=accept
add chain=forward                                      action=drop

/ip ssh set strong-crypto=yes forwarding-enabled=no
/system ntp client set enabled=yes servers=10.99.0.2
/snmp set enabled=yes trap-version=2
/snmp community add name=icdev-ro addresses=10.99.0.0/30 read-access=yes write-access=no

:log info "ZTP: NIPR-CORE-RTR config complete"
"""


def _rsc_bcap_vdss_fw() -> str:
    return """\
# CUI // SP-CTI
# ZTP: BCAP-VDSS-FW — DISA Virtual Data Center Security Stack Firewall
# Implements DoD IL4/IL5 perimeter inspection (DoDI 8500.01, CISA ZT Pillar 5)
# ether1=NIPR ingress, ether2=VDSS-IDS egress
:log info "ZTP: BCAP-VDSS-FW — starting config apply"

/system identity set name=BCAP-VDSS-FW
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=10.20.0.2/30   interface=ether1 comment="NIPR-INGRESS"
add address=10.20.1.1/30   interface=ether2 comment="VDSS-IDS-EGRESS"

/ip route
add dst-address=0.0.0.0/0        gateway=10.20.0.1 comment="Default via NIPR-CORE"
add dst-address=10.100.0.0/16    gateway=10.20.1.2 comment="AWS via IDS"
add dst-address=10.200.0.0/16    gateway=10.20.1.2 comment="Azure via IDS"

# ── VDSS Deep Packet Inspection rules (IL4 boundary) ─────────────────────────
# Layer 7 firewall — NIST SP 800-41 + DoD JRSS VDSS policy
/ip firewall layer7-protocol
add name=PROHIBITED-P2P regexp="(bittorrent|torrent|kazaa|gnutella)"
add name=SOCIAL-MEDIA    regexp="(facebook\\.com|twitter\\.com|tiktok\\.com)"

/ip firewall filter
# INBOUND from NIPR (allow only IL4-approved traffic to CSPs)
add chain=forward in-interface=ether1 layer7-protocol=PROHIBITED-P2P action=drop comment="Block P2P"
add chain=forward in-interface=ether1 connection-state=invalid         action=drop
add chain=forward in-interface=ether1 protocol=tcp dst-port=443        action=accept comment="HTTPS to CSP"
add chain=forward in-interface=ether1 protocol=tcp dst-port=22         action=accept comment="SSH to CSP"
add chain=forward in-interface=ether1 protocol=tcp dst-port=8443       action=accept comment="HTTPS-alt"
add chain=forward in-interface=ether1 protocol=icmp                    action=accept comment="ICMP"
add chain=forward in-interface=ether1 connection-state=established,related action=accept
add chain=forward in-interface=ether1                                  action=drop   comment="VDSS default deny"
# OUTBOUND from CSP back to NIPR
add chain=forward in-interface=ether2 connection-state=established,related action=accept
add chain=forward in-interface=ether2 connection-state=invalid             action=drop
add chain=forward in-interface=ether2 protocol=icmp                        action=accept
add chain=forward in-interface=ether2                                      action=drop

# Management access (only from MGMT-JUMPHOST)
add chain=input src-address=10.99.0.2 protocol=tcp dst-port=22 action=accept comment="SSH from MGMT"
add chain=input connection-state=established,related            action=accept
add chain=input                                                 action=drop

# ── Logging — SIEM feed (syslog to MGMT-JUMPHOST:514) ────────────────────────
/system logging action
add name=siem type=remote remote=10.99.0.2 remote-port=514 src-address=10.20.0.2
/system logging
add topics=firewall,info    action=siem
add topics=firewall,warning action=siem
add topics=firewall,error   action=memory

/ip ssh set strong-crypto=yes forwarding-enabled=no
/system ntp client set enabled=yes servers=10.99.0.2

:log info "ZTP: BCAP-VDSS-FW config complete"
"""


def _rsc_bcap_vdss_ids() -> str:
    return """\
# CUI // SP-CTI
# ZTP: BCAP-VDSS-IDS — Inline IPS/IDS sensor (simulated via RouterOS L7 + connection tracking)
# ether1=from VDSS-FW, ether2=to VDMS
:log info "ZTP: BCAP-VDSS-IDS — starting config apply"

/system identity set name=BCAP-VDSS-IDS
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=10.20.1.2/30   interface=ether1 comment="FROM-VDSS-FW"
add address=10.20.2.1/30   interface=ether2 comment="TO-VDMS"

/ip route
add dst-address=0.0.0.0/0     gateway=10.20.1.1 comment="Default via VDSS-FW"
add dst-address=10.100.0.0/16 gateway=10.20.2.2 comment="AWS via VDMS"
add dst-address=10.200.0.0/16 gateway=10.20.2.2 comment="Azure via VDMS"

# ── IDS/IPS Signature Rules (MITRE ATT&CK simulation) ────────────────────────
/ip firewall layer7-protocol
add name=SQL-INJECTION      regexp="(union.*select|select.*from|insert.*into|drop.*table|;.*--)"
add name=XSS-PATTERN        regexp="(<script|javascript:|onerror=|onload=)"
add name=CMD-INJECTION      regexp="(;.*cat /etc|&&.*whoami|\\|.*id)"
add name=EXFIL-PATTERN      regexp="(base64.*decode|curl.*-o|wget.*http)"

/ip firewall filter
# Signature-based detection — log and drop
add chain=forward layer7-protocol=SQL-INJECTION  action=drop log=yes log-prefix="IDS-SQL-INJECT: "
add chain=forward layer7-protocol=XSS-PATTERN    action=drop log=yes log-prefix="IDS-XSS: "
add chain=forward layer7-protocol=CMD-INJECTION  action=drop log=yes log-prefix="IDS-CMDINJ: "
add chain=forward layer7-protocol=EXFIL-PATTERN  action=drop log=yes log-prefix="IDS-EXFIL: "

# Rate limiting — anomaly detection (> 100 conn/min from single src = suspect)
add chain=forward protocol=tcp connection-limit=100,32 action=drop log=yes log-prefix="IDS-RATELIMIT: "

# Allow inspected traffic through
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid             action=drop
add chain=forward                                      action=accept comment="IDS pass-through"

add chain=input src-address=10.99.0.0/30 protocol=tcp dst-port=22 action=accept
add chain=input connection-state=established,related               action=accept
add chain=input                                                    action=drop

/system logging action add name=siem type=remote remote=10.99.0.2 remote-port=514
/system logging add topics=firewall action=siem

/ip ssh set strong-crypto=yes forwarding-enabled=no
/system ntp client set enabled=yes servers=10.99.0.2

:log info "ZTP: BCAP-VDSS-IDS config complete"
"""


def _rsc_bcap_vdms() -> str:
    return """\
# CUI // SP-CTI
# ZTP: BCAP-VDMS — DISA Virtual Data Center Managed Services
# Policy router: NIPR → AWS GovCloud IL4 or Azure Gov IL5 selection
# ether1=from IDS, ether2=AWS-GovCloud, ether3=Azure-Gov, ether4=TCCM
:log info "ZTP: BCAP-VDMS — starting config apply"

/system identity set name=BCAP-VDMS
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=10.20.2.2/30   interface=ether1 comment="FROM-VDSS-IDS"
add address=10.100.0.1/30  interface=ether2 comment="AWS-GOVCLOUD-LINK"
add address=10.200.0.1/30  interface=ether3 comment="AZURE-GOV-LINK"
add address=10.20.3.1/30   interface=ether4 comment="TCCM-LINK"

/ip route
add dst-address=0.0.0.0/0       gateway=10.20.2.1 comment="Default via IDS→FW→NIPR"
add dst-address=10.100.0.0/16   gateway=10.100.0.2 comment="AWS GovCloud IL4"
add dst-address=10.200.0.0/16   gateway=10.200.0.2 comment="Azure Gov IL5"
add dst-address=10.20.3.0/30    gateway=10.20.3.2  comment="TCCM"

# ── Policy routing — workload-to-CSP mapping ─────────────────────────────────
# IL4 workloads → AWS GovCloud; IL5 workloads → Azure Gov
/ip firewall mangle
add chain=prerouting src-address=10.30.1.10/32 action=mark-routing new-routing-mark=IL4 comment="SRV1=IL4→AWS"
add chain=prerouting src-address=10.30.1.11/32 action=mark-routing new-routing-mark=IL5 comment="SRV2=IL5→Azure"

/ip route
add dst-address=0.0.0.0/0 gateway=10.100.0.2 routing-mark=IL4 comment="IL4 workloads via AWS"
add dst-address=0.0.0.0/0 gateway=10.200.0.2 routing-mark=IL5 comment="IL5 workloads via Azure"

# ── Firewall ──────────────────────────────────────────────────────────────────
/ip firewall filter
add chain=forward connection-state=established,related action=accept
add chain=forward connection-state=invalid             action=drop
add chain=forward src-address=10.20.2.0/30             action=accept comment="Allow from IDS"
add chain=forward src-address=10.100.0.0/16            action=accept comment="Allow from AWS"
add chain=forward src-address=10.200.0.0/16            action=accept comment="Allow from Azure"
add chain=forward src-address=10.20.3.0/30             action=accept comment="Allow TCCM"
add chain=forward                                      action=drop

add chain=input connection-state=established,related action=accept
add chain=input src-address=10.99.0.0/30 protocol=tcp dst-port=22 action=accept
add chain=input                                      action=drop

/system logging action add name=siem type=remote remote=10.99.0.2 remote-port=514
/system logging add topics=info,warning,error action=siem

/ip ssh set strong-crypto=yes forwarding-enabled=no
/system ntp client set enabled=yes servers=10.99.0.2

:log info "ZTP: BCAP-VDMS config complete"
"""


def _rsc_onprem_dc() -> str:
    return """\
# CUI // SP-CTI
# ZTP: ONPREM-DC1-RTR — On-premise DC router
# ether1=NIPR-CORE link, ether2=DC internal LAN, ether3=JWICS cross-domain guard
:log info "ZTP: ONPREM-DC1-RTR — starting config apply"

/system identity set name=ONPREM-DC1-RTR
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=10.30.0.2/30   interface=ether1 comment="NIPR-CORE-LINK"
add address=10.30.1.1/24   interface=ether2 comment="DC-LAN-GW"
add address=10.40.0.1/30   interface=ether3 comment="JWICS-XD-GUARD"

/ip route
add dst-address=0.0.0.0/0       gateway=10.30.0.1 comment="Default via NIPR-CORE"
add dst-address=192.168.100.0/24 gateway=10.40.0.2 comment="JWICS via XD-Guard"

# ── Cross-domain guard ACL (JWICS interface — EXTREMELY strict) ───────────────
# Only explicit, approved flows pass; everything else is deny+log
/ip firewall address-list
add list=APPROVED-XD-SOURCES address=10.30.1.10 comment="SRV1 approved for XD transfer"
add list=JWICS-APPROVED-DST   address=192.168.100.10 comment="JWICS-SRV1 approved dst"

/ip firewall filter
# XD-Guard outbound (NIPR → JWICS): only approved src/dst pairs
add chain=forward out-interface=ether3 src-address-list=APPROVED-XD-SOURCES \
    dst-address-list=JWICS-APPROVED-DST protocol=tcp dst-port=443 \
    action=accept log=yes log-prefix="XD-APPROVED: "
add chain=forward out-interface=ether3 action=drop log=yes log-prefix="XD-DENIED: "

# XD-Guard inbound (JWICS → NIPR): deny all unsolicited
add chain=forward in-interface=ether3  connection-state=established,related action=accept
add chain=forward in-interface=ether3  action=drop log=yes log-prefix="JWICS-INBOUND-DENY: "

# LAN firewall
add chain=forward in-interface=ether2  connection-state=established,related action=accept
add chain=forward in-interface=ether2  connection-state=invalid             action=drop
add chain=forward in-interface=ether2  dst-address=10.100.0.0/16            action=accept comment="to AWS via NIPR+BCAP"
add chain=forward in-interface=ether2  dst-address=10.200.0.0/16            action=accept comment="to Azure via NIPR+BCAP"
add chain=forward in-interface=ether2  protocol=icmp                        action=accept
add chain=forward in-interface=ether2                                       action=drop

add chain=input connection-state=established,related action=accept
add chain=input src-address=10.99.0.0/30 protocol=tcp dst-port=22 action=accept
add chain=input protocol=icmp                        action=accept
add chain=input                                      action=drop

/system logging action add name=siem type=remote remote=10.99.0.2 remote-port=514
/system logging add topics=firewall action=siem

/ip ssh set strong-crypto=yes forwarding-enabled=no
/system ntp client set enabled=yes servers=10.99.0.2

:log info "ZTP: ONPREM-DC1-RTR config complete"
"""


def _rsc_jwics_edge() -> str:
    return """\
# CUI // SP-CTI  (SECRET // SCI in production — JWICS is TS/SCI)
# ZTP: JWICS-EDGE — JWICS edge router / cross-domain guard (classified enclave)
# ether1=ONPREM XD-Guard link, ether2=JWICS internal
:log info "ZTP: JWICS-EDGE — starting config apply"

/system identity set name=JWICS-EDGE
/ip service disable telnet,ftp,www,api,api-ssl,winbox

/ip address
add address=10.40.0.2/30       interface=ether1 comment="NIPR-ONPREM-XD-GUARD"
add address=192.168.100.1/30   interface=ether2 comment="JWICS-INTERNAL"

# NO default route to NIPR — JWICS is one-way pull only
/ip route add dst-address=192.168.100.0/24 gateway=192.168.100.2 comment="JWICS LAN"

# ── JWICS enclave firewall — DENY ALL from NIPR unless explicit approval ──────
/ip firewall address-list
add list=NIPR-APPROVED-SRC address=10.30.1.10 comment="On-prem SRV1 (approved XD)"
add list=JWICS-INTERNAL     address=192.168.100.0/24

# Inbound from NIPR side — only approved source, no broadcast/multicast
add chain=forward in-interface=ether1 src-address-list=NIPR-APPROVED-SRC \
    dst-address-list=JWICS-INTERNAL protocol=tcp dst-port=443 \
    action=accept log=yes log-prefix="JWICS-XD-ACCEPT: "
add chain=forward in-interface=ether1 action=drop log=yes log-prefix="JWICS-BLOCK: "

# JWICS outbound back to NIPR — established sessions only, no new connections
add chain=forward out-interface=ether1 connection-state=established,related action=accept
add chain=forward out-interface=ether1 action=drop log=yes log-prefix="JWICS-OUTBOUND-BLOCK: "

# JWICS internal — unrestricted (classified enclave)
add chain=forward in-interface=ether2  action=accept

add chain=input connection-state=established,related action=accept
add chain=input in-interface=ether2 protocol=tcp dst-port=22 action=accept comment="JWICS mgmt only"
add chain=input                                    action=drop

/system logging add topics=firewall,info,warning,error action=memory

/ip ssh set strong-crypto=yes forwarding-enabled=no
# NTP internal only — no NIPR-side NTP (JWICS uses standalone Stratum 1)
/system ntp client set enabled=yes servers=192.168.100.10

:log info "ZTP: JWICS-EDGE config complete"
"""


# VPCS host startup configs
_VPCS_HOSTS = {
    "BCAP-TCCM":      ("10.20.3.2",      24, "10.20.3.1",      "Trusted Cloud Credential Manager"),
    "ONPREM-SRV1":    ("10.30.1.10",     24, "10.30.1.1",      "On-Prem App Server (IL4)"),
    "ONPREM-SRV2":    ("10.30.1.11",     24, "10.30.1.1",      "On-Prem DB Server (IL5)"),
    "ONPREM-WORKSTA": ("10.30.1.20",     24, "10.30.1.1",      "User Workstation"),
    "JWICS-SRV1":     ("192.168.100.10", 24, "192.168.100.1",  "JWICS App Server (TS/SCI)"),
    "JWICS-SRV2":     ("192.168.100.11", 24, "192.168.100.1",  "JWICS DB Server (TS/SCI)"),
    "MGMT-JUMPHOST":  ("10.99.0.2",      30, "10.99.0.1",      "Out-of-Band Management Jump Host"),
}


def _vpcs_config(name: str, ip: str, prefix: int, gw: str, desc: str) -> str:
    return f"# VPCS: {name} — {desc}\nip {ip}/{prefix} {gw}\n"


# ══════════════════════════════════════════════════════════════════════════════
# Ansible site playbook + inventory + group_vars
# ══════════════════════════════════════════════════════════════════════════════

def _ansible_inventory(ts: str) -> str:
    return f"""\
# Ansible Inventory — DoD BCAP/NIPR/JWICS Lab
# Generated: {ts}
# Requirements: community.routeros, ansible.netcommon

[nipr_edge]
NIPR-EDGE-1  ansible_host=10.10.0.1  ansible_network_os=routeros
NIPR-EDGE-2  ansible_host=10.10.0.5  ansible_network_os=routeros

[nipr_core]
NIPR-CORE-RTR  ansible_host=10.10.0.2  ansible_network_os=routeros

[bcap]
BCAP-VDSS-FW   ansible_host=10.20.0.2  ansible_network_os=routeros
BCAP-VDSS-IDS  ansible_host=10.20.1.2  ansible_network_os=routeros
BCAP-VDMS      ansible_host=10.20.2.2  ansible_network_os=routeros

[onprem]
ONPREM-DC1-RTR  ansible_host=10.30.0.2  ansible_network_os=routeros

[jwics]
JWICS-EDGE  ansible_host=10.40.0.2  ansible_network_os=routeros

[mikrotik_routers:children]
nipr_edge
nipr_core
bcap
onprem
jwics

[all:vars]
ansible_user=admin
ansible_password=
ansible_connection=network_cli
ansible_become=no
"""


def _ansible_site_yml(ts: str) -> str:
    return f"""\
---
# Ansible Site Playbook — DoD BCAP/NIPR/JWICS Zero Touch Provisioning
# Generated: {ts}
#
# Prerequisites:
#   ansible-galaxy collection install community.routeros
#   ansible-galaxy collection install ansible.netcommon
#
# Run:
#   ansible-playbook -i inventory.ini site.yml
#   ansible-playbook -i inventory.ini site.yml --limit NIPR-EDGE-1
#   ansible-playbook -i inventory.ini site.yml --tags verify

- name: Zero Touch Provisioning — NIPR Edge Routers
  hosts: nipr_edge
  gather_facts: no
  roles:
    - nipr_edge

- name: Zero Touch Provisioning — NIPR Core Router
  hosts: nipr_core
  gather_facts: no
  roles:
    - nipr_core

- name: Zero Touch Provisioning — BCAP Components
  hosts: bcap
  gather_facts: no
  roles:
    - bcap

- name: Zero Touch Provisioning — On-Premise DC
  hosts: onprem
  gather_facts: no
  roles:
    - onprem_dc

- name: Zero Touch Provisioning — JWICS Edge
  hosts: jwics
  gather_facts: no
  roles:
    - jwics_edge

- name: Post-ZTP Verification
  hosts: mikrotik_routers
  gather_facts: no
  tags: verify
  tasks:
    - name: Gather IP addresses
      community.routeros.command:
        commands: /ip address print
      register: ip_table

    - name: Gather routes
      community.routeros.command:
        commands: /ip route print
      register: route_table

    - name: Gather BGP peers
      community.routeros.command:
        commands: /routing bgp peer print
      register: bgp_peers
      when: inventory_hostname in groups['nipr_edge']

    - name: Report
      ansible.builtin.debug:
        msg:
          host:    "{{{{ inventory_hostname }}}}"
          ips:     "{{{{ ip_table.stdout_lines }}}}"
          routes:  "{{{{ route_table.stdout_lines }}}}"
"""


def _ansible_role(role_name: str, rsc_filename: str, ts: str) -> tuple[str, str]:
    """Returns (tasks/main.yml content, meta/main.yml content)."""
    tasks = f"""\
---
# Role: {role_name}
# Generated: {ts}
- name: Apply ZTP config — {{{{ inventory_hostname }}}}
  community.routeros.command:
    commands: "{{{{ lookup('file', playbook_dir + '/ztp/{rsc_filename}') | split('\\\\n') | select | list }}}}"
  register: ztp_result

- name: Verify identity
  community.routeros.command:
    commands: /system identity print
  register: identity

- name: Report identity
  ansible.builtin.debug:
    msg: "{{{{ inventory_hostname }}}} identity: {{{{ identity.stdout_lines }}}}"
"""
    meta = f"galaxy_info:\n  role_name: {role_name}\n  author: ICDEV\n  description: ZTP for {role_name}\n"
    return tasks, meta


# ══════════════════════════════════════════════════════════════════════════════
# Terraform — AWS GovCloud IL4 + Azure Gov IL5 modules
# ══════════════════════════════════════════════════════════════════════════════

def _tf_main() -> str:
    return """\
# CUI // SP-CTI
# Terraform — DoD BCAP Demo Lab Cloud Infrastructure
# AWS GovCloud (IL4) + Azure Government (IL5)
# Run: terraform init && terraform plan && terraform apply

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }

  # State backend — replace with S3 GovCloud bucket for production
  # backend "s3" {
  #   bucket  = "icdev-tfstate-govcloud"
  #   key     = "dod-bcap-lab/terraform.tfstate"
  #   region  = "us-gov-west-1"
  #   encrypt = true
  # }
}

provider "aws" {
  region = var.aws_region
  # GovCloud requires explicit partition — set AWS_PROFILE=govcloud in env
}

provider "azurerm" {
  environment     = "usgovernment"
  subscription_id = var.azure_subscription_id
  features {}
}

# ── AWS GovCloud IL4 ──────────────────────────────────────────────────────────
module "aws_govcloud_il4" {
  source = "./modules/aws_govcloud"

  vpc_cidr           = "10.100.0.0/16"
  bcap_tunnel_ip     = "10.100.0.2"
  bcap_vdms_ip       = "10.100.0.1"
  environment        = "IL4"
  classification     = "CUI"
  mission_owner_tag  = var.mission_owner
  project_tag        = var.project_name
}

# ── Azure Government IL5 ──────────────────────────────────────────────────────
module "azure_gov_il5" {
  source = "./modules/azure_gov"

  vnet_cidr          = "10.200.0.0/16"
  bcap_tunnel_ip     = "10.200.0.2"
  bcap_vdms_ip       = "10.200.0.1"
  environment        = "IL5"
  classification     = "CUI"
  mission_owner_tag  = var.mission_owner
  project_tag        = var.project_name
}

output "aws_vpc_id"              { value = module.aws_govcloud_il4.vpc_id }
output "aws_transit_gateway_id"  { value = module.aws_govcloud_il4.transit_gateway_id }
output "azure_vnet_id"           { value = module.azure_gov_il5.vnet_id }
output "azure_expressroute_id"   { value = module.azure_gov_il5.expressroute_id }
"""


def _tf_variables() -> str:
    return """\
variable "aws_region" {
  default     = "us-gov-west-1"
  description = "AWS GovCloud region"
}

variable "azure_subscription_id" {
  description = "Azure Government subscription ID"
  sensitive   = true
}

variable "mission_owner" {
  default     = "ICDEV-DOD-LAB"
  description = "Mission owner tag"
}

variable "project_name" {
  default     = "dod-bcap-demo"
  description = "Project tag"
}
"""


def _tf_aws_module() -> str:
    return """\
# AWS GovCloud IL4 module — BCAP-connected VPC
variable "vpc_cidr"           {}
variable "bcap_tunnel_ip"     {}
variable "bcap_vdms_ip"       {}
variable "environment"        {}
variable "classification"     {}
variable "mission_owner_tag"  {}
variable "project_tag"        {}

resource "aws_vpc" "il4" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = {
    Name           = "dod-lab-il4-vpc"
    Classification = var.classification
    Environment    = var.environment
    MissionOwner   = var.mission_owner_tag
    Project        = var.project_tag
  }
}

resource "aws_subnet" "app" {
  vpc_id            = aws_vpc.il4.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone = "us-gov-west-1a"
  tags = { Name = "il4-app-subnet", Tier = "app" }
}

resource "aws_subnet" "data" {
  vpc_id            = aws_vpc.il4.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 2)
  availability_zone = "us-gov-west-1b"
  tags = { Name = "il4-data-subnet", Tier = "data" }
}

# Transit Gateway — BCAP connectivity point
resource "aws_ec2_transit_gateway" "bcap_tgw" {
  description     = "BCAP-to-AWS GovCloud IL4 Transit Gateway"
  amazon_side_asn = 64512
  tags = { Name = "bcap-il4-tgw", Classification = var.classification }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "il4" {
  transit_gateway_id = aws_ec2_transit_gateway.bcap_tgw.id
  vpc_id             = aws_vpc.il4.id
  subnet_ids         = [aws_subnet.app.id]
}

# Site-to-Site VPN — simulates BCAP IPSec tunnel
resource "aws_vpn_gateway" "bcap_vpgw" {
  vpc_id = aws_vpc.il4.id
  tags   = { Name = "bcap-vpgw" }
}

resource "aws_customer_gateway" "bcap_vdms" {
  bgp_asn    = 65001
  ip_address = var.bcap_vdms_ip
  type       = "ipsec.1"
  tags       = { Name = "bcap-vdms-cgw" }
}

resource "aws_vpn_connection" "bcap_to_aws" {
  vpn_gateway_id      = aws_vpn_gateway.bcap_vpgw.id
  customer_gateway_id = aws_customer_gateway.bcap_vdms.id
  type                = "ipsec.1"
  static_routes_only  = false
  tags                = { Name = "bcap-to-aws-il4", Classification = var.classification }
}

# Security Group — IL4 workloads
resource "aws_security_group" "il4_workload" {
  name        = "il4-workload-sg"
  description = "IL4 workload security group — BCAP-only ingress"
  vpc_id      = aws_vpc.il4.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.20.0.0/16"]  # BCAP VDMS subnet only
    description = "HTTPS from BCAP"
  }
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.99.0.0/30"]
    description = "SSH from MGMT only"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "il4-workload-sg", Classification = var.classification }
}

output "vpc_id"              { value = aws_vpc.il4.id }
output "transit_gateway_id"  { value = aws_ec2_transit_gateway.bcap_tgw.id }
output "vpn_connection_id"   { value = aws_vpn_connection.bcap_to_aws.id }
"""


def _tf_azure_module() -> str:
    return """\
# Azure Government IL5 module — BCAP-connected VNet
variable "vnet_cidr"         {}
variable "bcap_tunnel_ip"    {}
variable "bcap_vdms_ip"      {}
variable "environment"       {}
variable "classification"    {}
variable "mission_owner_tag" {}
variable "project_tag"       {}

resource "azurerm_resource_group" "il5" {
  name     = "dod-lab-il5-rg"
  location = "usgovvirginia"
  tags = {
    Classification = var.classification
    Environment    = var.environment
    MissionOwner   = var.mission_owner_tag
    Project        = var.project_tag
  }
}

resource "azurerm_virtual_network" "il5" {
  name                = "il5-vnet"
  resource_group_name = azurerm_resource_group.il5.name
  location            = azurerm_resource_group.il5.location
  address_space       = [var.vnet_cidr]
  tags                = { Classification = var.classification }
}

resource "azurerm_subnet" "app" {
  name                 = "il5-app-subnet"
  resource_group_name  = azurerm_resource_group.il5.name
  virtual_network_name = azurerm_virtual_network.il5.name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 8, 1)]
}

resource "azurerm_subnet" "gateway" {
  name                 = "GatewaySubnet"
  resource_group_name  = azurerm_resource_group.il5.name
  virtual_network_name = azurerm_virtual_network.il5.name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 8, 254)]
}

# ExpressRoute — simulates DISA BCAP connectivity to Azure Gov
resource "azurerm_express_route_circuit" "bcap" {
  name                  = "bcap-to-azure-il5"
  resource_group_name   = azurerm_resource_group.il5.name
  location              = azurerm_resource_group.il5.location
  service_provider_name = "Equinix"
  peering_location      = "Washington DC"
  bandwidth_in_mbps     = 1000

  sku {
    tier   = "Premium"
    family = "MeteredData"
  }
  tags = { Name = "bcap-expressroute", Classification = var.classification }
}

resource "azurerm_virtual_network_gateway" "il5" {
  name                = "il5-vng"
  resource_group_name = azurerm_resource_group.il5.name
  location            = azurerm_resource_group.il5.location
  type                = "ExpressRoute"
  sku                 = "ErGw3AZ"

  ip_configuration {
    name                          = "il5-gw-ipconfig"
    subnet_id                     = azurerm_subnet.gateway.id
    private_ip_address_allocation = "Dynamic"
  }
}

# Network Security Group — IL5 workloads (stricter than IL4)
resource "azurerm_network_security_group" "il5_workload" {
  name                = "il5-workload-nsg"
  resource_group_name = azurerm_resource_group.il5.name
  location            = azurerm_resource_group.il5.location

  security_rule {
    name                       = "Allow-BCAP-HTTPS"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "10.20.0.0/16"
    destination_address_prefix = "*"
  }
  security_rule {
    name                       = "Allow-MGMT-SSH"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "10.99.0.0/30"
    destination_address_prefix = "*"
  }
  security_rule {
    name                       = "Deny-All"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
  tags = { Classification = var.classification }
}

output "vnet_id"         { value = azurerm_virtual_network.il5.id }
output "expressroute_id" { value = azurerm_express_route_circuit.bcap.id }
"""


# ══════════════════════════════════════════════════════════════════════════════
# Demo scenarios
# ══════════════════════════════════════════════════════════════════════════════

def _scenario_docs() -> dict[str, str]:
    return {
        "01_nipr_to_cloud.md": """\
# Scenario 1 — DoD User Accessing AWS GovCloud Application
**Segment path:** ONPREM-WORKSTA → ONPREM-DC1-SW → ONPREM-DC1-RTR → NIPR-CORE-RTR → BCAP-VDSS-FW → BCAP-VDSS-IDS → BCAP-VDMS → AWS-GOVCLOUD-IL4

## Steps
1. `ONPREM-WORKSTA> ping 10.100.0.2` — confirm reachability to AWS GovCloud
2. Initiate HTTPS session (TCP/443) from ONPREM-WORKSTA (10.30.1.20) to AWS app (10.100.1.10)
3. **Capture packets at BCAP-VDSS-FW** — verify traffic inspected at IL4 boundary
4. **Check VDSS-FW logs** — `log print where topics~"firewall"` on BCAP-VDSS-FW
5. **Check IDS alerts** — `log print where topics~"firewall" and message~"IDS"` on BCAP-VDSS-IDS
6. Verify BCAP-VDMS policy routing selects AWS for IL4 workload (10.30.1.10 → IL4 mark)

## Business Case
Demonstrates Zero Trust Architecture (ZTA) — every packet between NIPR and cloud is inspected. No implicit trust across the BCAP boundary. Compliance: DoDI 8500.01, NIST SP 800-207.
""",
        "02_isp_failover.md": """\
# Scenario 2 — ISP Redundancy and Failover
**Actors:** NIPR-EDGE-1 (ATT + Lumen), NIPR-EDGE-2 (Verizon)

## Steps
1. Confirm both ISPs active: on NIPR-EDGE-1 run `routing bgp peer print`
2. **Simulate ATT outage:** `/interface set ether1 disabled=yes` on NIPR-EDGE-1
3. Watch BGP reconvergence: `routing bgp peer print` — ATT peer goes Idle
4. Confirm traffic reroutes via Lumen (ether2): `ip route print`
5. **Simulate full NIPR-EDGE-1 failure:** shutdown ether1 and ether2
6. Verify OSPF reroutes through NIPR-EDGE-2 / Verizon
7. Restore: `/interface set ether1 disabled=no`

## Business Case
Demonstrates BCAP connectivity resilience. DoD requirements mandate no single point of failure for NIPR egress (CJCSM 6510.01B). Shows sub-60s reconvergence using BGP + OSPF.
""",
        "03_bcap_threat_detection.md": """\
# Scenario 3 — BCAP Threat Detection (SQL Injection / Exfil Attempt)
**Actor:** ONPREM-WORKSTA attempts simulated malicious traffic through BCAP

## Steps
1. From ONPREM-WORKSTA, attempt SQL injection in HTTP payload:
   `echo "GET /app?id=1;SELECT*FROM users HTTP/1.1" | nc 10.100.0.2 80`
2. Check BCAP-VDSS-IDS logs: `log print where message~"IDS-SQL"` — alert fires
3. Confirm traffic dropped (no response from AWS)
4. Attempt data exfiltration pattern:
   `echo "GET /api?data=base64decode(aGVsbG8=) HTTP/1.1" | nc 10.100.0.2 443`
5. Check IDS log for EXFIL-PATTERN alert
6. Review SIEM feed on MGMT-JUMPHOST (port 514)

## Business Case
Live demonstration of DISA VDSS IDS capability. Satisfies NIST SI-4 (System Monitoring) and DoD JRSS requirements. Shows AI/ML integration point — IDS signatures can be updated from threat feeds in real time.
""",
        "04_jwics_crossdomain.md": """\
# Scenario 4 — JWICS Cross-Domain Controlled Data Transfer
**Actors:** ONPREM-SRV1 (approved source) → JWICS-SRV1 (approved destination)

## Steps
1. Verify cross-domain guard ACL on ONPREM-DC1-RTR: `ip firewall address-list print`
2. Attempt transfer from approved source (ONPREM-SRV1 = 10.30.1.10):
   `nc 192.168.100.10 443` — should PASS (in approved list)
3. Attempt from unapproved source (ONPREM-WORKSTA = 10.30.1.20):
   `nc 192.168.100.10 443` — should be DENIED + logged
4. Check XD-Guard log: `log print where message~"XD-DENIED"` on ONPREM-DC1-RTR
5. Check JWICS-EDGE deny log: `log print where message~"JWICS-BLOCK"`
6. Verify NIPR cannot reach JWICS directly (no route exists from NIPR-CORE-RTR to 192.168.100.0/24 without traversing ONPREM-DC1-RTR)

## Business Case
Demonstrates DoD Cross-Domain Solution (CDS) policy enforcement. Even within a single physical lab, JWICS is logically air-gapped. Compliance: DoDI 8540.01, CNSSP-12.
""",
        "05_tccm_credential_management.md": """\
# Scenario 5 — TCCM Zero-Trust Credential Issuance
**Actor:** Application at AWS GovCloud requests credentials via BCAP-TCCM

## Steps
1. Simulate app credential request from AWS side to TCCM (10.20.3.2):
   `nc 10.20.3.2 443` from AWS-GOVCLOUD-IL4
2. Verify BCAP-VDMS routes credential request to TCCM via ether4
3. TCCM validates source IP (must be in BCAP-VDMS range 10.20.2.0/30 or 10.100.0.0/16)
4. Show time-limited credential concept (TCCM issues 1-hour TTL tokens)
5. Demonstrate that direct NIPR access to TCCM is blocked by BCAP-VDMS firewall

## Business Case
Zero Trust credential management — no long-lived secrets in CSP workloads. Aligns with NIST SP 800-207 (ZTA), NSA/CISA Zero Trust Guidance, and DoD ZT Strategy 2022.
""",
        "06_ai_agentic_network_ops.md": """\
# Scenario 6 — Agentic AI Network Operations (Art of the Possible)
**Actors:** ICDEV Agentic AI ↔ All segments

## Use Case A: Autonomous Threat Response
1. IDS on BCAP-VDSS-IDS fires SQL injection alert (Scenario 3)
2. AI Agent receives syslog event on MGMT-JUMPHOST
3. Agent analyzes: source IP, pattern type, MITRE ATT&CK mapping (T1190)
4. Agent automatically updates firewall ACL on BCAP-VDSS-FW to block source /32
5. Agent opens incident ticket in ServiceNow + notifies SOC via Slack
6. Agent re-validates after 30 min and removes block if false positive

## Use Case B: Predictive ISP Failover
1. AI Agent monitors BGP peer health every 30s
2. Detects latency spike on ISP-ATT link (BGP MED change)
3. Agent pre-positions routes to ISP-LUMEN before ATT failure
4. Zero-packet-loss failover (vs. 30-60s reactive BGP)
5. Agent generates RCA report automatically

## Use Case C: Autonomous Network Modernization Assessment
1. AI Agent scans all devices in lab via SNMP/SSH
2. Builds current-state inventory (device type, OS version, config)
3. Compares against DoD STIG benchmarks (online + offline)
4. Generates prioritized remediation plan with blast-radius analysis
5. Creates GNS3 "future state" topology with recommended changes
6. Estimates CAPEX/OPEX delta + migration timeline

## Business Case
See `dod_lab_ai_use_cases.md` for full ROI and business case analysis.
""",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    artifacts: list[dict] = []

    def _write(path: Path, content: str, atype: str, desc: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        artifacts.append({"type": atype, "desc": desc, "path": str(path.relative_to(_ROOT))})

    # ── RouterOS ZTP scripts ─────────────────────────────────────────────────
    ztp = _LAB_DIR / "ztp"
    _write(ztp / "NIPR-EDGE-1.rsc",    _rsc_nipr_edge1(),  "rsc", "NIPR-EDGE-1 RouterOS ZTP")
    _write(ztp / "NIPR-EDGE-2.rsc",    _rsc_nipr_edge2(),  "rsc", "NIPR-EDGE-2 RouterOS ZTP")
    _write(ztp / "NIPR-CORE-RTR.rsc",  _rsc_nipr_core(),   "rsc", "NIPR-CORE-RTR RouterOS ZTP")
    _write(ztp / "BCAP-VDSS-FW.rsc",   _rsc_bcap_vdss_fw(),  "rsc", "BCAP-VDSS-FW RouterOS ZTP")
    _write(ztp / "BCAP-VDSS-IDS.rsc",  _rsc_bcap_vdss_ids(), "rsc", "BCAP-VDSS-IDS RouterOS ZTP")
    _write(ztp / "BCAP-VDMS.rsc",      _rsc_bcap_vdms(),   "rsc", "BCAP-VDMS RouterOS ZTP")
    _write(ztp / "ONPREM-DC1-RTR.rsc", _rsc_onprem_dc(),   "rsc", "ONPREM-DC1-RTR RouterOS ZTP")
    _write(ztp / "JWICS-EDGE.rsc",     _rsc_jwics_edge(),  "rsc", "JWICS-EDGE RouterOS ZTP")

    # ── VPCS startup scripts ─────────────────────────────────────────────────
    for name, (ip, pfx, gw, desc) in _VPCS_HOSTS.items():
        _write(ztp / f"vpcs_{name}.txt", _vpcs_config(name, ip, pfx, gw, desc),
               "vpcs", f"VPCS config — {name}")

    # ── Ansible ──────────────────────────────────────────────────────────────
    ans = _LAB_DIR / "ansible"
    _write(ans / "inventory.ini",  _ansible_inventory(ts),  "ini", "Ansible inventory")
    _write(ans / "site.yml",       _ansible_site_yml(ts),   "yml", "Ansible site playbook")

    _ROLES = [
        ("nipr_edge",    "NIPR-EDGE-1.rsc"),
        ("nipr_core",    "NIPR-CORE-RTR.rsc"),
        ("bcap",         "BCAP-VDSS-FW.rsc"),
        ("onprem_dc",    "ONPREM-DC1-RTR.rsc"),
        ("jwics_edge",   "JWICS-EDGE.rsc"),
    ]
    for role, rsc in _ROLES:
        tasks, meta = _ansible_role(role, rsc, ts)
        _write(ans / "roles" / role / "tasks" / "main.yml", tasks, "yml", f"Role tasks — {role}")
        _write(ans / "roles" / role / "meta"  / "main.yml", meta,  "yml", f"Role meta — {role}")

    # ── Terraform ────────────────────────────────────────────────────────────
    tf = _LAB_DIR / "terraform"
    _write(tf / "main.tf",                         _tf_main(),      "tf", "Terraform main")
    _write(tf / "variables.tf",                    _tf_variables(), "tf", "Terraform variables")
    _write(tf / "modules" / "aws_govcloud" / "main.tf",  _tf_aws_module(),   "tf", "AWS GovCloud IL4 module")
    _write(tf / "modules" / "azure_gov"    / "main.tf",  _tf_azure_module(), "tf", "Azure Gov IL5 module")

    # ── Demo scenarios ───────────────────────────────────────────────────────
    sc = _LAB_DIR / "scenarios"
    for fname, content in _scenario_docs().items():
        _write(sc / fname, content, "md", f"Scenario — {fname}")

    return {
        "status":     "ok",
        "generated":  ts,
        "lab_dir":    str(_LAB_DIR.relative_to(_ROOT)),
        "artifacts":  artifacts,
        "counts": {
            "rsc_configs":  8,
            "vpcs_configs": len(_VPCS_HOSTS),
            "ansible":      2 + len(_ROLES) * 2,
            "terraform":    4,
            "scenarios":    len(_scenario_docs()),
        },
        "run_order": [
            "1. python tools/ndc/gns3_dod_topology_builder.py --json",
            "2. python tools/ndc/ztp_dod_config_generator.py --json",
            "3. python tools/ndc/ztp_console_push.py --project dod-bcap-lab --json",
            "4. cd data/studio_artifacts/ndc/dod_lab/ansible && ansible-playbook -i inventory.ini site.yml",
            "5. cd data/studio_artifacts/ndc/dod_lab/terraform && terraform init && terraform plan",
        ],
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="ZTP DoD Config Generator")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Generated: {result['generated']}")
        print(f"Lab dir:   {result['lab_dir']}")
        for k, v in result["counts"].items():
            print(f"  {k:20s}: {v}")
        print("\nRun order:")
        for step in result["run_order"]:
            print(f"  {step}")


if __name__ == "__main__":
    main()
