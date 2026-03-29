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
    "colocation": [
        {"type": "meet-me-room", "label": "Meet-Me Room", "icon": "MMR", "desc": "Carrier-neutral meet-me room — physical handoff between customer and CSP/carrier"},
        {"type": "cross-connect", "label": "Cross-Connect", "icon": "XX", "desc": "Physical cross-connect cable between racks/cages in a colocation facility"},
        {"type": "demarc", "label": "Demarc", "icon": "DM", "desc": "Demarcation point — boundary between carrier and customer responsibility"},
        {"type": "cage", "label": "Cage/Suite", "icon": "CGE", "desc": "Colocation cage or private suite housing customer equipment"},
        {"type": "cabinet", "label": "Cabinet", "icon": "CAB", "desc": "Equipment cabinet/rack in colocation facility"},
    ],
}

# ── Extended Cloud Networking Objects (Well-Architected Hybrid Networking) ────
# Adds missing services identified from AWS Well-Architected Hybrid Networking
# Lens and multi-CSP equivalence research.
CLOUD_OBJECTS_EXTENDED = {
    "aws": [
        {"type": "aws-dx-gw", "label": "DX Gateway", "icon": "DGW", "desc": "AWS Direct Connect Gateway — global resource for multi-Region DX access (up to 20 VGWs, 6 TGWs)"},
        {"type": "aws-privatelink", "label": "PrivateLink", "icon": "PL", "desc": "AWS PrivateLink — private endpoint for AWS/custom services via ENI in VPC"},
        {"type": "aws-cloudwan", "label": "Cloud WAN", "icon": "CWN", "desc": "AWS Cloud WAN — global network with policy-based segmentation"},
        {"type": "aws-ga", "label": "Global Accelerator", "icon": "GA", "desc": "AWS Global Accelerator — anycast IPs for TCP/UDP optimization via AWS backbone"},
        {"type": "aws-shield", "label": "Shield", "icon": "SHD", "desc": "AWS Shield — DDoS protection (Standard=free, Advanced=$3k/mo)"},
        {"type": "aws-netmgr", "label": "Network Manager", "icon": "NMG", "desc": "AWS Network Manager — centralized network monitoring and route analysis"},
        {"type": "aws-flowlogs", "label": "Flow Logs", "icon": "FLG", "desc": "VPC Flow Logs — capture IP traffic metadata to S3/CloudWatch Logs"},
        {"type": "aws-reach", "label": "Reachability Analyzer", "icon": "RCH", "desc": "VPC Reachability Analyzer — automated network path analysis"},
        {"type": "aws-gwlb", "label": "Gateway LB", "icon": "GLB", "desc": "AWS Gateway Load Balancer — transparent inline inspection (L3 bump-in-wire)"},
        {"type": "aws-localzone", "label": "Local Zone", "icon": "LZ", "desc": "AWS Local Zone — edge compute for ultra-low latency (<10ms)"},
        {"type": "aws-outpost", "label": "Outposts", "icon": "OP", "desc": "AWS Outposts — AWS infrastructure on-premises (hybrid cloud)"},
    ],
    "azure": [
        {"type": "az-er-global", "label": "ER Global Reach", "icon": "EGR", "desc": "Azure ExpressRoute Global Reach — connect on-prem sites via Microsoft backbone"},
        {"type": "az-privatelink", "label": "Private Link", "icon": "PL", "desc": "Azure Private Link — private endpoint for Azure/custom services"},
        {"type": "az-ddos", "label": "DDoS Protection", "icon": "DDP", "desc": "Azure DDoS Protection Standard (~$2.9k/mo)"},
        {"type": "az-netwatcher", "label": "Network Watcher", "icon": "NW", "desc": "Azure Network Watcher — topology, connectivity checks, NSG diagnostics"},
        {"type": "az-flowlogs", "label": "VNet Flow Logs", "icon": "FLG", "desc": "Azure VNet Flow Logs — VNet-level traffic capture with analytics"},
        {"type": "az-stack", "label": "Stack HCI", "icon": "HCI", "desc": "Azure Stack HCI — hyperconverged Azure on-premises"},
        {"type": "az-crosslb", "label": "Cross-region LB", "icon": "XLB", "desc": "Azure Cross-region Load Balancer — global L4 load balancing"},
    ],
    "gcp": [
        {"type": "gcp-psc", "label": "Private Svc Connect", "icon": "PSC", "desc": "GCP Private Service Connect — private access to Google/custom services"},
        {"type": "gcp-ncc", "label": "Network CC", "icon": "NCC", "desc": "GCP Network Connectivity Center — global hub for hybrid/multi-cloud spokes"},
        {"type": "gcp-nic", "label": "Network Intel", "icon": "NIC", "desc": "GCP Network Intelligence Center — topology, performance, firewall insights"},
        {"type": "gcp-gfe", "label": "Global LB", "icon": "GFE", "desc": "GCP Global External Application LB — single anycast IP across all regions"},
        {"type": "gcp-gdc", "label": "Distributed Cloud", "icon": "GDC", "desc": "Google Distributed Cloud — sovereign/air-gapped deployment (IL6/SECRET)"},
        {"type": "gcp-flowlogs", "label": "Flow Logs", "icon": "FLG", "desc": "GCP VPC Flow Logs — configurable sampling rate, direct BigQuery export"},
    ],
    "oci": [
        {"type": "oci-ddos", "label": "DDoS Protection", "icon": "DDP", "desc": "OCI DDoS Protection — always-on, FREE for all customers (unique among CSPs)"},
        {"type": "oci-pathanalyzer", "label": "Path Analyzer", "icon": "PA", "desc": "OCI Network Path Analyzer — automated reachability analysis"},
        {"type": "oci-flowlogs", "label": "Flow Logs", "icon": "FLG", "desc": "OCI VCN Flow Logs — subnet or VNIC level capture"},
        {"type": "oci-dedicated", "label": "Dedicated Region", "icon": "DR", "desc": "OCI Dedicated Region Cloud@Customer — full OCI on-premises"},
        {"type": "oci-fd", "label": "Fault Domain", "icon": "FD", "desc": "OCI Fault Domain — three-tier isolation (Region > AD > FD)"},
    ],
    "ibm": [
        {"type": "ibm-satellite", "label": "Satellite", "icon": "SAT", "desc": "IBM Cloud Satellite — extend IBM services to any infrastructure"},
        {"type": "ibm-cis", "label": "CIS", "icon": "CIS", "desc": "IBM Cloud Internet Services — Cloudflare-powered CDN/WAF/DDoS/DNS"},
        {"type": "ibm-flowlogs", "label": "Flow Logs", "icon": "FLG", "desc": "IBM Cloud Flow Logs for VPC — instance NIC or subnet capture"},
    ],
}

# ── CSP Service Equivalence Map ──────────────────────────────────────────────
# Cross-cloud mapping from AWS Well-Architected Hybrid Networking Lens.
# Used for multi-cloud design guidance, migration planning, and cost comparison.
# parity: "full" | "full+" (exceeds AWS) | "partial" | "none"
CSP_EQUIVALENCE = {
    "dedicated_interconnect": {
        "category": "Connectivity",
        "description": "Dedicated physical circuit to cloud provider",
        "aws": {"service": "Direct Connect", "type": "aws-dx", "speeds": "1/10/100/400 Gbps"},
        "azure": {"service": "ExpressRoute", "type": "az-er", "speeds": "1/2/5/10/50/100 Gbps", "parity": "full"},
        "gcp": {"service": "Cloud Interconnect (Dedicated)", "type": "gcp-ic", "speeds": "10/100 Gbps", "parity": "full"},
        "oci": {"service": "FastConnect", "type": "oci-fc", "speeds": "1/10/100 Gbps", "parity": "full",
                "note": "FREE egress over FastConnect — unique among CSPs"},
        "ibm": {"service": "Direct Link", "type": "ibm-dl", "speeds": "1/2/5/10 Gbps", "parity": "partial"},
    },
    "site_to_site_vpn": {
        "category": "Connectivity",
        "description": "IPSec VPN tunnel over internet",
        "aws": {"service": "Site-to-Site VPN", "type": "aws-vpn", "throughput": "1.25 Gbps/tunnel"},
        "azure": {"service": "VPN Gateway", "type": "az-vpn-gw", "throughput": "Up to 10 Gbps", "parity": "full"},
        "gcp": {"service": "Cloud VPN (HA VPN)", "type": "gcp-vpn", "throughput": "3 Gbps/tunnel", "parity": "full"},
        "oci": {"service": "Site-to-Site VPN", "throughput": "250 Mbps/tunnel", "parity": "partial"},
        "ibm": {"service": "VPN for VPC", "type": "ibm-vpn", "throughput": "650 Mbps", "parity": "partial",
                "note": "No BGP — static routes only"},
    },
    "transit_hub": {
        "category": "Connectivity",
        "description": "Hub-and-spoke network transit for multi-VPC connectivity",
        "aws": {"service": "Transit Gateway", "type": "aws-tgw", "scope": "Regional"},
        "azure": {"service": "Virtual WAN", "type": "az-vwan", "scope": "Global", "parity": "full+",
                  "note": "Natively global with integrated SD-WAN partner NVAs"},
        "gcp": {"service": "Network Connectivity Center", "type": "gcp-ncc", "scope": "Global", "parity": "full"},
        "oci": {"service": "DRG v2", "type": "oci-drg", "scope": "Regional", "parity": "full",
                "note": "Free, supports transitive peering natively"},
        "ibm": {"service": "Transit Gateway", "type": "ibm-tg", "scope": "Regional", "parity": "partial"},
    },
    "private_endpoint": {
        "category": "Connectivity",
        "description": "Private access to cloud services without internet exposure",
        "aws": {"service": "PrivateLink", "type": "aws-privatelink"},
        "azure": {"service": "Private Link", "type": "az-privatelink", "parity": "full",
                  "note": "Deepest PaaS integration — nearly every Azure service supported"},
        "gcp": {"service": "Private Service Connect", "type": "gcp-psc", "parity": "full"},
        "oci": {"service": "Service Gateway / Private Endpoint", "parity": "partial",
                "note": "Primarily Oracle services only"},
        "ibm": {"service": "Virtual Private Endpoints", "parity": "partial"},
    },
    "global_wan": {
        "category": "Connectivity",
        "description": "Global WAN orchestration with policy-based routing",
        "aws": {"service": "Cloud WAN", "type": "aws-cloudwan"},
        "azure": {"service": "Virtual WAN", "type": "az-vwan", "parity": "full+",
                  "note": "Built-in SD-WAN partner NVA integration"},
        "gcp": {"service": "Network Connectivity Center", "type": "gcp-ncc", "parity": "full"},
        "oci": {"parity": "none"},
        "ibm": {"parity": "none"},
    },
    "virtual_network": {
        "category": "Architecture",
        "description": "Isolated virtual network",
        "aws": {"service": "VPC", "type": "aws-vpc", "scope": "Regional"},
        "azure": {"service": "VNet", "type": "az-vnet", "scope": "Regional", "parity": "full"},
        "gcp": {"service": "VPC", "type": "gcp-vpc", "scope": "Global", "parity": "full+",
                "note": "Global VPCs — subnets span regions, eliminates cross-region peering"},
        "oci": {"service": "VCN", "type": "oci-vcn", "scope": "Regional", "parity": "full"},
        "ibm": {"service": "VPC", "type": "ibm-vpc", "scope": "Regional", "parity": "full"},
    },
    "network_firewall": {
        "category": "Security",
        "description": "Cloud-native L3-L7 stateful firewall",
        "aws": {"service": "Network Firewall", "type": "aws-nfw"},
        "azure": {"service": "Azure Firewall Premium", "type": "az-fw", "parity": "full"},
        "gcp": {"service": "Cloud NGFW (Palo Alto)", "type": "gcp-armor", "parity": "full"},
        "oci": {"service": "Network Firewall", "parity": "full"},
        "ibm": {"parity": "none", "note": "Use NVA — no native L7 firewall"},
    },
    "ddos_protection": {
        "category": "Security",
        "description": "DDoS mitigation service",
        "aws": {"service": "Shield Standard/Advanced", "type": "aws-shield", "cost": "$3,000/mo (Advanced)"},
        "azure": {"service": "DDoS Protection Standard", "type": "az-ddos", "cost": "$2,944/mo", "parity": "full"},
        "gcp": {"service": "Cloud Armor", "type": "gcp-armor", "parity": "full"},
        "oci": {"service": "DDoS Protection", "type": "oci-ddos", "cost": "FREE", "parity": "full+",
                "note": "Enterprise-grade DDoS included at no cost for all customers"},
        "ibm": {"service": "CIS (Cloudflare)", "type": "ibm-cis", "parity": "full"},
    },
    "flow_logs": {
        "category": "Monitoring",
        "description": "Network traffic flow metadata capture",
        "aws": {"service": "VPC Flow Logs", "type": "aws-flowlogs"},
        "azure": {"service": "VNet Flow Logs", "type": "az-flowlogs", "parity": "full"},
        "gcp": {"service": "VPC Flow Logs", "type": "gcp-flowlogs", "parity": "full",
                "note": "Configurable sampling rate + direct BigQuery export"},
        "oci": {"service": "VCN Flow Logs", "type": "oci-flowlogs", "parity": "full"},
        "ibm": {"service": "Flow Logs for VPC", "type": "ibm-flowlogs", "parity": "full"},
    },
    "network_analysis": {
        "category": "Monitoring",
        "description": "Automated network path and reachability analysis",
        "aws": {"service": "Reachability Analyzer", "type": "aws-reach"},
        "azure": {"service": "Network Watcher", "type": "az-netwatcher", "parity": "full"},
        "gcp": {"service": "Connectivity Tests (NIC)", "type": "gcp-nic", "parity": "full"},
        "oci": {"service": "Network Path Analyzer", "type": "oci-pathanalyzer", "parity": "full"},
        "ibm": {"parity": "none"},
    },
    "global_load_balancing": {
        "category": "Load Balancing",
        "description": "Global L7 load balancing with CDN/WAF integration",
        "aws": {"service": "CloudFront + ALB / Global Accelerator", "type": "aws-ga"},
        "azure": {"service": "Front Door", "type": "az-front", "parity": "full+",
                  "note": "Unified global L7 LB + CDN + WAF"},
        "gcp": {"service": "Global External Application LB", "type": "gcp-gfe", "parity": "full+",
                "note": "Single anycast IP across all regions — no DNS-based failover needed"},
        "oci": {"parity": "none", "note": "DNS-based only"},
        "ibm": {"service": "CIS GLB (Cloudflare)", "type": "ibm-cis", "parity": "partial"},
    },
    "hybrid_edge": {
        "category": "Architecture",
        "description": "Cloud infrastructure deployed on-premises (hybrid edge)",
        "aws": {"service": "Outposts / Local Zones", "type": "aws-outpost"},
        "azure": {"service": "Stack HCI / Arc", "type": "az-stack", "parity": "full"},
        "gcp": {"service": "Distributed Cloud (GDC)", "type": "gcp-gdc", "parity": "full",
                "note": "Air-gapped sovereign deployment for IL6/SECRET"},
        "oci": {"service": "Dedicated Region Cloud@Customer", "type": "oci-dedicated", "parity": "full"},
        "ibm": {"service": "Satellite", "type": "ibm-satellite", "parity": "full",
                "note": "Most flexible — extends IBM services to any infrastructure"},
    },
}

# ── Resiliency Tiers (AWS Well-Architected Hybrid Networking Lens) ───────────
# Based on AWS Direct Connect Resiliency Toolkit + re:Invent ARC322.
# Applied to all CSPs via equivalence mapping.
RESILIENCY_TIERS = {
    "maximum": {
        "label": "Maximum Resiliency",
        "sla": "99.99%",
        "description": "Separate connections on separate devices in 2+ DX/ER locations, at least one co-located with workload Region",
        "requirements": {
            "min_connections": 4,
            "min_locations": 2,
            "location_colocated_with_region": True,
            "unique_aws_devices": True,
        },
        "aws_requirements": "4+ dedicated connections, 2+ DX locations, ≥1 in associated Region, Enterprise Support + Well-Architected Review",
        "azure_requirements": "Zone-redundant ExpressRoute with Global Reach for multi-site",
        "gcp_requirements": "Redundant VLAN attachments across 2+ metro areas",
        "oci_requirements": "Redundant FastConnect virtual circuits across diverse paths",
    },
    "high": {
        "label": "High Resiliency",
        "sla": "99.9%",
        "description": "Two single connections to multiple locations — resiliency against connectivity + location failure",
        "requirements": {
            "min_connections": 2,
            "min_locations": 2,
        },
    },
    "development": {
        "label": "Development / Test",
        "sla": "None",
        "description": "Separate connections on separate devices in one location — device failure only",
        "requirements": {
            "min_connections": 2,
            "min_locations": 1,
        },
    },
    "single": {
        "label": "No Resiliency",
        "sla": "None",
        "description": "Single connection — single point of failure, no SLA guarantee",
        "requirements": {
            "min_connections": 1,
            "min_locations": 1,
        },
    },
}

# ── Hybrid Connectivity Patterns ─────────────────────────────────────────────
# Architecture patterns from AWS Well-Architected Hybrid Networking Lens.
HYBRID_CONNECTIVITY_PATTERNS = {
    "dx_primary_vpn_backup": {
        "label": "DX Primary + VPN Backup",
        "description": "Dedicated circuit as primary with IPSec VPN over internet as automatic failover",
        "resiliency": "high",
        "cost": "medium",
        "failover_time_sec": {"with_bfd": 1, "without_bfd": 90},
        "applicable_csps": ["aws", "azure", "gcp", "oci"],
        "aws": {"primary": "aws-dx", "backup": "aws-vpn", "hub": "aws-tgw"},
        "azure": {"primary": "az-er", "backup": "az-vpn-gw", "hub": "az-vwan"},
        "gcp": {"primary": "gcp-ic", "backup": "gcp-vpn", "hub": "gcp-ncc"},
        "oci": {"primary": "oci-fc", "backup": "oci-vpn", "hub": "oci-drg"},
    },
    "dual_dx_diverse_locations": {
        "label": "Dual DX at Diverse Locations",
        "description": "Two dedicated circuits at geographically diverse colocation facilities for maximum resiliency",
        "resiliency": "maximum",
        "cost": "high",
        "failover_time_sec": {"with_bfd": 1, "without_bfd": 90},
        "applicable_csps": ["aws", "azure", "gcp", "oci", "ibm"],
    },
    "transit_hub_multi_vpc": {
        "label": "Transit Hub (Multi-VPC)",
        "description": "Central hub (TGW/vWAN/NCC/DRG) connecting multiple VPCs with on-prem via single DX",
        "resiliency": "high",
        "cost": "medium",
        "aws": {"hub": "aws-tgw", "vif_type": "transit"},
        "azure": {"hub": "az-vwan"},
        "gcp": {"hub": "gcp-ncc"},
        "oci": {"hub": "oci-drg"},
    },
    "ipsec_over_dx": {
        "label": "IPSec VPN over Dedicated Circuit",
        "description": "End-to-end encryption via IPSec tunnel running on top of DX/ER for compliance (not just point-to-point MACsec)",
        "resiliency": "high",
        "cost": "medium",
        "encryption": "end-to-end",
        "variants": {
            "public_vif": "IPSec over DX public VIF — requires public IPs",
            "private_ip_vpn": "Private IP VPN over transit VIF (RECOMMENDED) — no public IPs needed",
            "software_vpn": "Self-managed VPN on EC2 over private VIF — full control of both endpoints",
        },
    },
    "macsec_dx": {
        "label": "MACsec on Dedicated Circuit",
        "description": "Layer 2 point-to-point encryption between DX edge and customer edge device",
        "resiliency": "high",
        "cost": "medium",
        "encryption": "point-to-point",
        "supported_speeds": ["10 Gbps", "100 Gbps", "400 Gbps"],
        "limitation": "Point-to-point only — does NOT provide end-to-end encryption",
    },
    "multi_cloud_peering": {
        "label": "Multi-Cloud Interconnect",
        "description": "Direct peering between CSPs (e.g., AWS ↔ Azure via Megaport/Equinix Fabric)",
        "resiliency": "high",
        "cost": "high",
        "applicable_csps": ["aws", "azure", "gcp", "oci"],
    },
    "sdwan_overlay": {
        "label": "SD-WAN Overlay",
        "description": "Application-aware overlay across multiple transports (MPLS, DIA, LTE) with centralized orchestration",
        "resiliency": "high",
        "cost": "medium",
        "vendors": ["Cisco Viptela", "VMware VeloCloud", "Fortinet SD-WAN", "Palo Alto Prisma SD-WAN"],
    },
}

# ── Cloud Egress Pricing (USD/GB, 2026 approximate) ─────────────────────────
# Used for multi-cloud cost estimation in NDC.
CLOUD_EGRESS_PRICING = {
    "aws": {
        "internet_per_gb": 0.09,
        "cross_region_per_gb": 0.02,
        "cross_az_per_gb": 0.01,
        "dx_per_gb": 0.02,
        "ingress": 0.0,
    },
    "azure": {
        "internet_per_gb": 0.087,
        "cross_region_per_gb": 0.02,
        "cross_az_per_gb": 0.0,
        "er_per_gb": 0.02,
        "ingress": 0.0,
    },
    "gcp": {
        "internet_per_gb_premium": 0.12,
        "internet_per_gb_standard": 0.085,
        "cross_region_per_gb": 0.01,
        "cross_az_per_gb": 0.0,
        "interconnect_per_gb": 0.02,
        "ingress": 0.0,
        "note": "Premium vs Standard network tier — choose latency vs cost",
    },
    "oci": {
        "internet_per_gb": 0.0085,
        "cross_region_per_gb": 0.0,
        "cross_az_per_gb": 0.0,
        "fastconnect_per_gb": 0.0,
        "ingress": 0.0,
        "note": "~10x cheaper egress than AWS/Azure/GCP; cross-region and FastConnect FREE",
    },
    "ibm": {
        "internet_per_gb": 0.09,
        "cross_region_per_gb": 0.02,
        "cross_az_per_gb": 0.0,
        "direct_link_per_gb": 0.02,
        "ingress": 0.0,
    },
}

# ── Dedicated Interconnect Port Pricing (monthly, approximate) ───────────────
INTERCONNECT_PORT_PRICING = {
    "aws":   {"10g": 1638, "100g": 16380},
    "azure": {"10g": 1700, "100g": 17000},
    "gcp":   {"10g": 1700, "100g": 17000},
    "oci":   {"10g": 438,  "100g": 4380, "note": "~75% cheaper than AWS/Azure/GCP"},
    "ibm":   {"10g": 1800},
}

# ── BGP Configuration Constants (from AWS Direct Connect docs) ───────────────
BGP_CONSTANTS = {
    "aws_asn": 7224,
    "private_asn_2byte": {"min": 64512, "max": 65534},
    "private_asn_4byte": {"min": 4200000000, "max": 4294967294},
    "dx_community_tags": {
        "local_region": "7224:9100",
        "continent": "7224:9200",
        "global": "7224:9300",
        "origin_same_region": "7224:8100",
        "origin_same_continent": "7224:8200",
        "medium_local_pref": "7224:7200",
    },
    "reserved_range": "7224:1 – 7224:65535",
    "default_hold_time_sec": 90,
    "bfd_interval_ms": 300,
    "bfd_detect_time_ms": 900,
    "routing_evaluation_order": [
        "1. Longest prefix match",
        "2. Local preference (recommended for active/passive)",
        "3. AS_PATH length",
        "4. MED (NOT recommended by AWS)",
        "5. ECMP across equal paths",
    ],
}

# ── Failover Detection Timers ────────────────────────────────────────────────
FAILOVER_TIMERS = {
    "bgp_default": {"detect_sec": 90, "description": "Standard BGP keepalive/hold (3 × 30s)"},
    "bfd": {"detect_sec": 0.9, "description": "BFD keepalive (3 × 300ms) — sub-second detection"},
    "ospf": {"detect_sec": 40, "description": "OSPF dead interval (4 × 10s hello)"},
    "ospf_fast": {"detect_sec": 1, "description": "OSPF fast-hello (3 × 333ms)"},
    "vrrp": {"detect_sec": 3, "description": "VRRP master-down (3 × advert interval)"},
    "hsrp": {"detect_sec": 10, "description": "HSRP default hold time"},
    "lacp": {"detect_sec": 3, "description": "LACP short timeout (3 × 1s)"},
}

# ── VPN Bandwidth Specifications (from Well-Architected Hybrid Networking Lens) ──
VPN_BANDWIDTH_SPECS = {
    "aws_vpn_tunnel": {"max_gbps": 4.9, "note": "Per tunnel; 2 tunnels per connection"},
    "aws_vpn_tgw_ecmp": {"max_gbps": 9.8, "note": "2 tunnels with ECMP on TGW/Cloud WAN"},
    "aws_vpn_concentrator": {"per_site_mbps": 100, "min_sites": 25,
                              "note": "Managed VPN concentrator for 25+ remote sites"},
    "aws_ec2_vpn_small": {"max_gbps": 5, "note": "EC2 <32 vCPU software VPN limit"},
    "aws_ec2_vpn_large": {"pct_bandwidth": 50, "note": "EC2 32+ vCPU: 50% of instance bandwidth"},
    "azure_vpn_gw5": {"max_gbps": 10, "note": "VpnGw5/VpnGw5AZ SKU"},
    "gcp_ha_vpn": {"max_gbps": 3, "note": "Per tunnel on HA VPN"},
    "oci_ipsec": {"per_tunnel_mbps": 250, "note": "Multiple tunnels for aggregation"},
    "ibm_vpn": {"max_mbps": 650, "note": "Static routes only, no BGP"},
}

# ── Architecture Patterns (Well-Architected Hybrid Networking Lens §2-3) ─────
CLOUD_ARCHITECTURE_PATTERNS = {
    "single_vpc_vgw": {
        "label": "Single VPC with Virtual Private Gateway",
        "description": "VPN/DX to VGW provides access to one VPC only. "
                       "Cost-effective for large data transfers to single VPC.",
        "use_when": "Single VPC, high bandwidth to one destination",
        "limitations": "One VPC per VGW, only 1 of 2 VPN tunnels active",
    },
    "transit_gateway_hub": {
        "label": "Transit Gateway Hub-and-Spoke",
        "description": "VPN/DX attached to TGW enables multi-VPC access in same region. "
                       "Supports ECMP for bandwidth aggregation.",
        "use_when": "Multi-VPC in same region, need ECMP or route segmentation",
        "aws_max_bandwidth": "9.8 Gbps (2 VPN tunnels ECMP)",
    },
    "cloud_wan_global": {
        "label": "Cloud WAN Multi-Region Mesh",
        "description": "Core network edge for multi-VPC in same or different regions. "
                       "Uses segments for traffic isolation with global management plane.",
        "use_when": "Multi-region, need segment-based isolation and global policy",
    },
    "dx_gateway_global": {
        "label": "DX Gateway Global Hub",
        "description": "Global resource connecting DX to resources in multiple Regions. "
                       "Associate up to 20 VGWs or 6 TGWs across regions.",
        "use_when": "Multi-region DX access from single/dual DX locations",
        "limits": {"max_vgws": 20, "max_tgws": 6, "max_cloudwan": 1},
    },
    "landing_zone": {
        "label": "Landing Zone (Central Networking Account)",
        "description": "Standardized multi-account architecture via Control Tower. "
                       "Central networking account hosts all hybrid resources "
                       "(DX, VPN, TGW) shared via RAM to spoke accounts.",
        "use_when": "Enterprise multi-account, need centralized network governance",
        "components": ["Control Tower", "Organizations", "RAM", "Transit Gateway", "Central Networking Account"],
    },
    "vpn_concentrator": {
        "label": "VPN Concentrator (25+ Sites)",
        "description": "Managed VPN concentrator attachment to TGW for many remote sites "
                       "at 50-100 Mbps each.",
        "use_when": "25+ remote sites, low per-site bandwidth (branch offices)",
    },
    "accelerated_vpn": {
        "label": "Accelerated Site-to-Site VPN",
        "description": "Uses AWS Global Accelerator to route VPN traffic via AWS edge "
                       "locations instead of public internet — reduces jitter and latency.",
        "use_when": "VPN performance issues due to internet routing variability",
    },
}

# ── Well-Architected Pillar Risk Mappings ────────────────────────────────────
# Risk level assigned to each best practice from the Hybrid Networking Lens.
# Used for compliance prioritization and audit weighting.
WA_HYBRID_RISK_LEVELS = {
    "high": [
        "Network segmentation and least privilege (HNSEC01-BP01)",
        "Encryption in transit (HNSEC01-BP02)",
        "Continuous logging (HNSEC01-BP03)",
        "Landing zone deployment (HNSEC02-BP01)",
        "Least privilege API access (HNSEC02-BP04)",
        "Routing controls (HNSEC04-BP02)",
        "Traffic inspection via GWLB/Network Firewall (HNSEC04-BP03)",
        "Physical location redundancy (HNREL04-BP01/HNREL06-BP01)",
        "Redundant hardware and diverse providers (HNREL04-BP02/HNREL06-BP02)",
        "Dynamic routing with BFD (HNREL04-BP03)",
        "Sufficient capacity provisioning (HNREL04-BP04)",
        "Failover testing (HNREL05-BP01)",
        "Bandwidth monitoring and scaling (HNREL03-BP01)",
        "Define performance requirements (HNPERF01-BP01)",
        "Application-aware network design (HNPERF01-BP02)",
    ],
    "medium": [
        "VPC/TGW Flow Logs (HNOPS03-BP02)",
        "Resource tagging (HNCOST01-BP01)",
        "DNS security via Route 53 Firewall (HNSEC04-BP04)",
        "KPI tracking dashboards (HNOPS05-BP01)",
        "Cost threshold alerts (HNCOST02-BP02)",
        "Tiered connectivity (HNCOST03-BP01)",
        "Region/AZ cost selection (HNCOST04-BP02)",
        "QoS policies (HNCOST06-BP01)",
    ],
    "low": [
        "Data transfer optimization (HNCOST04-BP01)",
        "Compression and caching (HNCOST04-BP03)",
        "Traffic class separation (HNCOST06-BP02)",
        "Regular cost analysis (HNCOST08-BP01)",
    ],
}

# ── Anti-Patterns (from AWS re:Invent ARC322) ───────────────────────────────
CLOUD_NETWORKING_ANTIPATTERNS = [
    {"id": "AP-001", "title": "DX locations not in Associated Region",
     "description": "Using DX locations that are not co-located with the workload AWS Region — will NOT qualify for 99.99% SLA even with redundant connections",
     "severity": "high", "recommendation": "Ensure at least one DX location is in the Associated Region"},
    {"id": "AP-002", "title": "Backup DX Gateway for resilience",
     "description": "DXGW is a configuration overlay, not infrastructure — adding a backup DXGW does NOT improve availability",
     "severity": "medium", "recommendation": "Focus on diverse DX locations instead of backup DXGW"},
    {"id": "AP-003", "title": "Backup Transit Gateway for data plane",
     "description": "TGW uses Hyperplane distributed nodes with 99.99% SLA — a backup TGW does NOT improve data plane resilience",
     "severity": "medium", "recommendation": "Use Dev TGW only for management plane safety (config testing via CI/CD)"},
    {"id": "AP-004", "title": "BGP-only failover without BFD",
     "description": "Default BGP hold timer is 90 seconds — unacceptable for production. BFD detects failure in <1 second",
     "severity": "high", "recommendation": "Always enable BFD on all DX/ER virtual interfaces"},
    {"id": "AP-005", "title": "Cross-zone LB enabled on NLB for resilience",
     "description": "Counter-intuitively, enabling cross-zone LB on NLB REDUCES resilience — a failed AZ drags down healthy AZs instead of being cleanly removed",
     "severity": "medium", "recommendation": "Disable cross-zone LB; use Route 53 health checks to remove failed AZ endpoints"},
    {"id": "AP-006", "title": "LAG for high availability",
     "description": "LAG terminates on a SINGLE AWS device — NOT suitable for HA. All connections share one device SPOF",
     "severity": "high", "recommendation": "Use separate connections at diverse locations instead of LAG for HA"},
    {"id": "AP-007", "title": "Customer-side policing only for noisy neighbor",
     "description": "Policing on customer router only works on ingress — does NOT protect from UDP floods originating from AWS side",
     "severity": "medium", "recommendation": "Use Transit VIF + GRE tunnels with separate TGW route tables for hard traffic isolation"},
    {"id": "AP-008", "title": "Single-VIF DX without VPN backup",
     "description": "A single DX VIF with no VPN backup has no failover path — internet VPN should always be configured as backup",
     "severity": "high", "recommendation": "Configure Site-to-Site VPN as automatic DX backup"},
]

# Default auto-populated components per CSP group (used when group_type="full")
CSP_GROUP_DEFAULTS = {
    "aws": [
        {"type": "aws-vpc", "label": "VPC", "dx": 40, "dy": 60},
        {"type": "aws-tgw", "label": "Transit Gateway", "dx": 200, "dy": 60},
        {"type": "aws-dx-gw", "label": "DX Gateway", "dx": 360, "dy": 60},
        {"type": "aws-subnet", "label": "Public Subnet", "dx": 40, "dy": 150},
        {"type": "aws-subnet", "label": "Private Subnet", "dx": 200, "dy": 150},
        {"type": "aws-nfw", "label": "Network Firewall", "dx": 120, "dy": 230},
        {"type": "aws-alb", "label": "ALB", "dx": 40, "dy": 230},
        {"type": "aws-r53", "label": "Route 53", "dx": 360, "dy": 150},
        {"type": "aws-shield", "label": "Shield", "dx": 360, "dy": 230},
        {"type": "aws-privatelink", "label": "PrivateLink", "dx": 200, "dy": 230},
    ],
    "azure": [
        {"type": "az-vnet", "label": "VNet", "dx": 40, "dy": 60},
        {"type": "az-vwan", "label": "Virtual WAN", "dx": 200, "dy": 60},
        {"type": "az-subnet", "label": "Subnet", "dx": 40, "dy": 150},
        {"type": "az-fw", "label": "Azure Firewall", "dx": 200, "dy": 150},
        {"type": "az-appgw", "label": "App Gateway", "dx": 40, "dy": 230},
        {"type": "az-nsg", "label": "NSG", "dx": 200, "dy": 230},
        {"type": "az-er", "label": "ExpressRoute", "dx": 360, "dy": 60},
        {"type": "az-ddos", "label": "DDoS Protection", "dx": 360, "dy": 150},
        {"type": "az-privatelink", "label": "Private Link", "dx": 360, "dy": 230},
    ],
    "gcp": [
        {"type": "gcp-vpc", "label": "VPC (global)", "dx": 40, "dy": 60},
        {"type": "gcp-subnet", "label": "Subnet", "dx": 200, "dy": 60},
        {"type": "gcp-router", "label": "Cloud Router", "dx": 40, "dy": 150},
        {"type": "gcp-lb", "label": "Cloud LB", "dx": 200, "dy": 150},
        {"type": "gcp-armor", "label": "Cloud Armor", "dx": 40, "dy": 230},
        {"type": "gcp-ic", "label": "Interconnect", "dx": 360, "dy": 60},
        {"type": "gcp-ncc", "label": "Network CC", "dx": 360, "dy": 150},
        {"type": "gcp-psc", "label": "Private Svc Connect", "dx": 200, "dy": 230},
    ],
    "oci": [
        {"type": "oci-vcn", "label": "VCN", "dx": 40, "dy": 60},
        {"type": "oci-drg", "label": "DRG v2 (free)", "dx": 200, "dy": 60},
        {"type": "oci-subnet", "label": "Subnet", "dx": 40, "dy": 150},
        {"type": "oci-lb", "label": "Load Balancer", "dx": 200, "dy": 150},
        {"type": "oci-fc", "label": "FastConnect", "dx": 360, "dy": 60},
        {"type": "oci-ddos", "label": "DDoS (FREE)", "dx": 360, "dy": 150},
        {"type": "oci-nsg", "label": "NSG", "dx": 40, "dy": 230},
    ],
    "ibm": [
        {"type": "ibm-vpc", "label": "VPC", "dx": 40, "dy": 60},
        {"type": "ibm-tg", "label": "Transit Gateway", "dx": 200, "dy": 60},
        {"type": "ibm-subnet", "label": "Subnet", "dx": 40, "dy": 150},
        {"type": "ibm-lb", "label": "Load Balancer", "dx": 200, "dy": 150},
        {"type": "ibm-dl", "label": "Direct Link", "dx": 360, "dy": 60},
        {"type": "ibm-satellite", "label": "Satellite", "dx": 360, "dy": 150},
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
    # Colocation facility objects
    "meet-me-room": 0, "cross-connect": 300, "demarc": 0, "cage": 0, "cabinet": 2500,
    # Cloud objects are OpEx, not CapEx — zero hardware cost
}
