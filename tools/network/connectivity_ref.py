# CUI // SP-CTI
"""NDC Cloud Connectivity Reference — structured data for the Connectivity Reference page.

Pure Python, no Flask. Imports constants from tools.network.constants and SOP
lookups from tools.network.sops. All functions return plain dicts/lists safe
for json.dumps() and Jinja2 template rendering.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = _ICDEV_ROOT / "data" / "network_canvas.db"


def _get_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


# ── Connectivity Matrix ───────────────────────────────────────────────────────

CONNECTIVITY_MATRIX: dict[str, dict[str, dict[str, Any]]] = {
    "aws": {
        "dedicated_private": {
            "service": "Direct Connect",
            "service_abbrev": "DX",
            "node_type": "aws-dx",
            "bandwidth_options": ["1G", "10G", "100G"],
            "redundancy": "Dual-location or LAG",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_mod", "fedramp_high", "il4", "il5"],
            "dod_bcap_compatible": True,
            "macsec_support": True,
            "sop_titles": [
                "AWS Direct Connect — Hosted Connection via Partner (1 Gbps/10 Gbps)",
                "AWS Direct Connect — Dedicated Connection (10 Gbps/100 Gbps) with MACsec",
            ],
        },
        "ipsec_vpn": {
            "service": "Site-to-Site VPN",
            "service_abbrev": "S2S-VPN",
            "node_type": "aws-vpn",
            "bandwidth_options": ["Up to 1.25 Gbps/tunnel"],
            "redundancy": "Dual-tunnel active/active",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod", "fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "AWS Site-to-Site VPN — BGP over IPSec (IKEv2, AES-256-GCM)",
            ],
        },
        "hub_transit": {
            "service": "Transit Gateway",
            "service_abbrev": "TGW",
            "node_type": "aws-tgw",
            "bandwidth_options": ["50 Gbps per AZ"],
            "redundancy": "Multi-AZ, multi-region peering",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4", "il5"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "AWS Transit Gateway — Hub Creation with ECMP and Multi-Region Peering",
                "AWS Transit Gateway — Centralized Inspection VPC Routing (VDSS Pattern)",
            ],
        },
        "sdwan_overlay": {
            "service": "SD-WAN / Cloud WAN",
            "service_abbrev": "CWN",
            "node_type": "aws-cloudwan",
            "bandwidth_options": ["Policy-defined"],
            "redundancy": "Multi-path ECMP",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod", "fedramp_high"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
        "cloud_exchange": {
            "service": "Equinix Fabric / Megaport",
            "service_abbrev": "ECX",
            "node_type": "equinix-fabric",
            "bandwidth_options": ["50M–10G"],
            "redundancy": "Dual port",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "Equinix Fabric (ECX) — L3 Connection Between Two CSPs via MCR",
            ],
        },
    },
    "azure": {
        "dedicated_private": {
            "service": "ExpressRoute",
            "service_abbrev": "ER",
            "node_type": "az-er",
            "bandwidth_options": ["50M", "100M", "200M", "500M", "1G", "2G", "5G", "10G", "100G"],
            "redundancy": "Dual circuit active/active or active/standby",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4", "il5"],
            "dod_bcap_compatible": True,
            "macsec_support": True,
            "sop_titles": [
                "Azure ExpressRoute — New Circuit Provisioning (Provider Model)",
            ],
        },
        "ipsec_vpn": {
            "service": "VPN Gateway",
            "service_abbrev": "VPN-GW",
            "node_type": "az-vpn-gw",
            "bandwidth_options": ["100M–10G (SKU-dependent)"],
            "redundancy": "Active-active with zone-redundant SKU",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod", "fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "Azure VPN Gateway — Active-Active BGP IPSec to On-Prem",
            ],
        },
        "hub_transit": {
            "service": "Virtual WAN",
            "service_abbrev": "vWAN",
            "node_type": "az-vwan",
            "bandwidth_options": ["Policy-defined"],
            "redundancy": "Zone-redundant hub",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4", "il5"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "Azure Virtual WAN — Hub Deployment with Routing Intent (Firewall Policy)",
            ],
        },
        "sdwan_overlay": {
            "service": "Virtual WAN + SD-WAN NVA",
            "service_abbrev": "VWAN-SD",
            "node_type": "az-vwan",
            "bandwidth_options": ["Partner NVA-defined"],
            "redundancy": "Multi-path",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod", "fedramp_high"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
        "cloud_exchange": {
            "service": "Equinix Fabric / Megaport",
            "service_abbrev": "ECX",
            "node_type": "equinix-fabric",
            "bandwidth_options": ["50M–10G"],
            "redundancy": "Dual port",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "Equinix Fabric (ECX) — L3 Connection Between Two CSPs via MCR",
            ],
        },
    },
    "gcp": {
        "dedicated_private": {
            "service": "Cloud Interconnect",
            "service_abbrev": "IC",
            "node_type": "gcp-ic",
            "bandwidth_options": ["10G", "100G"],
            "redundancy": "Dual colocation or metro-diverse",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "GCP Cloud Interconnect — Dedicated Interconnect (10G/100G VLAN attachment)",
            ],
        },
        "ipsec_vpn": {
            "service": "HA VPN",
            "service_abbrev": "HA-VPN",
            "node_type": "gcp-ha-vpn",
            "bandwidth_options": ["3 Gbps/tunnel (x2 tunnels)"],
            "redundancy": "Active/active dual-tunnel",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "GCP Cloud VPN — HA VPN with BGP (Active/Active Tunnels)",
            ],
        },
        "hub_transit": {
            "service": "Network Connectivity Center",
            "service_abbrev": "NCC",
            "node_type": "gcp-ncc",
            "bandwidth_options": ["Policy-defined"],
            "redundancy": "Multi-region spokes",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "GCP Network Connectivity Center — Hub-Spoke with VPN Spoke",
            ],
        },
        "sdwan_overlay": {
            "service": "NCC + SD-WAN spoke",
            "service_abbrev": "NCC-SD",
            "node_type": "gcp-ncc",
            "bandwidth_options": ["Router appliance-defined"],
            "redundancy": "Multi-spoke",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
        "cloud_exchange": {
            "service": "Equinix Fabric / Megaport",
            "service_abbrev": "ECX",
            "node_type": "equinix-fabric",
            "bandwidth_options": ["50M–10G"],
            "redundancy": "Dual port",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
    },
    "oci": {
        "dedicated_private": {
            "service": "FastConnect",
            "service_abbrev": "FC",
            "node_type": "oci-fc",
            "bandwidth_options": ["1G", "2G", "5G", "10G", "100G"],
            "redundancy": "Dual FastConnect circuits",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4", "il5"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "OCI FastConnect — Provisioning via Partner or Colocation",
            ],
        },
        "ipsec_vpn": {
            "service": "VPN Connect",
            "service_abbrev": "VPN",
            "node_type": "oci-ipsec",
            "bandwidth_options": ["Up to 4 Gbps (4 tunnels × 1 Gbps)"],
            "redundancy": "4 redundant IPSec tunnels",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "OCI VPN Connect — IPSec with BGP and Static Route Failover",
            ],
        },
        "hub_transit": {
            "service": "Dynamic Routing Gateway",
            "service_abbrev": "DRG",
            "node_type": "oci-drg",
            "bandwidth_options": ["Up to 100 Gbps"],
            "redundancy": "Regional HA",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4", "il5"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "OCI Dynamic Routing Gateway — Transit Routing Configuration",
            ],
        },
        "sdwan_overlay": {
            "service": "DRG + SD-WAN NVA",
            "service_abbrev": "DRG-SD",
            "node_type": "oci-drg",
            "bandwidth_options": ["NVA-defined"],
            "redundancy": "Multi-path",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
        "cloud_exchange": {
            "service": "Equinix Fabric / FastConnect Partner",
            "service_abbrev": "ECX",
            "node_type": "equinix-fabric",
            "bandwidth_options": ["50M–10G"],
            "redundancy": "Dual port",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [],
        },
    },
    "ibm": {
        "dedicated_private": {
            "service": "Direct Link 2.0",
            "service_abbrev": "DL",
            "node_type": "ibm-dl-ded",
            "bandwidth_options": ["1G", "2G", "5G", "10G"],
            "redundancy": "Dual Direct Link ports",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": True,
            "macsec_support": False,
            "sop_titles": [
                "IBM Cloud Direct Link 2.0 — Dedicated Connection Setup",
            ],
        },
        "ipsec_vpn": {
            "service": "VPN Gateway",
            "service_abbrev": "IBM-VPN",
            "node_type": "ibm-vpn",
            "bandwidth_options": ["650 Mbps per gateway"],
            "redundancy": "Active/standby member pairs",
            "bgp_support": False,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod", "fedramp_high"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "IBM Cloud VPN Gateway — Policy-Based IPSec Configuration",
            ],
        },
        "hub_transit": {
            "service": "Transit Gateway",
            "service_abbrev": "IBM-TGW",
            "node_type": "ibm-tg",
            "bandwidth_options": ["Up to 100 Gbps"],
            "redundancy": "Cross-region HA",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high", "il4"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [
                "IBM Cloud Transit Gateway — Connecting VPCs Across Regions",
            ],
        },
        "sdwan_overlay": {
            "service": "Transit GW + SD-WAN",
            "service_abbrev": "IBM-SD",
            "node_type": "ibm-tg",
            "bandwidth_options": ["NVA-defined"],
            "redundancy": "Multi-path",
            "bgp_support": True,
            "latency_class": "medium",
            "compliance_levels": ["fedramp_mod"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
        "cloud_exchange": {
            "service": "Equinix Fabric / Direct Link Connect",
            "service_abbrev": "ECX",
            "node_type": "equinix-fabric",
            "bandwidth_options": ["50M–5G"],
            "redundancy": "Dual port",
            "bgp_support": True,
            "latency_class": "low",
            "compliance_levels": ["fedramp_high"],
            "dod_bcap_compatible": False,
            "macsec_support": False,
            "sop_titles": [],
        },
    },
}

# ── On-Prem to CSP Patterns ───────────────────────────────────────────────────

_ONPREM_PATTERNS: dict[str, dict[str, dict[str, Any]]] = {
    "aws": {
        "ipsec_vpn": {
            "pattern": "IPSec VPN (Internet)",
            "description": "Site-to-Site IPSec VPN over public internet. Quick to provision with minimal hardware. Best for remote/branch offices, Dev/Test, or as backup for DX.",
            "pros": ["Fast to deploy (hours)", "Low cost (fixed per-connection)", "No physical circuit ordering lead time", "Redundant dual tunnels active/active"],
            "cons": ["Bandwidth limited to 1.25 Gbps/tunnel", "Internet latency variability", "Not BCAP-compatible for DoD IL4/IL5", "BGP over IPSec adds config complexity"],
            "use_cases": ["Dev/Test environments", "Remote branch offices", "DX backup path", "Temporary connectivity"],
            "csp_service": "AWS Site-to-Site VPN + VGW or TGW",
            "compliance": ["fedramp_mod", "fedramp_high", "il4 (with compensating controls)"],
            "dod_note": "Not directly BCAP-compatible. For DoD IL4/IL5, DX is the required primary path. VPN acceptable as encrypted backup.",
            "diagram": "On-Prem CE Router ─[IKEv2/IPSec]─> Internet ─> AWS CGW ─> VGW/TGW ─> VPC",
            "sop_titles": ["AWS Site-to-Site VPN — BGP over IPSec (IKEv2, AES-256-GCM)", "On-Prem CE Router — IKEv2/IPSec Peer Configuration (IOS/JunOS)"],
        },
        "dedicated_private": {
            "pattern": "Dedicated Private (Direct Connect)",
            "description": "Physical 1G/10G/100G fiber from on-prem to AWS Direct Connect location. Consistent latency, high bandwidth, no internet dependency.",
            "pros": ["Consistent sub-ms latency", "Up to 100 Gbps bandwidth", "MACsec Layer 2 encryption (10G/100G)", "BCAP-compatible for DoD IL4/IL5", "Private VIF for single account or Transit VIF for multi-account via DXGW"],
            "cons": ["Lead time 4–12 weeks (physical circuit)", "Higher monthly cost", "Requires colocation or carrier partner", "Single point of failure without dual DX"],
            "use_cases": ["Production workloads", "DoD IL4/IL5 missions", "High-bandwidth data transfer", "Latency-sensitive apps"],
            "csp_service": "AWS Direct Connect + DXGW + TGW (or VGW)",
            "compliance": ["fedramp_high", "il4", "il5"],
            "dod_note": "Required for DoD IL4/IL5. Must connect through DISA BCAP. Use Transit VIF with DXGW for multi-account mission owner architecture.",
            "diagram": "On-Prem ─[10G fiber]─> Colo/DX Location ─> AWS DX ─> DXGW ─> TGW ─> VPCs",
            "sop_titles": ["AWS Direct Connect — Hosted Connection via Partner (1 Gbps/10 Gbps)", "AWS Direct Connect — Dedicated Connection (10 Gbps/100 Gbps) with MACsec"],
        },
        "partner_managed": {
            "pattern": "Partner / Managed (MSP or Colo Fabric)",
            "description": "Connectivity via cloud exchange partner (Equinix Fabric, Megaport) or MSP-managed SD-WAN. Provider manages physical circuit and cross-connect.",
            "pros": ["No colocation required", "Flexible bandwidth scaling", "Provider SLA", "Multi-CSP from single logical port"],
            "cons": ["Additional provider cost layer", "Provider dependency", "Longer troubleshooting path"],
            "use_cases": ["Multi-CSP organizations", "Enterprises without colo presence", "Bandwidth-flexible requirements"],
            "csp_service": "Equinix Fabric / Megaport MCR → AWS DX Hosted",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "Acceptable for DoD if partner is FedRAMP-authorized and connection terminates at BCAP-compliant point.",
            "diagram": "On-Prem ─> Megaport MCR/Equinix ECX ─> AWS DX Hosted Connection ─> VGW/TGW",
            "sop_titles": ["Megaport MCR — Multi-CSP Transit Routing Configuration"],
        },
        "sdwan_overlay": {
            "pattern": "SD-WAN Overlay",
            "description": "Policy-based overlay network using SD-WAN CPE at on-prem sites and cloud. Provides application-aware routing, QoS, and centralized management.",
            "pros": ["Application-aware routing", "Zero-touch provisioning", "WAN optimization", "Multi-path ECMP"],
            "cons": ["Additional CPE/license cost", "Bandwidth subject to underlay (internet/MPLS)", "Complexity for compliance evidence"],
            "use_cases": ["Branch office connectivity", "Hybrid WAN replacement", "Multi-CSP policy routing"],
            "csp_service": "AWS Cloud WAN + SD-WAN NVA in VPC",
            "compliance": ["fedramp_mod"],
            "dod_note": "SD-WAN overlays acceptable for non-mission data. For classified/CUI, must use dedicated private connectivity.",
            "diagram": "On-Prem SD-WAN Edge ─[overlay]─> AWS Cloud WAN ─> VPCs",
            "sop_titles": [],
        },
    },
    "azure": {
        "ipsec_vpn": {
            "pattern": "IPSec VPN (Internet)",
            "description": "Azure VPN Gateway S2S connection over internet. Supports IKEv2, BGP, active-active HA. Multiple gateway SKUs from 100 Mbps to 10 Gbps.",
            "pros": ["Fast to deploy", "Multiple bandwidth SKUs", "Active-active HA with zone-redundant SKU", "BGP support with custom ASN (VpnGw1+)"],
            "cons": ["Internet dependency", "Not BCAP-compatible for IL4/IL5", "Gateway SKU determines max throughput"],
            "use_cases": ["Branch offices", "Dev/Test", "ER backup"],
            "csp_service": "Azure VPN Gateway + Local Network Gateway",
            "compliance": ["fedramp_mod", "fedramp_high"],
            "dod_note": "Not BCAP-compatible. ExpressRoute required for DoD IL4/IL5 Azure Government environments.",
            "diagram": "On-Prem ─[IKEv2/IPSec]─> Internet ─> Azure VPN GW ─> VNet",
            "sop_titles": ["Azure VPN Gateway — Active-Active BGP IPSec to On-Prem"],
        },
        "dedicated_private": {
            "pattern": "Dedicated Private (ExpressRoute)",
            "description": "Private connectivity from on-prem to Azure via ExpressRoute circuit. Available through 200+ providers globally. Supports Microsoft peering for O365/Azure services.",
            "pros": ["Consistent latency", "50M–100G bandwidth options", "MACsec on QinQ", "Global Reach for site-to-site via Azure backbone", "BCAP-compatible"],
            "cons": ["Circuit provisioning lead time", "Higher cost", "Requires ER partner or colocation"],
            "use_cases": ["Production Azure Gov workloads", "DoD IL4/IL5", "O365 GCC High", "Large data migrations"],
            "csp_service": "ExpressRoute Circuit + ExpressRoute Gateway + VNet",
            "compliance": ["fedramp_high", "il4", "il5"],
            "dod_note": "Required for Azure Gov DoD IL4/IL5. Must terminate at DISA BCAP Gen2/Gen3. ERGW must be ultra-performance SKU for latency-sensitive workloads.",
            "diagram": "On-Prem ─[ER Circuit via Provider]─> Azure ER ─> ERGW ─> VNet",
            "sop_titles": ["Azure ExpressRoute — New Circuit Provisioning (Provider Model)"],
        },
        "partner_managed": {
            "pattern": "Partner / Managed (Cloud Exchange)",
            "description": "ExpressRoute via Equinix/Megaport without local ER provider presence. Provider operates the physical handoff.",
            "pros": ["No ER partner required at on-prem", "Flexible bandwidth", "Multi-CSP from one port"],
            "cons": ["Provider dependency", "Additional cost tier"],
            "use_cases": ["Multi-CSP orgs", "Colocation-heavy environments"],
            "csp_service": "Equinix Fabric → Azure ExpressRoute Hosted",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "Acceptable if exchange provider is FedRAMP-authorized.",
            "diagram": "On-Prem ─> Equinix ECX ─> Azure ER Hosted ─> ERGW ─> VNet",
            "sop_titles": [],
        },
        "sdwan_overlay": {
            "pattern": "SD-WAN Overlay via Virtual WAN",
            "description": "Azure Virtual WAN with integrated SD-WAN partner NVAs. Automated branch provisioning, centralized routing intent policy.",
            "pros": ["Automated branch provisioning", "Integrated SD-WAN + firewall", "Global hub mesh"],
            "cons": ["NVA licensing cost", "vWAN routing intent complexity"],
            "use_cases": ["Global WAN replacement", "Branch consolidation"],
            "csp_service": "Azure Virtual WAN Hub + SD-WAN NVA",
            "compliance": ["fedramp_mod"],
            "dod_note": "SD-WAN NVAs must be FedRAMP-authorized for Azure Gov deployment.",
            "diagram": "Branches ─[SD-WAN]─> Azure Virtual WAN Hub ─> VNets",
            "sop_titles": ["Azure Virtual WAN — Hub Deployment with Routing Intent (Firewall Policy)"],
        },
    },
    "gcp": {
        "ipsec_vpn": {
            "pattern": "IPSec VPN (HA VPN)",
            "description": "GCP HA VPN Gateway with dual tunnels and BGP. 99.99% availability SLA. Supports dynamic routing via Cloud Router.",
            "pros": ["99.99% SLA with HA VPN", "BGP via Cloud Router", "No dedicated hardware required", "Fast provisioning"],
            "cons": ["3 Gbps/tunnel max (x2 active tunnels)", "Internet path variability", "Not BCAP-compatible"],
            "use_cases": ["Dev/Test", "Lower-bandwidth workloads", "IC backup"],
            "csp_service": "GCP HA VPN Gateway + Cloud Router",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "GCP GovCloud requires IKEv2 with AES-256. Dedicated Interconnect required for BCAP-compatible DoD deployments.",
            "diagram": "On-Prem ─[IKEv2/BGP]─> Internet ─> GCP HA VPN GW ─> Cloud Router ─> VPC",
            "sop_titles": ["GCP Cloud VPN — HA VPN with BGP (Active/Active Tunnels)"],
        },
        "dedicated_private": {
            "pattern": "Dedicated Interconnect",
            "description": "Physical 10G or 100G fiber to GCP Interconnect facility. VLAN attachments connect to Cloud Router. Requires presence at GCP colocation facility.",
            "pros": ["Sub-ms latency", "10G/100G bandwidth", "99.99% SLA with redundant circuits", "Lower egress cost"],
            "cons": ["Colocation presence required", "4+ week lead time", "Higher monthly commitment"],
            "use_cases": ["Production GCP workloads", "DoD IL4 on GCP", "High-bandwidth data pipelines"],
            "csp_service": "GCP Dedicated Interconnect + VLAN Attachment + Cloud Router",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "Required for DoD IL4 on GCP. MACsec not supported — use VPN over Interconnect for encryption in transit where required.",
            "diagram": "On-Prem ─[10G/100G fiber]─> GCP Colo ─> Cloud Interconnect ─> VLAN Attach ─> Cloud Router ─> VPC",
            "sop_titles": ["GCP Cloud Interconnect — Dedicated Interconnect (10G/100G VLAN attachment)"],
        },
        "partner_managed": {
            "pattern": "Partner Interconnect",
            "description": "GCP connectivity via NSP without direct colocation. Provider establishes the physical connection to GCP Interconnect facility.",
            "pros": ["No colocation required", "100 Mbps–50 Gbps options", "Wider geographic availability"],
            "cons": ["Provider dependency", "Higher latency than Dedicated IC"],
            "use_cases": ["Locations without GCP colo presence", "Bandwidth-flexible requirements"],
            "csp_service": "GCP Partner Interconnect + VLAN Attachment",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "Partner must be GCP-authorized and provide a FedRAMP-compliant service.",
            "diagram": "On-Prem ─> NSP ─> GCP Partner IC ─> VLAN Attach ─> Cloud Router ─> VPC",
            "sop_titles": ["GCP Cloud Interconnect — Dedicated Interconnect (10G/100G VLAN attachment)"],
        },
        "sdwan_overlay": {
            "pattern": "SD-WAN via Network Connectivity Center",
            "description": "GCP NCC with Router Appliance spokes for SD-WAN integration. Third-party SD-WAN appliances peer BGP with Cloud Router via NCC.",
            "pros": ["Centralized hub visibility", "BGP integration with Cloud Router", "Multi-region spoke aggregation"],
            "cons": ["Third-party NVA cost", "Complex BGP configuration"],
            "use_cases": ["Multi-site WAN replacement", "Hybrid cloud orchestration"],
            "csp_service": "GCP NCC Hub + Router Appliance Spoke + Cloud Router",
            "compliance": ["fedramp_mod"],
            "dod_note": "NVA appliance must be FedRAMP-authorized for GovCloud deployments.",
            "diagram": "Sites ─[SD-WAN]─> NCC Router Appliance Spoke ─> Cloud Router ─> VPC",
            "sop_titles": [],
        },
    },
    "oci": {
        "ipsec_vpn": {
            "pattern": "IPSec VPN (VPN Connect)",
            "description": "OCI VPN Connect provides up to 4 redundant IPSec tunnels per connection. Supports BGP and static routing. Terminates at DRG.",
            "pros": ["Up to 4 Gbps (4 × 1 Gbps tunnels)", "BGP or static routing", "No additional license cost", "Redundant tunnels"],
            "cons": ["Internet path variability", "1 Gbps per tunnel limit", "Not BCAP-compatible"],
            "use_cases": ["Dev/Test", "Remote access", "FastConnect backup"],
            "csp_service": "OCI VPN Connect + CPE + DRG",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "Use IKEv2 with AES-256-GCM. FastConnect required for DoD BCAP-compatible deployments.",
            "diagram": "On-Prem CPE ─[IKEv2 × 4 tunnels]─> OCI DRG ─> VCN",
            "sop_titles": ["OCI VPN Connect — IPSec with BGP and Static Route Failover"],
        },
        "dedicated_private": {
            "pattern": "FastConnect",
            "description": "OCI FastConnect provides 1G–100G private connectivity from on-prem. Available via OCI partners or direct colocation at FastConnect locations.",
            "pros": ["1G–100G bandwidth", "Consistent latency", "BGP support", "AWS-OCI native interconnect partnership"],
            "cons": ["Partner/colo required", "4–8 week lead time"],
            "use_cases": ["Production OCI workloads", "DoD IL4/IL5 on OCI", "AWS-OCI hybrid workloads"],
            "csp_service": "OCI FastConnect + Virtual Circuit + DRG",
            "compliance": ["fedramp_high", "il4", "il5"],
            "dod_note": "OCI is FedRAMP High authorized (IL4 compliant). FastConnect required for BCAP path.",
            "diagram": "On-Prem ─[Fiber via Partner/Colo]─> OCI FastConnect ─> Virtual Circuit ─> DRG ─> VCN",
            "sop_titles": ["OCI FastConnect — Provisioning via Partner or Colocation"],
        },
        "partner_managed": {
            "pattern": "FastConnect via Partner",
            "description": "OCI FastConnect delivered via OCI authorized network partner. Partner manages the physical handoff.",
            "pros": ["No colo required", "Flexible bandwidth", "Global partner availability"],
            "cons": ["Partner dependency", "Additional cost layer"],
            "use_cases": ["Locations without OCI colo access"],
            "csp_service": "OCI FastConnect Partner Circuit + Virtual Circuit",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "Partner must be FedRAMP-authorized.",
            "diagram": "On-Prem ─> OCI FastConnect Partner ─> Virtual Circuit ─> DRG ─> VCN",
            "sop_titles": [],
        },
        "sdwan_overlay": {
            "pattern": "SD-WAN via DRG v2",
            "description": "OCI DRG v2 supports attachment of SD-WAN NVAs via VCN attachments with route tables for policy-based forwarding.",
            "pros": ["Centralized routing policy", "Multi-VCN aggregation", "High throughput (100 Gbps DRG)"],
            "cons": ["NVA cost", "DRG v2 required (v1 not supported)"],
            "use_cases": ["Multi-site WAN consolidation"],
            "csp_service": "OCI DRG v2 + SD-WAN NVA VCN",
            "compliance": ["fedramp_mod"],
            "dod_note": "NVA must be FedRAMP-authorized.",
            "diagram": "Sites ─[SD-WAN]─> NVA VCN ─> DRG v2 ─> Mission VCNs",
            "sop_titles": [],
        },
    },
    "ibm": {
        "ipsec_vpn": {
            "pattern": "IPSec VPN Gateway",
            "description": "IBM Cloud VPN Gateway provides policy-based IKEv2 IPSec connectivity. 650 Mbps aggregate per gateway with active/standby member pairs.",
            "pros": ["No additional hardware required", "Active/standby HA", "Fast provisioning"],
            "cons": ["Policy-based only (no BGP)", "650 Mbps aggregate limit", "Not BCAP-compatible"],
            "use_cases": ["Dev/Test", "Small branch offices", "Direct Link backup"],
            "csp_service": "IBM Cloud VPN Gateway + IKEv2 connection",
            "compliance": ["fedramp_mod", "fedramp_high"],
            "dod_note": "Direct Link required for DoD BCAP-compatible IBM Cloud deployments.",
            "diagram": "On-Prem ─[IKEv2/IPSec]─> IBM VPN Gateway ─> VPC",
            "sop_titles": ["IBM Cloud VPN Gateway — Policy-Based IPSec Configuration"],
        },
        "dedicated_private": {
            "pattern": "Direct Link 2.0",
            "description": "IBM Direct Link 2.0 Dedicated provides 1G–10G physical fiber to IBM Cloud. Consistent performance, low latency, private connectivity.",
            "pros": ["1G–10G bandwidth", "Consistent latency", "BGP support", "FIPS 140-2 L4 HSM available (HPCS)"],
            "cons": ["Physical circuit lead time", "Higher cost", "Colo/PoP required"],
            "use_cases": ["Production IBM Cloud workloads", "FedRAMP High workloads", "Financial/regulated industries"],
            "csp_service": "IBM Direct Link 2.0 Dedicated + BGP + Transit Gateway",
            "compliance": ["fedramp_high", "il4"],
            "dod_note": "IBM Cloud FedRAMP High authorized. Direct Link required for BCAP-compatible path.",
            "diagram": "On-Prem ─[Fiber]─> IBM PoP ─> Direct Link 2.0 ─> BGP ─> VPC",
            "sop_titles": ["IBM Cloud Direct Link 2.0 — Dedicated Connection Setup"],
        },
        "partner_managed": {
            "pattern": "Direct Link 2.0 Connect",
            "description": "IBM Direct Link Connect via network service provider. Provider manages the physical connection. Flexible 50 Mbps–5 Gbps options.",
            "pros": ["No colo required", "50 Mbps–5 Gbps flexibility", "Wide provider availability"],
            "cons": ["Provider SLA dependency", "Higher per-Mbps cost vs Dedicated"],
            "use_cases": ["Smaller bandwidth requirements", "No colo presence"],
            "csp_service": "IBM Direct Link 2.0 Connect via NSP",
            "compliance": ["fedramp_high"],
            "dod_note": "NSP must be FedRAMP-authorized.",
            "diagram": "On-Prem ─> NSP ─> IBM Direct Link Connect ─> VPC",
            "sop_titles": [],
        },
        "sdwan_overlay": {
            "pattern": "SD-WAN + IBM Transit Gateway",
            "description": "IBM Transit Gateway aggregates VPCs with SD-WAN NVA attachments for policy-based WAN routing.",
            "pros": ["Cross-region VPC connectivity", "BGP integration", "Centralized policy"],
            "cons": ["NVA cost", "Limited SD-WAN partners for IBM Cloud"],
            "use_cases": ["Multi-VPC consolidation"],
            "csp_service": "IBM Transit Gateway + SD-WAN NVA VPC",
            "compliance": ["fedramp_mod"],
            "dod_note": "NVA must be FedRAMP-authorized.",
            "diagram": "Sites ─[SD-WAN]─> NVA VPC ─> IBM Transit GW ─> VPCs",
            "sop_titles": [],
        },
    },
}

# ── CSP-to-CSP Patterns ───────────────────────────────────────────────────────

_C2C_PATTERNS: list[dict[str, Any]] = [
    {
        "pattern": "ipsec_internet",
        "label": "IPSec over Internet",
        "description": "VPN tunnels between CSP gateways over public internet. Universal availability but highest latency and bandwidth-limited.",
        "availability": "universal",
        "specific_csps": [],
        "latency_class": "high",
        "bandwidth": "1–10 Gbps per CSP gateway",
        "cost_class": "low",
        "notes": "Quickest to deploy. Not suitable for latency-sensitive or high-bandwidth workloads. BGP over IPSec enables dynamic routing.",
        "sop_titles": [
            "AWS-to-Azure Connectivity — IPSec VPN (BGP, Active/Passive)",
            "AWS-to-GCP Connectivity — HA VPN with Cloud Router BGP",
        ],
    },
    {
        "pattern": "cloud_exchange",
        "label": "Cloud Exchange (Equinix / Megaport)",
        "description": "Private L2/L3 interconnect between CSPs via colocation exchange fabric. Low latency, high bandwidth, no internet exposure.",
        "availability": "universal",
        "specific_csps": [],
        "latency_class": "low",
        "bandwidth": "50 Mbps – 10 Gbps per virtual connection",
        "cost_class": "medium",
        "notes": "Best for production CSP-to-CSP workloads. Equinix Fabric and Megaport MCR both support BGP and VLAN-tagged connections to any major CSP.",
        "sop_titles": [
            "Equinix Fabric (ECX) — L3 Connection Between Two CSPs via MCR",
            "Megaport MCR — Multi-CSP Transit Routing Configuration",
        ],
    },
    {
        "pattern": "native_backbone",
        "label": "Native CSP Backbone Partnership",
        "description": "Private fiber connection directly between specific CSP pairs via native partnership (e.g., AWS-OCI Oracle Interconnect). Lowest latency for specific pairs.",
        "availability": "specific_pair",
        "specific_csps": [["aws", "oci"]],
        "latency_class": "low",
        "bandwidth": "Up to 10 Gbps",
        "cost_class": "low",
        "notes": "Only available for AWS↔OCI today. Uses Oracle-native FastConnect on OCI side and AWS Direct Connect on AWS side, with private fiber between them.",
        "sop_titles": [
            "AWS-to-OCI Native Interconnect — Oracle Partnership Circuit",
        ],
    },
    {
        "pattern": "transit_hub",
        "label": "Managed Multi-Cloud Transit (Aviatrix / Alkira)",
        "description": "Third-party transit network controller orchestrates CSP-to-CSP routing via gateway VMs deployed in each cloud. Provides centralized policy, visibility, and security.",
        "availability": "universal",
        "specific_csps": [],
        "latency_class": "medium",
        "bandwidth": "Controller/gateway-defined",
        "cost_class": "high",
        "notes": "Best for complex multi-CSP environments needing unified security policy, encryption, and east-west inspection. Aviatrix is FedRAMP-authorized.",
        "sop_titles": [
            "Aviatrix Multi-Cloud Transit — Controller + Gateway Deployment",
        ],
    },
]


# ── SCCA Reference Flow ───────────────────────────────────────────────────────

def _get_scca_constants() -> tuple[dict, dict]:
    """Lazy import to avoid circular dependency."""
    try:
        from tools.network.constants import SCCA_COMPONENTS, SCCA_CSP_MAPPING
        return SCCA_COMPONENTS, SCCA_CSP_MAPPING
    except ImportError:
        return {}, {}


# ── Public API ────────────────────────────────────────────────────────────────

def get_connectivity_matrix() -> dict[str, dict[str, dict[str, Any]]]:
    """Return full CSP → connectivity-type → service property dict."""
    return CONNECTIVITY_MATRIX


def get_onprem_to_csp_patterns(csp: str, pattern_type: str) -> dict[str, Any]:
    """Return on-prem → CSP pattern detail for given CSP and pattern type."""
    csp_patterns = _ONPREM_PATTERNS.get(csp, {})
    pattern = csp_patterns.get(pattern_type, {})
    if not pattern:
        return {
            "error": f"No pattern '{pattern_type}' found for CSP '{csp}'",
            "available_types": list(csp_patterns.keys()),
        }
    return pattern


def get_csp_to_csp_patterns(src_csp: str, dst_csp: str) -> list[dict[str, Any]]:
    """Return available CSP-to-CSP connectivity patterns for the given CSP pair."""
    result = []
    pair_key = tuple(sorted([src_csp, dst_csp]))
    for p in _C2C_PATTERNS:
        if p["availability"] == "universal":
            result.append(p)
        elif p["availability"] == "specific_pair":
            for pair in p["specific_csps"]:
                if tuple(sorted(pair)) == pair_key:
                    result.append(p)
                    break
    return result


def get_scca_flow() -> dict[str, Any]:
    """Return ordered SCCA reference architecture data for the DoD/SCCA tab."""
    scca_comps, scca_csp = _get_scca_constants()

    ordered_components = []
    for key in ["bcap", "vdss", "vdms", "tccm"]:
        comp = scca_comps.get(key, {})
        csp_services = scca_csp.get(key, {})
        ordered_components.append({
            "key": key,
            "label": comp.get("acronym", key.upper()),
            "name": comp.get("name", ""),
            "description": comp.get("description", ""),
            "disa_ref": comp.get("disa_ref", ""),
            "requirements": comp.get("requirements", []),
            "csp_services": csp_services,
        })

    return {
        "flow_description": (
            "DoD mission traffic flows: On-Prem NIPRNet → BCAP (DISA-managed boundary) → "
            "VDSS (perimeter security inspection) → VDMS (host security services) → "
            "Mission Owner VPC/VNet/VCN"
        ),
        "flow_ascii": (
            "NIPRNet ─[DX/ER/IC/FC/DL]─> BCAP ─> VDSS ─> VDMS ─> Mission VPC/VNet/VCN"
        ),
        "components": ordered_components,
        "sop_titles": [
            "BCAP On-Boarding — DoD Organization DISN Connection Request Process",
            "AWS SCCA — VDSS Inspection VPC with Network Firewall and TGW",
            "AWS SCCA — VDMS Account Configuration (SecurityHub, Inspector, Managed AD)",
            "Azure SACA — Hub VNet VDSS Configuration (Azure Firewall + App Gateway WAF)",
            "Azure SACA — VDMS Identity Stack (Entra ID CAC Auth, Defender for Cloud)",
            "OCI SCCA Landing Zone — VDSS VCN with Network Firewall Deployment",
            "OCI SCCA Landing Zone — VDMS VCN with Cloud Guard and Vault",
            "TCCM Setup — Cloud Credential Management Plan (CCMP) Workflow",
            "PPSM Registration — Ports/Protocols/Services Management Submission Process",
        ],
    }


def get_resiliency_tiers() -> dict[str, Any]:
    """Return RESILIENCY_TIERS from constants."""
    try:
        from tools.network.constants import RESILIENCY_TIERS
        return RESILIENCY_TIERS
    except ImportError:
        return {}


def get_sop_deep_link(title: str) -> str | None:
    """Return URL deep-link to SOP library filtered by title."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT sop_id FROM ndc_sops WHERE title = %s LIMIT 1", (title,)
        ).fetchone()
        conn.close()
        if row:
            return f"/network/sops?q={urllib.parse.quote(title)}"
    except Exception:
        pass
    return None


def list_sops_by_category(category: str) -> list[dict[str, Any]]:
    """Return approved SOPs for a category (proxy with minimal fields)."""
    try:
        from tools.network.sops import list_sops
        return [
            {"sop_id": s["sop_id"], "title": s["title"],
             "category": s["category"], "status": s["status"], "version": s["version"]}
            for s in list_sops(category=category, status="approved", limit=200)
        ]
    except Exception:
        return []


# ── Pattern Seeder ────────────────────────────────────────────────────────────

def seed_patterns(dry_run: bool = False) -> int:
    """Seed nc_connectivity_patterns from HYBRID_CONNECTIVITY_PATTERNS. Idempotent (INSERT OR IGNORE)."""
    import itertools
    import json as _json

    try:
        from tools.network.constants import HYBRID_CONNECTIVITY_PATTERNS
    except ImportError:
        return 0

    all_csps = ["aws", "azure", "gcp", "oci", "ibm"]
    rows: list[tuple] = []

    for pattern_key, pdata in HYBRID_CONNECTIVITY_PATTERNS.items():
        label = pdata.get("label", pattern_key)
        description = pdata.get("description", "")
        resiliency = pdata.get("resiliency", "high")
        cost_tier = pdata.get("cost", "medium")
        applicable: list[str] = pdata.get("applicable_csps", [])

        # Collect node type hints from any CSP sub-dicts in the pattern
        node_types: list[str] = []
        for csp in (applicable or all_csps):
            csp_data = pdata.get(csp, {})
            if isinstance(csp_data, dict):
                for v in csp_data.values():
                    if isinstance(v, str):
                        node_types.append(v)
        # Deduplicate preserving order
        seen: set[str] = set()
        node_types = [x for x in node_types if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]

        is_c2c = "multi_cloud" in pattern_key
        if is_c2c:
            # One row per ordered CSP pair
            for src, dst in itertools.combinations(applicable or all_csps, 2):
                csp_pair = f"{src}:{dst}"
                row_id = f"{pattern_key}:{csp_pair}"
                rows.append((row_id, csp_pair, pattern_key, label, description,
                              resiliency, cost_tier, "[]", _json.dumps(node_types), "[]"))
        elif applicable:
            # One row per on-prem → CSP direction
            for csp in applicable:
                csp_pair = f"onprem:{csp}"
                row_id = f"{pattern_key}:{csp}"
                rows.append((row_id, csp_pair, pattern_key, label, description,
                              resiliency, cost_tier, "[]", _json.dumps(node_types), "[]"))
        else:
            # Universal / technology pattern — single "multi" row
            row_id = f"{pattern_key}:multi"
            rows.append((row_id, "multi", pattern_key, label, description,
                         resiliency, cost_tier, "[]", _json.dumps(node_types), "[]"))

    if dry_run:
        return len(rows)

    conn = _get_conn()
    inserted = 0
    try:
        for row in rows:
            cur = conn.execute(
                """INSERT OR IGNORE INTO nc_connectivity_patterns
                   (id, csp_pair, pattern_key, label, description, resiliency, cost_tier,
                    use_cases, node_types, sop_refs)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                row,
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


if __name__ == "__main__":
    import argparse
    import json as _json2
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    ap = argparse.ArgumentParser(description="Seed nc_connectivity_patterns table")
    ap.add_argument("--dry-run", action="store_true", help="Count rows without writing")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args()

    count = seed_patterns(dry_run=args.dry_run)
    result = {"rows": count, "dry_run": args.dry_run, "status": "ok"}
    if args.as_json:
        print(_json2.dumps(result))
    else:
        action = "Would insert" if args.dry_run else "Inserted"
        print(f"{action} {count} pattern rows into nc_connectivity_patterns")
        sys.exit(0)
