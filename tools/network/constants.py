# CUI // SP-CTI — ICDEV Network Canvas Constants
# Classification: CUI — Controlled Unclassified Information
"""
Network Canvas module-level constants.

Extracted from the Network Canvas app.py to allow reuse across modules
without importing Flask or any web framework dependencies.
"""

# ── Cloud Networking Object Types (all major CSPs) ────────────────────────────
CLOUD_OBJECTS = {
    "aws": [
        {"type": "aws-vpc", "label": "VPC", "icon": "VPC", "desc": "AWS Virtual Private Cloud"},
        {"type": "aws-subnet", "label": "Subnet", "icon": "SUB", "desc": "AWS VPC Subnet (public/private)"},
        {"type": "aws-tgw", "label": "Transit GW", "icon": "TGW", "desc": "AWS Transit Gateway — hub for VPC/VPN/DX"},
        {"type": "aws-dx", "label": "Direct Connect", "icon": "DX", "desc": "AWS Direct Connect — dedicated circuit"},
        {"type": "aws-vpn", "label": "Site-to-Site VPN", "icon": "VPN", "desc": "AWS Site-to-Site VPN (IPSec)"},
        {"type": "aws-alb", "label": "ALB", "icon": "ALB", "desc": "AWS Application Load Balancer (L7)"},
        {"type": "aws-nlb", "label": "NLB", "icon": "NLB", "desc": "AWS Network Load Balancer (L4)"},
        {"type": "aws-cloudfront", "label": "CloudFront", "icon": "CF", "desc": "AWS CloudFront CDN"},
        {"type": "aws-r53", "label": "Route 53", "icon": "R53", "desc": "AWS Route 53 DNS"},
        {"type": "aws-nfw", "label": "Network FW", "icon": "NFW", "desc": "AWS Network Firewall"},
        {"type": "aws-waf", "label": "WAF", "icon": "WAF", "desc": "AWS WAF — Web Application Firewall"},
        {"type": "aws-gw-ep", "label": "GW Endpoint", "icon": "GEP", "desc": "AWS Gateway/Interface VPC Endpoint"},
    ],
    "azure": [
        {"type": "az-vnet", "label": "VNet", "icon": "VNT", "desc": "Azure Virtual Network"},
        {"type": "az-subnet", "label": "Subnet", "icon": "SUB", "desc": "Azure VNet Subnet"},
        {"type": "az-vwan", "label": "Virtual WAN", "icon": "WAN", "desc": "Azure Virtual WAN hub"},
        {"type": "az-er", "label": "ExpressRoute", "icon": "ER", "desc": "Azure ExpressRoute — dedicated circuit"},
        {"type": "az-vpn-gw", "label": "VPN Gateway", "icon": "VGW", "desc": "Azure VPN Gateway (IPSec/IKEv2)"},
        {"type": "az-fw", "label": "Azure Firewall", "icon": "AFW", "desc": "Azure Firewall (L3-L7)"},
        {"type": "az-appgw", "label": "App Gateway", "icon": "AGW", "desc": "Azure Application Gateway (L7 LB + WAF)"},
        {"type": "az-front", "label": "Front Door", "icon": "FD", "desc": "Azure Front Door — global CDN + WAF"},
        {"type": "az-dns", "label": "Azure DNS", "icon": "DNS", "desc": "Azure DNS zones"},
        {"type": "az-bastion", "label": "Bastion", "icon": "BST", "desc": "Azure Bastion — secure RDP/SSH"},
        {"type": "az-nsg", "label": "NSG", "icon": "NSG", "desc": "Azure Network Security Group"},
    ],
    "gcp": [
        {"type": "gcp-vpc", "label": "VPC", "icon": "VPC", "desc": "GCP Virtual Private Cloud (global)"},
        {"type": "gcp-subnet", "label": "Subnet", "icon": "SUB", "desc": "GCP VPC Subnet (regional)"},
        {"type": "gcp-ic", "label": "Interconnect", "icon": "IC", "desc": "GCP Cloud Interconnect — dedicated/partner"},
        {"type": "gcp-vpn", "label": "Cloud VPN", "icon": "VPN", "desc": "GCP Cloud VPN (HA VPN)"},
        {"type": "gcp-nat", "label": "Cloud NAT", "icon": "NAT", "desc": "GCP Cloud NAT gateway"},
        {"type": "gcp-lb", "label": "Cloud LB", "icon": "CLB", "desc": "GCP Cloud Load Balancer (global/regional)"},
        {"type": "gcp-armor", "label": "Cloud Armor", "icon": "ARM", "desc": "GCP Cloud Armor — DDoS + WAF"},
        {"type": "gcp-cdn", "label": "Cloud CDN", "icon": "CDN", "desc": "GCP Cloud CDN"},
        {"type": "gcp-dns", "label": "Cloud DNS", "icon": "DNS", "desc": "GCP Cloud DNS"},
        {"type": "gcp-router", "label": "Cloud Router", "icon": "CR", "desc": "GCP Cloud Router — BGP peering"},
    ],
    "oci": [
        {"type": "oci-vcn", "label": "VCN", "icon": "VCN", "desc": "OCI Virtual Cloud Network"},
        {"type": "oci-subnet", "label": "Subnet", "icon": "SUB", "desc": "OCI VCN Subnet"},
        {"type": "oci-drg", "label": "DRG", "icon": "DRG", "desc": "OCI Dynamic Routing Gateway"},
        {"type": "oci-fc", "label": "FastConnect", "icon": "FC", "desc": "OCI FastConnect — dedicated circuit"},
        {"type": "oci-lb", "label": "Load Balancer", "icon": "OLB", "desc": "OCI Load Balancer"},
        {"type": "oci-waf", "label": "WAF", "icon": "WAF", "desc": "OCI Web Application Firewall"},
        {"type": "oci-nsg", "label": "NSG", "icon": "NSG", "desc": "OCI Network Security Group"},
    ],
    "ibm": [
        {"type": "ibm-vpc", "label": "VPC", "icon": "VPC", "desc": "IBM Cloud VPC"},
        {"type": "ibm-subnet", "label": "Subnet", "icon": "SUB", "desc": "IBM Cloud VPC Subnet"},
        {"type": "ibm-dl", "label": "Direct Link", "icon": "DL", "desc": "IBM Cloud Direct Link — dedicated circuit"},
        {"type": "ibm-vpn", "label": "VPN Gateway", "icon": "VPN", "desc": "IBM Cloud VPN Gateway"},
        {"type": "ibm-lb", "label": "Load Balancer", "icon": "ILB", "desc": "IBM Cloud Load Balancer"},
        {"type": "ibm-tg", "label": "Transit GW", "icon": "TG", "desc": "IBM Cloud Transit Gateway"},
    ],
    "multi_cloud": [
        {"type": "cloud-peering", "label": "Cloud Peering", "icon": "PER", "desc": "Cross-cloud peering / interconnect"},
        {"type": "sdwan-overlay", "label": "SD-WAN", "icon": "SDW", "desc": "SD-WAN overlay (Cisco Viptela, VMware VeloCloud, etc.)"},
        {"type": "sase-pop", "label": "SASE PoP", "icon": "SSE", "desc": "SASE/SSE point of presence (Zscaler, Prisma, etc.)"},
        {"type": "internet-exchange", "label": "IXP", "icon": "IXP", "desc": "Internet Exchange Point"},
        {"type": "cloud-region", "label": "Region", "icon": "REG", "desc": "Cloud region/availability zone boundary"},
    ],
}

# Default auto-populated components per CSP group (used when group_type="full")
CSP_GROUP_DEFAULTS = {
    "aws": [
        {"type": "aws-vpc", "label": "VPC", "dx": 40, "dy": 60},
        {"type": "aws-tgw", "label": "Transit Gateway", "dx": 200, "dy": 60},
        {"type": "aws-subnet", "label": "Public Subnet", "dx": 40, "dy": 150},
        {"type": "aws-subnet", "label": "Private Subnet", "dx": 200, "dy": 150},
        {"type": "aws-nfw", "label": "Network Firewall", "dx": 120, "dy": 230},
        {"type": "aws-alb", "label": "ALB", "dx": 40, "dy": 230},
        {"type": "aws-r53", "label": "Route 53", "dx": 320, "dy": 60},
    ],
    "azure": [
        {"type": "az-vnet", "label": "VNet", "dx": 40, "dy": 60},
        {"type": "az-vwan", "label": "Virtual WAN", "dx": 200, "dy": 60},
        {"type": "az-subnet", "label": "Subnet", "dx": 40, "dy": 150},
        {"type": "az-fw", "label": "Azure Firewall", "dx": 200, "dy": 150},
        {"type": "az-appgw", "label": "App Gateway", "dx": 40, "dy": 230},
        {"type": "az-nsg", "label": "NSG", "dx": 200, "dy": 230},
        {"type": "az-er", "label": "ExpressRoute", "dx": 320, "dy": 60},
    ],
    "gcp": [
        {"type": "gcp-vpc", "label": "VPC", "dx": 40, "dy": 60},
        {"type": "gcp-subnet", "label": "Subnet", "dx": 200, "dy": 60},
        {"type": "gcp-router", "label": "Cloud Router", "dx": 40, "dy": 150},
        {"type": "gcp-lb", "label": "Cloud LB", "dx": 200, "dy": 150},
        {"type": "gcp-armor", "label": "Cloud Armor", "dx": 40, "dy": 230},
        {"type": "gcp-ic", "label": "Interconnect", "dx": 320, "dy": 60},
    ],
    "oci": [
        {"type": "oci-vcn", "label": "VCN", "dx": 40, "dy": 60},
        {"type": "oci-drg", "label": "DRG", "dx": 200, "dy": 60},
        {"type": "oci-subnet", "label": "Subnet", "dx": 40, "dy": 150},
        {"type": "oci-lb", "label": "Load Balancer", "dx": 200, "dy": 150},
        {"type": "oci-fc", "label": "FastConnect", "dx": 320, "dy": 60},
    ],
    "ibm": [
        {"type": "ibm-vpc", "label": "VPC", "dx": 40, "dy": 60},
        {"type": "ibm-tg", "label": "Transit Gateway", "dx": 200, "dy": 60},
        {"type": "ibm-subnet", "label": "Subnet", "dx": 40, "dy": 150},
        {"type": "ibm-lb", "label": "Load Balancer", "dx": 200, "dy": 150},
        {"type": "ibm-dl", "label": "Direct Link", "dx": 320, "dy": 60},
    ],
}

# ── Compliance Regimes & Rule Definitions ─────────────────────────────────────
# 7 regimes with crosswalk mapping. Each rule is deterministic (no LLM needed).

COMPLIANCE_REGIMES = {
    "fisma_high": {"name": "FISMA High", "framework": "NIST 800-53 Rev 5", "baseline": "High"},
    "stig": {"name": "DISA STIG", "framework": "DoD STIG", "baseline": "Network"},
    "fips": {"name": "FIPS 140-2/3", "framework": "FIPS", "baseline": "Level 2"},
    "zta": {"name": "Zero Trust (NIST 800-207)", "framework": "NIST 800-207", "baseline": "Advanced"},
    "cjis": {"name": "CJIS Security Policy", "framework": "FBI CJIS", "baseline": "5.9.1"},
    "icd503": {"name": "ICD 503 (IC)", "framework": "ODNI ICD 503", "baseline": "Full"},
    "cnss1253": {"name": "CNSS 1253 (NSS)", "framework": "CNSS", "baseline": "High"},
}

# Crosswalk: rule_id -> list of regimes it applies to
# Rules run once, findings tagged with all applicable regimes
COMPLIANCE_RULES = [
    # ── Encryption ──────────────────────────────────────────────────────────
    {"id": "NET-ENC-001", "title": "WAN links require encryption",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["fisma_high", "stig", "fips", "cjis", "icd503", "cnss1253"],
     "description": "All WAN/inter-site links must use IPSec, MACsec, or Type 1 encryption to protect CUI in transit (NIST SC-8, SC-13).",
     "check": "wan_encryption"},

    {"id": "NET-ENC-002", "title": "Type 1 (NSA) encryption required for SECRET+",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["icd503", "cnss1253"],
     "description": "SECRET and above require NSA Type 1 encryption (KG-175D, KG-250, etc.) per CNSS Policy 15.",
     "check": "type1_encryption"},

    {"id": "NET-ENC-003", "title": "Encryptor speed rating matches link bandwidth",
     "severity": "CAT2", "category": "encryption",
     "regimes": ["fisma_high", "stig", "fips", "cnss1253"],
     "description": "Encryption device throughput must meet or exceed link bandwidth (e.g., KG-175D ≤10G, KG-250 ≤100G).",
     "check": "encryptor_speed_match"},

    {"id": "NET-ENC-004", "title": "FIPS 140-2/3 validated crypto on all encrypted links",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["fisma_high", "fips", "cjis", "icd503"],
     "description": "All cryptographic modules must be FIPS 140-2 Level 2+ validated (NIST SC-13).",
     "check": "fips_validated_crypto"},

    # ── Redundancy ──────────────────────────────────────────────────────────
    {"id": "NET-RED-001", "title": "Core/distribution devices require dual uplinks",
     "severity": "CAT1", "category": "redundancy",
     "regimes": ["fisma_high", "stig", "cjis", "cnss1253"],
     "description": "Core and distribution switches/routers must have ≥2 uplinks to prevent single point of failure (NIST CP-8, SC-36).",
     "check": "core_dual_uplinks"},

    {"id": "NET-RED-002", "title": "Diverse path routing for critical circuits",
     "severity": "CAT2", "category": "redundancy",
     "regimes": ["fisma_high", "stig", "cnss1253"],
     "description": "Critical circuits should traverse physically diverse paths (different conduit/provider) per NIST CP-8.",
     "check": "diverse_paths"},

    {"id": "NET-RED-003", "title": "Access layer single uplink acceptable with documentation",
     "severity": "CAT3", "category": "redundancy",
     "regimes": ["fisma_high", "stig"],
     "description": "Access-layer switches with single uplink are acceptable if documented in the SSP with risk acceptance.",
     "check": "access_single_uplink_documented"},

    # ── Boundary / Firewall ──────────────────────────────────────────────────
    {"id": "NET-BND-001", "title": "Firewall between internal and WAN/internet",
     "severity": "CAT1", "category": "boundary",
     "regimes": ["fisma_high", "stig", "zta", "cjis", "icd503", "cnss1253"],
     "description": "Every site must have a firewall between internal networks and WAN/internet segments (NIST SC-7).",
     "check": "firewall_at_boundary"},

    {"id": "NET-BND-002", "title": "Micro-segmentation between security zones",
     "severity": "CAT2", "category": "boundary",
     "regimes": ["zta", "fisma_high", "icd503"],
     "description": "Zero Trust requires network segmentation between security zones — no flat networks (NIST 800-207 §3.1).",
     "check": "micro_segmentation"},

    {"id": "NET-BND-003", "title": "Cloud VPC/VNet isolation from on-prem",
     "severity": "CAT2", "category": "boundary",
     "regimes": ["fisma_high", "stig", "zta", "cjis"],
     "description": "Cloud environments must be isolated from on-prem via dedicated interconnect with firewall/ACL (NIST SC-7).",
     "check": "cloud_isolation"},

    # ── Management Plane ──────────────────────────────────────────────────────
    {"id": "NET-MGT-001", "title": "Out-of-band management network",
     "severity": "CAT2", "category": "management",
     "regimes": ["fisma_high", "stig", "icd503", "cnss1253"],
     "description": "Network devices should be managed via out-of-band (OOB) management network, separate from production traffic (NIST SC-7(13)).",
     "check": "oob_management"},

    {"id": "NET-MGT-002", "title": "In-band management encrypted (SSH/HTTPS only)",
     "severity": "CAT2", "category": "management",
     "regimes": ["fisma_high", "stig", "cjis", "zta"],
     "description": "If in-band management is used, it must be encrypted (SSH, HTTPS) — no Telnet, HTTP, SNMPv1/v2 (NIST SC-8).",
     "check": "inband_encrypted"},

    # ── DNS ──────────────────────────────────────────────────────────────────
    {"id": "NET-DNS-001", "title": "DNS redundancy (≥2 DNS servers)",
     "severity": "CAT2", "category": "dns",
     "regimes": ["fisma_high", "stig", "cjis"],
     "description": "At least 2 DNS resolvers or cloud DNS service for fault tolerance (NIST SC-20, SC-22).",
     "check": "dns_redundancy"},

    # ── Zero Trust Specific ──────────────────────────────────────────────────
    {"id": "NET-ZTA-001", "title": "No implicit trust zones",
     "severity": "CAT1", "category": "zta",
     "regimes": ["zta"],
     "description": "Zero Trust architecture requires all access to be explicitly verified — no flat trust zones (NIST 800-207 §2.1).",
     "check": "no_implicit_trust"},

    {"id": "NET-ZTA-002", "title": "East-west traffic inspection",
     "severity": "CAT2", "category": "zta",
     "regimes": ["zta"],
     "description": "Traffic between internal segments must be inspected (firewall or IDS/IPS between zones) per NIST 800-207.",
     "check": "east_west_inspection"},

    # ── CJIS Specific ──────────────────────────────────────────────────────
    {"id": "NET-CJIS-001", "title": "128-bit encryption minimum for CJI",
     "severity": "CAT1", "category": "encryption",
     "regimes": ["cjis"],
     "description": "CJIS Security Policy 5.10.1.2 requires minimum 128-bit encryption (AES) for Criminal Justice Information in transit.",
     "check": "cjis_128bit"},

    # ── General Best Practice ──────────────────────────────────────────────
    {"id": "NET-BP-001", "title": "All devices labeled with hostname",
     "severity": "CAT3", "category": "documentation",
     "regimes": ["fisma_high", "stig", "cjis", "icd503", "cnss1253"],
     "description": "All network devices should have meaningful hostnames for identification in audit logs.",
     "check": "devices_labeled"},

    {"id": "NET-BP-002", "title": "Network diagram matches as-built documentation",
     "severity": "CAT3", "category": "documentation",
     "regimes": ["fisma_high", "stig", "icd503"],
     "description": "Topology diagram should have a saved version labeled 'as-built' for ATO documentation.",
     "check": "as_built_version"},
]

# Encryptor speed ratings (Mbps) for NET-ENC-003
ENCRYPTOR_RATINGS = {
    "kg-175d": 10000, "kg-175g": 10000, "kg-250": 100000, "kg-340": 400000,
    "kg-245x": 10000, "kg-255": 100000, "fips-140-l1": 10000, "fips-140-l2": 10000,
    "fips-140-l3": 100000, "fips-140-l4": 100000, "macsec": 400000,
    "type1-encryptor": 10000, "hsm": 1000,
}

# Bill of Materials unit costs (USD) per device type
BOM_COSTS = {
    "router": 15000, "switch-l2": 3000, "switch-l3": 8000, "firewall": 25000,
    "load-balancer": 20000, "wap": 800, "server": 5000, "patch-panel": 200,
    "roadm": 45000, "oadm": 12000, "edfa": 8000, "transponder": 18000,
    "olt": 10000, "odf": 500, "sonet-adm": 35000,
    "media-ge": 50, "media-10ge": 150, "media-25ge": 300, "media-40ge": 500,
    "media-100ge": 1200, "media-400ge": 3500, "media-fiber": 80, "media-optical": 200,
    "sfp": 80, "sfp-plus": 200, "qsfp": 800, "qsfp-dd": 2000,
    "patch-panel-fiber": 400,
    # Cloud objects are OpEx, not CapEx — zero hardware cost
}
